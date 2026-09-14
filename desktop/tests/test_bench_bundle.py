"""A standalone test kit must describe the exact browser and executable bytes shipped."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("bench_freeze", ROOT / "tools/bench/freeze.py")
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


@pytest.fixture(autouse=True)
def build_dependency_version(monkeypatch):
    # Inventory tests do not need the platform-specific browser build environment.
    monkeypatch.setattr(freeze.importlib.metadata, "version", lambda _name: "1.62.0")


def test_bench_inventory_covers_runtime_and_browser_bytes(tmp_path):
    (tmp_path / "SmdBench.exe").write_bytes(b"binary")
    browser = tmp_path / "browsers/chromium/chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"browser")
    manifest = freeze.write_manifest(tmp_path, "0.3.0-rc.5", "a" * 40, {"browsers": []})
    assert manifest["files"]["browsers/chromium/chrome.exe"] == freeze.sha256(browser)
    assert manifest["files"]["SmdBench.exe"] == freeze.sha256(tmp_path / "SmdBench.exe")
    assert manifest["protocol_version"] == "2.0"
    assert manifest["design_revision"] == "2.0-design.1"
    assert "tool-manifest.json" not in manifest["files"]
    assert json.loads((tmp_path / "tool-manifest.json").read_text(encoding="utf-8")) == manifest
    with pytest.raises(FileExistsError):
        freeze.write_manifest(tmp_path, "0.3.0-rc.5", "a" * 40, {"browsers": []})


def test_bench_inventory_refuses_linked_content(tmp_path):
    (tmp_path / "SmdBench.exe").write_bytes(b"binary")
    try:
        (tmp_path / "external").symlink_to(tmp_path / "SmdBench.exe")
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode")
    with pytest.raises(ValueError, match="link"):
        freeze.write_manifest(tmp_path, "0.3.0-rc.5", "a" * 40, {"browsers": []})
