"""Build the independent Windows bench without adding test code to the HMI payload."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_manifest(bundle: Path, version: str, commit: str, browsers: dict) -> dict:
    files = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Bench bundles must not contain links")
        if path.is_file():
            files[path.relative_to(bundle).as_posix()] = sha256(path)
    manifest = {
        "schema_version": 1,
        "name": "SmdBench",
        "platform": "windows-x64",
        "version": version,
        "commit": commit,
        "protocol_version": "2.0",
        "design_revision": "2.0-design.1",
        "scenario_version": "1",
        "playwright_version": importlib.metadata.version("playwright"),
        "browsers": [
            entry
            for entry in browsers["browsers"]
            if entry["name"] in {"chromium", "chromium-headless-shell", "ffmpeg", "winldd"}
        ],
        "python_version": sys.version.split()[0],
        "build_dependencies": [
            {"name": item.metadata["Name"], "version": item.version}
            for item in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower())
        ],
        "files": files,
    }
    with (bundle / "tool-manifest.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, ensure_ascii=False)
        output.write("\n")
    return manifest


def arguments(output: Path, work: Path) -> list[str]:
    args = ["--noconfirm", "--clean", "--onedir", "--name", "SmdBench"]
    for path in (ROOT / "backend", ROOT / "desktop", ROOT / "tools/bench"):
        args.extend(("--paths", str(path)))
    args.extend(("--distpath", str(output), "--workpath", str(work), "--specpath", str(work / "spec")))
    for package in ("smd_bench", "playwright", "pypdf"):
        args.extend(("--collect-all", package))
    for package in ("app.hostcomm.v2_contract", "app.hostcomm.v2_simulator"):
        args.extend(("--collect-submodules", package))
    for module in ("win32timezone", "win32job", "win32api", "win32process"):
        args.extend(("--hidden-import", module))
    args.append(str(ROOT / "tools/bench/entry.py"))
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    opts = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 13):
        parser.error("Build with the locked Windows x64 Python 3.13 environment")
    output, work = opts.output.resolve(), opts.work.resolve()
    if output.exists() or work.exists():
        parser.error("Use fresh bench output and work directories")
    work.mkdir(parents=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(str(ROOT / name) for name in ("backend", "desktop", "tools/bench"))
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(work / "browsers")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], env=environment, check=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", *arguments(output, work)], env=environment, check=True)
    bundle = output / "SmdBench"
    shutil.copytree(work / "browsers", bundle / "browsers")
    shutil.copy2(ROOT / "tools/bench/README.md", bundle / "README.md")
    version = subprocess.check_output([sys.executable, str(ROOT / "scripts/release/metadata.py")], text=True).strip()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    import playwright

    browser_metadata = json.loads(
        (Path(playwright.__file__).parent / "driver/package/browsers.json").read_text(encoding="utf-8")
    )
    write_manifest(bundle, version, commit, browser_metadata)
    print(f"Built independent SmdBench {version}; synthetic software validation only")


if __name__ == "__main__":
    main()
