# Implementation Plan — Automatic Guides Writer

> An autonomous Python agent that runs daily at 06:00 on an Ubuntu VPS, discovers trending
> tech (Product Hunt / GitHub Trending / TheresAnAIForThat), uses the **xAI Grok API** to
> select **3 beginner-guide-worthy topics**, generates complete HTML guides that look
> identical to **https://guides.uvfarms.in/local_rag_guide**, and delivers them to a
> **private Discord channel** (`#guides-drafts`) via webhook.

---

## 1. Goals & Non-Goals

**Goals**
- Fully hands-off daily run via cron; zero human intervention required.
- Guides visually indistinguishable from the existing UVF IT blueprint style
  (same fonts, color palette, hero, step-boxes, code blocks, SVG architecture
  diagram, checklists, fix/alert boxes, footer).
- Pluggable source system so new discovery sources can be added later.
- Safe failure modes: a broken scraper must never kill the whole run.

**Non-Goals (v1)**
- No direct deployment to guides.uvfarms.in (only Discord delivery).
- No auto-updating of the catalog/index page (phase-2 nice-to-have).
- No CMS/database — state lives in small JSON files.

---

## 2. High-Level Architecture

```
                 ┌────────────────────── 06:00 daily (cron / flock) ─────────────────────┐
                 │                                                                       │
   ┌─────────────▼─────────────┐      ┌──────────────────┐      ┌───────────────────┐ │
   │ 1. DISCOVER               │      │ 3. SELECT        │      │ 5. RENDER         │ │
   │ producthunt.py            │      │ Grok scores all  │      │ Jinja2 + exact    │ │
   │ github_trending.py        ├─────►│ candidates,      ├─────►│ local_rag_guide   │ │
   │ taaft.py                  │      │ dedupes vs       │      │ CSS → 3 HTML      │ │
   │ → CandidateItem[]         │      │ history, picks 3 │      │ files             │ │
   └───────────────────────────┘      └────────▲─────────┘      └─────────┬─────────┘ │
                                               │                          │           │
   ┌───────────────────────────┐      ┌────────┴─────────┐      ┌─────────▼─────────┐ │
   │ 2. ENRICH (optional)      │      │ 4. WRITE         │      │ 6. DELIVER        │ │
   │ Fetch README / product    │      │ Grok 2-stage:    │      │ Discord webhook   │ │
   │ page text for chosen      ├─────►│ outline → full   ├─────►│ → #guides-drafts  │ │
   │ topics only (cheap)       │      │ guide JSON       │      │ (HTML attachments)│ │
   └───────────────────────────┘      └──────────────────┘      └───────────────────┘ │
                                                                                       │
                                     7. LOG + STATE (data/history.json, runs/*.json) ◄──┘
```

Pipeline stages are independently wrapped in error handling — stage failures degrade
gracefully (e.g., 1 dead scraper still leaves 2 sources to pick from).

---

## 3. Technology Stack

| Concern            | Choice                                             | Why |
|--------------------|----------------------------------------------------|-----|
| Language           | Python 3.11+                                       | matches requirement |
| LLM                | **Groq** `openai/gpt-oss-120b` via OpenAI-compatible chat API (`LLM_BASE_URL` swappable → xAI Grok etc.) | free tier; provider-agnostic |
| HTTP               | `httpx` (timeouts, HTTP/2) + `tenacity` retries    | resilient scraping/API calls |
| Parsing            | `beautifulsoup4` + `lxml`                          | light scraping; no headless browser in v1 |
| Templates          | `Jinja2`                                           | pixel-faithful reuse of reference CSS |
| Validation         | `pydantic` v2                                      | enforce strict LLM JSON schemas |
| Delivery           | **Discord webhook** — multipart file POST via `httpx` | zero-OAuth drafts in a private channel, ~15 LOC |
| Config/secrets     | `.env` + `python-dotenv`                           | 12-factor |
| Scheduling         | **cron** (`CRON_TZ`) + `flock` single-instance lock| requirement; systemd timer documented as alt |
| Logging            | stdlib `logging` → JSON lines + rotating file      | greppable, logrotate-friendly |
| Tests              | `pytest` + saved HTML fixtures + mocked LLM        | offline CI-safe |

