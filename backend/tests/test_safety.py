"""安全红线测试 T11（开发规格说明书 9.2 / CLAUDE 第 2 节）。

按 CLAUDE 第 7 节，本文件应在 CI 中最先运行，验证不存在违禁接口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 违禁标识：不得在代码中出现"强制打开 CO / 绕过安全 / 直写 DO / 强制恢复加热"等接口
FORBIDDEN_IDENTIFIERS = [
    "force_co",
    "force_open_co",
    "force_co_valve",
    "bypass_safety",
    "bypass_relay",
    "bypass_interlock",
    "force_heating",
    "force_heat_permit",
    "force_resume_heat",
    "write_do_bitmap",
    "set_do_bit",
    "override_safety",
    "force_scr",
]


def _python_sources() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py")]


# ---------------------------------------------------- T11 无强制 CO / 绕过接口
@pytest.mark.parametrize("identifier", FORBIDDEN_IDENTIFIERS)
def test_t11_no_forbidden_interfaces(identifier):
    """T11：代码中不存在 force_co / bypass_safety 等违禁函数/标识。"""
    hits = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        if identifier in text:
            hits.append(str(path.relative_to(APP_DIR.parent)))
    assert not hits, f"发现违禁标识 '{identifier}' 于: {hits}"


def test_co_commands_require_confirm():
    """CO 相关命令集合存在且 start_test/stop_test 在内（二次确认前提）。"""
    from app.services.command_service import CO_COMMANDS

    assert "start_test" in CO_COMMANDS
    assert "stop_test" in CO_COMMANDS


def test_command_permissions_deny_observer():
    """权限矩阵中 Observer 不在任何可下发命令的允许集合内。"""
    from app.services.command_service import COMMAND_PERMISSIONS

    for command, roles in COMMAND_PERMISSIONS.items():
        assert "observer" not in roles, f"Observer 不应被允许执行 {command}"
