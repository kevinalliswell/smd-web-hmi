"""Verify RC identity or software-stable acceptance against this run's actual assets.

Software qualification does not assert Windows desktop or device qualification.
The protected CI producer is the trust boundary; arbitrary manifest prose is not evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release.metadata import source_version
from scripts.release.package_bench import validate_evidence as validate_bench_evidence

CORE_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
RC_VERSION = re.compile(CORE_VERSION + r"-rc\.[1-9][0-9]*")
STABLE_VERSION = re.compile(CORE_VERSION)
SHA256 = re.compile(r"[0-9a-f]{64}")
WINDOWS_CHECKS = frozenset(
    {
        "fresh_install",
        "upgrade_rc4",
        "upgrade_rc5",
        "same_version_repair",
        "downgrade_rejected",
        "offline_confirmation",
        "busy_rejected",
        "rollback_recovery",
        "custom_database_preserved",
    }
)
REQUIRED_LIMITATIONS = frozenset(
    {
        "win10_win11_desktop_unverified",
        "firmware_unverified",
        "physical_interlocks_unverified",
        "gb_conformance_unverified",
    }
)


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _file(directory: Path, reference: object, *, name: str | None = None) -> Path:
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError("acceptance file reference requires path and sha256")
    filename = reference["path"]
    digest = reference["sha256"]
    if (
        not isinstance(filename, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename)
        or (name is not None and filename != name)
    ):
        raise ValueError("acceptance path must be the expected artifact-directory filename")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ValueError("acceptance digest must be SHA-256")
    path = directory / filename
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("acceptance path cannot be linked")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("acceptance file is missing or empty")
    if _digest(path) != digest:
        raise ValueError("acceptance digest differs from actual artifact bytes")
    return path


def _json(path: Path) -> dict:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("acceptance JSON exceeds the bounded metadata size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("acceptance JSON must be an object")
    return value


def _identity(evidence: dict, *, version: str, commit: str, ci_run_id: str) -> None:
    if (
        type(evidence.get("schema_version")) is not int
        or evidence["schema_version"] != 1
        or evidence.get("version") != version
        or evidence.get("commit") != commit
        or evidence.get("ci_run_id") != ci_run_id
    ):
        raise ValueError("acceptance schema/version/commit/CI run must match this release")


def _bench_inventory(archive_path: Path, manifest_path: Path, manifest: dict) -> None:
    expected = manifest.get("files")
    if not isinstance(expected, dict) or "SmdBench.exe" not in expected:
        raise ValueError("bench inventory must include SmdBench.exe")
    if any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in expected.values()):
        raise ValueError("bench inventory contains invalid digests")
    actual = {}
    embedded_manifest = None
    roots = set()
    seen = set()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            mode = stat.S_IFMT(info.external_attr >> 16)
            if (
                "\\" in name
                or ":" in name
                or name.startswith("/")
                or any(part in {".", ".."} for part in name.split("/") if part)
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                or name in seen
            ):
                raise ValueError("bench inventory contains unsafe or duplicate ZIP entries")
            seen.add(name)
            if info.is_dir():
                continue
            if len(parts) < 2:
                raise ValueError("bench inventory requires one bundle root")
            roots.add(parts[0])
            relative = "/".join(parts[1:])
            if relative == "tool-manifest.json":
                if info.file_size > 4 * 1024 * 1024 or embedded_manifest is not None:
                    raise ValueError("bench inventory manifest is ambiguous or oversized")
                embedded_manifest = archive.read(info)
            else:
                with archive.open(info) as source:
                    actual[relative] = hashlib.file_digest(source, "sha256").hexdigest()
    if len(roots) != 1 or embedded_manifest != manifest_path.read_bytes() or actual != expected:
        raise ValueError("bench ZIP inventory differs from the tested tool manifest")


def _windows_evidence(evidence: dict, *, directory: Path, installer_sha256: str) -> None:
    if (
        evidence.get("status") != "passed"
        or evidence.get("cleanup_complete") is not True
        or evidence.get("runner_os") != "Windows"
        or evidence.get("execution") != "actual-installed-service"
        or evidence.get("installer_sha256") != installer_sha256
    ):
        raise ValueError("Windows acceptance must execute this actual installed service")
    assertions = evidence.get("assertions")
    if not isinstance(assertions, list):
        raise ValueError("Windows acceptance requires scenario assertions")
    names = set()
    logs = set()
    for assertion in assertions:
        if (
            not isinstance(assertion, dict)
            or not isinstance(assertion.get("name"), str)
            or assertion["name"] in names
            or assertion.get("status") != "passed"
        ):
            raise ValueError("Windows acceptance contains duplicate, missing, failed or skipped scenarios")
        names.add(assertion["name"])
        log = _file(directory, assertion.get("evidence"))
        validate_scenario_log(_json(log), evidence, name=assertion["name"])
        if log in logs:
            raise ValueError("Windows acceptance requires an individual scenario log")
        logs.add(log)
    if names != WINDOWS_CHECKS:
        raise ValueError("Windows acceptance must include the exact required scenario set")


def validate_scenario_log(log: dict, identity: dict, *, name: str) -> None:
    names = {log[key] for key in ("name", "scenario") if isinstance(log.get(key), str)}
    if (
        type(log.get("schema_version")) is not int
        or log["schema_version"] != 1
        or any(log.get(key) != identity[key] for key in ("version", "commit", "ci_run_id", "installer_sha256"))
        or log.get("execution") != "actual-installed-service"
        or names != {name}
        or not isinstance(log.get("observations"), (dict, list))
        or not log["observations"]
        or (name not in {"busy_rejected", "offline_confirmation"} and log.get("status") != "passed")
    ):
        raise ValueError("Scenario log identity or actual observations differ from this release assertion")


def _software_acceptance(
    acceptance: dict,
    *,
    directory: Path,
    version: str,
    commit: str,
    ci_run_id: str | None,
) -> None:
    if not isinstance(ci_run_id, str) or not re.fullmatch(r"[1-9][0-9]*", ci_run_id):
        raise ValueError("software acceptance requires this CI run ID")
    _identity(acceptance, version=version, commit=commit, ci_run_id=ci_run_id)
    limitations = acceptance.get("limitations")
    if (
        acceptance.get("release_scope") != "software-stable"
        or not isinstance(limitations, list)
        or not all(isinstance(item, str) for item in limitations)
        or set(limitations) != REQUIRED_LIMITATIONS
        or len(limitations) != len(REQUIRED_LIMITATIONS)
    ):
        raise ValueError("software acceptance must retain the exact unverified qualification boundaries")
    expected_names = {
        "installer": f"SmdHmi-{version}-windows-x64.exe",
        "bench": f"SmdBench-{version}-windows-x64.zip",
        "bench_manifest": "bench-manifest.json",
        "bench_acceptance": "bench-acceptance.json",
        "windows_acceptance": "windows-acceptance.json",
    }
    references = acceptance.get("artifacts")
    if not isinstance(references, dict) or set(references) != set(expected_names):
        raise ValueError("software acceptance must bind every required artifact")
    paths = {key: _file(directory, references[key], name=name) for key, name in expected_names.items()}
    installer_sha256 = references["installer"]["sha256"]
    windows = _json(paths["windows_acceptance"])
    _identity(windows, version=version, commit=commit, ci_run_id=ci_run_id)
    _windows_evidence(windows, directory=directory, installer_sha256=installer_sha256)
    tool = _json(paths["bench_manifest"])
    if tool.get("version") != version or tool.get("commit") != commit:
        raise ValueError("bench manifest must match this release version and commit")
    validate_bench_evidence(
        _json(paths["bench_acceptance"]),
        tool,
        installer_sha256=installer_sha256,
        tool_manifest_sha256=references["bench_manifest"]["sha256"],
    )
    _bench_inventory(paths["bench"], paths["bench_manifest"], tool)


def validate_release(
    manifest: dict,
    *,
    tag: str,
    version: str,
    commit: str,
    artifacts_dir: Path | None = None,
    software_acceptance: dict | None = None,
    ci_run_id: str | None = None,
) -> None:
    """Check downloaded bytes against the checked commit; do not infer device sign-off."""
    if tag != f"v{version}":
        raise ValueError("tag does not match the checked source version")
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError("manifest schema_version must be 1")
    if manifest.get("version") != version:
        raise ValueError("manifest version differs from the checked source/tag")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or manifest.get("commit") != commit:
        raise ValueError("manifest commit differs from the checked tag commit")
    if STABLE_VERSION.fullmatch(version):
        if manifest.get("prerelease") is not False:
            raise ValueError("software stable manifest prerelease must be the JSON boolean false")
        if artifacts_dir is None or not isinstance(software_acceptance, dict):
            raise ValueError("software stable requires machine-verifiable acceptance and actual artifacts")
        _software_acceptance(
            software_acceptance,
            directory=artifacts_dir,
            version=version,
            commit=commit,
            ci_run_id=ci_run_id,
        )
        return
    if not RC_VERSION.fullmatch(version):
        raise ValueError("发布策略只接受 vX.Y.Z 或 vX.Y.Z-rc.N，N 必须为无前导零的正整数")
    if manifest.get("prerelease") is not True:
        raise ValueError("RC manifest prerelease must be the JSON boolean true")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--software-acceptance", type=Path)
    args = parser.parse_args()
    try:
        manifest = _json(args.manifest)
        acceptance = _json(args.software_acceptance) if args.software_acceptance else None
        version = source_version()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        validate_release(
            manifest,
            tag=args.tag,
            version=version,
            commit=commit,
            artifacts_dir=args.artifacts_dir,
            software_acceptance=acceptance,
            ci_run_id=os.environ.get("GITHUB_RUN_ID"),
        )
    except (
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Release blocked: {exc}", file=sys.stderr)
        return 1
    scope = "Candidate" if RC_VERSION.fullmatch(version) else "Software stable"
    print(f"{scope} release permitted: {args.tag} ({commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
