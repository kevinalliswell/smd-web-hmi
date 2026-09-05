"""冻结程序路径与受限配置加载；配置必须先于任何 app 模块导入。"""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values


def data_root() -> Path:
    return Path(
        os.environ.get("SMD_DATA_ROOT", str(Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "SmdHmi"))
    ).resolve()


def version_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    raise RuntimeError("桌面入口只支持打包后的版本目录；开发使用独立 web 开发命令")


def load_environment(data: Path, version: Path) -> None:
    config = data / "config/service.env"
    if not config.is_file():
        raise RuntimeError("缺少受限的 ProgramData/config/service.env")
    values = dotenv_values(config, interpolate=False)
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value
    if not Path(values.get("SMD_DB_PATH") or "").is_absolute():
        raise RuntimeError("服务 SMD_DB_PATH 必须是绝对路径")
    os.environ["SMD_FRONTEND_DIST"] = str(version / "frontend")
    os.environ["SMD_MAINTENANCE_FILE"] = str(data / "maintenance.json")


def require_fixed_runtime(version: Path) -> Path:
    runtime = version / "webview2"
    if not (runtime / "msedgewebview2.exe").is_file() or not (runtime / "icudtl.dat").is_file():
        raise RuntimeError("缺少随版本交付的 Fixed WebView2；请修复安装")
    return runtime


def tls_options(host: str) -> dict:
    cert, key = os.environ.get("SMD_TLS_CERTFILE"), os.environ.get("SMD_TLS_KEYFILE")
    if not cert and not key and host in {"127.0.0.1", "::1", "localhost"}:
        return {}
    if not cert or not key or not Path(cert).is_file() or not Path(key).is_file():
        raise RuntimeError("LAN 监听必须配置完整有效的 SMD_TLS_CERTFILE/SMD_TLS_KEYFILE；本机可使用 HTTP")
    return {"ssl_certfile": cert, "ssl_keyfile": key}
