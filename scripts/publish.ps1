param([string]$Project = $env:CLOUDFLARE_PAGES_PROJECT, [string]$Branch = $env:CLOUDFLARE_PAGES_BRANCH)
if (-not $Project) { $Project = "uvf-guides" }
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:CLOUDFLARE_API_TOKEN -and -not $env:CLOUDFLARE_API_KEY) {
  $loggedIn = $false
  try { npx wrangler whoami *> $null; $loggedIn = $LASTEXITCODE -eq 0 } catch { $loggedIn = $false }
  if (-not $loggedIn) {
    Write-Warning "Not logged in and no CLOUDFLARE_API_TOKEN - run: npx wrangler login"
    exit 1
  }
}
Write-Host "Deploying out/ to Worker: $Project"
npx wrangler deploy --assets out --name $Project --compatibility-date 2026-08-25
exit $LASTEXITCODE
