"""冻结参数必须能从位于输出目录的spec解析到真实源码/资源。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_freeze", ROOT / "scripts/release/freeze.py")
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


@pytest.mark.parametrize("entry,name", freeze.APPLICATIONS)
def test_generated_spec_assets_resolve_outside_repository_working_directory(tmp_path, monkeypatch, entry, name):
    monkeypatch.chdir(tmp_path)
    args = freeze.arguments(ROOT, tmp_path / "payload", tmp_path / "freeze-work", entry, name)
    spec_dir = Path(args[args.index("--specpath") + 1])
    values = [args[index + 1] for index, item in enumerate(args) if item == "--add-data"]
    assert len(values) == 2
    for value in values:
        source, destination = value.rsplit(":", 1)
        resolved = spec_dir / source
        assert resolved.exists(), f"generated spec cannot find {source}"
        assert resolved.is_relative_to(ROOT)
        assert destination in {".", "app/db/migrations"}
    assert Path(args[-1]).is_file()
    assert ("--windowed" in args) is (entry == "shell")


def test_unknown_freeze_entry_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        freeze.arguments(ROOT, tmp_path / "payload", tmp_path / "work", "unknown", "Unknown")
