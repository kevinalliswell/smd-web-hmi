"""启动安全配置测试。"""

from __future__ import annotations

import pytest

from app.core.config import Settings, _ephemeral_secret


class _LoggerSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs) -> None:
        self.calls.append((event, kwargs))


def test_production_rejects_missing_jwt_secret() -> None:
    settings = Settings(smd_jwt_secret="", hostcomm_mock=False)

    with pytest.raises(RuntimeError, match="SMD_JWT_SECRET"):
        settings.validate_startup(_LoggerSpy())
    with pytest.raises(RuntimeError, match="SMD_JWT_SECRET"):
        _ = settings.jwt_secret


@pytest.mark.parametrize("secret", ["short", "密" * 10])
def test_rejects_jwt_secret_shorter_than_32_bytes(secret: str) -> None:
    settings = Settings(smd_jwt_secret=secret, hostcomm_mock=True)

    with pytest.raises(RuntimeError, match="32 字节"):
        settings.validate_startup(_LoggerSpy())


def test_mock_mode_warns_and_uses_process_stable_ephemeral_secret() -> None:
    _ephemeral_secret.cache_clear()
    settings = Settings(smd_jwt_secret="", hostcomm_mock=True)
    logger = _LoggerSpy()

    settings.validate_startup(logger)
    first = settings.jwt_secret
    second = settings.jwt_secret

    assert first == second
    assert len(first.encode("utf-8")) >= 32
    assert logger.calls == [
        (
            "security.ephemeral_jwt_secret",
            {"note": "仅允许 HOSTCOMM_MOCK=true 的开发环境；重启后现有令牌失效"},
        )
    ]


def test_bootstrap_password_can_be_read_from_restricted_installer_file(tmp_path) -> None:
    password_file = tmp_path / "initial-admin-password.txt"
    password_file.write_text("generated-one-time-password\n", encoding="utf-8")
    settings = Settings(
        smd_bootstrap_admin_password="",
        smd_bootstrap_admin_password_file=str(password_file),
    )

    assert settings.bootstrap_admin_password == "generated-one-time-password"
