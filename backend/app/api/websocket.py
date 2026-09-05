"""WebSocket 端点 /ws/realtime（规格第 4 节）。

认证：连接建立后的第一条消息须为 ``authenticate``，避免 JWT 进入 URL 和访问日志。
推送：由 HostComm 回调经 ws_manager.broadcast 下发（status_update 等）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.deps import ROLE_LEVEL
from app.api.ws_manager import ConnectionContext, ConnectionLimitExceeded, ws_manager
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.db.database import get_sessionmaker
from app.db.models import UserAccount

logger = get_logger("ws.endpoint")

router = APIRouter()

WS_CLOSE_UNAUTHORIZED = 4001
WS_CLOSE_INVALID_MESSAGE = 4002
WS_CLOSE_FORBIDDEN_ORIGIN = 4003
WS_CLOSE_TOO_MANY_CONNECTIONS = 4008
WS_CLOSE_RATE_LIMITED = 4009
WS_CLOSE_MESSAGE_TOO_LARGE = 1009


async def authenticate_websocket(token: str) -> ConnectionContext | None:
    """校验 token 声明，并与数据库中的当前用户状态和角色对账。"""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    if payload.get("must_change_password"):
        return None
    username = payload.get("sub")
    role = payload.get("role")
    if not isinstance(username, str) or not username or role not in ROLE_LEVEL:
        return None

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = await session.scalar(select(UserAccount).where(UserAccount.username == username))
    if user is None or not user.is_active or user.role != role or user.token_version != payload.get("ver", 0):
        return None
    return ConnectionContext(username=username, role=role)


def _origin_allowed(ws: WebSocket, configured_origins: list[str]) -> bool:
    """阻止浏览器跨站 WebSocket 劫持；非浏览器客户端可省略 Origin。"""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if origin.rstrip("/") in {item.rstrip("/") for item in configured_origins if item != "*"}:
        return True
    return parsed.netloc == ws.headers.get("host")


def _parse_message(raw: str, *, max_bytes: int) -> dict | None:
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("message_too_large")
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


@router.websocket("/ws/realtime")
async def realtime(ws: WebSocket) -> None:
    """实时推送端点：同源校验后以首帧 JWT 完成认证。"""
    settings = get_settings()
    if not _origin_allowed(ws, settings.cors_origins):
        await ws.close(code=WS_CLOSE_FORBIDDEN_ORIGIN)
        return
    await ws.accept()
    token = ""
    registered = False
    try:
        try:
            raw = await asyncio.wait_for(
                ws.receive_text(),
                timeout=settings.smd_ws_auth_timeout_seconds,
            )
        except TimeoutError:
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        try:
            message = _parse_message(raw, max_bytes=settings.smd_ws_max_message_bytes)
        except ValueError:
            await ws.close(code=WS_CLOSE_MESSAGE_TOO_LARGE)
            return
        if message is None or message.get("type") != "authenticate" or not isinstance(message.get("token"), str):
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        token = message["token"]
        context = await authenticate_websocket(token)
        if context is None:
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
            return
        await ws_manager.connect(
            ws,
            context,
            max_connections_per_user=settings.smd_ws_max_connections_per_user,
            accept=False,
        )
        registered = True
        await ws.send_json({"type": "auth_ok"})
    except ConnectionLimitExceeded:
        await ws.close(code=WS_CLOSE_TOO_MANY_CONNECTIONS)
        return

    attempts: deque[float] = deque()
    try:
        while True:
            raw = await asyncio.wait_for(
                ws.receive_text(),
                timeout=settings.smd_ws_idle_timeout_seconds,
            )
            try:
                message = _parse_message(raw, max_bytes=settings.smd_ws_max_message_bytes)
            except ValueError:
                await ws.close(code=WS_CLOSE_MESSAGE_TOO_LARGE)
                return
            if message is None:
                await ws.close(code=WS_CLOSE_INVALID_MESSAGE)
                return

            now = time.monotonic()
            cutoff = now - 60
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= settings.smd_ws_rate_limit_per_minute:
                await ws.close(code=WS_CLOSE_RATE_LIMITED)
                return
            attempts.append(now)

            if message.get("type") == "ping":
                if await authenticate_websocket(token) is None:
                    await ws.close(code=WS_CLOSE_UNAUTHORIZED)
                    return
                await ws.send_json({"type": "pong"})
            else:
                await ws.send_json({"type": "error", "error_code": "unsupported_message"})
    except TimeoutError:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws.error", error=str(exc))
    finally:
        if registered:
            await ws_manager.disconnect(ws)
