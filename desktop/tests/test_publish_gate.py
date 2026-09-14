import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
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
    "claimed_validation",
    [None, "unverified", "CI build only", "passed", True, {"status": "passed"}],
)
def test_stable_release_cannot_be_unlocked_by_manifest_claims(claimed_validation):
    package = manifest(
        "0.3.0",
        compatibility={
            "firmware_validation": claimed_validation,
            "windows_validation": claimed_validation,
        },
    )
    with pytest.raises(ValueError, match="acceptance"):
        gate.validate_release(package, tag="v0.3.0", version="0.3.0", commit="a" * 40)


@pytest.mark.parametrize(
    "version",
    ["0.3.0-beta.1", "0.3.0-dev", "0.3.0-rc.0", "0.3.0-rc.01", "0.3.0-rc.2-extra"],
)
def test_other_hyphenated_versions_do_not_bypass_candidate_policy(version):
    with pytest.raises(ValueError, match="rc.N"):
        gate.validate_release(manifest(version), tag=f"v{version}", version=version, commit="a" * 40)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"version": "0.3.0-rc.1"}, "version"),
        ({"commit": "b" * 40}, "commit"),
        ({"prerelease": "true"}, "prerelease"),
    ],
)
def test_publish_checks_the_downloaded_artifact_identity(changes, message):
    with pytest.raises(ValueError, match=message):
        gate.validate_release(
            manifest(**changes),
            tag="v0.3.0-rc.2",
            version="0.3.0-rc.2",
            commit="a" * 40,
        )


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


def test_cli_checks_current_source_and_requires_stable_acceptance(tmp_path):
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
    if "-" in version:
        assert accepted.returncode == 0, accepted.stderr
    else:
        assert accepted.returncode != 0 and "acceptance" in accepted.stderr
    package.write_text(json.dumps(manifest(version, commit="b" * 40)), encoding="utf-8")
    rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "manifest commit" in rejected.stderr


@pytest.fixture
def stable_assets(tmp_path):
    def write(name, value):
        content = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
        (tmp_path / name).write_bytes(content)
        return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}

    installer = write("SmdHmi-0.3.0-windows-x64.exe", b"tested installer bytes")
    tool = {
        "version": "0.3.0",
        "commit": "a" * 40,
        "scenario_version": "1",
        "files": {"SmdBench.exe": hashlib.sha256(b"tested bench").hexdigest()},
    }
    tool_ref = write("bench-manifest.json", tool)
    with zipfile.ZipFile(tmp_path / "SmdBench-0.3.0-windows-x64.zip", "w") as archive:
        archive.writestr("SmdBench/tool-manifest.json", (tmp_path / tool_ref["path"]).read_bytes())
        archive.writestr("SmdBench/SmdBench.exe", b"tested bench")
    bench_ref = {
        "path": "SmdBench-0.3.0-windows-x64.zip",
        "sha256": hashlib.sha256((tmp_path / "SmdBench-0.3.0-windows-x64.zip").read_bytes()).hexdigest(),
    }
    bench = write(
        "bench-acceptance.json",
        {
            "schema_version": 1,
            "version": "0.3.0",
            "commit": "a" * 40,
            "scenario_version": "1",
            "scenario": "all",
            "status": "passed",
            "cleanup_complete": True,
            "installer_sha256": installer["sha256"],
            "tool_manifest_sha256": tool_ref["sha256"],
            "assertions": [
                {"name": name, "status": "passed"}
                for name in (
                    "installed_service",
                    "tls_authentication",
                    "standard_recipe",
                    "custom_recipe",
                    "valid_drip",
                    "no_drip",
                    "abort",
                    "invalid_sample",
                    "unknown_run_recovery",
                    "faults",
                    "permissions",
                    "report_formats",
                )
            ],
        },
    )
    windows = {
        "schema_version": 1,
        "version": "0.3.0",
        "commit": "a" * 40,
        "ci_run_id": "12345",
        "runner_os": "Windows",
        "execution": "actual-installed-service",
        "status": "passed",
        "cleanup_complete": True,
        "installer_sha256": installer["sha256"],
        "assertions": [
            {
                "name": name,
                "status": "passed",
                "evidence": write(
                    f"{name}.log",
                    {
                        "schema_version": 1,
                        "version": "0.3.0",
                        "commit": "a" * 40,
                        "ci_run_id": "12345",
                        "execution": "actual-installed-service",
                        "installer_sha256": installer["sha256"],
                        "scenario": name,
                        "status": "passed",
                        "observations": {"actual_observation": True},
                    },
                ),
            }
            for name in (
                "fresh_install",
                "upgrade_rc4",
                "upgrade_rc5",
                "same_version_repair",
                "downgrade_rejected",
                "offline_confirmation",
                "busy_rejected",
                "rollback_recovery",
                "custom_database_preserved",
            )
        ],
    }
    acceptance = {
        "schema_version": 1,
        "release_scope": "software-stable",
        "version": "0.3.0",
        "commit": "a" * 40,
        "ci_run_id": "12345",
        "limitations": [
            "win10_win11_desktop_unverified",
            "firmware_unverified",
            "physical_interlocks_unverified",
            "gb_conformance_unverified",
        ],
        "artifacts": {
            "installer": installer,
            "bench": bench_ref,
            "bench_manifest": tool_ref,
            "bench_acceptance": bench,
            "windows_acceptance": write("windows-acceptance.json", windows),
        },
    }
    return tmp_path, acceptance, windows, write


