"""SQLite online backup 的共同实现，后台备份与离线升级共用。"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path


def backup_sqlite(source: Path, target: Path) -> None:
    """包含已提交 WAL 页；校验临时库后原子发布，拒绝覆盖已有备份。"""
    source, target = source.resolve(), target.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("备份数据库完整性检查失败")
        # Windows _commit/fsync 要求可写文件句柄；rb 句柄会报 EBADF。
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
