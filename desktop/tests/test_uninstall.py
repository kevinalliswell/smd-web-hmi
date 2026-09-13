"""卸载在授权、服务删除和状态归档断点后可重试，不能走升级回滚。"""

import json
from pathlib import Path

import pytest
from smd_desktop.storage import atomic_json
from smd_desktop.uninstall import UninstallTransaction


class Platform:
    def release_backend_guard(self):
        pass

    def __init__(self, data, fail=None):
        self.data = data
        self.fail = fail
        self.service_exists = True
        self.task_exists = True
        self.claims = 0
        self.removals = 0

    def request(self, path, *, token):
        assert path == "/api/system/maintenance/claim"
        gate = json.loads((self.data / "maintenance.json").read_text())
        assert gate["state"] == "prepared" and gate["token"] == token
        self.claims += 1
        gate["state"] = "claimed"
        atomic_json(self.data / "maintenance.json", gate)
        if self.fail == "claim_response":
            self.fail = None
            raise OSError("claim response lost")
        return gate

    def remove_recovery_task(self):
        self.task_exists = False
        if self.fail == "task":
            self.fail = None
            raise SystemExit("power cut after removing task")

    def remove_service(self):
        self.removals += 1
        self.service_exists = False
        if self.fail == "service":
            self.fail = None
            raise SystemExit("power cut after deleting service")


@pytest.fixture
def installed(tmp_path):
    install, data = tmp_path / "program", tmp_path / "data"
    install.mkdir()
    data.mkdir()
    atomic_json(data / "installation.json", {"version": "0.3.0-rc.2"})
    (data / "database.db").write_bytes(b"historical experiment data")
    atomic_json(
        data / "maintenance.json",
        {
            "state": "prepared",
            "upgrade_id": "a" * 32,
            "current_version": "0.3.0-rc.2",
            "target_version": "0.3.0-rc.2",
            "token": "b" * 64,
            "db_path": str(data / "database.db"),
        },
    )
    return install, data


@pytest.mark.parametrize("failure", ["claim_response", "task", "service"])
def test_uninstall_resumes_without_reclaiming_or_restarting_removed_service(installed, failure):
    install, data = installed
    platform = Platform(data, failure)
    with pytest.raises((OSError, SystemExit)):
        UninstallTransaction(install, data, platform).apply()
    assert (data / "maintenance.json").exists()
    transaction = UninstallTransaction(install, data, platform)
    transaction.apply()
    assert platform.claims == 1
    assert not platform.service_exists and not platform.task_exists
    assert not (data / "installation.json").exists()
    assert not (data / "maintenance.json").exists()
    assert (data / "database.db").read_bytes() == b"historical experiment data"
    assert json.loads(transaction.journal_path.read_text())["phase"] == "committed"
    removals = platform.removals
    transaction.apply()
    assert platform.removals == removals  # NSIS may still need to retry filesystem cleanup.


def test_archive_failure_after_service_deletion_keeps_gate_and_can_retry(installed, monkeypatch):
    install, data = installed
    platform = Platform(data)
    transaction = UninstallTransaction(install, data, platform)
    original = Path.unlink

    def fail_pointer(path, *args, **kwargs):
        if path == data / "installation.json":
            raise PermissionError("installation state temporarily locked")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_pointer)
        with pytest.raises(PermissionError):
            transaction.apply()
    assert not platform.service_exists
    assert (data / "maintenance.json").exists()
    transaction.apply()
    assert not (data / "installation.json").exists()
    assert json.loads(transaction.journal_path.read_text())["phase"] == "committed"


def test_wrong_version_or_missing_claim_never_deletes_service(installed):
    install, data = installed
    platform = Platform(data)
    gate = json.loads((data / "maintenance.json").read_text())
    gate["target_version"] = "0.4.0"
    atomic_json(data / "maintenance.json", gate)
    with pytest.raises(RuntimeError):
        UninstallTransaction(install, data, platform).apply()
    assert platform.service_exists and platform.task_exists
    assert platform.claims == 0


