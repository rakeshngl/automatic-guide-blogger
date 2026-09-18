import email
import html
import imaplib
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from email import policy
from urllib.parse import unquote

from guides_writer.sources.base import CandidateItem, SourceError
from guides_writer.sources.reddit_digest import (
    DigestEntry,
    DigestIdea,
    _to_candidates,
    _write_audit,
    extract_ideas,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "imap.gmail.com"
DEFAULT_SENDER = "noreply@redditmail.com"
# Only these subreddits feed guide ideas (case-insensitive). Everything else
# (r/Indian_flex, r/scamindia, r/TeenIndia, salary flexes, etc.) is dropped.
DEFAULT_ALLOWED_SUBREDDITS = {
    "selfhosted", "appideas", "indiehackers", "startups", "startup_ideas",
    "sideproject",
}

# Reddit digest emails wrap every link in a click.redditmail.com/CL0/<enc> tracker.
_TRACKER_ANCHOR = re.compile(
    r'<a[^>]+href\s*=\s*["\']([^"\']*click\.redditmail\.com/CL0/[^"\']*)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_COMMENTS_RE = re.compile(r"reddit\.com/(?:%2F)?r/([^/%]+)/comments/([^/%]+)")


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _decode(text: str) -> str:
    return html.unescape(text).strip()


def _decode_tracker(tracker_url: str) -> str:
    after = tracker_url.split("/CL0/", 1)[1] if "/CL0/" in tracker_url else tracker_url
    return unquote(after)


def find_html_body(msg: email.message.Message) -> str:
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        content_type = str(msg.get_content_type() or "").lower()
        if content_type == "text/html" and payload:
            return _wrap_charset_decode(payload, msg.get_content_charset())
        if payload:
            return _wrap_charset_decode(payload, msg.get_content_charset())
        return ""
    for part in msg.walk():
        ctype = str(part.get_content_type() or "").lower()
        if ctype == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                return _wrap_charset_decode(payload, part.get_content_charset())
    for part in msg.walk():
        ctype = str(part.get_content_type() or "").lower()
        if ctype == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                return _wrap_charset_decode(payload, part.get_content_charset())
    return ""


def _wrap_charset_decode(payload: bytes, charset: str | None) -> str:
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def parse_digest_email(raw: bytes, limit: int = 60) -> list[DigestEntry]:
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        raise SourceError(f"unparseable email: {exc}") from exc
    body = find_html_body(msg)
    if not body:
        raise SourceError("Gmail digest email has no usable body")
    # Group tracker anchors by decoded thread id, keeping title + body anchors.
    posts: dict[str, dict] = {}
    for m in _TRACKER_ANCHOR.finditer(body):
        tracker = m.group(1)
        try:
            real = _decode_tracker(tracker)
        except Exception:  # noqa: BLE001
            continue
        cm = _COMMENTS_RE.search(real)
        if not cm:
            continue
        subreddit, thread_id = cm.group(1), cm.group(2)
        inner = m.group(2)
        text = _decode(_strip_tags(inner))
        text = re.sub(r"\s+", " ", text).strip()
        post = posts.setdefault(thread_id, {"subreddit": subreddit, "title": "", "snippet": ""})
        if "<strong>" in inner:
            strong = re.search(r"<strong>(.*?)</strong>", inner, re.DOTALL)
            post["title"] = _decode(_strip_tags(strong.group(1))) if strong else text
        elif len(text) > len(post["snippet"]) and not text.lower().startswith(("u/", "posted", "ago", "comment")):
            post["snippet"] = text[:200]
    entries: list[DigestEntry] = []
    for thread_id, post in posts.items():
        if not post["title"]:
            continue
        entries.append(
            DigestEntry(
                title=post["title"][:200],
                url=f"https://www.reddit.com/r/{post['subreddit']}/comments/{thread_id}",
                subreddit=post["subreddit"],
                snippet=post["snippet"][:200],
            )
        )
        if len(entries) >= limit:
            break
    if not entries:
        raise SourceError("Gmail digest email contains no Reddit thread links")
    logger.info("gmail_digest_parsed entries=%d", len(entries))
    return entries


def filter_allowed_entries(
    entries: Iterable[DigestEntry],
    allowed: set[str],
) -> list[DigestEntry]:
    """Keep only threads whose subreddit is in the allowlist (case-insensitive)."""
    result: list[DigestEntry] = []
    for entry in entries:
        if entry.subreddit.lower() in allowed:
            result.append(entry)
    return result


class GmailDigestAdapter:
    name = "gmail_digest"

    def __init__(
        self,
        user: str,
        app_password: str,
        host: str = DEFAULT_HOST,
        sender: str = DEFAULT_SENDER,
        allowed_subreddits: set[str] | None = None,
        llm_client=None,
        lookback_days: int = 7,
        max_emails: int = 10,
        limit: int = 60,
        max_fetch_entries: int = 20,
        audit: bool = True,
    ):
        self._user = user
        self._app_password = app_password
        self._host = host
        self._sender = sender
        self._allowed_subreddits = (
            {s.strip().lower() for s in allowed_subreddits}
            if allowed_subreddits is not None
            else DEFAULT_ALLOWED_SUBREDDITS
        )
        self._llm_client = llm_client
        self._lookback_days = lookback_days
        self._max_emails = max_emails
        self._limit = limit
        self._max_fetch_entries = max_fetch_entries
        self._audit = audit

    def _fetch_raw_emails(self) -> list[bytes]:
        since = (datetime.now(timezone.utc) - timedelta(days=self._lookback_days)).strftime(
            "%d-%b-%Y"
        )
        try:
            conn = imaplib.IMAP4_SSL(self._host)
            conn.login(self._user, self._app_password)
        except (imaplib.IMAP4.error, OSError, Exception) as exc:  # noqa: BLE001
            raise SourceError(f"Gmail IMAP login failed: {exc}") from exc
        try:
            conn.select("INBOX")
            typ, data = conn.search(None, f'(FROM "{self._sender}" SINCE {since})')
            if typ != "OK":
                raise SourceError(f"Gmail IMAP search failed: {typ} {data}")
            ids = data[0].split() if data and data[0] else []
            raws: list[bytes] = []
            for msg_id in ids[-self._max_emails :]:
                _typ, msg_data = conn.fetch(msg_id, "(RFC822)")
                if msg_data and isinstance(msg_data[0], tuple) and msg_data[0][1]:
                    raws.append(msg_data[0][1])
            return raws
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def fetch(self) -> list[CandidateItem]:
        raws = self._fetch_raw_emails()
        if not raws:
            raise SourceError(f"no emails from {self._sender} in the lookback window")
        entries: list[DigestEntry] = []
        seen: set[str] = set()
        for raw in raws:
            try:
                for entry in parse_digest_email(raw, limit=self._limit):
                    if entry.url in seen:
                        continue
                    seen.add(entry.url)
                    entries.append(entry)
            except SourceError as exc:
                logger.warning("gmail_digest_email_skip err=%s", exc)
        entries = filter_allowed_entries(entries, self._allowed_subreddits)
        if not entries:
            raise SourceError("Gmail digests parsed zero usable thread entries")
        ideas: list[DigestIdea] = []
        if self._llm_client and self._max_fetch_entries:
            ideas = extract_ideas(entries, self._llm_client)
        if not ideas:
            if self._llm_client:
                logger.warning("gmail_digest_extract_empty falling_back_to_raw_entries")
            ideas = [
                DigestIdea(
                    title=e.title, angle=e.snippet,
                    why_guide_worthy="Extracted from Reddit Gmail digest",
                    thread_url=e.url,
                )
                for e in entries[:self._max_fetch_entries]
            ]
        if self._audit:
            _write_audit({"digests": [{"entries": [e.model_dump() for e in entries]}]},
                         entries, ideas)
        candidates = _to_candidates(ideas, rank_offset=0)
        if not candidates:
            raise SourceError("Gmail digests produced zero guide ideas")
        logger.info("gmail_digest_fetch_ok emails=%d entries=%d ideas=%d",
                    len(raws), len(entries), len(candidates))
        return candidates
