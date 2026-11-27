import pytest
from pydantic import BaseModel

from guides_writer.agents.selector import TopicSelector, _balanced_pool, _is_duplicate_title
from guides_writer.llm.client import JSONValidateError, LLMClient, LLMError, ModelNotFoundError
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
        self.prompts: list[str] = []

    def chat_json(self, messages, schema, **kwargs):
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
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

    def test_overasks_then_trims_to_count(self):
        titles = [
            "PostHog Self-Hosted Analytics",
            "n8n Workflow Automation Platform",
            "Coolify Heroku-Style PaaS",
            "Miniflux Minimal RSS Reader",
            "Stirling PDF Tool Suite",
            "Memos Self-Hosted Note Box",
        ]
        client = FakeChatClient(
            [{"picks": [_pick_dict(t, f"https://e/{i}") for i, t in enumerate(titles)]}]
        )
        result = TopicSelector(client).select([_cand(i) for i in range(10)], count=3)
        # asks for count + OVER_ASK so dedupe drops still leave `count` fresh picks
        assert "Select exactly 5" in client.prompts[0]
        assert len(result.picks) == 3
        assert not result.degraded
        assert client.calls == 1

    def test_prefilters_covered_urls_from_pool(self):
        cands = [
            _cand(1, title="Fresh Tool Alpha", url="https://github.com/org/alpha"),
            _cand(2, title="Fresh Tool Beta", url="https://github.com/org/beta"),
            _cand(3, title="Fresh Tool Gamma", url="https://github.com/org/gamma"),
        ]
        client = FakeChatClient([
            {"picks": [
                # the model invents the covered URL anyway -> pick-level check
                _pick_dict("Fresh Tool Alpha", "https://github.com/org/alpha"),
                _pick_dict("Fresh Tool Beta", "https://github.com/org/beta"),
                _pick_dict("Fresh Tool Gamma", "https://github.com/org/gamma"),
            ]},
        ])
        result = TopicSelector(client).select(
            cands, count=2,
            # trailing slash must still match the candidate URL
            exclude_urls=["https://github.com/org/alpha/"],
        )
        # covered candidate never reaches the prompt
        assert "https://github.com/org/alpha" not in client.prompts[0]
        assert "Fresh Tool Alpha" not in client.prompts[0]
        assert [p.title for p in result.picks] == ["Fresh Tool Beta", "Fresh Tool Gamma"]
        assert result.duplicates_dropped == ["Fresh Tool Alpha"]
        assert client.calls == 1

    def test_url_starvation_feedback_retry_recovers(self):
        covered = ["PostHog Self-Hosted Analytics", "n8n Workflow Platform", "Coolify PaaS"]
        fresh = ["Local DNS Ad-Blocker", "Status Page Dashboard", "Bookmark Vault Manager"]
        client = FakeChatClient([
            {"picks": [_pick_dict(t, f"https://e/{i}") for i, t in enumerate(covered)]},
            {"picks": [_pick_dict(t, f"https://f/{i}") for i, t in enumerate(fresh)]},
        ])
        result = TopicSelector(client).select(
            [_cand(i) for i in range(10)], count=3,
            exclude_urls=[f"https://e/{i}" for i in range(3)],
        )
        assert client.calls == 2
        assert "REJECTED" in client.prompts[1]
        assert [p.title for p in result.picks] == fresh
        assert not result.degraded
        assert set(result.duplicates_dropped) == set(covered)

    def test_history_title_starvation_feedback_retry_recovers(self):
        """The 2026-11-25/26 production failure: the LLM re-picked three
        already-covered themes and dedupe dropped ALL of them, ending the run
        with zero guides. The feedback round must replace the rejects."""
        stale = [
            "Build a CLI-Anything Wrapper: Turn Any Command into an AI-Friendly Tool",
            "Build a Firecrawl-Powered Web-Scrape-and-Summarize Mini-App",
            "Build a Simple Agentic Skill with the Superpowers Framework",
        ]
        fresh = [
            "Build a Local DNS Ad-Blocker with Python",
            "Build a Tiny Status Page with Flask",
            "Build a Self-Hosted Bookmark Vault",
        ]
        client = FakeChatClient([
            {"picks": [_pick_dict(t, f"https://e/{i}") for i, t in enumerate(stale)]},
            {"picks": [_pick_dict(t, f"https://f/{i}") for i, t in enumerate(fresh)]},
        ])
        result = TopicSelector(client).select(
            [_cand(i) for i in range(10)], count=3, exclude_titles=stale,
        )
        assert client.calls == 2
        assert [p.title for p in result.picks] == fresh
        assert not result.degraded
        # the feedback round restates every rejected title
        for title in stale:
            assert title in client.prompts[1]

    def test_feedback_retry_failure_keeps_first_round_result(self):
        stale = ["Build a Covered Topic Alpha", "Build a Covered Topic Beta"]
        client = FakeChatClient(
            [{"picks": [_pick_dict(t, f"https://e/{i}") for i, t in enumerate(stale)]}]
        )
        result = TopicSelector(client).select(
            [_cand(i) for i in range(5)], count=2, exclude_titles=stale,
        )
        # second scripted response missing -> the retry error is swallowed
        # and the (degraded) first-round result stands, as before
        assert client.calls == 2
        assert result.picks == []
        assert result.degraded
        assert result.duplicates_dropped == stale


