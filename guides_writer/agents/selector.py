import logging

from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz

from guides_writer.llm.client import LLMClient
from guides_writer.sources.base import CandidateItem

logger = logging.getLogger(__name__)

FUZZY_DUPE_THRESHOLD = 82
# Single request must stay under Groq's 8k TPM budget for qwen3.6-27b.
# 72 candidates ~ 3k tokens of pool + history + system stays well inside.
MAX_POOL_CANDIDATES = 72
MAX_EXCLUDE_TITLES = 30

SYSTEM_PROMPT_TEMPLATE = """You are the topic editor for "UVF IT", a catalog of beginner-level "build your own X" development guides (see style: step-by-step blueprints like "Build a RAG Chat Bot from Scratch" or "Build an AI Recruitment Agent in 4 Steps").

Your job: choose exactly {count} topics from the numbered candidate pool below that would make the best BEGINNER BUILD GUIDES.

Guide-worthiness rubric (score mentally, pick the best):
1. Hands-on coding outcome a beginner can build in ~45 minutes — a minimal but working clone inspired by the selected product/repo/tool ("build a Ghost-like blog", "build a PostHog-like analytics tracker").
2. Uses free-tier, open-source stack (Python/JS, SQLite/Postgres, free APIs). Avoid anything needing paid enterprise accounts or closed hardware.
3. Has real instructional depth: scaffold project, write core code incrementally, run locally, verify and extend.
4. NOT pure news, funding rounds, listicles, opinion pieces, or hype announcements with nothing to build.
5. Diversity: prefer picks spread across different sources and categories rather than 3 similar tools.

Hard rules:
- Choose ONLY from the numbered candidate pool. Never invent tools.
- Copy each pick's source URL exactly as given in the pool.
- "source" must be ONLY the short source id shown in square brackets at the start of the pool line (e.g. "github_trending"), never the full line.
- Each pick must be a distinct product/project; never two variants of the same thing.
 - "title" must be a concrete build-style title mentioning what you will build, e.g. "Build a Ghost-like Blog with Node.js + SQLite" or "Build a PostHog-like Analytics Tracker in Python".
- "angle" = one sentence on what unique beginner angle this guide takes.
- "why_guide_worthy" = one sentence referencing the rubric.
- "difficulty" is one of: "beginner", "confident-beginner".
- "est_minutes" is an integer 15-60.
- "tags": 2-4 short tech tags.

Output STRICT JSON only:
{{"picks": [{{"title": "...", "angle": "...", "why_guide_worthy": "...", "source": "...", "source_url": "...", "difficulty": "beginner", "est_minutes": 40, "tags": ["..."]}}]}}"""


class TopicPick(BaseModel):
    title: str
    angle: str
    why_guide_worthy: str
    source: str
    source_url: str
    difficulty: str = "beginner"
    est_minutes: int = 40
    tags: list[str] = Field(default_factory=list)

    @field_validator("source")
    @classmethod
    def _clean_source(cls, value: str) -> str:
        cleaned = value.strip()
        if "[" in cleaned and "]" in cleaned:
            cleaned = cleaned[cleaned.find("[") + 1 : cleaned.find("]")]
        return cleaned.strip().lower()


class RawSelection(BaseModel):
    picks: list[TopicPick]


class SelectionResult(BaseModel):
    picks: list[TopicPick]
    requested_count: int
    duplicates_dropped: list[str] = Field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return len(self.picks) < self.requested_count


def _is_duplicate_title(title: str, seen_titles: list[str]) -> bool:
    lowered = title.lower()
    for existing in seen_titles:
        existing_lower = existing.lower()
        # Exact substring both ways is a strong signal (guard against tiny
        # accidental matches on 1-2 char words).
        if (existing_lower in lowered or lowered in existing_lower) and (
            len(existing_lower) >= 8 and len(lowered) >= 8
        ):
            return True
        # Token-set overlap measures real shared vocabulary. NOTE: we
        # deliberately avoid fuzz.WRatio here — its partial-ratio component
        # over-scores short titles that merely share one common word (e.g.
        # "AI"), producing false-positive "duplicates" and dropping valid
        # picks (which is why some runs degrade to 2/3 for no good reason).
        if fuzz.token_set_ratio(lowered, existing_lower) >= FUZZY_DUPE_THRESHOLD:
            return True
    return False


