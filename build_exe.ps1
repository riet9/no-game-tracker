$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
}

if (-not $py) {
    throw "Python launcher not found. Install Python and make sure py.exe or python.exe is in PATH."
}

Set-Location $projectDir

& $py -m pip install -r requirements.txt
& $py -m pip install -r requirements-build.txt

$iconPath = Join-Path $projectDir "assets\\app_icon.ico"
$iconArgs = @()
if (Test-Path $iconPath) {
    $iconArgs = @("--icon", $iconPath)
}

& $py -m PyInstaller --noconfirm --clean --windowed --name NoGameTrackerUI @iconArgs app.py
& $py -m PyInstaller --noconfirm --clean --windowed --name NoGameTrackerAgent @iconArgs agent.py

Write-Host "Build complete. See dist\\NoGameTrackerUI and dist\\NoGameTrackerAgent"
