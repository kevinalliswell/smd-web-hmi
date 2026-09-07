"""Ownership checks shared by installation and interrupted-run cleanup."""

import json
import os
import stat
from pathlib import Path

from .contracts import validate_run_id

PURPOSE = "SmdBench isolated installation"


def validate_marker(value: dict, run_id: str) -> None:
    validate_run_id(run_id)
    if value != {"schema_version": 1, "run_id": run_id, "purpose": PURPOSE}:
        raise ValueError("directory ownership marker does not match this run")


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def assert_owned_path(path: Path, expected: Path, *, recursive: bool = False) -> None:
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(expected)):
        raise ValueError("cleanup path differs from its fixed run location")
    for parent in (path, *path.parents):
        if parent.exists() and is_link(parent):
            raise ValueError("reparse points are not allowed in owned paths")
    if recursive and path.exists():
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in folders + files:
                if is_link(Path(directory) / name):
                    raise ValueError("owned directory contains a reparse point")


def marker_path(path: Path, run_id: str) -> Path:
    return path / f".smd-bench-{validate_run_id(run_id)}.json"


def claim(path: Path, run_id: str) -> None:
    path.mkdir(parents=False, exist_ok=False)
    marker_path(path, run_id).write_text(
        json.dumps({"schema_version": 1, "run_id": run_id, "purpose": PURPOSE}), encoding="ascii"
    )


def check_claim(path: Path, run_id: str) -> None:
    assert_owned_path(path, path, recursive=True)
    validate_marker(json.loads(marker_path(path, run_id).read_text(encoding="ascii")), run_id)
