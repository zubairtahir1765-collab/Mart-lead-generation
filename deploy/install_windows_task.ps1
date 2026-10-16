<#
  Registers the weekly task in Windows Task Scheduler.

  Run once from PowerShell (no admin needed):
      powershell -ExecutionPolicy Bypass -File deploy\install_windows_task.ps1
      powershell -ExecutionPolicy Bypass -File deploy\install_windows_task.ps1 -Day Tuesday -Time 07:30

  Remove it:   Unregister-ScheduledTask -TaskName MartLeadsWeekly -Confirm:$false
  Test it now: Start-ScheduledTask -TaskName MartLeadsWeekly      (then look at logs\last_run.json)

  -StartWhenAvailable: if the PC was off at the scheduled time, the task runs as soon as it is back on.
  The task runs as you, while you are logged in. (To run it when logged out, open the task in the Task Scheduler
  app > Properties > "Run whether user is logged on or not" and enter your Windows password.)
#>
param(
    [string]$Day = "Monday",
    [string]$Time = "09:00",
    [string]$TaskName = "MartLeadsWeekly"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "run_weekly.bat"

$action   = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $project
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Weekly mart lead generation (run_weekly.py)" -Force | Out-Null

Write-Host "Task '$TaskName' registered: every $Day at $Time."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Then check:        $project\logs\last_run.json"
