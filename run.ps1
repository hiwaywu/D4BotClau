param(
    [switch]$Doctor,
    [ValidateSet("regions", "skill", "sample", "form", "buff", "death")]
    [string]$Calibrate,
    [string]$SkillName,
    [ValidateSet("health_globe", "resource_globe", "skill_bar", "enemy_roi", "center", "minimap", "buff_bar")]
    [string]$Region,
    [switch]$Debug,
    [switch]$EnableInput,
    [switch]$IAcceptBanRisk,
    [switch]$StartHuman,
    [switch]$StartDemon,
    [double]$MaxSeconds = 0,
    [string]$Snapshots,
    [string]$Record,
    [string]$Snap,
    [switch]$Town,
    [switch]$TeachStash,
    [switch]$Helltide,
    [switch]$WithTown,
    [switch]$Cycle,
    [double]$MaxHours = 2,
    [double]$IdleResetMin = 4
)

$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $env:LOCALAPPDATA "d4-visual-bot\venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Config = Join-Path $Project "config.yaml"

if (-not (Test-Path $Python)) {
    throw "Run .\setup.cmd first (virtualenv not found at $Venv)."
}

Set-Location $Project
# Always run this folder's code, even if the shared venv was installed from a copy elsewhere.
$env:PYTHONPATH = Join-Path $Project "src"

if ($Doctor) {
    & $Python -m d4bot doctor --config $Config
    exit $LASTEXITCODE
}

if ($Cycle) {
    $CArgs = @("-m", "d4bot", "cycle", "--config", $Config, "--max-hours", "$MaxHours", "--idle-reset-min", "$IdleResetMin")
    if ($EnableInput) { $CArgs += "--enable-input" }
    if ($IAcceptBanRisk) { $CArgs += "--i-understand-ban-risk" }
    & $Python @CArgs
    exit $LASTEXITCODE
}

if ($Helltide) {
    $HArgs = @("-m", "d4bot", "helltide", "--config", $Config)
    if ($EnableInput) { $HArgs += "--enable-input" }
    if ($IAcceptBanRisk) { $HArgs += "--i-understand-ban-risk" }
    if ($WithTown) { $HArgs += "--with-town" }
    & $Python @HArgs
    exit $LASTEXITCODE
}

if ($TeachStash) {
    & $Python -m d4bot teach-stash --config $Config
    exit $LASTEXITCODE
}

if ($Town) {
    $TownArgs = @("-m", "d4bot", "town", "--config", $Config)
    if ($EnableInput) { $TownArgs += "--enable-input" }
    if ($IAcceptBanRisk) { $TownArgs += "--i-understand-ban-risk" }
    & $Python @TownArgs
    exit $LASTEXITCODE
}

if ($Snap) {
    & $Python -m d4bot snap $Snap --config $Config
    exit $LASTEXITCODE
}

if ($Record) {
    $RecordArgs = @("-m", "d4bot", "record", $Record, "--config", $Config)
    if ($MaxSeconds -gt 0) { $RecordArgs += @("--max-seconds", "$MaxSeconds") }
    & $Python @RecordArgs
    exit $LASTEXITCODE
}

if ($Calibrate) {
    if ($Calibrate -eq "skill") {
        if (-not $SkillName) {
            throw "Use -SkillName skill_1 with -Calibrate skill."
        }
        & $Python -m d4bot calibrate skill $SkillName --config $Config
    } elseif ($Calibrate -eq "regions" -and $Region) {
        & $Python -m d4bot calibrate regions --only $Region --config $Config
    } else {
        & $Python -m d4bot calibrate $Calibrate --config $Config
    }
    exit $LASTEXITCODE
}

$Arguments = @("-m", "d4bot", "run", "--config", $Config)
if ($Debug) { $Arguments += "--debug" }
if ($EnableInput) { $Arguments += "--enable-input" }
if ($IAcceptBanRisk) { $Arguments += "--i-understand-ban-risk" }
if ($StartHuman -and $StartDemon) { throw "Use only one of -StartHuman / -StartDemon." }
if ($StartHuman) { $Arguments += @("--start", "human") }
if ($StartDemon) { $Arguments += @("--start", "demon") }
if ($MaxSeconds -gt 0) { $Arguments += @("--max-seconds", "$MaxSeconds") }
if ($Snapshots) { $Arguments += @("--snapshots", $Snapshots) }

& $Python @Arguments
exit $LASTEXITCODE
