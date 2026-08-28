import logging

import httpx

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=20"

BROWSER_HEADERS = {
    "User-Agent": "guides-writer/1.0 (+https://guides.uvfarms.in)",
    "Accept": "application/json",
}


def parse_hn(payload: dict, limit: int = 20) -> list[CandidateItem]:
    hits = payload.get("hits")
    if hits is None:
        raise SourceError("HN Algolia payload missing 'hits'")
    if not isinstance(hits, list) or not hits:
        raise SourceError("HN Algolia returned no Show HN hits")
    items: list[CandidateItem] = []
    for i, hit in enumerate(hits[:limit], start=1):
        title = (hit.get("title") or "").strip()
        if not title:
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID','')}"
        tagline = (hit.get("story_text") or hit.get("title") or "")[:140]
        items.append(
            CandidateItem(
                source="hn_show",
                title=title,
                url=url,
                tagline=tagline,
                metrics={
                    "points": hit.get("points"),
                    "author": hit.get("author"),
                    "object_id": hit.get("objectID"),
                    "num_comments": hit.get("num_comments"),
                    "created_at": hit.get("created_at"),
                },
                rank=i,
            )
        )
    if not items:
        raise SourceError("HN Show parsed zero usable hits")
    logger.info("hn_show_fetch_ok count=%d", len(items))
    return items


class HNShowAdapter:
    name = "hn_show"

    def __init__(self, limit: int = 20):
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        resp = httpx.get(HN_ALGOLIA_URL, headers=BROWSER_HEADERS, timeout=30)
        if resp.status_code != 200:
            raise SourceError(f"HN Algolia HTTP {resp.status_code}")
        return parse_hn(resp.json(), limit=self._limit)
