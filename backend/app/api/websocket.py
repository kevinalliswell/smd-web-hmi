"""WebSocket 端点 /ws/realtime（规格第 4 节）。

认证：连接须携带 ``?token=<JWT>``。无效令牌以 4001 关闭（T14）。
推送：由 HostComm 回调经 ws_manager.broadcast 下发（status_update 等）。
"""

from __future__ import annotations

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_manager import ws_manager
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.hostcomm.protocol import now_iso

logger = get_logger("ws.endpoint")

router = APIRouter()

WS_CLOSE_UNAUTHORIZED = 4001


def current_comm_quality(ws: WebSocket) -> str:
    """返回当前 HostComm 链路质量；客户端未就绪时按最保守值 offline 处理。"""
    client = getattr(ws.app.state, "hostcomm_client", None)
    if client is None:
        return "offline"
    return getattr(client, "comm_quality", "offline")


async def send_comm_status_snapshot(ws: WebSocket) -> None:
    """向刚接入的客户端补推一帧 comm_status。

    comm_status 只在链路质量**变迁**时广播，新连接或重连的浏览器否则无从
    得知设备链路状态，会把"浏览器↔后端已连接"误当作"设备在线"（安全红线 6
    要求断链时上位机侧显示报警，不得反过来显示正常）。
    """
    try:
        await ws.send_json({"type": "comm_status", "ts": now_iso(), "data": {"status": current_comm_quality(ws)}})
    except Exception as exc:  # noqa: BLE001
        # 补推失败不影响连接本身，后续变迁广播仍会纠正
        logger.warning("ws.comm_status_snapshot_failed", error=str(exc))


@router.websocket("/ws/realtime")
async def realtime(ws: WebSocket, token: str | None = None) -> None:
    """实时推送端点。前端通过 ?token= 传 JWT 认证。"""
    if not token:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    try:
        decode_access_token(token)
    except jwt.PyJWTError:
        await ws.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    await ws_manager.connect(ws)
    await send_comm_status_snapshot(ws)
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
