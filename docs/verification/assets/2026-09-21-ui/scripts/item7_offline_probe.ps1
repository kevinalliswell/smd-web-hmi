# PR #84 第 7 项：断网离线探针。
#
# 打开已安装的 WebView2 桌面壳并登录，然后按固定间隔记录：本机网络连通性、后台健康检查、
# 静态页与其引用的 JS 资源、以及桌面壳窗口截图。操作员在运行期间把网络断开再恢复，
# 事后用日志里 internet=False 的那些采样点判断离线期间上位机是否仍然可用。
#
# 不需要管理员：桌面壳是普通用户程序，后台走 127.0.0.1 明文 HTTP。
# 本文件必须以 UTF-8 with BOM 保存（本机 ANSI 代码页是 gb2312）。
[CmdletBinding()]
param(
  [string]$ShellExe = 'C:\Program Files\熔滴炉上位机\versions\0.3.0\SmdDesktop\SmdDesktop.exe',
  [string]$BaseUrl  = 'http://127.0.0.1:8000',
  [string]$OutDir   = 'C:\Users\kevin\.cache\smd-pr84-item7\offline',
  [string]$LoginUser     = 'admin',
  [string]$LoginPassword = 'Pr84Item7#2026',
  [string]$LoginClickAt  = '1440,906',
  [int]$TotalSeconds  = 300,
  [int]$IntervalSeconds = 10
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class SmdProbe {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool repaint);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
[void][SmdProbe]::SetProcessDPIAware()
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$logPath = Join-Path $OutDir 'offline-probe.ndjson'
Remove-Item $logPath -ErrorAction SilentlyContinue

$proc = [System.Diagnostics.Process]::Start($ShellExe)
try {
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $proc.Refresh()
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) { break }
  }
  if ($proc.MainWindowHandle -eq [IntPtr]::Zero) { throw 'shell window did not appear' }
  $h = $proc.MainWindowHandle
  [void][SmdProbe]::ShowWindow($h, 9)
  $r0 = New-Object SmdProbe+RECT
  [void][SmdProbe]::GetWindowRect($h, [ref]$r0)
  [void][SmdProbe]::MoveWindow($h, 0, 0, ($r0.Right - $r0.Left), ($r0.Bottom - $r0.Top), $true)
  [void][SmdProbe]::SetForegroundWindow($h)
  Start-Sleep -Seconds 5

  # 壳内登录（剪贴板粘贴绕开中文输入法），用完恢复原剪贴板
  $xy = $LoginClickAt.Split(',')
  [void][SmdProbe]::SetCursorPos([int]$xy[0], [int]$xy[1])
  Start-Sleep -Milliseconds 400
  [SmdProbe]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  [SmdProbe]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 800
  $clipBackup = $null
  try { $clipBackup = Get-Clipboard -Raw -ErrorAction SilentlyContinue } catch { }
  Set-Clipboard -Value $LoginUser
  Start-Sleep -Milliseconds 400
  [System.Windows.Forms.SendKeys]::SendWait('^a'); [System.Windows.Forms.SendKeys]::SendWait('^v')
  Start-Sleep -Milliseconds 700
  [System.Windows.Forms.SendKeys]::SendWait('{TAB}')
  Start-Sleep -Milliseconds 600
  Set-Clipboard -Value $LoginPassword
  Start-Sleep -Milliseconds 400
  [System.Windows.Forms.SendKeys]::SendWait('^a'); [System.Windows.Forms.SendKeys]::SendWait('^v')
  Start-Sleep -Milliseconds 700
  [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
  if ($clipBackup) { Set-Clipboard -Value $clipBackup } else { Set-Clipboard -Value ' ' }
  Start-Sleep -Seconds 10
  [void][SmdProbe]::SetForegroundWindow($h)
  [void][SmdProbe]::SetCursorPos(20, 1780)
  Start-Sleep -Seconds 2

  $ticks = [math]::Max(1, [math]::Floor($TotalSeconds / $IntervalSeconds))
  for ($i = 0; $i -lt $ticks; $i++) {
    $row = [ordered]@{ tick = $i; ts = (Get-Date).ToUniversalTime().ToString('o') }

    # 本机是否还有对外网络：只看连接配置文件，不发任何外部请求
    $profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
    $row['adapters_up'] = @(Get-NetAdapter -ErrorAction SilentlyContinue |
      Where-Object { $_.Status -eq 'Up' -and -not $_.Virtual }).Count
    $row['internet'] = [bool]($profiles | Where-Object { $_.IPv4Connectivity -eq 'Internet' -or $_.IPv6Connectivity -eq 'Internet' })
    $row['connectivity'] = (($profiles | ForEach-Object { $_.Name + '=' + $_.IPv4Connectivity }) -join ';')

    try {
      $resp = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/api/system/health" -TimeoutSec 6
      $h2 = ($resp.Content | ConvertFrom-Json).data
      $row['health_status']  = $h2.status
      $row['health_version'] = $h2.version
      $row['health_db']      = $h2.checks.database
      $row['health_storage'] = $h2.checks.storage
      $row['health_hostcomm']= $h2.checks.hostcomm
    } catch { $row['health_error'] = $_.Exception.Message }

    try {
      $page = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/login" -TimeoutSec 6
      $row['login_status'] = $page.StatusCode
      $asset = [regex]::Match($page.Content, '/assets/[A-Za-z0-9_\-\.]+\.js').Value
      $row['asset_status'] = (Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl$asset" -TimeoutSec 6).StatusCode
    } catch { $row['page_error'] = $_.Exception.Message }

    $proc.Refresh()
    $row['shell_alive'] = -not $proc.HasExited

    if (-not $proc.HasExited) {
      $r = New-Object SmdProbe+RECT
      [void][SmdProbe]::GetWindowRect($h, [ref]$r)
      $w = $r.Right - $r.Left; $t = $r.Bottom - $r.Top
      if ($w -gt 0 -and $t -gt 0) {
        $bmp = New-Object System.Drawing.Bitmap $w, $t
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
        $shot = Join-Path $OutDir ("shell-tick{0:d3}.png" -f $i)
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        $row['screenshot'] = Split-Path -Leaf $shot
      }
    }

    ($row | ConvertTo-Json -Compress) | Add-Content -Path $logPath -Encoding UTF8
    Start-Sleep -Seconds $IntervalSeconds
  }
}
finally {
  if (-not $proc.HasExited) {
    $proc.CloseMainWindow() | Out-Null
    Start-Sleep -Seconds 3
    if (-not $proc.HasExited) { $proc.Kill() }
  }
}
Write-Output "done -> $logPath"
