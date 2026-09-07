"""Execute the production cleanup stop fragment against deterministic SCM races.

Windows uses its real PowerShell 5.1 wrapper. An optional local pwsh can exercise
the same language/control-flow cases without creating or stopping any service.
"""

import base64
import json
import os
import shutil
import subprocess

import pytest
from smd_bench import windows

PROBE = r"""
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
public class StopProbe {
    public string Status = "Running";
    public string Actual;
    public string Scenario;
    public int StopCalls;
    public int WaitCalls;
    public double WaitSeconds;
    public bool Disposed;
    public StopProbe(string scenario) {
        Scenario = scenario;
        Actual = scenario == "stopped" ? "Stopped" :
            (scenario == "pending" || scenario == "timeout" ? "StopPending" : "Running");
        if (scenario == "stopped") Status = "Stopped";
    }
    public void Refresh() { Status = Actual; }
    private Exception StopError(int code) {
        // .NET Framework ServiceController.Stop wraps the native exception.
        return new InvalidOperationException("controlled stop failure", new Win32Exception(code));
    }
    public void Stop() {
        StopCalls++;
        if (Scenario == "race") { Actual = "StopPending"; throw StopError(1061); }
        if (Scenario == "race_stopped") { Actual = "Stopped"; throw StopError(1061); }
        if (Scenario == "wrong_state") { Actual = "StartPending"; throw StopError(1061); }
        if (Scenario == "denied") { Actual = "StopPending"; throw StopError(5); }
        if (Scenario == "text_only") { Actual = "StopPending"; throw new InvalidOperationException("1061"); }
        if (Actual == "StopPending") throw StopError(1061);
        Actual = "StopPending";
    }
    public void WaitForStatus(string desired, TimeSpan timeout) {
        WaitCalls++;
        WaitSeconds = timeout.TotalSeconds;
        if (Scenario == "timeout") throw new TimeoutException("controlled timeout");
        Actual = Status = desired;
    }
    public void Dispose() { Disposed = true; }
}
'@
$controller = New-Object StopProbe($p.scenario)
function Get-Service { param($Name) return $controller }
$finished=$false; $errorType=$null; $nativeCode=$null
try {
"""

RESULT = r"""
    $finished=$true
} catch {
    $cause=$_.Exception.GetBaseException()
    $errorType=$cause.GetType().Name
    if($cause -is [ComponentModel.Win32Exception]) {$nativeCode=$cause.NativeErrorCode}
}
@{finished=$finished;state=$controller.Actual;disposed=$controller.Disposed;
  stops=$controller.StopCalls;waits=$controller.WaitCalls;wait_seconds=$controller.WaitSeconds;
  error_type=$errorType;native_code=$nativeCode;stage=$benchStage}|ConvertTo-Json -Compress
"""


def cleanup_stop_fragment(tmp_path, monkeypatch):
    scripts = []
    with monkeypatch.context() as patch:
        patch.setattr(windows, "powershell", lambda script, *args, **kwargs: scripts.append(script))
        windows.remove_registration(*(tmp_path / name for name in ("service", "updater", "desktop", "install", "data")))
    # Execute the actual region before DeleteService; do not reproduce its logic
    # in a Python model or merely assert that the source contains a condition.
    return scripts[0].split("if($service){", 1)[1].split("$benchStage='service_delete';", 1)[0]


def execute_fragment(script, scenario):
    if os.name == "nt":
        return windows.powershell(script, {"scenario": scenario})
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("requires Windows PowerShell 5.1 or an optional local pwsh")
    preamble = (
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; $p=$env:SMD_BENCH_INPUT|ConvertFrom-Json;\n"
    )
    encoded = base64.b64encode((preamble + script).encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        env={**os.environ, "SMD_BENCH_INPUT": json.dumps({"scenario": scenario})},
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout.decode("utf-8-sig"))


@pytest.mark.parametrize(
    "scenario,stops,waits",
    [("pending", 0, 1), ("race", 1, 1), ("race_stopped", 1, 1), ("running", 1, 1), ("stopped", 0, 0)],
)
def test_cleanup_waits_for_pending_stop_without_reissuing_control(tmp_path, monkeypatch, scenario, stops, waits):
    result = execute_fragment(PROBE + cleanup_stop_fragment(tmp_path, monkeypatch) + RESULT, scenario)
    assert result["finished"] is True and result["state"] == "Stopped"
    assert result["stops"] == stops and result["waits"] == waits
    assert result["disposed"] is True
    if waits:
        assert result["wait_seconds"] == 60


@pytest.mark.parametrize(
    "scenario,error_type,code,waits",
    [
        ("wrong_state", "Win32Exception", 1061, 0),
        ("denied", "Win32Exception", 5, 0),
        ("text_only", "InvalidOperationException", None, 0),
        ("timeout", "TimeoutException", None, 1),
    ],
)
def test_cleanup_does_not_delete_after_unproven_or_timed_out_stop(
    tmp_path, monkeypatch, scenario, error_type, code, waits
):
    result = execute_fragment(PROBE + cleanup_stop_fragment(tmp_path, monkeypatch) + RESULT, scenario)
    assert result["finished"] is False and result["state"] != "Stopped"
    assert result["error_type"] == error_type and result["native_code"] == code
    assert result["waits"] == waits and result["disposed"] is True
    if waits:
        assert result["wait_seconds"] == 60 and result["stage"] == "service_wait"
