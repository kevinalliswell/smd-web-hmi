import base64
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from smd_bench import windows
from smd_bench.diagnostics import failure_details
from smd_desktop import windows_powershell
from windows_capabilities import requires_admin_owner


def test_bench_child_isolates_inherited_powershell_modules(tmp_path, monkeypatch):
    environment = {
        "SystemRoot": str(tmp_path / "untrusted-system-root"),
        "PSModulePath": "untrusted-pwsh-modules",
        "WinPSModulePath": "untrusted-legacy-modules",
        "retained": "value",
    }
    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt", environ=environment))
    system = tmp_path / "actual-system32"
    monkeypatch.setattr(windows_powershell, "system_directory", lambda: system)
    calls = []

    def child(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0, b'{"ok":true}', b"")

    monkeypatch.setattr(subprocess, "run", child)
    assert windows.powershell("@{ok=$true}|ConvertTo-Json", {"label": "test value"}, timeout=7) == {"ok": True}
    command, options = calls[0]
    assert command[0] == str(system / "WindowsPowerShell/v1.0/powershell.exe")
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert "@{ok=$true}" in base64.b64decode(command[4]).decode("utf-16-le")
    assert options["env"]["PSModulePath"] == str(system / "WindowsPowerShell/v1.0/Modules")
    assert "WinPSModulePath" not in options["env"]
    assert json.loads(options["env"]["SMD_BENCH_INPUT"]) == {"label": "test value"}
    assert options["timeout"] == 7 and options["env"]["retained"] == "value"
    assert environment["PSModulePath"] == "untrusted-pwsh-modules"


@pytest.mark.parametrize("category", ["PermissionDenied", "secret-token-in-category"])
def test_windows_failure_exposes_only_fixed_diagnostic_fields(tmp_path, monkeypatch, category):
    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(tmp_path)}))
    monkeypatch.setattr(windows_powershell, "system_directory", lambda: tmp_path)
    secret = "do-not-publish-private-token"
    stderr = (secret + "\nSMD_BENCH_ERROR:" + json.dumps({"stage": "acl_set", "category": category})).encode()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", stderr))
    with pytest.raises(RuntimeError) as caught:
        windows.powershell("throw 'fixed test error'")
    details = failure_details(caught.value)
    assert details["windows_operation"] == {
        "code": "powershell_failed",
        "stage": "acl_set",
        "category": category if category == "PermissionDenied" else "unknown",
        "exit_code": 1,
    }
    assert secret not in json.dumps(details) and "secret-token" not in json.dumps(details)
    assert secret not in str(caught.value)


@pytest.mark.parametrize("invalid", ["private-secret", True, ["private-secret"], 2**40])
def test_cleanup_diagnostics_accept_only_known_types_and_numeric_os_codes(invalid):
    diagnostic = windows.WindowsOperationError(
        1,
        (
            "SMD_BENCH_ERROR:"
            + json.dumps(
                {
                    "stage": "service_wait",
                    "category": "NotSpecified",
                    "exception_type": invalid,
                    "hresult": invalid,
                    "native_error": invalid,
                    "message": "private-secret",
                }
            )
        ).encode(),
    ).diagnostic
    assert diagnostic == {
        "code": "powershell_failed",
        "stage": "service_wait",
        "category": "NotSpecified",
        "exit_code": 1,
    }
    assert "private-secret" not in json.dumps(diagnostic)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows PowerShell exception serialization")
def test_real_service_wait_diagnostic_does_not_publish_exception_message():
    with pytest.raises(windows.WindowsOperationError) as caught:
        windows.powershell("$benchStage='service_wait'; throw [TimeoutException]::new('private-secret')")
    diagnostic = caught.value.diagnostic
    assert diagnostic["stage"] == "service_wait"
    assert diagnostic["exception_type"] == "TimeoutException"
    assert type(diagnostic["hresult"]) is int
    assert "private-secret" not in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows PowerShell 5.1 ACL behavior")
@requires_admin_owner
def test_real_bench_acl_ignores_incompatible_parent_modules(tmp_path, monkeypatch):
    for name in ("Microsoft.PowerShell.Security", "Microsoft.PowerShell.Utility"):
        module = tmp_path / "modules" / name
        module.mkdir(parents=True)
        (module / (name + ".psd1")).write_text(
            "@{ModuleVersion='99.0';PowerShellVersion='7.0';RootModule='incompatible.psm1'}", encoding="ascii"
        )
        (module / "incompatible.psm1").write_text("throw 'untrusted module executed'", encoding="ascii")
    monkeypatch.setenv("PSModulePath", str(tmp_path / "modules"))
    monkeypatch.setenv("WinPSModulePath", str(tmp_path / "modules"))
    private = tmp_path / "private"
    private.mkdir()
    windows.secure_directory(private)
    result = windows.powershell(
        "@{version=$PSVersionTable.PSVersion.Major;protected=(Get-Acl -LiteralPath $p.path).AreAccessRulesProtected;"
        "module=(Get-Command Get-Acl).Module.Path;home=$PSHOME}|ConvertTo-Json -Compress",
        {"path": str(private)},
    )
    assert result["version"] == 5 and result["protected"] is True
    assert result["module"].lower().startswith(result["home"].lower() + "\\")
    with pytest.raises(windows.WindowsOperationError) as caught:
        windows.secure_directory(tmp_path / "absent-directory")
    assert caught.value.diagnostic["stage"] == "acl_set"
    assert caught.value.diagnostic["category"] == "ObjectNotFound"