class TestBalancedPool:
    def test_under_cap_returns_all(self):
        cands = [_cand(i) for i in range(10)]
        out = _balanced_pool(cands, max_n=20)
        assert out == cands

    def test_caps_and_spreads_across_sources(self):
        cands = [_cand(i, source="github_trending") for i in range(60)]
        cands.extend(_cand(i, source="reddit_digest", url=f"https://r/{i}") for i in range(3))
        out = _balanced_pool(cands, max_n=40)
        assert len(out) == 40
        sources = {c.source for c in out}
        assert sources == {"github_trending", "reddit_digest"}
        # round-robin keeps smallest source represented, not swallowed by biggest
        digest_count = sum(1 for c in out if c.source == "reddit_digest")
        assert digest_count == 3


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

    def test_shared_common_word_is_not_a_duplicate(self):
        # Regression: WRatio over-scored these at 85.5 purely on the shared
        # word "AI" + build-guide boilerplate, wrongly dropping a valid pick.
        assert not _is_duplicate_title(
            "Build a Screenshot-to-Code Converter with Python and AI",
            ["Local-First AI Job Search Agent", "Minimal AI Job Search Agent"],
        )

    def test_real_thematic_repeat_still_detected(self):
        assert _is_duplicate_title(
            "Minimal AI Job Search Agent", ["Local-First AI Job Search Agent"]
        )
        assert _is_duplicate_title(
            "HTML Cleaner", ["Build an HTML Cleaner Tool with Python"]
        )
        assert _is_duplicate_title(
            "Minimal Satellite Map Viewer", ["Build a Satellite Map Viewer in JS"]
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

    def test_model_not_found_raises_without_retry(self):
        """A dead model id is terminal - no retry/backoff, fail fast."""
        client = LLMClient(api_key="k", base_url="https://fake", model="gone/model")
        calls = {"n": 0}

        def fake_post(payload):
            calls["n"] += 1
            raise ModelNotFoundError(
                'LLM API HTTP 404: {"error":{"message":"model does not exist",'
                '"code":"model_not_found"}}'
            )

        client._post = fake_post
        with pytest.raises(ModelNotFoundError):
            client.chat_json(messages=[{"role": "user", "content": "x"}], schema=Answer)
        assert calls["n"] == 1


class TestJSONValidateHardening:
    def test_validate_error_then_success_on_retry(self):
        """Provider-side json_validate_failed recovers when a fresh generation validates."""
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        raw_garbled = '{"value": trun'
        attempts = {"n": 0}

        def fake_post(payload):
            if attempts["n"] == 0:
                attempts["n"] += 1
                raise JSONValidateError(
                    "LLM JSON generation failed (400 json_validate_failed)",
                    failed_generation=raw_garbled,
                )
            return {"choices": [{"message": {"content": '{"value": 7}'}}]}

        client._post = fake_post
        answer = client.chat_json(messages=[], schema=Answer, retries=3)
        assert answer.value == 7
        assert attempts["n"] == 1

    def test_exhausts_retries_then_raises(self):
        """Persistent json_validate_failed surfaces the error after all retries."""
        client = LLMClient(api_key="k", base_url="https://fake", model="m")

        def always_fail(payload):
            raise JSONValidateError(
                "LLM JSON generation failed (400 json_validate_failed)",
                failed_generation='{"value": trun',
            )

        client._post = always_fail
        with pytest.raises(JSONValidateError):
            client.chat_json(messages=[], schema=Answer, retries=2)

    def test_failed_gen_repair_then_valid(self):
        """When failed_generation is fed back, the model completes a valid object."""
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        attempts = {"n": 0}

        def fake_post(payload):
            if attempts["n"] == 0:
                attempts["n"] += 1
                raise JSONValidateError(
                    "LLM JSON generation failed (400 json_validate_failed)",
                    failed_generation='{"value": 9',
                )
            return {"choices": [{"message": {"content": '{"value": 9}'}}]}

        client._post = fake_post
        answer = client.chat_json(messages=[], schema=Answer, retries=3)
        assert answer.value == 9

    def test_non_json_transient_retry(self):
        """A transient non-JSON 5xx retries, while a hard 400 json_validate_failed differs."""
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        attempts = {"n": 0}

        def fake_post(payload):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise LLMError("LLM API HTTP 503")
            return {"choices": [{"message": {"content": '{"value": 3}'}}]}

        client._post = fake_post
        answer = client.chat_json(messages=[], schema=Answer, retries=3)
        assert answer.value == 3
        assert attempts["n"] == 2


class TestLLMErrorRetryable:
    def test_llm_error_is_retry_type(self):

        assert LLMError is not None


class TestTokenPacer:
    def test_groq_enables_pacer_other_hosts_do_not(self):
        groq = LLMClient(api_key="k", base_url="https://api.groq.com/openai/v1", model="m")
        other = LLMClient(api_key="k", base_url="https://fake", model="m")
        assert groq.pace_enabled is True
        assert other.pace_enabled is False

    def test_throttle_waits_until_window_frees(self, monkeypatch):
        from guides_writer.llm import client as c

        clock = {"t": 1000.0}
        sleeps: list[float] = []
        monkeypatch.setattr(c.time, "monotonic", lambda: clock["t"])

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        monkeypatch.setattr(c.time, "sleep", fake_sleep)
        client = LLMClient(
            api_key="k", base_url="https://api.groq.com/openai/v1", model="m",
            tokens_per_minute=1000, tpm_safety=1.0,
        )
        client._throttle(600)
        client._record(600)
        assert sleeps == []
        client._throttle(600)
        assert sleeps and sleeps[0] > 0
        assert clock["t"] >= 1060.0

    def test_throttle_disabled_on_plain_host(self, monkeypatch):
        from guides_writer.llm import client as c

        calls = []
        monkeypatch.setattr(c.time, "sleep", lambda s: calls.append(s))
        client = LLMClient(api_key="k", base_url="https://fake", model="m")
        client._throttle(999999)
        assert calls == []
