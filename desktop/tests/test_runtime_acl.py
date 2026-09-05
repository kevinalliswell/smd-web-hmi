"""Windows原生ACL检查；不把临时目录权限检查当作真实WebView2窗口验收。"""

import json
import os
from pathlib import Path

import pytest
from smd_desktop import windows_powershell

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/windows/configure-acl.ps1"
PACKAGE_SIDS = {"S-1-15-2-1", "S-1-15-2-2"}
LOAD_FUNCTIONS = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SMD_TEST_ACL_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$initializers = @()
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) { break }
    $initializers += $statement.Extent.Text
}
. ([scriptblock]::Create(($initializers -join "`n")))
foreach ($name in @('Invoke-Icacls', 'Grant-WebView2RuntimeAccess')) {
    $function = $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $false) | Where-Object Name -eq $name
    if (-not $function) { throw "Required ACL function missing: $name" }
    Invoke-Expression $function.Extent.Text
}
"""


def execute(script, install, **environment):
    return windows_powershell.run(
        ["-Command", LOAD_FUNCTIONS + script],
        env={**os.environ, "SMD_TEST_ACL_SCRIPT": str(SCRIPT), "SMD_TEST_ACL_INSTALL": str(install), **environment},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows icacls and NTFS security descriptors")
def test_fixed_runtime_package_acl_does_not_grant_access_to_programs_or_secrets(tmp_path):
    install = tmp_path / "program"
    paths = {"install": install, "versions": install / "versions"}
    for version in ("0.3.0-rc.1", "0.3.0-rc.2"):
        directory = install / "versions" / version
        runtime = directory / "webview2"
        runtime.mkdir(parents=True)
        executable = runtime / "msedgewebview2.exe"
        executable.write_bytes(b"fixture")
        service = directory / "SmdService"
        service.mkdir()
        (service / "SmdService.exe").write_bytes(b"fixture")
        paths[version] = directory
        paths[version + "/runtime"] = runtime
        paths[version + "/browser"] = executable
        paths[version + "/service"] = service / "SmdService.exe"
    secret = tmp_path / "ProgramData/config/private.key"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"test secret")
    result = execute(
        r"""
$before = (Get-Acl -LiteralPath $env:SMD_TEST_ACL_SECRET).Sddl
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Invoke-Icacls @($env:SMD_TEST_ACL_INSTALL, '/inheritance:r', '/grant:r', ('*' + $owner + ':(OI)(CI)F'), '*S-1-5-18:(OI)(CI)F')
Grant-WebView2RuntimeAccess -InstallDir $env:SMD_TEST_ACL_INSTALL
if ((Get-Acl -LiteralPath $env:SMD_TEST_ACL_SECRET).Sddl -ne $before) { throw 'Secret ACL changed' }
$paths = $env:SMD_TEST_ACL_PATHS | ConvertFrom-Json
$output = @{}
foreach ($entry in $paths.PSObject.Properties) {
    $rules = (Get-Acl -LiteralPath $entry.Value).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
    $output[$entry.Name] = @($rules | Where-Object { $_.IdentityReference.Value -in @('S-1-15-2-1','S-1-15-2-2') } | ForEach-Object {
        @{sid=$_.IdentityReference.Value; rights=[int]$_.FileSystemRights; inheritance=[int]$_.InheritanceFlags; type=$_.AccessControlType.ToString()}
    })
}
$output | ConvertTo-Json -Depth 4 -Compress
""",
        install,
        SMD_TEST_ACL_SECRET=str(secret),
        SMD_TEST_ACL_PATHS=json.dumps({name: str(path) for name, path in paths.items()}),
    )
    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)
    for name, rules in entries.items():
        if name.endswith("/service"):
            assert rules == []
            continue
        assert {rule["sid"] for rule in rules} == PACKAGE_SIDS
        assert all(rule["type"] == "Allow" for rule in rules)
        if name.endswith(("/runtime", "/browser")):
            assert all(rule["rights"] & 0x200A9 == 0x200A9 for rule in rules)  # ReadAndExecute
            assert all(rule["rights"] & 0x116 == 0 for rule in rules)  # no write/create/append
        else:
            assert all(rule["rights"] & 0xFFFF == 0x20 and rule["inheritance"] == 0 for rule in rules)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows icacls and NTFS security descriptors")
def test_explicit_ancestor_traverse_denial_stops_runtime_acl_setup(tmp_path):
    parent = tmp_path / "restricted"
    install = parent / "program"
    (install / "versions/0.3.0-rc.2/webview2").mkdir(parents=True)
    result = execute(
        r"""
$parent = Split-Path $env:SMD_TEST_ACL_INSTALL -Parent
$acl = Get-Acl -LiteralPath $parent
$sid = [System.Security.Principal.SecurityIdentifier]::new('S-1-15-2-2')
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new($sid, [System.Security.AccessControl.FileSystemRights]::Traverse, [System.Security.AccessControl.AccessControlType]::Deny)
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $parent -AclObject $acl
Grant-WebView2RuntimeAccess -InstallDir $env:SMD_TEST_ACL_INSTALL
""",
        install,
    )
    assert result.returncode != 0
    assert "traverse" in result.stderr.lower()
