"""应用配置：Pydantic Settings，读取 backend/.env。"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根 / backend 目录定位（用于解析相对 DB 路径与 .env 位置）
BACKEND_DIR = Path(__file__).resolve().parents[2]


class WarningLogger(Protocol):
    """启动校验所需的最小日志接口。"""

    def warning(self, event: str, **kwargs) -> None: ...


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

    # ---- 前端静态托管（生产同源部署）----
    # 留空时自动探测仓库内 frontend/dist；显式配置用于离线包部署（见 deploy/windows/）
    smd_frontend_dist: str = ""

    # ---- 元信息 ----
    client_id: str = "hmi-01"
    client_name: str = "smd-web-backend"
    protocol_version: str = "1.0"

    @property
    def jwt_secret(self) -> str:
        """返回已校验的 JWT 密钥；临时密钥仅允许 HostComm Mock 开发模式。"""
        self._validate_jwt_secret()
        if self.smd_jwt_secret:
            return self.smd_jwt_secret
        return _ephemeral_secret()

    def validate_startup(self, logger: WarningLogger) -> None:
        """启动前校验安全配置，并显式告警开发临时密钥。"""
        self._validate_jwt_secret()
        if not self.smd_jwt_secret:
            logger.warning(
                "security.ephemeral_jwt_secret",
                note="仅允许 HOSTCOMM_MOCK=true 的开发环境；重启后现有令牌失效",
            )

    def _validate_jwt_secret(self) -> None:
        """强制生产密钥存在且 UTF-8 编码后至少 32 字节。"""
        if self.smd_jwt_secret:
            if len(self.smd_jwt_secret.encode("utf-8")) < 32:
                raise RuntimeError("SMD_JWT_SECRET 必须至少包含 32 字节")
            return
        if not self.hostcomm_mock:
            raise RuntimeError("生产模式必须配置 SMD_JWT_SECRET（至少 32 字节），拒绝启动")

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
