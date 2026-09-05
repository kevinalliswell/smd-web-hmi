"""Fail closed before GitHub publication: this development baseline may publish RCs only.

M5 must introduce a reviewed, verifiable acceptance contract before stable releases
are enabled. Free-text compatibility notes are not acceptance evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release.metadata import source_version

RC_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-rc\.[1-9][0-9]*")


def validate_release(manifest: dict, *, tag: str, version: str, commit: str) -> None:
    """Check the downloaded package against the checked commit; do not infer sign-off."""
    if tag != f"v{version}":
        raise ValueError("tag does not match the checked source version")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if manifest.get("version") != version:
        raise ValueError("manifest version differs from the checked source/tag")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or manifest.get("commit") != commit:
        raise ValueError("manifest commit differs from the checked tag commit")
    if "-" not in version:
        raise ValueError(
            "正式发布已锁定：M5 尚未接入可核验的固件、干净断网 Windows 和现场实验验收证据。"
            "manifest 的 firmware_validation/windows_validation 说明文字或 passed/true 声明均不能解锁；"
            "本轮只允许 vX.Y.Z-rc.N 候选发布。"
        )
    if not RC_VERSION.fullmatch(version):
        raise ValueError("当前发布策略只接受 vX.Y.Z-rc.N，N 必须为无前导零的正整数")
    if manifest.get("prerelease") is not True:
        raise ValueError("RC manifest prerelease must be the JSON boolean true")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        version = source_version()  # Reuse backend/frontend/lock/tag checks from the build.
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        validate_release(manifest, tag=args.tag, version=version, commit=commit)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Release blocked: {exc}", file=sys.stderr)
        return 1
    print(f"Candidate release permitted: {args.tag} ({commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
