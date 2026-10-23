"""Single render process at a time; scrub service environment and reap on timeout/cancel."""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.services.job_store import encode

_slot = asyncio.Semaphore(1)
_state = {"running": False, "pid": None, "completed": 0, "failures": 0}


class RenderError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def metrics() -> dict[str, Any]:
    return dict(_state, concurrency=1)


async def render_snapshot(
    snapshot: dict[str, Any], directory: Path, *, min_free_bytes: int = 0, timeout: float = 300
) -> bytes:
    async with _slot:
        if (await asyncio.to_thread(shutil.disk_usage, directory)).free < min_free_bytes:
            raise RenderError("insufficient_disk_space")
        temporary = Path(tempfile.mkdtemp(prefix=".render-", dir=directory))
        process = None
        try:
            source, output = temporary / "snapshot.json", temporary / "artifact"
            await asyncio.to_thread(source.write_text, encode(snapshot), encoding="utf-8")
            environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
            environment["PYTHONUTF8"] = "1"
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            if getattr(sys, "frozen", False):
                executable = Path(sys.executable).resolve().parent.parent / "SmdRender" / "SmdRender.exe"
                command = [str(executable)]
            else:
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
                command = [sys.executable, "-m", "app.render_worker"]
            kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}
            process = await asyncio.create_subprocess_exec(
                *command,
                "--snapshot",
                str(source),
                "--output",
                str(output),
                cwd=temporary,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                **kwargs,
            )
            _state.update(running=True, pid=process.pid)
            try:
                async with asyncio.timeout(timeout):
                    code = await process.wait()
            except TimeoutError as exc:
                raise RenderError("render_timeout") from exc
            if code != 0 or not output.is_file():
                raise RenderError("render_worker_failed")
            if output.stat().st_size > 32 * 1024 * 1024:
                raise RenderError("report_size_limit")
            content = await asyncio.to_thread(output.read_bytes)
            magic = {"html": b"<!DOCTYPE html>", "pdf": b"%PDF", "xlsx": b"PK"}[snapshot["format"]]
            if not content.startswith(magic):
                raise RenderError("invalid_render_artifact")
            _state["completed"] += 1
            return content
        except BaseException:
            _state["failures"] += 1
            raise
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            _state.update(running=False, pid=None)
            await asyncio.to_thread(shutil.rmtree, temporary)
