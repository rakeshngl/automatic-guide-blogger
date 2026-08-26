#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${CLOUDFLARE_PAGES_PROJECT:-uvf-guides}"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] && [ -z "${CLOUDFLARE_API_KEY:-}" ] && ! npx wrangler whoami >/dev/null 2>&1; then
  echo "Not logged in and no CLOUDFLARE_API_TOKEN — run npx wrangler login or set token." >&2
  exit 0
fi
echo "Deploying out/ to Worker: $PROJECT"
npx wrangler deploy --assets out --name "$PROJECT" --compatibility-date 2026-08-25
