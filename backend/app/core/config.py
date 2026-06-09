"""应用配置：Pydantic Settings，读取 backend/.env。"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根 / backend 目录定位（用于解析相对 DB 路径与 .env 位置）
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置项。环境变量名见 .env.example。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 后端服务 ----
    smd_host: str = "0.0.0.0"
    smd_port: int = 8000
    smd_db_path: str = "./data/smd.db"
    smd_jwt_secret: str = ""
    smd_jwt_expire_minutes: int = 480
    smd_jwt_algorithm: str = "HS256"

    # ---- HostComm ----
    hostcomm_host: str = "192.168.1.100"
    hostcomm_port: int = 34211
    hostcomm_heartbeat_interval: float = 2.0
    hostcomm_timeout_count: int = 3
    hostcomm_command_timeout: float = 3.0
    hostcomm_mock: bool = False

    # ---- 元信息 ----
    client_id: str = "hmi-01"
    client_name: str = "smd-web-backend"
    protocol_version: str = "1.0"

    @property
    def jwt_secret(self) -> str:
        """返回 JWT 密钥；若未配置则进程内生成临时密钥（仅开发，重启即失效）。"""
        if self.smd_jwt_secret:
            return self.smd_jwt_secret
        return _ephemeral_secret()

    @property
    def db_url(self) -> str:
        """SQLAlchemy async SQLite 连接串（绝对路径）。"""
        raw = Path(self.smd_db_path)
        path = raw if raw.is_absolute() else (BACKEND_DIR / raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def db_path_resolved(self) -> Path:
        raw = Path(self.smd_db_path)
        return raw if raw.is_absolute() else (BACKEND_DIR / raw)


@lru_cache(maxsize=1)
def _ephemeral_secret() -> str:
    """开发环境兜底密钥：进程生命周期内稳定，重启后失效（强制重新登录）。"""
    return secrets.token_hex(32)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局单例配置。"""
    return Settings()
