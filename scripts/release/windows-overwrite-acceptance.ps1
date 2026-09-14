#requires -Version 7.0
param(
    [Parameter(Mandatory=$true)][string]$CurrentInstaller,
    [Parameter(Mandatory=$true)][string]$Rc4Installer,
    [Parameter(Mandatory=$true)][string]$Rc5Installer,
    [Parameter(Mandatory=$true)][string]$EvidenceDir
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Overwrite acceptance requires a disposable GitHub-hosted Windows runner'
}
# Python is a CI coordinator dependency, not a requirement of the actual installed product.
# The coordinator creates each actual NSIS process suspended and attaches a kill-on-close
# job before running it; secrets stay in memory or ACL-protected owned installation files.
& python (Join-Path $PSScriptRoot 'windows_overwrite_acceptance.py') `
    --current-installer $CurrentInstaller --rc4-installer $Rc4Installer `
    --rc5-installer $Rc5Installer --evidence-dir $EvidenceDir
if ($LASTEXITCODE -ne 0) { throw 'Actual Windows overwrite acceptance failed; inspect only the sanitized evidence directory' }
