#requires -Version 7.0
param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$Evidence,
    [ValidateRange(30,900)][int]$InstallTimeoutSeconds = 360
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# No opt-out: this script may remove its own service/data only on an ephemeral hosted runner.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or -not $IsWindows) {
    throw 'This smoke test requires a disposable GitHub-hosted Windows runner'
}
$Principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not [Environment]::Is64BitProcess -or -not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This smoke test requires an elevated Windows x64 PowerShell process'
}
foreach ($Scope in @('Process','User','Machine')) {
    if ([Environment]::GetEnvironmentVariable('SMD_DATA_ROOT', $Scope)) { throw 'SMD_DATA_ROOT override must be absent' }
}

function Test-SamePath([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    return [string]::Equals([IO.Path]::GetFullPath($Left.Trim('"')), [IO.Path]::GetFullPath($Right), [StringComparison]::OrdinalIgnoreCase)
}
function Get-SmokeService { return Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5 }

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Manifest = (Resolve-Path -LiteralPath $Manifest).Path
$Expected = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
if ($Expected.platform -ne 'windows-x64' -or $Expected.schema_version -ne 1 -or
    $Expected.version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$' -or
    $Expected.commit -notmatch '^[0-9a-f]{40}$' -or -not $Expected.compatibility.database_revision) {
    throw 'The smoke test requires the manifest generated with this Windows package'
}
if ([IO.Path]::GetFileName($Installer) -ne "SmdHmi-$($Expected.version)-windows-x64.exe") {
    throw 'Installer filename and manifest version differ'
}
$Evidence = [IO.Path]::GetFullPath($Evidence)
$Identity = [guid]::NewGuid().ToString('N')
$InstallDir = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) "SmdHmi-CI-$Identity"
$DataDir = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'SmdHmi'
if (-not (Test-SamePath $env:PROGRAMDATA ([Environment]::GetFolderPath('CommonApplicationData')))) {
    throw 'PROGRAMDATA must be the actual Windows common data directory'
}
$MenuDir = Join-Path $env:PROGRAMDATA 'Microsoft/Windows/Start Menu/Programs/SMD HMI'
$ProductKey = 'HKLM:\Software\SmdHmi'
$UninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi'
$VersionDir = Join-Path $InstallDir "versions/$($Expected.version)"
$ServiceExe = Join-Path $VersionDir 'SmdService/SmdService.exe'
$UpdaterExe = Join-Path $VersionDir 'SmdUpdate/SmdUpdate.exe'
$DesktopExe = Join-Path $VersionDir 'SmdDesktop/SmdDesktop.exe'
$OwnerFile = ".ci-smoke-$Identity"
$FirewallName = "SmdHmi-CI-$Identity"
if ((Test-SamePath $Evidence $Installer) -or (Test-SamePath $Evidence $Manifest)) { throw 'Evidence cannot overwrite build inputs' }
foreach ($Root in @($DataDir, $InstallDir, $MenuDir)) {
    if ($Evidence.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Evidence must remain outside the test installation' }
}

# These checks happen before creating a directory, rule, task or process.
foreach ($Existing in @($DataDir, $InstallDir, $MenuDir, $ProductKey, $UninstallKey,
    (Join-Path $env:PROGRAMDATA 'SmdHmi-TestBackups'),
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'SmdHmi'))) {
    if (Test-Path -LiteralPath $Existing) { throw "Refusing existing installation object: $Existing" }
}
if (Get-SmokeService) { throw 'Refusing an existing SmdHmi service' }
if (Get-ScheduledTask -TaskName 'SmdHmi-Recover' -ErrorAction SilentlyContinue) { throw 'Refusing an existing recovery task' }
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8000 already has a listener' }
if (@(Get-NetFirewallProfile | Where-Object { -not $_.Enabled }).Count) { throw 'Firewall profiles must already be enabled' }

