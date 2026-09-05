"""安装/恢复共用内置Windows PowerShell，不能继承PS7或用户模块搜索路径。"""

import os
import subprocess
from pathlib import Path

import pytest
from smd_desktop import windows_powershell as ps


def test_child_uses_system_powershell_and_discards_inherited_module_paths(tmp_path, monkeypatch):
    system = tmp_path / "System32"
    monkeypatch.setattr(ps, "system_directory", lambda: system)
    monkeypatch.setenv("PSModulePath", "untrusted;PowerShell7/Modules")
    monkeypatch.setenv("WinPSModulePath", "untrusted-legacy")
    original = dict(os.environ)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    ps.run(["-ExecutionPolicy", "Bypass", "-File", "C:/Program Files/SMD/configure-acl.ps1"], check=True)
    command, options = calls[0]
    assert command[0] == str(system / "WindowsPowerShell/v1.0/powershell.exe")
    assert command[1:3] == ["-NoProfile", "-NonInteractive"]
    assert options["env"]["PSModulePath"] == str(system / "WindowsPowerShell/v1.0/Modules")
    assert not any(key.lower() == "winpsmodulepath" for key in options["env"])
    assert options["check"] is True
    assert dict(os.environ) == original


@pytest.mark.skipif(os.name != "nt", reason="requires built-in Windows PowerShell 5.1")
@pytest.mark.parametrize("script", ["configure-acl.ps1", "configure-recovery.ps1"])
def test_fixed_script_loads_builtin_modules_despite_incompatible_inherited_modules(tmp_path, script):
    # Reproduce PS7's incompatible same-name manifests in a Python child process.
    # Use raw powershell.exe here to exercise the script's own bootstrap, too.
    for name in ("Microsoft.PowerShell.Security", "Microsoft.PowerShell.Utility", "ScheduledTasks"):
        directory = tmp_path / "modules" / name
        directory.mkdir(parents=True)
        (directory / (name + ".psd1")).write_text(
            "@{ModuleVersion='99.0';PowerShellVersion='7.0';RootModule='incompatible.psm1'}", encoding="ascii"
        )
        (directory / "incompatible.psm1").write_text("throw 'untrusted module executed'", encoding="ascii")
    command = r"""
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5) { throw 'Requires Windows PowerShell 5.1' }
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SMD_TEST_PS_SCRIPT, [ref]$tokens, [ref]$errors)
$initializers = @()
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) { break }
    if ($statement -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $statement.Left.Extent.Text -eq '$Action') { break }
    $initializers += $statement.Extent.Text
}
. ([scriptblock]::Create(($initializers -join "`n")))
if ($env:PSModulePath -ne ($PSHOME + '\Modules')) { throw 'Inherited module search path remains' }
if ($env:SMD_TEST_PS_SCRIPT.EndsWith('configure-acl.ps1')) {
    $null = Get-Acl -LiteralPath $env:TEMP
    $module = (Get-Command Get-Acl).Module
} else {
    # Construct an in-memory action only: do not register or alter a real task.
    $null = New-ScheduledTaskAction -Execute 'cmd.exe'
    $null = New-TimeSpan -Minutes 10
    $module = (Get-Command New-ScheduledTaskAction).Module
}
if (-not $module.Path.StartsWith($PSHOME, [StringComparison]::OrdinalIgnoreCase)) {
    throw ('Unexpected module source: ' + $module.Path)
}
"""
    result = subprocess.run(
        [
            str(ps.system_directory() / "WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        env={
            **os.environ,
            "PSModulePath": str(tmp_path / "modules"),
            "SMD_TEST_PS_SCRIPT": str(Path(__file__).resolve().parents[2] / "deploy/windows" / script),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
