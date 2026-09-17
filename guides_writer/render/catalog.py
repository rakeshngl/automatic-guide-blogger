import logging
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


CARD_TEMPLATE = """        <article class="post-card" data-href="html/{href}" tabindex="0" role="link" aria-label="{title}">
            <div class="card-eyebrow">{eyebrow}</div>
            <h2><a href="html/{href}">{title}</a></h2>
            <p class="post-card-desc">{desc}</p>
            <div class="read-more">
                Read Blueprint 
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
            </div>
            <div class="post-card-meta">
                <span class="meta-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                    {est} min setup
                </span>
                <span class="meta-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                    {stack}
                </span>
                <span class="meta-item">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
                    Posted on: {date}
                </span>
            </div>
        </article>"""


def _stack_from_result(result: dict) -> str:
    tags = result.get("tags") or []
    if tags:
        return " \u00b7 ".join(tags[:3])
    source = result.get("source", "")
    return source.replace("_", " ").title() or "Guide"


def patch_catalog(index_path: Path, results: list[dict]) -> int:
    if not index_path.exists():
        logger.warning("catalog_missing path=%s", index_path)
        return 0

    html = index_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")
    grid = soup.select_one("div.blog-grid")
    if not grid:
        logger.warning("catalog_no_grid path=%s", index_path)
        return 0

    html_dir = index_path.resolve().parent / "html"

    purged = 0
    for card in list(soup.select("div.blog-grid [data-href]")):
        href = card.get("data-href", "")
        if not href:
            continue
        target = html_dir / href.replace("html/", "")
        if not target.exists():
            logger.warning("catalog_purged_dangling href=%s", href)
            card.decompose()
            purged += 1

    existing_hrefs = {a.get("href", "") for a in soup.select("div.blog-grid a[href]")}
    today = datetime.now(timezone.utc).strftime("%b %d, %Y")

    inserted = 0
    for result in results:
        if result.get("status") != "ok":
            continue
        file_path = result.get("file", "")
        href = Path(file_path).name if file_path else ""
        if not href or f"html/{href}" in existing_hrefs or href in existing_hrefs:
            continue
        if not (html_dir / href).exists():
            logger.warning("catalog_skip_missing_file href=%s", href)
            continue

        eyebrow = (result.get("navbar_badge") or result.get("source", "Guide")).strip() or "Guide"
        title = result.get("title", href)
        desc = (result.get("tagline") or result.get("title", ""))[:180]
        est = result.get("est_minutes") or 40
        stack = _stack_from_result(result)

        card_html = CARD_TEMPLATE.format(
            eyebrow=eyebrow,
            href=href,
            title=title,
            desc=desc,
            est=est,
            stack=stack,
            date=today,
        )
        card_soup = BeautifulSoup(card_html, "lxml")
        article = card_soup.select_one("article.post-card")
        if article:
            grid.insert(0, article)
            existing_hrefs.add(f"html/{href}")
            inserted += 1

    if inserted:
        index_path.write_text(str(soup), encoding="utf-8")
        logger.info("catalog_patched inserted=%d path=%s", inserted, index_path)
    elif purged:
        index_path.write_text(str(soup), encoding="utf-8")
        logger.info("catalog_patched purged_only purged=%d path=%s", purged, index_path)

    return inserted
