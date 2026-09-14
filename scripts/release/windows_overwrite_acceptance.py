"""Actual installer acceptance on a disposable Windows runner; never a field reset tool.

API-created users and recipes are the preserved business fixture. The only direct
SQLite operations are copying the stopped database and injecting recovery metadata;
no experiment/sample rows are invented by this installation suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT, ROOT / "desktop", ROOT / "backend", ROOT / "tools/bench"):
    sys.path.insert(0, str(source))

from smd_bench import windows
from smd_bench.installation import Installation
from smd_bench.ownership import assert_owned_path, check_claim, claim
from smd_desktop.storage import atomic_json, atomic_text

from app.services.sqlite_backup import backup_sqlite

SCENARIOS = frozenset(
    {
        "fresh_install",
        "upgrade_rc4",
        "upgrade_rc5",
        "same_version_repair",
        "downgrade_rejected",
        "rollback_recovery",
        "custom_database_preserved",
    }
)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require_isolated_ci() -> dict:
    if (
        os.name != "nt"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("Overwrite acceptance requires a disposable GitHub-hosted Windows runner")
    found = windows.inventory()
    if (
        not found["admin"]
        or not found["firewall_enabled"]
        or any(
            found[key]
            for key in (
                "existing",
                "service",
                "recovery_task",
                "overrides",
                "busy_ports",
            )
        )
    ):
        raise RuntimeError("Overwrite acceptance refused existing resources or unsafe runner configuration")
    return found


class Evidence:
    def __init__(self, directory: Path, manifest: dict, installer_digest: str, ci_run_id: str):
        self.directory = directory
        self.identity = {
            "schema_version": 1,
            "version": manifest["version"],
            "commit": manifest["commit"],
            "ci_run_id": ci_run_id,
            "runner_os": "Windows",
            "execution": "actual-installed-service",
            "installer_sha256": installer_digest,
        }
        self.assertions = []

    @contextmanager
    def scenario(self, name: str):
        if name not in SCENARIOS or (self.directory / f"{name}.log").exists():
            raise ValueError("Unknown or previously recorded acceptance scenario")
        log = {
            **self.identity,
            "scenario": name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "observations": {},
        }
        try:
            yield log["observations"]
            if not log["observations"]:
                raise RuntimeError("No actual observations were recorded")
            log["status"] = "passed"
        except BaseException as error:
            log["status"] = "failed"
            log["failure_type"] = type(error).__name__
            raise
        finally:
            log["completed_at"] = datetime.now(timezone.utc).isoformat()
            path = self.directory / f"{name}.log"
            atomic_json(path, log)
            if log["status"] == "passed":
                self.assertions.append(
                    {
                        "name": name,
                        "status": "passed",
                        "evidence": {"path": path.name, "sha256": digest(path)},
                    }
                )

    def finish(self, *, success: bool, cleanup_complete: bool, failure_type: str | None = None):
        # This is deliberately a seven-scenario partial report. The root CI adds
        # the two independently executed simulator maintenance scenarios.
        atomic_json(
            self.directory / "windows-overwrite-partial.json",
            {
                **self.identity,
                "suite": "overwrite-installer",
                "status": "passed" if success and cleanup_complete else "failed",
                "cleanup_complete": cleanup_complete,
                "assertions": self.assertions,
                "failure_type": failure_type,
            },
        )


class Api:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.token = None

    def call(self, path: str, *, body=None, method="GET"):
        if not path.startswith("/api/"):
            raise ValueError("The acceptance API is restricted to fixed loopback application paths")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(
            "http://127.0.0.1:8000" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=8) as response:
                return json.load(response)["data"]
        except (urllib.error.URLError, ValueError, KeyError) as error:
            # Never include response bodies, Authorization or submitted passwords.
            raise RuntimeError("Installed API request failed") from error

    def login(self, password: str):
        auth = self.call(
            "/api/auth/login",
            method="POST",
            body={"username": "admin", "password": password},
        )
        self.token = auth["token"]
        return auth

    def ready(self, version: str) -> dict:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                value = self.call("/api/system/health")
                if (
                    value["status"] == "ready"
                    and value["version"] == version
                    and all(value["checks"][name] == "ok" for name in ("database", "schema", "storage", "backup"))
                    and value["checks"]["hostcomm"] == "offline"
                ):
                    return {
                        "version": version,
                        "application_ready": True,
                        "device_offline": True,
                    }
            except (RuntimeError, KeyError):
                pass
            time.sleep(0.5)
        raise TimeoutError("Installed application did not become ready within deadline")


def update_config(path: Path, changes: dict[str, str]) -> None:
    remaining = dict(changes)
    lines = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
        if key in changes:
            if key in remaining:
                lines.append(key + "=" + remaining.pop(key))
        else:
            lines.append(line)
    lines.extend(key + "=" + value for key, value in remaining.items())
    atomic_text(path, "\n".join(lines) + "\n")


def config_inventory(data: Path) -> dict:
    root = data / "config"
    assert_owned_path(root, root, recursive=True)
    return {path.relative_to(root).as_posix(): digest(path) for path in root.rglob("*") if path.is_file()}


def write_legacy_recovery_fixture(data: Path, database: Path, target: str, *, publish: bool = True) -> dict:
    """Only installation metadata is injected, after the owned service has stopped."""
    if (data / "updates/active.json").exists() or (data / "maintenance.json").exists():
        raise FileExistsError("Refusing to overwrite an existing transaction in recovery fixture")
    previous = json.loads((data / "installation.json").read_text(encoding="utf-8"))
    identity = uuid.uuid4().hex
    backup = data / "updates" / identity
    backup.mkdir(parents=True, exist_ok=False)
    backup_sqlite(database, backup / "database.sqlite")
    shutil.copytree(data / "config", backup / "config")
    journal = {
        "schema_version": 1,
        "upgrade_id": identity,
        "previous": previous,
        "target_version": target,
        "db_path": str(database),
        "backup_dir": str(backup),
        "backup_ready": True,
        "backup_sha256": digest(backup / "database.sqlite"),
        "phase": "rollback_failed",
        "last_error": "CI engineering fixture: prior upgrade health timeout",
        "rollback_error": "CI engineering fixture: previous health timeout",
    }
    if publish:
        publish_legacy_recovery_fixture(data, journal)
    return journal


def publish_legacy_recovery_fixture(data: Path, journal: dict) -> None:
    if (data / "updates/active.json").exists() or (data / "maintenance.json").exists():
        raise FileExistsError("Refusing an existing transaction while publishing the recovery fixture")
    atomic_json(data / "updates/active.json", journal)
    atomic_json(
        data / "maintenance.json",
        {
            "state": "claimed",
            "upgrade_id": journal["upgrade_id"],
            "current_version": journal["previous"]["version"],
            "target_version": journal["target_version"],
            "db_path": journal["db_path"],
            "token": secrets.token_hex(32),
        },
    )


def database_has_user(database: Path, username: str) -> bool:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        return source.execute("SELECT 1 FROM user_account WHERE username=?", (username,)).fetchone() is not None


def verify_legacy_refusal(code: int, before: bytes, after: bytes) -> None:
    if code != 20 or not after.startswith(before):
        raise ValueError("Legacy installer did not return the specific maintenance-required refusal")
    added = after[len(before) :].decode("utf-8", errors="replace")
    if "MaintenanceRequired" not in added or "0.3.0-rc.5" not in added:
        raise ValueError("Legacy maintenance refusal lacks this invocation's target-specific log evidence")


def run_installer(executable: Path, install: Path, private: Path, *, confirmations=True) -> int:
    """Keep NSIS /D last and unquoted, with process-tree containment before execution."""
    import win32api
    import win32con
    import win32event
    import win32job
    import win32process

    if any(char in str(install) for char in ('"', "\r", "\n")):
        raise ValueError("Unsafe NSIS installation path")
    flags = " /PHYSICALSHUTDOWN=1 /CONFIRMRECOVERY=1" if confirmations else ""
    command = subprocess.list2cmdline([str(executable)]) + " /S" + flags + " /D=" + str(install)
    job = windows.create_kill_on_close_job()
    process = thread = None
    assigned = False
    try:
        process, thread, _, _ = win32process.CreateProcess(
            str(executable),
            command,
            None,
            None,
            False,
            win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW,
            None,
            str(private),
            win32process.STARTUPINFO(),
        )
        win32job.AssignProcessToJobObject(job, process)
        assigned = True
        win32process.ResumeThread(thread)
        if win32event.WaitForSingleObject(process, 600_000) != win32event.WAIT_OBJECT_0:
            win32job.TerminateJobObject(job, 1)
            win32event.WaitForSingleObject(process, 10_000)
            raise TimeoutError("Owned installer exceeded 10 minute deadline")
        return win32process.GetExitCodeProcess(process)
    except BaseException:
        if process and not assigned:
            win32api.TerminateProcess(process, 1)
            win32event.WaitForSingleObject(process, 10_000)
        raise
    finally:
        if thread:
            win32api.CloseHandle(thread)
        if process:
            win32api.CloseHandle(process)
        win32api.CloseHandle(job)


class Round:
    def __init__(self, folders: dict, version: str, target: str, target_manifest_digest: str):
        self.run_id = uuid.uuid4().hex
        self.installation = Installation(self.run_id, folders, version)
        self.version = version
        self.target = target
        self.target_manifest_digest = target_manifest_digest
        self.external = Path(folders["program_data"]) / f"SmdHmi-CI-Data-{self.run_id}"
        self.rules = []
        self.api = Api()
        self.database = self.installation.data / "db/smd.db"
        self.created = False
        self.cleaned = False

    def prepare(self):
        item = self.installation
        for path in (item.private, item.install, item.data, self.external):
            assert_owned_path(path, path)
            if path.exists():
                raise ValueError("Acceptance directory already exists")
        item.private.parent.mkdir(parents=True, exist_ok=True)
        claim(item.private, self.run_id)
        windows.secure_directory(item.private)
        self.created = True
        item.save("overwrite_claimed")
        for path in (item.install, item.data):
            claim(path, self.run_id)
        for version in {self.version, self.target, "0.3.0-rc.5"}:
            identity = uuid.uuid4().hex
            executable = item.install / "versions" / version / "SmdService/SmdService.exe"
            windows.firewall(identity, executable)
            self.rules.append((identity, executable))
        item.save("overwrite_network_isolated")

    def set_version(self, version: str):
        self.version = version
        item = self.installation
        item.version = version
        item.state["version"] = version
        item.version_dir = item.install / "versions" / version
        item.service = item.version_dir / "SmdService/SmdService.exe"
        item.updater = item.version_dir / "SmdUpdate/SmdUpdate.exe"
        item.desktop = item.version_dir / "SmdDesktop/SmdDesktop.exe"

    def install(self, installer: Path, version: str, *, confirmations=True) -> dict:
        result = run_installer(
            installer,
            self.installation.install,
            self.installation.private,
            confirmations=confirmations,
        )
        if result != 0:
            raise RuntimeError("Actual installer returned a failure exit code")
        self.set_version(version)
        service = windows.check_service(self.installation.service)
        installed = json.loads((self.installation.version_dir / "manifest.json").read_text(encoding="utf-8"))
        if installed["version"] != version:
            raise ValueError("Actual installed manifest version differs")
        if (
            version == self.target
            and digest(self.installation.version_dir / "manifest.json") != self.target_manifest_digest
        ):
            raise ValueError("Installed current manifest does not match the exact checked build")
        return {
            "installer_exit_code": result,
            "source_installer_sha256": digest(installer),
            "installed_manifest_sha256": digest(self.installation.version_dir / "manifest.json"),
            "service_account_verified": service["account"] == "NT AUTHORITY\\LocalService",
            **self.api.ready(version),
        }

    def seed_business(self):
        initial = (
            (self.installation.data / "config/bootstrap-admin-password.txt").read_text(encoding="utf-8-sig").strip()
        )
        self.password = "Ci!" + secrets.token_urlsafe(30)
        auth = self.api.login(initial)
        if auth.get("must_change_password") is not True:
            raise ValueError("Fresh installation must require password change")
        self.api.call(
            "/api/users/change-password",
            method="POST",
            body={"old_password": initial, "new_password": self.password},
        )
        self.api.login(self.password)
        self.username = "upgrade_" + self.run_id[:12]
        self.api.call(
            "/api/users",
            method="POST",
            body={
                "username": self.username,
                "password": "Ci!" + secrets.token_urlsafe(30),
                "role": "observer",
                "display_name": "覆盖安装保留用户",
            },
        )
        template = self.api.call("/api/recipes/template/standard")["definition"]
        self.recipe = self.api.call("/api/recipes", method="POST", body={"definition": template})
        # Real installed pairing command creates identities/keys. It stops here;
        # no simulator or physical device is connected in this installation suite.
        self.installation.pair()
        self.installation.start()
        self.api.ready(self.version)
        self.config = config_inventory(self.installation.data)
        if not any(name.endswith("firmware-pairing.json") for name in self.config):
            raise ValueError("Real installed pairing did not create credential material")
        self.assert_business()

    def assert_business(self) -> dict:
        auth = self.api.login(self.password)
        if auth.get("must_change_password") is not False:
            raise ValueError("Upgrade lost the changed account password state")
        users = self.api.call("/api/users")
        if not any(
            user["username"] == self.username
            and user["role"] == "observer"
            and user["display_name"] == "覆盖安装保留用户"
            for user in users
        ):
            raise ValueError("Upgrade lost the API-created user")
        recipe = self.api.call("/api/recipes/" + self.recipe["recipe_id"])
        if any(recipe[key] != self.recipe[key] for key in ("recipe_id", "version", "digest", "definition")):
            raise ValueError("Upgrade changed the API-created immutable recipe")
        if config_inventory(self.installation.data) != self.config:
            raise ValueError("Upgrade changed an existing configuration or key file")
        return {
            "changed_admin_password_preserved": True,
            "api_user_preserved": True,
            "api_recipe_preserved": True,
            "configuration_and_pairing_keys_preserved": True,
            "business_fixture_source": "installed_authenticated_api",
            "experiment_samples": "not_created_in_installation_suite",
        }

    def custom_database(self):
        self.installation.stop()
        check_claim(self.installation.data, self.run_id)
        claim(self.external, self.run_id)
        windows.secure_directory(self.external)
        windows.powershell(
            r"""
