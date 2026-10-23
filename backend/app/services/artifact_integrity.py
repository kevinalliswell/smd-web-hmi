"""Verify an archived file against its recorded digest before serving it."""

import asyncio
import hashlib
from pathlib import Path


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def verify(path: Path, expected: str | None) -> None:
    if not expected or await asyncio.to_thread(file_digest, path) != expected:
        raise ValueError("artifact_digest_mismatch")
