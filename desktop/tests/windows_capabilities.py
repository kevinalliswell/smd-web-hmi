"""探测当前进程的 Windows 能力,供能力受限环境下的用例跳过判定使用。

非管理员进程(如自托管 CI 的 NetworkService 服务)无法把文件所有者设为
Administrators 组,也可能运行在没有 .git 的 REST 归档检出里。这里的跳过
只描述"该环境无法执行",不改变验收口径:托管 Windows runner(管理员、
完整 git 检出)照常执行全部用例。
"""

import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def can_assign_administrators_owner() -> bool:
    """当前进程能否把新建文件的所有者设为 Administrators 组(配对私钥写入的前置能力)。"""
    if os.name != "nt":
        return True
    from smd_desktop.pairing import _private_fd

    with tempfile.TemporaryDirectory() as scratch:
        try:
            os.close(_private_fd(Path(scratch) / "ownership-probe"))
        except OSError:
            return False
    return True


@lru_cache(maxsize=1)
def git_head_available() -> bool:
    """仓库检出是否带 .git 且 git 可解析 HEAD(REST 归档回退检出没有)。"""
    try:
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


requires_admin_owner = pytest.mark.skipif(
    not can_assign_administrators_owner(),
    reason="需要能把文件所有者设为 Administrators 的特权;非管理员环境(如 NetworkService 自托管服务)无法执行,托管 Windows runner 照常运行",
)

requires_git_head = pytest.mark.skipif(
    not git_head_available(),
    reason="需要带 .git 的检出与可用 git;REST 归档回退检出无法执行,托管 runner 照常运行",
)
