"""Measure the unmodified API against disposable, explicitly synthetic loopback data.

Run with backend/.venv/Scripts/python.exe. Does not issue physical commands or
read installed service configuration. Output never contains credentials/tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import platform
import secrets
import site
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def stop_child(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def free_port() -> int:
    with socket.socket() as channel:
        channel.bind(("127.0.0.1", 0))
        return channel.getsockname()[1]


def working_set(pid: int) -> int | None:
    """Current Windows child working set; not whole-machine or desktop memory."""
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in ("peak", "working", "pool_peak", "pool", "nonpaged_peak", "nonpaged", "page", "page_peak")
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
    if not handle:
        return None
    try:
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        return counters.working if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb) else None
    finally:
        kernel.CloseHandle(handle)


async def seed(password: str, count: int) -> None:
    from app.core.security import hash_password
    from app.db.database import create_all, dispose_engine, get_sessionmaker
    from app.db.models import SamplePoint, TestSession, UserAccount

    await create_all()
    async with get_sessionmaker()() as session:
        session.add(
            UserAccount(
                username="baseline_only",
                hashed_pw=hash_password(password),
                role="admin",
                display_name="SYNTHETIC BASELINE",
                is_active=1,
                must_change_password=0,
                created_at="2026-01-01T00:00:00Z",
            )
        )
        session.add(
            TestSession(
                test_id="SYNTHETIC-BASELINE",
                operator_id="baseline_only",
                sample_label="SYNTHETIC / NOT A VALID STANDARD EXPERIMENT",
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-01-02T00:00:00Z",
                original_height_mm=20,
                phase="needs_review",
                mode="custom",
                data_integrity="unknown",
            )
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for offset in range(count):
            session.add(
                SamplePoint(
                    test_id="SYNTHETIC-BASELINE",
                    ts=(start + timedelta(seconds=offset)).isoformat(),
                    furnace_pv=600 + offset % 900,
                    burden_temp=590 + offset % 900,
                    delta_p=offset % 300,
                    drip_weight=offset / 100,
                    displacement=offset / max(count, 1) * 10,
                    source="synthetic_baseline",
                )
            )
        await session.commit()
    await dispose_engine()


async def measure(base: str, password: str) -> dict:
    import httpx
    from websockets.asyncio.client import connect

    results: dict = {"reports": {}}
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as client:
        login = await client.post("/api/auth/login", json={"username": "baseline_only", "password": password})
        login.raise_for_status()
        token = login.json()["data"]["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        deadline = time.monotonic() + 15
        while True:
            response = await client.get("/api/status")
            response.raise_for_status()
            status = response.json()["data"]
            if status.get("comm_quality") == "online":
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Loopback mock never became online")
            await asyncio.sleep(0.1)
        results["hostcomm_quality"] = status["comm_quality"]
        async with connect(base.replace("http://", "ws://") + "/ws/realtime", proxy=None) as ws:
            await ws.send(json.dumps({"type": "authenticate", "token": token}))
            await asyncio.wait_for(ws.recv(), 5)
            await ws.send(json.dumps({"type": "ping"}))
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if message["type"] == "pong":
                    results["websocket_authenticated_ping"] = True
                    break
        for fmt, magic in (("html", b"<!"), ("pdf", b"%PDF"), ("xlsx", b"PK")):
            started = time.perf_counter()
            response = await client.post("/api/reports/generate", json={"test_id": "SYNTHETIC-BASELINE", "format": fmt})
            response.raise_for_status()
            task_id = response.json()["data"]["task_id"]
            deadline = time.monotonic() + 90
            while True:
                snapshot = (await client.get(f"/api/reports/tasks/{task_id}")).json()["data"]
                if snapshot["status"] == "completed":
                    break
                if snapshot["status"] in {"failed", "interrupted"} or time.monotonic() > deadline:
                    raise RuntimeError(f"{fmt} report did not complete")
                await asyncio.sleep(0.05)
            download = await client.get(snapshot["result"]["download_url"])
            download.raise_for_status()
            if not download.content.lstrip().startswith(magic):
                raise RuntimeError(f"Invalid {fmt} artifact signature")
            results["reports"][fmt] = {
                "seconds": round(time.perf_counter() - started, 3),
                "bytes": len(download.content),
            }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=3600)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 13) or not 1 <= args.samples <= 100000:
        parser.error("Python 3.13 and 1..100000 synthetic samples required")
    args.output.mkdir(parents=True, exist_ok=False)
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scope": "loopback HostComm 1.0 mock; synthetic incomplete data; no device or installed service",
        "synthetic_samples": args.samples,
    }
    with tempfile.TemporaryDirectory(prefix="smd-baseline-") as scratch:
        area = Path(scratch)
        password = secrets.token_urlsafe(24)
        os.environ.update(
            HOSTCOMM_MOCK="true",
            PROTOCOL_VERSION="1.0",
            HOSTCOMM_HOST="127.0.0.1",
            HOSTCOMM_PORT=str(free_port()),
            SMD_HOST="127.0.0.1",
            SMD_PORT=str(free_port()),
            SMD_DB_PATH=str(area / "synthetic.sqlite"),
            SMD_MAINTENANCE_FILE=str(area / "maintenance.json"),
            SMD_JWT_SECRET=secrets.token_urlsafe(48),
            SMD_FRONTEND_DIST=str(ROOT / "frontend/dist"),
        )
        asyncio.run(seed(password, args.samples))
        started = time.perf_counter()
        # Avoid measuring the Windows venv redirector instead of the real server.
        executable = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
        child_env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "backend"), *site.getsitepackages()]))
        with (
            (args.output / "service.log").open("w", encoding="utf-8") as output,
            (args.output / "mock.log").open("w", encoding="utf-8") as mock_output,
            ExitStack() as children,
        ):
            mock = subprocess.Popen(
                [
                    executable,
                    "-X",
                    "utf8",
                    "-m",
                    "app.hostcomm.mock_server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    os.environ["HOSTCOMM_PORT"],
                ],
                cwd=ROOT / "backend",
                stdout=mock_output,
                stderr=mock_output,
                env=child_env,
            )
            children.callback(stop_child, mock)
            process = subprocess.Popen(
                [executable, "-X", "utf8", "-m", "app.server"],
                cwd=ROOT / "backend",
                stdout=output,
                stderr=output,
                env=child_env,
            )
            children.callback(stop_child, process)
            try:
                import httpx

                base = f"http://127.0.0.1:{os.environ['SMD_PORT']}"
                with httpx.Client(base_url=base, timeout=2, trust_env=False) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            response = client.get("/api/system/health")
                            response.raise_for_status()
                            if response.json()["data"]["status"] == "ready":
                                break
                        except httpx.HTTPError:
                            pass
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Baseline service did not become ready; inspect service.log")
                        time.sleep(0.1)
                result["startup_ready_seconds"] = round(time.perf_counter() - started, 3)
                result["service_working_set_before_bytes"] = working_set(process.pid)
                result.update(asyncio.run(measure(base, password)))
                result["service_working_set_after_bytes"] = working_set(process.pid)
                result["outcome"] = "passed"
            finally:
                (args.output / "baseline.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
