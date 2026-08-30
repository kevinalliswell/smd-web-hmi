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
    smd_ws_max_connections_per_user: int = Field(default=3, ge=1, le=20)

    # ---- HostComm ----
    hostcomm_host: str = "192.168.1.100"
    hostcomm_port: int = 34211
    hostcomm_heartbeat_interval: float = 2.0
    hostcomm_timeout_count: int = 3
    hostcomm_command_timeout: float = 3.0
    hostcomm_mock: bool = False

    # ---- 前端静态托管（生产同源部署）----
    # 留空时自动探测仓库内 frontend/dist；显式配置用于离线包部署（见 deploy/windows/）
    smd_frontend_dist: str = ""

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

    @property
    def data_dir(self) -> Path:
        """数据目录（数据库所在目录），报告/导出文件落在其子目录下。"""
        d = self.db_path_resolved.parent
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def reports_dir(self) -> Path:
        d = self.data_dir / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def exports_dir(self) -> Path:
        d = self.data_dir / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def frontend_dist_dir(self) -> Path | None:
        """前端构建产物目录；未配置且默认位置无产物时返回 None（开发模式走 Vite dev server）。"""
        if self.smd_frontend_dist:
            raw = Path(self.smd_frontend_dist)
            candidate = raw if raw.is_absolute() else (BACKEND_DIR / raw)
        else:
            candidate = BACKEND_DIR.parent / "frontend" / "dist"
        return candidate if (candidate / "index.html").is_file() else None


@lru_cache(maxsize=1)
def _ephemeral_secret() -> str:
    """开发环境兜底密钥：进程生命周期内稳定，重启后失效（强制重新登录）。"""
    return secrets.token_hex(32)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局单例配置。"""
    return Settings()
