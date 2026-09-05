import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("publish_gate", ROOT / "scripts/release/publish_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def manifest(version="0.3.0-rc.2", **changes):
    return {
        "schema_version": 1,
        "version": version,
        "commit": "a" * 40,
        "prerelease": "-" in version,
        "compatibility": {
            "firmware_validation": "unverified: real firmware must pass M5 acceptance",
            "windows_validation": "CI build only; clean offline Win10/11 acceptance required",
        },
        **changes,
    }


def test_rc_remains_publishable_with_explicit_unverified_compatibility():
    gate.validate_release(manifest(), tag="v0.3.0-rc.2", version="0.3.0-rc.2", commit="a" * 40)


@pytest.mark.parametrize(
    "claimed_validation", [None, "unverified", "CI build only", "passed", True, {"status": "passed"}]
)
def test_stable_release_cannot_be_unlocked_by_manifest_claims(claimed_validation):
    package = manifest(
        "0.3.0",
        compatibility={"firmware_validation": claimed_validation, "windows_validation": claimed_validation},
    )
    with pytest.raises(ValueError, match="M5"):
        gate.validate_release(package, tag="v0.3.0", version="0.3.0", commit="a" * 40)


@pytest.mark.parametrize("version", ["0.3.0-beta.1", "0.3.0-dev", "0.3.0-rc.0", "0.3.0-rc.01", "0.3.0-rc.2-extra"])
def test_other_hyphenated_versions_do_not_bypass_candidate_policy(version):
    with pytest.raises(ValueError, match="rc.N"):
        gate.validate_release(manifest(version), tag=f"v{version}", version=version, commit="a" * 40)


@pytest.mark.parametrize(
    "changes, message",
    [({"version": "0.3.0-rc.1"}, "version"), ({"commit": "b" * 40}, "commit"), ({"prerelease": "true"}, "prerelease")],
)
def test_publish_checks_the_downloaded_artifact_identity(changes, message):
    with pytest.raises(ValueError, match=message):
        gate.validate_release(manifest(**changes), tag="v0.3.0-rc.2", version="0.3.0-rc.2", commit="a" * 40)


def test_tag_must_exactly_match_checked_source():
    with pytest.raises(ValueError, match="tag"):
        gate.validate_release(manifest(), tag="v0.3.0-rc.3", version="0.3.0-rc.2", commit="a" * 40)


def test_invalid_manifest_stops_cli_with_nonzero_exit(tmp_path):
    invalid = tmp_path / "manifest.json"
    invalid.write_text("not json", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release/publish_gate.py"),
            "--tag",
            "v0.3.0-rc.2",
            "--manifest",
            str(invalid),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Release blocked" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_accepts_only_matching_current_rc_artifact(tmp_path):
    version = gate.source_version()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    package = tmp_path / "manifest.json"
    package.write_text(json.dumps(manifest(version, commit=commit)), encoding="utf-8")
    command = [
        sys.executable,
        str(ROOT / "scripts/release/publish_gate.py"),
        "--tag",
        f"v{version}",
        "--manifest",
        str(package),
    ]
    accepted = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    package.write_text(json.dumps(manifest(version, commit="b" * 40)), encoding="utf-8")
    rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "manifest commit" in rejected.stderr
