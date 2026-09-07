"""Gate and archive the exact independent bench that passed installed-loop checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

REQUIRED_CHECKS = frozenset(
    {
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
    }
)


def validate_evidence(evidence: dict, manifest: dict, *, installer_sha256: str, tool_manifest_sha256: str) -> None:
    if (
        evidence.get("schema_version") != 1
        or evidence.get("status") != "passed"
        or evidence.get("cleanup_complete") is not True
        or evidence.get("scenario") != "all"
        or evidence.get("scenario_version") != "1"
        or manifest.get("scenario_version") != "1"
        or evidence.get("installer_sha256") != installer_sha256
        or evidence.get("tool_manifest_sha256") != tool_manifest_sha256
        or any(evidence.get(key) != manifest.get(key) for key in ("version", "commit"))
    ):
        raise ValueError("Bench evidence must pass all scenarios and cleanup on this exact build")
    assertions = evidence.get("assertions")
    if not isinstance(assertions, list) or not all(
        isinstance(item, dict) and isinstance(item.get("name"), str) and item.get("status") == "passed"
        for item in assertions
    ):
        raise ValueError("Bench evidence contains missing, failed or skipped assertions")
    names = {item["name"] for item in assertions}
    if not REQUIRED_CHECKS <= names or len(names) != len(assertions):
        raise ValueError("Bench evidence does not contain every required unique scenario")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_inventory(bundle: Path, manifest: dict) -> None:
    expected = manifest["files"]
    if not isinstance(expected, dict) or "SmdBench.exe" not in expected:
        raise ValueError("Missing bench executable inventory")
    actual = {}
    for path in bundle.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Linked files cannot enter the released kit")
        if path.is_file() and path.relative_to(bundle).as_posix() != "tool-manifest.json":
            actual[path.relative_to(bundle).as_posix()] = digest(path)
    if actual != expected:
        raise ValueError("Bench bytes changed after freezing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.bundle / "tool-manifest.json").read_text(encoding="utf-8"))
    evidence_path = args.evidence / "acceptance.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(
        evidence,
        manifest,
        installer_sha256=digest(args.output / f"SmdHmi-{manifest['version']}-windows-x64.exe"),
        tool_manifest_sha256=digest(args.bundle / "tool-manifest.json"),
    )
    verify_inventory(args.bundle, manifest)
    installed = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
    if any(installed.get(key) != manifest.get(key) for key in ("version", "commit")):
        raise ValueError("Installer and bench come from different builds")
    name = f"SmdBench-{manifest['version']}-windows-x64"
    if (args.output / f"{name}.zip").exists():
        raise FileExistsError("Refusing to overwrite a release kit")
    archive = Path(shutil.make_archive(str(args.output / name), "zip", args.bundle.parent, args.bundle.name))
    shutil.copyfile(args.bundle / "tool-manifest.json", args.output / "bench-manifest.json")
    shutil.copyfile(evidence_path, args.output / "bench-acceptance.json")
    # The original installer digest remains in place; bind the kit and evidence too.
    with (args.output / "SHA256SUMS.txt").open("a", encoding="ascii") as sums:
        for path in (archive, args.output / "bench-manifest.json", args.output / "bench-acceptance.json"):
            sums.write(f"{digest(path)}  {path.name}\n")
    print(f"Packaged {name}; all installed-loop checks and owned cleanup passed")


if __name__ == "__main__":
    main()
