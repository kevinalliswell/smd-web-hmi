"""受保护的卸载事务；服务已删除后的重试不再依赖在线后台或升级回滚。"""

import json
import re
from pathlib import Path

from .storage import atomic_json


class UninstallTransaction:
    PHASES = {"authorizing", "authorized", "removing_task", "removing_service", "service_removed", "committed"}

    def __init__(self, install: Path, data: Path, platform):
        self.install, self.data, self.platform = install.resolve(), data.resolve(), platform
        self.journal_path = self.data / "updates/uninstall.json"
        self.pointer_path = self.data / "installation.json"
        self.gate_path = self.data / "maintenance.json"
        self.archive_path = self.data / "uninstalled.json"

    def _record(self, journal, phase):
        journal["phase"] = phase
        atomic_json(self.journal_path, journal)

    def _archive_matches(self, journal):
        if not self.archive_path.exists():
            return False
        archived = json.loads(self.archive_path.read_text(encoding="utf-8"))
        return archived.get("uninstall_id") == journal["upgrade_id"] and archived.get("install_dir") == str(
            self.install
        )

    def apply(self, *, permit=None):
        if self.journal_path.exists():
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if (
                not isinstance(journal, dict)
                or journal.get("schema_version") != 1
                or journal.get("install_dir") != str(self.install)
                or journal.get("phase") not in self.PHASES
                or type(journal.get("authorized")) is not bool
                or not isinstance(journal.get("previous"), dict)
                or not isinstance(journal["previous"].get("version"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", str(journal.get("upgrade_id", "")))
                or (not journal["authorized"] and journal["phase"] != "authorizing")
            ):
                raise RuntimeError("卸载日志损坏或属于其他安装目录")
        else:
            previous = json.loads(self.pointer_path.read_text(encoding="utf-8"))
            gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            local_authorized = (
                permit is not None
                and permit == gate
                and gate.get("schema_version") == 2
                and gate.get("issuer") == "windows_installer"
                and gate.get("operation") == "uninstall"
                and gate.get("state") == "claimed"
                and re.fullmatch(r"[0-9a-f]{64}", str(gate.get("package_sha256", "")))
                and re.fullmatch(r"[0-9a-f]{64}", str(gate.get("request_sha256", "")))
                and re.fullmatch(r"S-1-(?:\d+-)+\d+", str(gate.get("admin_sid", "")))
            )
            if (
                (not local_authorized and gate.get("state") != "prepared")
                or gate.get("current_version") != previous["version"]
                or gate.get("target_version") != previous["version"]
                or not re.fullmatch(r"[0-9a-f]{32}", gate.get("upgrade_id", ""))
            ):
                raise RuntimeError("卸载前须在系统维护中准备当前版本")
            journal = {
                "schema_version": 1,
                "install_dir": str(self.install),
                "previous": previous,
                "upgrade_id": gate["upgrade_id"],
                "operator_id": gate.get("operator_id"),
                "authorized": bool(local_authorized),
                "authorization_source": "windows_installer" if local_authorized else "legacy",
                "admin_sid": gate.get("admin_sid"),
                "package_sha256": gate.get("package_sha256"),
                "permit": permit if local_authorized else None,
            }
            self._record(journal, "authorized" if local_authorized else "authorizing")
        if journal["phase"] == "committed":
            if self.pointer_path.exists() or not self._archive_matches(journal):
                raise RuntimeError("卸载完成后安装状态已变化，禁止沿用旧日志")
            return
        if self.pointer_path.exists():
            if json.loads(self.pointer_path.read_text(encoding="utf-8")) != journal["previous"]:
                raise RuntimeError("安装状态与卸载日志不一致")
        elif not self._archive_matches(journal):
            raise RuntimeError("缺少卸载安装状态及归档证据")
        if self.gate_path.exists():
            gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            if (
                gate.get("upgrade_id") != journal["upgrade_id"]
                or gate.get("current_version") != journal["previous"]["version"]
                or gate.get("target_version") != journal["previous"]["version"]
                or (journal["authorized"] and gate.get("state") != "claimed")
            ):
                raise RuntimeError("维护票据与卸载日志不一致")
        elif not journal["authorized"] or not self._archive_matches(journal):
            raise RuntimeError("卸载缺少有效维护许可")
        if not journal["authorized"]:
            if gate.get("state") == "prepared":
                self.platform.request("/api/system/maintenance/claim", token=gate["token"])
                gate = json.loads(self.gate_path.read_text(encoding="utf-8"))
            if gate.get("state") != "claimed" or gate.get("upgrade_id") != journal["upgrade_id"]:
                raise RuntimeError("后台尚未确认卸载维护许可")
            journal["authorized"] = True
            journal["claimed_at"] = gate.get("claimed_at")
            self._record(journal, "authorized")
        try:
            if journal.get("authorization_source") == "windows_installer" and journal["phase"] in {
                "authorized",
                "removing_task",
            }:
                permit = journal.get("permit")
                if not isinstance(permit, dict) or permit != gate:
                    raise RuntimeError("卸载授权记录与维护门禁不一致")
                self.platform.stop()
                self.platform.validate_stopped(permit)
            self._record(journal, "removing_task")
            self.platform.remove_recovery_task()
            self._record(journal, "removing_service")
            self.platform.remove_service()  # 缺失可重试；访问拒绝、超时仍阻断后续归档。
            self._record(journal, "service_removed")
            atomic_json(
                self.archive_path,
                {**journal["previous"], "uninstall_id": journal["upgrade_id"], "install_dir": str(self.install)},
            )
            self.pointer_path.unlink(missing_ok=True)
            self.gate_path.unlink(missing_ok=True)
            self._record(journal, "committed")
        except Exception as error:
            journal["last_error"] = str(error)
            self._record(journal, journal["phase"])
            raise
