"""A real published-schema upgrade preserves history and does not synthesize dates."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_recovery_migration_preserves_old_rows_and_allows_unknown_start(tmp_path):
    path = tmp_path / "旧数据.db"
    env = {**os.environ, "SMD_DB_PATH": str(path)}

    def upgrade(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    upgrade("hostcommv2001")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO test_session(test_id,operator_id,start_time,notes) VALUES(?,?,?,?)",
            ("old", "operator", "2026-09-01T00:00:00Z", "原样保留"),
        )
        db.execute(
            "INSERT INTO v2_run_binding VALUES(?,?,?,?,?,?)", ("a" * 32, "b" * 32, "old", "c" * 64, "d" * 64, "now")
        )
    upgrade("head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT start_time,discovered_at,notes FROM test_session").fetchone() == (
            "2026-09-01T00:00:00Z",
            None,
            "原样保留",
        )
        assert db.execute("SELECT recipe_digest,profile_digest FROM v2_run_binding").fetchone() == ("c" * 64, "d" * 64)
        db.execute(
            "INSERT INTO test_session(test_id,operator_id,start_time,discovered_at) VALUES(?,?,NULL,?)",
            ("recovered", "device-recovery", "2026-09-08T00:00:00Z"),
        )
        db.execute(
            "INSERT INTO v2_run_binding VALUES(?,?,?,?,?,?)", ("a" * 32, "e" * 32, "recovered", None, None, "now")
        )
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM v2_run_recovery").fetchone() == (0,)
