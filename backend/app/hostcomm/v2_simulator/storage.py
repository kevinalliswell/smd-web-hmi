"""SQLite persistence for the inert simulator, separate from the HMI database."""

import json
import sqlite3
from pathlib import Path

from app.hostcomm.v2_contract.codec import canonical_bytes


class StorageUnavailable(RuntimeError):
    pass


class DeviceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        existing = {
            row[0]
            for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        if existing and existing != {"state", "records", "gaps"}:
            self.db.close()
            raise ValueError("simulator requires a dedicated database; refusing unrelated application tables")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (seq INTEGER PRIMARY KEY, data BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS gaps (seq INTEGER PRIMARY KEY, reason TEXT NOT NULL)")
        self.db.commit()
        self.fail_writes = False

    def load(self) -> dict | None:
        row = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, state: dict, records: list[dict] = ()) -> None:
        if self.fail_writes:
            raise StorageUnavailable("injected durable storage failure")
        try:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO state VALUES (1, ?)",
                    (json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")),),
                )
                for record in records:
                    self.db.execute(
                        "INSERT INTO records VALUES (?, ?)",
                        (int(record["record_seq"]), canonical_bytes(record) + b"\n"),
                    )
        except sqlite3.Error as exc:
            raise StorageUnavailable("simulator durable write failed") from exc

    def catalog(self) -> tuple[int | None, int | None]:
        return self.db.execute("SELECT MIN(seq), MAX(seq) FROM records").fetchone()

    def cut(self, first: int, last: int) -> tuple[list[tuple[int, bytes]], dict[int, str]]:
        rows = self.db.execute("SELECT seq,data FROM records WHERE seq BETWEEN ? AND ? ORDER BY seq", (first, last))
        gaps = self.db.execute("SELECT seq,reason FROM gaps WHERE seq BETWEEN ? AND ?", (first, last))
        return list(rows), dict(gaps)

    def remove_records(self, sequences, reason: str = "storage_fault") -> None:
        if reason not in {"storage_fault", "not_recorded", "retention_expired"}:
            raise ValueError("unknown missing-record reason")
        with self.db:
            for seq in sequences:
                self.db.execute("DELETE FROM records WHERE seq=?", (int(seq),))
                self.db.execute("INSERT OR REPLACE INTO gaps VALUES (?,?)", (int(seq), reason))

    def close(self) -> None:
        self.db.close()
