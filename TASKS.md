# Tasks & Phases — Automatic Guides Writer

Checklist companion to `PLAN.md`. Work top-to-bottom; each phase ends with a
**Verify gate** — do not proceed until it passes.

---

## Phase 0 — Project Setup & Prerequisites *(you + me)*

- [x] Create repo folder structure (`guides_writer/`, `tests/`, `fixtures/`, `data/`, `out/`)
- [x] `requirements.txt` / venv bootstrap script (`run.sh` skeleton)
- [x] `.env.example` committed; real `.env` gitignored
- [x] **You:** create xAI account → generate `XAI_API_KEY` *(superseded: Groq free-tier key, provider-agnostic client)*
- [x] **You:** register Product Hunt API app → `PH_API_TOKEN` (free developer token)
- [x] **You:** private Discord server → `#guides-drafts` channel → webhook created → URL stored in `.env` as `DISCORD_WEBHOOK_URL`
- [x] `.gitignore` (`.env`, `secrets/`, `data/`, `out/`, `logs/`, `__pycache__/`, `venv/`)

**✅ Verify:** `pip install -r requirements.txt` succeeds in a fresh venv; secrets exist locally, never in repo.

---

## Phase 1 — Foundation: Config, Logging, LLM Client *(M1)* ✅

- [x] `config.py` — pydantic-settings loading all env vars (fail-fast on missing required)
- [x] `utils/logging.py` — JSON-lines logger, rotating file handler (`logs/app.log`)
- [x] `llm/client.py` — provider-agnostic OpenAI-compatible client (Groq default; xAI swap-in via `LLM_BASE_URL`)
  - [x] timeout + `tenacity` exponential backoff (×4) on 429/5xx/network
  - [x] `chat(messages, model, temperature)` helper (incl. reasoning-model support)
  - [x] `chat_json(messages, schema)` → validates via pydantic, 1 repair-retry on invalid JSON
- [x] CLI entry `__main__.py` with subcommand `hello`

**✅ Verify:** ~~`python -m guides_writer hello` → prints live Grok round-trip ("pong")~~ **PASSED** — `openai/gpt-oss-120b` replied "pong".

---

## Phase 2 — Source Adapters: Discover Candidates *(M2)* ✅

- [x] `sources/base.py` — `SourceAdapter` protocol + pydantic `CandidateItem` model
- [x] `sources/producthunt.py` — GraphQL API (top by RANKING) via `curl_cffi` TLS impersonation (bypasses Cloudflare); fixture-tested parser
- [x] `sources/github_trending.py` — parse `github.com/trending?since=daily` (repo, desc, language, stars-today)
- [x] `sources/taaft.py` — parse theresanaiforthat.com home "today" rows (`div.home-today-row`: name, topic, views); failure-tolerant
- [x] Snapshots saved into `fixtures/` (`producthunt_posts.json`, `github_trending.html`, `taaft_home.html`)
- [x] Per-source pytest against fixtures (9/9 passing); each adapter independently non-fatal on error
- [x] CLI subcommand `test-sources` (live check, pretty-prints candidate pool)

**✅ Verify:** ~~merged pool of ~30–60 candidates from ≥ 2 sources~~ **PASSED** — live run: PH 10 + GH 17 + TAAFT 20 = **47 unique candidates, 0 failures**.

---

## Phase 3 — Selection Agent: Pick Today's 3 Topics *(M3)* ✅

- [x] `storage/history.py` — load/append `data/history.json` (BOM-tolerant); last-N-days title list
- [x] Prompt builder: guide-worthiness rubric (beginner-buildable ≤ 45 min, free-tier/self-host, hands-on depth), history exclusion list injected
- [x] `agents/selector.py` — one LLM call → strict JSON of 3 picks (title, angle, why, source_url, tags…); source-id normalizer validator
- [x] Pydantic validation + repair-retry; fuzzy dedupe vs history (`rapidfuzz`, WRatio+token_set @ 82)
- [x] Degraded path: < 3 fresh topics → emit what exists, mark run `degraded`
- [x] Unit tests with mocked LLM responses — 19/19 passing incl. invalid-then-valid repair path

**✅ Verify:** **PASSED** — `select` prints 3 picks as JSON; seeded-history re-run returned entirely fresh topics and actively dropped repeated ones (`duplicates_dropped` populated).

---

## Phase 4 — Rendering Engine: Pixel-Faithful Template *(M4)* ✅

- [x] `render/templates/guide.css` — exact CSS from `local_rag_guide` (palette vars, fonts, navbar, gradient header, step-boxes, code blocks, fix/alert boxes, checklist) + mobile media query
- [x] `render/schema.py` — pydantic `Guide` schema (meta/intro/warning/diagram/phases/checklist/closing) = writer contract for M5
- [x] `render/svg_builder.py` — deterministic SVG: column layout by group, dashed group boxes (green local / red cloud), arrowheads, bidirectional edges, edge labels; LLM never draws coordinates
- [x] `render/renderer.py` — Jinja2 render; **autoescape always-on**; markdown fences stripped; `**bold**`/`` `code`` `` rich text; `#`/`//`/`<!--` comment highlighting via `.code-comment`; post-render validator (signature classes, no empty steps)
- [x] `render-sample` subcommand + bundled Umami Analytics sample exercising every component

**✅ Verify:** ~~sample ≈ reference~~ **PASSED** — class inventory identical to live page (only diff: added `.code-comment` support), all palette tokens present, SVG deterministic, 32/32 tests green. Open `out/sample.html` in a browser to eyeball.

---

## Phase 5 — Writer Agent: Generate Full Guides *(M5)* ✅

