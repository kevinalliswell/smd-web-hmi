param([Parameter(Mandatory=$true)][string]$DataDir, [Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
function Invoke-Icacls([string[]]$Arguments) {
    & icacls.exe @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'ACL configuration failed' }
}
# SID literals avoid localized Administrators/Users names; service SID is resolved after registration.
Invoke-Icacls @($InstallDir, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-32-545:(OI)(CI)RX')
Invoke-Icacls @($DataDir, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', 'NT SERVICE\SmdHmi:(OI)(CI)M', '*S-1-5-32-545:RX')
foreach ($Restricted in @('config', 'updates')) {
    Invoke-Icacls @((Join-Path $DataDir $Restricted), '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', 'NT SERVICE\SmdHmi:(OI)(CI)RX')
}
if (Test-Path (Join-Path $DataDir 'client.json')) {
    Invoke-Icacls @((Join-Path $DataDir 'client.json'), '/grant:r', '*S-1-5-32-545:R')
}