def _balanced_pool(candidates: list[CandidateItem], max_n: int = MAX_POOL_CANDIDATES) -> list[CandidateItem]:
    """Cap the pool sent to the LLM without starving any source.

    Round-robins across sources in rank order so small/fresh sources
    (e.g. reddit_digest) keep representation instead of being drowned by
    the biggest feeds.
    """
    if len(candidates) <= max_n:
        return candidates
    by_source: dict[str, list[CandidateItem]] = {}
    for c in candidates:
        by_source.setdefault(c.source, []).append(c)
    out: list[CandidateItem] = []
    round_idx = 0
    while len(out) < max_n:
        progressed = False
        for items in by_source.values():
            if len(out) >= max_n:
                break
            if round_idx < len(items):
                out.append(items[round_idx])
                progressed = True
        if not progressed:
            break
        round_idx += 1
    return out


def _format_pool(candidates: list[CandidateItem]) -> str:
    lines = []
    for i, c in enumerate(candidates, start=1):
        metric_bits = []
        for key in ("votes", "stars_today"):
            value = c.metrics.get(key)
            if value:
                metric_bits.append(f"{key}={value}")
        for key in ("language", "topic"):
            value = c.metrics.get(key)
            if value:
                metric_bits.append(f"{key}={value}")
        tagline = (c.tagline or "").replace("\n", " ")[:140]
        meta = " | ".join(filter(None, [*metric_bits, *c.topics]))
        lines.append(f"{i}. [{c.source}] {c.title} - {tagline}" + (f" | {meta}" if meta else ""))
        lines.append(f"   url: {c.url}")
    return "\n".join(lines)


class TopicSelector:
    name = "selector"

    def __init__(self, client: LLMClient):
        self._client = client

    def select(
        self,
        candidates: list[CandidateItem],
        count: int = 3,
        exclude_titles: list[str] | None = None,
    ) -> SelectionResult:
        exclude_titles = (exclude_titles or [])[-MAX_EXCLUDE_TITLES:]
        if not candidates:
            logger.warning("selection_skipped no_candidates")
            return SelectionResult(picks=[], requested_count=count)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(count=count)
        pool = _balanced_pool(candidates)
        user_prompt = (
            "Recently covered topics (do NOT repeat these themes):\n"
            + ("\n".join(f"- {t}" for t in exclude_titles) if exclude_titles else "(none yet)")
            + "\n\nCandidate pool:\n"
            + _format_pool(pool)
            + f"\n\nSelect exactly {count} guide-worthy topics."
        )
        logger.info(
            "selection_prompt_sizes candidates=%d pool_shown=%d excludes=%d",
            len(candidates), len(pool), len(exclude_titles),
        )
        raw = self._client.chat_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            schema=RawSelection,
            temperature=0.3,
        )

        picked_titles: list[str] = []
        kept: list[TopicPick] = []
        dropped: list[str] = []
        for pick in raw.picks:
            known = exclude_titles + picked_titles
            if _is_duplicate_title(pick.title, known):
                dropped.append(pick.title)
                continue
            picked_titles.append(pick.title)
            kept.append(pick)
            if len(kept) >= count:
                break

        if dropped:
            logger.warning("selection_duplicates_dropped %s", dropped)
        result = SelectionResult(
            picks=kept[:count],
            requested_count=count,
            duplicates_dropped=dropped,
        )
        if result.degraded:
            logger.warning(
                "selection_degraded got=%d wanted=%d pool=%d",
                len(result.picks), count, len(candidates),
            )
        return result
