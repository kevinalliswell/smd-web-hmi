"""前端静态托管（同源部署）测试：SPA 回退、API 404 语义、路径穿越防护。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings

INDEX_MARK = "SMD-HMI-INDEX"


@pytest.fixture
def static_app(tmp_path, monkeypatch):
    """构造带前端产物目录的应用（临时 dist：index.html + assets/app.js）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(f"<html><body>{INDEX_MARK}</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"icon")
    monkeypatch.setenv("SMD_FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    from app.main import create_app

    yield create_app()
    get_settings.cache_clear()


async def _get(app, path: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


async def test_root_serves_index(static_app):
    """GET / 返回 index.html。"""
    resp = await _get(static_app, "/")
    assert resp.status_code == 200
    assert INDEX_MARK in resp.text


async def test_deep_link_falls_back_to_index(static_app):
    """SPA 深链（如刷新 /overview）回退到 index.html。"""
    resp = await _get(static_app, "/overview")
    assert resp.status_code == 200
    assert INDEX_MARK in resp.text


async def test_static_files_served(static_app):
    """assets 与根级静态文件正常伺服。"""
    resp = await _get(static_app, "/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text
    resp = await _get(static_app, "/favicon.ico")
    assert resp.status_code == 200


async def test_unknown_api_route_stays_404_json(static_app):
    """未知 /api 路径保持 JSON 404，不被 SPA 回退吞掉。"""
    resp = await _get(static_app, "/api/definitely-missing")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "not_found"


async def test_health_not_shadowed(static_app):
    """catch-all 不遮蔽已注册路由（/health 仍返回 JSON）。"""
    resp = await _get(static_app, "/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_path_traversal_never_escapes_dist(static_app, tmp_path):
    """路径穿越请求绝不返回 dist 之外的文件内容。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    for path in ("/..%2fsecret.txt", "/%2e%2e/secret.txt", "/a/../../secret.txt"):
        resp = await _get(static_app, path)
        assert "TOP-SECRET" not in resp.text
