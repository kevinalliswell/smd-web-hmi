"""Space planning is checked against independent byte budgets and distinct volumes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from smd_desktop import space
from smd_desktop.bundle import BundleError
from smd_desktop.versions import compare_versions


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("0.3.0-rc.9", "0.3.0-rc.10", -1),
        ("0.3.0", "0.3.0-rc.999", 1),
        ("0.3.0-rc.5", "0.3.0-rc.5", 0),
        ("1.0.0-alpha.1", "1.0.0-alpha.beta", -1),
        ("1.0.0-alpha", "1.0.0-alpha.1", -1),
        ("1.0.0", "0.99.99", 1),
    ],
)
def test_semver_ordering(left, right, expected):
    assert compare_versions(left, right) == expected


@pytest.mark.parametrize("version", ["1.0.0-rc.01", "1.0.0-rc..1"])
def test_invalid_numeric_prerelease_does_not_enter_version_ordering(version):
    with pytest.raises(BundleError):
        compare_versions(version, "1.0.0")


def files(tmp_path):
    install, data, source = tmp_path / "program", tmp_path / "data", tmp_path / "actual.db"
    install.mkdir()
    (data / "config").mkdir(parents=True)
    (data / "config/settings.env").write_bytes(b"c" * 20)
    source.write_bytes(b"d" * 100)
    Path(str(source) + "-wal").write_bytes(b"w" * 50)
    return install, data, source


def test_same_volume_uses_peak_not_sum_of_sequential_scratch(tmp_path, monkeypatch):
    install, data, source = files(tmp_path)
    monkeypatch.setattr(space.shutil, "disk_usage", lambda path: SimpleNamespace(free=10_000))
    monkeypatch.setattr(space, "OTHER_VOLUME_RESERVE", 0)
    # Default argument reserve has its own value; an explicit large minimum dominates it.
    reserve = space.MIN_DB_FREE_BYTES
    monkeypatch.setattr(space.shutil, "disk_usage", lambda path: SimpleNamespace(free=reserve + 7470))
    result = space.check_upgrade_space(
        install, data, source, payload_bytes=1000, bootstrap_bytes=2000, bootstrap_dir=tmp_path
    )
    assert len(result) == 1
    # 1000 payload + 2000 bootstrap + 150 DB backup + 20 config backup + 2*150 migration scratch.
    assert result[0]["additional_bytes"] == 3470
    assert result[0]["reserve_bytes"] == reserve
    assert result[0]["deficit_bytes"] == 0


def test_already_extracted_payload_not_reserved_twice(tmp_path, monkeypatch):
    install, data, source = files(tmp_path)
    free = space.MIN_DB_FREE_BYTES + 470
    monkeypatch.setattr(space.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    result = space.check_upgrade_space(install, data, source, bootstrap_dir=tmp_path)
    assert result[0]["required_free_bytes"] == free
    with pytest.raises(space.SpaceError, match="还差 1 字节"):
        space.check_upgrade_space(install, data, source, payload_bytes=1, bootstrap_dir=tmp_path)


def test_recovery_budgets_additional_current_snapshot(tmp_path, monkeypatch):
    install, data, source = files(tmp_path)
    monkeypatch.setattr(space.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * space.MIN_DB_FREE_BYTES))
    normal = space.check_upgrade_space(install, data, source, bootstrap_dir=tmp_path)
    recovery = space.check_upgrade_space(install, data, source, bootstrap_dir=tmp_path, preserve_current=True)
    assert recovery[0]["additional_bytes"] - normal[0]["additional_bytes"] == 170


def test_separate_volume_failure_reports_the_actual_database_volume(tmp_path, monkeypatch):
    install, data, source = files(tmp_path)

    class Volume:
        def __init__(self, name, identity):
            self.name, self.identity = name, identity

        def stat(self):
            return SimpleNamespace(st_dev=self.identity)

        def __str__(self):
            return self.name

    program, storage, db_disk, temporary = [
        Volume(name, index) for index, name in enumerate(("program", "storage", "db", "temp"))
    ]

    def mounted(path):
        if path == source:
            return db_disk
        if path == data:
            return storage
        if path == install:
            return program
        return temporary

    monkeypatch.setattr(space, "existing_parent", mounted)
    monkeypatch.setattr(
        space.shutil,
        "disk_usage",
        lambda volume: SimpleNamespace(free=space.MIN_DB_FREE_BYTES + (299 if volume is db_disk else 10_000)),
    )
    with pytest.raises(space.SpaceError, match=r"db 需可用.*还差 1 字节"):
        space.check_upgrade_space(install, data, source)


def test_symlink_config_cannot_escape_space_and_backup_inventory(tmp_path):
    install, data, source = files(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (data / "config/linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows runner has no symlink privilege")
    with pytest.raises(space.SpaceError, match="重解析"):
        space.check_upgrade_space(install, data, source)
