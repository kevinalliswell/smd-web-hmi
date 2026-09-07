"""Release only the exact kit that completed every installed-loop scenario."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("bench_package", ROOT / "scripts/release/package_bench.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def evidence():
    return {
        "schema_version": 1,
        "version": "0.3.0-rc.5",
        "commit": "a" * 40,
        "status": "passed",
        "cleanup_complete": True,
        "scenario": "all",
        "scenario_version": "1",
        "installer_sha256": "b" * 64,
        "tool_manifest_sha256": "c" * 64,
        "assertions": [{"name": name, "status": "passed"} for name in package.REQUIRED_CHECKS],
    }


@pytest.mark.parametrize(
    "change", [{"commit": "b" * 40}, {"cleanup_complete": False}, {"scenario": "full"}, {"scenario_version": "2"}]
)
def test_release_rejects_wrong_build_or_incomplete_run(change):
    candidate = {**evidence(), **change}
    with pytest.raises(ValueError):
        package.validate_evidence(candidate, evidence(), installer_sha256="b" * 64, tool_manifest_sha256="c" * 64)


def test_release_rejects_missing_or_skipped_scenario():
    candidate = evidence()
    candidate["assertions"].pop()
    with pytest.raises(ValueError):
        package.validate_evidence(candidate, evidence(), installer_sha256="b" * 64, tool_manifest_sha256="c" * 64)
    candidate = evidence()
    candidate["assertions"][0]["status"] = "skipped"
    with pytest.raises(ValueError):
        package.validate_evidence(candidate, evidence(), installer_sha256="b" * 64, tool_manifest_sha256="c" * 64)


def test_release_accepts_complete_matching_evidence():
    candidate = evidence()
    package.validate_evidence(candidate, evidence(), installer_sha256="b" * 64, tool_manifest_sha256="c" * 64)


@pytest.mark.parametrize("key", ["installer_sha256", "tool_manifest_sha256"])
def test_same_commit_with_changed_build_bytes_cannot_reuse_acceptance(key):
    candidate = evidence()
    candidate[key] = "d" * 64
    with pytest.raises(ValueError):
        package.validate_evidence(candidate, evidence(), installer_sha256="b" * 64, tool_manifest_sha256="c" * 64)


def test_inventory_reports_differences_without_file_contents_or_rewriting_manifest(tmp_path):
    executable = tmp_path / "SmdBench.exe"
    executable.write_bytes(b"original")
    manifest = {"files": {"SmdBench.exe": package.digest(executable), "removed.dll": "a" * 64}}
    executable.write_bytes(b"private changed payload")
    (tmp_path / "added.cache").write_bytes(b"private cache payload")
    with pytest.raises(ValueError) as failure:
        package.verify_inventory(tmp_path, manifest)
    summary = json.loads(str(failure.value).split(": ", 1)[1])
    assert summary == {
        "added": {"count": 1, "paths": ["added.cache"]},
        "removed": {"count": 1, "paths": ["removed.dll"]},
        "changed": {"count": 1, "paths": ["SmdBench.exe"]},
    }
    assert "private" not in str(failure.value) and str(tmp_path) not in str(failure.value)
    assert manifest["files"]["SmdBench.exe"] != package.digest(executable)


def test_inventory_only_is_read_only_and_does_not_issue_acceptance(tmp_path):
    executable = tmp_path / "SmdBench.exe"
    executable.write_bytes(b"frozen")
    manifest = tmp_path / "tool-manifest.json"
    manifest.write_text(json.dumps({"files": {"SmdBench.exe": package.digest(executable)}}), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    command = [
        sys.executable,
        str(ROOT / "scripts/release/package_bench.py"),
        "--bundle",
        str(tmp_path),
        "--inventory-only",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    (tmp_path / "unexpected.cache").write_bytes(b"private data")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode != 0 and '"added"' in result.stderr
    assert "private data" not in result.stderr
    assert manifest.read_bytes() == before["tool-manifest.json"]
