"""Verify the offline tool and installer against their pinned release inventories."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from .contracts import Manifest
from .ownership import assert_owned_path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_inventory(root: Path, files: dict[str, str]) -> None:
    if not files:
        raise ValueError("empty tool inventory")
    for relative, expected in files.items():
        name = PurePosixPath(relative)
        if name.is_absolute() or ".." in name.parts or "\\" in relative or ":" in relative:
            raise ValueError("unsafe inventory path")
        path = root.joinpath(*name.parts)
        assert_owned_path(path, path)
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or not path.is_file() or sha256(path) != expected:
            raise ValueError("tool inventory verification failed")


def tool_manifest() -> dict:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
        manifest = json.loads((root / "tool-manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != 1
            or manifest.get("name") != "SmdBench"
            or manifest.get("platform") != "windows-x64"
            or manifest.get("protocol_version") != "2.0"
            or manifest.get("design_revision") != "2.0-design.1"
            or manifest.get("scenario_version") != "1"
        ):
            raise ValueError("unsupported tool manifest")
        verify_inventory(root, manifest["files"])
        return manifest
    from app import __version__

    repo = Path(__file__).resolve().parents[3]
    # REST 归档检出(无 .git)时回退 CI 注入的 GITHUB_SHA(同一棵树)。
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = os.environ.get("GITHUB_SHA", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise
    return {"version": __version__, "commit": commit, "source_execution": True, "scenario_version": "1"}


def verify_installer(installer: Path, manifest_file: Path, tool: dict) -> tuple[Manifest, str]:
    installer, manifest_file = installer.resolve(strict=True), manifest_file.resolve(strict=True)
    manifest = Manifest.model_validate_json(manifest_file.read_bytes())
    if (
        manifest.version != tool["version"]
        or manifest.commit != tool["commit"]
        or installer.name != f"SmdHmi-{manifest.version}-windows-x64.exe"
        or not manifest.compatibility.get("database_revision")
    ):
        raise ValueError("installer and tool must have identical version and source commit")
    if (
        manifest.compatibility.get("hostcomm_version") != "2.0"
        or manifest.compatibility.get("hostcomm_v2_design_revision") != "2.0-design.1"
    ):
        raise ValueError("installer protocol and design must match the frozen acceptance tool")
    expected = []
    for line in (installer.parent / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if match and match[2] == installer.name:
            expected.append(match[1].lower())
    actual = sha256(installer)
    if expected != [actual]:
        raise ValueError("installer checksum missing, duplicated or incorrect")
    return manifest, actual