Model default: `grok-4-fast` (quality/cost sweet spot; configurable via env — e.g.
`grok-3-mini` for cheaper outline steps). Roughly **7–13 LLM calls per day total**
(1 selection + 3×(outline + full write) + enrichment summaries) → negligible cost.

---

## 4. Source Adapters (pluggable)

Every adapter implements:

```python
class SourceAdapter(Protocol):
    name: str
    def fetch(self) -> list[CandidateItem]: ...
```

`CandidateItem` (pydantic): `source, title, url, tagline, extra_metrics(dict), fetched_at`.

### 4.1 Product Hunt — *preferred: official GraphQL API*
- Register a free developer token → GraphQL `https://www.producthunt.com/v2/api/graphql`,
  query today's/top posts (`posts(first: 10, order: VOTES)` style) → name, tagline, URL, votes.
- Reliable, ToS-compliant JSON. **Fallback** (if no token): scrape the homepage leaderboard
  with browser-mimicking headers; expected to be fragile (Cloudflare) — treated as
  best-effort, never fatal.

### 4.2 GitHub Trending — *scrape*
- No official trending API. Parse `https://github.com/trending?since=daily`
  → repo full-name, description, language, stars-this-week, URL. Markup is stable;
  selectors isolated in one module + fixture tests. Optional enrichment later: fetch
  the repo README via the official GitHub REST API (free, unauthenticated 60 req/hr is plenty).

### 4.3 TheresAnAIForThat — *scrape*
- Crawl its "new"/trending listings pages; normalize name, one-liner, link.
- Highest markup-change risk → parser fully isolated, tolerant (`find_all` chains),
  and its failure never blocks the other two sources.

### 4.4 Merge strategy (recommended over strict fallback)
Fetch **all three**, union into a pool of ~30–60 candidates, and let Grok pick the best
3 across sources. A config flag (`SOURCE_PRIORITY=ph>gh>taaft|merge`) allows switching
to a strict waterfall later if desired. This yields better guide quality than
"first-source-with-data wins".

---

## 5. Topic Selection Agent (Grok call #1)

System prompt encodes explicit **guide-worthiness criteria** (mirrors existing catalog):
- Beginner-buildable end-to-end in ≤ 45 min; concrete outcome ("deploy X", "build Y").
- Free-tier / self-hostable tooling preferred; not pure news/funding/hype.
- Has real instructional depth: setup, config, execution, verification.

Also injected: last N days of `history.json` titles → "do not repeat these themes."

Output contract (enforced by pydantic, auto-retry once on schema violation):

```json
{"picks": [{
  "title": "...", "angle": "...", "why_guide_worthy": "...",
  "source": "github_trending", "source_url": "...",
  "difficulty": "beginner", "est_minutes": 30, "tags": ["Python","Docker"]
}]}
```

Post-selection safety net: `rapidfuzz` similarity check against history (catches
paraphrased duplicates the LLM missed). If < 3 fresh topics exist, emit what we have
and warn in the run report (configurable minimum).

---

## 6. Guide Generation Agent (Grok, 2-stage per guide)

**Why 2 stages:** one mega-prompt produces rambling, inconsistent structure. Two
constrained passes give catalog-quality output.

1. **Outline call** → phases, section titles, what code each phase contains,
   verification-checklist rows, warning-box bullets. Reviewed against a structural
   rubric in code (≥ 4 phases, ≥ 1 code block/phase, has verification table…).
2. **Full-write call(s)** (outline + enrichment text in context) → strict JSON matching
   the template schema below. Long guides may be split per-phase calls to stay well
   under output limits.

Template schema (drives rendering):
```
meta:        title, tagline, navbar_brand("UVF IT"), navbar_badge, hero_paragraph
warning_box: heading + bullets          ← the yellow ⚠ fix-box
diagram_spec: nodes[{id,label,sub,style}], edges[...]   ← see §7
phases[]:    number, title, prose_md[], code_blocks[{lang, filename?, code}]
checklist[]: rows[{check, command_why}]
closing_alert: heading + paragraph
footer:      static
```