def validate_stable(assets):
    directory, acceptance, _, _ = assets
    gate.validate_release(
        manifest("0.3.0"),
        tag="v0.3.0",
        version="0.3.0",
        commit="a" * 40,
        artifacts_dir=directory,
        software_acceptance=acceptance,
        ci_run_id="12345",
    )


def test_stable_software_release_requires_real_matching_artifacts_and_suites(
    stable_assets,
):
    validate_stable(stable_assets)


@pytest.mark.parametrize("field", ["version", "commit", "ci_run_id", "release_scope", "limitations"])
def test_stable_rejects_wrong_identity_or_missing_unverified_boundaries(stable_assets, field):
    stable_assets[1][field] = "forged"
    with pytest.raises(ValueError):
        validate_stable(stable_assets)


@pytest.mark.parametrize(
    "field",
    ["version", "commit", "ci_run_id", "runner_os", "execution", "installer_sha256", "status", "cleanup_complete"],
)
def test_windows_suite_must_be_this_installed_build(stable_assets, field):
    _, acceptance, windows, write = stable_assets
    windows[field] = "wrong"
    acceptance["artifacts"]["windows_acceptance"] = write("windows-acceptance.json", windows)
    with pytest.raises(ValueError):
        validate_stable(stable_assets)


@pytest.mark.parametrize("change", ["missing", "skipped", "duplicate", "no_evidence", "unknown"])
def test_windows_suite_requires_every_unique_executed_scenario(stable_assets, change):
    _, acceptance, windows, write = stable_assets
    if change == "missing":
        windows["assertions"].pop()
    elif change == "skipped":
        windows["assertions"][0]["status"] = "skipped"
    elif change == "duplicate":
        windows["assertions"].append(windows["assertions"][0])
    elif change == "no_evidence":
        del windows["assertions"][0]["evidence"]
    else:
        windows["assertions"][0]["name"] = "invented"
    acceptance["artifacts"]["windows_acceptance"] = write("windows-acceptance.json", windows)
    with pytest.raises(ValueError):
        validate_stable(stable_assets)


@pytest.mark.parametrize(
    "artifact",
    ["installer", "bench", "bench_manifest", "bench_acceptance", "windows_acceptance"],
)
def test_stable_rejects_changed_bytes_even_at_identical_commit(stable_assets, artifact):
    directory, acceptance, _, _ = stable_assets
    (directory / acceptance["artifacts"][artifact]["path"]).write_bytes(b"changed after acceptance")
    with pytest.raises(ValueError, match="digest"):
        validate_stable(stable_assets)


@pytest.mark.parametrize(
    "path",
    ["../outside.log", "/outside.log", "C:\\private\\outside.log", "nested/log.txt"],
)
def test_evidence_cannot_escape_artifact_directory(stable_assets, path):
    _, acceptance, windows, write = stable_assets
    windows["assertions"][0]["evidence"]["path"] = path
    acceptance["artifacts"]["windows_acceptance"] = write("windows-acceptance.json", windows)
    with pytest.raises(ValueError, match="path"):
        validate_stable(stable_assets)


def test_empty_or_changed_log_cannot_back_a_passed_assertion(stable_assets):
    directory, _, windows, _ = stable_assets
    (directory / windows["assertions"][0]["evidence"]["path"]).write_bytes(b"")
    with pytest.raises(ValueError):
        validate_stable(stable_assets)


def test_valid_zip_hash_does_not_allow_other_bench_bytes(stable_assets):
    directory, acceptance, _, _ = stable_assets
    archive_path = directory / acceptance["artifacts"]["bench"]["path"]
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "SmdBench/tool-manifest.json",
            (directory / "bench-manifest.json").read_bytes(),
        )
        archive.writestr("SmdBench/SmdBench.exe", b"not the tested bench")
    acceptance["artifacts"]["bench"]["sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="bench.*inventory"):
        validate_stable(stable_assets)


def test_a_passed_bench_status_without_required_checks_is_rejected(stable_assets):
    directory, acceptance, _, write = stable_assets
    bench = json.loads((directory / "bench-acceptance.json").read_text(encoding="utf-8"))
    bench["assertions"] = []
    acceptance["artifacts"]["bench_acceptance"] = write("bench-acceptance.json", bench)
    with pytest.raises(ValueError, match="Bench evidence"):
        validate_stable(stable_assets)


def test_stable_prerelease_flag_must_be_false(stable_assets):
    directory, acceptance, _, _ = stable_assets
    with pytest.raises(ValueError, match="prerelease"):
        gate.validate_release(
            manifest("0.3.0", prerelease=True),
            tag="v0.3.0",
            version="0.3.0",
            commit="a" * 40,
            artifacts_dir=directory,
            software_acceptance=acceptance,
            ci_run_id="12345",
        )


@pytest.mark.parametrize(
    "field,value", [("ci_run_id", "999"), ("commit", "c" * 40), ("scenario", "invented"), ("observations", {})]
)
def test_final_gate_rejects_rehashed_but_foreign_scenario_log(stable_assets, field, value):
    directory, acceptance, windows, write = stable_assets
    claim = windows["assertions"][0]
    path = directory / claim["evidence"]["path"]
    log = json.loads(path.read_text(encoding="utf-8"))
    log[field] = value
    claim["evidence"] = write(path.name, log)
    acceptance["artifacts"]["windows_acceptance"] = write("windows-acceptance.json", windows)
    with pytest.raises(ValueError, match="log"):
        validate_stable(stable_assets)
