"""Bind executed Windows and bench suites to the exact release assets, then gate them."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release import publish_gate as gate

MAINTENANCE_CHECKS = frozenset({"busy_rejected", "offline_confirmation"})
OVERWRITE_CHECKS = gate.WINDOWS_CHECKS - MAINTENANCE_CHECKS


def digest(path: Path) -> str:
    return gate._digest(path)


def _reference(path: Path) -> dict:
    return {"path": path.name, "sha256": digest(path)}


def _identity(document: dict, *, version: str, commit: str, ci_run_id: str, installer_sha256: str) -> None:
    gate._identity(document, version=version, commit=commit, ci_run_id=ci_run_id)
    if document.get("installer_sha256") != installer_sha256:
        raise ValueError("Executed evidence refers to different installer bytes")


def _write_once(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Existing evidence output cannot be a link")
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError("Refusing to replace different existing acceptance bytes")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".acceptance-", delete=False) as target:
            temporary = Path(target.name)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _assertions(document: dict) -> dict[str, dict]:
    claims = document.get("assertions")
    if not isinstance(claims, list):
        raise ValueError("Executed suite has no assertions")
    result = {}
    for claim in claims:
        if (
            not isinstance(claim, dict)
            or not isinstance(claim.get("name"), str)
            or claim["name"] in result
            or claim.get("status") != "passed"
        ):
            raise ValueError("Executed suite contains duplicate, missing, failed or skipped assertions")
        result[claim["name"]] = claim
    return result


def _verify_log(directory: Path, claim: dict, identity: dict, *, bench: bool) -> Path:
    path = gate._file(directory, claim.get("evidence"))
    log = gate._json(path)
    gate.validate_scenario_log(log, identity, name=claim["name"])
    if log.get("name" if bench else "scenario") != claim["name"]:
        raise ValueError("Scenario log identity does not match its source suite")
    return path


def _append_sums(artifacts: Path, paths: list[Path]) -> None:
    sums = artifacts / "SHA256SUMS.txt"
    if not sums.is_file() or sums.is_symlink():
        raise ValueError("Existing release checksum inventory is required")
    text = sums.read_text(encoding="ascii")
    entries = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if not match or match[2] in entries:
            raise ValueError("Existing release checksum inventory is malformed or duplicated")
        entries[match[2]] = match[1]
    additions = []
    for path in paths:
        value = digest(path)
        if path.name in entries:
            if entries[path.name] != value:
                raise ValueError("Existing checksum refers to different acceptance bytes")
        else:
            entries[path.name] = value
            additions.append(value + "  " + path.name + "\n")
    if additions:
        with sums.open("a", encoding="ascii") as output:
            if text and not text.endswith("\n"):
                output.write("\n")
            output.writelines(additions)
            output.flush()
            os.fsync(output.fileno())


def assemble(
    artifacts: Path, overwrite_evidence: Path, bench_evidence: Path, *, version: str, commit: str, ci_run_id: str
) -> dict:
    if not isinstance(ci_run_id, str) or not re.fullmatch(r"[1-9][0-9]*", ci_run_id):
        raise ValueError("Current CI run ID is required")
    for directory in (artifacts, overwrite_evidence, bench_evidence):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or (hasattr(directory, "is_junction") and directory.is_junction())
        ):
            raise ValueError("Acceptance inputs must be real artifact directories")
    manifest = gate._json(artifacts / "manifest.json")
    if manifest.get("version") != version or manifest.get("commit") != commit:
        raise ValueError("Packaged manifest differs from checked source identity")
    installer = artifacts / f"SmdHmi-{version}-windows-x64.exe"
    identity = {"version": version, "commit": commit, "ci_run_id": ci_run_id, "installer_sha256": digest(installer)}
    partial = gate._json(overwrite_evidence / "windows-overwrite-partial.json")
    _identity(partial, **identity)
    if (
        partial.get("suite") != "overwrite-installer"
        or partial.get("status") != "passed"
        or partial.get("cleanup_complete") is not True
        or partial.get("runner_os") != "Windows"
        or partial.get("execution") != "actual-installed-service"
    ):
        raise ValueError("Overwrite suite did not pass and clean up on actual Windows")
    overwrite_claims = _assertions(partial)
    if set(overwrite_claims) != OVERWRITE_CHECKS:
        raise ValueError("Overwrite suite omitted or invented installation scenarios")
    packaged_bench = artifacts / "bench-acceptance.json"
    if packaged_bench.read_bytes() != (bench_evidence / "acceptance.json").read_bytes():
        raise ValueError("Bench acceptance differs between executed and packaged copies")
    bench = gate._json(packaged_bench)
    _identity(bench, **identity)
    tool_path = artifacts / "bench-manifest.json"
    gate.validate_bench_evidence(
        bench,
        gate._json(tool_path),
        installer_sha256=identity["installer_sha256"],
        tool_manifest_sha256=digest(tool_path),
    )
    bench_claims = _assertions(bench)
    if not MAINTENANCE_CHECKS <= bench_claims.keys():
        raise ValueError("Bench did not execute both required maintenance scenarios")
    logs = {}
    claims = {}
    for name, claim in overwrite_claims.items():
        logs[name] = _verify_log(overwrite_evidence, claim, identity, bench=False)
        claims[name] = claim
    for name in sorted(MAINTENANCE_CHECKS):
        claim = bench_claims[name]
        logs[name] = _verify_log(bench_evidence, claim, identity, bench=True)
        claims[name] = claim
    if len({path.name for path in logs.values()}) != len(gate.WINDOWS_CHECKS):
        raise ValueError("Each Windows scenario requires a distinct evidence file")
    # Only copy already checked, explicit scenario receipts. No directory upload,
    # reconstructed passed list or private installation/configuration copy.
    published_logs = []
    assertions = []
    for name in sorted(claims):
        source = logs[name]
        destination = artifacts / source.name
        _write_once(destination, source.read_bytes())
        reference = _reference(destination)
        if reference != claims[name]["evidence"]:
            raise ValueError("Scenario bytes changed while aggregating evidence")
        published_logs.append(destination)
        assertions.append({key: claims[name][key] for key in ("name", "status", "evidence")})
    windows = {
        "schema_version": 1,
        **identity,
        "status": "passed",
        "cleanup_complete": True,
        "runner_os": "Windows",
        "execution": "actual-installed-service",
        "assertions": assertions,
    }
    windows_path = artifacts / "windows-acceptance.json"
    _write_once(windows_path, _json_bytes(windows))
    references = {
        "installer": _reference(installer),
        "bench": _reference(artifacts / f"SmdBench-{version}-windows-x64.zip"),
        "bench_manifest": _reference(tool_path),
        "bench_acceptance": _reference(packaged_bench),
        "windows_acceptance": _reference(windows_path),
    }
    acceptance = {
        "schema_version": 1,
        "release_scope": "software-stable",
        "version": version,
        "commit": commit,
        "ci_run_id": ci_run_id,
        "artifacts": references,
        "limitations": sorted(gate.REQUIRED_LIMITATIONS),
    }
    gate.validate_release(
        manifest,
        tag=f"v{version}",
        version=version,
        commit=commit,
        artifacts_dir=artifacts,
        software_acceptance=acceptance,
        ci_run_id=ci_run_id,
    )
    software_path = artifacts / "software-acceptance.json"
    _write_once(software_path, _json_bytes(acceptance))
    _append_sums(artifacts, [windows_path, software_path, *published_logs])
    return acceptance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--overwrite-evidence", type=Path, required=True)
    parser.add_argument("--bench-evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        assemble(
            args.artifacts,
            args.overwrite_evidence,
            args.bench_evidence,
            version=gate.source_version(),
            commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            ci_run_id=os.environ.get("GITHUB_RUN_ID"),
        )
    except (ValueError, OSError, subprocess.CalledProcessError, gate.zipfile.BadZipFile) as error:
        print("Acceptance aggregation blocked: " + str(error), file=sys.stderr)
        return 1
    print("Actual installer, bench and Windows evidence verified for this software release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
