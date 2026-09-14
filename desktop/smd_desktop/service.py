"""独立 Windows Service；仅 SCM 停止信号才退出 Uvicorn。"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from .log_stream import LogStream
from .runtime import data_root, load_environment, tls_options, version_root
from .single_instance import inherited_migration_guard, single_instance


def migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import app

    backend = Path(app.__file__).resolve().parent.parent
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "app/db/migrations"))
    command.upgrade(config, "head")


def create_server(settings):
    """SCM waits for actual exit; only HTTP/WebSocket request draining is bounded."""
    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=settings.smd_host,
        port=settings.smd_port,
        workers=1,
        log_config=None,
        proxy_headers=False,
        timeout_graceful_shutdown=15,
        **tls_options(settings.smd_host),
    )
    return uvicorn.Server(config)


def main() -> None:
    data, version = data_root(), version_root()
    load_environment(data, version)
    if "--migrate" in sys.argv:
        if "--migration-lock-handle" in sys.argv:
            index = sys.argv.index("--migration-lock-handle")
            guard = inherited_migration_guard(int(sys.argv[index + 1]))
        else:
            guard = single_instance("SmdHmi.Backend", data / "backend.lock")
        with guard:
            migrate()
        return
    import servicemanager
    import win32service
    import win32serviceutil

    if "--self-check" in sys.argv:
        import uvicorn

        # 加载冻结HTTP/WebSocket实现、应用及其数据依赖；不进入lifespan或连接设备。
        uvicorn.Config("app.main:app", log_config=None, proxy_headers=False).load()
        return

    class SmdService(win32serviceutil.ServiceFramework):
        _svc_name_ = "SmdHmi"
        _svc_display_name_ = "SMD Experiment Backend"
        _svc_description_ = "Single STM32 gateway and experiment records service"

        def __init__(self, args):
            super().__init__(args)
            self.server = None
            self.stop_requested = False

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stop_requested = True
            if self.server is not None:
                self.server.should_exit = True

        def SvcDoRun(self):
            from app.core.config import get_settings

            with single_instance("SmdHmi.Backend", data / "backend.lock"):
                settings = get_settings()
                logdir = data / "logs"
                logdir.mkdir(exist_ok=True)
                handler = RotatingFileHandler(
                    logdir / "service.log", maxBytes=10_000_000, backupCount=10, encoding="utf-8"
                )
                logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
                sys.stdout = sys.stderr = LogStream(logging.getLogger("backend.stdout"))
                self.server = create_server(settings)
                self.server.should_exit = self.stop_requested
                self.server.run()

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(SmdService)
    servicemanager.StartServiceCtrlDispatcher()
