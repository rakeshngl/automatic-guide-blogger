import logging

import httpx
from bs4 import BeautifulSoup

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

HOME_URL = "https://theresanaiforthat.com/"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def parse_home(html: str, limit: int = 20) -> list[CandidateItem]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("div.home-today-row")
    if not rows:
        raise SourceError("TAAFT markup changed: no div.home-today-row found")
    items: list[CandidateItem] = []
    for i, row in enumerate(rows, start=1):
        link = row.select_one("a.home-today-image-link[href]")
        if not link:
            continue
        name = link.get("aria-label") or link.get_text(strip=True)
        href = link["href"]
        if not name or "/ai/" not in href:
            continue
        topic_el = row.select_one("div.home-today-topic-cell")
        views_el = row.select_one("span.home-today-views-value")
        date_el = row.select_one("span.home-today-date-value")
        items.append(
            CandidateItem(
                source="taaft",
                title=name.strip(),
                url=href,
                tagline="",
                metrics={
                    "topic": topic_el.get_text(strip=True) if topic_el else None,
                    "views": views_el.get_text(strip=True) if views_el else None,
                    "listed_ago": date_el.get_text(strip=True) if date_el else None,
                },
                rank=i,
            )
        )
        if len(items) >= limit:
            break
    if not items:
        raise SourceError("TAAFT parsed zero usable rows")
    logger.info("taaft_fetch_ok count=%d", len(items))
    return items


class TaaftAdapter:
    name = "taaft"

    def __init__(self, limit: int = 20):
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        resp = httpx.get(HOME_URL, headers=BROWSER_HEADERS, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            raise SourceError(f"TAAFT HTTP {resp.status_code}")
        return parse_home(resp.text, limit=self._limit)