- [x] Enrichment: `enrich()` — GitHub README via API (raw) or page meta (`<title>`+`og:description`) truncated to 2.4k chars
- [x] Stage 1 `outline` (`GuideOutline`): LLM returns title/tagline/intro/warning/diagram/phases/checklist/closing → rubric enforcer (≥4 phases, ≥1 code/phase, ≥3 checks, ≥2 warnings, ≥3 diagram nodes) with one auto-repair
- [x] Stage 2 `write` — **split into two part-calls** (`GuidePart1` meta+intro+warning+diagram+first-half phases, `GuidePart2` remaining phases+checklist+closing) then merged → `Guide`; each part `max_tokens` capped (3.2k/3.8k) and TPM-paced sleeps to stay under Groq's 8k TPM free-tier window
- [x] Guardrails: runnable Ubuntu commands, no bare placeholders without adjacent explanation, beginner UVF IT voice, `**bold**`/`code` only
- [x] `pipeline.py` — discover→enrich→select→write×3→render×3; candidate→pick mapping, collision-proof filenames, degraded handling, run summary `data/runs/YYYY-MM-DD.json`; enrich/writer duck-typed for `CandidateItem`/`TopicPick`
- [x] `run --dry-run` CLI; pacing (22s/28s per LLM call + 38s between guides), 400 `json_validate_failed`→`ValueError` repair path, 413/429 TPM-aware backoff (8-70s ×6), `reasoning_effort` per-model mapping
- [x] E2E mocked tests (7 new, incl. rubric-retry, partial-failure isolation) — **39/39 green**

**✅ Verify:** **PASSED** — `python -m guides_writer run --dry-run` → **3/3 guides** (PostHog, career-ops, Google Timeline Visualizer; each 5 steps/5 code blocks/SVG/table, ~14-16 KB) in `out/`. Free-tier model switched to `qwen/qwen3.6-27b` (separate 200k TPD quota; `gpt-oss-120b` hit 197k/200k from test runs — one daily production run ≈40k fits easily).

---

## Phase 6 — Discord Delivery *(M6)* ✅

- [x] `deliver/discord.py` — `build_deliverer(webhook_url)` returns `deliver(files, guide_results)`: rich green embed (`color 0x2E7D32` with title/source/tags/file) + `text/html` attachment `YYYY-MM-DD_<slug>.html` via `?wait=true` (captures `message_id`)
- [x] Honors Discord `Retry-After` (JSON `retry_after` + header, +0.5s) on 429 and exponential backoff on 5xx/network — 4 attempts, failed files always kept in `out/` for manual re-send
- [x] Pipeline wiring: `run` builds deliverer from `DISCORD_WEBHOOK_URL` when not `--dry-run`; captures `delivery_ids`/`delivery_errors` into `data/runs/YYYY-MM-DD.json`
- [x] Config slot ready for optional `DISCORD_WEBHOOK_URL_PUBLISHED` later
- [x] Webhook connectivity smoke test + live delivery of 3 guides ✅

**✅ Verify:** **PASSED** — 6 mocked tests (embed, retry-after parsing, 429→retry, no-webhook skip); live delivery of 3 existing guides returned IDs `1540685919164698747`, `1540685926190288938`, `1540685930749239310` — check `#guides-drafts`.

---

## Phase 7 — Scheduling & Ops on Ubuntu VPS *(M7)* ✅

- [x] `run.sh` final — `set -euo pipefail`, auto-creates `.venv` on first run, `set -a; source .env`, `exec .venv/bin/python -m guides_writer "$@"`
- [x] `crontab.txt` — `CRON_TZ=Asia/Kolkata` + `0 6 * * * flock -n /tmp/guides-writer.lock /opt/guides-writer/run.sh run >> /opt/guides-writer/logs/cron.log 2>&1` (install via `crontab -e`)
- [x] Exit codes — `0` ok/partial, `2` no_candidates, `3` selection/all_guides_failed, `4` delivery failed (delivery_errors non-empty)
- [x] Run summary `data/runs/YYYY-MM-DD.json` (+ `logs/cron.log` + `logs/app.log` JSON lines)
- [x] `ops/logrotate.conf` — daily, `rotate 14`, `compress`, `delaycompress`, `missingok`, `create 0644`
- [x] Deploy steps documented (scp/rsync to `/opt/guides-writer`, `chmod +x run.sh`, `./run.sh run --dry-run`, `crontab -e`, `sudo cp ops/logrotate.conf /etc/logrotate.d/guides-writer`)
- [ ] Optional: systemd timer variant (`Persistent=true` catch-up) — documented as alternative in `PLAN.md` §9

**✅ Verify:** `bash -n run.sh` syntax ok; `crontab.txt` and `ops/logrotate.conf` present; 46/46 tests green. On VPS: `flock -n` prevents overlap; second immediate `flock -n ... run` exits with code 1.

---

## Phase 8 — Hardening, Tests & Docs *(M8)*

- [ ] Full offline pytest suite green (parsers, selector, writer-mock, renderer, svg)
- [ ] Failure drills: kill each scraper / return bad LLM JSON / point at an invalid webhook URL → correct degraded behavior + exit codes
- [ ] `README.md`: setup, env table, ops runbook, troubleshooting
- [ ] First-week monitoring plan: review daily posts in **#guides-drafts** before trusting autonomy

**✅ Verify:** clean checkout on a blank VPS path → follow README → working daily agent.

---

## Phase 9 — Go-Live & Phase-2 Backlog

- [ ] Enable crontab; monitor 3–5 consecutive runs in **#guides-drafts**
- [ ] On confidence: repoint delivery to a `#guides-published` webhook (one env var)
- [ ] Backlog (opt-in): auto-generated `index.html` catalog page · Telegram/email failure alerts · healthchecks.io ping · Playwright fallback for TAAFT markup changes
