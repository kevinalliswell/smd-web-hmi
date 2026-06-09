"""API 请求/响应 Pydantic 模型与统一响应封装。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.hostcomm.protocol import now_iso


def ok(data: Any) -> dict[str, Any]:
    """成功响应封装（规格 3）。"""
    return {"data": data, "ts": now_iso()}


def err(error_code: str, message: str) -> dict[str, Any]:
    """失败响应封装（规格 3）。"""
    return {"error_code": error_code, "message": message, "ts": now_iso()}


# ---- auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginData(BaseModel):
    token: str
    role: str
    display_name: str | None = None
    expires_at: datetime


# ---- commands ----
class CommandRequest(BaseModel):
    command: str
    params: dict[str, Any] = {}
    confirm_token: str | None = None


class ConfirmIntentRequest(BaseModel):
    command: str
