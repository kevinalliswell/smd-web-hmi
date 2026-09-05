"""独立 Windows Service；仅 SCM 停止信号才退出 Uvicorn。"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from .log_stream import LogStream
from .runtime import data_root, load_environment, tls_options, version_root
from .single_instance import single_instance


def migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import app

    backend = Path(app.__file__).resolve().parent.parent
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "app/db/migrations"))
    command.upgrade(config, "head")


def main() -> None:
    data, version = data_root(), version_root()
    load_environment(data, version)
    if "--migrate" in sys.argv:
        with single_instance("SmdHmi.Backend", data / "backend.lock"):
            migrate()
        return
    import servicemanager
    import win32service
    import win32serviceutil

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
            import uvicorn

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
                config = uvicorn.Config(
                    "app.main:app",
                    host=settings.smd_host,
                    port=settings.smd_port,
                    workers=1,
                    log_config=None,
                    proxy_headers=False,
                    **tls_options(settings.smd_host),
                )
                self.server = uvicorn.Server(config)
                self.server.should_exit = self.stop_requested
                self.server.run()

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(SmdService)
    servicemanager.StartServiceCtrlDispatcher()
