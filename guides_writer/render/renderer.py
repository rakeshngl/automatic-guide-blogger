import html
import logging
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from guides_writer.render.schema import Guide
from guides_writer.render.svg_builder import build_svg

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class RenderError(RuntimeError):
    pass


def rich_text(text: str) -> Markup:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return Markup(escaped)


def clean_code(code: str) -> str:
    lines = code.strip("\n").splitlines()
    while lines and lines[0].lstrip().startswith("```"):
        lines.pop(0)
    while lines and lines[-1].lstrip().startswith("```"):
        lines.pop()
    return "\n".join(lines).rstrip()


_SLASH_LANGS = {"javascript", "js", "typescript", "ts", "java", "go", "rust", "c", "cpp"}
_XML_LANGS = {"html", "xml"}


def highlight_code(code: str, lang: str) -> Markup:
    lowered = (lang or "").lower()
    if lowered in _XML_LANGS:
        pattern = re.compile(r"^(\s*)(<!--.*-->)\s*$")
    elif lowered in _SLASH_LANGS:
        pattern = re.compile(r"^(\s*)(//.*)$")
    else:
        pattern = re.compile(r"^(\s*)(#.*)$")

    out = []
    for line in code.splitlines():
        match = pattern.match(line)
        if match:
            out.append(
                f"{match.group(1)}<span class=\"code-comment\">{html.escape(match.group(2), quote=False)}</span>"
            )
        else:
            out.append(html.escape(line, quote=False))
    return Markup("\n".join(out))


def _validate(html_out: str, guide: Guide) -> None:
    soup = BeautifulSoup(html_out, "lxml")
    problems: list[str] = []
    if not soup.select_one("nav.navbar .navbar-brand"):
        problems.append("missing navbar")
    if not (soup.select_one("header h1") or {}).get_text(strip=True):
        problems.append("empty hero title")
    step_numbers = soup.select(".step-number")
    if len(step_numbers) < min(3, len(guide.phases)):
        problems.append(f"too few step-numbers: {len(step_numbers)}")
    if guide.phases and len(soup.select(".code-block")) == 0:
        problems.append("no code-blocks rendered")
    if guide.checklist and not soup.select_one("table.checklist"):
        problems.append("missing checklist table")
    if guide.diagram and not soup.select_one(".diagram-container svg"):
        problems.append("missing diagram svg")
    empty_steps = [n for n in step_numbers if not n.get_text(strip=True)]
    if empty_steps:
        problems.append("empty step-number present")
    if problems:
        raise RenderError("; ".join(problems))


def render_guide(guide: Guide) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["rich"] = rich_text

    phases = [
        {"number": i, "title": p.title, "prose": p.prose,
         "code_blocks": [{"filename": b.filename, "lang": b.lang,
                          "code_html": highlight_code(clean_code(b.code), b.lang)} for b in p.code_blocks]}
        for i, p in enumerate(guide.phases, start=1)
    ]

    css_text = (TEMPLATES_DIR / "guide.css").read_text(encoding="utf-8")
    svg = build_svg(guide.diagram) if guide.diagram else ""

    html_out = env.get_template("guide.html.j2").render(
        page_title=f"UVF IT: {guide.meta.title}",
        css_text=css_text,
        meta=guide.meta,
        intro=guide.intro,
        warning_box=guide.warning_box,
        diagram_svg=svg,
        diagram_title=guide.diagram.title if guide.diagram else "",
        phases=phases,
        checklist=guide.checklist,
        closing_alert=guide.closing_alert,
        year=datetime.now().year,
    )
    _validate(html_out, guide)
    logger.info("guide_rendered title=%s bytes=%d", guide.meta.title, len(html_out))
    return html_out
