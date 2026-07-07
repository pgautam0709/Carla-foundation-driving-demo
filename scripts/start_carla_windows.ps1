<#
.SYNOPSIS
    Start a local CARLA server on Windows.

.DESCRIPTION
    Locates CarlaUE4.exe (or CarlaUnreal.exe), starts it with the specified
    port, and prints connection information. Intended for Windows development
    environments with CARLA installed natively.

    After running this script, set CARLA_HOST=127.0.0.1 in your shell and
    run `make smoke` to verify connectivity.

.PARAMETER CARLA_ROOT
    Path to the CARLA installation directory.
    Defaults to the CARLA_ROOT environment variable.
    Example: C:\CARLA\CARLA_0.9.15

.PARAMETER Port
    CARLA server port. Default: 2000.

.PARAMETER Headless
    If specified, adds -RenderOffScreen flag (no display window).

.EXAMPLE
    # Start CARLA in a visible window:
    powershell -ExecutionPolicy Bypass -File scripts\start_carla_windows.ps1 `
        -CARLA_ROOT C:\CARLA\CARLA_0.9.15

.EXAMPLE
    # Start CARLA headlessly on port 3000:
    powershell -ExecutionPolicy Bypass -File scripts\start_carla_windows.ps1 `
        -CARLA_ROOT C:\CARLA\CARLA_0.9.15 -Port 3000 -Headless

.NOTES
    This script is not required on macOS or Linux.
    For macOS, use: bash scripts/start_carla_docker.sh
    For Linux, use: ./CarlaUE4.sh -RenderOffScreen
#>
param(
    [string]$CARLA_ROOT = $env:CARLA_ROOT,
    [int]$Port = 2000,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "────────────────────────────────────────────────────────────────" -ForegroundColor White
Write-Host "  CARLA Foundation Driving Demo — Windows Launcher" -ForegroundColor Cyan
Write-Host "────────────────────────────────────────────────────────────────" -ForegroundColor White
Write-Host ""

# ── Resolve CARLA_ROOT ────────────────────────────────────────────────────────
if ([string]::IsNullOrEmpty($CARLA_ROOT)) {
    Write-Host "  [FAIL] -CARLA_ROOT is required. Example:" -ForegroundColor Red
    Write-Host "         powershell -File scripts\start_carla_windows.ps1 -CARLA_ROOT C:\CARLA\CARLA_0.9.15"
    Write-Host ""
    Write-Host "  Tip: Set permanently with:"
    Write-Host '         [System.Environment]::SetEnvironmentVariable("CARLA_ROOT", "C:\CARLA\CARLA_0.9.15", "User")'
    exit 1
}

if (-not (Test-Path $CARLA_ROOT)) {
    Write-Host "  [FAIL] CARLA_ROOT path does not exist: $CARLA_ROOT" -ForegroundColor Red
    Write-Host "         Download CARLA 0.9.15 from:"
    Write-Host "         https://github.com/carla-simulator/carla/releases/tag/0.9.15"
    exit 1
}

Write-Host "  → CARLA_ROOT : $CARLA_ROOT"
Write-Host "  → Port       : $Port"
Write-Host "  → Headless   : $Headless"

# ── Find executable ───────────────────────────────────────────────────────────
$CarlaExe = $null
$CandidateNames = @("CarlaUE4.exe", "CarlaUnreal.exe")
foreach ($Name in $CandidateNames) {
    $Candidate = Join-Path $CARLA_ROOT $Name
    if (Test-Path $Candidate) {
        $CarlaExe = $Candidate
        break
    }
}

if ($null -eq $CarlaExe) {
    Write-Host ""
    Write-Host "  [FAIL] Could not find CarlaUE4.exe or CarlaUnreal.exe in:" -ForegroundColor Red
    Write-Host "         $CARLA_ROOT"
    Write-Host ""
    Write-Host "  Expected one of:"
    foreach ($Name in $CandidateNames) {
        Write-Host "    $CARLA_ROOT\$Name"
    }
    exit 1
}

Write-Host "  → Executable : $CarlaExe"

# ── Build argument list ───────────────────────────────────────────────────────
$Args = @("-carla-port=$Port")
if ($Headless) {
    $Args += "-RenderOffScreen"
}
$Args += "-nosound"

Write-Host ""
Write-Host "  Running: $CarlaExe $($Args -join ' ')"
Write-Host ""

# ── Start process ─────────────────────────────────────────────────────────────
try {
    Start-Process -FilePath $CarlaExe -ArgumentList $Args -WindowStyle Normal
} catch {
    Write-Host "  [FAIL] Failed to start CARLA: $_" -ForegroundColor Red
    exit 1
}

Write-Host "  [ OK ] CARLA process launched. Allow 20-40s to start." -ForegroundColor Green
Write-Host ""
Write-Host "  To connect from this session or another terminal:"
Write-Host ""
Write-Host '    set CARLA_HOST=127.0.0.1' -ForegroundColor Yellow
Write-Host "    set CARLA_PORT=$Port" -ForegroundColor Yellow
Write-Host '    set PROFILE=windows_local' -ForegroundColor Yellow
Write-Host '    make smoke' -ForegroundColor Yellow
Write-Host ""
Write-Host "  Install CARLA Python wheel (once per environment):"
Write-Host "    pip install $CARLA_ROOT\PythonAPI\carla\dist\carla-0.9.15-cp310-*.whl"
Write-Host ""
Write-Host "  To stop CARLA:"
Write-Host "    Stop-Process -Name CarlaUE4 -Force"
Write-Host ""
