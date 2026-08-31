"""应用时间边界：统一解析并输出秒精度 UTC ISO 8601。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """返回固定为 UTC、带显式偏移的当前时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_utc_iso(value: str | datetime) -> str:
    """将 ISO 8601 时间归一为秒精度 UTC；旧版无偏移值按 UTC 解释。"""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith(("Z", "z")):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError("时间戳必须是 ISO 8601 格式") from exc
    else:
        raise ValueError("时间戳必须是字符串或 datetime")

    # 兼容升级前未携带偏移的历史数据；从本版本起所有新值均显式为 UTC。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
