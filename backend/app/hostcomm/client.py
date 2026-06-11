"""HostCommClient —— asyncio TCP 长连接客户端（后端 ↔ STM32）。

规格见开发规格说明书第 5.3 节。要点：
- 连接后先 hello/hello_ack 协商能力。
- 周期心跳（默认 2s），连续 N 次超时标记 degraded。
- 断线自动重连（指数退避 1→2→4→8→30s 上限）。
- 请求/响应匹配：command 按 request_msg_id；status/parameters 按下一帧类型。
- 收帧分发：status_snapshot 更新缓存并广播，event 写日志并广播。
- 帧解析容错（非法 JSON 不 crash，由 FrameParser 处理）。

安全约束：仅发送"请求"，最终裁决在 STM32 与硬接线联锁；不提供任何强制/绕过接口。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.hostcomm.protocol import FrameParser, make_frame, new_msg_id

logger = get_logger("hostcomm.client")

Callback = Callable[[dict[str, Any]], Awaitable[None] | None]


class HostCommError(Exception):
    """HostComm 通用错误基类。"""


class HostCommTimeoutError(HostCommError):
    """请求在超时时间内未收到响应。"""


class HostCommNotConnectedError(HostCommError):
    """当前未连接，无法发送请求。"""


# status/parameters 这类"请求下一帧"的等待类型
_SNAPSHOT_TYPES = {"status_snapshot", "parameters_snapshot", "hello_ack"}


class HostCommClient:
    """HostComm TCP 客户端。"""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        heartbeat_interval: float = 2.0,
        timeout_count: int = 3,
        command_timeout: float = 3.0,
        client_id: str = "hmi-01",
        client_name: str = "smd-web-backend",
        client_version: str = "0.1.0",
        auto_reconnect: bool = True,
        reconnect_base: float = 1.0,
        reconnect_max: float = 30.0,
        on_status: Callback | None = None,
        on_event: Callback | None = None,
        on_comm_status: Callback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.timeout_count = timeout_count
        self.command_timeout = command_timeout
        self.client_id = client_id
        self.client_name = client_name
        self.client_version = client_version
        self.auto_reconnect = auto_reconnect
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max

        self.on_status = on_status
        self.on_event = on_event
        self.on_comm_status = on_comm_status

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._parser = FrameParser()

        # 请求跟踪
        self._pending_cmd: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}

        # 任务
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

        # 状态
        self._connected = False
        self._stopping = False
        self._comm_quality = "offline"
        self._last_heartbeat_ack: float | None = None
        self._missed_heartbeats = 0
        self.hello_ack: dict[str, Any] | None = None

    # ----------------------------------------------------------- 属性
    @property
    def is_online(self) -> bool:
        return self._connected and self._comm_quality == "online"

    @property
    def comm_quality(self) -> str:
        """"online" / "degraded" / "offline"。"""
        return self._comm_quality

    @property
    def capabilities(self) -> list[str]:
        if self.hello_ack:
            return list(self.hello_ack.get("payload", {}).get("capabilities", []))
        return []

    @property
    def stats(self) -> dict[str, Any]:
        age = None
        if self._last_heartbeat_ack is not None:
            age = round(time.monotonic() - self._last_heartbeat_ack, 2)
        return {
            "comm_quality": self._comm_quality,
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "fw_version": (self.hello_ack or {}).get("payload", {}).get("fw_version") if self.hello_ack else None,
            "capabilities": self.capabilities,
            "frames_parsed": self._parser.frames_parsed,
            "frames_dropped": self._parser.frames_dropped,
            "json_errors": self._parser.json_errors,
            "heartbeat_age_s": age,
            "missed_heartbeats": self._missed_heartbeats,
        }

    # ----------------------------------------------------------- 连接生命周期
    async def connect(self) -> None:
        """建立连接并完成 hello 握手，启动收帧与心跳任务。"""
        self._stopping = False
        await self._open()

    async def start(self) -> None:
        """应用启动用：尝试连接，失败不抛出，转入后台自动重连。

        适用于无控制板/Mock 尚未就绪的场景，保证后端启动不被阻断。
        """
        self._stopping = False
        try:
            await self._open()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.initial_connect_failed", error=str(exc))
            await self._teardown_connection()
            await self._set_comm_quality("offline")
            if self.auto_reconnect and (
                self._reconnect_task is None or self._reconnect_task.done()
            ):
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_loop(), name="hostcomm-reconnect"
                )

    async def _open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._parser = FrameParser()
        self._connected = True
        # 启动收帧任务
        self._reader_task = asyncio.create_task(self._reader_loop(), name="hostcomm-reader")
        # 握手
        self.hello_ack = await self._handshake()
        logger.info("hostcomm.connected", host=self.host, port=self.port, caps=self.capabilities)
        await self._set_comm_quality("online")
        # 启动心跳
        self._last_heartbeat_ack = time.monotonic()
        self._missed_heartbeats = 0
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="hostcomm-hb")

    async def _handshake(self) -> dict[str, Any]:
        frame = make_frame(
            "hello",
            {"client_name": self.client_name, "client_version": self.client_version},
            prefix="pc-hello",
        )
        return await self._await_type("hello_ack", frame, timeout=self.command_timeout)

    async def close(self) -> None:
        """主动断开并停止所有后台任务（不再自动重连）。"""
        self._stopping = True
        await self._teardown_connection()
        for task in (self._reconnect_task,):
            if task is not None:
                task.cancel()
        self._reconnect_task = None
        await self._set_comm_quality("offline")

    async def _teardown_connection(self) -> None:
        self._connected = False
        for task in (self._heartbeat_task, self._reader_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self._heartbeat_task = None
        self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None
        # 解除所有挂起请求
        self._fail_pending(HostCommNotConnectedError("connection closed"))

    def _fail_pending(self, exc: Exception) -> None:
        for fut in list(self._pending_cmd.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending_cmd.clear()
        for futs in self._waiters.values():
            for fut in futs:
                if not fut.done():
                    fut.set_exception(exc)
        self._waiters.clear()

    # ----------------------------------------------------------- 收帧循环
    async def _reader_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                data = await self._reader.read(4096)
                if not data:
                    raise ConnectionError("peer closed connection")
                for frame in self._parser.feed(data):
                    await self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.reader_lost", error=str(exc))
            await self._on_connection_lost()

    async def _on_connection_lost(self) -> None:
        if self._stopping:
            return
        await self._teardown_connection()
        await self._set_comm_quality("offline")
        if self.auto_reconnect and (self._reconnect_task is None or self._reconnect_task.done()):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop(), name="hostcomm-reconnect")

    async def _reconnect_loop(self) -> None:
        delay = self.reconnect_base
        while not self._stopping:
            await asyncio.sleep(delay)
            if self._stopping:
                return
            try:
                logger.info("hostcomm.reconnecting", host=self.host, port=self.port)
                await self._open()
                logger.info("hostcomm.reconnected")
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("hostcomm.reconnect_failed", error=str(exc))
                delay = min(delay * 2, self.reconnect_max)

    # ----------------------------------------------------------- 分发
    async def _dispatch(self, frame: dict[str, Any]) -> None:
        msg_type = frame.get("type")
        payload = frame.get("payload", {}) or {}

        if msg_type == "heartbeat_ack":
            self._last_heartbeat_ack = time.monotonic()
            self._missed_heartbeats = 0
            await self._set_comm_quality("online")
            return

        if msg_type == "status_snapshot":
            await self._set_comm_quality("online")
            await self._emit(self.on_status, payload)
            self._resolve_waiter("status_snapshot", frame)
            return

        if msg_type in ("hello_ack", "parameters_snapshot"):
            self._resolve_waiter(msg_type, frame)
            return

        if msg_type == "command_result":
            req = payload.get("request_msg_id")
            fut = self._pending_cmd.pop(req, None)
            if fut is not None and not fut.done():
                fut.set_result(frame)
            return

        if msg_type == "event":
            await self._emit(self.on_event, payload)
            return

        if msg_type == "error":
            logger.warning("hostcomm.error_frame", payload=payload)
            return

        logger.debug("hostcomm.unhandled_frame", type=msg_type)

    async def _emit(self, cb: Callback | None, payload: dict[str, Any]) -> None:
        if cb is None:
            return
        try:
            result = cb(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.callback_error", error=str(exc))

    def _resolve_waiter(self, expect_type: str, frame: dict[str, Any]) -> None:
        futs = self._waiters.get(expect_type)
        if not futs:
            return
        fut = futs.pop(0)
        if not fut.done():
            fut.set_result(frame)

    # ----------------------------------------------------------- 发送原语
    async def _send(self, frame: dict[str, Any]) -> None:
        if self._writer is None or not self._connected:
            raise HostCommNotConnectedError("not connected")
        self._writer.write(FrameParser.encode(frame))
        await self._writer.drain()

    async def _await_type(
        self, expect_type: str, frame: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        """发送 frame 并等待下一帧 expect_type 类型的响应。"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters.setdefault(expect_type, []).append(fut)
        try:
            await self._send(frame)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise HostCommTimeoutError(f"等待 {expect_type} 超时（{timeout}s）") from exc
        finally:
            futs = self._waiters.get(expect_type)
            if futs and fut in futs:
                futs.remove(fut)

    # ----------------------------------------------------------- 公共接口
    async def get_status(self) -> dict[str, Any]:
        """请求并返回最新 status_snapshot 的 payload。"""
        frame = make_frame("get_status", prefix="pc-status")
        resp = await self._await_type("status_snapshot", frame, timeout=self.command_timeout)
        return resp.get("payload", {})

    async def get_parameters(self) -> dict[str, Any]:
        """请求并返回 parameters_snapshot 的 payload。"""
        frame = make_frame("get_parameters", prefix="pc-param")
        resp = await self._await_type("parameters_snapshot", frame, timeout=self.command_timeout)
        return resp.get("payload", {})

    async def send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        operator_id: str,
        role: str,
        confirm_token: str | None = None,
        msg_id: str | None = None,
    ) -> dict[str, Any]:
        """发送 command 帧，等待 command_result，返回其 payload。

        超时（默认 3s）抛出 HostCommTimeoutError。msg_id 不复用，重试由上层
        重新生成（命令幂等性见规格 5.4）。
        """
        mid = msg_id or new_msg_id("pc-cmd")
        payload: dict[str, Any] = {
            "command": command,
            "operator_id": operator_id,
            "operator_role": role,
            "params": params or {},
        }
        if confirm_token is not None:
            payload["confirm_token"] = confirm_token
        frame = make_frame("command", payload, msg_id=mid)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_cmd[mid] = fut
        try:
            await self._send(frame)
            resp = await asyncio.wait_for(fut, self.command_timeout)
            return resp.get("payload", {})
        except asyncio.TimeoutError as exc:
            raise HostCommTimeoutError(f"命令 {command} 超时（{self.command_timeout}s）") from exc
        finally:
            self._pending_cmd.pop(mid, None)

    # ----------------------------------------------------------- 心跳
    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(self.heartbeat_interval)
                if not self._connected:
                    return
                try:
                    frame = make_frame(
                        "heartbeat", {"client_id": self.client_id}, prefix="pc-hb"
                    )
                    await self._send(frame)
                except HostCommNotConnectedError:
                    return
                # 检查心跳新鲜度
                if self._last_heartbeat_ack is not None:
                    elapsed = time.monotonic() - self._last_heartbeat_ack
                    if elapsed > self.heartbeat_interval * self.timeout_count:
                        self._missed_heartbeats += 1
                        await self._set_comm_quality("degraded")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.heartbeat_error", error=str(exc))

    # ----------------------------------------------------------- comm_quality
    async def _set_comm_quality(self, quality: str) -> None:
        if quality == self._comm_quality:
            return
        self._comm_quality = quality
        logger.info("hostcomm.comm_quality", quality=quality)
        await self._emit(self.on_comm_status, {"status": quality})


# ---- 简易 CLI：/hostcomm-ping 使用（只读握手）-----------------------------
async def _ping(host: str, port: int) -> None:
    from app.core.logging import configure_logging

    configure_logging()
    client = HostCommClient(host, port, auto_reconnect=False)
    t0 = time.monotonic()
    await client.connect()
    dt = (time.monotonic() - t0) * 1000
    ack = client.hello_ack or {}
    p = ack.get("payload", {})
    print(f"[hostcomm-ping] 握手成功 ({dt:.0f}ms)")
    print(f"  fw_version   = {p.get('fw_version')}")
    print(f"  capabilities = {p.get('capabilities')}")
    await client.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="HostComm 连接测试（只读 hello 握手）")
    parser.add_argument("--ping", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=34211)
    args = parser.parse_args()
    asyncio.run(_ping(args.host, args.port))


if __name__ == "__main__":
    main()
