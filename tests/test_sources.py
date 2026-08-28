import json
import pathlib

import pytest

from guides_writer.sources.base import SourceError, dedupe
from guides_writer.sources.github_trending import parse_trending
from guides_writer.sources.hn_show import parse_hn
from guides_writer.sources.producthunt import parse_posts
from guides_writer.sources.taaft import parse_home

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def _read(name: str) -> str:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture missing: {name}")
    return path.read_text(encoding="utf-8")


class TestGitHubTrending:
    def test_parses_rows(self):
        items = parse_trending(_read("github_trending.html"))
        assert len(items) >= 10
        assert all(item.source == "github_trending" for item in items)

    def test_fields(self):
        items = parse_trending(_read("github_trending.html"))
        first = items[0]
        assert "/" in first.metrics["full_name"]
        assert first.url.startswith("https://github.com/")
        assert isinstance(first.metrics["stars_today"], int)
        assert first.metrics["stars_today"] > 0

    def test_broken_markup_raises(self):
        with pytest.raises(SourceError):
            parse_trending("<html><body>captcha wall</body></html>")


class TestTaaft:
    def test_parses_rows(self):
        items = parse_home(_read("taaft_home.html"))
        assert len(items) >= 10
        assert all(item.source == "taaft" for item in items)

    def test_fields(self):
        items = parse_home(_read("taaft_home.html"))
        for item in items:
            assert "/ai/" in item.url
            assert item.title.strip()

    def test_broken_markup_raises(self):
        with pytest.raises(SourceError):
            parse_home("<html><body>blocked</body></html>")


class TestProductHunt:
    def test_parses_fixture(self):
        data = json.loads(_read("producthunt_posts.json"))
        items = parse_posts(data)
        assert len(items) == 10
        first = items[0]
        assert first.title
        assert isinstance(first.metrics["votes"], int)
        assert first.rank == 1

    def test_bad_payload_raises(self):
        with pytest.raises(SourceError):
            parse_posts({"data": {}})


class TestHNShow:
    def test_parses_rows(self):
        data = json.loads(_read("hn_show.json"))
        items = parse_hn(data)
        assert len(items) == 20
        assert all(item.source == "hn_show" for item in items)

    def test_fields(self):
        data = json.loads(_read("hn_show.json"))
        items = parse_hn(data)
        first = items[0]
        assert first.title
        assert first.url.startswith("http")
        assert first.rank == 1

    def test_bad_payload_raises(self):
        with pytest.raises(SourceError):
            parse_hn({})

    def test_empty_hits_raises(self):
        with pytest.raises(SourceError):
            parse_hn({"hits": []})


class TestDedupe:
    def test_removes_same_url(self):
        from guides_writer.sources.base import CandidateItem

        a = CandidateItem(source="x", title="a", url="https://e/1")
        b = CandidateItem(source="y", title="b", url="https://e/1/")
        out = dedupe([a, b])
        assert len(out) == 1
