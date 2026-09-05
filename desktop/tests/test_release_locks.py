import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_locks", ROOT / "scripts/release/check_locks.py")
locks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(locks)


def test_declaration_only_dependency_update_fails(tmp_path):
    declaration = tmp_path / "requirements.txt"
    locked = tmp_path / "requirements.lock"
    declaration.write_text("fastapi==0.120.0\n")
    locked.write_text("fastapi==0.119.0 \\\n    --hash=sha256:abc\n")
    with pytest.raises(ValueError, match="differs"):
        locks.check(declaration, locked)


def test_dependency_versions_must_satisfy_declared_constraints(tmp_path):
    declaration = tmp_path / "requirements.txt"
    locked = tmp_path / "requirements.lock"
    declaration.write_text("package-name[extra]>=1.0,<2.0 # context\n")
    locked.write_text("package_name==1.2.3\n")
    assert locks.check(declaration, locked) == {"package-name": {"1.2.3"}}
