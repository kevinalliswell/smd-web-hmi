"""冒烟入口即使拿到路径参数，也不能在普通主机或自托管runner执行安装。"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/smoke-windows.ps1"


def test_install_smoke_script_is_part_of_release_tools():
    assert SCRIPT.is_file()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell runtime required for refusal-path test")
@pytest.mark.parametrize("actions,runner", [("false", "github-hosted"), ("true", "self-hosted")])
def test_install_smoke_refuses_unowned_hosts_before_resolving_package(tmp_path, actions, runner):
    sentinel = tmp_path / "untouched.txt"
    sentinel.write_text("unchanged")
    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-Installer",
            str(tmp_path / "does-not-exist.exe"),
            "-Manifest",
            str(tmp_path / "does-not-exist.json"),
            "-Evidence",
            str(tmp_path / "evidence.json"),
        ],
        env={**os.environ, "GITHUB_ACTIONS": actions, "RUNNER_ENVIRONMENT": runner},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "disposable GitHub-hosted Windows runner" in result.stderr
    assert sentinel.read_text() == "unchanged"
    assert not (tmp_path / "evidence.json").exists()
