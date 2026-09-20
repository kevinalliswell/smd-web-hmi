"""探测当前进程的 Windows 能力,供能力受限环境下的用例跳过判定使用。

非管理员进程(如自托管 CI 的 NetworkService 服务)既无法完成
smd_bench.windows 的所有者/ACL 设定(acl_set 需把所有者设为
Administrators),也没有创建符号链接的 SeCreateSymbolicLink 特权。
这里的跳过只描述"该环境无法执行",不改变验收口径:托管 Windows
runner(管理员)与 POSIX 通道照常执行全部用例。
"""

import os
import tempfile
from functools import lru_cache
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:  # 构建 job 只装运行时锁,无 pytest;探测函数仍需可导入
    pytest = None


@lru_cache(maxsize=1)
def can_assign_administrators_owner() -> bool:
    """当前进程能否完成 smd_bench.windows.secure_directory 的所有者/ACL 设定。"""
    if os.name != "nt":
        return True
    from smd_bench.windows import WindowsOperationError, secure_directory

    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "acl-probe"
        probe.mkdir()
        try:
            secure_directory(probe)
        except WindowsOperationError:
            return False
    return True


@lru_cache(maxsize=1)
def can_create_symlink() -> bool:
    """当前进程是否持有创建符号链接的特权(SeCreateSymbolicLink 或开发者模式)。"""
    if os.name != "nt":
        return True
    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "target"
        target.write_text("probe", encoding="utf-8")
        try:
            os.symlink(target, Path(scratch) / "link")
        except OSError:
            return False
    return True


if pytest is not None:
    requires_admin_owner = pytest.mark.skipif(
        not can_assign_administrators_owner(),
        reason="需要能把文件所有者设为 Administrators 的特权;非管理员环境(如 NetworkService 自托管服务)无法执行,托管 Windows runner 照常运行",
    )

    requires_symlink_privilege = pytest.mark.skipif(
        not can_create_symlink(),
        reason="需要创建符号链接的特权(SeCreateSymbolicLink);非管理员环境无法执行,托管 Windows runner 照常运行",
    )
