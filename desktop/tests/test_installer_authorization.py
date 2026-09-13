"""Installer authorization never needs an application password or forged API token."""

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path

import pytest
from smd_desktop import installer_authorization as auth
from smd_desktop.storage import atomic_json


class Platform:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def service_stopped(self):
        return self.stopped


@pytest.fixture
def installation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    db = tmp_path / "独立数据库.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE test_session (end_time TEXT)")
        connection.execute("CREATE TABLE v2_operation (status TEXT, reconciled BOOLEAN)")
    (data / "config/service.env").write_text(f'SMD_DB_PATH="{db}"\nHOSTCOMM_DEVICE_ID="board"\n')
    monkeypatch.setattr(auth, "admin_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(auth, "protected_json", atomic_json)
    monkeypatch.setattr(auth, "single_instance", lambda *args: nullcontext())
    return data, db, Platform()


def test_legacy_paired_requires_physical_confirmation_before_stop(installation):
    data, db, platform = installation
    with pytest.raises(auth.ConfirmationRequired):
        auth.authorize(data, platform, current="0.3.0-rc.4", target="0.3.0", package_sha256="a" * 64)
    assert not platform.stopped
    assert not (data / "maintenance.json").exists()


def test_legacy_confirmation_keeps_unknown_experiment_and_binds_actual_database(installation):
    data, db, platform = installation
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO test_session VALUES (NULL)")
    permit = auth.authorize(
        data,
        platform,
        current="0.3.0-rc.4",
        target="0.3.0",
        package_sha256="a" * 64,
        physical_shutdown_confirmed=True,
    )
    assert platform.stopped
    assert permit["db_path"] == str(db)
    assert permit["issuer"] == "windows_installer"
    assert permit["operation"] == "upgrade"
    assert permit["package_sha256"] == "a" * 64
    assert permit["state"] == "claimed"
    assert "token" not in permit
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT end_time FROM test_session").fetchone() == (None,)


def test_unpaired_legacy_with_unknown_run_still_requires_confirmation(installation):
    data, db, platform = installation
    (data / "config/service.env").write_text(f'SMD_DB_PATH="{db}"\nHOSTCOMM_DEVICE_ID=""\n')
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO test_session VALUES (NULL)")
    with pytest.raises(auth.ConfirmationRequired):
        auth.authorize(data, platform, current="0.3.0-rc.5", target="0.3.0", package_sha256="a" * 64)
    assert not platform.stopped


def test_reply_with_other_package_cannot_authorize(installation, monkeypatch):
    data, db, platform = installation

    def reply(path, request):
        atomic_json(path, request)
        atomic_json(
            data / "maintenance-reply.json",
            {
                "transaction_id": request["transaction_id"],
                "request_sha256": "b" * 64,
                "state": "authorized",
                "gate": {"db_path": str(db)},
            },
        )

    monkeypatch.setattr(auth, "protected_json", reply)
    with pytest.raises(RuntimeError, match="回复"):
        auth.authorize(data, platform, current="0.3.0", target="0.3.1", package_sha256="a" * 64)
    assert not platform.stopped


def test_stopped_damaged_stable_service_can_be_repaired_without_a_reply_or_login(installation):
    data, database, platform = installation
    platform.stopped = True
    gate = auth.authorize(
        data,
        platform,
        current="0.3.0",
        target="0.3.0",
        package_sha256="a" * 64,
        operation="repair",
        physical_shutdown_confirmed=True,
        timeout=0.01,
    )
    assert gate["state"] == "claimed" and gate["operation"] == "repair"
    assert gate["authorization_source"] == "stopped_service_admin"
    assert gate["db_path"] == str(database)
    assert not (data / "maintenance-reply.json").exists()


def test_stopped_paired_service_still_requires_physical_shutdown_confirmation(installation):
    data, _, platform = installation
    platform.stopped = True
    with pytest.raises(auth.ConfirmationRequired):
        auth.authorize(data, platform, current="0.3.0", target="0.3.0", package_sha256="a" * 64, timeout=0.01)
    assert not (data / "maintenance-request.json").exists()


def test_stopped_unpaired_clean_service_does_not_require_a_fictitious_board(installation):
    data, database, platform = installation
    platform.stopped = True
    (data / "config/service.env").write_text(
        f'SMD_DB_PATH="{database}"\nPROTOCOL_VERSION="2.0"\nHOSTCOMM_DEVICE_ID=""\n'
    )
    gate = auth.authorize(
        data,
        platform,
        current="0.3.0",
        target="0.3.0",
        package_sha256="a" * 64,
        operation="repair",
        timeout=0.01,
    )
    assert gate["physical_shutdown_confirmed"] is False
    assert gate["state"] == "claimed"


def test_live_stable_service_timeout_never_falls_back_to_local_authorization(installation):
    data, _, platform = installation
    with pytest.raises(RuntimeError, match="期限内"):
        auth.authorize(
            data,
            platform,
            current="0.3.0",
            target="0.3.0",
            package_sha256="a" * 64,
            operation="repair",
            physical_shutdown_confirmed=True,
            timeout=0,
        )
    assert not platform.stopped
    assert not (data / "maintenance.json").exists()


def test_stopped_local_authorization_rechecks_new_unknown_records_after_mutex(installation):
    data, database, platform = installation
    platform.stopped = True
    (data / "config/service.env").write_text(
        f'SMD_DB_PATH="{database}"\nPROTOCOL_VERSION="2.0"\nHOSTCOMM_DEVICE_ID=""\n'
    )

    def stop_and_find_command():
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO v2_operation VALUES ('unknown', 0)")

    platform.stop = stop_and_find_command
    with pytest.raises(auth.ConfirmationRequired):
        auth.authorize(
            data,
            platform,
            current="0.3.0",
            target="0.3.0",
            package_sha256="a" * 64,
            operation="repair",
            timeout=0.01,
        )
    assert json.loads((data / "maintenance.json").read_text())["state"] == "prepared"
