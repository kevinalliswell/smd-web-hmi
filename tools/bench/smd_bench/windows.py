"""Fixed Windows object inventory; PowerShell code never interpolates caller text."""

import base64
import json
import os
import struct
from pathlib import Path

from smd_desktop import windows_powershell

ERROR_STAGES = frozenset({"bootstrap", "operation", "acl_create", "acl_set", "acl_read", "acl_verify"})
ERROR_CATEGORIES = frozenset(
    {
        "NotSpecified",
        "PermissionDenied",
        "ObjectNotFound",
        "ResourceUnavailable",
        "InvalidArgument",
        "InvalidData",
        "InvalidOperation",
        "ParserError",
        "SecurityError",
    }
)


class WindowsOperationError(RuntimeError):
    """Retain fixed diagnostics only, never PowerShell messages or payload values."""

    def __init__(self, exit_code: int, stderr: bytes):
        details = {}
        for line in stderr[-8192:].decode("utf-8", errors="replace").splitlines():
            if line.startswith("SMD_BENCH_ERROR:"):
                try:
                    candidate = json.loads(line.removeprefix("SMD_BENCH_ERROR:"))
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    details = candidate
        stage, category = details.get("stage"), details.get("category")
        self.diagnostic = {
            "code": "powershell_failed",
            "stage": stage if isinstance(stage, str) and stage in ERROR_STAGES else "unknown",
            "category": category if isinstance(category, str) and category in ERROR_CATEGORIES else "unknown",
            "exit_code": int(exit_code),
        }
        super().__init__("Windows object operation failed: " + json.dumps(self.diagnostic, sort_keys=True))


