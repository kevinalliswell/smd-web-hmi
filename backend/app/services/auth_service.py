"""登录防暴力破解：IP 滑动窗口、恒定密码校验路径与账户锁定。"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, password_hash_needs_upgrade, verify_password
from app.db.models import OperatorAction, UserAccount

# 不存在的用户名也执行同成本 PBKDF2，降低用户名枚举的时序差异。
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$200000$d4f56b1f8e81c86ede117124d7b85066$"
    "7fc091e457d7fbef57c8bf866828a754f9100b3efca5ec58f1931f9c2609ab00"
)


class IpRateLimiter:
    """进程内按客户端 IP 统计登录尝试的滑动窗口限流器。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_ip: str, *, limit: int, window_seconds: int) -> bool:
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            attempts = self._attempts[client_ip]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                return False
            attempts.append(now)
            return True

    def reset(self) -> None:
        """清空计数；仅供测试与进程生命周期管理。"""
        with self._lock:
            self._attempts.clear()


@dataclass
class LoginRejected(Exception):
    """内部登录拒绝原因；对外始终映射为统一凭据错误。"""

    reason: str


class LoginProtector:
    """验证密码并维护账户失败计数、锁定窗口和审计记录。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._clock = clock

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        username: str,
        password: str,
        client_ip: str,
        max_failures: int,
        lock_minutes: int,
    ) -> UserAccount:
        user = await session.scalar(select(UserAccount).where(UserAccount.username == username))
        password_hash = user.hashed_pw if user is not None else _DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, password_hash)
        now = self._clock()

        if user is None or not user.is_active:
            raise LoginRejected("invalid_credentials")

        locked_until = self._parse_timestamp(user.locked_until)
        if locked_until is not None and locked_until <= now:
            user.locked_until = None
            user.failed_login_attempts = 0
            locked_until = None

        if locked_until is not None:
            raise LoginRejected("account_locked")

        if not password_ok:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= max_failures:
                user.locked_until = (now + timedelta(minutes=lock_minutes)).isoformat(timespec="seconds")
                session.add(
                    OperatorAction(
                        ts=now.isoformat(timespec="seconds"),
                        operator_id=user.username,
                        operator_role=user.role,
                        action_type="login_lockout",
                        result="locked",
                        reason_code="too_many_failures",
                        client_ip=client_ip,
                    )
                )
            await session.commit()
            raise LoginRejected("invalid_credentials")

        user.failed_login_attempts = 0
        user.locked_until = None
        if password_hash_needs_upgrade(user.hashed_pw):
            user.hashed_pw = hash_password(password)
        return user

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


login_rate_limiter = IpRateLimiter()
login_protector = LoginProtector()