$CreatedRoots = [Collections.Generic.List[string]]::new()
$CleanupErrors = [Collections.Generic.List[string]]::new()
$InstallProcess = $null
$PairingProcess = $null
$FirewallCreated = $false
$AttemptedInstall = $false
$Passed = $false
$Stage = 'claim_test_directories'
$Result = [ordered]@{
    schema_version = 1
    validation = 'github_hosted_install_smoke_only'
    version = $Expected.version
    commit = $Expected.commit
    expected_database_revision = $Expected.compatibility.database_revision
    installer_sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLower()
    started_at = [DateTime]::UtcNow.ToString('o')
    status = 'failed'
}
function Invoke-SmokeCleanup([string]$Name, [scriptblock]$Action) {
    try { & $Action } catch { $CleanupErrors.Add($Name) }
}
function Remove-OwnedDirectory([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $Marker = Join-Path $Directory $OwnerFile
    if ((Get-Item -LiteralPath $Directory).Attributes -band [IO.FileAttributes]::ReparsePoint -or
        -not (Test-Path -LiteralPath $Marker) -or (Get-Content -LiteralPath $Marker -Raw).Trim() -ne $Identity) {
        throw 'Directory ownership could not be verified'
    }
    if (@(Get-ChildItem -LiteralPath $Directory -Force -Recurse -Attributes ReparsePoint).Count) {
        throw 'Refusing a directory containing reparse points'
    }
    Remove-Item -LiteralPath $Directory -Recurse -Force
}

try {
    foreach ($Directory in @($InstallDir, $DataDir)) {
        New-Item -ItemType Directory -Path $Directory | Out-Null
        $CreatedRoots.Add($Directory)
        Set-Content -LiteralPath (Join-Path $Directory $OwnerFile) -Value $Identity -Encoding ascii
    }
    $Stage = 'block_device_network'
    # All default HostComm endpoints are blocked for this exact new service executable.
    New-NetFirewallRule -Name $FirewallName -DisplayName $FirewallName -Direction Outbound -Action Block `
        -Profile Any -Program $ServiceExe -Protocol TCP -RemotePort 34211 | Out-Null
    $FirewallCreated = $true
    $Stage = 'silent_installer'
    Write-Host 'Install smoke: starting this build with device traffic blocked'
    $AttemptedInstall = $true
    # NSIS requires /D to be last, absolute and unquoted, including when Program Files contains spaces.
    $InstallProcess = Start-Process -FilePath $Installer -ArgumentList "/S /D=$InstallDir" -PassThru
    if (-not $InstallProcess.WaitForExit($InstallTimeoutSeconds * 1000)) { throw 'Installer exceeded its time limit' }
    $Result.installer_exit_code = $InstallProcess.ExitCode
    if ($InstallProcess.ExitCode -ne 0) { throw 'Installer returned a failure exit code' }

    $Stage = 'service_identity'
    $Service = Get-SmokeService
    if (-not $Service -or $Service.State -ne 'Running' -or $Service.StartName -ne 'NT AUTHORITY\LocalService' -or
        -not (Test-SamePath $Service.PathName $ServiceExe)) { throw 'SCM identity/state does not match this installed LocalService' }
    $Result.service_account = $Service.StartName
    $Result.service_running = $true
    $Installed = Get-Content -LiteralPath (Join-Path $VersionDir 'manifest.json') -Raw | ConvertFrom-Json
    if ($Installed.version -ne $Expected.version -or $Installed.commit -ne $Expected.commit -or
        $Installed.compatibility.database_revision -ne $Expected.compatibility.database_revision) { throw 'Installed manifest differs' }

    $Stage = 'http_readiness'
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    $Health = $null
    do {
        try { $Health = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/health' -Method Get -NoProxy -TimeoutSec 3).data }
        catch { $Health = $null }
        if ($Health -and $Health.status -eq 'ready') { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (-not $Health -or $Health.status -ne 'ready' -or $Health.version -ne $Expected.version) { throw 'Application readiness/version failed' }
    foreach ($Check in @('database','schema','storage','backup')) {
        if ($Health.checks.$Check -ne 'ok') { throw 'A required readiness check failed' }
    }
    if ($Health.checks.hostcomm -ne 'offline') { throw 'The smoke runner must never have a connected device' }
    $Result.readiness = @{ status = $Health.status; version = $Health.version; database = $Health.checks.database;
        schema = $Health.checks.schema; storage = $Health.checks.storage; backup = $Health.checks.backup; hostcomm = $Health.checks.hostcomm }

    $Stage = 'static_frontend'
    $Page = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -NoProxy -TimeoutSec 5
    if ($Page.StatusCode -ne 200 -or $Page.Headers['Content-Type'] -notmatch 'text/html' -or $Page.Content -notmatch 'id="app"') { throw 'Static application page missing' }
    $Asset = [regex]::Match($Page.Content, '<script[^>]+src="(/assets/[^"?]+)"').Groups[1].Value
    if (-not $Asset) { throw 'Built application script missing from static page' }
    $Script = Invoke-WebRequest -Uri ("http://127.0.0.1:8000" + $Asset) -NoProxy -TimeoutSec 5
    if ($Script.StatusCode -ne 200 -or $Script.Headers['Content-Type'] -match 'text/html') { throw 'Application script asset was not served' }
    $Result.static_page = 'ok'
    $Result.static_script = 'ok'

    $Stage = 'same_version_repair'
    if (Test-Path -LiteralPath (Join-Path $DataDir 'maintenance.json')) { throw 'Unexpected maintenance ticket before repair' }
    $ConfigurationFile = Join-Path $DataDir 'config/service.env'
    $BeforeConfigurationHash = (Get-FileHash -LiteralPath $ConfigurationFile -Algorithm SHA256).Hash
    $InstallProcess.Dispose()
    $InstallProcess = Start-Process -FilePath $Installer -ArgumentList "/S /D=$InstallDir" -PassThru
    if (-not $InstallProcess.WaitForExit($InstallTimeoutSeconds * 1000)) { throw 'Repair exceeded its time limit' }
    if ($InstallProcess.ExitCode -ne 0) { throw 'Same-build repair must succeed without an application maintenance ticket' }
    $AfterRepairService = Get-SmokeService
    if (-not $AfterRepairService -or $AfterRepairService.State -ne 'Running' -or
        -not (Test-SamePath $AfterRepairService.PathName $ServiceExe)) { throw 'Repair failed to restore the running backend' }
    if ((Get-FileHash -LiteralPath $ConfigurationFile -Algorithm SHA256).Hash -ne $BeforeConfigurationHash -or
        (Test-Path -LiteralPath (Join-Path $DataDir 'maintenance.json'))) { throw 'Repair changed config or left a maintenance lock' }
    $Result.same_version_repair = @{ installer_exit_code = $InstallProcess.ExitCode; configuration_preserved = $true;
        application_login_required = $false; target_version_input_required = $false }

    $Stage = 'test_machine_reset_and_reinstall'
    . (Join-Path $PSScriptRoot 'test-reset-smoke.ps1')
    $Result.test_reset = Invoke-TestResetSmoke -Installer $Installer -InstallDir $InstallDir -DataDir $DataDir `
        -Version $Expected.version -Identity $Identity -OwnerFile $OwnerFile -TimeoutSeconds $InstallTimeoutSeconds

    $Stage = 'offline_pairing'
    $Controller = Get-Service -Name 'SmdHmi'
    try {
        $Controller.Stop()
        $Controller.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    } finally { $Controller.Dispose() }
    $PairDeviceId = [guid]::NewGuid().ToString('N')
    $PairingProcess = Start-Process -FilePath $UpdaterExe -ArgumentList `
        "--install `"$InstallDir`" --pair-device $PairDeviceId --reason `"Isolated CI credential access verification`"" `
        -RedirectStandardOutput (Join-Path $DataDir 'logs/pairing-cli.stdout') `
        -RedirectStandardError (Join-Path $DataDir 'logs/pairing-cli.stderr') -PassThru
    if (-not $PairingProcess.WaitForExit(60000)) { throw 'Offline pairing exceeded its time limit' }
    if ($PairingProcess.ExitCode -ne 0) { throw 'Installed offline pairing command failed' }

    $Stage = 'localservice_pairing_read'
    Start-Service -Name 'SmdHmi'
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    $CredentialRead = $false
    $ServiceLog = Join-Path $DataDir 'logs/service.log'
    do {
        # A fresh random device identity binds this event to this CLI transaction.
        # The event occurs only after production load_psk has verified ownership,
        # DACL and key bytes in the actual service process, before any handshake.
        if (Test-Path -LiteralPath $ServiceLog) {
            $CredentialRead = @(Select-String -LiteralPath $ServiceLog -Encoding utf8 -Pattern 'v2\.tls_credentials_loaded' |
                Where-Object { $_.Line.Contains($PairDeviceId) -and $_.Line.Contains('not_started') }).Count -gt 0
        }
        try { $Health = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/system/health' -Method Get -NoProxy -TimeoutSec 3).data }
        catch { $Health = $null }
        if ($CredentialRead -and $Health -and $Health.status -eq 'ready') { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    $Service = Get-SmokeService
    if (-not $CredentialRead -or -not $Health -or $Health.status -ne 'ready' -or
        $Health.checks.hostcomm -ne 'offline' -or -not $Service -or $Service.State -ne 'Running' -or
        $Service.StartName -ne 'NT AUTHORITY\LocalService' -or -not (Test-SamePath $Service.PathName $ServiceExe)) {
        throw 'LocalService credential read was not verified with device traffic blocked'
    }
    $Result.pairing = @{ cli = 'passed'; credential_read = 'verified_as_LocalService'; device_id = $PairDeviceId;
        hostcomm = 'offline'; tls_handshake = 'not_validated_device_network_blocked'; key_material_in_evidence = $false }
    $Passed = $true
} catch {
    # Do not serialize exception bodies, environment/configuration or authentication material.
    $Result.failure_stage = $Stage
    $Result.failure_type = $_.Exception.GetType().Name
    $Result.failure_line = $_.InvocationInfo.ScriptLineNumber
    $FailureScript = [IO.Path]::GetFileName($_.InvocationInfo.ScriptName)
    if ($FailureScript -in @('smoke-windows.ps1', 'test-reset-smoke.ps1')) { $Result.failure_script = $FailureScript }
} finally {
    if ($PairingProcess) {
        Invoke-SmokeCleanup 'pairing_process' {
            if (-not $PairingProcess.HasExited) { $PairingProcess.Kill($true); [void]$PairingProcess.WaitForExit(10000) }
            $PairingProcess.Dispose()
        }
    }
    if ($InstallProcess) {
        Invoke-SmokeCleanup 'installer_process' {
            if (-not $InstallProcess.HasExited) { $InstallProcess.Kill($true); [void]$InstallProcess.WaitForExit(10000) }
            $InstallProcess.Dispose()
        }
    }
    if ($AttemptedInstall -and -not $CleanupErrors.Contains('reset_process')) {
        Invoke-SmokeCleanup 'recovery_task' {
            $Task = Get-ScheduledTask -TaskName 'SmdHmi-Recover' -ErrorAction SilentlyContinue
            if ($Task) {
                if (@($Task.Actions).Count -ne 1 -or -not (Test-SamePath $Task.Actions[0].Execute $UpdaterExe)) { throw 'Task belongs to another installation' }
                Stop-ScheduledTask -InputObject $Task
                Unregister-ScheduledTask -InputObject $Task -Confirm:$false
            }
        }
        Invoke-SmokeCleanup 'service' {
            $Service = Get-SmokeService
            if ($Service) {
                if (-not (Test-SamePath $Service.PathName $ServiceExe)) { throw 'Service belongs to another installation' }
                $Controller = Get-Service -Name 'SmdHmi'
                try {
                    if ($Controller.Status -ne 'Stopped') { $Controller.Stop(); $Controller.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) }
                } finally { $Controller.Dispose() }
                & sc.exe delete SmdHmi | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'SCM deletion failed' }
                $Deadline = [DateTime]::UtcNow.AddSeconds(15)
                while ((Get-SmokeService) -and [DateTime]::UtcNow -lt $Deadline) { Start-Sleep -Milliseconds 250 }
                if (Get-SmokeService) { throw 'SCM deletion did not complete' }
            }
        }
        Invoke-SmokeCleanup 'registry' {
            if (Test-Path -LiteralPath $ProductKey) {
                if (-not (Test-SamePath (Get-ItemProperty -LiteralPath $ProductKey).InstallDir $InstallDir)) { throw 'Product registration belongs to another installation' }
                Remove-Item -LiteralPath $ProductKey -Recurse -Force
            }
            if (Test-Path -LiteralPath $UninstallKey) {
                if (-not (Test-SamePath (Get-ItemProperty -LiteralPath $UninstallKey).UninstallString (Join-Path $InstallDir 'Uninstall.exe'))) { throw 'Uninstall registration belongs to another installation' }
                Remove-Item -LiteralPath $UninstallKey -Recurse -Force
            }
        }
        Invoke-SmokeCleanup 'start_menu' {
            if (Test-Path -LiteralPath $MenuDir) {
                $Items = @(Get-ChildItem -LiteralPath $MenuDir -Force)
                if ($Items.Count -gt 1 -or ($Items.Count -eq 1 -and $Items[0].Name -ne 'SMD HMI.lnk')) { throw 'Unowned start menu contents' }
                if ($Items.Count) {
                    $Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Items[0].FullName)
                    if (-not (Test-SamePath $Shortcut.TargetPath $DesktopExe)) { throw 'Shortcut belongs to another installation' }
                }
                Remove-Item -LiteralPath $MenuDir -Recurse -Force
            }
        }
    }
    if ($FirewallCreated) {
        Invoke-SmokeCleanup 'firewall_rule' {
            if ($CleanupErrors.Contains('installer_process') -or $CleanupErrors.Contains('pairing_process') -or $CleanupErrors.Contains('reset_process') -or (Get-SmokeService)) { throw 'Keep device traffic blocked until installer, pairing, reset and service have stopped' }
            Remove-NetFirewallRule -Name $FirewallName
        }
    }
    foreach ($Directory in $CreatedRoots) {
        Invoke-SmokeCleanup "directory:$Directory" {
            if ($CleanupErrors.Contains('installer_process') -or $CleanupErrors.Contains('pairing_process') -or $CleanupErrors.Contains('reset_process') -or (Get-SmokeService)) { throw 'Do not remove directories while installer, pairing, reset or service may still run' }
            Remove-OwnedDirectory $Directory
        }
    }
    $Result.cleanup_failures = @($CleanupErrors.ToArray())
    $Result.cleanup_complete = $CleanupErrors.Count -eq 0
    if ($Passed -and $Result.cleanup_complete) { $Result.status = 'passed' }
    $Result.completed_at = [DateTime]::UtcNow.ToString('o')
    New-Item -ItemType Directory -Path (Split-Path -Parent $Evidence) -Force | Out-Null
    $Result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Evidence -Encoding utf8
}
if ($Result.status -ne 'passed') { throw 'Windows installation smoke failed; inspect the sanitized evidence stages' }
Write-Host 'Install smoke passed: LocalService read the offline pairing, application ready with HostComm offline; owned test objects removed'
