param([string]$InstallDir = (Split-Path -Parent $MyInvocation.MyCommand.Path))

$ErrorActionPreference = "Stop"
$TaskName = "smd-web-hmi"
$StartScript = Join-Path $InstallDir "start_server.ps1"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $InstallDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -StartWhenAvailable
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "smd-web-hmi production server" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered scheduled task: $TaskName"
