<#
.SYNOPSIS
    Paper Research — Smoke test script.

.DESCRIPTION
    Runs a quick sanity check on the project:
    1. Unit test suite (in-memory DB, no side effects)
    2. Import verification (all key modules loadable)
    3. Database initialization (dev mode, temporary DB)
    4. Config path resolution check

    Run from the project root:
        pwsh scripts/smoke.ps1

.NOTES
    This script does NOT start the Flet GUI — it validates the backend
    and infrastructure layers only.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "=== Paper Research — Smoke Test ===" -ForegroundColor Cyan
$allOk = $true

# ---------------------------------------------------------------------------
# 1. Import check
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[1/4] Module import check..." -ForegroundColor Yellow

$modules = @(
    "app.main",
    "app.domain.models",
    "app.application.services",
    "app.application.dto",
    "app.infrastructure.arxiv.client",
    "app.infrastructure.arxiv.parser",
    "app.infrastructure.db.connection",
    "app.infrastructure.db.migrations",
    "app.infrastructure.config.runtime_paths",
    "app.infrastructure.config.app_config",
    "app.infrastructure.logging.setup",
    "app.infrastructure.scheduler.sync_scheduler"
)

foreach ($mod in $modules) {
    $result = python -c "import $mod; print('OK')" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $mod" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $mod — $result" -ForegroundColor Red
        $allOk = $false
    }
}

# ---------------------------------------------------------------------------
# 2. Config resolution
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[2/4] Runtime config resolution..." -ForegroundColor Yellow

# Dev mode
$result = python -c @"
from app.infrastructure.config.app_config import AppRuntimeConfig
config = AppRuntimeConfig.create(is_dev_mode=True)
print(f"DEV  db_path={config.db_path}")
print(f"DEV  log_file={config.log_file}")
print(f"DEV  log_level={config.log_level}")
print(f"DEV  data_dir={config.data_dir}")
"@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Dev mode config resolved" -ForegroundColor Green
    foreach ($line in ($result -split "`n")) {
        Write-Host "       $line" -ForegroundColor Gray
    }
} else {
    Write-Host "  [FAIL] Config resolution failed: $result" -ForegroundColor Red
    $allOk = $false
}

# Default mode
$result = python -c @"
from app.infrastructure.config.app_config import AppRuntimeConfig
config = AppRuntimeConfig.create(is_dev_mode=False)
print(f"REL  db_path={config.db_path}")
print(f"REL  data_dir={config.data_dir}")
"@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Release mode config resolved" -ForegroundColor Green
    foreach ($line in ($result -split "`n")) {
        Write-Host "       $line" -ForegroundColor Gray
    }
} else {
    Write-Host "  [FAIL] Config resolution failed: $result" -ForegroundColor Red
    $allOk = $false
}

# ---------------------------------------------------------------------------
# 3. Database initialization (temp DB)
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[3/4] Database initialization..." -ForegroundColor Yellow

$result = python -c @"
import tempfile, os
from app.infrastructure.db.connection import get_connection

tmp = os.path.join(tempfile.mkdtemp(), 'smoke_test.db')
conn = get_connection(tmp, auto_init=True)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
conn.close()
for t in tables:
    print(f'  table: {t[0]}')
print(f'OK — {len(tables)} tables created')
"@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Schema initialized successfully" -ForegroundColor Green
    foreach ($line in ($result -split "`n")) {
        Write-Host "       $line" -ForegroundColor Gray
    }
} else {
    Write-Host "  [FAIL] Schema initialization failed: $result" -ForegroundColor Red
    $allOk = $false
}

# ---------------------------------------------------------------------------
# 4. Unit tests
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "[4/4] Unit tests..." -ForegroundColor Yellow

$result = python -m pytest tests/ -v --tb=short 2>&1
$testExitCode = $LASTEXITCODE

# Show summary only (last few lines)
$lines = $result -split "`n"
$showCount = [math]::Min(10, $lines.Count)
if ($showCount -gt 0) {
    foreach ($line in $lines[-$showCount..-1]) {
        Write-Host "       $line" -ForegroundColor Gray
    }
}

if ($testExitCode -eq 0) {
    Write-Host "  [OK] All unit tests passed." -ForegroundColor Green
} else {
    Write-Host "  [FAIL] Some tests failed (exit code $testExitCode)." -ForegroundColor Red
    $allOk = $false
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "  SMOKE TEST: ALL CHECKS PASSED" -ForegroundColor Green
} else {
    Write-Host "  SMOKE TEST: SOME CHECKS FAILED" -ForegroundColor Red
}
Write-Host "=====================================" -ForegroundColor Cyan

exit ($allOk ? 0 : 1)
