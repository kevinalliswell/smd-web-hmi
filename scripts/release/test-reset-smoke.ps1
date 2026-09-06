# Invoked only by the isolated GitHub-hosted installation smoke, never a field tool.
function Invoke-TestResetSmoke {
    param([string]$Installer, [string]$InstallDir, [string]$DataDir, [string]$Version,
        [string]$Identity, [string]$OwnerFile, [int]$TimeoutSeconds)
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or -not $IsWindows) {
        throw 'Reset smoke requires a disposable GitHub-hosted Windows runner'
    }
    $ResetScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../deploy/windows/reset-test-installation.ps1')).Path
    $WindowsPowerShell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $ConfigFile = Join-Path $DataDir 'config/service.env'
    $PasswordFile = Join-Path $DataDir 'config/bootstrap-admin-password.txt'
    $OldPasswordHash = (Get-FileHash -LiteralPath $PasswordFile -Algorithm SHA256).Hash
    $FixtureUser = 'reset_' + $Identity.Substring(0, 12)
    $ChangedPassword = 'Ci!' + [guid]::NewGuid().ToString('N')
    $BaseUrl = 'http://127.0.0.1:8000'
    $Auth = (Invoke-RestMethod -Uri "$BaseUrl/api/auth/login" -Method Post -ContentType 'application/json' -NoProxy -TimeoutSec 5 `
        -Body (@{ username = 'admin'; password = (Get-Content -LiteralPath $PasswordFile -Raw).Trim() } | ConvertTo-Json)).data
    Invoke-RestMethod -Uri "$BaseUrl/api/users/change-password" -Method Post -ContentType 'application/json' -NoProxy -TimeoutSec 5 `
        -Headers @{ Authorization = "Bearer $($Auth.token)" } `
        -Body (@{ old_password = (Get-Content -LiteralPath $PasswordFile -Raw).Trim(); new_password = $ChangedPassword } | ConvertTo-Json) | Out-Null
    $Auth = (Invoke-RestMethod -Uri "$BaseUrl/api/auth/login" -Method Post -ContentType 'application/json' -NoProxy -TimeoutSec 5 `
        -Body (@{ username = 'admin'; password = $ChangedPassword } | ConvertTo-Json)).data
    Invoke-RestMethod -Uri "$BaseUrl/api/users" -Method Post -ContentType 'application/json' -NoProxy -TimeoutSec 5 `
        -Headers @{ Authorization = "Bearer $($Auth.token)" } `
        -Body (@{ username = $FixtureUser; password = ('Ci!' + [guid]::NewGuid().ToString('N')); role = 'observer' } | ConvertTo-Json) | Out-Null

    # Reproduce the legacy protocol gate only in this owned, firewall-isolated fixture.
    Stop-Service SmdHmi
    $Controller = Get-Service SmdHmi
    try { $Controller.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) } finally { $Controller.Dispose() }
    $OldConfig = Get-Content -LiteralPath $ConfigFile -Raw -Encoding utf8
    if ($OldConfig -notmatch '(?m)^PROTOCOL_VERSION="2\.0"\r?$') { throw 'Unexpected new-install protocol fixture' }
    [IO.File]::WriteAllText($ConfigFile, ($OldConfig -replace '(?m)^PROTOCOL_VERSION="2\.0"\r?$', 'PROTOCOL_VERSION="1.0"'), [Text.UTF8Encoding]::new($false))
    Start-Service SmdHmi
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        try { $Health = (Invoke-RestMethod -Uri "$BaseUrl/api/system/health" -NoProxy -TimeoutSec 3).data } catch { $Health = $null }
        if ($Health -and $Health.status -eq 'ready') { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)
    if (-not $Health -or $Health.status -ne 'ready' -or $Health.checks.hostcomm -ne 'offline') { throw 'Legacy offline fixture did not become application-ready' }
    $Rejected = Invoke-WebRequest -Uri "$BaseUrl/api/system/maintenance/prepare" -Method Post -ContentType 'application/json' `
        -NoProxy -TimeoutSec 5 -SkipHttpErrorCheck -Headers @{ Authorization = "Bearer $($Auth.token)" } `
        -Body (@{ target_version = $Version } | ConvertTo-Json)
    if ($Rejected.StatusCode -ne 503 -or ($Rejected.Content | ConvertFrom-Json).error_code -ne 'maintenance_active' -or
        (Test-Path -LiteralPath (Join-Path $DataDir 'maintenance.json'))) { throw 'Legacy offline preparation was not rejected as expected' }
    $OldConfigHash = (Get-FileHash -LiteralPath $ConfigFile -Algorithm SHA256).Hash
    $BeforeService = Get-SmokeService
    $BackupRoot = Join-Path $env:PROGRAMDATA 'SmdHmi-TestBackups'
    if (Test-Path -LiteralPath $BackupRoot) { throw 'Reset smoke requires an absent backup root' }
    $Stdout = [IO.Path]::GetTempFileName()
    $Stderr = [IO.Path]::GetTempFileName()
    $Process = $null
    try {
        Write-Host 'Reset smoke: preview under Windows PowerShell 5.1'
        $Process = Start-Process -FilePath $WindowsPowerShell -ArgumentList `
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ResetScript`"" `
            -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
        if (-not $Process.WaitForExit(60000)) { throw 'Reset preview timed out under Windows PowerShell 5.1' }
        if ($Process.ExitCode -ne 0) {
            # This tool emits only controlled reasons, exception types and protected paths.
            Write-Host (Get-Content -LiteralPath $Stderr -Raw)
            throw 'Reset preview failed under Windows PowerShell 5.1'
        }
        $Preview = Get-Content -LiteralPath $Stdout -Raw | ConvertFrom-Json
        $AfterService = Get-SmokeService
        $AfterBackups = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName)
        if ($Preview.mode -ne 'preview' -or -not $AfterService -or $AfterService.State -ne 'Running' -or
            $AfterService.ProcessId -ne $BeforeService.ProcessId -or $AfterBackups.Count -ne 0 -or (Test-Path -LiteralPath $BackupRoot) -or
            (Get-FileHash -LiteralPath $ConfigFile -Algorithm SHA256).Hash -ne $OldConfigHash) { throw 'Preview changed the old installation' }
        $Process.Dispose(); $Process = $null
        Write-Host 'Reset smoke: archive the isolated legacy fixture'
        $Process = Start-Process -FilePath $WindowsPowerShell -ArgumentList `
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ResetScript`" -Apply -ConfirmNoDeviceAttached" `
            -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
        if (-not $Process.WaitForExit(600000)) { throw 'Test reset timed out under Windows PowerShell 5.1' }
        if ($Process.ExitCode -ne 0) {
            Write-Host (Get-Content -LiteralPath $Stderr -Raw)
            throw 'Test reset failed under Windows PowerShell 5.1'
        }
        $Reset = Get-Content -LiteralPath $Stdout -Raw | ConvertFrom-Json
        if ($Reset.mode -ne 'apply' -or $Reset.phase -ne 'completed' -or $Reset.version -ne $Version -or
            -not (Test-SamePath $Reset.install_dir $InstallDir) -or -not (Test-SamePath $Reset.data_dir $DataDir) -or
            -not (Test-SamePath (Split-Path -Parent $Reset.backup_dir) $BackupRoot) -or
            (Get-SmokeService) -or (Test-Path -LiteralPath $DataDir) -or (Test-Path -LiteralPath $InstallDir)) { throw 'Reset did not leave an empty installation location' }
        if ((Get-FileHash -LiteralPath (Join-Path $Reset.backup_dir 'data/config/service.env') -Algorithm SHA256).Hash -ne $OldConfigHash -or
            (Get-FileHash -LiteralPath (Join-Path $Reset.backup_dir 'data/config/bootstrap-admin-password.txt') -Algorithm SHA256).Hash -ne $OldPasswordHash) { throw 'Archived configuration differs' }
        $Process.Dispose(); $Process = $null
        Write-Host 'Reset smoke: install into the empty active directories'
        foreach ($Directory in @($InstallDir, $DataDir)) {
            New-Item -ItemType Directory -Path $Directory | Out-Null
            Set-Content -LiteralPath (Join-Path $Directory $OwnerFile) -Value $Identity -Encoding ascii
        }
        $Process = Start-Process -FilePath $Installer -ArgumentList "/S /D=$InstallDir" -PassThru
        if (-not $Process.WaitForExit($TimeoutSeconds * 1000) -or $Process.ExitCode -ne 0) { throw 'Fresh installation after reset failed' }
        if ((Get-FileHash -LiteralPath $PasswordFile -Algorithm SHA256).Hash -eq $OldPasswordHash -or
            (Get-Content -LiteralPath $ConfigFile -Raw) -notmatch '(?m)^PROTOCOL_VERSION="2\.0"\r?$') { throw 'Fresh installation reused old credentials or configuration' }
        $NewAuth = (Invoke-RestMethod -Uri "$BaseUrl/api/auth/login" -Method Post -ContentType 'application/json' -NoProxy -TimeoutSec 5 `
            -Body (@{ username = 'admin'; password = (Get-Content -LiteralPath $PasswordFile -Raw).Trim() } | ConvertTo-Json)).data
        if (-not $NewAuth.must_change_password) { throw 'Fresh installation did not require changing the initial password' }
        $Database = Join-Path $DataDir 'db/smd.db'
        $ArchivedDatabase = Join-Path $Reset.backup_dir 'data/db/smd.db'
        & python -c 'import sqlite3,sys; from contextlib import closing; old,new,user=sys.argv[1:]; a=sqlite3.connect("file:"+old+"?mode=ro",uri=True); b=sqlite3.connect("file:"+new+"?mode=ro",uri=True); assert a.execute("select count(*) from user_account where username=?",(user,)).fetchone()[0]==1; assert b.execute("select count(*) from user_account where username=?",(user,)).fetchone()[0]==0; a.close(); b.close()' $ArchivedDatabase $Database $FixtureUser
        if ($LASTEXITCODE -ne 0) { throw 'Old database was not preserved independently of the fresh database' }
        $Health = (Invoke-RestMethod -Uri "$BaseUrl/api/system/health" -NoProxy -TimeoutSec 3).data
        if ($Health.status -ne 'ready' -or $Health.version -ne $Version -or $Health.checks.hostcomm -ne 'offline') { throw 'Fresh application is not ready offline' }
        return @{ legacy_offline_prepare = 'rejected'; powershell = 'Windows 5.1'; preview_unchanged = $true;
            reset = 'completed'; backup_verified = $true; old_database_preserved = $true; fresh_database = $true;
            fresh_credentials = $true; reinstall_exit_code = 0; hostcomm = 'offline';
            tool_sha256 = (Get-FileHash -LiteralPath $ResetScript -Algorithm SHA256).Hash.ToLower();
            backup_manifest_sha256 = $Reset.backup_manifest_sha256 }
    } finally {
        $ChildExited = $true
        if ($Process) {
            try {
                if (-not $Process.HasExited) {
                    $Process.Kill($true)
                    if (-not $Process.WaitForExit(10000) -or -not $Process.HasExited) { throw 'Reset child did not exit' }
                }
                $Process.Dispose()
            } catch { $ChildExited = $false; $CleanupErrors.Add('reset_process') }
        }
        if ($ChildExited) {
            try {
                if (Test-Path -LiteralPath $BackupRoot) {
                    if ((Get-Item -LiteralPath $BackupRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Backup root is a reparse point' }
                    foreach ($Directory in @(Get-ChildItem -LiteralPath $BackupRoot -Force)) {
                        if (-not $Directory.PSIsContainer -or $Directory.Name -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{32}$' -or
                            $Directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unexpected reset backup entry' }
                        $StageRecord = Get-Content -LiteralPath (Join-Path $Directory.FullName 'stage.json') -Raw | ConvertFrom-Json
                        if (-not (Test-SamePath $StageRecord.install_dir $InstallDir) -or -not (Test-SamePath $StageRecord.data_dir $DataDir) -or
                            -not (Test-SamePath $StageRecord.backup_dir $Directory.FullName) -or $StageRecord.version -ne $Version) { throw 'Reset backup ownership is unknown' }
                        Set-Content -LiteralPath (Join-Path $Directory.FullName $OwnerFile) -Value $Identity -Encoding ascii
                        $CreatedRoots.Add($Directory.FullName)
                    }
                    Set-Content -LiteralPath (Join-Path $BackupRoot $OwnerFile) -Value $Identity -Encoding ascii
                    $CreatedRoots.Add($BackupRoot)
                }
            } catch { $CleanupErrors.Add('reset_backup_ownership') }
            Remove-Item -LiteralPath $Stdout,$Stderr -Force
        } else {
            throw 'Reset child exit is unconfirmed; preserve its files and network isolation'
        }
    }
}
