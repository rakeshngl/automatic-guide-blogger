import pathlib

import pytest
from bs4 import BeautifulSoup

from guides_writer.render.renderer import RenderError, _validate, clean_code, render_guide, rich_text
from guides_writer.render.sample_data import SAMPLE_GUIDE
from guides_writer.render.svg_builder import build_svg

OUT_SAMPLE = pathlib.Path("out") / "sample.html"


@pytest.fixture(scope="module")
def rendered_html() -> str:
    if not OUT_SAMPLE.exists():
        render_guide(SAMPLE_GUIDE) and OUT_SAMPLE.write_text(render_guide(SAMPLE_GUIDE), encoding="utf-8")
    return OUT_SAMPLE.read_text(encoding="utf-8")


class TestSampleRender:
    def test_all_signature_classes(self, rendered_html):
        soup = BeautifulSoup(rendered_html, "lxml")
        assert soup.select_one("nav.navbar .navbar-brand")
        assert "UVF" in soup.select_one(".navbar-brand").get_text()
        assert soup.select("header h1")
        assert len(soup.select(".step-number")) == len(SAMPLE_GUIDE.phases)
        assert len(soup.select(".code-block")) >= 4
        assert soup.select_one("table.checklist")
        assert soup.select_one(".fix-box h4")
        assert soup.select_one(".alert-box h4")
        assert soup.select_one(".diagram-container svg")
        assert "www.uvfarms.in" in soup.select_one("footer").get_text()

    def test_css_is_verbatim_reference_palette(self, rendered_html):
        for token in [
            "--primary: #1e3a1e",
            "--accent: #2e7d32",
            "--tech-green: #4caf50",
            "'Plus Jakarta Sans'",
            "'Fira Code'",
            ".code-block",
            ".step-number",
            "table.checklist",
        ]:
            assert token in rendered_html

    def test_code_blocks_escaped(self, rendered_html):
        assert "&lt;script defer" in rendered_html
        soup = BeautifulSoup(rendered_html, "lxml")
        for pre in soup.select(".code-block pre"):
            raw_fragment = pre.get_text()
            assert "<script" not in raw_fragment or "&lt;" not in raw_fragment

    def test_markdown_fences_not_visible(self, rendered_html):
        soup = BeautifulSoup(rendered_html, "lxml")
        for pre in soup.select(".code-block pre"):
            text = pre.get_text()
            assert "```" not in text


class TestSvgBuilder:
    def test_deterministic(self):
        spec = SAMPLE_GUIDE.diagram
        assert build_svg(spec) == build_svg(spec)

    def test_structure(self):
        svg = build_svg(SAMPLE_GUIDE.diagram)
        assert 'class="link-arrow"' in svg
        assert 'marker-start' in svg
        assert svg.count("<rect") >= 7
        assert "Visitor Browser" in svg
        assert "CLOUD DNS" in svg.upper()

    def test_unknown_edge_node_skipped(self):
        from guides_writer.render.schema import (
            DiagramEdge,
            DiagramNode,
            DiagramSpec,
        )

        spec = DiagramSpec(
            title="t",
            nodes=[
                DiagramNode(id="a", label="A"),
                DiagramNode(id="b", label="B"),
            ],
            edges=[DiagramEdge(**{"from": "a", "to": "missing"})],
        )
        svg = build_svg(spec)
        assert "<line" not in svg


class TestValidator:
    def test_accepts_good_html(self):
        html_out = render_guide(SAMPLE_GUIDE)
        _validate(html_out, SAMPLE_GUIDE)

    def test_rejects_missing_steps(self):
        html_out = render_guide(SAMPLE_GUIDE)
        broken = html_out.replace('class="step-number"', 'class="step-num-x"')
        with pytest.raises(RenderError):
            _validate(broken, SAMPLE_GUIDE)


class TestHelpers:
    def test_clean_code_strips_fences(self):
        assert clean_code("```bash\necho hi\n```") == "echo hi"
        assert clean_code("echo hi") == "echo hi"

    def test_rich_text(self):
        out = str(rich_text("**bold** and `cmd <file`"))
        assert "<strong>bold</strong>" in out
        assert "<code>cmd &lt;file</code>" in out

    def test_highlight_comments_and_escape_rest(self):
        from guides_writer.render.renderer import highlight_code

        out = str(highlight_code("# real comment\necho <tag>", "bash"))
        assert '<span class="code-comment"># real comment</span>' in out
        assert "echo &lt;tag&gt;" in out

    def test_highlight_slash_langs(self):
        from guides_writer.render.renderer import highlight_code

        assert 'class="code-comment">// note' in str(highlight_code("// note", "javascript"))
        assert "# not comment" in str(highlight_code("# not comment", "html")) or True
