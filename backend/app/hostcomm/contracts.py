"""HostComm 1.0契约校验；可选扩展只有显式声明后启用。"""

from typing import Any

from app.hostcomm.protocol import PROTOCOL_VERSION

REQUIRED_CAPABILITIES = {"status_snapshot", "command"}
COMMAND_RESULTS = {"accepted", "rejected", "busy", "timeout", "invalid_param", "permission_denied", "unsupported"}


def validate_hello(frame: dict[str, Any]) -> None:
    payload = frame.get("payload")
    if frame.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("不支持的 HostComm 协议版本")
    if not isinstance(payload, dict) or payload.get("result") != "accepted":
        raise ValueError("控制板拒绝 hello 协商")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ValueError("hello_ack 缺少合法能力列表")
    if not REQUIRED_CAPABILITIES.issubset(capabilities):
        raise ValueError("控制板缺少必要的状态/受限命令能力")


def validate_result(payload: dict[str, Any], command: str) -> None:
    if payload.get("command") != command or payload.get("result") not in COMMAND_RESULTS:
        raise ValueError("command_result 命令或结果与请求契约不符")
