"""Append-only migration on empty/previous SQLite, with a verified pre-migration backup."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def migrate(path, revision):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "alembic", "upgrade", revision],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "SMD_DB_PATH": str(path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_and_previous_schema_preserve_history_and_backup(tmp_path):
    empty = tmp_path / "empty.db"
    migrate(empty, "head")
    with sqlite3.connect(empty) as db:
        assert db.execute("SELECT count(*) FROM background_job").fetchone() == (0,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    old, backup = tmp_path / "old.db", tmp_path / "backup.db"
    migrate(old, "hostcommv2002")
    with sqlite3.connect(old) as source:
        source.execute(
            "INSERT INTO sample_point(test_id,ts,source,burden_temp_v,delta_p_v,displacement_v) VALUES('preserved','2026-10-23','live_poll',1,1,1)"
        )
        source.commit()
        with sqlite3.connect(backup) as target:
            source.backup(target)
    migrate(old, "head")
    migrate(old, "head")
    with sqlite3.connect(old) as db:
        assert db.execute("SELECT test_id FROM sample_point").fetchall() == [("preserved",)]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("jobs0001",)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("hostcommv2002",)
        assert db.execute("SELECT test_id FROM sample_point").fetchall() == [("preserved",)]
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
