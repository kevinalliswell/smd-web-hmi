"""离线包必须有真实、完整且校验通过的固定运行时。"""

import json
from pathlib import Path

import pytest
from smd_desktop.bundle import BundleError, build_manifest, verify_bundle, verify_version


def bundle(root: Path):
    for relative in (
        "SmdService/SmdService.exe",
        "SmdDesktop/SmdDesktop.exe",
        "SmdUpdate/SmdUpdate.exe",
        "frontend/index.html",
        "webview2/msedgewebview2.exe",
        "webview2/icudtl.dat",
        "sbom.cdx.json",
    ):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"fixture content")
    build_manifest(root, version="0.4.0-rc.1", commit="a" * 40, webview2_version="135.0.1.2")
    return root


def test_versions_must_match_tag_backend_and_frontend():
    assert verify_version("0.4.0-rc.1", "0.4.0-rc.1", "v0.4.0-rc.1") == "0.4.0-rc.1"
    with pytest.raises(BundleError):
        verify_version("0.4.0", "0.4.0-rc.1", "v0.4.0")
    with pytest.raises(BundleError):
        verify_version("0.4.0", "0.4.0", "v99.0.0")


def test_missing_runtime_is_not_an_offline_bundle(tmp_path):
    with pytest.raises(BundleError, match="运行时"):
        build_manifest(tmp_path, version="0.4.0", commit="a" * 40, webview2_version="135.0.1.2")


def test_manifest_detects_modified_runtime_and_extra_executable(tmp_path):
    root = bundle(tmp_path)
    assert verify_bundle(root)["version"] == "0.4.0-rc.1"
    (root / "webview2/icudtl.dat").write_bytes(b"changed")
    with pytest.raises(BundleError, match="校验"):
        verify_bundle(root)


def test_manifest_rejects_traversal_and_unlisted_files(tmp_path):
    root = bundle(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["../outside.exe"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        verify_bundle(root)
    bundle(root)
    (root / "injected.exe").write_bytes(b"extra")
    with pytest.raises(BundleError, match="清单"):
        verify_bundle(root)
