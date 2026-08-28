import logging

import httpx

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

DEVTO_URL = "https://dev.to/api/articles?top=7&per_page=20"

BROWSER_HEADERS = {
    "User-Agent": "guides-writer/1.0 (+https://guides.uvfarms.in)",
    "Accept": "application/json",
}


def parse_devto(payload: list, limit: int = 20) -> list[CandidateItem]:
    if not isinstance(payload, list):
        raise SourceError("Dev.to payload is not a list")
    if not payload:
        raise SourceError("Dev.to returned no articles")
    items: list[CandidateItem] = []
    for i, article in enumerate(payload[:limit], start=1):
        title = (article.get("title") or "").strip()
        if not title:
            continue
        url = article.get("url")
        if not url:
            continue
        tagline = (article.get("description") or "")[:140]
        items.append(
            CandidateItem(
                source="devto",
                title=title,
                url=url,
                tagline=tagline,
                metrics={
                    "reactions": article.get("positive_reactions_count"),
                    "comments": article.get("comments_count"),
                    "user": (article.get("user") or {}).get("username"),
                    "reading_time": article.get("reading_time_minutes"),
                },
                topics=(article.get("tag_list") or [])[:5],
                rank=i,
            )
        )
    if not items:
        raise SourceError("Dev.to parsed zero usable articles")
    logger.info("devto_fetch_ok count=%d", len(items))
    return items


class DevToAdapter:
    name = "devto"

    def __init__(self, limit: int = 20):
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        resp = httpx.get(DEVTO_URL, headers=BROWSER_HEADERS, timeout=30)
        if resp.status_code != 200:
            raise SourceError(f"Dev.to HTTP {resp.status_code}")
        return parse_devto(resp.json(), limit=self._limit)
