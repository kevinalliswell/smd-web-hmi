"""桌面服务与源码Web部署共用的TLS监听策略。"""

import os
from pathlib import Path


def tls_options(host: str, *, cert: str | None = None, key: str | None = None) -> dict:
    # Explicit Settings values include backend/.env; desktop loads service.env into os.environ.
    cert = os.environ.get("SMD_TLS_CERTFILE") if cert is None else cert
    key = os.environ.get("SMD_TLS_KEYFILE") if key is None else key
    if not cert and not key and host in {"127.0.0.1", "::1", "localhost"}:
        return {}
    if not cert or not key or not Path(cert).is_file() or not Path(key).is_file():
        raise RuntimeError("LAN 监听必须配置完整有效的 SMD_TLS_CERTFILE/SMD_TLS_KEYFILE；本机可使用 HTTP")
    return {"ssl_certfile": cert, "ssl_keyfile": key}
