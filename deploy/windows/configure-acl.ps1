param([Parameter(Mandatory=$true)][string]$DataDir, [Parameter(Mandatory=$true)][string]$InstallDir)
$ErrorActionPreference = 'Stop'
# PS7 -> Python/installer -> Windows PS5 inherits PS7 module paths unchanged.
# Restrict even direct -File invocation to the modules shipped with this host.
$env:PSModulePath = $PSHOME + '\Modules'
Import-Module ($PSHOME + '\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
function Invoke-Icacls([string[]]$Arguments) {
    & icacls.exe @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'ACL configuration failed' }
}
function Grant-WebView2RuntimeAccess([string]$InstallDir) {
    # Windows 10 Fixed Runtime >=120 requires both AppContainer package SIDs.
    # https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
    $PackageSids = @('S-1-15-2-1', 'S-1-15-2-2')
    $Versions = Join-Path $InstallDir 'versions'
    $Runtimes = @(Get-ChildItem -LiteralPath $Versions -Directory | Where-Object {
        -not $_.Name.StartsWith('.') -and (Test-Path -LiteralPath (Join-Path $_.FullName 'webview2'))
    } | ForEach-Object { Get-Item -LiteralPath (Join-Path $_.FullName 'webview2') })
    if (-not $Runtimes.Count) { throw 'No installed Fixed WebView2 runtime found' }
    $Checked = @{}
    foreach ($Runtime in $Runtimes) {
        # Never override an explicit deny or change ancestors outside the installation.
        $Ancestor = $Runtime
        while ($null -ne $Ancestor) {
            if (-not $Checked.ContainsKey($Ancestor.FullName)) {
                $Rules = (Get-Acl -LiteralPath $Ancestor.FullName).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
                foreach ($Rule in $Rules) {
                    $Applies = $Rule.IdentityReference.Value -in ($PackageSids + @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545'))
                    $InheritOnly = ($Rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0
                    if ($Applies -and -not $InheritOnly -and $Rule.AccessControlType -eq 'Deny' -and
                        ($Rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::Traverse)) {
                        throw "Fixed WebView2 ancestor denies AppContainer traverse: $($Ancestor.FullName)"
                    }
                }
                $Checked[$Ancestor.FullName] = $true
            }
            $Ancestor = $Ancestor.Parent
        }
    }
    # Only traverse on installation ancestors, with no inheritance to application files.
    foreach ($Directory in @($InstallDir, $Versions)) {
        Invoke-Icacls @($Directory, '/grant:r', '*S-1-15-2-1:(X)', '*S-1-15-2-2:(X)')
    }
    foreach ($Runtime in $Runtimes) {
        Invoke-Icacls @($Runtime.Parent.FullName, '/grant:r', '*S-1-15-2-1:(X)', '*S-1-15-2-2:(X)')
        Invoke-Icacls @($Runtime.FullName, '/grant:r', '*S-1-15-2-1:(OI)(CI)RX', '*S-1-15-2-2:(OI)(CI)RX')
    }
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
Grant-WebView2RuntimeAccess -InstallDir $InstallDir
