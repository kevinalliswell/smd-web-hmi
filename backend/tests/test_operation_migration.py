"""实际 SQLite 从上一发布 schema 升级，保留审计并建立持久操作表。"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_operation_migration_preserves_existing_audit(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    path = tmp_path / "upgrade.db"
    env = {**os.environ, "SMD_DB_PATH": str(path)}

    def upgrade(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    upgrade("d74293c580aa")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO operator_action(ts,operator_id,operator_role,action_type,result) VALUES(?,?,?,?,?)",
            ("2026-09-05T00:00:00+00:00", "operator", "operator", "tare_balance", "accepted"),
        )
    upgrade("operation001")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT operator_id,result FROM operator_action").fetchall() == [("operator", "accepted")]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("operation001",)
        columns = {row[1] for row in db.execute("PRAGMA table_info(operation)")}
        assert {"operation_id", "msg_id", "request_hash", "status", "device_result_json", "result_json"} <= columns
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
