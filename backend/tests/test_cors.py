"""CORS 暴露面配置测试。"""

from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware

from app import main
from app.core.config import Settings


def _cors_options(monkeypatch, settings: Settings) -> dict | None:
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    app = main.create_app()
    middleware = [item for item in app.user_middleware if item.cls is CORSMiddleware]
    if not middleware:
        return None
    assert len(middleware) == 1
    return middleware[0].kwargs


def test_production_defaults_to_same_origin_without_cors(monkeypatch) -> None:
    settings = Settings(hostcomm_mock=False, smd_cors_origins="")

    assert _cors_options(monkeypatch, settings) is None


def test_production_uses_explicit_origins_without_credentials(monkeypatch) -> None:
    settings = Settings(
        hostcomm_mock=False,
        smd_cors_origins="https://hmi.example, http://10.0.0.5:8000",
    )

    options = _cors_options(monkeypatch, settings)

    assert options is not None
    assert options["allow_origins"] == ["https://hmi.example", "http://10.0.0.5:8000"]
    assert options["allow_credentials"] is False
    assert options["allow_methods"] == ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    assert options["allow_headers"] == ["Authorization", "Content-Type"]


def test_production_rejects_wildcard_origin(monkeypatch) -> None:
    settings = Settings(hostcomm_mock=False, smd_cors_origins="*")

    with pytest.raises(RuntimeError, match="通配符"):
        _cors_options(monkeypatch, settings)


def test_mock_mode_allows_wildcard_without_credentials(monkeypatch) -> None:
    settings = Settings(hostcomm_mock=True, smd_cors_origins="")

    options = _cors_options(monkeypatch, settings)

    assert options is not None
    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False
