
import httpx
import pytest

from guides_writer.agents.selector import RawSelection
from guides_writer.agents.writer import GuideOutline
from guides_writer.llm.client import LLMError
from guides_writer.pipeline import run_pipeline
from guides_writer.render.sample_data import SAMPLE_GUIDE
from guides_writer.sources.base import CandidateItem, SourceError


class SettingsLite:
    dry_run = True
    guides_per_run = 3
    history_days = 14


def _cand(i: int):
    return CandidateItem(
        source="github_trending",
        title=f"tool{i}",
        url=f"https://example.com/{i}",
        tagline=f"tagline {i}",
        metrics={"full_name": f"org/tool{i}"},
        rank=i,
    )


class BoomAdapter:
    name = "boom"

    def fetch(self):
        raise SourceError("simulated scraper outage")


class GoodAdapter:
    name = "good"

    def __init__(self, n=5):
        self._n = n

    def fetch(self):
        return [_cand(i) for i in range(1, self._n + 1)]


def _valid_outline():
    return GuideOutline(
        title="Deploy Tool X",
        tagline="Self-host Tool X on Docker.",
        intro_points=["Welcome to another official **UVF IT** blueprint.", "Today we deploy Tool X."],
        warning_heading="Read this first",
        warning_bullets=["Stays local: everything.", "Leaves machine: nothing."],
        checklist_rows=[
            {"check": "running", "command_why": "`docker compose ps` shows Up"},
            {"check": "persisting", "command_why": "restart keeps data"},
            {"check": "port closed", "command_why": "`curl` fails externally"},
        ],
        diagram={
            "title": "Blueprint",
            "groups": [{"id": "local", "label": "Local", "style": "local"}],
            "nodes": [
                {"id": "a", "label": "User"},
                {"id": "b", "label": "Tool X", "group": "local", "style": "tech"},
                {"id": "c", "label": "DB", "group": "local"},
            ],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
        },
        phases=[
            {"title": f"Phase {i}: step", "prose_points": ["point"], "code_blocks": [{"lang": "bash", "purpose": "run"}]}
            for i in range(1, 5)
        ],
    )


def _selection():
    return RawSelection.model_validate(
        {"picks": [
            {"title": t, "angle": "a", "why_guide_worthy": "w", "source": "github_trending", "source_url": f"https://example.com/{i}"}
            for i, t in enumerate(["PostHog Self-Host", "n8n Automation", "Coolify Deploy"], 1)
        ]}
    )


class FakeLLM:
    def __init__(self, script=None):
        self.script = script or {}
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def chat(self, *a, **kw):
        raise LLMError("should not be called in this drill")

    def chat_json(self, messages, schema, **kw):
        name = schema.__name__
        self.calls.append(name)
        if name == "RawSelection":
            self.prompts.append(str(messages[-1].get("content", "")))
        queue = self.script.get(name)
        if queue is not None and queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item if isinstance(item, schema) else schema.model_validate(item)
        from guides_writer.agents.writer import GuidePart1, GuidePart2
        defaults = {
            "RawSelection": _selection(),
            "GuideOutline": _valid_outline(),
            "GuidePart1": GuidePart1(
                meta=SAMPLE_GUIDE.meta, intro=list(SAMPLE_GUIDE.intro),
                warning_box=SAMPLE_GUIDE.warning_box, diagram=SAMPLE_GUIDE.diagram,
                phases=list(SAMPLE_GUIDE.phases[:3])),
            "GuidePart2": GuidePart2(
                phases=list(SAMPLE_GUIDE.phases[3:]),
                checklist=list(SAMPLE_GUIDE.checklist), closing_alert=SAMPLE_GUIDE.closing_alert),
        }
        return schema.model_validate(defaults[name].model_dump(exclude_none=True))


