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
sys.path.insert(0, str(ROOT / "backend"))
from smd_desktop.bundle import build_manifest, verify_version


def source_version():
    source = (ROOT / "backend/app/__init__.py").read_text(encoding="utf-8")
    backend = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', source).group(1)
    frontend = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
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
    packages = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))["packages"]
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
        runtime = json.loads((ROOT / "desktop/webview2.lock.json").read_text(encoding="utf-8"))
        sbom(args.bundle, version, runtime)
        from app.db.database import get_expected_schema_head

        build_manifest(
            args.bundle,
            version=version,
            commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
            webview2_version=runtime["version"],
            compatibility={
                "database_revision": get_expected_schema_head(),
                "hostcomm_version": "2.0",
                "hostcomm_supported_versions": ["1.0", "2.0"],
                "new_install_protocol": "2.0 (unpaired; explicit offline pairing required)",
                "upgrade_protocol": "preserve existing configuration; no automatic protocol switch or downgrade",
                "hostcomm_v2_design_revision": "2.0-design.1",
                "hostcomm_v2_security": "TLS 1.3 external PSK / AES_128_GCM_SHA256 / secp256r1; Python 3.13",
                "required_capabilities": ["durable_operations", "atomic_recipe", "sample_log", "alarm_log"],
                "firmware_validation": "unverified: real firmware must pass the documented contract and M5 acceptance",
                "windows_validation": "CI build only; clean offline Win10/11 acceptance required",
            },
        )


if __name__ == "__main__":
    main()
