"""One owned fresh installation; never attach to or reset an existing HMI."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from smd_desktop.storage import atomic_json

from . import windows
from .contracts import Manifest, validate_run_id
from .ownership import assert_owned_path, check_claim, claim


def preflight() -> dict:
    found = windows.inventory()
    reasons = []
    if not found["admin"]:
        reasons.append("administrator_required")
    if found["existing"] or found["service"] or found["recovery_task"]:
        reasons.append("existing_installation_refused")
    if found["overrides"]:
        reasons.append("environment_overrides_refused")
    if found["busy_ports"]:
        reasons.append("test_port_in_use")
    if not found["firewall_enabled"]:
        reasons.append("firewall_disabled")
    return {
        "schema_version": 1,
        "status": "ready" if not reasons else "refused",
        "reasons": reasons,
        "program_files": found["program_files"],
        "program_data": found["program_data"],
    }


def checked_process(executable: Path, arguments: list[str], private: Path, *, timeout: int = 360, nsis=False) -> int:
    """Create suspended, then assign a kill-on-close job before any child can spawn."""
    import win32api
    import win32con
    import win32event
    import win32job
    import win32process

    job = win32job.CreateJobObject(None, None)
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    command = subprocess.list2cmdline([str(executable), *arguments])
    if nsis:
        command = subprocess.list2cmdline([str(executable)]) + " /S /D=" + arguments[-1]
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
        if win32event.WaitForSingleObject(process, timeout * 1000) != win32event.WAIT_OBJECT_0:
            win32job.TerminateJobObject(job, 1)
            win32event.WaitForSingleObject(process, 10000)
            raise TimeoutError("owned installer or pairing process exceeded deadline")
        return win32process.GetExitCodeProcess(process)
    except BaseException:
        if process and not assigned:
            win32api.TerminateProcess(process, 1)
            win32event.WaitForSingleObject(process, 10000)
        raise
    finally:
        if thread:
            win32api.CloseHandle(thread)
        if process:
            win32api.CloseHandle(process)
        win32api.CloseHandle(job)


class Installation:
    def __init__(self, run_id: str, folders: dict, version: str):
        self.run_id = validate_run_id(run_id)
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version):
            raise ValueError("invalid installation version")
        self.version = version
        self.claimed = self.evidence_created = False
        self.program_files, self.program_data = Path(folders["program_files"]), Path(folders["program_data"])
        self.install = self.program_files / f"SmdHmi-CI-{run_id}"
        self.data = self.program_data / "SmdHmi"
        self.private = self.program_data / "SmdBench" / "runs" / run_id
        self.version_dir = self.install / "versions" / version
        self.service = self.version_dir / "SmdService/SmdService.exe"
        self.updater = self.version_dir / "SmdUpdate/SmdUpdate.exe"
        self.desktop = self.version_dir / "SmdDesktop/SmdDesktop.exe"
        self.journal = self.private / "run.json"
        self.state = {"schema_version": 1, "run_id": run_id, "version": version, "phase": "new"}

    def save(self, phase: str) -> None:
        self.state["phase"] = phase
        atomic_json(self.journal, self.state)

    def prepare(self, evidence: Path, manifest: Manifest, digest: str) -> None:
        roots = (self.private, self.install, self.data, evidence)
        for index, left in enumerate(roots):
            for right in roots[index + 1 :]:
                if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                    raise ValueError("owned directories and public evidence must not overlap")
        for path in roots:
            assert_owned_path(path, path)
            if path.exists():
                raise ValueError("test run directories must be new")
        for root in (self.private.parent,):
            root.mkdir(parents=True, exist_ok=True)
        claim(self.private, self.run_id)
        self.claimed = True
        self.save("claimed_private")
        windows.secure_directory(self.private)
        self.state.update(commit=manifest.commit, installer_sha256=digest, evidence=str(evidence))
        self.save("claimed_private")
        evidence.mkdir(parents=True, exist_ok=False)
        self.evidence_created = True
        for path in (self.install, self.data):
            claim(path, self.run_id)
        self.save("claimed_installation")
        windows.firewall(self.run_id, self.service)
        self.save("network_isolated")

    def install_package(self, installer: Path) -> None:
        self.save("installing")
        if checked_process(installer, [str(self.install)], self.private, nsis=True) != 0:
            raise RuntimeError("fresh installation failed")
        actual = json.loads((self.version_dir / "manifest.json").read_text(encoding="utf-8"))
        if actual["version"] != self.version or actual["commit"] != self.state["commit"]:
            raise ValueError("installed version differs from the tested package")
        windows.check_service(self.service)
        self.save("installed")

    def pair(self) -> Path:
        from uuid import uuid4

        windows.stop_service(self.service, self.data)
        identity = uuid4().hex
        if (
            checked_process(
                self.updater,
                [
                    "--install",
                    str(self.install),
                    "--pair-device",
                    identity,
                    "--reason",
                    "SmdBench inert loopback acceptance",
                ],
                self.private,
                timeout=60,
            )
            != 0
        ):
            raise RuntimeError("installed offline pairing failed")
        matches = []
        for file in (self.data / "config/pairings").glob("*/firmware-pairing.json"):
            pairing = json.loads(file.read_text(encoding="utf-8"))
            if pairing.get("device_id") == identity:
                matches.append(file)
        if len(matches) != 1:
            raise ValueError("pairing output is not uniquely bound to this run")
        configuration = self.data / "config/service.env"
        lines = configuration.read_text(encoding="utf-8-sig").splitlines()
        values = {}
        for line in lines:
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value
        if values.get("PROTOCOL_VERSION") not in ('"2.0"', "2.0") or values.get("HOSTCOMM_MOCK") not in (
            '"false"',
            "false",
        ):
            raise ValueError("pairing did not produce a real protocol 2.0 configuration")
        values.update(HOSTCOMM_HOST='"127.0.0.1"', HOSTCOMM_PORT='"34212"')
        temporary = configuration.with_suffix(".bench.tmp")
        temporary.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        os.replace(temporary, configuration)
        self.save("paired_loopback")
        return matches[0]

    def stop(self):
        windows.stop_service(self.service, self.data)

    def start(self):
        windows.start_service(self.service, self.data)

    def cleanup(self) -> None:
        check_claim(self.private, self.run_id)
        for directory in (self.install, self.data):
            if directory.exists():
                check_claim(directory, self.run_id)
        # Every shared registration is checked before any registration is removed.
        windows.remove_registration(self.service, self.updater, self.desktop, self.install, self.data)
        if windows.service_info():
            raise RuntimeError("service remains; retain network isolation and test files")
        database = self.data / "db/smd.db"
        if database.exists():
            with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as source:
                with sqlite3.connect(self.private / "host-archive.sqlite") as destination:
                    source.backup(destination)
        for directory in (self.install, self.data):
            if directory.exists():
                check_claim(directory, self.run_id)
                shutil.rmtree(directory)
        windows.firewall(self.run_id, self.service, remove=True)
        self.save("cleaned")

    @classmethod
    def recover(cls, run_id: str):
        folders = windows.inventory()
        if not folders["admin"]:
            raise ValueError("cleanup requires administrator")
        run_id = validate_run_id(run_id)
        private = Path(folders["program_data"]) / "SmdBench/runs" / run_id
        check_claim(private, run_id)
        state = json.loads((private / "run.json").read_text(encoding="utf-8"))
        if state.get("schema_version") != 1 or state.get("run_id") != run_id:
            raise ValueError("run journal identity differs")
        installation = cls(run_id, folders, state["version"])
        installation.state = state
        return installation
