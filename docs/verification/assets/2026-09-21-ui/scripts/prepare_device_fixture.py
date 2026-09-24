"""PR #84 UI 验收前置：在本地 Mock 控制板上保存/校验/下发一份标准候选配方。

start_test 要求绑定已在板端回读一致的配方版本；Mock 进程重启会丢失该绑定，
所以每次重启 Mock 后都要重跑本脚本。只与本机 127.0.0.1:8100 的验收用后端交互，
不接触真实设备。
"""

from __future__ import annotations

import json
import os
import urllib.request

BASE = os.environ.get("SMD_UI_BASE", "http://127.0.0.1:8100")
USER = os.environ.get("SMD_UI_USER", "maint1")
PASSWORD = os.environ.get("SMD_UI_PASSWORD", "Pr84Maint#2026")


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        return {"_http_error": exc.code, "_body": exc.read().decode("utf-8", "replace")}


def main() -> None:
    token = call("POST", "/api/auth/login", body={"username": USER, "password": PASSWORD})["data"]["token"]
    listed = call("GET", "/api/recipes?page=1&size=20", token)["data"]
    if listed["items"]:
        recipe_id = listed["items"][0]["recipe_id"]
        version = listed["items"][0]["version"]
    else:
        definition = call("GET", "/api/recipes/template/standard", token)["data"]["definition"]
        saved = call("POST", "/api/recipes", token, {"definition": definition})["data"]
        recipe_id, version = saved["recipe_id"], saved["version"]
    validated = call("POST", f"/api/recipes/{recipe_id}/validate", token, {"version": version})
    activated = call("POST", f"/api/recipes/{recipe_id}/activate", token, {"version": version})
    print(json.dumps({
        "recipe_id": recipe_id,
        "version": version,
        "executable": validated.get("data", {}).get("executable"),
        "activate_ok": "data" in activated,
        "parameter_crc": activated.get("data", {}).get("parameter_crc"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
