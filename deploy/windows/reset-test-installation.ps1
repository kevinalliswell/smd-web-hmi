#requires -Version 5.1
param([switch]$Apply, [switch]$ConfirmNoDeviceAttached)
# Standalone reset for an explicitly confirmed, disconnected development machine.
# No upgrade ticket is created and no application protocol setting is modified.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PSModulePath = Join-Path $PSHOME 'Modules'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$script:ResetBackup = $null
$script:ResetStage = 'preflight'

function Stop-ResetError([string]$Message) {
    $ResetFailure = New-Object InvalidOperationException($Message)
    $ResetFailure.Data['SmdResetSafeMessage'] = $Message
    throw $ResetFailure
}
function Test-SameResetPath([string]$Left, [string]$Right) {
    return [string]::Equals([IO.Path]::GetFullPath($Left).TrimEnd('\'), [IO.Path]::GetFullPath($Right).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
}
function Assert-NoReparse([string]$Root) {
    $Item = Get-Item -LiteralPath $Root -Force
    if (-not $Item.PSIsContainer) { Stop-ResetError 'Expected a directory' }
    Assert-CleanAncestors $Root
    $Queue = New-Object 'Collections.Generic.Queue[string]'
    $Queue.Enqueue($Item.FullName)
    while ($Queue.Count) {
        foreach ($Child in @(Get-ChildItem -LiteralPath $Queue.Dequeue() -Force)) {
            if ($Child.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-ResetError 'Reparse points are unsupported' }
            if ($Child.PSIsContainer) { $Queue.Enqueue($Child.FullName) }
        }
    }
}
function Assert-CleanAncestors([string]$Root) {
    $Item = Get-Item -LiteralPath $Root -Force
    $Ancestor = $Item
    while ($null -ne $Ancestor) {
        if ($Ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-ResetError 'Reparse points are unsupported' }
        $Ancestor = $Ancestor.Parent
    }
}
function Read-ResetJson([string]$Path) {
    return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
}
function Write-ResetJson([string]$Path, $Value) {
    $Temporary = $Path + '.tmp'
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
    $Stream = [IO.File]::Open($Temporary, 'Open', 'ReadWrite', 'None')
    try { $Stream.Flush($true) } finally { $Stream.Dispose() }
    if ([IO.File]::Exists($Path)) { [IO.File]::Replace($Temporary, $Path, $null) }
    else { [IO.File]::Move($Temporary, $Path) }
}
function Assert-TransactionsClosed([string]$DataDir) {
    if (Test-Path -LiteralPath (Join-Path $DataDir 'maintenance.json')) { Stop-ResetError 'An existing maintenance ticket requires separate recovery' }
    if (Test-Path -LiteralPath (Join-Path $DataDir 'updates/install.json')) { Stop-ResetError 'An installation transaction requires separate recovery' }
    foreach ($Name in @('active.json', 'uninstall.json', 'pairing.json')) {
        $Path = Join-Path $DataDir ('updates/' + $Name)
        if (Test-Path -LiteralPath $Path) {
            $Journal = Read-ResetJson $Path
            $Allowed = if ($Name -eq 'active.json') { @('committed','rolled_back') } else { @('committed') }
            if ($Journal.phase -notin $Allowed) { Stop-ResetError 'An unfinished transaction requires separate recovery' }
        }
    }
}
function Assert-DefaultDatabase([string]$EnvironmentFile, [string]$ExpectedDatabase) {
    $DatabaseValues = @()
    foreach ($Line in [IO.File]::ReadAllLines($EnvironmentFile, [Text.Encoding]::UTF8)) {
        if ($Line -match '^\s*(?:export\s+)?(SMD_DB_PATH|SMD_DATA_ROOT|SMD_MAINTENANCE_FILE)\s*=\s*(.*?)\s*$') {
            $Name, $Value = $Matches[1], $Matches[2]
            if ($Value.StartsWith('"')) { $Value = $Value | ConvertFrom-Json }
            elseif ($Value.StartsWith("'")) { Stop-ResetError 'Nonstandard path configuration requires separate maintenance' }
            if ($Name -eq 'SMD_DB_PATH') { $DatabaseValues += $Value }
            elseif ($Value) { Stop-ResetError 'Path overrides require separate maintenance; do not remove them to force this tool' }
        }
    }
    if ($DatabaseValues.Count -ne 1 -or -not [IO.Path]::IsPathRooted($DatabaseValues[0]) -or
        -not (Test-SameResetPath $DatabaseValues[0] $ExpectedDatabase)) { Stop-ResetError 'Only the default SmdHmi/db/smd.db database is supported' }
}
function Get-ResetService { return Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5 }
function Get-ResetPlan {
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not [Environment]::Is64BitProcess -or -not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Stop-ResetError 'Use an elevated 64-bit Windows PowerShell 5.1 terminal'
    }
    foreach ($Scope in @('Process','User','Machine')) {
        foreach ($Name in @('SMD_DATA_ROOT','SMD_DB_PATH','SMD_MAINTENANCE_FILE')) {
            if ([Environment]::GetEnvironmentVariable($Name, $Scope)) { Stop-ResetError 'Environment path overrides require separate maintenance' }
        }
    }
    $Common = [Environment]::GetFolderPath('CommonApplicationData')
    $Programs = [Environment]::GetFolderPath('ProgramFiles')
    if (-not (Test-SameResetPath $env:PROGRAMDATA $Common)) { Stop-ResetError 'PROGRAMDATA does not match the Windows common data directory' }
    $Data = Join-Path $Common 'SmdHmi'
    $BackupRoot = Join-Path $Common 'SmdHmi-TestBackups'
    $Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine','Registry64')
    try {
        $Product = $Base.OpenSubKey('Software\SmdHmi')
        if ($null -eq $Product) { Stop-ResetError 'No registered SmdHmi installation exists' }
        try { $Install = [string]$Product.GetValue('InstallDir'); $Version = [string]$Product.GetValue('Version') }
        finally { $Product.Dispose() }
        $Uninstall = $Base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi')
        if ($null -eq $Uninstall) { Stop-ResetError 'The uninstall registration is missing' }
        try { $UninstallVersion = [string]$Uninstall.GetValue('DisplayVersion'); $UninstallCommand = [string]$Uninstall.GetValue('UninstallString') }
        finally { $Uninstall.Dispose() }
    } finally { $Base.Dispose() }
    if (-not [IO.Path]::IsPathRooted($Install) -or
        -not (Test-SameResetPath ([IO.Path]::GetDirectoryName($Install)) $Programs) -or
        [IO.Path]::GetFileName($Install) -notmatch '^SmdHmi(?:-CI-[0-9a-f]{32})?$') { Stop-ResetError 'The registered installation is outside the supported Program Files boundary' }
    if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$' -or $UninstallVersion -cne $Version -or
        $UninstallCommand -ine ('"' + (Join-Path $Install 'Uninstall.exe') + '"')) { Stop-ResetError 'The product and uninstall registrations disagree' }
    Assert-NoReparse $Install
    Assert-NoReparse $Data
    if (Test-Path -LiteralPath $BackupRoot) { Assert-CleanAncestors $BackupRoot }
    else { Assert-CleanAncestors $Common }
    if (-not (Test-SameResetPath ([IO.Path]::GetPathRoot($Install)) ([IO.Path]::GetPathRoot($BackupRoot)))) { Stop-ResetError 'Program and backup directories must be on the same volume' }
    Assert-TransactionsClosed $Data
    $VersionDir = Join-Path $Install ('versions/' + $Version)
    $Manifest = Read-ResetJson (Join-Path $VersionDir 'manifest.json')
    $Installation = Read-ResetJson (Join-Path $Data 'installation.json')
    if ($Manifest.version -cne $Version -or $Installation.version -cne $Version -or $Manifest.platform -ne 'windows-x64' -or
        $Manifest.schema_version -ne 1 -or $Manifest.commit -notmatch '^[0-9a-f]{40}$') { Stop-ResetError 'Manifest, installation state and registry version disagree' }
    $ExpectedDatabase = Join-Path $Data 'db/smd.db'
    Assert-DefaultDatabase (Join-Path $Data 'config/service.env') $ExpectedDatabase
    if (-not (Test-Path -LiteralPath $ExpectedDatabase -PathType Leaf)) { Stop-ResetError 'The default database is missing' }
    $ServiceExe = Join-Path $VersionDir 'SmdService/SmdService.exe'
    $UpdaterExe = Join-Path $VersionDir 'SmdUpdate/SmdUpdate.exe'
    $DesktopExe = Join-Path $VersionDir 'SmdDesktop/SmdDesktop.exe'
    foreach ($Path in @($ServiceExe,$UpdaterExe,$DesktopExe,(Join-Path $Install 'Uninstall.exe'))) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Stop-ResetError 'A required installed program is missing' }
    }
    $Service = Get-ResetService
    if (-not $Service -or $Service.StartName -ine 'NT AUTHORITY\LocalService' -or
        $Service.PathName -ine ('"' + $ServiceExe + '"') -or $Service.State -notin @('Running','Stopped')) { Stop-ResetError 'The SCM service identity, path or state is unsupported' }
    $Task = Get-ScheduledTask -TaskName 'SmdHmi-Recover' -TaskPath '\'
    $Actions = @($Task.Actions)
    if ($Actions.Count -ne 1 -or $Actions[0].Execute -ine $UpdaterExe -or
        $Actions[0].Arguments -cne ('--recover --install "' + $Install + '"') -or $Actions[0].WorkingDirectory) { Stop-ResetError 'The recovery task does not belong exclusively to this installation' }
    if ($Task.State -eq 'Running') { Stop-ResetError 'The recovery task is currently running' }
    $TaskSid = if ($Task.Principal.UserId -match '^S-1-') { $Task.Principal.UserId } else {
        (New-Object Security.Principal.NTAccount($Task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if ($TaskSid -ne 'S-1-5-18') { Stop-ResetError 'The recovery task does not run as SYSTEM' }
    $Menu = Join-Path $Common 'Microsoft/Windows/Start Menu/Programs/SMD HMI'
    if (Test-Path -LiteralPath $Menu) {
        Assert-NoReparse $Menu
        $Items = @(Get-ChildItem -LiteralPath $Menu -Force)
        if ($Items.Count -ne 1 -or $Items[0].Name -ne 'SMD HMI.lnk') { Stop-ResetError 'The start menu directory contains unexpected files' }
        $Shell = New-Object -ComObject WScript.Shell
        try {
            $Shortcut = $Shell.CreateShortcut($Items[0].FullName)
            try { if ($Shortcut.TargetPath -ine $DesktopExe) { Stop-ResetError 'The shortcut belongs to a different program' } }
            finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shortcut) }
        } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell) }
    }
    return @{version=$Version; install_dir=$Install; data_dir=$Data; backup_root=$BackupRoot; menu_dir=$Menu;
        service=$Service; service_exe=$ServiceExe; updater_exe=$UpdaterExe; task=$Task}
}
function Invoke-ResetNative([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-ResetError 'A Windows metadata or ACL operation failed' }
}
function New-PrivateDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { [void][IO.Directory]::CreateDirectory($Path) }
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { Stop-ResetError 'A backup directory is a reparse point' }
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true,$false)
    foreach ($Sid in @('S-1-5-18','S-1-5-32-544')) {
        $Identity = New-Object Security.Principal.SecurityIdentifier($Sid)
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $Acl.AddAccessRule($Rule)
    }
    $Acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    Set-Acl -LiteralPath $Path -AclObject $Acl
}
function Protect-ResetTree([string]$Path) {
    Assert-NoReparse $Path
    New-PrivateDirectory $Path
    foreach ($Child in @(Get-ChildItem -LiteralPath $Path -Force)) {
        Invoke-ResetNative (Join-Path $env:SystemRoot 'System32/icacls.exe') @($Child.FullName,'/reset','/T','/Q')
    }
    foreach ($Item in @((Get-Item -LiteralPath $Path -Force)) + @(Get-ChildItem -LiteralPath $Path -Force -Recurse)) {
        $Rules = (Get-Acl -LiteralPath $Item.FullName).GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])
        foreach ($Rule in $Rules) {
            if ($Rule.AccessControlType -eq 'Allow' -and $Rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) { Stop-ResetError 'A backup file retained an unexpected access grant' }
        }
    }
}
function Enter-ResetMutex([string]$Name) {
    $Created = $false
    $Mutex = New-Object Threading.Mutex($false,('Global\' + $Name),[ref]$Created)
    if (-not $Created) { $Mutex.Dispose(); Stop-ResetError 'Another backend or updater process is active' }
    return $Mutex
}
function Set-ResetStage([string]$Phase, $Plan, [string]$BackupDir) {
    $script:ResetStage = $Phase
    Write-ResetJson (Join-Path $BackupDir 'stage.json') @{schema_version=1;phase=$Phase;version=$Plan.version;
        install_dir=$Plan.install_dir;data_dir=$Plan.data_dir;backup_dir=$BackupDir;updated_at=[DateTime]::UtcNow.ToString('o')}
}
function Get-TreeSnapshot([string]$Root) {
    Assert-NoReparse $Root
    $Prefix = (Get-Item -LiteralPath $Root).FullName.TrimEnd('\') + '\'
    foreach ($Item in @(Get-ChildItem -LiteralPath $Root -Force -Recurse | Sort-Object FullName)) {
        $Relative = $Item.FullName.Substring($Prefix.Length).Replace('\','/')
        if ($Item.PSIsContainer) { [ordered]@{path=$Relative;kind='directory'} }
        else { [ordered]@{path=$Relative;kind='file';length=$Item.Length;sha256=(Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash.ToLower()} }
    }
}
function Assert-SameSnapshot($Left, $Right) {
    if (($Left | ConvertTo-Json -Depth 6 -Compress) -cne ($Right | ConvertTo-Json -Depth 6 -Compress)) { Stop-ResetError 'Backup verification failed; original files must remain in place' }
}
function Copy-ResetTree([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { Stop-ResetError 'A backup destination already exists' }
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}
function New-VerifiedBackup($Plan, [string]$BackupDir) {
    $Manifest = [ordered]@{schema_version=1;version=$Plan.version}
    foreach ($Name in @('program','data')) {
        $Source = if ($Name -eq 'program') { $Plan.install_dir } else { $Plan.data_dir }
        $Before = @(Get-TreeSnapshot $Source)
        $Destination = Join-Path $BackupDir $Name
        Copy-ResetTree $Source $Destination
        Protect-ResetTree $Destination
        Assert-SameSnapshot $Before @(Get-TreeSnapshot $Destination)
        Assert-SameSnapshot $Before @(Get-TreeSnapshot $Source)
        $Manifest[$Name] = $Before
    }
    $Manifest['metadata'] = @(Get-TreeSnapshot (Join-Path $BackupDir 'metadata'))
    $Path = Join-Path $BackupDir 'manifest.json'
    Write-ResetJson $Path $Manifest
    $ReadBack = Read-ResetJson $Path
    Assert-SameSnapshot $Manifest.data @($ReadBack.data)
    Assert-SameSnapshot $Manifest.program @($ReadBack.program)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower()
}
function Export-ResetMetadata($Plan, [string]$BackupDir) {
    $Metadata = Join-Path $BackupDir 'metadata'
    New-PrivateDirectory $Metadata
    $Reg = Join-Path $env:SystemRoot 'System32/reg.exe'
    Invoke-ResetNative $Reg @('export','HKLM\Software\SmdHmi',(Join-Path $Metadata 'product.reg'),'/y')
    Invoke-ResetNative $Reg @('export','HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi',(Join-Path $Metadata 'uninstall.reg'),'/y')
    $Xml = Export-ScheduledTask -TaskName 'SmdHmi-Recover' -TaskPath '\'
    [IO.File]::WriteAllText((Join-Path $Metadata 'recovery-task.xml'),$Xml,[Text.Encoding]::Unicode)
    Write-ResetJson (Join-Path $Metadata 'service.json') ($Plan.service | Select-Object Name,DisplayName,StartName,StartMode,State,PathName,ProcessId)
    foreach ($Name in @('program','data')) {
        $Source = if ($Name -eq 'program') { $Plan.install_dir } else { $Plan.data_dir }
        Invoke-ResetNative (Join-Path $env:SystemRoot 'System32/icacls.exe') @($Source,'/save',(Join-Path $Metadata ($Name + '-acl.txt')),'/T','/Q')
    }
    if (Test-Path -LiteralPath $Plan.menu_dir) { Copy-ResetTree $Plan.menu_dir (Join-Path $Metadata 'menu') }
    Protect-ResetTree $Metadata
}
function Stop-ResetInstallation($Plan) {
    Disable-ScheduledTask -TaskName 'SmdHmi-Recover' -TaskPath '\' | Out-Null
    Set-Service -Name 'SmdHmi' -StartupType Disabled
    $Controller = Get-Service -Name 'SmdHmi'
    try {
        if ($Controller.Status -ne 'Stopped') { $Controller.Stop(); $Controller.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(60)) }
    } finally { $Controller.Dispose() }
    $Deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ($Plan.service.ProcessId -and (Get-Process -Id $Plan.service.ProcessId -ErrorAction SilentlyContinue)) {
        if ([DateTime]::UtcNow -ge $Deadline) { Stop-ResetError 'The old backend process has not exited; it will not be killed' }
        Start-Sleep -Milliseconds 200
    }
    $Actual = Get-ResetService
    if (-not $Actual -or $Actual.State -ne 'Stopped' -or $Actual.StartMode -ne 'Disabled' -or
        (Get-ScheduledTask -TaskName 'SmdHmi-Recover' -TaskPath '\').State -ne 'Disabled') { Stop-ResetError 'The backend and recovery task are not safely stopped and disabled' }
}
function Remove-ResetRegistration($Plan) {
    $Current = Get-ResetPlan
    if ($Current.version -cne $Plan.version -or -not (Test-SameResetPath $Current.install_dir $Plan.install_dir) -or
        $Current.service.State -ne 'Stopped' -or $Current.service.StartMode -ne 'Disabled') { Stop-ResetError 'Installation ownership changed before removal' }
    Unregister-ScheduledTask -TaskName 'SmdHmi-Recover' -TaskPath '\' -Confirm:$false
    $Deleted = Invoke-CimMethod -InputObject $Current.service -MethodName Delete
    if ($Deleted.ReturnValue -ne 0) { Stop-ResetError 'SCM refused to delete the stopped service' }
    $Deadline = [DateTime]::UtcNow.AddSeconds(30)
    while (Get-ResetService) {
        if ([DateTime]::UtcNow -ge $Deadline) { Stop-ResetError 'The service registration is still present' }
        Start-Sleep -Milliseconds 250
    }
    $Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine','Registry64')
    try {
        $Base.DeleteSubKeyTree('Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi',$false)
        $Base.DeleteSubKeyTree('Software\SmdHmi',$false)
    } finally { $Base.Dispose() }
}
function Move-RetiredTree([string]$Source, [string]$Destination) {
    Assert-NoReparse $Source
    if (Test-Path -LiteralPath $Destination) { Stop-ResetError 'The retired destination already exists' }
    if (-not (Test-SameResetPath ([IO.Path]::GetPathRoot($Source)) ([IO.Path]::GetPathRoot($Destination)))) { Stop-ResetError 'Retiring directories requires the same volume' }
    [IO.Directory]::Move($Source,$Destination)
    Protect-ResetTree $Destination
}
function Invoke-TestInstallationReset([switch]$Apply, [switch]$ConfirmNoDeviceAttached) {
    if ($Apply.IsPresent -ne $ConfirmNoDeviceAttached.IsPresent) { Stop-ResetError 'Both -Apply and -ConfirmNoDeviceAttached are required to change this machine' }
    $Plan = Get-ResetPlan
    if (-not $Apply) { return @{mode='preview';phase='validated';version=$Plan.version;install_dir=$Plan.install_dir;data_dir=$Plan.data_dir} }
    $UpdaterMutex = Enter-ResetMutex 'SmdHmi.Updater'
    $BackendMutex = $null
    try {
        $Plan = Get-ResetPlan
        New-PrivateDirectory $Plan.backup_root
        $Backup = Join-Path $Plan.backup_root ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
        if (Test-Path -LiteralPath $Backup) { Stop-ResetError 'The backup destination already exists' }
        New-PrivateDirectory $Backup
        $script:ResetBackup = $Backup
        Set-ResetStage 'planned' $Plan $Backup
        Export-ResetMetadata $Plan $Backup
        Set-ResetStage 'stopping' $Plan $Backup
        Stop-ResetInstallation $Plan
        $BackendMutex = Enter-ResetMutex 'SmdHmi.Backend'
        Set-ResetStage 'copying' $Plan $Backup
        $ManifestHash = New-VerifiedBackup $Plan $Backup
        Set-ResetStage 'verified' $Plan $Backup
        $Manifest = Read-ResetJson (Join-Path $Backup 'manifest.json')
        Assert-SameSnapshot @($Manifest.program) @(Get-TreeSnapshot $Plan.install_dir)
        Assert-SameSnapshot @($Manifest.data) @(Get-TreeSnapshot $Plan.data_dir)
        Set-ResetStage 'removing_registration' $Plan $Backup
        Remove-ResetRegistration $Plan
        Set-ResetStage 'retiring_program' $Plan $Backup
        Move-RetiredTree $Plan.install_dir (Join-Path $Backup 'retired-program')
        Set-ResetStage 'retiring_data' $Plan $Backup
        Move-RetiredTree $Plan.data_dir (Join-Path $Backup 'retired-data')
        if (Test-Path -LiteralPath $Plan.menu_dir) { Move-RetiredTree $Plan.menu_dir (Join-Path $Backup 'retired-menu') }
        Set-ResetStage 'completed' $Plan $Backup
        return @{mode='apply';phase='completed';version=$Plan.version;install_dir=$Plan.install_dir;data_dir=$Plan.data_dir;
            backup_dir=$Backup;backup_manifest_sha256=$ManifestHash;retired_program_dir=(Join-Path $Backup 'retired-program');retired_data_dir=(Join-Path $Backup 'retired-data')}
    } finally {
        if ($null -ne $BackendMutex) { $BackendMutex.Dispose() }
        $UpdaterMutex.Dispose()
    }
}
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-TestInstallationReset -Apply:$Apply -ConfirmNoDeviceAttached:$ConfirmNoDeviceAttached | ConvertTo-Json -Depth 8; exit 0 }
    catch {
        $Failure = $_.Exception
        $Reason = 'Windows operation failed (' + $Failure.GetType().Name + ')'
        while ($null -ne $Failure) {
            if ($Failure.Data.Contains('SmdResetSafeMessage')) { $Reason = $Failure.Data['SmdResetSafeMessage']; break }
            $Failure = $Failure.InnerException
        }
        [Console]::Error.WriteLine(('Reset failed at phase {0}: {1}. Backup/stage location: {2}. Files are retained. Contact the maintainer before retrying or installing.' -f $script:ResetStage,$Reason,$script:ResetBackup))
        exit 1
    }
}
