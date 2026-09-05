"""原始设备/数据库 JSON 边界：异常形状保留为质量证据，不用 truthy 值假定对象。"""

import json
from typing import Any


def object_value(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def json_object(value: Any) -> tuple[dict, bool]:
    if value is None or value == "":
        return {}, True
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}, False
    return (decoded, True) if isinstance(decoded, dict) else ({}, False)


def capabilities(snapshot: dict) -> set[str]:
    values = object_value(snapshot.get("_hostcomm")).get("capabilities")
    return set(values) if isinstance(values, list) and all(isinstance(item, str) for item in values) else set()
