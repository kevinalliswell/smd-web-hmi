"""Tauri keeps the existing bundle/version contract and does not replace the service."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tauri_metadata", ROOT / "scripts/release/metadata.py")
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


def version_tree(root):
    for name in ("backend/app", "frontend", "desktop-tauri"):
        (root / name).mkdir(parents=True)
    (root / "backend/app/__init__.py").write_text('__version__ = "0.3.0"', encoding="utf-8")
    (root / "frontend/package.json").write_text('{"version":"0.3.0"}', encoding="utf-8")
    (root / "frontend/package-lock.json").write_text(
        '{"version":"0.3.0","packages":{"":{"version":"0.3.0"}}}', encoding="utf-8"
    )
    (root / "desktop-tauri/Cargo.toml").write_text('[package]\nversion = "0.3.0"', encoding="utf-8")
    (root / "desktop-tauri/tauri.conf.json").write_text('{"version":"0.3.0"}', encoding="utf-8")


@pytest.mark.parametrize("file", ["Cargo.toml", "tauri.conf.json"])
def test_tauri_version_drift_blocks_existing_bundle(tmp_path, monkeypatch, file):
    version_tree(tmp_path)
    monkeypatch.setattr(metadata, "ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    assert metadata.source_version() == "0.3.0"
    target = tmp_path / "desktop-tauri" / file
    target.write_text(target.read_text().replace("0.3.0", "0.4.0"), encoding="utf-8")
    with pytest.raises(ValueError, match="Tauri source versions differ"):
        metadata.source_version()


def test_staged_rust_lock_is_included_with_checksums_in_sbom(tmp_path):
    (tmp_path / "SmdDesktop").mkdir()
    (tmp_path / "SmdDesktop/Cargo.lock").write_text(
        'version = 4\n[[package]]\nname = "shell"\nversion = "0.3.0"\n'
        '[[package]]\nname = "tauri"\nversion = "2.12.5"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{"a" * 64}"\n',
        encoding="utf-8",
    )
    metadata.sbom(tmp_path, "0.3.0", {"version": "1", "sha256": "b" * 64, "url": "https://example.test"})
    components = json.loads((tmp_path / "sbom.cdx.json").read_text(encoding="utf-8"))["components"]
    rust = [item for item in components if item.get("purl", "").startswith("pkg:cargo/")]
    assert len(rust) == 1
    assert rust[0]["purl"] == "pkg:cargo/tauri@2.12.5"
    assert rust[0]["hashes"] == [{"alg": "SHA-256", "content": "a" * 64}]


def test_tauri_freeze_preserves_service_and_updater(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("tauri_freeze", ROOT / "scripts/release/freeze.py")
    freeze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze)
    builds = []
    monkeypatch.setattr(freeze.subprocess, "run", lambda args, **kwargs: builds.append(args))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze.py",
            "--output",
            str(tmp_path / "payload"),
            "--work",
            str(tmp_path / "work"),
            "--desktop-shell",
            "tauri",
        ],
    )
    freeze.main()
    assert [args[args.index("--name") + 1] for args in builds] == ["SmdService", "SmdUpdate", "SmdRender"]
