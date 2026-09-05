"""试验编号的服务层安全契约。"""

from __future__ import annotations

import re

TEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidTestIdError(ValueError):
    """试验编号不满足跨平台文件名与协议白名单。"""


def validate_test_id(value: object) -> str:
    """返回清理后的安全试验编号，否则拒绝。

    此校验位于服务层，不能被绕过 Pydantic/HTTP 路由的内部调用规避。
    """
    if not isinstance(value, str):
        raise InvalidTestIdError("试验编号必须是字符串")
    candidate = value.strip()
    if TEST_ID_PATTERN.fullmatch(candidate) is None:
        raise InvalidTestIdError("试验编号仅允许 1-64 位字母、数字、下划线或连字符")
    return candidate
