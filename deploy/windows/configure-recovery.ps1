param([Parameter(Mandatory=$true)][string]$VersionDir, [Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = $PSHOME + '\Modules'
Import-Module ($PSHOME + '\Modules\ScheduledTasks\ScheduledTasks.psd1') -ErrorAction Stop
$Action = New-ScheduledTaskAction -Execute (Join-Path $VersionDir 'SmdUpdate/SmdUpdate.exe') -Argument ('--recover --non-interactive --install "' + $InstallDir + '"')
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'SmdHmi-Recover' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
