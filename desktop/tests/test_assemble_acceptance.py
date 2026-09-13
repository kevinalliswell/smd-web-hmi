"""Release aggregation consumes executed evidence; it cannot manufacture missing passes."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE_SPEC = importlib.util.spec_from_file_location("publish_gate_fixture", ROOT / "desktop/tests/test_publish_gate.py")
fixtures = importlib.util.module_from_spec(GATE_SPEC)
GATE_SPEC.loader.exec_module(fixtures)
# Reuse complete byte fixtures; the collector scenarios below remain independent.
stable_assets = fixtures.stable_assets
SPEC = importlib.util.spec_from_file_location("assemble_acceptance", ROOT / "scripts/release/assemble_acceptance.py")
assembler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembler)


@pytest.fixture
def suites(stable_assets):
    directory, accepted, windows, write = stable_assets
    overwrite = directory / "overwrite-evidence"
    bench = directory / "bench-evidence"
    overwrite.mkdir()
    bench.mkdir()
    windows.update(status="passed", cleanup_complete=True)
    claims = windows.pop("assertions")
    for claim in claims:
        name = claim["name"]
        kind = "bench" if name in {"busy_rejected", "offline_confirmation"} else "overwrite"
        log = {
            "schema_version": 1,
            "version": "0.3.0",
            "commit": "a" * 40,
            "ci_run_id": "12345",
            "installer_sha256": accepted["artifacts"]["installer"]["sha256"],
            "execution": "actual-installed-service",
            "status": "passed",
            "observations": {"actual_observation": True},
        }
        log["name" if kind == "bench" else "scenario"] = name
        content = json.dumps(log).encode()
        folder = bench if kind == "bench" else overwrite
        (folder / claim["evidence"]["path"]).write_bytes(content)
        (directory / claim["evidence"]["path"]).unlink()
        claim["evidence"]["sha256"] = assembler.digest(folder / claim["evidence"]["path"])
    partial = {
        **windows,
        "suite": "overwrite-installer",
        "assertions": [item for item in claims if item["name"] not in {"busy_rejected", "offline_confirmation"}],
    }
    (overwrite / "windows-overwrite-partial.json").write_text(json.dumps(partial))
    original_bench = json.loads((directory / "bench-acceptance.json").read_text())
    original_bench["ci_run_id"] = "12345"
    original_bench["assertions"].extend(
        item for item in claims if item["name"] in {"busy_rejected", "offline_confirmation"}
    )
    write("bench-acceptance.json", original_bench)
    (bench / "acceptance.json").write_bytes((directory / "bench-acceptance.json").read_bytes())
    (directory / "manifest.json").write_text(json.dumps(fixtures.manifest("0.3.0")))
    (directory / "windows-acceptance.json").unlink()
    (directory / "SHA256SUMS.txt").write_text(
        accepted["artifacts"]["installer"]["sha256"] + "  " + accepted["artifacts"]["installer"]["path"] + "\n"
    )
    return directory, overwrite, bench


def collect(suites):
    return assembler.assemble(*suites, version="0.3.0", commit="a" * 40, ci_run_id="12345")


def test_aggregation_validates_exact_bytes_and_does_not_duplicate_checksums(suites):
    collect(suites)
    directory = suites[0]
    actual = json.loads((directory / "windows-acceptance.json").read_text())
    assert actual["status"] == "passed" and actual["cleanup_complete"] is True
    assert len(actual["assertions"]) == 9
    before = (directory / "SHA256SUMS.txt").read_bytes()
    collect(suites)
    assert (directory / "SHA256SUMS.txt").read_bytes() == before
    assert len(before.decode().splitlines()) == 12


@pytest.mark.parametrize(
    "field,value", [("cleanup_complete", False), ("status", "failed"), ("ci_run_id", "999"), ("assertions", [])]
)
def test_missing_or_failed_execution_is_not_filled_from_required_names(suites, field, value):
    path = suites[1] / "windows-overwrite-partial.json"
    partial = json.loads(path.read_text())
    partial[field] = value
    path.write_text(json.dumps(partial))
    with pytest.raises(ValueError):
        collect(suites)
    assert not (suites[0] / "software-acceptance.json").exists()


def test_packaged_bench_acceptance_cannot_differ_from_executed_copy(suites):
    (suites[2] / "acceptance.json").write_text('{"status":"passed"}')
    with pytest.raises(ValueError, match="Bench acceptance"):
        collect(suites)


def test_changed_or_foreign_scenario_log_blocks_aggregation(suites):
    first = next(suites[1].glob("*.log"))
    first.write_text('{"private-key":"must-never-be-published"}')
    with pytest.raises(ValueError):
        collect(suites)
    assert not (suites[0] / first.name).exists()


def test_source_log_identity_is_checked_even_when_its_hash_matches(suites):
    partial_path = suites[1] / "windows-overwrite-partial.json"
    partial = json.loads(partial_path.read_text())
    claim = partial["assertions"][0]
    log = suites[1] / claim["evidence"]["path"]
    body = json.loads(log.read_text())
    body["commit"] = "c" * 40
    log.write_text(json.dumps(body))
    claim["evidence"]["sha256"] = assembler.digest(log)
    partial_path.write_text(json.dumps(partial))
    with pytest.raises(ValueError, match="log identity"):
        collect(suites)
    assert not (suites[0] / log.name).exists()
