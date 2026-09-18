
from guides_writer.agents.selector import RawSelection
from guides_writer.agents.writer import GuideOutline, GuidePart1, GuidePart2
from guides_writer.pipeline import run_pipeline
from guides_writer.render.content_guard import check
from guides_writer.render.renderer import render_guide
from guides_writer.render.sample_data import SAMPLE_GUIDE
from guides_writer.sources.base import CandidateItem, SourceError


class SettingsLite:
    dry_run = True
    guides_per_run = 3
    history_days = 14
    content_guard_enabled = True
    content_guard_max_external_links = 6


class GoodAdapter:
    name = "good"

    def fetch(self):
        return [
            CandidateItem(
                source="github_trending",
                title=f"tool{i}",
                url=f"https://example.com/{i}",
                tagline=f"tagline {i}",
                metrics={"full_name": f"org/tool{i}"},
                rank=i,
            )
            for i in range(1, 4)
        ]


class BoomAdapter:
    name = "boom"

    def fetch(self):
        raise SourceError("simulated scraper outage")


def _selection():
    return RawSelection.model_validate(
        {"picks": [
            {"title": t, "angle": "a", "why_guide_worthy": "w",
             "source": "github_trending", "source_url": f"https://example.com/{i}"}
            for i, t in enumerate(["PostHog Self-Host", "n8n Automation", "Coolify Deploy"], 1)
        ]}
    )


def _outline():
    return GuideOutline(
        title="Deploy Tool X",
        tagline="Self-host Tool X on Docker.",
        intro_points=["Welcome to another official **UVF IT** blueprint."],
        warning_heading="Read this first",
        warning_bullets=["Stays local.", "Leaves machine: nothing."],
        checklist_rows=[
            {"check": "running", "command_why": "`docker compose ps` shows Up"},
            {"check": "persisting", "command_why": "restart keeps data"},
            {"check": "port closed", "command_why": "`curl` fails externally"},
        ],
        phases=[
            {"title": f"Phase {i}: step", "prose_points": ["point"],
             "code_blocks": [{"lang": "bash", "purpose": "run"}]}
            for i in range(1, 5)
        ],
    )


class FakeLLM:
    def __init__(self, intro_extra=None):
        self._intro_extra = intro_extra

    def chat(self, *a, **kw):
        raise AssertionError("chat should not be used")

    def chat_json(self, messages, schema, **kw):
        name = schema.__name__
        if name == "RawSelection":
            return _selection()
        if name == "GuideOutline":
            return _outline()
        if name == "GuidePart1":
            meta = SAMPLE_GUIDE.meta.model_copy()
            intro = list(SAMPLE_GUIDE.intro)
            if self._intro_extra:
                intro.append(self._intro_extra)
            return GuidePart1(
                meta=meta, intro=intro, warning_box=SAMPLE_GUIDE.warning_box,
                diagram=SAMPLE_GUIDE.diagram, phases=list(SAMPLE_GUIDE.phases[:3]))
        return GuidePart2(
            phases=list(SAMPLE_GUIDE.phases[3:]),
            checklist=list(SAMPLE_GUIDE.checklist),
            closing_alert=SAMPLE_GUIDE.closing_alert)


def _sample_with(text: str):
    guide = SAMPLE_GUIDE.model_copy(deep=True)
    guide.intro = [*SAMPLE_GUIDE.intro, text]
    return guide


class TestUnitGuard:
    def test_safe_guide_passes(self):
        guide = _sample_with("Deploy on your own box.")
        assert check(guide, render_guide(guide), "https://github.com/acme/tool") == []

    def test_source_host_allowed(self):
        guide = _sample_with("Clone the repo at https://github.com/acme/tool.")
        assert check(guide, render_guide(guide), "https://github.com/acme/tool") == []

    def test_token_links_allowed(self):
        guide = _sample_with("Tail logs with https://localhost:8080 and curl http://127.0.0.1/ping.")
        assert check(guide, render_guide(guide), "https://example.com/x") == []

    def test_unsafe_scheme_blocked(self):
        guide = _sample_with("Bypass the proxy with javascript:alert(1)")
        problems = check(guide, render_guide(guide), "https://example.com/x")
        assert any("unsafe scheme" in p for p in problems)

    def test_too_many_external_hosts_blocked(self):
        guide = _sample_with("Intro line without links.")
        extra = " and ".join(f"https://spam-host-{i}.example/x" for i in range(1, 9))
        guide.intro.append(extra)
        problems = check(guide, render_guide(guide), "https://example.com/1",
                         max_external_links=6)
        assert any("too many external hosts" in p for p in problems)

    def test_template_hosts_not_counted(self):
        guide = _sample_with("Powered by https://uvfarms.in")
        assert check(guide, render_guide(guide), "https://github.com/x/y") == []

    def test_unsafe_html_src_blocked(self):
        html = render_guide(_sample_with("plain"))
        html = html.replace("</head>", '<link href="data:text/html;base64,AAAA"></head>')
        problems = check(_sample_with("plain"), html, "https://github.com/x/y")
        assert any("unsafe html" in p for p in problems)


class TestPipelineGuardDrill:
    def test_guard_violation_blocks_guide_and_write(self, tmp_path):
        fake = FakeLLM(intro_extra="Win prizes now at javascript:void(0)")
        summary = run_pipeline(
            SettingsLite(), adapters=[GoodAdapter()],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs",
            history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "all_guides_failed"
        assert all("failed" in g["status"] for g in summary["guides"])
        assert len(list(tmp_path.glob("*.html"))) == 0

    def test_guard_ok_writes_normal(self, tmp_path):
        fake = FakeLLM()
        summary = run_pipeline(
            SettingsLite(), adapters=[GoodAdapter()],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs",
            history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "ok"
        assert len(list(tmp_path.glob("*.html"))) == 3

    def test_guard_disabled_allows_blocked_content(self, tmp_path):
        class GuardOff(SettingsLite):
            content_guard_enabled = False

        fake = FakeLLM(intro_extra="Win prizes now at javascript:void(0)")
        summary = run_pipeline(
            GuardOff(), adapters=[GoodAdapter()],
            llm_client=fake, out_dir=tmp_path, runs_dir=tmp_path / "runs",
            history_path=tmp_path / "history.json",
        )
        assert summary["status"] == "ok"
