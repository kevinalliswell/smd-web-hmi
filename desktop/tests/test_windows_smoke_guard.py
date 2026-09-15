"""冒烟入口即使拿到路径参数，也不能在普通主机或自托管runner执行安装。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/smoke-windows.ps1"


def test_install_smoke_script_is_part_of_release_tools():
    assert SCRIPT.is_file()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell runtime required for refusal-path test")
@pytest.mark.parametrize("actions,runner", [("false", "github-hosted"), ("true", "self-hosted")])
def test_install_smoke_refuses_unowned_hosts_before_resolving_package(tmp_path, actions, runner):
    sentinel = tmp_path / "untouched.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-Installer",
            str(tmp_path / "does-not-exist.exe"),
            "-Manifest",
            str(tmp_path / "does-not-exist.json"),
            "-Evidence",
            str(tmp_path / "evidence.json"),
        ],
        env={**os.environ, "GITHUB_ACTIONS": actions, "RUNNER_ENVIRONMENT": runner},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "disposable GitHub-hosted Windows runner" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (tmp_path / "evidence.json").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None, reason="requires native Windows PowerShell 7 CIM types"
)
@pytest.mark.parametrize(
    "mode,failures,expected_calls,expected_service",
    [
        ("present", 1, 2, ["SmdHmi"]),
        ("present", 2, 3, ["SmdHmi"]),
        ("absent", 1, 2, []),
        ("absent", 2, 3, []),
        ("partial_output_then_absent", 1, 2, []),
        ("cim_timeout", 3, 3, None),
        ("cim_access_denied", 3, 3, None),
        ("programming_error", 1, 1, None),
    ],
)
def test_smoke_service_query_retries_only_cim_errors_and_never_infers_absence(
    mode, failures, expected_calls, expected_service
):
    command = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module CimCmdlets -ErrorAction Stop
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SMD_TEST_SMOKE_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Cannot parse the actual smoke script' }
$function = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-SmokeService'
}, $true)
if (-not $function) { throw 'Actual smoke service helper was not found' }
. ([scriptblock]::Create($function.Extent.Text))
$script:Queries = [Collections.Generic.List[object]]::new()
$script:Delays = [Collections.Generic.List[int]]::new()
$script:LastFailure = $null
function Get-CimInstance {
    [CmdletBinding()]
    param([Parameter(Position=0)][string]$ClassName, [string]$Filter, [uint32]$OperationTimeoutSec)
    $script:Queries.Add([ordered]@{
        class_name = $ClassName; filter = $Filter; timeout = $OperationTimeoutSec
        error_action = [string]$PSBoundParameters['ErrorAction']
    })
    if ($env:SMD_TEST_CIM_MODE -eq 'programming_error') {
        $script:LastFailure = [InvalidOperationException]::new('programming-error')
        throw $script:LastFailure
    }
    if ($script:Queries.Count -le [int]$env:SMD_TEST_CIM_FAILURES) {
        if ($env:SMD_TEST_CIM_MODE -eq 'partial_output_then_absent') {
            Write-Output ([pscustomobject]@{ Name = 'SmdHmi'; State = 'Stopped' })
        }
        $script:LastFailure = [Microsoft.Management.Infrastructure.CimException]::new(
            ($env:SMD_TEST_CIM_MODE + '-' + $script:Queries.Count))
        throw $script:LastFailure
    }
    if ($env:SMD_TEST_CIM_MODE -eq 'present') { [pscustomobject]@{ Name = 'SmdHmi'; State = 'Stopped' } }
}
function Start-Sleep { param([int]$Milliseconds); $script:Delays.Add($Milliseconds) }
$service = @(); $caught = $null
try { $service = @(Get-SmokeService) } catch { $caught = $_ }
$outcome = 'returned'; $errorType = $null; $errorMessage = $null; $sameError = $false
if ($caught) {
    $outcome = 'error'; $errorType = $caught.Exception.GetType().FullName; $errorMessage = $caught.Exception.Message
    $sameError = [object]::ReferenceEquals($caught.Exception, $script:LastFailure)
}
[ordered]@{
    outcome = $outcome; service_names = @($service | ForEach-Object { $_.Name })
    queries = $script:Queries.ToArray(); delays = $script:Delays.ToArray()
    error_type = $errorType; error_message = $errorMessage; same_error = $sameError
} | ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        env={
            **os.environ,
            "SMD_TEST_SMOKE_SCRIPT": str(SCRIPT),
            "SMD_TEST_CIM_MODE": mode,
            "SMD_TEST_CIM_FAILURES": str(failures),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert (
        evidence["queries"]
        == [{"class_name": "Win32_Service", "filter": "Name='SmdHmi'", "timeout": 5, "error_action": "Stop"}]
        * expected_calls
    )
    assert evidence["delays"] == [100] * (expected_calls - 1)
    if expected_service is not None:
        assert evidence["outcome"] == "returned"
        assert evidence["service_names"] == expected_service
    else:
        assert evidence["outcome"] == "error"
        assert evidence["service_names"] == []
        assert evidence["same_error"] is True
        if mode == "programming_error":
            assert evidence["error_type"] == "System.InvalidOperationException"
            assert evidence["error_message"] == "programming-error"
        else:
            assert evidence["error_type"] == "Microsoft.Management.Infrastructure.CimException"
            assert evidence["error_message"] == f"{mode}-3"