$acl=Get-Acl -LiteralPath $p.path
$sid=[Security.Principal.NTAccount]::new('NT SERVICE','SmdHmi').Translate([Security.Principal.SecurityIdentifier])
$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,'Modify','ContainerInherit,ObjectInherit','None','Allow')
$acl.AddAccessRule($rule);Set-Acl -LiteralPath $p.path -AclObject $acl
""",
            {"path": str(self.external)},
        )
        folder = self.external / "中文 数据"
        folder.mkdir()
        self.database = folder / "现场 试验.db"
        backup_sqlite(self.installation.data / "db/smd.db", self.database)
        update_config(
            self.installation.data / "config/service.env",
            {"SMD_DB_PATH": '"' + self.database.as_posix() + '"'},
        )
        self.config = config_inventory(self.installation.data)
        self.installation.start()
        self.api.ready(self.version)
        self.assert_business()

    def assert_custom_database(self) -> dict:
        from smd_desktop.installer_authorization import environment

        _, actual = environment(self.installation.data)
        if actual != self.database.resolve():
            raise ValueError("Actual database configuration no longer points to the custom database")
        marker = "path_" + self.run_id[:12]
        self.api.call(
            "/api/users",
            method="POST",
            body={
                "username": marker,
                "password": "Ci!" + secrets.token_urlsafe(30),
                "role": "observer",
            },
        )

        def contains(path):
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                return source.execute("SELECT 1 FROM user_account WHERE username=?", (marker,)).fetchone() is not None

        if not contains(self.database) or contains(self.installation.data / "db/smd.db"):
            raise ValueError("API write did not reach only the actual custom database")
        return {
            "custom_database_config_preserved": True,
            "api_write_reached_custom_database": True,
            "default_database_not_reused": True,
            "unicode_and_spaces": True,
        }

    def cleanup(self):
        item = self.installation
        check_claim(item.private, self.run_id)
        for path in (item.install, item.data):
            check_claim(path, self.run_id)
        if self.external.exists():
            check_claim(self.external, self.run_id)
        windows.remove_registration(item.service, item.updater, item.desktop, item.install, item.data)
        if windows.service_info():
            raise RuntimeError("Owned service remains; test resources and firewall remain preserved")
        for path in (item.install, item.data, self.external):
            if path.exists():
                check_claim(path, self.run_id)
                shutil.rmtree(path)
        for identity, executable in self.rules:
            windows.firewall(identity, executable, remove=True)
        self.cleaned = True
        item.save("overwrite_cleaned")


def execute(current: Path, rc4: Path, rc5: Path, directory: Path):
    folders = require_isolated_ci()
    manifest = json.loads((current.parent / "manifest.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", "")) or not re.fullmatch(
        r"\d+\.\d+\.\d+", manifest.get("version", "")
    ):
        raise ValueError("Overwrite suite requires current software-stable manifest")
    target = manifest["version"]
    if (
        current.name != f"SmdHmi-{target}-windows-x64.exe"
        or rc4.name != "SmdHmi-0.3.0-rc.4-windows-x64.exe"
        or rc5.name != "SmdHmi-0.3.0-rc.5-windows-x64.exe"
    ):
        raise ValueError("Installer filenames do not identify the required releases")
    for path in (current, rc4, rc5):
        assert_owned_path(path, path)
        if not path.is_file():
            raise FileNotFoundError("Required actual installer is missing")
    assert_owned_path(directory, directory)
    if any(directory.is_relative_to(Path(folders[key])) for key in ("program_files", "program_data")):
        raise ValueError("Public evidence must remain outside program and protected data directories")
    if directory.exists():
        raise FileExistsError("Acceptance output must be a new directory")
    directory.mkdir(parents=True, exist_ok=False)
    evidence = Evidence(directory, manifest, digest(current), os.environ["GITHUB_RUN_ID"])
    rounds = []
    success = False
    failure = None
    try:
        fresh = Round(folders, target, target, digest(current.parent / "manifest.json"))
        rounds.append(fresh)
        fresh.prepare()
        with evidence.scenario("fresh_install") as log:
            log.update(fresh.install(current, target))
            fresh.seed_business()
            log.update(fresh.assert_business())
        with evidence.scenario("same_version_repair") as log:
            fresh.installation.stop()
            page = fresh.installation.version_dir / "frontend/index.html"
            original = digest(page)
            page.write_bytes(b"CI damaged program fixture")
            log.update(fresh.install(current, target))
            if digest(page) != original:
                raise ValueError("Same-version installation did not repair damaged program bytes")
            log.update(fresh.assert_business())
            log["damaged_program_restored"] = True
        with evidence.scenario("downgrade_rejected") as log:
            pointer = digest(fresh.installation.data / "installation.json")
            updater_log = fresh.installation.data / "logs/updater.log"
            before_log = updater_log.read_bytes()
            result = run_installer(
                rc5,
                fresh.installation.install,
                fresh.installation.private,
                confirmations=False,
            )
            verify_legacy_refusal(result, before_log, updater_log.read_bytes())
            if digest(fresh.installation.data / "installation.json") != pointer:
                raise ValueError("Rejected downgrade changed the installed version pointer")
            fresh.api.ready(target)
            log.update(fresh.assert_business())
            log.update(
                installer_exit_code=result,
                version_pointer_unchanged=True,
                attempted_version="0.3.0-rc.5",
                rejection_path="actual_legacy_installer_no_maintenance_permission",
            )
        fresh.cleanup()

        old4 = Round(
            require_isolated_ci(),
            "0.3.0-rc.4",
            target,
            digest(current.parent / "manifest.json"),
        )
        rounds.append(old4)
        old4.prepare()
        with evidence.scenario("upgrade_rc4") as log:
            log["old_installation"] = old4.install(rc4, "0.3.0-rc.4")
            old4.seed_business()
            old4.custom_database()
            log["new_installation"] = old4.install(current, target)
            log.update(old4.assert_business())
        with evidence.scenario("custom_database_preserved") as log:
            log.update(old4.assert_custom_database())
        old4.cleanup()

        old5 = Round(
            require_isolated_ci(),
            "0.3.0-rc.5",
            target,
            digest(current.parent / "manifest.json"),
        )
        rounds.append(old5)
        old5.prepare()
        # Nested scenarios share execution but each log records a distinct assertion:
        # actual rc5 preservation, and observed recovery of a real backed-up journal.
        with evidence.scenario("upgrade_rc5") as upgrade_log, evidence.scenario("rollback_recovery") as recovery_log:
            upgrade_log["old_installation"] = old5.install(rc5, "0.3.0-rc.5")
            old5.seed_business()
            old5.installation.stop()
            if windows.check_service(old5.installation.service, running=False)["state"] != "Stopped":
                raise ValueError("Recovery fixture requires the owned service to have actually stopped")
            journal = write_legacy_recovery_fixture(old5.installation.data, old5.database, target, publish=False)
            # Distinguish the failed transaction's original backup from current data
            # using a real authenticated API write, never a synthetic database row.
            old5.installation.start()
            old5.api.ready("0.3.0-rc.5")
            old5.api.login(old5.password)
            current_only_user = "restore_" + old5.run_id[:12]
            old5.api.call(
                "/api/users",
                method="POST",
                body={
                    "username": current_only_user,
                    "password": "Ci!" + secrets.token_urlsafe(30),
                    "role": "observer",
                },
            )
            old5.installation.stop()
            if not database_has_user(old5.database, current_only_user) or database_has_user(
                Path(journal["backup_dir"]) / "database.sqlite", current_only_user
            ):
                raise ValueError("API marker did not distinguish current data from the original backup")
            publish_legacy_recovery_fixture(old5.installation.data, journal)
            recovery_log["fixture"] = "stopped_service_legacy_schema1_with_actual_db_config_backup"
            recovery_log["business_rows_injected"] = False
            upgrade_log["new_installation"] = old5.install(current, target)
            upgrade_log.update(old5.assert_business())
            retained = json.loads(
                (old5.installation.data / "updates/transactions" / f"{journal['upgrade_id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            snapshot = Path(retained["pre_recovery_snapshot"])
            if retained["phase"] != "rolled_back" or not snapshot.is_relative_to(Path(journal["backup_dir"])):
                raise ValueError("Old failed transaction was not recovered with a retained snapshot")
            if not all((snapshot / name).is_file() for name in ("snapshot.json", "database.sqlite", "journal.json")):
                raise ValueError("Recovery did not preserve the extra current-state snapshot")
            if database_has_user(old5.database, current_only_user) or not database_has_user(
                snapshot / "database.sqlite", current_only_user
            ):
                raise ValueError("Recovery did not restore original data and preserve newer data separately")
            current_journal = json.loads((old5.installation.data / "updates/active.json").read_text(encoding="utf-8"))
            if current_journal["phase"] != "committed" or current_journal["target_version"] != target:
                raise ValueError("Recovered installation did not commit the subsequent upgrade")
            recovery_log.update(
                old_failed_phase="rollback_failed",
                old_recovery_phase=retained["phase"],
                extra_current_snapshot_retained=True,
                newer_api_marker_only_in_extra_snapshot=True,
                original_api_business_restored=True,
                subsequent_upgrade_committed=True,
                **old5.assert_business(),
            )
        old5.cleanup()
        if {item["name"] for item in evidence.assertions} != SCENARIOS:
            raise RuntimeError("Not every installer scenario executed")
        success = True
    except BaseException as error:
        failure = type(error).__name__
        # Do not attempt reset after a failed install. The recovery journal,
        # protected test data, ownership markers and network block remain intact.
        raise
    finally:
        cleaned = all(item.cleaned for item in rounds)
        evidence.finish(success=success, cleanup_complete=cleaned, failure_type=failure)
        atomic_json(
            directory / "overwrite-resources.json",
            {
                "schema_version": 1,
                "runs": [{"run_id": item.run_id, "cleaned": item.cleaned} for item in rounds],
                "recovery_guidance": "Failed owned installations are retained on the disposable runner; do not upload private data or configuration.",
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-installer", type=Path, required=True)
    parser.add_argument("--rc4-installer", type=Path, required=True)
    parser.add_argument("--rc5-installer", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        execute(
            args.current_installer.resolve(),
            args.rc4_installer.resolve(),
            args.rc5_installer.resolve(),
            args.evidence_dir.resolve(),
        )
    except Exception as error:
        print(
            "Windows overwrite acceptance failed: " + type(error).__name__,
            file=sys.stderr,
        )
        return 1
    print("Seven actual Windows installer scenarios passed; simulator maintenance checks are a separate suite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
