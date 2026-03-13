$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "NoGameTracker"
$builtExe = Join-Path $projectDir "dist\NoGameTrackerAgent\NoGameTrackerAgent.exe"

if (Test-Path $builtExe) {
    $action = New-ScheduledTaskAction -Execute $builtExe
}
else {
    $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pythonw) {
        throw "No built agent exe found and pythonw.exe is not in PATH. Build the exe or install Python with PATH integration."
    }

    $scriptPath = Join-Path $projectDir "agent.py"
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$scriptPath`""
}

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Runs No Game Tracker background agent at Windows logon." -Force

Write-Host "Scheduled task '$taskName' created."
