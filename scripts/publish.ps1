param(
  [string]$Project = $env:CLOUDFLARE_PAGES_PROJECT,
  [string]$Branch = $env:CLOUDFLARE_PAGES_BRANCH
)
if (-not $Project) { $Project = "guides" }
if (-not $Branch) { $Branch = "main" }
if (-not $env:CLOUDFLARE_API_TOKEN -and -not $env:CLOUDFLARE_API_KEY) {
  Write-Warning "CLOUDFLARE_API_TOKEN not set — skipping Cloudflare Pages publish (guides still in out/)."
  exit 0
}
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command wrangler -ErrorAction SilentlyContinue)) {
  Write-Host "wrangler not found — installing..."
  npm install -g wrangler
}
Write-Host "Deploying out/ to Cloudflare Pages project: $Project (branch: $Branch)"
npx wrangler pages deploy out --project-name $Project --branch $Branch
