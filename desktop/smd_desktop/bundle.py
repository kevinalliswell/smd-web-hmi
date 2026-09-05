"""离线交付清单：每个版本自带运行时，缺件或校验失败直接拒绝。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
REQUIRED = (
    "SmdService/SmdService.exe",
    "SmdDesktop/SmdDesktop.exe",
    "SmdUpdate/SmdUpdate.exe",
    "frontend/index.html",
    "webview2/msedgewebview2.exe",
    "webview2/icudtl.dat",
    "sbom.cdx.json",
)


class BundleError(ValueError):
    """版本或离线包内容不可信。"""


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_version(backend: str, frontend: str, tag: str | None = None) -> str:
    if not VERSION.fullmatch(backend) or backend != frontend or (tag and tag != f"v{backend}"):
        raise BundleError("tag、后端、前端版本必须完全一致且符合 SemVer")
    return backend


def _files(root: Path) -> dict[str, Path]:
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise BundleError("离线包禁止符号链接或重解析目录")
        if path.is_file() and path != root / "manifest.json":
            name = path.relative_to(root).as_posix()
            if path.name in {".env", "service.env"} or path.suffix in {".db", ".sqlite", ".pem", ".key"}:
                raise BundleError("离线包不得包含现场数据库、配置或私钥")
            files[name] = path
    return files


def build_manifest(root: Path, *, version: str, commit: str, webview2_version: str) -> dict:
    verify_version(version, version)
    files = _files(root)
    missing = [name for name in REQUIRED if name not in files]
    if missing:
        raise BundleError(f"缺少真实离线运行时/必要产物: {missing}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", webview2_version):
        raise BundleError("必须记录构建 commit 和固定 WebView2 四段版本")
    manifest = {
        "schema_version": 1,
        "version": version,
        "commit": commit,
        "prerelease": "-" in version,
        "platform": "windows-x64",
        "python": "3.13",
        "webview2_version": webview2_version,
        "files": {name: sha256(path) for name, path in sorted(files.items())},
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_bundle(root: Path) -> dict:
    root = root.resolve()
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        verify_version(manifest["version"], manifest["version"])
        if manifest.get("schema_version") != 1 or manifest.get("platform") != "windows-x64":
            raise BundleError("离线包 schema/平台不匹配")
        listed = manifest["files"]
        if not isinstance(listed, dict) or any(name not in listed for name in REQUIRED):
            raise BundleError("离线包缺少必要清单内容")
        for name, expected in listed.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise BundleError("清单路径不得越出版本目录")
            target = (root / name).resolve()
            if not target.is_relative_to(root) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise BundleError("清单路径/摘要无效")
            if not target.is_file() or sha256(target) != expected:
                raise BundleError(f"离线包校验失败: {name}")
        if set(_files(root)) != set(listed):
            raise BundleError("目录内容与清单不一致")
        return manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BundleError("无法读取完整离线包清单") from exc
