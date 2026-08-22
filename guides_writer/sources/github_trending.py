import logging
import re

import httpx
from bs4 import BeautifulSoup

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending?since=daily"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_int(text: str) -> int | None:
    digits = re.sub(r"[,\s]", "", text)
    match = re.search(r"\d+", digits)
    return int(match.group()) if match else None


def parse_trending(html: str) -> list[CandidateItem]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("article.Box-row")
    if not rows:
        raise SourceError("GitHub Trending markup changed: no article.Box-row found")
    items: list[CandidateItem] = []
    for i, row in enumerate(rows, start=1):
        repo_link = row.select_one("h2 a[href]")
        if not repo_link:
            continue
        full_name = repo_link["href"].strip("/")
        desc_el = row.select_one("p")
        lang_el = row.select_one('[itemprop="programmingLanguage"]')
        stars_el = row.select_one('a[href$="/stargazers"]')
        stars_today_el = row.select_one("span.d-inline-block.float-sm-right")
        items.append(
            CandidateItem(
                source="github_trending",
                title=full_name.split("/", 1)[-1],
                url=f"https://github.com/{full_name}",
                tagline=desc_el.get_text(strip=True) if desc_el else "",
                metrics={
                    "full_name": full_name,
                    "language": lang_el.get_text(strip=True) if lang_el else None,
                    "stars_total": _parse_int(stars_el.get_text()) if stars_el else None,
                    "stars_today": _parse_int(stars_today_el.get_text()) if stars_today_el else None,
                },
                rank=i,
            )
        )
    if not items:
        raise SourceError("GitHub Trending parsed zero usable rows")
    logger.info("github_trending_fetch_ok count=%d", len(items))
    return items


class GitHubTrendingAdapter:
    name = "github_trending"

    def fetch(self) -> list[CandidateItem]:
        resp = httpx.get(TRENDING_URL, headers=BROWSER_HEADERS, timeout=30, follow_redirects=True)
        if resp.status_code != 200:
            raise SourceError(f"GitHub Trending HTTP {resp.status_code}")
        return parse_trending(resp.text)
