"""WebSocket 端点 /ws/realtime（规格第 4 节）。

认证：连接须携带 ``?token=<JWT>``。无效令牌以 4001 关闭（T14）。
推送：由 HostComm 回调经 ws_manager.broadcast 下发（status_update 等）。
"""

from __future__ import annotations

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
WS_CLOSE_TOO_MANY_CONNECTIONS = 4008


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
    if user is None or not user.is_active or user.role != role:
        return None
    return ConnectionContext(username=username, role=role)


@router.websocket("/ws/realtime")
async def realtime(ws: WebSocket, token: str | None = None) -> None:
    """实时推送端点。前端通过 ?token= 传 JWT 认证。"""
    if not token:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    context = await authenticate_websocket(token)
    if context is None:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    try:
        await ws_manager.connect(
            ws,
            context,
            max_connections_per_user=get_settings().smd_ws_max_connections_per_user,
        )
    except ConnectionLimitExceeded:
        await ws.close(code=WS_CLOSE_TOO_MANY_CONNECTIONS)
        return
    try:
        while True:
            msg = await ws.receive_json()
            # 前端可发 ping / subscribe（规格 4.2）
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ws.error", error=str(exc))
        await ws_manager.disconnect(ws)
