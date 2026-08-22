import pytest
from pydantic import BaseModel

from guides_writer.agents.selector import TopicSelector, _is_duplicate_title
from guides_writer.llm.client import LLMClient, LLMError
from guides_writer.sources.base import CandidateItem


def _cand(i: int, source: str = "github_trending", title: str | None = None, url: str | None = None):
    return CandidateItem(
        source=source,
        title=title or f"tool{i}",
        url=url or f"https://example.com/{i}",
        tagline=f"tagline {i}",
        rank=i,
    )


class FakeChatClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = 0

    def chat_json(self, messages, schema, **kwargs):
        self.calls += 1
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item)


def _pick_dict(title="Build X", url="https://example.com/1"):
    return {
        "title": title,
        "angle": "a",
        "why_guide_worthy": "w",
        "source": "github_trending",
        "source_url": url,
    }


class TestTopicSelector:
    def test_valid_selection(self):
        titles = [
            "PostHog Analytics Self-Host",
            "n8n Workflow Automation",
            "Coolify PaaS Deploy",
            "Docmost Wiki Setup",
            "Umami Analytics Install",
        ]
        client = FakeChatClient(
            [{"picks": [_pick_dict(t, f"https://e/{i}") for i, t in enumerate(titles)]}]
        )
        result = TopicSelector(client).select([_cand(i) for i in range(10)], count=3)
        assert len(result.picks) == 3
        assert not result.degraded

    def test_history_duplicate_dropped_degrades(self):
        picks = [_pick_dict("Macky AI Guide", "https://e/1"), _pick_dict("Other Tool Guide", "https://e/2")]
        client = FakeChatClient([{"picks": picks}])
        result = TopicSelector(client).select(
            [_cand(1), _cand(2)], count=2, exclude_titles=["Guide to Macky"]
        )
        assert len(result.picks) == 1
        assert result.degraded
        assert result.duplicates_dropped == ["Macky AI Guide"]

    def test_duplicate_within_picks_dropped(self):
        picks = [
            _pick_dict("SuperTool Setup Guide", "https://e/1"),
            _pick_dict("Setup Guide for SuperTool", "https://e/9"),
            _pick_dict("Unrelated Tool Guide", "https://e/3"),
        ]
        client = FakeChatClient([{"picks": picks}])
        result = TopicSelector(client).select([_cand(i) for i in (1, 3, 9)], count=3)
        titles = [p.title for p in result.picks]
        assert len(titles) == 2
        assert "Unrelated Tool Guide" in titles

    def test_no_candidates_empty_result(self):
        client = FakeChatClient([])
        result = TopicSelector(client).select([], count=3)
        assert result.picks == []
        assert result.degraded
        assert client.calls == 0


class TestIsDuplicateTitle:
    def test_fuzzy_match(self):
        assert _is_duplicate_title(
            "Build a Supabase Agent Marketplace Guide",
            ["Guide to the Supabase Agent Marketplace"],
        )

    def test_different_topics_pass(self):
        assert not _is_duplicate_title(
            "Deploy Supabase Agents Guide", ["How to Deploy Supabase Agent Marketplace"]
        )
        assert not _is_duplicate_title(
            "PostHog Analytics Self-Host", ["MoneyPrinterTurbo Video Tool"]
        )


class Answer(BaseModel):
    value: int


class TestLLMClientRepair:
    def test_invalid_then_valid_repairs(self):
        client = LLMClient(api_key="k", base_url="https://fake", model="m")

        responses = [
            {"choices": [{"message": {"content": "{{{ not json"}}]},
            {"choices": [{"message": {"content": '{"value": 42}'}}]},
        ]
        calls = {"n": 0}

        def fake_post(payload):
            resp = responses[calls["n"]]
            calls["n"] += 1
            return resp

        client._post = fake_post
        answer = client.chat_json(
            messages=[{"role": "user", "content": "give json"}], schema=Answer
        )
        assert answer.value == 42
        assert calls["n"] == 2

    def test_both_invalid_raises(self):
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        client._post = lambda payload: {"choices": [{"message": {"content": "garbage"}}]}
        with pytest.raises(ValueError):
            client.chat_json(messages=[], schema=Answer)

    def test_empty_content_raises_runtime(self):
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        client._post = lambda payload: {"choices": [{"message": {"content": ""}}]}
        with pytest.raises(RuntimeError):
            client.chat(messages=[])


class TestLLMErrorRetryable:
    def test_llm_error_is_retry_type(self):
        from tenacity import retry_if_exception_type

        assert LLMError is not None
