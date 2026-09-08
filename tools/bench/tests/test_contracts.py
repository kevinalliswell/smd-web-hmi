from pathlib import Path

import pytest
from pydantic import ValidationError
from smd_bench.contracts import DriverAction, Manifest, validate_run_id
from smd_bench.ownership import assert_owned_path, validate_marker


def test_run_id_never_becomes_a_path_or_powershell_expression():
    assert validate_run_id("a" * 32) == "a" * 32
    for value in ("../other", "a" * 31, "A" * 32, "$(whoami)", "a" * 32 + "/child"):
        with pytest.raises(ValueError):
            validate_run_id(value)


def test_manifest_rejects_unbound_package_identity():
    with pytest.raises(ValidationError):
        Manifest.model_validate({"schema_version": 1, "platform": "windows-x64", "version": "../bad"})


def test_driver_cannot_advance_transport_clock_or_accept_free_code():
    for body in ({"id": "x", "action": "eval", "code": "pass"}, {"id": "x", "action": "tick", "ms": 600000}):
        with pytest.raises(ValidationError):
            DriverAction.model_validate(body)
    assert DriverAction.model_validate({"id": "x", "action": "sample", "values": {"burden_mc": 600000}})


def test_cleanup_requires_exact_marker_and_refuses_symlink(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    marker = {"schema_version": 1, "run_id": "a" * 32, "purpose": "SmdBench isolated installation"}
    assert validate_marker(marker, "a" * 32) is None
    with pytest.raises(ValueError):
        validate_marker({**marker, "run_id": "b" * 32}, "a" * 32)
    assert_owned_path(root, root)
    with pytest.raises(ValueError):
        assert_owned_path(tmp_path, root)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        assert_owned_path(linked, root)


def test_cleanup_refuses_reparse_point_within_owned_tree(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        assert_owned_path(root, root, recursive=True)


def test_prepare_rejects_overlapping_evidence_before_claiming(tmp_path, monkeypatch):
    from smd_bench.installation import Installation

    installation = Installation(
        "a" * 32, {"program_files": str(tmp_path / "pf"), "program_data": str(tmp_path / "pd")}, "0.3.0-rc.5"
    )
    monkeypatch.setattr(
        "smd_bench.windows.secure_directory", lambda path: pytest.fail("mutation before path admission")
    )
    with pytest.raises(ValueError, match="overlap"):
        installation.prepare(installation.data / "evidence", None, "0" * 64)
    assert not installation.private.exists()


def test_job_assignment_failure_terminates_suspended_owned_process(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from smd_bench.installation import checked_process

    events = []

    def assignment(*_):
        raise RuntimeError("job assignment refused")

    modules = {
        "win32api": SimpleNamespace(
            CloseHandle=lambda h: events.append(("close", h)),
            TerminateProcess=lambda p, c: events.append(("terminate", p)),
            SetLastError=lambda _: None,
            GetLastError=lambda: 0,
        ),
        "win32con": SimpleNamespace(CREATE_SUSPENDED=1, CREATE_NO_WINDOW=2),
        "win32event": SimpleNamespace(
            WAIT_OBJECT_0=0, WaitForSingleObject=lambda p, t: events.append(("wait", p)) or 0
        ),
        "win32job": SimpleNamespace(
            CreateJobObject=lambda *_: 3,
            QueryInformationJobObject=lambda *_: {"BasicLimitInformation": {"LimitFlags": 0}},
            SetInformationJobObject=lambda *_: None,
            AssignProcessToJobObject=assignment,
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=4,
        ),
        "win32process": SimpleNamespace(
            CreateProcess=lambda *_: (1, 2, 100, 101),
            STARTUPINFO=lambda: None,
            ResumeThread=lambda _: pytest.fail("uncontained child resumed"),
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(RuntimeError, match="assignment"):
        checked_process(tmp_path / "installer.exe", [], tmp_path)
    assert events[0] == ("terminate", 1)
    assert events.index(("wait", 1)) < events.index(("close", 1))
