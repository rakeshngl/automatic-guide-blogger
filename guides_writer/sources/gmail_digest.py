import email
import html
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email import policy

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

_THREAD_HREF = re.compile(
    r'href\s*=\s*["\'](https://(?:www\.)?reddit\.com/r/[^"\']+/comments/[^"\']+)["\']',
    re.IGNORECASE,
)
_THREAD_LINK_TITLE = re.compile(
    r'<a[^>]+href\s*=\s*["\'](https://(?:www\.)?reddit\.com/r/[^"\']+/comments/[^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _decode(text: str) -> str:
    return html.unescape(text).strip()


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


def _entry_from_match(m: re.Match, snippet: str) -> DigestEntry:
    url = _decode(m.group(1)).rstrip("/")
    sub = re.search(r"/r/([^/]+)/", url)
    return DigestEntry(
        title=_decode(_strip_tags(m.group(2)))[:200],
        url=url,
        subreddit=sub.group(1) if sub else "",
        snippet=snippet[:200],
    )


def parse_digest_email(raw: bytes, limit: int = 60) -> list[DigestEntry]:
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        raise SourceError(f"unparseable email: {exc}") from exc
    body = find_html_body(msg)
    entries: list[DigestEntry] = []
    seen: set[str] = set()
    if body:
        # Take a chunk after each thread link as the snippet (up to next link).
        matches = list(_THREAD_LINK_TITLE.finditer(body))
        for idx, m in enumerate(matches):
            url = _decode(m.group(1)).rstrip("/")
            if url in seen:
                continue
            seen.add(url)
            seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
            snippet = _decode(_strip_tags(body[m.end() : seg_end]))
            snippet = re.sub(r"\s+", " ", snippet).strip()
            entries.append(_entry_from_match(m, snippet))
            if len(entries) >= limit:
                break
        # Fallback: plain links without readable anchor text.
        if not entries:
            for m in _THREAD_HREF.finditer(body):
                url = _decode(m.group(1)).rstrip("/")
                if url in seen:
                    continue
                seen.add(url)
                sub = re.search(r"/r/([^/]+)/", url)
                entries.append(
                    DigestEntry(title=url.rsplit("/comments/", 1)[-1][:200], url=url,
                                subreddit=sub.group(1) if sub else "")
                )
                if len(entries) >= limit:
                    break
    if not entries:
        raise SourceError("Gmail digest email contains no Reddit thread links")
    logger.info("gmail_digest_parsed entries=%d", len(entries))
    return entries


class GmailDigestAdapter:
    name = "gmail_digest"

    def __init__(
        self,
        user: str,
        app_password: str,
        host: str = DEFAULT_HOST,
        sender: str = DEFAULT_SENDER,
        llm_client=None,
        lookback_days: int = 7,
        max_emails: int = 10,
        limit: int = 60,
        audit: bool = True,
    ):
        self._user = user
        self._app_password = app_password
        self._host = host
        self._sender = sender
        self._llm_client = llm_client
        self._lookback_days = lookback_days
        self._max_emails = max_emails
        self._limit = limit
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
        if not entries:
            raise SourceError("Gmail digests parsed zero usable thread entries")
        if self._llm_client:
            ideas = extract_ideas(entries, self._llm_client)
        else:
            ideas = [
                DigestIdea(
                    title=e.title, angle=e.snippet,
                    why_guide_worthy="Extracted from Reddit Gmail digest",
                    thread_url=e.url,
                )
                for e in entries
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