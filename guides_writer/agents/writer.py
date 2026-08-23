import json
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from guides_writer.llm.client import LLMClient
from guides_writer.render.schema import (
    ChecklistRow,
    ClosingAlert,
    DiagramSpec,
    Guide,
    GuideMeta,
    Phase,
    WarningBox,
)
from guides_writer.sources.base import CandidateItem


class GuidePart1(BaseModel):
    meta: GuideMeta
    intro: list[str] = Field(default_factory=list)
    warning_box: WarningBox
    diagram: DiagramSpec | None = None
    phases: list[Phase]


class GuidePart2(BaseModel):
    phases: list[Phase]
    checklist: list[ChecklistRow] = Field(min_length=3)
    closing_alert: ClosingAlert | None = None

logger = logging.getLogger(__name__)


class WriterError(RuntimeError):
    pass


MAX_ENRICH_CHARS = 2400


def _truncate(text: str, limit: int = MAX_ENRICH_CHARS) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return cleaned[:limit]


def fetch_github_readme(candidate) -> str:
    metrics = getattr(candidate, "metrics", None) or {}
    full_name = metrics.get("full_name")
    if not full_name:
        return ""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{full_name}/readme",
            headers={"Accept": "application/vnd.github.raw+json", "User-Agent": "guides-writer"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("github_readme_failed status=%s repo=%s", resp.status_code, full_name)
            return ""
        return _truncate(resp.text)
    except httpx.HTTPError as exc:
        logger.warning("github_readme_error repo=%s err=%s", full_name, exc)
        return ""


def extract_page_meta(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    parts = []
    title = soup.find("title")
    if title:
        parts.append(title.get_text(strip=True))
    for name in ("og:description", "description", "twitter:description"):
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            parts.append(tag["content"])
    return _truncate(" \n".join(dict.fromkeys(parts)), 2000)


def fetch_page_meta(url: str) -> str:
    if not url:
        return ""
    try:
        resp = httpx.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
            timeout=30,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        return extract_page_meta(resp.text)
    except httpx.HTTPError as exc:
        logger.warning("page_meta_error url=%s err=%s", url, exc)
        return ""


def enrich(candidate) -> str:
    if getattr(candidate, "source", "") == "github_trending":
        readme = fetch_github_readme(candidate)
        if readme:
            return f"README of {candidate.metrics.get('full_name')}:\n{readme}"
    bits = [f"Title: {candidate.title}"]
    tagline = getattr(candidate, "tagline", "")
    if tagline:
        bits.append(f"Tagline: {tagline}")
    page_meta = fetch_page_meta(getattr(candidate, "url", ""))
    if page_meta:
        bits.append(f"Page info:\n{page_meta}")
    return "\n".join(bits)


def _topic_url(topic) -> str:
    return getattr(topic, "source_url", None) or getattr(topic, "url", "") or ""


def _topic_tagline(topic) -> str:
    return getattr(topic, "tagline", "") or ""


class OutlineCodeBlock(BaseModel):
    lang: str = "bash"
    filename: str | None = None
    purpose: str = ""


class OutlinePhase(BaseModel):
    title: str
    prose_points: list[str] = Field(default_factory=list)
    code_blocks: list[OutlineCodeBlock] = Field(default_factory=list)


class GuideOutline(BaseModel):
    title: str
    tagline: str = ""
    navbar_badge: str = "guide"
    intro_points: list[str] = Field(default_factory=list)
    warning_heading: str = "Read this before you start"
    warning_bullets: list[str] = Field(default_factory=list)
    diagram: DiagramSpec | None = None
    phases: list[OutlinePhase]
    checklist_rows: list[ChecklistRow] = Field(default_factory=list)
    closing_alert: ClosingAlert | None = None


OUTLINE_SYSTEM_PROMPT = """Stage 1 of 2: outline (no full prose) one beginner "UVF IT" BUILD guide — a minimal working clone inspired by the selected product.

Rules:
- Reader is a beginner dev; build a minimal but working clone in ~45 min using a free-tier open-source stack (Python or Node.js + SQLite/Postgres, no paid APIs).
- 4-6 phases, ordered like: scaffold project -> core feature 1 (data/model) -> core feature 2 (API/UI) -> run locally -> verify and extend.
- EVERY phase has >=1 code_block with COMPLETE runnable code that builds incrementally (not just install commands); no bare placeholders unless prose explains how to obtain the value.
- diagram: >=4 nodes; group style "local" for your codebase/runtime/DB, "cloud" for any external API the clone calls; edges reference node ids via "from"/"to".
- warning_bullets: 2-3 honest bullets (prerequisites like Node/Python version, what is simplified vs the real product, common gotchas).
- checklist_rows: 3-5 checks with the exact command or browser observation that proves the build works.
- intro_points: 2 short paragraphs, UVF IT voice ("Welcome to another official **UVF IT** ... build your own X inspired by Y ...").
- Use **bold**/`code` sparingly; no markdown headings.

Rule: title must NOT include "UVF IT" prefix (render adds it).
Output STRICT JSON only:
{"title": "...", "tagline": "...", "navbar_badge": "...", "intro_points": ["..",".."], "warning_heading": "...", "warning_bullets": [".."], "diagram": {"title": "..", "groups": [{"id": "local", "label": "..", "style": "local"}], "nodes": [{"id": "app", "label": "..", "sublabel": null, "group": "local", "style": "tech"}], "edges": [{"from": "a", "to": "b", "label": null, "bidirectional": false}]}, "phases": [{"title": "Phase 1: ..", "prose_points": [".."], "code_blocks": [{"lang": "bash", "filename": null, "purpose": ".."}]}], "checklist_rows": [{"check": "..", "command_why": ".."}], "closing_alert": {"heading": "..", "body": ".."}}"""

WRITE_COMMON_RULES = """You are expanding an OUTLINE into part of the FULL JSON guide for a "UVF IT" BUILD blueprint — the reader is building a minimal clone from scratch.

Rules:
- Keep titles/order/filenames/langs from the outline; drop nothing assigned to you.
- prose entries: paragraphs of 2-4 sentences, beginner dev tone, practical UVF IT voice — explain *what* you're coding and *why*.
- code fields: COMPLETE runnable code that builds on previous phases (scaffold, then feature code, then run); no placeholders unless adjacent prose explains how to obtain the value; never markdown fences.
- checklist command_why: concrete backticked command or precise browser observation that proves the clone works.

"""

WRITE_PART1_PROMPT = WRITE_COMMON_RULES + """Produce ONLY: meta, intro, warning_box, diagram, and phases 1..{split} of {total}.

Output STRICT JSON only:
{"meta": {"title": "..", "tagline": "..", "navbar_badge": ".."}, "intro": [".."], "warning_box": {"heading": "..", "bullets": [".."]}, "diagram": {"title": "..", "groups": [{"id": "..", "label": "..", "style": "local|cloud|plain"}], "nodes": [{"id": "..", "label": "..", "sublabel": null, "group": "..", "style": "default|tech|danger"}], "edges": [{"from": "..", "to": "..", "label": null, "bidirectional": false}]}, "phases": [{"title": "..", "prose": [".."], "code_blocks": [{"lang": "bash", "filename": null, "code": ".."}]}]}"""

WRITE_PART2_PROMPT = WRITE_COMMON_RULES + """Phases 1..{split} are already written. Produce ONLY: the REMAINING phases ({remaining}) expanded fully, then checklist (3-5 rows) and closing_alert.

Output STRICT JSON only:
{"phases": [{"title": "..", "prose": [".."], "code_blocks": [{"lang": "bash", "filename": null, "code": ".."}]}], "checklist": [{"check": "..", "command_why": ".."}], "closing_alert": {"heading": "..", "body": ".."}}"""


def _rubric_violations(outline: GuideOutline) -> list[str]:
    problems = []
    if len(outline.phases) < 4:
        problems.append(f"only {len(outline.phases)} phases, need >= 4")
    for i, phase in enumerate(outline.phases, 1):
        if not phase.code_blocks:
            problems.append(f"phase {i} ({phase.title}) has no code blocks")
    if len(outline.checklist_rows) < 3:
        problems.append("fewer than 3 checklist rows")
    if len(outline.warning_bullets) < 2:
        problems.append("fewer than 2 warning bullets")
    if outline.diagram and len(outline.diagram.nodes) < 3:
        problems.append("diagram has fewer than 3 nodes")
    return problems


class GuideWriter:
    name = "writer"

    def __init__(self, client: LLMClient):
        self._client = client

    def _outline(self, pick: CandidateItem, context: str) -> GuideOutline:
        base_messages = [
            {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Topic: {pick.title}\n"
                    f"Source: [{pick.source}] {_topic_url(pick)}\n"
                    + (f"Tagline: {_topic_tagline(pick)}\n" if _topic_tagline(pick) else "")
                    + f"\nReference material:\n{context}\n\nProduce the outline JSON now."
                ),
            },
        ]
        outline = self._client.chat_json(base_messages, schema=GuideOutline, temperature=0.4, max_tokens=4200)
        if isinstance(self._client, LLMClient):
            time.sleep(22)
        problems = _rubric_violations(outline)
        if not problems:
            return outline

        logger.warning("outline_rubric_retry problems=%s", problems)
        repair_messages = base_messages + [
            {"role": "assistant", "content": outline.model_dump_json(exclude_none=True)},
            {
                "role": "user",
                "content": (
                    "Your outline violated these rules:\n- "
                    + "\n- ".join(problems)
                    + "\n\nReturn the corrected full outline JSON only."
                ),
            },
        ]
        outline = self._client.chat_json(repair_messages, schema=GuideOutline, temperature=0.4, max_tokens=4200)
        if isinstance(self._client, LLMClient):
            time.sleep(22)
        remaining = _rubric_violations(outline)
        if remaining:
            raise WriterError(f"outline failed rubric after retry: {remaining}")
        return outline

    def write_guide(self, pick: CandidateItem, context: str) -> Guide:
        outline = self._outline(pick, context)
        total = len(outline.phases)
        split = (total + 1) // 2
        outline_json = json.dumps(
            outline.model_dump(exclude_none=True), ensure_ascii=False, separators=(",", ":")
        )
        source_url = _topic_url(pick)

        part1_messages = [
            {"role": "system", "content": WRITE_PART1_PROMPT.replace("{split}", str(split)).replace("{total}", str(total))},
            {
                "role": "user",
                "content": (
                    f"Topic source URL: {source_url}\n\nOUTLINE:\n{outline_json}\n\n"
                    f"Produce part 1 JSON now (phases 1..{split} only)."
                ),
            },
        ]
        part1 = self._client.chat_json(part1_messages, schema=GuidePart1, temperature=0.5, max_tokens=3800)
        if isinstance(self._client, LLMClient):
            time.sleep(28)

        remaining_titles = ", ".join(p.title for p in outline.phases[split:])
        part2_messages = [
            {"role": "system", "content": WRITE_PART2_PROMPT.replace("{split}", str(split)).replace("{remaining}", remaining_titles)},
            {
                "role": "user",
                "content": (
                    f"Topic source URL: {source_url}\n\nOUTLINE:\n{outline_json}\n\n"
                    f"Produce part 2 JSON now (phases {split + 1}..{total}, checklist, closing_alert)."
                ),
            },
        ]
        part2 = self._client.chat_json(part2_messages, schema=GuidePart2, temperature=0.5, max_tokens=3800)
        if isinstance(self._client, LLMClient):
            time.sleep(28)

        guide = Guide(
            meta=part1.meta,
            intro=part1.intro,
            warning_box=part1.warning_box,
            diagram=part1.diagram if part1.diagram else outline.diagram,
            phases=list(part1.phases) + list(part2.phases),
            checklist=part2.checklist,
            closing_alert=part2.closing_alert or outline.closing_alert,
        )
        logger.info("guide_written title=%s phases=%d", guide.meta.title, len(guide.phases))
        return guide