Guardrails:
- Code fences stripped; `<`, `>` escaped inside code blocks.
- Every command block must be copy-paste runnable; prompt forbids placeholders
  like `your-key-here` without an adjacent explanation sentence.
- Hard refusal topics (none expected) logged and skipped.

---

## 7. Rendering — Pixel-Faithful Template

- `template.html.j2` embeds the **exact CSS** captured from
  `local_rag_guide` (CSS custom properties, Plus Jakarta Sans + Fira Code imports,
  `.navbar`, gradient `header`, negative-margin `.container`, `.step-box/.step-number`,
  `.code-block` with green left border, `.fix-box`, `.alert-box`, `table.checklist`,
  `footer`).
- **SVG diagrams are built deterministically in Python** from `diagram_spec`
  (grid layout math, arrowheads, dashed group boxes, red "cloud" groups) — the LLM
  never emits raw SVG coordinates. This guarantees valid, aligned diagrams every run.
- Renderer output validated with a lightweight HTML parse (well-formedness, no empty
  sections) before upload.

---

## 8. Draft Delivery — Discord Webhook

**Chosen: incoming webhook** (simplest free option — no OAuth, no key files):
1. Private server → `#guides-drafts` channel → Integrations → Webhooks → copy URL.
2. Per guide: one multipart POST to `DISCORD_WEBHOOK_URL` with a rich embed
   (title, tagline, source link, est. time) + the HTML file attached as
   `YYYY-MM-DD_<slug>.html`.

Details:
- Respect Discord rate limits: honor `Retry-After` on HTTP 429; backoff ×3 on 5xx/network.
- Local copies are **always** kept in `out/` first — a delivery failure never loses a guide.
- Optional second webhook (`DISCORD_WEBHOOK_URL_PUBLISHED`) for a `#guides-published`
  channel once output quality is trusted.
- *(Superseded alternative kept for reference: Google Drive service-account upload —
  same result but far more setup ceremony.)*

---

## 9. Scheduling & Ops (Ubuntu VPS)

```cron
CRON_TZ=Asia/Kolkata
0 6 * * * flock -n /tmp/guides-writer.lock /opt/guides-writer/run.sh >> /opt/guides-writer/logs/cron.log 2>&1
```

