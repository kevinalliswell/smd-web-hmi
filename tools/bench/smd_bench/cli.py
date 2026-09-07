"""Public standalone tool entry points. Unexpected failures always fail acceptance."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx

from .contracts import validate_run_id
from .diagnostics import failure_details
from .installation import Installation, preflight
from .package import sha256, tool_manifest, verify_installer


def parser():
    result = argparse.ArgumentParser(
        description="SmdBench: inert Windows HMI acceptance on a clean, dedicated test machine"
    )
    commands = result.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("preflight", help="read-only: refuse any existing installation or unsafe environment")
    inspect.add_argument("--installer", type=Path)
    inspect.add_argument("--manifest", type=Path)
    run = commands.add_parser(
        "run", help="install, pair and test a NEW owned installation; always attempt owned cleanup"
    )
    run.add_argument("--installer", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--evidence", type=Path, required=True)
    run.add_argument("--run-id", type=validate_run_id, default=None)
    run.add_argument("--scenario", choices=("full", "faults", "all"), default="all")
    cleanup = commands.add_parser("cleanup", help="remove only this tool's owned installation; retain private evidence")
    cleanup.add_argument("--run-id", type=validate_run_id, required=True)
    commands.add_parser("driver", help=argparse.SUPPRESS)
    commands.add_parser("self-check", help="verify offline tool inventory and bundled browser")
    return result


async def wait_health(base="http://127.0.0.1:8000", *, online=False):
    from .browser import eventually

    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:

        async def fetch():
            try:
                response = await client.get(base + "/api/system/health")
            except httpx.RequestError:
                return None
            response.raise_for_status()
            return response.json()["data"]

        health = await eventually(fetch, lambda value: bool(value and value["status"] == "ready"))
    if not online and health["checks"]["hostcomm"] != "offline":
        raise AssertionError("new installation unexpectedly connected to a device")
    for key in ("database", "schema", "storage", "backup"):
        if health["checks"][key] != "ok":
            raise AssertionError("installed application health failed")
    return health


async def run_scenarios(installation, result, scenario):
    from .browser import Browser, eventually
    from .scenarios import Scenarios
    from .worker import Worker

    ui = Browser(Path(result["evidence_dir"]))
    worker = None
    try:
        await ui.open()
        initial = (installation.data / "config/bootstrap-admin-password.txt").read_text(encoding="utf-8-sig").strip()
        await ui.first_login(initial)
        del initial
        result["last_stage"] = "offline_pairing"
        ui.current_stage = "offline_pairing"
        pairing = await asyncio.to_thread(installation.pair)
        worker = Worker(installation.private, pairing)
        await worker.start(wrong_psk=True)
        await asyncio.to_thread(installation.start)
        # Observe several real connection attempts; wrong credentials must never yield hello or control.
        wrong = await eventually(
            lambda: worker.request("snapshot"), lambda value: value["psk_attempts"] > 0, timeout=60
        )
        status = await ui.api("/api/status")
        if wrong["tls_handshakes"] or status["system"].get("can_start_test"):
            raise AssertionError("wrong PSK reached an authenticated or controllable state")
        await worker.close()
        await worker.start()
        await ui.ready()
        authenticated = await worker.request("snapshot")
        if not authenticated["tls_handshakes"] or any(
            row["version"] != "TLSv1.3" or row["cipher"] != "TLS_AES_128_GCM_SHA256"
            for row in authenticated["tls_handshakes"]
        ):
            raise AssertionError("installed service did not negotiate the frozen TLS policy")
        result["assertions"].append({"name": "tls_authentication", "status": "passed"})
        scenes = Scenarios(ui, worker, installation.data / "db/smd.db", installation.run_id, result, installation)
        if scenario in {"full", "all"}:
            await scenes.full()
        else:
            await scenes.recipes()
        if scenario in {"faults", "all"}:
            from .faults import faults

            await faults(scenes)
        if ui.page_errors or ui.api_failures or ui.console_errors:
            raise AssertionError("unexpected browser or API errors were observed")
        result["browser_errors"] = len(ui.page_errors)
        result["unexpected_api_errors"] = len(ui.api_failures)
        result["unexpected_console_errors"] = len(ui.console_errors)
        result["expected_fault_console_errors"] = len(ui.expected_console_errors)
    finally:
        try:
            if worker:
                await worker.close()
        finally:
            await ui.close()


def run(args):
    from . import windows

    tool = tool_manifest()
    manifest, digest = verify_installer(args.installer, args.manifest, tool)
    found = preflight()
    if found["status"] != "ready":
        raise ValueError("preflight refused; use a clean dedicated Windows test machine")
    installation = Installation(args.run_id or uuid4().hex, found, manifest.version)
    evidence = args.evidence.resolve()
    for root in (installation.private, installation.data, installation.install, Path(sys.executable).parent):
        if evidence == root or evidence.is_relative_to(root):
            raise ValueError("public evidence must remain outside the installation, secrets and tool bundle")
    result = {
        "schema_version": 1,
        "run_id": installation.run_id,
        "version": manifest.version,
        "commit": manifest.commit,
        "installer_sha256": digest,
        "scenario": args.scenario,
        "status": "failed",
        "cleanup_complete": False,
        "assertions": [],
        "evidence_dir": str(evidence),
        "boundary": "inert simulator with injected process boundaries; not firmware or physical timing certification",
    }
    result.update(scenario_version="1", protocol_version="2.0", design_revision="2.0-design.1")
    if getattr(sys, "frozen", False):
        result["tool_manifest_sha256"] = sha256(Path(sys.executable).parent / "tool-manifest.json")
    else:
        result["source_execution"] = True
    print(
        json.dumps(
            {
                "run_id": installation.run_id,
                "phase": "before_installation",
                "cleanup_command": "SmdBench.exe cleanup --run-id " + installation.run_id,
            }
        ),
        flush=True,
    )
    passed = False
    try:
        installation.prepare(evidence, manifest, digest)
        windows.contain_child_processes()
        result["last_stage"] = "installation"
        installation.install_package(args.installer.resolve())
        health = asyncio.run(wait_health())
        if health["version"] != manifest.version:
            raise AssertionError("installed HTTP version differs from package")
        result["assertions"].append({"name": "installed_service", "status": "passed"})
        asyncio.run(run_scenarios(installation, result, args.scenario))
        passed = True
    except Exception as error:
        # Fail closed at the CLI boundary. Never serialize error bodies (browser errors can contain credentials).
        diagnostic = failure_details(
            error, private=installation.private if installation.claimed else None, run_id=installation.run_id
        )
        result["failure_type"] = diagnostic.pop("type")
        result["failure_frames"] = diagnostic.pop("frames")
        result.update(diagnostic)
        result["cleanup_command"] = "SmdBench.exe cleanup --run-id " + installation.run_id
    finally:
        try:
            if installation.claimed:
                installation.cleanup()
            result["cleanup_complete"] = True
        except Exception as error:
            diagnostic = failure_details(
                error, private=installation.private if installation.claimed else None, run_id=installation.run_id
            )
            result["cleanup_failure_type"] = diagnostic["type"]
            result["cleanup_failure_frames"] = diagnostic["frames"]
            result["cleanup_private_trace_saved"] = diagnostic["private_trace_saved"]
            result["cleanup_command"] = "SmdBench.exe cleanup --run-id " + installation.run_id
        if passed and result["cleanup_complete"]:
            result["status"] = "passed"
        result.pop("evidence_dir", None)
        if not installation.evidence_created:
            raise RuntimeError("preparation failed before evidence directory creation")
        (evidence / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("run_id", "status", "cleanup_complete")}), flush=True)
    return 0 if result["status"] == "passed" else 1


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "driver":
            from .driver import main as driver_main

            driver_main()
            return 0
        if args.command == "run":
            return run(args)
        if args.command == "cleanup":
            Installation.recover(args.run_id).cleanup()
            print(json.dumps({"run_id": args.run_id, "cleanup_complete": True}))
            return 0
        if args.command == "self-check":
            from .browser import Browser

            tool_manifest()

            async def check():
                browser = Browser(Path.cwd())
                try:
                    await browser.open()
                    await browser.page.set_content("<p>SmdBench browser self-check</p>")
                finally:
                    await browser.close()

            asyncio.run(check())
            print('{"self_check":"passed"}')
            return 0
        tool = tool_manifest()
        if bool(args.installer) != bool(args.manifest):
            raise ValueError("installer and manifest must be supplied together")
        if args.installer:
            verify_installer(args.installer, args.manifest, tool)
        result = preflight()
        print(json.dumps(result))
        return 0 if result["status"] == "ready" else 2
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(error).__name__, "frames": failure_details(error)["frames"]}
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
