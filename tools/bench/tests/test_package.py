import hashlib
import json

import pytest
from smd_bench.package import verify_installer, verify_inventory


def test_inventory_rejects_path_escape_and_modified_file(tmp_path):
    exe = tmp_path / "SmdBench.exe"
    exe.write_bytes(b"frozen tool")
    files = {"SmdBench.exe": hashlib.sha256(exe.read_bytes()).hexdigest()}
    verify_inventory(tmp_path, files)
    with pytest.raises(ValueError):
        verify_inventory(tmp_path, {"../outside": "0" * 64})
    exe.write_bytes(b"changed")
    with pytest.raises(ValueError):
        verify_inventory(tmp_path, files)


def test_installer_requires_matching_tool_commit_and_checksum(tmp_path):
    version, commit = "0.3.0-rc.5", "a" * 40
    installer = tmp_path / f"SmdHmi-{version}-windows-x64.exe"
    installer.write_bytes(b"fixture")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    (tmp_path / "SHA256SUMS.txt").write_text(f"{digest}  {installer.name}\n", encoding="ascii")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "windows-x64",
                "version": version,
                "commit": commit,
                "compatibility": {"database_revision": "test"},
            }
        )
    )
    verified, actual = verify_installer(installer, manifest, {"version": version, "commit": commit})
    assert actual == digest and verified.commit == commit
    with pytest.raises(ValueError):
        verify_installer(installer, manifest, {"version": version, "commit": "b" * 40})
    installer.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        verify_installer(installer, manifest, {"version": version, "commit": commit})


def test_frozen_tool_rejects_different_scenario_before_file_inventory(tmp_path, monkeypatch):
    import sys

    from smd_bench.package import tool_manifest

    (tmp_path / "tool-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "SmdBench",
                "platform": "windows-x64",
                "protocol_version": "2.0",
                "design_revision": "2.0-design.1",
                "scenario_version": "2",
                "files": {"SmdBench.exe": "0" * 64},
            }
        )
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SmdBench.exe"))
    with pytest.raises(ValueError, match="unsupported tool manifest"):
        tool_manifest()