`run.sh`: activates venv, loads `.env`, execs `python -m guides_writer run`.
- `flock -n` prevents overlap if a run exceeds 24 h (won't happen, but cheap insurance).
- `logrotate` config included (daily, 14 rotations).
- Distinct exit codes (2 = no candidates, 3 = LLM failure, 4 = upload failure) so
  cron-mail / a future healthchecks.io ping can alert precisely.
- Documented alternative: systemd timer with `Persistent=true` (catches up missed runs
  after reboots — cron does not).

---

## 10. Configuration (`.env`)

```
XAI_API_KEY=...              # required
XAI_MODEL=grok-4-fast
PH_API_TOKEN=...             # optional but recommended
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/…   # required → #guides-drafts
DISCORD_WEBHOOK_URL_PUBLISHED=  # optional, for later auto-publish
TZ_LABEL=Asia/Kolkata
GUIDES_PER_RUN=3
SOURCE_MODE=merge            # merge | ph | gh | taaft | ph>gh>taaft
DRY_RUN=false                # true → write ./out/, skip delivery & history update
```

---

## 11. Project Structure

```
automatic-guides-writer/
├─ guides_writer/
│  ├─ __main__.py            # CLI: run | dry-run | test-sources | render-sample
│  ├─ config.py              # env parsing (pydantic-settings)
│  ├─ pipeline.py            # orchestrates discover→deliver
│  ├─ sources/               # base.py, producthunt.py, github_trending.py, taaft.py
│  ├─ agents/                # selector.py, writer.py  (prompt builders + validators)
│  ├─ llm/client.py          # OpenAI-compatible wrapper (Groq default), retries, JSON-mode helper
│  ├─ render/                # renderer.py, svg_builder.py, template.html.j2
│  ├─ storage/history.py     # dedupe state (JSON)
│  ├─ deliver/discord.py     # webhook delivery (embeds + HTML attachments)
│  └─ utils/logging.py
├─ tests/                    # unit + fixture-based parser tests, mocked LLM e2e
├─ fixtures/                 # saved HTML snapshots per source
├─ data/history.json         # generated at runtime
├─ out/                      # dry-run artifacts
├─ run.sh  crontab.txt  requirements.txt  .env.example  README.md
```

---

## 12. Resilience & State

| Failure                        | Behavior |
|--------------------------------|----------|
| One scraper down               | Warn; continue with remaining sources |
| All scrapers down              | Exit 2, cron.log entry, no delivery |
| Invalid LLM JSON               | 1 repair retry → exit 3 |
| Grok 4xx/5xx / rate limit      | tenacity exp. backoff ×4 |
| Discord delivery fails / 429   | Honor `Retry-After`, retry ×3; guide kept in `out/` for manual re-send |
| Repeat topic                   | History + fuzzy-match filter |
| Partial run (< 3 topics fresh) | Generate available count, mark run "degraded" |

History record per guide: `{date, title, slug, source_url, discord_message_id}` → also
acts as a mini audit log.

---

## 13. Testing & Verification Strategy

1. **Parser unit tests** against committed HTML fixtures (each source).
2. **LLM mock e2e**: canned Grok responses → assert valid rendered HTML containing
   signature classes (`.step-number`, `.code-block`, `table.checklist`).
3. **`test-sources` CLI**: live one-shot scraper check for ops debugging.
4. **`dry-run` mode**: full pipeline minus delivery; inspect `out/*.html` in a browser
   side-by-side with `local_rag_guide`.
5. First production week: review the daily posts in `#guides-drafts` before any
   auto-publish flow is considered.

---

## 14. Build Milestones (implementation order)

| # | Deliverable | Verify by |
|---|-------------|-----------|
| M1 | Scaffold, config, logging, xai_client (ping test) | `python -m guides_writer hello` |
| M2 | Three source adapters + fixtures + tests | `test-sources` live |
| M3 | Selector agent + history/dedupe | dry-run prints 3 picks JSON |
| M4 | Template + SVG builder + renderer | sample render matches reference visually |
| M5 | Writer agent (outline→write) | full guide HTML from a real repo |
| M6 | Discord delivery (`#guides-drafts`) | guides land in the channel |
| M7 | Cron + run.sh + logrotate + exit codes | simulated 06:00 run on VPS |
| M8 | Polish: degraded-run paths, README, dry-run docs | full green test suite |

---

## 15. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Product Hunt blocks scraping | Official API token is primary path |
| GitHub/TAAFT markup drift | Isolated parsers, fixture tests, non-fatal failures |
| LLM hallucinates broken commands | Rubric prompts, escape/validate, manual review via Discord drafts |
| SVG garbage from LLM | Python builds all SVG; LLM only supplies node labels |
| Grok schema drift / downtime | Strict pydantic + repair-retry + exit codes |
| Webhook URL leakage | Treated as a secret in `.env`; regenerate in Discord if ever leaked |

---

## 16. Decisions (confirmed)

1. **Output volume:** generate **3 full guides/day**.
2. **Sources:** merge & rank all three; Grok picks the best topics across sources
   (`SOURCE_MODE=merge`).
3. **Publish flow:** the 3 guides are posted daily to the private Discord channel
   **`#guides-drafts`** via webhook; publishing to the live site stays manual.
4. **Timezone:** 06:00 **IST** → `CRON_TZ=Asia/Kolkata`.
5. **Catalog upkeep (phase-2):** optionally auto-maintain an `index.html` catalog page.
6. **Delivery destination:** Discord webhook (Google Drive service-account documented as
   a swap-in alternative in §8).
7. **Branding:** identical UVF IT branding — navbar "UVF IT", footer
   "© UVF IT · www.uvfarms.in".
8. **Depth:** full blueprint depth (~30–45 min guides) matching `local_rag_guide`.
9. **Product Hunt:** user will register for the official free developer token.
10. **Dev flow:** develop + test on Windows; VPS deployment handled at Phase 7.

Ready to start implementation at **M1** on your go-ahead.
