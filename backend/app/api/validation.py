"""REST 边界复用的 Pydantic/FastAPI 约束类型。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Path, Query
from pydantic import StringConstraints, TypeAdapter

Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_]+$"),
]
LoginPassword = Annotated[str, StringConstraints(min_length=1, max_length=128)]
NewPassword = Annotated[str, StringConstraints(min_length=8, max_length=128)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
Role = Literal["observer", "operator", "admin", "maintainer"]
LogType = Literal["sample", "event", "alarm", "parameter"]

TestId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
]
TestIdPath = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
]
TaskIdPath = Annotated[str, Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")]

Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]
MaxPoints = Annotated[int, Query(ge=1, le=10_000)]
EventLimit = Annotated[int, Query(ge=1, le=5_000)]

_test_id_adapter = TypeAdapter(TestId)


def validate_test_id(value: object) -> str:
    """供嵌套命令参数等非声明式边界复用 test_id 契约。"""
    return _test_id_adapter.validate_python(value)
