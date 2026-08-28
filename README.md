# Automatic Guides Writer

Daily autonomous agent that discovers trending tech from 7 sources (Product Hunt / GitHub Trending / TAAFT / Hacker News Show HN / Hugging Face Spaces / Dev.to / Reddit r/selfhosted), picks 3 beginner-guide-worthy topics with an LLM, generates complete HTML blueprints styled exactly like [guides.uvfarms.in/local_rag_guide](https://guides.uvfarms.in/local_rag_guide), and delivers them to a private Discord channel (`#guides-drafts`) via webhook. Runs at **06:00 IST** on an Ubuntu VPS via `cron` + `flock`.

## Features

- **7-source discovery** — Product Hunt GraphQL (via `curl_cffi` TLS impersonation to bypass Cloudflare), GitHub Trending scrape, TAAFT scrape, Hacker News Show HN (Algolia API), Hugging Face Spaces (likes API), Dev.to top-week (API), Reddit r/selfhosted (Atom feed; JSON is 403-blocked); merged into one pool, non-fatal per source
- **LLM topic selection** — provider-agnostic OpenAI-compatible client (Groq `qwen/qwen3.6-27b` default, `grok-4-fast` swappable via `LLM_BASE_URL`), fuzzy dedupe vs 14-day `history.json` (rapidfuzz)
- **Two-stage writer** — outline (rubric: ≥4 phases, ≥1 code/phase, ≥3 checks) → split write into 2 part-calls (fits 8k TPM free-tier window), deterministic SVG diagrams (Python builds, LLM only supplies labels)
- **Pixel-faithful rendering** — verbatim CSS from the reference guide, Jinja2, autoescape always-on, `**bold**`/`code` rich text, `.code-comment` highlighting
- **Discord delivery** — rich green embeds + `text/html` attachments, `Retry-After` on 429, `out/` always retained
- **Ops-ready** — `run.sh`, `crontab.txt` (`CRON_TZ=Asia/Kolkata`), `ops/logrotate.conf`, exit codes, `data/runs/*.json` summaries

## Project Structure

```
guides_writer/
  __main__.py            # CLI: hello | test-sources | select | render-sample | run
  config.py              # pydantic-settings (.env)
  pipeline.py            # discover → enrich → select → write×3 → render×3 → deliver
  sources/               # base.py, producthunt, github_trending, taaft, hn_show, hf_spaces, devto, reddit
  agents/                # selector.py, writer.py (enrich + outline + split-write)
  llm/client.py          # OpenAI-compatible, tenacity 8-70s ×6, JSON repair, per-model reasoning_effort
  render/                # schema.py, svg_builder.py, renderer.py, templates/guide.{css,html.j2}
  deliver/discord.py     # build_deliverer(webhook_url)
  storage/history.py     # 14-day rolling dedupe
  utils/logging.py       # JSON-lines + rotating file
tests/  fixtures/  data/  out/  ops/  logs/
run.sh  crontab.txt  requirements.txt  .env.example
```

## Prerequisites

- Python 3.11+ (3.13 recommended), `pip`
- A Groq API key (free at [console.groq.com](https://console.groq.com), `gsk_...`) or xAI key (`xai-...`)
- A Product Hunt developer token (free at [producthunt.com/v2/oauth/applications](https://www.producthunt.com/v2/oauth/applications) → **Create Token**)
- A Discord private server → `#guides-drafts` channel → Integrations → Webhooks → Copy URL

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in keys (see table below)
.venv/bin/python -m guides_writer hello          # LLM round-trip → pong
.venv/bin/python -m guides_writer test-sources   # ~120 candidates from 7 sources
.venv/bin/python -m guides_writer render-sample  # -> out/sample.html (eyeball vs reference)
.venv/bin/python -m guides_writer run --dry-run  # full pipeline, no delivery/history
```

## Configuration (.env)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `LLM_API_KEY` | yes | — | `gsk_...` (Groq) or `xai-...` (xAI) |
| `LLM_BASE_URL` | no | `https://api.groq.com/openai/v1` | `https://api.x.ai/v1` for Grok |
| `LLM_MODEL` | no | `qwen/qwen3.6-27b` | `openai/gpt-oss-120b`, `grok-4-fast`, etc. |
| `PH_API_TOKEN` | no | — | Product Hunt developer token; without it PH is skipped |
| `DISCORD_WEBHOOK_URL` | no | — | `https://discord.com/api/webhooks/...` → `#guides-drafts`; if unset, `run` renders locally only |
| `LLM_MODEL` | no | `qwen/qwen3.6-27b` | provider default; free-tier daily 200k TPD |
| `TZ_LABEL` | no | `Asia/Kolkata` | for reference only; cron uses `CRON_TZ` |
| `GUIDES_PER_RUN` | no | `3` |  |
| `SOURCE_MODE` | no | `merge` | `merge` pools all three; `ph>gh>taaft` etc. possible |
| `DRY_RUN` | no | `false` | `true` → `out/` only, no Discord, no `history.json` |

`LLM_MODEL` note: `qwen/qwen3.6-27b` is the free-tier default (separate 200k TPD quota from `grok-4-fast`/`gpt-oss-120b`). One daily production run ≈40k tokens fits easily; test runs burned 197k in this repo’s dev day.

## Usage

```bash
.venv/bin/python -m guides_writer hello
.venv/bin/python -m guides_writer test-sources
.venv/bin/python -m guides_writer select              # prints {"picks": [...]}
.venv/bin/python -m guides_writer select --record     # also persists to history
.venv/bin/python -m guides_writer render-sample
.venv/bin/python -m guides_writer run --dry-run
.venv/bin/python -m guides_writer run                 # delivers to Discord + records history
# Windows:
.venv\Scripts\python.exe -m guides_writer run
```

Exit codes: `0` ok/partial, `2` no candidates, `3` selection/all-guides failed, `4` Discord delivery failed.

## Scheduling (Ubuntu VPS)

```bash
# deploy
scp -r . ubuntu@YOUR_VPS:/opt/guides-writer
scp .env ubuntu@YOUR_VPS:/opt/guides-writer/.env
ssh ubuntu@YOUR_VPS "cd /opt/guides-writer && chmod +x run.sh && ./run.sh run --dry-run"

# cron (06:00 IST) — paste via: crontab -e
# crontab.txt already contains:
CRON_TZ=Asia/Kolkata
0 6 * * * flock -n /tmp/guides-writer.lock /opt/guides-writer/run.sh run >> /opt/guides-writer/logs/cron.log 2>&1

# logrotate
sudo cp /opt/guides-writer/ops/logrotate.conf /etc/logrotate.d/guides-writer
sudo logrotate --debug /etc/logrotate.d/guides-writer
```

`flock -n` prevents overlap. Systemd timer with `Persistent=true` is documented as an alternative in `PLAN.md` §9.

## Testing

```bash
.venv/bin/pytest -q          # 53 tests, ~25s, fully offline (fixtures + FakeLLM + mocked httpx)
.venv/bin/pytest tests/test_failure_drills.py -q  # scraper isolation, bad LLM JSON, invalid webhook, exit codes
```

The offline suite is CI-safe — no Groq / Product Hunt / Discord calls. Live checks: `test-sources`, `hello`, `run --dry-run`.

## Ops Runbook

| Artifact | Path | Purpose |
|---|---|---|
| Logs | `logs/app.log` (JSON lines, rotated) + `logs/cron.log` | `tail -f logs/app.log` |
| History | `data/history.json` | 14-day dedupe list; delete to reset |
| Run summaries | `data/runs/YYYY-MM-DD.json` | `status`, `degraded_selection`, `delivery_ids`, `delivery_errors` |
| Out | `out/YYYY-MM-DD_<slug>.html` | always kept, even on delivery failure |

**Troubleshooting**

| Symptom | Cause | Fix |
|---|---|---|
| `producthunt 403 Just a moment` | Cloudflare block (no `curl_cffi`) | `pip install curl_cffi` (already in requirements) |
| `LLM 413 Request too large` | TPM 8k/min exceeded | sleeps (22s/28s/38s) are built-in; reduce `MAX_ENRICH_CHARS` if custom |
| `429 TPD Limit 200k` | daily free-tier quota | wait for UTC midnight or switch `LLM_MODEL` (separate quota per model) |
| `json_validate_failed` | truncated/invalid JSON | auto-repaired once via `ValueError` path; check `failed_generation` in logs |
| Discord nothing arrives | `DISCORD_WEBHOOK_URL` not set or `DRY_RUN=true` | `run` without `--dry-run` + valid webhook |
| History never advances | `DRY_RUN=true` | history only written when not dry-run |

## First-Week Monitoring Plan

Eyeball `#guides-drafts` daily before trusting autonomy. Each run posts 3 embeds + HTML files. Promote to a `#guides-published` webhook (`DISCORD_WEBHOOK_URL_PUBLISHED`, `PLAN.md` §8) only after 5 consecutive clean days.

## License

Internal — UVF IT.
