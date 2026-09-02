import json
import pathlib

import pytest

from guides_writer.sources.base import SourceError, dedupe
from guides_writer.sources.devto import parse_devto
from guides_writer.sources.github_trending import parse_trending
from guides_writer.sources.gmail_digest import parse_digest_email
from guides_writer.sources.hf_spaces import parse_hf_spaces
from guides_writer.sources.hn_show import parse_hn
from guides_writer.sources.producthunt import parse_posts
from guides_writer.sources.reddit import parse_reddit
from guides_writer.sources.reddit_digest import (
    RedditDigestAdapter,
    extract_ideas,
    parse_digest_payload,
)
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


class TestHFSpace:
    def test_parses_rows(self):
        data = json.loads(_read("hf_spaces.json"))
        items = parse_hf_spaces(data)
        assert len(items) == 20
        assert all(item.source == "hf_spaces" for item in items)

    def test_fields(self):
        data = json.loads(_read("hf_spaces.json"))
        items = parse_hf_spaces(data)
        first = items[0]
        assert "/" in first.title
        assert first.url.startswith("https://huggingface.co/spaces/")
        assert first.rank == 1
        assert isinstance(first.metrics["likes"], int)

    def test_bad_payload_raises(self):
        with pytest.raises(SourceError):
            parse_hf_spaces({})

    def test_empty_list_raises(self):
        with pytest.raises(SourceError):
            parse_hf_spaces([])


class TestDevTo:
    def test_parses_rows(self):
        data = json.loads(_read("devto.json"))
        items = parse_devto(data)
        assert len(items) == 20
        assert all(item.source == "devto" for item in items)

    def test_fields(self):
        data = json.loads(_read("devto.json"))
        items = parse_devto(data)
        first = items[0]
        assert first.title
        assert first.url.startswith("https://dev.to/")
        assert first.rank == 1
        assert isinstance(first.metrics["reactions"], int)

    def test_bad_payload_raises(self):
        with pytest.raises(SourceError):
            parse_devto({})

    def test_empty_list_raises(self):
        with pytest.raises(SourceError):
            parse_devto([])


class TestReddit:
    def test_parses_rows(self):
        items = parse_reddit(_read("reddit_selfhosted.xml"))
        assert len(items) >= 20
        assert all(item.source == "reddit_selfhosted" for item in items)

    def test_fields(self):
        items = parse_reddit(_read("reddit_selfhosted.xml"))
        first = items[0]
        assert first.title
        assert first.url.startswith("https://www.reddit.com/")
        assert first.rank == 1
        assert "selfhosted" in first.topics

    def test_broken_markup_raises(self):
        with pytest.raises(SourceError):
            parse_reddit("<html><body>blocked</body></html>")

    def test_empty_feed_raises(self):
        with pytest.raises(SourceError):
            parse_reddit("<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry></entry></feed>")


class TestDedupe:
    def test_removes_same_url(self):
        from guides_writer.sources.base import CandidateItem

        a = CandidateItem(source="x", title="a", url="https://e/1")
        b = CandidateItem(source="y", title="b", url="https://e/1/")
        out = dedupe([a, b])
        assert len(out) == 1


class TestRedditDigest:
    def test_parses_rows(self):
        data = json.loads(_read("reddit_digest.json"))
        entries = parse_digest_payload(data)
        assert len(entries) == 6
        assert all(entry.title for entry in entries)
        assert entries[0].subreddit == "selfhosted"
        assert entries[5].subreddit == "webdev"
        assert entries[0].url.startswith("https://www.reddit.com/")

    def test_missing_digests_raises(self):
        with pytest.raises(SourceError):
            parse_digest_payload({})

    def test_empty_entries_raises(self):
        with pytest.raises(SourceError):
            parse_digest_payload({"digests": [{"entries": []}]})

    def test_adapter_local_file_without_llm(self, tmp_path):
        adapter = RedditDigestAdapter(
            local_file=str(FIXTURES / "reddit_digest.json"), audit=False
        )
        items = adapter.fetch()
        assert len(items) == 6
        assert all(item.source == "reddit_digest" for item in items)
        assert all(item.url and item.tagline for item in items)

    def test_extract_ideas_maps_thread_urls_through_llm(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def chat_json(self, messages, schema, **kwargs):
                self.calls += 1
                return schema.model_validate(
                    {
                        "ideas": [
                            {
                                "title": "Local-First AI Job Search Agent",
                                "angle": "Agentic job aggregation",
                                "why_guide_worthy": "Build your own",
                                "thread_url": "https://www.reddit.com/r/selfhosted/comments/abc/local_first_ai_job_agent/",
                            }
                        ]
                    }
                )

        data = json.loads(_read("reddit_digest.json"))
        entries = parse_digest_payload(data)
        ideas = extract_ideas(entries, FakeLLM())
        assert len(ideas) == 1
        assert ideas[0].thread_url == entries[0].url  # survived the round-trip

    def test_extract_ideas_drops_unknown_urls(self):
        class FakeLLM:
            def chat_json(self, messages, schema, **kwargs):
                return schema.model_validate(
                    {
                        "ideas": [
                            {
                                "title": "Totally Made Up Thing",
                                "angle": "a",
                                "why_guide_worthy": "b",
                                "thread_url": "https://example.com/nope",
                            }
                        ]
                    }
                )

        data = json.loads(_read("reddit_digest.json"))
        entries = parse_digest_payload(data)
        ideas = extract_ideas(entries, FakeLLM())
        assert ideas == []


class TestGmailDigest:
    def test_parses_mime_digest(self):
        raw = (FIXTURES / "gmail_reddit_digest.eml").read_bytes()
        entries = parse_digest_email(raw)
        assert len(entries) == 2
        first = entries[0]
        assert first.title.startswith("We built a local-first")
        assert first.url == "https://www.reddit.com/r/selfhosted/comments/abc/local_first_ai_job_agent"
        assert first.subreddit == "selfhosted"
        assert "ranks them by your skills" in first.snippet

    def test_empty_or_text_only_email_raises(self):
        bad = (
            b"From: x@y.z\nTo: a@b.c\nSubject: no html\n\nJust some words, no links."
        )
        with pytest.raises(SourceError):
            parse_digest_email(bad)
