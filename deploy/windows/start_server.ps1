$ErrorActionPreference = "Stop"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $InstallDir "logs"
$BackendDir = Join-Path $InstallDir "app\backend"
$Python = Join-Path $InstallDir "venv\Scripts\python.exe"
$LogFile = Join-Path $LogDir "server.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -ge 20MB) {
    $Archive = Join-Path $LogDir ("server-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    Move-Item $LogFile $Archive
    Get-ChildItem $LogDir -Filter "server-*.log" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 5 |
        Remove-Item -Force
}
Set-Location $BackendDir
& $Python -m app.server *>> $LogFile
exit $LASTEXITCODE