class TestScraperIsolation:
    def test_one_dead_source_still_succeeds(self, tmp_path):
        summary = run_pipeline(
            SettingsLite(), adapters=[BoomAdapter(), GoodAdapter(5)],
            llm_client=FakeLLM(), out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "ok"
        assert "boom" in summary["failed_sources"]
        assert len(list(tmp_path.glob("*.html"))) == 3

    def test_all_scrapers_dead(self, tmp_path):
        summary = run_pipeline(
            SettingsLite(), adapters=[BoomAdapter(), BoomAdapter()],
            llm_client=FakeLLM(), out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "no_candidates"
        assert summary["failed_sources"] == ["boom", "boom"]
        # failure summaries carry the full key set so the run report and
        # run-file consumers never print None for these fields
        assert summary["dry_run"] is True
        assert summary["guides"] == []
        assert summary["delivery_ids"] == []
        assert summary["delivery_errors"] == []
        assert summary["catalog_patched"] == 0


class TestSelectionStarvation:
    """2026-11-25/26 production failure: every pick the LLM proposed was
    already covered, dedupe dropped all of them, and the run died with
    status=selection_failed and zero guides (exit 3)."""

    def _seed_history(self, tmp_path) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        seeded = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        titles = ["PostHog Self-Host", "n8n Automation", "Coolify Deploy"]
        history = {
            "guides": [
                {"date": seeded, "title": t, "source_url": f"https://example.com/{i}"}
                for i, t in enumerate(titles, 1)
            ]
        }
        tmp_path.joinpath("history.json").write_text(json.dumps(history), encoding="utf-8")

    def test_all_picks_covered_recovers_on_feedback_round(self, tmp_path):
        self._seed_history(tmp_path)
        # FakeLLM returns the default 3 picks every call: round 1 is fully
        # rejected (title AND url match history), the feedback round gets the
        # same answer again -> still starved -> selection_failed, but with
        # the complete summary shape.
        fake = FakeLLM()
        summary = run_pipeline(
            SettingsLite(), adapters=[GoodAdapter(5)],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs",
            history_path=tmp_path / "history.json",
        )
        selection_calls = [c for c in fake.calls if c == "RawSelection"]
        assert len(selection_calls) == 2  # original + feedback retry
        assert summary["status"] == "selection_failed"
        assert summary["guides"] == []
        assert summary["dry_run"] is True
        assert summary["degraded_selection"] is False
        assert summary["delivery_errors"] == []
        run_file = tmp_path / "runs" / f"{summary['date']}.json"
        assert run_file.exists()

    def test_covered_candidates_removed_from_prompt(self, tmp_path):
        self._seed_history(tmp_path)
        fake = FakeLLM()
        run_pipeline(
            SettingsLite(), adapters=[GoodAdapter(5)],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs",
            history_path=tmp_path / "history.json",
        )
        assert fake.prompts, "selector never prompted"
        # candidates whose URL was already written about (example.com/1..3)
        # are pre-filtered from the pool; the fresh ones remain
        for covered in (1, 2, 3):
            assert f"https://example.com/{covered}" not in fake.prompts[0]
        assert "https://example.com/4" in fake.prompts[0]
        assert "https://example.com/5" in fake.prompts[0]


class TestBadLLMJson:
    def test_outline_always_invalid_all_guides_fail(self, tmp_path):
        bad = ValueError("schema mismatch: missing phases")
        fake = FakeLLM({"GuideOutline": [bad] * 9})
        summary = run_pipeline(
            SettingsLite(), adapters=[GoodAdapter(3)],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "all_guides_failed"
        assert all("failed" in g["status"] for g in summary["guides"])

    def test_garbage_structure_triggers_repair_then_fails(self, tmp_path):
        fake = FakeLLM({"GuideOutline": [ValueError("invalid JSON")] * 9})
        summary = run_pipeline(
            SettingsLite(), adapters=[GoodAdapter(3)],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "all_guides_failed"


class TestInvalidWebhook:
    def test_delivery_failure_recorded(self, tmp_path, monkeypatch):
        class DryRunOff:
            dry_run = False
            guides_per_run = 1
            history_days = 14

        def boom_deliver(files, results):
            raise httpx.HTTPError("webhook unreachable")

        summary = run_pipeline(
            DryRunOff(), adapters=[GoodAdapter(3)],
            llm_client=FakeLLM(), out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json",
            deliver=boom_deliver,
        )
        assert summary["status"] in ("ok", "partial")
        assert len(summary["delivery_errors"]) == 1
        assert "webhook unreachable" in summary["delivery_errors"][0]

    def test_invalid_webhook_url_raises_on_post(self, tmp_path, monkeypatch):
        import guides_writer.deliver.discord as d

        def fake_post(*a, **kw):
            raise httpx.ConnectError("DNS failure")

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(d.time, "sleep", lambda s: None)

        from guides_writer.deliver.discord import send_one
        path = tmp_path / "2026-08-22_test.html"
        path.write_text("<html>hi</html>", encoding="utf-8")
        with pytest.raises(httpx.HTTPError):
            send_one("https://discord.com/api/webhooks/x/y", path, {"title": "t"})


class TestExitCodes:
    def test_cmd_run_exit_codes(self, tmp_path, monkeypatch):
        from guides_writer.__main__ import cmd_run

        monkeypatch.setattr("guides_writer.pipeline.run_pipeline", lambda *a, **kw: {"status": "no_candidates", "delivery_errors": [], "guides": []})
        assert cmd_run(dry_run=True) == 2

        monkeypatch.setattr("guides_writer.pipeline.run_pipeline", lambda *a, **kw: {"status": "all_guides_failed", "delivery_errors": [], "guides": []})
        assert cmd_run(dry_run=True) == 3

        monkeypatch.setattr("guides_writer.pipeline.run_pipeline", lambda *a, **kw: {"status": "ok", "delivery_errors": ["boom"], "guides": [{"title": "t", "status": "ok"}]})
        assert cmd_run(dry_run=False) == 4

        monkeypatch.setattr("guides_writer.pipeline.run_pipeline", lambda *a, **kw: {"status": "ok", "delivery_errors": [], "guides": [{"title": "t", "status": "ok"}]})
        assert cmd_run(dry_run=False) == 0
