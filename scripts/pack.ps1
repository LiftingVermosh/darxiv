<#
.SYNOPSIS
    Paper Research — Desktop packaging script.

.DESCRIPTION
    Uses PyInstaller to create a standalone Windows executable.
    Run from the project root:
        pwsh scripts/pack.ps1

.OUTPUTS
    dist/PaperResearch.exe — standalone Windows executable.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "=== Paper Research — Packaging ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------

Write-Host "[1/5] Checking prerequisites..." -ForegroundColor Yellow

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    Write-Error "Python not found on PATH. Please install Python 3.11+."
    exit 1
}

Write-Host "  Python: $($python.Source)" -ForegroundColor Green

# Verify pyinstaller is available
$pyi = python -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller not found — installing..." -ForegroundColor Yellow
    pip install pyinstaller
}

Write-Host "  PyInstaller: $(python -c 'import PyInstaller; print(PyInstaller.__version__)' 2>&1)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Clean
# ---------------------------------------------------------------------------

Write-Host "[2/5] Cleaning previous build artifacts..." -ForegroundColor Yellow
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force *.spec -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 3. Build
# ---------------------------------------------------------------------------

Write-Host "[3/5] Building executable with PyInstaller..." -ForegroundColor Yellow

python -m PyInstaller `
    --name "PaperResearch" `
    --onedir `
    --windowed `
    --clean `
    --noconfirm `
    --add-data "app;app" `
    --hidden-import "flet" `
    --hidden-import "flet_core" `
    --hidden-import "httpx" `
    --hidden-import "feedparser" `
    --hidden-import "pydantic" `
    --hidden-import "dateutil" `
    --hidden-import "app.infrastructure.db.migrations" `
    --collect-all "flet" `
    --collect-all "flet_core" `
    app/main.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed."
    exit 1
}

Write-Host "  Build completed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. Verify output
# ---------------------------------------------------------------------------

Write-Host "[4/5] Verifying output..." -ForegroundColor Yellow

$exePath = "dist\PaperResearch\PaperResearch.exe"
if (-not (Test-Path $exePath)) {
    Write-Error "Output executable not found: $exePath"
    exit 1
}

$size = (Get-Item $exePath).Length
Write-Host "  Executable: $exePath" -ForegroundColor Green
Write-Host "  Size: $([math]::Round($size / 1MB, 1)) MB" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. Done
# ---------------------------------------------------------------------------

Write-Host "[5/5] Packaging complete!" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Output directory: dist\PaperResearch\" -ForegroundColor White
Write-Host "  Executable:       dist\PaperResearch\PaperResearch.exe" -ForegroundColor White
Write-Host ""
Write-Host "  To run: .\dist\PaperResearch\PaperResearch.exe" -ForegroundColor White
Write-Host "  To verify: pwsh scripts\smoke.ps1" -ForegroundColor White