def powershell(script: str, payload: dict | None = None, *, timeout: int = 60):
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise RuntimeError("SmdBench installation requires Windows x64")
    preamble = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; $benchStage='bootstrap'; try { $p=$env:SMD_BENCH_INPUT|ConvertFrom-Json; $benchStage='operation';\n"
    ending = "\n} catch { [Console]::Error.WriteLine('SMD_BENCH_ERROR:'+(@{stage=$benchStage;category=[string]$_.CategoryInfo.Category}|ConvertTo-Json -Compress)); exit 1 }"
    encoded = base64.b64encode((preamble + script + ending).encode("utf-16-le")).decode("ascii")
    process = windows_powershell.run(
        ["-EncodedCommand", encoded],
        env={**os.environ, "SMD_BENCH_INPUT": json.dumps(payload or {})},
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        raise WindowsOperationError(process.returncode, process.stderr)
    output = process.stdout.decode("utf-8-sig").strip()
    return json.loads(output) if output else None


def inventory() -> dict:
    return powershell(
        r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$overrides=@()
foreach($scope in @('Process','User','Machine')) {
 foreach($name in @('SMD_DATA_ROOT','SMD_DB_PATH','SMD_MAINTENANCE_FILE','SMD_GATEWAY_LOCK_FILE','HOSTCOMM_HOST','HOSTCOMM_PORT','HOSTCOMM_MOCK','PROTOCOL_VERSION','HOSTCOMM_PSK_FILE')) {
  if([Environment]::GetEnvironmentVariable($name,$scope)) {$overrides+=($scope+':'+$name)}
 }
}
$service=Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5
$task=Get-ScheduledTask -TaskName 'SmdHmi-Recover' -ErrorAction SilentlyContinue
$pf=[Environment]::GetFolderPath('ProgramFiles');$pd=[Environment]::GetFolderPath('CommonApplicationData')
$existing=@(); foreach($path in @((Join-Path $pd 'SmdHmi'),(Join-Path $pf 'SmdHmi'),(Join-Path $pd 'SmdHmi-TestBackups'),(Join-Path $pd 'Microsoft/Windows/Start Menu/Programs/SMD HMI'),'HKLM:\Software\SmdHmi','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi')) {if(Test-Path -LiteralPath $path){$existing+=$path}}
@{admin=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); program_files=$pf; program_data=$pd; overrides=$overrides; existing=$existing; service=if($service){@{path=$service.PathName;state=$service.State;account=$service.StartName;pid=$service.ProcessId}}else{$null}; recovery_task=[bool]$task; busy_ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object {$_.LocalPort -in @(8000,34212)}|ForEach-Object LocalPort); firewall_enabled=(@(Get-NetFirewallProfile|Where-Object {-not $_.Enabled}).Count -eq 0)}|ConvertTo-Json -Depth 5 -Compress
"""
    )


def secure_directory(path: Path) -> None:
    powershell(
        r"""
$benchStage='acl_create'
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$admin=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$acl.SetOwner($admin)
foreach($sid in @($admin,$system)) {$rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow');$acl.AddAccessRule($rule)}
$benchStage='acl_set'
Set-Acl -LiteralPath $p.path -AclObject $acl
$benchStage='acl_read'
$actual=Get-Acl -LiteralPath $p.path
$benchStage='acl_verify'
if(-not $actual.AreAccessRulesProtected -or $actual.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $admin.Value){throw 'private ownership mismatch'}
$rules=@($actual.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
if($rules.Count -ne 2){throw 'private ACL differs'}
foreach($rule in $rules){if($rule.IdentityReference.Value -notin @($admin.Value,$system.Value) -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne 'FullControl'){throw 'private ACL differs'}}
""",
        {"path": str(path)},
    )


def firewall(run_id: str, executable: Path, *, remove: bool = False) -> None:
    payload = {"name": "SmdBench-" + run_id, "executable": str(executable)}
    if remove:
        powershell(
            r"""
$rules=@();foreach($suffix in @('-external','-legacy')){
 $rule=Get-NetFirewallRule -Name ($p.name+$suffix) -ErrorAction SilentlyContinue
 if($rule){$app=$rule|Get-NetFirewallApplicationFilter;if(-not [String]::Equals($app.Program,$p.executable,[StringComparison]::OrdinalIgnoreCase)){throw 'foreign firewall rule'};$rules+=$rule}
}
$rules|Remove-NetFirewallRule
""",
            payload,
        )
    else:
        powershell(
            r"""
# Block the precise test service outside loopback, including any future default device port.
New-NetFirewallRule -Name ($p.name+'-external') -DisplayName ($p.name+'-external') -Direction Outbound -Action Block -Profile Any -Program $p.executable -RemoteAddress @('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')|Out-Null
New-NetFirewallRule -Name ($p.name+'-legacy') -DisplayName ($p.name+'-legacy') -Direction Outbound -Action Block -Profile Any -Program $p.executable -Protocol TCP -RemotePort 34211|Out-Null
""",
            payload,
        )


def service_info() -> dict | None:
    return powershell(
        r"""
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$s=Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5
if($s){@{path=$s.PathName;state=$s.State;account=$s.StartName;pid=$s.ProcessId}|ConvertTo-Json -Compress}
"""
    )


def check_service(executable: Path, *, running: bool = True) -> dict:
    service = service_info()
    if (
        not service
        or os.path.normcase(os.path.abspath(service["path"].strip('"'))) != os.path.normcase(str(executable))
        or service["account"] != "NT AUTHORITY\\LocalService"
        or (running and service["state"] != "Running")
    ):
        raise ValueError("SCM service does not match this installation")
    return service


def stop_service(executable: Path, data: Path) -> None:
    from smd_desktop.windows_platform import WindowsPlatform

    check_service(executable, running=False)
    WindowsPlatform(data).stop()


def start_service(executable: Path, data: Path) -> None:
    from smd_desktop.windows_platform import WindowsPlatform

    check_service(executable, running=False)
    WindowsPlatform(data).start()


def remove_registration(executable: Path, updater: Path, desktop: Path, install: Path, data: Path) -> None:
    powershell(
        r"""
function Same($a,$b){return [String]::Equals([IO.Path]::GetFullPath($a.Trim('"')),[IO.Path]::GetFullPath($b),[StringComparison]::OrdinalIgnoreCase)}
$service=Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5
$task=Get-ScheduledTask -TaskName 'SmdHmi-Recover' -ErrorAction SilentlyContinue
$product='HKLM:\Software\SmdHmi';$uninstall='HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi'
$menu=Join-Path $p.data_parent 'Microsoft/Windows/Start Menu/Programs/SMD HMI'
if($service -and -not (Same $service.PathName $p.service)){throw 'foreign service'}
if($task -and (@($task.Actions).Count -ne 1 -or -not (Same $task.Actions[0].Execute $p.updater) -or $task.Actions[0].Arguments -cne ('--recover --install "'+$p.install+'"') -or $task.Actions[0].WorkingDirectory)){throw 'foreign task'}
if((Test-Path $product) -and -not (Same (Get-ItemProperty $product).InstallDir $p.install)){throw 'foreign registration'}
if((Test-Path $uninstall) -and -not (Same (Get-ItemProperty $uninstall).UninstallString (Join-Path $p.install 'Uninstall.exe'))){throw 'foreign uninstall registration'}
if(Test-Path -LiteralPath $menu){
 $cursor=Get-Item -LiteralPath $menu -Force;while($cursor){if($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'shortcut path reparse'};$cursor=$cursor.Parent}
 $items=@(Get-ChildItem -LiteralPath $menu -Force)
 if($items.Count -gt 1 -or ($items.Count -eq 1 -and $items[0].Name -ne 'SMD HMI.lnk')){throw 'foreign shortcut'}
 if($items.Count){if($items[0].Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'shortcut reparse'};$shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut($items[0].FullName);if(-not (Same $shortcut.TargetPath $p.desktop)){throw 'foreign shortcut target'}}
}
if($task){Stop-ScheduledTask -InputObject $task;Unregister-ScheduledTask -InputObject $task -Confirm:$false}
if($service){$s=Get-Service SmdHmi;if($s.Status -ne 'Stopped'){$s.Stop();$s.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(60))};$s.Dispose();& sc.exe delete SmdHmi|Out-Null;if($LASTEXITCODE -ne 0){throw 'delete service failed'}
 $until=[DateTime]::UtcNow.AddSeconds(30);do{$service=Get-CimInstance Win32_Service -Filter "Name='SmdHmi'" -OperationTimeoutSec 5;if(-not $service){break};Start-Sleep -Milliseconds 250}while([DateTime]::UtcNow -lt $until);if($service){throw 'service still exists'}
}
foreach($key in @($product,$uninstall)){if(Test-Path -LiteralPath $key){Remove-Item -LiteralPath $key -Recurse -Force}}
if(Test-Path -LiteralPath $menu){Remove-Item -LiteralPath $menu -Recurse -Force}
""",
        {
            "service": str(executable),
            "updater": str(updater),
            "desktop": str(desktop),
            "install": str(install),
            "data_parent": str(data.parent),
        },
        timeout=120,
    )


_job_handle = None


def contain_child_processes() -> None:
    """Parent death closes this nested job and terminates simulator/Chromium children."""
    global _job_handle
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, None)
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    _job_handle = job  # Intentionally lives until process exit; never close it while running cleanup.
