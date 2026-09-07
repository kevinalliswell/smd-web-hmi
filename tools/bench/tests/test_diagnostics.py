import json
from pathlib import Path

import pytest
from smd_bench.diagnostics import archive_logs, failure_details
from smd_bench.ownership import claim


def private_run(tmp_path):
    run_id = "a" * 32
    private, data = tmp_path / "private", tmp_path / "data"
    claim(private, run_id)
    private.chmod(0o700)
    claim(data, run_id)
    return run_id, private, data


def test_public_frames_exclude_messages_source_and_secret_paths(tmp_path):
    run_id, private, _data = private_run(tmp_path)
    payload = "super-private-token-48625077"
    try:
        exec(compile("raise RuntimeError(payload)", f"/private/{payload}/injected.py", "exec"), {"payload": payload})
    except RuntimeError as error:
        details = failure_details(error, private=private, run_id=run_id)
    public = json.dumps(details)
    assert payload not in public and "/private/" not in public
    assert details["type"] == "RuntimeError" and details["private_trace_saved"] is True
    assert details["frames"] and all(set(frame) == {"file", "function", "line"} for frame in details["frames"])
    assert all(Path(frame["file"]).name == frame["file"] for frame in details["frames"])
    traces = list(private.glob("failure-*.traceback.txt"))
    assert len(traces) == 1 and payload in traces[0].read_text(encoding="utf-8")


def test_log_archive_is_private_and_only_copies_owned_service_logs(tmp_path):
    run_id, private, data = private_run(tmp_path)
    logs = data / "logs"
    logs.mkdir()
    (logs / "service.log").write_text("private service failure")
    (logs / "updater.log.1").write_text("private updater failure")
    (logs / "unrelated.txt").write_text("do not collect")
    archived = archive_logs(private, data, run_id)
    assert {p.name for p in archived.iterdir()} == {"service.log", "updater.log.1"}
    assert (archived / "service.log").read_text() == "private service failure"
    assert (logs / "service.log").exists()


def test_log_archive_refuses_link_instead_of_following_it(tmp_path):
    run_id, private, data = private_run(tmp_path)
    logs = data / "logs"
    logs.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("not owned")
    (logs / "service.log").symlink_to(outside)
    with pytest.raises(ValueError, match="reparse"):
        archive_logs(private, data, run_id)
    assert outside.read_text() == "not owned"


def test_owned_cleanup_archives_logs_before_removing_installation(tmp_path, monkeypatch):
    from smd_bench.installation import Installation

    run_id = "b" * 32
    folders = {"program_files": str(tmp_path / "pf"), "program_data": str(tmp_path / "pd")}
    installation = Installation(run_id, folders, "0.3.0-rc.5")
    for path in (installation.private, installation.install, installation.data):
        path.parent.mkdir(parents=True, exist_ok=True)
        claim(path, run_id)
    installation.private.chmod(0o700)
    logs = installation.data / "logs"
    logs.mkdir()
    (logs / "updater.log").write_text("private failed installer evidence")
    monkeypatch.setattr("smd_bench.windows.remove_registration", lambda *_args: None)
    monkeypatch.setattr("smd_bench.windows.service_info", lambda: None)
    monkeypatch.setattr("smd_bench.windows.firewall", lambda *_args, **_kwargs: None)
    installation.cleanup()
    assert not installation.install.exists() and not installation.data.exists()
    archived = list(installation.private.glob("installation-logs-*/updater.log"))
    assert len(archived) == 1 and archived[0].read_text() == "private failed installer evidence"


def test_log_preservation_failure_keeps_original_data(tmp_path, monkeypatch):
    from smd_bench.installation import Installation

    run_id = "c" * 32
    installation = Installation(
        run_id, {"program_files": str(tmp_path / "pf"), "program_data": str(tmp_path / "pd")}, "0.3.0-rc.5"
    )
    for path in (installation.private, installation.install, installation.data):
        path.parent.mkdir(parents=True, exist_ok=True)
        claim(path, run_id)
    installation.private.chmod(0o700)
    logs = installation.data / "logs"
    logs.mkdir()
    (logs / "service.log").write_text("keep this evidence")
    monkeypatch.setattr("smd_bench.windows.remove_registration", lambda *_args: None)
    monkeypatch.setattr("smd_bench.windows.service_info", lambda: None)

    def failed_copy(*_args):
        raise OSError("injected copy failure")

    monkeypatch.setattr("smd_bench.diagnostics.shutil.copyfile", failed_copy)
    monkeypatch.setattr(
        "smd_bench.windows.firewall",
        lambda *_args, **_kwargs: pytest.fail("network isolation removed after failed preservation"),
    )
    with pytest.raises(OSError, match="copy failure"):
        installation.cleanup()
    assert installation.install.exists() and (logs / "service.log").read_text() == "keep this evidence"
