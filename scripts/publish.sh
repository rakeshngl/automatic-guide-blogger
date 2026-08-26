#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${CLOUDFLARE_PAGES_PROJECT:-guides}"
BRANCH="${CLOUDFLARE_PAGES_BRANCH:-main}"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] && [ -z "${CLOUDFLARE_API_KEY:-}" ]; then
  echo "CLOUDFLARE_API_TOKEN not set — skipping Cloudflare Pages publish (guides still in out/)." >&2
  exit 0
fi

if ! command -v wrangler >/dev/null 2>&1; then
  echo "wrangler not found — installing globally via npm..."
  npm install -g wrangler
fi

echo "Deploying out/ to Cloudflare Pages project: $PROJECT (branch: $BRANCH)"
npx wrangler pages deploy out --project-name="$PROJECT" --branch="$BRANCH"
