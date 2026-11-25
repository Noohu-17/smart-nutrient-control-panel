# scripts/check.ps1 - runs every check in one go (task T1-4).
#
# Run from the repo root:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
#
# Checks: Python 3.13, back end (ruff + pytest), simulator (ruff + pytest),
# app (lint + tests). Every check runs even if an earlier one fails; the script
# ends with a list of what failed and exits with 1, or exits with 0 if all passed.
#
# Needs, once per laptop (see README.md, "Checks"):
#   backend\.venv  (or venv)  with  pip install -r requirements-dev.txt
#   simulator\.venv (or venv) with  pip install -r requirements-dev.txt
#   app\node_modules          with  npm ci   (current Node LTS)

$root = Split-Path -Parent $PSScriptRoot
$failed = New-Object System.Collections.Generic.List[string]

# The folder's own virtual environment if there is one, otherwise "python" on the PATH.
function Get-Python([string]$folder) {
    foreach ($venv in ".venv", "venv") {
        $exe = Join-Path $root "$folder\$venv\Scripts\python.exe"
        if (Test-Path $exe) { return $exe }
    }
    return "python"
}

function Invoke-Check([string]$name, [string]$folder, [scriptblock]$command) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    Push-Location (Join-Path $root $folder)
    try {
        $global:LASTEXITCODE = 1   # stays 1 if the command can't even start
        & $command
        if ($LASTEXITCODE -ne 0) { $failed.Add($name) }
    } catch {
        Write-Host $_ -ForegroundColor Red
        $failed.Add($name)
    } finally {
        Pop-Location
    }
}

# The back end's database tests use the dev stack's PostgreSQL. If infra/.env moves it to
# another port (POSTGRES_PORT, e.g. 5433 when a local PostgreSQL uses 5432), use that port.
$envFile = Join-Path $root "infra\.env"
if (-not $env:DB_PORT -and (Test-Path $envFile)) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^\s*POSTGRES_PORT\s*=\s*(\d+)' } | Select-Object -First 1
    if ($line -and $line -match '=\s*(\d+)') { $env:DB_PORT = $Matches[1] }
}

$backendPython = Get-Python "backend"
$simulatorPython = Get-Python "simulator"
$is313 = "import sys; print(sys.version); sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)"

Invoke-Check "Python 3.13 (backend)"   "backend"   { & $backendPython -c $is313 }
Invoke-Check "Python 3.13 (simulator)" "simulator" { & $simulatorPython -c $is313 }
Invoke-Check "Backend ruff"            "backend"   { & $backendPython -m ruff check . }
Invoke-Check "Backend pytest"          "backend"   { & $backendPython -m pytest -q -rs }
Invoke-Check "Simulator ruff"          "simulator" { & $simulatorPython -m ruff check . }
Invoke-Check "Simulator pytest"        "simulator" { & $simulatorPython -m pytest -q }
Invoke-Check "App lint"                "app"       { npm run lint }
Invoke-Check "App tests"               "app"       { npm test }

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + ($failed -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "All checks passed." -ForegroundColor Green
exit 0
