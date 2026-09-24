# PR #84 第 7 项：用被测安装器在中文安装目录做一次真实首次安装，并采集安装后事实。
#
# 必须以管理员运行（NSIS 安装器、SCM 注册与 ProgramData ACL 都需要提权）。
# 本脚本只做"前置检查 → 安装 → 采集 → 初始口令换成验收口令"，不做卸载。
# 一次性初始口令只在本进程内使用，不写入任何输出文件。
#
# 注意：本文件必须以 UTF-8 with BOM 保存。本机 ANSI 代码页是 gb2312，
# 无 BOM 的 UTF-8 会被 PowerShell 5.1 按 GBK 误读，中文路径会变成乱码。
[CmdletBinding()]
param(
  [string]$Installer   = 'C:\Users\kevin\AppData\Local\Temp\smd-pr84-artifact\windows-local-build\SmdHmi-0.3.0-windows-x64.exe',
  [string]$InstallDir  = 'C:\Program Files\熔滴炉上位机',
  [string]$OutFile     = 'C:\Users\kevin\.cache\smd-pr84-item7\install-result.json',
  [string]$NewPassword = 'Pr84Item7#2026',
  [int]$ReadyTimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$result = [ordered]@{}

function Fail([string]$stage, [string]$message) {
  $result['stage'] = $stage
  $result['error'] = $message
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
  $result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8
  Write-Output ("FAILED at {0}: {1}" -f $stage, $message)
  exit 1
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
      ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Fail 'precheck' 'must run elevated'
}
if (-not (Test-Path $Installer)) { Fail 'precheck' "installer not found: $Installer" }

# ---- 1. 前置：机器上不能有既有安装 ----
$pre = [ordered]@{
  service        = [bool](Get-Service -Name 'SmdHmi' -ErrorAction SilentlyContinue)
  program_data   = Test-Path 'C:\ProgramData\SmdHmi'
  registry       = Test-Path 'HKLM:\SOFTWARE\SmdHmi'
  scheduled_task = [int]((Get-ScheduledTask -TaskName 'SmdHmi*' -ErrorAction SilentlyContinue | Measure-Object).Count)
  port_8000      = [int]((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count)
  install_dir    = Test-Path $InstallDir
  legacy_dir     = Test-Path 'C:\Program Files\SmdHmi'
}
$result['precheck'] = $pre
if ($pre.service -or $pre.program_data -or $pre.registry -or $pre.scheduled_task -gt 0 -or
    $pre.port_8000 -gt 0 -or $pre.install_dir) {
  Fail 'precheck' 'machine is not clean; refusing to install over an existing installation'
}

$result['installer'] = [ordered]@{
  path   = $Installer
  bytes  = (Get-Item $Installer).Length
  sha256 = (Get-FileHash -Algorithm SHA256 -Path $Installer).Hash.ToLower()
}
$result['install_dir'] = $InstallDir

# ---- 2. 静默安装到中文目录（NSIS 的 /D 必须放最后且不加引号）----
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $Installer -ArgumentList "/S /D=$InstallDir" -PassThru
$proc | Wait-Process -Timeout 900
$sw.Stop()
$result['install'] = [ordered]@{
  exit_code   = $proc.ExitCode
  seconds     = [math]::Round($sw.Elapsed.TotalSeconds, 1)
}
if ($proc.ExitCode -ne 0) { Fail 'install' "installer exit code $($proc.ExitCode)" }

# ---- 3. 安装后事实 ----
$svc = Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -ErrorAction SilentlyContinue
if (-not $svc) { Fail 'post-install' 'SmdHmi service not registered' }
$result['service'] = [ordered]@{
  name       = $svc.Name
  state      = $svc.State
  start_name = $svc.StartName
  path       = $svc.PathName
  path_under_install_dir = $svc.PathName -like "*$InstallDir*"
}

$reg = Get-ItemProperty 'HKLM:\SOFTWARE\SmdHmi' -ErrorAction SilentlyContinue
$result['registry'] = [ordered]@{
  present     = [bool]$reg
  install_dir = $reg.InstallDir
  data_dir    = $reg.DataDir
  version     = $reg.Version
}

$versionDir = Join-Path $InstallDir ('versions\' + $reg.Version)
$manifestPath = Join-Path $versionDir 'manifest.json'
if (Test-Path $manifestPath) {
  $m = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $result['manifest'] = [ordered]@{
    version          = $m.version
    commit           = $m.commit
    prerelease       = $m.prerelease
    webview2_version = $m.webview2_version
    file_count       = $m.files.Count
  }
  $result['version_dir'] = [ordered]@{
    path     = $versionDir
    entries  = (Get-ChildItem $versionDir | Select-Object -ExpandProperty Name) -join ','
    size_mb  = [math]::Round(((Get-ChildItem $versionDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
  }
} else {
  $result['manifest'] = @{ missing = $manifestPath }
}

$result['start_menu'] = @((Get-ChildItem 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs' `
  -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -like '*SMD*' } | Select-Object -ExpandProperty Name))

$clientPath = 'C:\ProgramData\SmdHmi\config\client.json'
if (-not (Test-Path $clientPath)) { $clientPath = 'C:\ProgramData\SmdHmi\client.json' }
$baseUrl = 'http://127.0.0.1:8000'
if (Test-Path $clientPath) {
  $baseUrl = (Get-Content $clientPath -Raw -Encoding UTF8 | ConvertFrom-Json).url.TrimEnd('/')
}
$result['client_url'] = $baseUrl

# ---- 4. 等待就绪并采集健康检查 ----
$deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
$health = $null
while ((Get-Date) -lt $deadline) {
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/system/health" -TimeoutSec 8
    $health = ($resp.Content | ConvertFrom-Json).data
    if ($health.status -eq 'ready') { break }
  } catch { }
  Start-Sleep -Seconds 3
}
if (-not $health) { Fail 'health' 'backend did not become reachable' }
$result['health'] = [ordered]@{
  status   = $health.status
  version  = $health.version
  database = $health.checks.database
  schema   = $health.checks.schema
  storage  = $health.checks.storage
  backup   = $health.checks.backup
  hostcomm = $health.checks.hostcomm
  storage_free_bytes = $health.checks.storage_free_bytes
}

# 静态页与它引用的实际 JS 资源都必须可取（避免把 SPA fallback HTML 当成功）
$index = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/login" -TimeoutSec 10
$asset = [regex]::Match($index.Content, '/assets/[A-Za-z0-9_\-\.]+\.js').Value
$assetResp = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl$asset" -TimeoutSec 10
$result['static'] = [ordered]@{
  login_status   = $index.StatusCode
  asset_path     = $asset
  asset_status   = $assetResp.StatusCode
  asset_is_js    = $assetResp.Headers['Content-Type'] -like '*javascript*'
  asset_bytes    = $assetResp.RawContentLength
}

# ---- 5. 用安装器生成的一次性口令登录并立即改成验收口令（口令不进输出）----
$bootstrapFile = 'C:\ProgramData\SmdHmi\config\bootstrap-admin-password.txt'
$result['bootstrap_password_file'] = [ordered]@{
  path    = $bootstrapFile
  present = Test-Path $bootstrapFile
}
if (Test-Path $bootstrapFile) {
  $boot = (Get-Content $bootstrapFile -Raw -Encoding UTF8).Trim()
  $login = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/auth/login" -Method Post `
    -ContentType 'application/json' -TimeoutSec 15 `
    -Body (@{ username = 'admin'; password = $boot } | ConvertTo-Json -Compress)
  $loginData = ($login.Content | ConvertFrom-Json).data
  $result['first_login'] = [ordered]@{
    ok                   = $true
    role                 = $loginData.role
    must_change_password = $loginData.must_change_password
  }
  Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/users/change-password" -Method Post `
    -Headers @{ Authorization = "Bearer $($loginData.token)" } `
    -ContentType 'application/json' -TimeoutSec 15 `
    -Body (@{ old_password = $boot; new_password = $NewPassword } | ConvertTo-Json -Compress) | Out-Null
  $boot = $null
  $relogin = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/auth/login" -Method Post `
    -ContentType 'application/json' -TimeoutSec 15 `
    -Body (@{ username = 'admin'; password = $NewPassword } | ConvertTo-Json -Compress)
  $result['password_changed'] = [ordered]@{
    ok                   = $true
    must_change_password = (($relogin.Content | ConvertFrom-Json).data).must_change_password
  }
}

# ---- 6. 新库应当是空的（首次安装路径）----
try {
  $token = ((Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/auth/login" -Method Post `
    -ContentType 'application/json' -TimeoutSec 15 `
    -Body (@{ username = 'admin'; password = $NewPassword } | ConvertTo-Json -Compress)
    ).Content | ConvertFrom-Json).data.token
  $tests = ((Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/tests?page=1&size=20" `
    -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15).Content | ConvertFrom-Json).data
  $users = ((Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/users" `
    -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15).Content | ConvertFrom-Json).data
  $result['fresh_database'] = [ordered]@{
    test_count = @($tests).Count
    user_names = @($users | Select-Object -ExpandProperty username)
  }
} catch {
  $result['fresh_database'] = @{ error = $_.Exception.Message }
}

$result['stage'] = 'completed'
$result['timestamp'] = (Get-Date).ToUniversalTime().ToString('o')
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
$result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8
Write-Output "OK -> $OutFile"
