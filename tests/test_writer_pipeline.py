import pathlib

import pytest

from guides_writer.agents.selector import RawSelection
from guides_writer.agents.writer import (
    GuideOutline,
    GuidePart1,
    GuidePart2,
    GuideWriter,
    WriterError,
)
from guides_writer.llm.client import LLMError
from guides_writer.pipeline import run_pipeline
from guides_writer.render.sample_data import SAMPLE_GUIDE
from guides_writer.sources.base import CandidateItem
from guides_writer.storage.history import HistoryStore


def _part1():
    half = len(SAMPLE_GUIDE.phases) // 2 + len(SAMPLE_GUIDE.phases) % 2
    return GuidePart1(
        meta=SAMPLE_GUIDE.meta,
        intro=list(SAMPLE_GUIDE.intro),
        warning_box=SAMPLE_GUIDE.warning_box,
        diagram=SAMPLE_GUIDE.diagram,
        phases=list(SAMPLE_GUIDE.phases[:half]),
    )


def _part2():
    half = len(SAMPLE_GUIDE.phases) // 2 + len(SAMPLE_GUIDE.phases) % 2
    return GuidePart2(
        phases=list(SAMPLE_GUIDE.phases[half:]),
        checklist=list(SAMPLE_GUIDE.checklist),
        closing_alert=SAMPLE_GUIDE.closing_alert,
    )


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


class StubAdapter:
    name = "stub"

    def __init__(self, n: int = 3):
        self._n = n

    def fetch(self):
        return [_cand(i) for i in range(1, self._n + 1)]


def _valid_outline() -> GuideOutline:
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
                {"id": "user", "label": "User"},
                {"id": "app", "label": "Tool X", "group": "local", "style": "tech"},
                {"id": "db", "label": "DB", "group": "local"},
            ],
            "edges": [{"from": "user", "to": "app"}, {"from": "app", "to": "db"}],
        },
        phases=[
            {"title": f"Phase {i}: step", "prose_points": ["point"], "code_blocks": [{"lang": "bash", "purpose": "run"}]}
            for i in range(1, 5)
        ],
    )


def _invalid_outline() -> GuideOutline:
    bad = _valid_outline().model_dump(exclude_none=True)
    bad["phases"] = bad["phases"][:1]
    bad["diagram"]["nodes"] = bad["diagram"]["nodes"][:2]
    return GuideOutline.model_validate(bad)


def _selection(n: int = 3) -> RawSelection:
    titles = ["PostHog Analytics Self-Host", "n8n Workflow Automation", "Coolify PaaS Deploy"]
    return RawSelection.model_validate(
        {"picks": [
            {"title": titles[i - 1], "angle": "a", "why_guide_worthy": "w",
             "source": "github_trending", "source_url": f"https://example.com/{i}"}
            for i in range(1, n + 1)
        ]}
    )


class FakeLLM:
    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[str] = []

    def chat_json(self, messages, schema, **kwargs):
        name = schema.__name__
        self.calls.append(name)
        queue = self.script.get(name)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, schema) or type(item).__name__ == name:
                return item
            return schema.model_validate(item)
        defaults = {
            "RawSelection": _selection(),
            "GuideOutline": _valid_outline(),
            "GuidePart1": _part1(),
            "GuidePart2": _part2(),
        }
        return schema.model_validate(defaults[name].model_dump(exclude_none=True))


class TestPipelineE2E:
    def test_three_guides_rendered(self, tmp_path):
        fake = FakeLLM()
        summary = run_pipeline(SettingsLite(), adapters=[StubAdapter(3)],
                               llm_client=fake, out_dir=tmp_path,
                               runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json")
        assert summary["status"] == "ok"
        assert not summary["degraded_selection"]
        files = sorted(tmp_path.glob("*.html"))
        assert len(files) == 3
        content = files[0].read_text(encoding="utf-8")
        assert 'class="step-number"' in content
        assert "table.checklist" in content or "<table" in content
        sel_calls = [c for c in fake.calls if c == "RawSelection"]
        assert len(sel_calls) == 1
        assert fake.calls.count("GuidePart1") == 3
        assert fake.calls.count("GuidePart2") == 3

    def test_dry_run_touches_no_history(self, tmp_path):
        history_file = tmp_path / "history.json"
        run_pipeline(SettingsLite(), adapters=[StubAdapter(2)], llm_client=FakeLLM(),
                     out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json")
        assert not history_file.exists()

    def test_partial_failure_isolated(self, tmp_path):
        boom = LLMError("simulated llm outage")
        fake = FakeLLM({"GuidePart2": [_part2(), boom, _part2()]})
        summary = run_pipeline(SettingsLite(), adapters=[StubAdapter(3)],
                               llm_client=fake, out_dir=tmp_path,
                               runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json")
        statuses = [g["status"] == "ok" for g in summary["guides"]]
        assert statuses == [True, False, True]
        assert summary["status"] == "partial"

    def test_no_candidates(self, tmp_path):
        class Empty:
            name = "empty"

            def fetch(self):
                return []

        summary = run_pipeline(SettingsLite(), adapters=[Empty()], llm_client=FakeLLM(),
                               out_dir=tmp_path, runs_dir=tmp_path / "runs", history_path=tmp_path / "history.json")
        assert summary["status"] == "no_candidates"


class TestWriterRubric:
    def test_rubric_retry_then_pass(self):
        from guides_writer.agents.writer import enrich  # noqa: F401

        fake = FakeLLM({"GuideOutline": [_invalid_outline(), _valid_outline()]})
        writer = GuideWriter(fake)
        guide = writer.write_guide(_cand(1), "context text")
        assert guide.meta.title == SAMPLE_GUIDE.meta.title
        outline_calls = [c for c in fake.calls if c == "GuideOutline"]
        assert len(outline_calls) == 2

    def test_rubric_fail_raises(self):
        fake = FakeLLM({"GuideOutline": [_invalid_outline(), _invalid_outline()]})
        with pytest.raises(WriterError):
            GuideWriter(fake).write_guide(_cand(1), "context")

    def test_enrich_github_uses_readme(self, monkeypatch):
        import guides_writer.agents.writer as w

        def fake_get(url, **kwargs):
            class R:
                status_code = 200
                text = "# ToolX\nGreat readme content"

            assert "/repos/org/tool9/readme" in url
            return R()

        monkeypatch.setattr(w.httpx, "get", fake_get)
        context = w.enrich(_cand(9))
        assert "README of org/tool9" in context
        assert "Great readme content" in context
