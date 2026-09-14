"""Peak free-space checks across program, temporary, data and actual DB volumes."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MIN_DB_FREE_BYTES = 1024**3
OTHER_VOLUME_RESERVE = 256 * 1024**2


class SpaceError(RuntimeError):
    """The install cannot preserve both the live data and a complete recovery path."""


def existing_parent(path: Path) -> Path:
    path = path.resolve()
    while not path.exists():
        parent = path.parent
        if path == parent:
            raise SpaceError("无法确定安装目标所在磁盘")
        path = parent
    return path if path.is_dir() else path.parent


def tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink() or path.is_junction():
        raise SpaceError("配置或备份目录包含重解析路径，无法安全计算所需空间")
    if path.is_file():
        return path.stat().st_size
    result = 0
    for child in path.iterdir():
        result += tree_bytes(child)
    return result


def database_bytes(path: Path) -> int:
    return sum(tree_bytes(Path(str(path) + suffix)) for suffix in ("", "-wal"))


@dataclass
class _Volume:
    path: Path
    free: int
    reserve: int = OTHER_VOLUME_RESERVE
    phases: dict[str, int] = field(default_factory=dict)


def check_upgrade_space(
    install_dir: Path,
    data_dir: Path,
    db_path: Path,
    *,
    payload_bytes: int = 0,
    bootstrap_bytes: int = 0,
    bootstrap_dir: Path | None = None,
    min_db_free_bytes: int = MIN_DB_FREE_BYTES,
    preserve_current: bool = False,
) -> list[dict]:
    """Check additional bytes from *now*, without counting already extracted files twice.

    Before extraction pass full payload/bootstrap sizes; afterward leave both zero.
    The payload is promoted on its own volume, so no second program copy is budgeted.
    An existing backup is already charged to free space. During legacy recovery,
    preserve_current budgets an additional current DB/config snapshot before restore.
    SQLite migration reserves twice DB+WAL size; rollback reserves a complete DB and
    a staged config copy. These are conservative estimates, never a reduced readiness
    threshold. Runtime ENOSPC still enters the durable recovery transaction.
    """
    for value in (payload_bytes, bootstrap_bytes, min_db_free_bytes):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("空间预算必须为非负整数")
    volumes: dict[int, _Volume] = {}

    def add(path: Path, amount: int, phases: tuple[str, ...], *, reserve: int = OTHER_VOLUME_RESERVE) -> None:
        existing = existing_parent(path)
        identity = existing.stat().st_dev
        volume = volumes.setdefault(identity, _Volume(existing, shutil.disk_usage(existing).free))
        volume.reserve = max(volume.reserve, reserve)
        for phase in phases:
            volume.phases[phase] = volume.phases.get(phase, 0) + amount

    all_phases = ("prepare", "migrate", "restore")
    add(install_dir, payload_bytes, all_phases)
    add(bootstrap_dir or Path(tempfile.gettempdir()), bootstrap_bytes, all_phases)
    db_bytes = database_bytes(db_path)
    config_bytes = tree_bytes(data_dir / "config")
    add(data_dir, db_bytes + config_bytes, all_phases)
    add(db_path, 2 * db_bytes, ("migrate",), reserve=min_db_free_bytes)
    add(db_path, db_bytes, ("restore",), reserve=min_db_free_bytes)
    add(data_dir, config_bytes, ("restore",))
    if preserve_current:
        add(data_dir, db_bytes + config_bytes, all_phases)
    result = []
    for volume in volumes.values():
        additional = max(volume.phases.values(), default=0)
        required = additional + volume.reserve
        result.append(
            {
                "volume_path": str(volume.path),
                "free_bytes": volume.free,
                "additional_bytes": additional,
                "reserve_bytes": volume.reserve,
                "required_free_bytes": required,
                "deficit_bytes": max(0, required - volume.free),
            }
        )
    failures = [entry for entry in result if entry["deficit_bytes"]]
    if failures:
        detail = "; ".join(
            f"{entry['volume_path']} 需可用 {entry['required_free_bytes']} 字节，当前 {entry['free_bytes']} 字节，"
            f"还差 {entry['deficit_bytes']} 字节"
            for entry in failures
        )
        raise SpaceError("磁盘空间不足，未降低数据库安全余量；" + detail)
    return result
