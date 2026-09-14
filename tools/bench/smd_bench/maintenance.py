"""Actual installer admission against the owned TLS simulator and installed service."""

import asyncio
import hashlib
import json

from . import windows
from .browser import eventually
from .package import sha256


def _snapshot(installation) -> dict:
    service = windows.check_service(installation.service)
    gate = installation.data / "maintenance.json"
    database = installation.data / "db/smd.db"
    return {
        "pid": service["pid"],
        "image": service["path"],
        "pointer_sha256": sha256(installation.data / "installation.json"),
        "database_file_id": database.stat().st_ino,
        "config_sha256": sha256(installation.data / "config/service.env"),
        "gate_sha256": sha256(gate) if gate.exists() else None,
    }


def _unchanged(before: dict, after: dict) -> None:
    if before != after:
        raise AssertionError(
            "rejected installer changed the service, program pointer, database identity or maintenance gate"
        )


def _reply(installation, expected: str, physical_confirmed: bool) -> dict:
    from smd_desktop.installer_authorization import request_digest

    request = json.loads((installation.data / "maintenance-request.json").read_text(encoding="utf-8"))
    reply = json.loads((installation.data / "maintenance-reply.json").read_text(encoding="utf-8"))
    if (
        reply.get("transaction_id") != request.get("transaction_id")
        or reply.get("request_sha256") != request_digest(request)
        or request.get("physical_shutdown_confirmed") is not physical_confirmed
        or request.get("current_version") != installation.version
        or request.get("target_version") != installation.version
        or reply.get("reason_code") != expected
    ):
        raise AssertionError("installer result is not backed by the current, matching service maintenance reply")
    return {
        "transaction_id": reply["transaction_id"],
        "request_sha256": reply["request_sha256"],
        "reason_code": reply["reason_code"],
        "state": reply["state"],
        "physical_shutdown_confirmed": physical_confirmed,
    }


def _record(scenes, name: str, observations: list[dict]) -> None:
    # Fixed whitelist: never copy maintenance gate, pairing configuration, raw
    # service logs, request bodies, passwords or the application database.
    artifact = scenes.ui.evidence / ("installer-" + name.replace("_", "-") + ".json")
    value = {
        "schema_version": 1,
        "name": name,
        "execution": "actual-installed-service",
        "run_id": scenes.run_id,
        "ci_run_id": scenes.result.get("ci_run_id"),
        "version": scenes.installation.version,
        "commit": scenes.result["commit"],
        "installer_sha256": scenes.result["installer_sha256"],
        "observations": observations,
    }
    artifact.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scenes.passed(name, evidence={"path": artifact.name, "sha256": sha256(artifact)})


async def busy_rejected(scenes, phase: str) -> dict:
    installation = scenes.installation
    if installation is None:
        raise AssertionError("installer admission requires an owned Windows installation")
    scenes.stage("installer_busy_" + phase)
    board = await scenes.state(phase)
    before = await asyncio.to_thread(_snapshot, installation)
    code = await asyncio.to_thread(installation.maintenance_attempt, physical_shutdown_confirmed=True)
    after = await asyncio.to_thread(_snapshot, installation)
    observed = await scenes.worker.request("snapshot")
    _unchanged(before, after)
    if code != 1:
        raise AssertionError("busy installer must refuse even an administrator's physical-stop confirmation")
    if observed["run"]["state"] != phase or observed["run"]["run_id"] != board["run"]["run_id"]:
        raise AssertionError("rejected installation interrupted or replaced the active board run")
    reply = _reply(installation, "device_busy", True)
    if reply["state"] != "blocked":
        raise AssertionError("busy device did not block installer admission")
    return {
        "phase": phase,
        "exit_code": code,
        "process_id_before": before["pid"],
        "process_id_after": after["pid"],
        "same_program_configuration_database_and_gate": True,
        "same_run_id": True,
        "maintenance_reply": reply,
    }


async def _archives(ui) -> dict:
    tests = []
    page = 1
    while True:
        rows = await ui.api(f"/api/tests?page={page}&size=100")
        tests.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    if not tests:
        raise AssertionError("repair preservation requires actual completed simulator experiments")
    samples = {}
    for test in tests:
        detail = await ui.api("/api/tests/" + test["test_id"])
        samples[test["test_id"]] = detail["sample_count"]
    reports = await ui.api("/api/reports")
    if not reports or not any(samples.values()):
        raise AssertionError("repair preservation requires actual samples and generated reports")
    hashes = {}
    for report in reports:
        response = await ui.api_client.get(f"/api/reports/{report['id']}/download")
        if response.status_code != 200:
            raise AssertionError("preexisting report could not be downloaded during repair verification")
        hashes[report["id"]] = hashlib.sha256(response.content).hexdigest()
    return {"tests": tests, "sample_counts": samples, "reports": reports, "report_hashes": hashes}


