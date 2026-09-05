"""Validate source versions and inventory the exact freeze/build dependencies."""

import argparse
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop"))
from smd_desktop.bundle import build_manifest, verify_version


def source_version():
    source = (ROOT / "backend/app/__init__.py").read_text(encoding="utf-8")
    backend = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', source).group(1)
    frontend = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    if lock["version"] != frontend or lock["packages"][""]["version"] != frontend:
        raise ValueError("package-lock version differs from package.json")
    tag = os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
    return verify_version(backend, frontend, tag)


def sbom(root: Path, version: str, runtime: dict):
    components = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name, revision = dist.metadata["Name"], dist.version
        components.append(
            {
                "type": "library",
                "name": name,
                "version": revision,
                "purl": f"pkg:pypi/{name.lower()}@{revision}",
                "properties": [{"name": "smd:inventory", "value": "python-freeze-environment"}],
            }
        )
    packages = json.loads((ROOT / "frontend/package-lock.json").read_text())["packages"]
    for path, item in sorted(packages.items()):
        if not path or "version" not in item:
            continue
        name = item.get("name") or path.rsplit("node_modules/", 1)[1]
        components.append(
            {
                "type": "library",
                "name": name,
                "version": item["version"],
                "properties": [
                    {"name": "smd:inventory", "value": "frontend-build-lock"},
                    {"name": "smd:dev", "value": str(item.get("dev", False)).lower()},
                ],
            }
        )
    components.append(
        {
            "type": "framework",
            "name": "Microsoft Edge WebView2 Fixed Runtime",
            "version": runtime["version"],
            "hashes": [{"alg": "SHA-256", "content": runtime["sha256"]}],
            "externalReferences": [{"type": "distribution", "url": runtime["url"]}],
        }
    )
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "smd-web-hmi", "version": version}},
        "components": components,
    }
    (root / "sbom.cdx.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    version = source_version()
    print(version)
    if args.bundle:
        runtime = json.loads((ROOT / "desktop/webview2.lock.json").read_text())
        sbom(args.bundle, version, runtime)
        build_manifest(
            args.bundle,
            version=version,
            commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
            webview2_version=runtime["version"],
        )


if __name__ == "__main__":
    main()
