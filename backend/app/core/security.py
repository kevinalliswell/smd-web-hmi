"""安全工具：密码哈希（PBKDF2-HMAC-SHA256）与 JWT 签发/校验。

不依赖 bcrypt 原生库，使用标准库 hashlib，便于离线工控机部署。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

_PBKDF2_ROUNDS = 200_000
_PBKDF2_ALGO = "sha256"


# ---------------------------------------------------------------- 密码哈希


def hash_password(password: str) -> str:
    """返回 ``pbkdf2_sha256$rounds$salt_hex$hash_hex`` 格式的密码哈希。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """常数时间校验明文密码与存储哈希。"""
    try:
        scheme, rounds_s, salt_hex, hash_hex = hashed.split("$")
        if not scheme.startswith("pbkdf2_"):
            return False
        algo = scheme.removeprefix("pbkdf2_")
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------- JWT


def create_access_token(username: str, role: str) -> tuple[str, datetime]:
    """签发 JWT，返回 (token, 过期时间)。"""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.smd_jwt_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expire}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.smd_jwt_algorithm)
    return token, expire


def decode_access_token(token: str) -> dict:
    """校验并解码 JWT，失败抛出 ``jwt.PyJWTError`` 子类。"""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.smd_jwt_algorithm])
