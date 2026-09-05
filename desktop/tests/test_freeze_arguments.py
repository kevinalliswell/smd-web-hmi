"""冻结参数必须能从位于输出目录的spec解析到真实源码/资源。"""

import importlib.util
import json
import subprocess
import sys
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


def test_actual_spec_collectors_discover_every_backend_module(tmp_path, monkeypatch):
    """Run real makespec/collectors with the build subprocess's paths, before Analysis.

    Requires the release environment's pinned PyInstaller; the ordinary backend
    environment need not install a platform-specific packaging toolchain.
    """
    pytest.importorskip("PyInstaller")
    run = subprocess.run
    builds = []
    monkeypatch.setattr(freeze.subprocess, "run", lambda args, **kwargs: builds.append((args, kwargs)))
    monkeypatch.setattr(
        sys, "argv", ["freeze.py", "--output", str(tmp_path / "payload"), "--work", str(tmp_path / "build")]
    )
    monkeypatch.chdir(tmp_path)
    freeze.main()

    # Discover the expected ordinary Python packages from sources independently
    # of PyInstaller. Alembic's scripts are data, not importable app modules.
    expected = set()
    for source in (ROOT / "backend/app").rglob("*.py"):
        if "migrations" in source.parts:
            continue
        parts = source.relative_to(ROOT / "backend").with_suffix("").parts
        expected.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))

    probe = """
import ast
import json
import sys
from pathlib import Path
from PyInstaller.__main__ import generate_parser, run_makespec

options = vars(generate_parser().parse_args(sys.argv[1:]))
spec = Path(run_makespec(options.pop('filenames'), **options))
tree = ast.parse(spec.read_text(encoding='utf-8'), filename=str(spec))
preamble = []
for statement in tree.body:
    if (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name) and statement.value.func.id == 'Analysis'):
        break
    preamble.append(statement)
else:
    raise AssertionError('generated spec has no Analysis boundary')
namespace = {}
exec(compile(ast.Module(body=preamble, type_ignores=[]), str(spec), 'exec'), namespace)
print(json.dumps(sorted(name for name in namespace['hiddenimports'] if name == 'app' or name.startswith('app.'))))
"""
    assert len(builds) == 3
    for args, kwargs in builds:
        result = run(
            [args[0], "-c", probe, *args[3:]],
            env=kwargs.get("env"),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        collected = set(json.loads(result.stdout))
        assert expected <= collected, f"{args}: missing app modules {sorted(expected - collected)}"
