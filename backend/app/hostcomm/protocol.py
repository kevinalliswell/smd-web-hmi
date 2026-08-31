"""HostComm 帧解析与编码（JSON Lines，\\n 分隔）。

容错要求（安全红线 4 / CLAUDE 5）：
- 非法 JSON 只记录日志，不抛异常、不 crash，继续处理后续帧。
- 单帧超过 MAX_FRAME_BYTES 丢弃并告警（防止超长帧）。
- 不得假设一次 TCP recv 等于一帧（处理粘包/拆包）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.logging import get_logger
from app.core.time import utc_now_iso

logger = get_logger("hostcomm.protocol")

MAX_FRAME_BYTES = 8192
PROTOCOL_VERSION = "1.0"


class FrameParser:
    """维护字节缓冲区，按 ``\\n`` 切分为完整 JSON 帧。"""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self._buffer = bytearray()
        self._max = max_frame_bytes
        # 统计计数（供诊断页展示）
        self.frames_parsed = 0
        self.frames_dropped = 0
        self.json_errors = 0

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        """输入原始字节，返回本次可解析出的完整帧（dict）列表。

        非法 JSON 与超长帧被跳过并计数，不会中断后续帧解析。
        """
        self._buffer.extend(data)
        frames: list[dict[str, Any]] = []

        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)

            if len(line) > self._max:
                self.frames_dropped += 1
                logger.warning("hostcomm.frame_too_long", size=len(line), limit=self._max)
                continue

            stripped = line.strip()
            if not stripped:
                continue

            try:
                obj = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.json_errors += 1
                logger.warning(
                    "hostcomm.bad_json",
                    error=str(exc),
                    raw=stripped[:200].decode("utf-8", errors="replace"),
                )
                continue

            if not isinstance(obj, dict):
                self.json_errors += 1
                logger.warning("hostcomm.frame_not_object", got=type(obj).__name__)
                continue

            self.frames_parsed += 1
            frames.append(obj)

        # 缓冲区累积超过上限且无换行 → 视为超长/异常，清空防止内存膨胀
        if len(self._buffer) > self._max * 2:
            self.frames_dropped += 1
            logger.warning("hostcomm.buffer_overflow", size=len(self._buffer))
            self._buffer = bytearray()

        return frames

    @staticmethod
    def encode(msg: dict[str, Any]) -> bytes:
        """将消息 dict 编码为 UTF-8 JSON + ``\\n``。"""
        return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def now_iso() -> str:
    """返回 UTC ISO 8601 时间串。"""
    return utc_now_iso()


def new_msg_id(prefix: str = "pc") -> str:
    """生成唯一 msg_id，格式 ``<prefix>-<uuid4_short>``。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def make_frame(
    msg_type: str,
    payload: dict[str, Any] | None = None,
    *,
    msg_id: str | None = None,
    prefix: str = "pc",
) -> dict[str, Any]:
    """组装一条符合 HostComm 通用格式（协议第 5.1 节）的报文。"""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "msg_id": msg_id or new_msg_id(prefix),
        "type": msg_type,
        "timestamp": now_iso(),
        "payload": payload or {},
    }
