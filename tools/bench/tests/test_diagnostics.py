import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from smd_bench.diagnostics import archive_logs, failure_details
from smd_bench.ownership import claim
from windows_capabilities import requires_admin_owner, requires_symlink_privilege


def private_run(tmp_path):
    run_id = "a" * 32
    private, data = tmp_path / "private", tmp_path / "data"
    claim(private, run_id)
    private.chmod(0o700)
    claim(data, run_id)
    return run_id, private, data


@requires_admin_owner
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


def test_shipped_maintenance_frame_keeps_location_without_private_context():
    from smd_bench.diagnostics import public_frames

    private = "private-maintenance-key-884422"
    code = "def offline_confirmation():\n    secret = payload\n    raise TimeoutError(secret)\noffline_confirmation()"
    try:
        exec(compile(code, rf"C:\\private\\{private}\\maintenance.py", "exec"), {"payload": private})
    except TimeoutError as error:
        frames = public_frames(error)
    assert frames[-1] == {"file": "maintenance.py", "function": "offline_confirmation", "line": 3}
    assert all(set(frame) == {"file", "function", "line"} for frame in frames)
    assert private not in json.dumps(frames) and "private" not in json.dumps(frames)


@requires_admin_owner
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


@requires_symlink_privilege
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


@requires_admin_owner
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


@requires_admin_owner
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
    assert installation.cleanup_stage == "logs"
    assert installation.install.exists() and (logs / "service.log").read_text() == "keep this evidence"


def installation_with_database(tmp_path, monkeypatch):
    from smd_bench.installation import Installation

    installation = Installation(
        "d" * 32, {"program_files": str(tmp_path / "pf"), "program_data": str(tmp_path / "pd")}, "0.3.0-rc.5"
    )
    for path in (installation.private, installation.install, installation.data):
        path.parent.mkdir(parents=True, exist_ok=True)
        claim(path, installation.run_id)
    installation.private.chmod(0o700)
    database = installation.data / "db/smd.db"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as source:
        source.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        source.execute("INSERT INTO evidence VALUES ('preserved synthetic experiment')")
        source.commit()
    monkeypatch.setattr("smd_bench.windows.remove_registration", lambda *_args: None)
    monkeypatch.setattr("smd_bench.windows.service_info", lambda: None)
    monkeypatch.setattr("smd_bench.windows.firewall", lambda *_args, **_kwargs: None)
    return installation


@requires_admin_owner
def test_cleanup_closes_both_real_backup_connections_before_removal(tmp_path, monkeypatch):
    import shutil

    installation = installation_with_database(tmp_path, monkeypatch)
    connections = []
    connect, remove = sqlite3.connect, shutil.rmtree

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)  # Keep strong refs: garbage collection must not make this pass.
        return connection

    def checked_remove(path, *args, **kwargs):
        assert len(connections) == 2
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(shutil, "rmtree", checked_remove)
    try:
        installation.cleanup()
    finally:
        for connection in connections:
            connection.close()
    assert not installation.install.exists() and not installation.data.exists()
    with closing(connect(installation.private / "host-archive.sqlite")) as backup:
        assert backup.execute("SELECT value FROM evidence").fetchone() == ("preserved synthetic experiment",)
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows SQLite file-sharing and real directory removal")
@requires_admin_owner
def test_windows_cleanup_releases_database_for_real_removal_and_retains_backup(tmp_path, monkeypatch):
    installation = installation_with_database(tmp_path, monkeypatch)
    installation.cleanup()  # Real sqlite connections, backup, ACL checks and shutil.rmtree.
    assert not installation.install.exists() and not installation.data.exists()
    with closing(sqlite3.connect(installation.private / "host-archive.sqlite")) as backup:
        assert backup.execute("SELECT value FROM evidence").fetchone() == ("preserved synthetic experiment",)
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_public_os_diagnostic_excludes_secret_path_and_message():
    error = PermissionError(13, "private token must not be published", "secret-path.db")
    error.winerror = 32
    diagnostic = failure_details(error)
    assert diagnostic["os_error"] == {"errno": 13, "winerror": 32}
    assert "private token" not in json.dumps(diagnostic) and "secret-path" not in json.dumps(diagnostic)


def test_failed_real_database_backup_retains_original_and_network_isolation(tmp_path, monkeypatch):
    installation = installation_with_database(tmp_path, monkeypatch)
    database = installation.data / "db/smd.db"
    database.write_bytes(b"invalid synthetic database")
    monkeypatch.setattr(
        "smd_bench.windows.firewall", lambda *_args, **_kwargs: pytest.fail("isolation removed after failed backup")
    )
    with pytest.raises(sqlite3.DatabaseError):
        installation.cleanup()
    assert installation.cleanup_stage == "backup"
    assert installation.install.exists() and database.read_bytes() == b"invalid synthetic database"


def test_shutdown_evidence_extracts_fixed_stages_after_latest_start_only(tmp_path):
    from smd_bench.diagnostics import service_shutdown_evidence

    run_id, _private, data = private_run(tmp_path)
    logs = data / "logs"
    logs.mkdir()
    log = logs / "service.log"
    raw = (
        "INFO:uvicorn.error:Shutting down\n"
        "INFO:uvicorn.error:Application shutdown complete.\n"
        "INFO:uvicorn.error:Started server process [1234]\n"
        "INFO:backend.stdout:private PSK, password and token must stay here\n"
        "INFO:uvicorn.error:Shutting down\n"
        "INFO:uvicorn.error:Waiting for connections to close. (CTRL+C to force quit)\n"
        "ERROR:uvicorn.error:Cancel 2 running task(s), timeout graceful shutdown exceeded\n"
        "INFO:uvicorn.error:Waiting for application shutdown.\n"
        "INFO:uvicorn.error:Application shutdown complete. secret suffix\n"
    ).encode()
    log.write_bytes(raw)
    result = service_shutdown_evidence(data, run_id)
    assert result == {
        "collection": "ok",
        "source": "service_log_tail",
        "stages": ["requested", "connections", "requests_cancelled", "application"],
    }
    assert log.read_bytes() == raw and "secret" not in json.dumps(result) and "1234" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["missing", "foreign", pytest.param("link", marks=requires_symlink_privilege)])
def test_shutdown_evidence_refuses_unowned_or_unavailable_logs(tmp_path, mode):
    from smd_bench.diagnostics import service_shutdown_evidence

    run_id, _private, data = private_run(tmp_path)
    if mode != "missing":
        logs = data / "logs"
        logs.mkdir()
        if mode == "link":
            foreign = tmp_path / "foreign.log"
            foreign.write_text("INFO:uvicorn.error:Shutting down\n", encoding="utf-8")
            (logs / "service.log").symlink_to(foreign)
        else:
            (logs / "service.log").write_text("INFO:uvicorn.error:Shutting down\n", encoding="utf-8")
            run_id = "b" * 32
    assert service_shutdown_evidence(data, run_id) == {"collection": "unavailable"}


def test_shutdown_evidence_is_bounded_and_never_reconstructs_a_truncated_line(tmp_path):
    from smd_bench.diagnostics import service_shutdown_evidence

    run_id, _private, data = private_run(tmp_path)
    logs = data / "logs"
    logs.mkdir()
    raw = b"private-prefix" * 50000 + b"INFO:uvicorn.error:Shutting down\n"
    raw += b"INFO:uvicorn.error:Waiting for background tasks to complete. (CTRL+C to force quit)\n"
    (logs / "service.log").write_bytes(raw)
    assert service_shutdown_evidence(data, run_id) == {
        "collection": "ok",
        "source": "service_log_tail",
        "stages": ["background_tasks"],
    }
