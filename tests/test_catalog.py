from pathlib import Path

import pytest

from guides_writer.render.catalog import patch_catalog

GRID = """<!DOCTYPE html>
<html>
<body>
<main class="container">
<div class="blog-grid">
<article class="post-card" data-href="html/old-real.html" tabindex="0" role="link" aria-label="Old Real">
    <div class="card-eyebrow">Python Build</div>
    <h2><a href="html/old-real.html">Old Real</a></h2>
    <p class="post-card-desc">desc</p>
    <div class="read-more">Read Blueprint</div>
</article>
<article class="post-card" data-href="html/dangling.html" tabindex="0" role="link" aria-label="Dangling">
    <div class="card-eyebrow">web analytics</div>
    <h2><a href="html/dangling.html">Dangling</a></h2>
    <p class="post-card-desc">desc</p>
    <div class="read-more">Read Blueprint</div>
</article>
</div>
</main>
</body>
</html>
"""


@pytest.fixture
def site(tmp_path):
    index = tmp_path / "index.html"
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "old-real.html").write_text("<html></html>", encoding="utf-8")
    index.write_text(GRID, encoding="utf-8")
    return tmp_path


def _result(href: str, title: str = "Fresh") -> dict:
    return {
        "status": "ok",
        "file": str(Path("out/html") / href),
        "title": title,
        "tagline": "a tagline",
        "navbar_badge": "Python Build",
        "source": "github",
        "est_minutes": 40,
        "tags": ["Python"],
    }


def _card_hrefs(index: Path):
    text = index.read_text(encoding="utf-8")
    import re

    return re.findall(r'data-href="(html/[^"]+)"', text)


def test_purges_dangling_card_but_keeps_real(site, caplog):
    index = site / "index.html"
    assert patch_catalog(index, []) == 0
    hrefs = _card_hrefs(index)
    assert "html/old-real.html" in hrefs
    assert "html/dangling.html" not in hrefs


def test_skips_insert_when_file_missing(site):
    index = site / "index.html"
    assert patch_catalog(index, [_result("missing-file.html")]) == 0
    assert "html/missing-file.html" not in _card_hrefs(index)


def test_inserts_card_for_existing_file(site):
    index = site / "index.html"
    (site / "html" / "fresh-guide.html").write_text("<html></html>", encoding="utf-8")
    assert patch_catalog(index, [_result("fresh-guide.html")]) == 1
    assert "html/fresh-guide.html" in _card_hrefs(index)


def test_skips_existing_and_duplicate_hrefs(site):
    index = site / "index.html"
    (site / "html" / "old-real.html").write_text("<html></html>", encoding="utf-8")
    assert patch_catalog(index, [_result("old-real.html")]) == 0
    assert _card_hrefs(index).count("html/old-real.html") == 1
