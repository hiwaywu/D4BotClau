param(
    [switch]$ForceReinstall
)

$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $env:LOCALAPPDATA "d4-visual-bot\venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Write-Host "Project: $Project"
Write-Host "Virtualenv: $Venv  (outside OneDrive on purpose)"

if (-not (Test-Path $Python)) {
    $launcher = Get-Command python -ErrorAction SilentlyContinue
    if (-not $launcher) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
    }
    if (-not $launcher) {
        throw "Python 3.11+ was not found on PATH. Install Python, then rerun this script."
    }
    Write-Host "Creating virtualenv..."
    if ($launcher.Name -eq "py.exe" -or $launcher.Name -eq "py") {
        & $launcher.Source -3 -m venv $Venv
    } else {
        & $launcher.Source -m venv $Venv
    }
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

& $Python -m pip install -r (Join-Path $Project "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

& $Python -m pip install -e $Project
if ($LASTEXITCODE -ne 0) { throw "editable install failed" }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  .\run.cmd -Doctor"
Write-Host "  .\run.cmd -Calibrate regions"
Write-Host "  .\run.cmd -Calibrate form"
Write-Host "  .\run.cmd -Calibrate sample"
Write-Host "  .\run.cmd -Debug"
