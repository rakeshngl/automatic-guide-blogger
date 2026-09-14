param(
    [string]$ProjectDir = "C:\Users\TKumar\Downloads\Automatic Guides Writer"
)

$ErrorActionPreference = "Continue"
$publishDone = $false

if (Test-Path $ProjectDir) {
    Set-Location $ProjectDir
} else {
    Write-Host "[ERROR] Project dir not found: $ProjectDir"
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "=========================================="
Write-Host " UVF Guides Writer - Daily Run"
Write-Host " $(Get-Date -Format 'dd-MM-yyyy HH:mm:ss')"
Write-Host "=========================================="
Write-Host ""

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[ERROR] Virtual env not found. Run: python -m venv .venv"
    Read-Host "Press Enter to close"
    exit 1
}

.\.venv\Scripts\python.exe -m guides_writer run
$code = $LASTEXITCODE

Write-Host ""
Write-Host "------------------------------------------"

$shouldPublish = $false
switch ($code) {
    0 { Write-Host "[OK] Guides produced - publishing to Cloudflare..."; $shouldPublish = $true }
    2 { Write-Host "[WARN] No candidates today (exit 2) - nothing to publish" }
    3 { Write-Host "[FAIL] Selection/LLM failed (exit 3) - nothing to publish" }
    4 { Write-Host "[FAIL] Discord delivery failed (exit 4) - still publishing built guides"; $shouldPublish = $true }
    default { Write-Host "[FAIL] Unexpected exit code $code" }
}

if ($shouldPublish) {
    Write-Host ""
    Write-Host "Publishing to Cloudflare (uvf-guides)..."
    if (Test-Path ".env") {
        Get-Content ".env" | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line -match "^[A-Za-z_][A-Za-z0-9_]*\s*=") {
                $name = ($line -split "=", 2)[0].Trim()
                $value = ($line -split "=", 2)[1].Trim().Trim('"', "'")
                if (-not $value) { return }
                Set-Item -Path "Env:$name" -Value $value
            }
        }
    }
    $env:PUBLISH_SETUP = "loaded .env for token"
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\publish.ps1"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Published to https://guides.uvfarms.in"
        $publishDone = $true
    } else {
        Write-Host ""
        Write-Host "[WARN] ============================================"
        Write-Host "[WARN] Publish FAILED. Fix and retry manually:"
        Write-Host "[WARN]   powershell -File scripts\publish.ps1"
        Write-Host "[WARN] If the error is auth, add CLOUDFLARE_API_TOKEN=... to .env then rerun."
        Write-Host "[WARN] ============================================"
    }
}

Write-Host "------------------------------------------"
Write-Host "Log: logs\app.log"
Write-Host "Guides: out\html"
Write-Host "Catalog: out\index.html"
Write-Host ""
Write-Host "Published: $publishDone"
Write-Host ""
Read-Host "Press Enter to close"
