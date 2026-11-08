param([string]$Project = $env:CLOUDFLARE_PAGES_PROJECT, [string]$Branch = $env:CLOUDFLARE_PAGES_BRANCH)
if (-not $Project) { $Project = "uvf-guides" }
$env:CI = "1"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:CLOUDFLARE_API_TOKEN -and -not $env:CLOUDFLARE_API_KEY) {
  $loggedIn = $false
  $who = ""
  try { $who = (npx --yes wrangler whoami 2>&1 | Out-String); $loggedIn = $LASTEXITCODE -eq 0 } catch { $loggedIn = $false; $who = "$_" }
  if (-not $loggedIn -and $who -match "workerd") {
    Write-Warning "wrangler install is missing its workerd binary - clearing npx cache and retrying once"
    Get-ChildItem (Join-Path $env:LOCALAPPDATA "npm-cache\_npx") -Directory -ErrorAction SilentlyContinue |
      Where-Object { Test-Path (Join-Path $_.FullName "node_modules\wrangler") } |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    try { $who = (npx --yes wrangler whoami 2>&1 | Out-String); $loggedIn = $LASTEXITCODE -eq 0 } catch { $loggedIn = $false }
  }
  if (-not $loggedIn) {
    Write-Warning "wrangler whoami failed - refusing to deploy."
    if ($who) { Write-Host $who }
    Write-Warning "If you are logged out, run: npx wrangler login"
    exit 1
  }
}
$indexFile = Join-Path (Get-Location) "out\index.html"
if (-not (Test-Path $indexFile) -or (Get-Item $indexFile).Length -lt 1024 -or
    -not (Select-String -Path $indexFile -Pattern "blog-grid" -SimpleMatch -Quiet)) {
  Write-Warning "out/index.html is missing, empty, or has no blog-grid - refusing to deploy"
  exit 1
}
Write-Host "Deploying out/ to Worker: $Project"
npx --yes wrangler deploy --assets out --name $Project --compatibility-date 2026-08-25
exit $LASTEXITCODE
