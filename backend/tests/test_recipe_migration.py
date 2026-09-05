"""从配方之前的schema追加历史表，既有实验不被覆盖。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_recipe_migration_preserves_experiment(tmp_path):
    database = tmp_path / "recipe-upgrade.db"
    env = {**os.environ, "SMD_DB_PATH": str(database)}
    backend = Path(__file__).resolve().parents[1]
    for revision in ("control001", "recipe001"):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if revision == "control001":
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO test_session(test_id,operator_id,start_time) VALUES(?,?,?)",
                    ("old", "operator", "2026-09-05T00:00:00Z"),
                )
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT test_id,phase FROM test_session").fetchall() == [("old", "needs_review")]
        assert db.execute("SELECT count(*) FROM recipe_version").fetchone() == (0,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
