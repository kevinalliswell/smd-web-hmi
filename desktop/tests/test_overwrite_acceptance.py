"""Acceptance harness fixtures preserve real bytes and never publish secret bodies."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "overwrite_acceptance", ROOT / "scripts/release/windows_overwrite_acceptance.py"
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_recovery_fixture_snapshots_existing_database_and_config_without_business_insertion(
    tmp_path,
):
    data = tmp_path / "data"
    config = data / "config"
    config.mkdir(parents=True)
    (config / "service.env").write_text('SMD_JWT_SECRET="private-value"\n', encoding="utf-8")
    (data / "installation.json").write_text(json.dumps({"version": "0.3.0-rc.5"}), encoding="utf-8")
    database = tmp_path / "中文 数据" / "smd.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE business(value TEXT)")
        db.execute("INSERT INTO business VALUES ('already created through API')")
    before = database.read_bytes()
    journal = runner.write_legacy_recovery_fixture(data, database, "0.3.0")
    assert database.read_bytes() == before
    assert journal["schema_version"] == 1 and journal["phase"] == "rollback_failed"
    with sqlite3.connect(Path(journal["backup_dir"]) / "database.sqlite") as db:
        assert db.execute("SELECT * FROM business").fetchall() == [("already created through API",)]
    assert (Path(journal["backup_dir"]) / "config/service.env").read_bytes() == (config / "service.env").read_bytes()
    assert journal["backup_sha256"] == runner.digest(Path(journal["backup_dir"]) / "database.sqlite")


def test_recovery_fixture_refuses_existing_failure_evidence(tmp_path):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates/active.json").write_text('{"phase":"rollback_failed"}', encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.write_legacy_recovery_fixture(tmp_path, tmp_path / "smd.db", "0.3.0")
    assert (tmp_path / "updates/active.json").read_text(encoding="utf-8") == '{"phase":"rollback_failed"}'


def test_failed_execution_cannot_become_a_passed_scenario_or_leak_exception(tmp_path):
    results = runner.Evidence(tmp_path, {"version": "0.3.0", "commit": "a" * 40}, "b" * 64, "123")
    with pytest.raises(RuntimeError):
        with results.scenario("upgrade_rc4") as observations:
            observations["old_installation_ready"] = True
            raise RuntimeError("PSK=private-secret and bearer token")
    log = json.loads((tmp_path / "upgrade_rc4.log").read_text(encoding="utf-8"))
    assert log["status"] == "failed"
    assert log["failure_type"] == "RuntimeError"
    assert "private-secret" not in (tmp_path / "upgrade_rc4.log").read_text(encoding="utf-8")
    assert results.assertions == []


def test_passed_scenario_is_bound_to_nonempty_written_observations(tmp_path):
    results = runner.Evidence(tmp_path, {"version": "0.3.0", "commit": "a" * 40}, "b" * 64, "123")
    with results.scenario("fresh_install") as observations:
        observations["installer_exit_code"] = 0
        observations["service_account_verified"] = True
    ref = results.assertions[0]["evidence"]
    assert ref == {
        "path": "fresh_install.log",
        "sha256": runner.digest(tmp_path / "fresh_install.log"),
    }
    assert results.assertions[0]["status"] == "passed"


def test_harness_refuses_non_ci_before_inventory_or_mutation(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="GitHub-hosted Windows"):
        runner.require_isolated_ci()


def test_configuration_change_preserves_existing_unrelated_values(tmp_path):
    path = tmp_path / "service.env"
    path.write_text('# original comment\nSMD_JWT_SECRET="original-secret"\nSMD_DB_PATH="old.db"\n', encoding="utf-8")
    runner.update_config(path, {"SMD_DB_PATH": '"C:/中文 数据/new.db"'})
    assert 'SMD_JWT_SECRET="original-secret"' in path.read_text(encoding="utf-8")
    assert 'SMD_DB_PATH="C:/中文 数据/new.db"' in path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").count("SMD_DB_PATH=") == 1


@pytest.mark.parametrize(
    "code,after",
    [
        (1, b"old MaintenanceRequired 0.3.0-rc.5"),
        (20, b"old unrelated failure"),
        (20, b"old MaintenanceRequired 0.3.0-rc.4"),
        (20, b"rotated MaintenanceRequired 0.3.0-rc.5"),
    ],
)
def test_downgrade_probe_requires_this_specific_refusal(code, after):
    with pytest.raises(ValueError):
        runner.verify_legacy_refusal(code, b"old ", after)


def test_historical_refusal_cannot_explain_a_new_installer_failure():
    historical = b"old MaintenanceRequired 0.3.0-rc.5\n"
    with pytest.raises(ValueError):
        runner.verify_legacy_refusal(20, historical, historical + b"new unrelated failure")
    runner.verify_legacy_refusal(20, historical, historical + b"MaintenanceRequired target 0.3.0-rc.5")


def test_recovery_snapshot_can_precede_a_distinguishable_current_state(tmp_path):
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config/service.env").write_text('SMD_JWT_SECRET="unchanged"\n', encoding="utf-8")
    (data / "installation.json").write_text(json.dumps({"version": "0.3.0-rc.5"}), encoding="utf-8")
    database = data / "smd.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE user_account(username TEXT)")
        db.execute("INSERT INTO user_account VALUES ('original')")
    journal = runner.write_legacy_recovery_fixture(data, database, "0.3.0", publish=False)
    assert not (data / "updates/active.json").exists()
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO user_account VALUES ('current_only')")
    runner.publish_legacy_recovery_fixture(data, journal)
    assert runner.database_has_user(database, "current_only")
    assert not runner.database_has_user(Path(journal["backup_dir"]) / "database.sqlite", "current_only")
    assert runner.database_has_user(Path(journal["backup_dir"]) / "database.sqlite", "original")
