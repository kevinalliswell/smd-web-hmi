param([string]$OutputDir = 'artifacts')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Set-Location $Repo
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE)" }
}
$Version = (& python scripts/release/metadata.py).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Source versions differ' }
$Lock = Get-Content desktop/webview2.lock.json -Raw | ConvertFrom-Json
if ($Lock.sha256 -notmatch '^[0-9a-f]{64}$' -or $Lock.url -notlike 'https://msedge.sf.dl.delivery.mp.microsoft.com/*') { throw 'Missing approved Fixed WebView2 hash/URL' }
$Stage = Join-Path $Repo "$OutputDir/payload"
if (Test-Path $Stage) { throw 'Output payload already exists; use a fresh output directory' }
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
$Cab = Join-Path $Repo "$OutputDir/webview2.cab"
Invoke-WebRequest -Uri $Lock.url -OutFile $Cab
if ((Get-FileHash $Cab -Algorithm SHA256).Hash.ToLower() -ne $Lock.sha256) { throw 'Fixed WebView2 archive checksum mismatch' }
$Extract = Join-Path $Repo "$OutputDir/webview2-extract"
New-Item -ItemType Directory $Extract | Out-Null
Invoke-Checked 'expand.exe' @($Cab,'-F:*',$Extract)
$RuntimeExe = @(Get-ChildItem $Extract -Recurse -Filter msedgewebview2.exe)
if ($RuntimeExe.Count -ne 1) { throw 'Archive must contain exactly one Fixed Runtime' }
$Signature = Get-AuthenticodeSignature $RuntimeExe[0].FullName
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Runtime Microsoft signature invalid' }
if ($RuntimeExe[0].VersionInfo.ProductVersion -ne $Lock.version) { throw 'Fixed Runtime product version differs from lock' }
Copy-Item $RuntimeExe[0].Directory.FullName (Join-Path $Stage 'webview2') -Recurse
Invoke-Checked 'python' @('scripts/release/freeze.py','--output',$Stage,'--work',"$OutputDir/freeze-work")
Copy-Item frontend/dist (Join-Path $Stage 'frontend') -Recurse
Copy-Item deploy/windows/configure-acl.ps1 $Stage
Copy-Item deploy/windows/configure-recovery.ps1 $Stage
# Exercise the frozen binaries with a scratch DB; migrations never start HostComm.
$Smoke = Join-Path $Repo "$OutputDir/smoke"
New-Item -ItemType Directory (Join-Path $Smoke 'config') -Force | Out-Null
$env:SMD_DATA_ROOT = $Smoke
$Db = (Join-Path $Smoke 'smd.db').Replace('\','/')
@("SMD_DB_PATH=$Db",'HOSTCOMM_MOCK=true','SMD_JWT_SECRET=ci-only-secret-at-least-thirty-two-bytes') | Set-Content (Join-Path $Smoke 'config/service.env') -Encoding utf8
try {
    Invoke-Checked (Join-Path $Stage 'SmdService/SmdService.exe') @('--migrate')
    Invoke-Checked (Join-Path $Stage 'SmdService/SmdService.exe') @('--self-check')
    Invoke-Checked (Join-Path $Stage 'SmdUpdate/SmdUpdate.exe') @('--self-check')
    Invoke-Checked (Join-Path $Stage 'SmdDesktop/SmdDesktop.exe') @('--self-check')
    if (-not (Test-Path (Join-Path $Smoke 'smd.db'))) { throw 'Frozen migration did not create configured DB' }
} finally { Remove-Item Env:SMD_DATA_ROOT -ErrorAction SilentlyContinue }
Invoke-Checked 'python' @('scripts/release/metadata.py','--bundle',$Stage)
$MakeNsis = Join-Path ${env:ProgramFiles(x86)} 'NSIS/makensis.exe'
Invoke-Checked $MakeNsis @('/V3',"/DVERSION=$Version","/DPAYLOAD=$Stage","/DOUTPUT=$Repo/$OutputDir/SmdHmi-$Version-windows-x64.exe",'deploy/windows/installer.nsi')
Copy-Item "$Stage/manifest.json" "$OutputDir/manifest.json"
Copy-Item "$Stage/sbom.cdx.json" "$OutputDir/sbom.cdx.json"
$Hash = Get-FileHash "$OutputDir/SmdHmi-$Version-windows-x64.exe" -Algorithm SHA256
"$($Hash.Hash.ToLower())  $([IO.Path]::GetFileName($Hash.Path))" | Set-Content "$OutputDir/SHA256SUMS.txt" -Encoding ascii
