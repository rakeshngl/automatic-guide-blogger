import logging
import xml.etree.ElementTree as ET

import httpx

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

# Atom feed (browser UA required; Reddit blocks the .json API and plain UA's)
REDDIT_URL = "https://www.reddit.com/r/selfhosted/top/.rss?t=week"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,text/xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_reddit(xml_text: str, limit: int = 20) -> list[CandidateItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"Reddit feed XML parse failed: {exc}") from exc
    entries = [e for e in root.iter(f"{_ATOM}entry")]
    if not entries:
        raise SourceError("Reddit feed has no entries")
    items: list[CandidateItem] = []
    for i, entry in enumerate(entries[:limit], start=1):
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        if not title:
            continue
        link_el = entry.find(f"{_ATOM}link")
        url = (link_el.get("href") if link_el is not None else None) or entry.findtext(
            f"{_ATOM}id"
        ) or ""
        summary = (entry.findtext(f"{_ATOM}summary") or "")[:140]
        items.append(
            CandidateItem(
                source="reddit_selfhosted",
                title=title,
                url=url,
                tagline=summary or title[:140],
                metrics={
                    "author": entry.findtext(f"{_ATOM}author/{_ATOM}name"),
                    "updated": entry.findtext(f"{_ATOM}updated"),
                },
                topics=["selfhosted"],
                rank=i,
            )
        )
    if not items:
        raise SourceError("Reddit parsed zero usable posts")
    logger.info("reddit_selfhosted_fetch_ok count=%d", len(items))
    return items


class RedditSelfHostedAdapter:
    name = "reddit_selfhosted"

    def __init__(self, limit: int = 20):
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        resp = httpx.get(
            REDDIT_URL, headers=BROWSER_HEADERS, timeout=30, follow_redirects=True
        )
        if resp.status_code == 429:
            raise SourceError("Reddit rate-limited (HTTP 429)")
        if resp.status_code != 200:
            raise SourceError(f"Reddit HTTP {resp.status_code}")
        return parse_reddit(resp.text, limit=self._limit)
