"""Release only the exact kit that completed every installed-loop scenario."""

import importlib.util
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