def _configuration(installation) -> dict:
    root = installation.data / "config"
    return {str(path.relative_to(root)): sha256(path) for path in root.rglob("*") if path.is_file()}


async def offline_confirmation(scenes) -> None:
    installation = scenes.installation
    if installation is None:
        raise AssertionError("offline repair requires an owned Windows installation")
    await scenes.state("idle")
    scenes.stage("installer_offline_confirmation")
    await scenes.ui.go("/settings")
    await scenes.ui.page.get_by_role("heading", name="版本与维护状态", exact=True).wait_for()
    if await scenes.ui.page.locator("section.maintenance input").count():
        raise AssertionError("installed maintenance page still requires manual version entry")
    await scenes.worker.close()  # Real TLS listener disappears; do not edit application state.
    await eventually(
        lambda: scenes.ui.api("/api/status"),
        # _v2 belongs to the last received board snapshot and remains cached after
        # disconnect. The API's top-level fields reflect current link/freshness.
        lambda value: value.get("comm_quality") == "offline"
        and value.get("control_ready") is False
        and value.get("data_fresh") is False,
    )
    before = await asyncio.to_thread(_snapshot, installation)
    archives_before = await _archives(scenes.ui)
    configuration_before = await asyncio.to_thread(_configuration, installation)
    refused = await asyncio.to_thread(installation.maintenance_attempt, physical_shutdown_confirmed=False)
    after_refusal = await asyncio.to_thread(_snapshot, installation)
    _unchanged(before, after_refusal)
    if refused != 20:
        raise AssertionError("offline installer must require explicit physical-stop confirmation")
    reply = _reply(installation, "physical_confirmation_required", False)
    if reply["state"] != "confirmation_required":
        raise AssertionError("offline refusal was not the physical confirmation gate")
    # The test owns only an inert loopback simulator. Its process is stopped and
    # its previous run was explicitly acknowledged idle through the real UI.
    scenes.stage("installer_offline_repair")
    async with scenes.ui.stopped_service():
        repaired = await asyncio.to_thread(installation.maintenance_attempt, physical_shutdown_confirmed=True)
    if repaired != 0:
        raise AssertionError("confirmed offline same-version repair failed")
    after_repair = await asyncio.to_thread(_snapshot, installation)
    if after_repair["pid"] == before["pid"] or any(
        after_repair[key] != before[key] for key in ("image", "config_sha256", "gate_sha256")
    ):
        raise AssertionError("same-version repair did not restart the same installation while preserving configuration")
    # Reuse the authenticated session established by the actual first-login UI;
    # resetting accounts or signing keys would make this request fail.
    account = await scenes.ui.api("/api/auth/me")
    if account.get("username") != "admin":
        raise AssertionError("repair did not preserve the existing authenticated account")
    if await _archives(scenes.ui) != archives_before:
        raise AssertionError(
            "same-version repair changed experiment identities, sample counts or existing report bytes"
        )
    if await asyncio.to_thread(_configuration, installation) != configuration_before:
        raise AssertionError("same-version repair changed configuration or pairing key files")
    await scenes.worker.start()
    await scenes.ui.ready()
    await scenes.state("idle")
    await scenes.ui.button("刷新状态").click()
    await scenes.ui.page.get_by_text("当前无维护操作", exact=True).wait_for()
    await scenes.ui.shot("installer-offline-repair")
    _record(
        scenes,
        "offline_confirmation",
        [
            {
                "physical_shutdown_confirmed": False,
                "exit_code": refused,
                "process_id_before": before["pid"],
                "process_id_after": after_refusal["pid"],
                "same_program_configuration_database_and_gate": True,
                "maintenance_reply": reply,
            },
            {
                "physical_shutdown_confirmed": True,
                "exit_code": repaired,
                "process_id_before": before["pid"],
                "process_id_after": after_repair["pid"],
                "same_version": True,
                "configuration_and_authenticated_account_preserved": True,
                "simulator_reconnected_idle": True,
                "experiment_count": len(archives_before["tests"]),
                "sample_count": sum(archives_before["sample_counts"].values()),
                "report_count": len(archives_before["reports"]),
                "experiment_ids_sample_counts_and_report_bytes_preserved": True,
                "configuration_and_pairing_keys_preserved": True,
            },
        ],
    )
