"""API 请求/响应 Pydantic 模型与统一响应封装。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.api.validation import LoginPassword, Username, validate_test_id
from app.hostcomm.protocol import now_iso


def ok(data: Any) -> dict[str, Any]:
    """成功响应封装（规格 3）。"""
    return {"data": data, "ts": now_iso()}


def err(error_code: str, message: str) -> dict[str, Any]:
    """失败响应封装（规格 3）。"""
    return {"error_code": error_code, "message": message, "ts": now_iso()}


# ---- auth ----
class LoginRequest(BaseModel):
    username: Username
    password: LoginPassword


class LoginData(BaseModel):
    token: str
    role: str
    display_name: str | None = None
    must_change_password: bool = False
    expires_at: datetime


# ---- commands ----
class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    params: dict[str, Any] = Field(default_factory=dict, max_length=100)
    confirm_token: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_start_test_id(self):
        if self.command == "start_test":
            if "test_id" in self.params:
                self.params["test_id"] = validate_test_id(self.params["test_id"])
            height = self.params.get("original_height_mm")
            if height is None:
                raise ValueError("启动试验必须提供原始料层高度 original_height_mm")
            try:
                parsed_height = float(height)
            except (TypeError, ValueError) as exc:
                raise ValueError("original_height_mm 必须为数值") from exc
            if not 0 < parsed_height <= 10_000:
                raise ValueError("original_height_mm 必须在 0 到 10000 mm 之间")
            self.params["original_height_mm"] = parsed_height
            for key, limit in (("sample_label", 128), ("notes", 1000)):
                value = self.params.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    raise ValueError(f"{key} 必须为不超过 {limit} 字符的文本")
        return self


class ConfirmIntentRequest(BaseModel):
    command: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
