"""使用统一 Settings 启动生产 ASGI 服务。"""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings
from app.core.network import tls_options


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.smd_host,
        port=settings.smd_port,
        access_log=settings.hostcomm_mock,
        workers=1,
        proxy_headers=False,
        **tls_options(settings.smd_host, cert=settings.smd_tls_certfile, key=settings.smd_tls_keyfile),
    )


if __name__ == "__main__":
    main()
