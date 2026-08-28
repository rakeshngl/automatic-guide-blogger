import logging
import os

import httpx

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

HF_SPACES_URL = "https://huggingface.co/api/spaces?sort=likes&limit=20&direction=-1"

BROWSER_HEADERS = {
    "User-Agent": "guides-writer/1.0 (+https://guides.uvfarms.in)",
    "Accept": "application/json",
}


def parse_hf_spaces(payload: list, limit: int = 20) -> list[CandidateItem]:
    if not isinstance(payload, list):
        raise SourceError("HF Spaces payload is not a list")
    if not payload:
        raise SourceError("HF Spaces returned no results")
    items: list[CandidateItem] = []
    for i, space in enumerate(payload[:limit], start=1):
        title = space.get("id", "").strip()
        if not title:
            continue
        card = space.get("cardData") or {}
        tagline = (card.get("short_description") or "")[:140]
        tags = card.get("tags") or []
        likes = space.get("likes")
        items.append(
            CandidateItem(
                source="hf_spaces",
                title=title,
                url=f"https://huggingface.co/spaces/{title}",
                tagline=tagline,
                metrics={
                    "likes": likes,
                    "sdk": space.get("sdk"),
                    "author": title.split("/")[0] if "/" in title else None,
                },
                topics=tags[:5],
                rank=i,
            )
        )
    if not items:
        raise SourceError("HF Spaces parsed zero usable items")
    logger.info("hf_spaces_fetch_ok count=%d", len(items))
    return items


class HFSpaceAdapter:
    name = "hf_spaces"

    def __init__(self, limit: int = 20):
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        headers = {**BROWSER_HEADERS}
        token = os.environ.get("HF_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.get(HF_SPACES_URL, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise SourceError(f"HF Spaces HTTP {resp.status_code}")
        return parse_hf_spaces(resp.json(), limit=self._limit)
