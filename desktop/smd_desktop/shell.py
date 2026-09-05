"""普通用户桌面薄壳；不启动/停止后台，不暴露 Python JS bridge。"""

import ctypes
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .runtime import data_root, require_fixed_runtime, version_root


def main() -> None:
    try:
        import webview

        root = version_root()
        runtime = require_fixed_runtime(root)
        if "--self-check" in sys.argv:
            return
        client = json.loads((data_root() / "client.json").read_text(encoding="utf-8"))
        parsed = urlsplit(client["url"])
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
        ):
            raise RuntimeError("桌面地址必须指向本机后台")
        webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime)
        webview.settings["IGNORE_SSL_ERRORS"] = False
        webview.create_window("SMD 软熔滴落实验", client["url"], width=1440, height=900, min_size=(1100, 700))
        profile = Path(os.environ["LOCALAPPDATA"]) / "SmdHmi/WebView2"
        webview.start(gui="edgechromium", debug=False, private_mode=False, storage_path=str(profile))
    except Exception as exc:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, "无法打开实验界面。后台服务不会因此停止。\n" + str(exc), "SMD", 0x10)
        raise
