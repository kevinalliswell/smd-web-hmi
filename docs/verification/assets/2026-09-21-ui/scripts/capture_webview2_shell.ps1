# PR #84 第 7 项：零安装隔离验证——运行被测安装器解包出的 SmdDesktop.exe（随包 Fixed
# WebView2 152.0.4191.62），窗口截图供与浏览器入口对比。
#
# 不安装服务、不写注册表、不读取已注册安装路径（显式 SMD_DATA_ROOT 绕过 registered_paths），
# WebView2 用户资料写到隔离目录（覆盖 LOCALAPPDATA）。用完整个临时根目录可直接删除。
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ShellExe,
  [Parameter(Mandatory = $true)][string]$DataRoot,
  [Parameter(Mandatory = $true)][string]$LocalAppData,
  [Parameter(Mandatory = $true)][string]$OutDir,
  [string]$Tag = 'shell',
  [int]$WaitSeconds = 25,
  [string]$SendKeysAfterLoad = '',
  [int]$PostKeysWaitSeconds = 6,
  # 默认保留窗口自身尺寸：强行按物理像素 MoveWindow 会让 WebView2 内容按原逻辑尺寸排版后被裁切
  [switch]$ForceWindowSize,
  # 主题切换按钮没有键盘快捷键，用屏幕物理坐标点一次（相对窗口左上角）
  [string]$ClickAt = '',
  [int]$PostClickWaitSeconds = 4,
  # 壳内登录：先点用户名输入框确保焦点，再分段发送按键（一次性 SendWait 会丢字符）
  [string]$LoginClickAt = '',
  [string]$LoginUser = '',
  [string]$LoginPassword = '',
  [int]$PostLoginWaitSeconds = 10
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class SmdWin {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool repaint);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@

# 本机 200% 缩放：截图进程必须 DPI 感知，否则 GetWindowRect/CopyFromScreen 拿到的是
# 虚拟化后的逻辑坐标，会把窗口外的桌面一起截进来。
[void][SmdWin]::SetProcessDPIAware()
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ShellExe
$psi.UseShellExecute = $false
$psi.EnvironmentVariables['SMD_DATA_ROOT'] = $DataRoot
$psi.EnvironmentVariables['LOCALAPPDATA'] = $LocalAppData
$proc = [System.Diagnostics.Process]::Start($psi)

try {
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $proc.Refresh()
    if ($proc.HasExited) { throw "shell exited early with code $($proc.ExitCode)" }
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) { break }
  }
  if ($proc.MainWindowHandle -eq [IntPtr]::Zero) { throw 'shell window did not appear' }
  $h = $proc.MainWindowHandle
  [void][SmdWin]::ShowWindow($h, 9)          # SW_RESTORE
  if ($ForceWindowSize) { [void][SmdWin]::MoveWindow($h, 0, 0, 1440, 900, $true) }
  else {
    # 保留窗口自身尺寸，只挪到屏幕左上角，避免窗口越界导致截图带上桌面
    $r0 = New-Object SmdWin+RECT
    [void][SmdWin]::GetWindowRect($h, [ref]$r0)
    [void][SmdWin]::MoveWindow($h, 0, 0, ($r0.Right - $r0.Left), ($r0.Bottom - $r0.Top), $true)
  }
  [void][SmdWin]::SetForegroundWindow($h)
  Start-Sleep -Seconds 4

  if ($LoginUser) {
    if ($LoginClickAt) {
      $lxy = $LoginClickAt.Split(',')
      [void][SmdWin]::SetCursorPos([int]$lxy[0], [int]$lxy[1])
      Start-Sleep -Milliseconds 400
      [SmdWin]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
      [SmdWin]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
      Start-Sleep -Milliseconds 800
    }
    # 本机默认中文输入法：逐字符 SendKeys 会被 IME 转换（实测 "maint1" -> "面条"），
    # 改用剪贴板粘贴绕开 IME；结束后恢复用户原有剪贴板文本。
    $clipBackup = $null
    try { $clipBackup = Get-Clipboard -Raw -ErrorAction SilentlyContinue } catch { }
    Set-Clipboard -Value $LoginUser
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.SendKeys]::SendWait('^a')
    [System.Windows.Forms.SendKeys]::SendWait('^v')
    Start-Sleep -Milliseconds 700
    [System.Windows.Forms.SendKeys]::SendWait('{TAB}')
    Start-Sleep -Milliseconds 600
    Set-Clipboard -Value $LoginPassword
    Start-Sleep -Milliseconds 400
    [System.Windows.Forms.SendKeys]::SendWait('^a')
    [System.Windows.Forms.SendKeys]::SendWait('^v')
    Start-Sleep -Milliseconds 700
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    if ($clipBackup) { Set-Clipboard -Value $clipBackup } else { Set-Clipboard -Value ' ' }
    Start-Sleep -Seconds $PostLoginWaitSeconds
    [void][SmdWin]::SetForegroundWindow($h)
    Start-Sleep -Seconds 1
  }

  if ($SendKeysAfterLoad) {
    [System.Windows.Forms.SendKeys]::SendWait($SendKeysAfterLoad)
    Start-Sleep -Seconds $PostKeysWaitSeconds
    [void][SmdWin]::SetForegroundWindow($h)
    Start-Sleep -Seconds 1
  }

  if ($ClickAt) {
    $xy = $ClickAt.Split(',')
    [void][SmdWin]::SetCursorPos([int]$xy[0], [int]$xy[1])
    Start-Sleep -Milliseconds 400
    [SmdWin]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # LEFTDOWN
    [SmdWin]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # LEFTUP
    Start-Sleep -Seconds $PostClickWaitSeconds
  }

  # 把光标挪开，避免图表 hover tooltip 进入截图
  [void][SmdWin]::SetCursorPos(20, 1780)
  Start-Sleep -Seconds 2

  $r = New-Object SmdWin+RECT
  [void][SmdWin]::GetWindowRect($h, [ref]$r)
  $w = $r.Right - $r.Left
  $t = $r.Bottom - $r.Top
  $bmp = New-Object System.Drawing.Bitmap $w, $t
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
  $path = Join-Path $OutDir ("item7-{0}.png" -f $Tag)
  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()

  [pscustomobject]@{
    tag        = $Tag
    shell_exe  = $ShellExe
    pid        = $proc.Id
    window_rect = "$($r.Left),$($r.Top),$($r.Right),$($r.Bottom)"
    capture_px = "${w}x${t}"
    screenshot = $path
  } | ConvertTo-Json -Compress
}
finally {
  if (-not $proc.HasExited) {
    $proc.CloseMainWindow() | Out-Null
    Start-Sleep -Seconds 3
    if (-not $proc.HasExited) { $proc.Kill() }
  }
}
