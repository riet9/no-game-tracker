$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "No Game Tracker.lnk"
$uiExe = Join-Path $projectDir "dist\NoGameTrackerUI\NoGameTrackerUI.exe"
$iconPath = Join-Path $projectDir "assets\app_icon.ico"

if (-not (Test-Path $uiExe)) {
    throw "UI exe not found. Build the project first with .\build_exe.ps1"
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $uiExe
$shortcut.WorkingDirectory = Split-Path $uiExe -Parent
if (Test-Path $iconPath) {
    $shortcut.IconLocation = $iconPath
}
$shortcut.Save()

Write-Host "Desktop shortcut created: $shortcutPath"
