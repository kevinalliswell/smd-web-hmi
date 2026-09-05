"""Fail CI when dependency declarations and committed locks drift."""

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def locked(path: Path):
    result = {}
    for line in path.read_text().splitlines():
        if match := re.match(r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", line):
            name, version = canonicalize_name(match[1]), match[2]
            result.setdefault(name, set()).add(version)
    return result


def check(declaration: Path, lock: Path):
    pins = locked(lock)
    for line in declaration.read_text().splitlines():
        line = line.partition("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = Requirement(line)
        versions = pins.get(canonicalize_name(requirement.name), set())
        if not versions or any(version not in requirement.specifier for version in versions):
            raise ValueError(f"{declaration}: {requirement} differs from {lock}: {versions}")
    return pins


def main():
    runtime = check(ROOT / "backend/requirements.txt", ROOT / "backend/requirements.lock")
    development = check(ROOT / "backend/requirements-dev.txt", ROOT / "backend/requirements-dev.lock")
    desktop = check(ROOT / "desktop/requirements.in", ROOT / "desktop/requirements.lock")
    for name, versions in runtime.items():
        if development.get(name) != versions or (name in desktop and desktop[name] != versions):
            raise ValueError(f"{name}: runtime/development/desktop locks disagree")
    print("Dependency declarations and locks agree")


if __name__ == "__main__":
    main()
