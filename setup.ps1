param(
    [switch]$ForceReinstall,
    [switch]$Yolo
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

if ($Yolo) {
    # Training tools for the optional YOLO detector (docs/YOLO.md). With an
    # NVIDIA card, install the CUDA build of torch first so training uses it.
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host "NVIDIA GPU found: installing CUDA torch..."
        & $Python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
        if ($LASTEXITCODE -ne 0) {
            & $Python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "CUDA torch failed; training will use the CPU." -ForegroundColor Yellow }
    }
    & $Python -m pip install -r (Join-Path $Project "requirements-yolo.txt")
    if ($LASTEXITCODE -ne 0) { throw "YOLO training tools install failed" }
    # Runs the trained detector on any DirectX 12 GPU (else OpenCV uses the CPU).
    & $Python -m pip install onnxruntime-directml
    if ($LASTEXITCODE -ne 0) { Write-Host "onnxruntime-directml failed; the detector will run on the CPU." -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  .\run.cmd -Doctor"
Write-Host "  .\run.cmd -Calibrate regions"
Write-Host "  .\run.cmd -Calibrate form"
Write-Host "  .\run.cmd -Calibrate sample"
Write-Host "  .\run.cmd -Debug"