def test_new_installation_is_not_removed_by_stale_committed_uninstall(installed):
    install, data = installed
    platform = Platform(data)
    transaction = UninstallTransaction(install, data, platform)
    transaction.apply()
    atomic_json(data / "installation.json", {"version": "0.4.0"})
    with pytest.raises(RuntimeError):
        transaction.apply()


def test_service_removal_error_keeps_installation_and_claim_for_retry(installed):
    install, data = installed
    platform = Platform(data)

    def denied():
        raise PermissionError("SCM access denied")

    platform.remove_service = denied
    transaction = UninstallTransaction(install, data, platform)
    with pytest.raises(PermissionError):
        transaction.apply()
    assert (data / "maintenance.json").exists()
    assert (data / "installation.json").exists()
    assert not (data / "uninstalled.json").exists()
    assert "SCM access denied" in json.loads(transaction.journal_path.read_text())["last_error"]


@pytest.mark.parametrize("argument", ["--uninstall", "--recover"])
def test_only_explicit_uninstall_resumes_pending_uninstall(installed, monkeypatch, argument):
    from types import SimpleNamespace

    from smd_desktop import updater

    install, data = installed
    platform = Platform(data, "service")
    with pytest.raises(SystemExit):
        UninstallTransaction(install, data, platform).apply()
    monkeypatch.setattr(updater, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        updater.ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: True)), raising=False
    )
    monkeypatch.setattr(updater, "data_root", lambda: data)
    monkeypatch.setattr(updater, "WindowsPlatform", lambda _: platform)
    monkeypatch.setattr(updater.sys, "argv", ["SmdUpdate", argument, "--install", str(install)])

    def unexpected_recovery(*args):
        raise AssertionError("uninstall must not restart an upgrade/service")

    monkeypatch.setattr(updater.UpgradeTransaction, "recover", unexpected_recovery)
    if argument == "--uninstall":
        updater.run()
        assert not (data / "installation.json").exists()
    else:
        before = (data / "updates/uninstall.json").read_bytes()
        with pytest.raises(RuntimeError, match="不会自动继续卸载"):
            updater.run()
        assert (data / "installation.json").exists()
        assert (data / "updates/uninstall.json").read_bytes() == before


def test_malformed_authorization_never_removes_service(installed):
    install, data = installed
    platform = Platform(data)
    transaction = UninstallTransaction(install, data, platform)
    atomic_json(
        transaction.journal_path,
        {
            "schema_version": 1,
            "install_dir": str(install.resolve()),
            "phase": "authorized",
            "previous": {"version": "0.3.0-rc.2"},
            "upgrade_id": "a" * 32,
            "authorized": "true",
        },
    )
    with pytest.raises(RuntimeError, match="日志"):
        transaction.apply()
    assert platform.service_exists and platform.task_exists


@pytest.mark.parametrize(
    "phase", ["authorizing", "authorized", "removing_task", "removing_service", "service_removed", "committed"]
)
def test_every_durable_uninstall_phase_resumes_after_power_cut(installed, monkeypatch, phase):
    install, data = installed
    platform = Platform(data)
    transaction = UninstallTransaction(install, data, platform)
    record = transaction._record

    def cut_after_record(journal, state):
        record(journal, state)
        if state == phase:
            raise SystemExit("power cut after journal " + state)

    monkeypatch.setattr(transaction, "_record", cut_after_record)
    with pytest.raises(SystemExit):
        transaction.apply()
    restarted = UninstallTransaction(install, data, platform)
    restarted.apply()
    assert json.loads(restarted.journal_path.read_text())["phase"] == "committed"
    assert platform.claims == 1
    assert not platform.service_exists and not platform.task_exists
    assert (data / "database.db").read_bytes() == b"historical experiment data"


def test_recovery_does_not_clear_another_maintenance_ticket(installed):
    install, data = installed
    platform = Platform(data, "service")
    transaction = UninstallTransaction(install, data, platform)
    with pytest.raises(SystemExit):
        transaction.apply()
    gate = json.loads((data / "maintenance.json").read_text())
    gate["upgrade_id"] = "c" * 32
    atomic_json(data / "maintenance.json", gate)
    with pytest.raises(RuntimeError, match="维护票据"):
        transaction.apply()
    assert json.loads((data / "maintenance.json").read_text()) == gate
    assert (data / "installation.json").exists()
