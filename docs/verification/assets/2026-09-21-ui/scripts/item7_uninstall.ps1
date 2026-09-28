# PR #84 第 7 项收尾：卸载本轮验收安装并核查残留。
#
# 必须以管理员运行。-RemoveDataDir 会连同本轮验收产生的 ProgramData 数据目录一并删除
# （本轮是空库首次安装，里面只有验收过程产生的数据）；不给该开关则保留并只做记录。
#
# 本文件必须以 UTF-8 with BOM 保存（本机 ANSI 代码页是 gb2312）。
[CmdletBinding()]
param(
  [string]$InstallDir = 'C:\Program Files\熔滴炉上位机',
  [string]$DataDir    = 'C:\ProgramData\SmdHmi',
  [string]$OutFile    = 'C:\Users\kevin\.cache\smd-pr84-item7\uninstall-result.json',
  [switch]$RemoveDataDir
)

$ErrorActionPreference = 'Stop'
$result = [ordered]@{}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
      ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'must run elevated'
}

$uninstaller = Join-Path $InstallDir 'Uninstall.exe'
$result['before'] = [ordered]@{
  install_dir      = Test-Path $InstallDir
  uninstaller      = Test-Path $uninstaller
  service          = [bool](Get-Service -Name 'SmdHmi' -ErrorAction SilentlyContinue)
  registry         = Test-Path 'HKLM:\SOFTWARE\SmdHmi'
  data_dir         = Test-Path $DataDir
  scheduled_task   = [int]((Get-ScheduledTask -TaskName 'SmdHmi*' -ErrorAction SilentlyContinue | Measure-Object).Count)
  start_menu       = [int]((Get-ChildItem 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs' -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*SMD*' } | Measure-Object).Count)
  port_8000_listen = [int]((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count)
}
if (-not (Test-Path $uninstaller)) { throw "uninstaller not found: $uninstaller" }

# 桌面壳属于普通用户程序，卸载前先确保没有残留窗口占用版本目录
Get-Process SmdDesktop -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $uninstaller -ArgumentList '/S' -PassThru
$proc | Wait-Process -Timeout 600
$sw.Stop()
$result['uninstall'] = [ordered]@{
  exit_code = $proc.ExitCode
  seconds   = [math]::Round($sw.Elapsed.TotalSeconds, 1)
}
Start-Sleep -Seconds 5

$result['after'] = [ordered]@{
  install_dir      = Test-Path $InstallDir
  service          = [bool](Get-Service -Name 'SmdHmi' -ErrorAction SilentlyContinue)
  registry         = Test-Path 'HKLM:\SOFTWARE\SmdHmi'
  data_dir         = Test-Path $DataDir
  scheduled_task   = [int]((Get-ScheduledTask -TaskName 'SmdHmi*' -ErrorAction SilentlyContinue | Measure-Object).Count)
  start_menu       = [int]((Get-ChildItem 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs' -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*SMD*' } | Measure-Object).Count)
  port_8000_listen = [int]((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count)
  install_dir_leftovers = if (Test-Path $InstallDir) {
      @(Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
    } else { @() }
}

if ($RemoveDataDir -and (Test-Path $DataDir)) {
  # 本轮是空库首次安装，数据目录里只有验收过程产生的内容
  $result['data_dir_contents_before_removal'] =
    @(Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
  takeown /F $DataDir /R /D Y  | Out-Null
  icacls $DataDir /grant "$env:USERNAME:(F)" /T /C | Out-Null
  Remove-Item -Recurse -Force $DataDir -ErrorAction SilentlyContinue
  $result['data_dir_removed'] = -not (Test-Path $DataDir)
}

$result['final'] = [ordered]@{
  install_dir = Test-Path $InstallDir
  data_dir    = Test-Path $DataDir
  legacy_dir  = Test-Path 'C:\Program Files\SmdHmi'
  free_gb     = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
}
$result['timestamp'] = (Get-Date).ToUniversalTime().ToString('o')
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
$result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8
Write-Output "OK -> $OutFile"
