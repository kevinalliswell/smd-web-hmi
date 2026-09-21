# 用与桌面壳完全相同的采集路径（DPI 感知 + GDI CopyFromScreen）抓取一个已存在的窗口。
# 目的：让"浏览器入口"的截图也经过同一条显示管线，取色比对才是同口径。
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$TitleLike,
  [Parameter(Mandatory = $true)][string]$OutFile,
  [int]$WaitSeconds = 20
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class SmdCap {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool repaint);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
[void][SmdCap]::SetProcessDPIAware()

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$target = $null
while ((Get-Date) -lt $deadline) {
  $target = Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like $TitleLike
  } | Select-Object -First 1
  if ($target) { break }
  Start-Sleep -Milliseconds 700
}
if (-not $target) { throw "no window matching '$TitleLike'" }

$h = $target.MainWindowHandle
[void][SmdCap]::ShowWindow($h, 9)
$r0 = New-Object SmdCap+RECT
[void][SmdCap]::GetWindowRect($h, [ref]$r0)
[void][SmdCap]::MoveWindow($h, 0, 0, ($r0.Right - $r0.Left), ($r0.Bottom - $r0.Top), $true)
[void][SmdCap]::SetForegroundWindow($h)
Start-Sleep -Seconds 3
[void][SmdCap]::SetCursorPos(20, 1780)
Start-Sleep -Seconds 2

$r = New-Object SmdCap+RECT
[void][SmdCap]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left
$t = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap $w, $t
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($OutFile, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

[pscustomobject]@{
  title       = $target.MainWindowTitle
  process     = $target.ProcessName
  window_rect = "$($r.Left),$($r.Top),$($r.Right),$($r.Bottom)"
  capture_px  = "${w}x${t}"
  screenshot  = $OutFile
} | ConvertTo-Json -Compress
