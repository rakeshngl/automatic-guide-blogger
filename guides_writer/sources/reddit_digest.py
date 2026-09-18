import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DEFAULT_ENDPOINT = "https://digest.uvfarms.in/latest"
DEFAULT_DIGESTS_DIR = BASE_DIR / "data" / "reddit_digests"
IDEAS_LOG_PATH = BASE_DIR / "data" / "reddit_ideas_log.json"


class DigestEntry(BaseModel):
    title: str
    url: str = ""
    subreddit: str = ""
    snippet: str = ""


class DigestPayload(BaseModel):
    digests: list[dict] = Field(default_factory=list)


class DigestIdea(BaseModel):
    title: str
    angle: str
    why_guide_worthy: str
    thread_url: str = ""


class DigestIdeaResponse(BaseModel):
    ideas: list[DigestIdea] = Field(default_factory=list)


def parse_digest_payload(payload: dict, limit: int = 60) -> list[DigestEntry]:
    try:
        digests = payload.get("digests") or []
    except AttributeError as exc:
        raise SourceError(f"Digest payload not an object: {exc}") from exc
    if not digests:
        raise SourceError("Digest payload has no 'digests' entry")
    entries: list[DigestEntry] = []
    for digest in digests:
        for raw in digest.get("entries") or []:
            if not isinstance(raw, dict) or not raw.get("title", "").strip():
                continue
            entries.append(
                DigestEntry(
                    title=raw["title"].strip(),
                    url=(raw.get("url") or "").strip(),
                    subreddit=(raw.get("subreddit") or digest.get("subreddit") or "").strip(),
                    snippet=(raw.get("snippet") or "").strip()[:200],
                )
            )
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    if not entries:
        raise SourceError("Digest payload parsed zero entries")
    return entries


def _ideas_prompt(entries: list[DigestEntry]) -> list[dict]:
    lines = []
    for i, e in enumerate(entries, start=1):
        snip = f" ({e.snippet})" if e.snippet else ""
        lines.append(f"{i}. [{e.subreddit or 'reddit'}] {e.title}{snip} URL: {e.url or '(none)'}")
    body = "\n".join(lines)
    return [
        {
            "role": "system",
            "content": (
                "You turn Reddit conversations into 'build-your-own-clone' guide ideas. "
                "A good idea names a concrete tool/project a reader could rebuild, and the "
                "guide angle explains the interesting part worth teaching. Keep each idea "
                "unique, ignore pure request-for-help or shopping threads. Return JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "From these Reddit digest conversation snippets, extract up to 6 "
                "build-your-own-clone guide ideas.\n\n"
                f"{body}\n\n"
                'Respond with JSON: {"ideas": [{"title": "...", "angle": "...", '
                '"why_guide_worthy": "...", "thread_url": "..."}]}. '
                "thread_url MUST be copied exactly from the URL shown for that entry — "
                "do not invent or omit it. If nothing is worth a guide, return {\"ideas\": []}."
            ),
        },
    ]


def extract_ideas(
    entries: list[DigestEntry], llm_client, retries: int = 2
) -> list[DigestIdea]:
    if not entries:
        return []
    raw = []
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = llm_client.chat_json(_ideas_prompt(entries), DigestIdeaResponse)
            break
        except Exception as exc:  # noqa: BLE001 - surface after retries
            last_exc = exc
            logger.warning("digest_extract_failed attempt=%d err=%s", attempt, str(exc)[:160])
    if not raw:
        if last_exc:
            logger.warning("digest_extract_gives_up err=%s", str(last_exc)[:160])
        return []
    # Map entries by URL so thread links survive the LLM round-trip.
    url_index = {e.url.rstrip("/"): e for e in entries if e.url}
    title_index = {e.title.lower(): e for e in entries}
    # Only keep ideas that trace back to a digest entry (URL or exact title);
    # purely hallucinated ideas have no thread to link to.
    ideas: list[DigestIdea] = []
    for idea in raw.ideas:
        matched = url_index.get(idea.thread_url.rstrip("/")) or title_index.get(idea.title.lower())
        if matched is None:
            logger.info("digest_extract_hallucinated_dropped title=%s", idea.title)
            continue
        ideas.append(idea.model_copy(update={"thread_url": matched.url}))
    logger.info("digest_extract_ok ideas=%d", len(ideas))
    return ideas


def _to_candidates(ideas: list[DigestIdea], rank_offset: int = 0) -> list[CandidateItem]:
    items = []
    for i, idea in enumerate(ideas, start=1):
        items.append(
            CandidateItem(
                source="reddit_digest",
                title=idea.title,
                url=idea.thread_url or idea.title,
                tagline=f"{idea.angle} — {idea.why_guide_worthy}"[:140],
                metrics={"subreddit": "", "thread": idea.thread_url},
                topics=["reddit_digest"],
                rank=rank_offset + i,
            )
        )
    return items


class RedditDigestAdapter:
    name = "reddit_digest"

    def __init__(
        self,
        endpoint: str | None = None,
        local_file: str | None = None,
        llm_client=None,
        limit: int = 60,
        audit: bool = True,
    ):
        self._endpoint = endpoint or DEFAULT_ENDPOINT
        self._local_file = local_file
        self._llm_client = llm_client
        self._limit = limit
        self._audit = audit

    def _load_payload(self) -> dict:
        if self._local_file:
            path = Path(self._local_file)
            if not path.exists():
                raise SourceError(f"local digest file missing: {self._local_file}")
            return json.loads(path.read_text(encoding="utf-8"))
        resp = httpx.get(self._endpoint, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            raise SourceError(f"Digest endpoint HTTP {resp.status_code}")
        return resp.json()

    def fetch(self) -> list[CandidateItem]:
        payload = self._load_payload()
        entries = parse_digest_payload(payload, limit=self._limit)
        ideas = extract_ideas(entries, self._llm_client) if self._llm_client else [
            DigestIdea(
                title=e.title, angle=e.snippet, why_guide_worthy="Extracted from Reddit digest",
                thread_url=e.url,
            )
            for e in entries
        ]
        if self._audit:
            _write_audit(payload, entries, ideas)
        candidates = _to_candidates(ideas)
        if not candidates:
            raise SourceError("Digest produced zero guide ideas")
        logger.info("reddit_digest_fetch_ok entries=%d ideas=%d", len(entries), len(candidates))
        return candidates


def _write_audit(payload: dict, entries: list[DigestEntry], ideas: list[DigestIdea]) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    digests_dir = DEFAULT_DIGESTS_DIR
    digests_dir.mkdir(parents=True, exist_ok=True)
    (digests_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_raw.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log_path = IDEAS_LOG_PATH
    existing = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    record = {
        "ts": ts,
        "entries": [e.model_dump() for e in entries],
        "ideas": [i.model_dump() for i in ideas],
    }
    existing.append(record)
    log_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
