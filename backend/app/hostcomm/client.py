"""HostCommClient —— asyncio TCP 长连接客户端（后端 ↔ STM32）。

规格见开发规格说明书第 5.3 节。要点：
- 连接后先 hello/hello_ack 协商能力。
- 周期心跳（默认 2s），连续 N 次超时后断线并进入自动重连。
- 断线自动重连（指数退避 1→2→4→8→30s 上限）。
- 请求/响应匹配：command 按 request_msg_id；快照按协商能力关联，旧协议串行等待。
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
from app.hostcomm.contracts import validate_hello, validate_result
from app.hostcomm.protocol import FrameParser, make_frame, new_msg_id, now_iso

logger = get_logger("hostcomm.client")

Callback = Callable[[dict[str, Any]], Awaitable[None] | None]


class HostCommError(Exception):
    """HostComm 通用错误基类。"""


class HostCommTimeoutError(HostCommError):
    """请求在超时时间内未收到响应。"""


class HostCommProtocolError(HostCommError):
    """协议不兼容或结果不能安全解释。"""


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
        callback_queue_size: int = 256,
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
        self._waiters: dict[str, list[tuple[str, asyncio.Future[dict[str, Any]]]]] = {}
        self._snapshot_locks: dict[str, asyncio.Lock] = {}
        self._command_names: dict[str, str] = {}
        self._sent_command_ids: set[str] = set()
        self._handshake_complete = False
        self._session_id = ""
        self._callback_dropped = 0
        self._callback_overloaded = False
        if callback_queue_size < 1:
            raise ValueError("callback_queue_size must be positive")

        # 任务
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._callback_queues: dict[str, asyncio.Queue[tuple[Callback, dict[str, Any]]]] = {
            "data": asyncio.Queue(maxsize=callback_queue_size),
            "comm": asyncio.Queue(maxsize=8),
        }
        self._callback_workers: dict[str, asyncio.Task[None]] = {}
        self._connection_loss_lock = asyncio.Lock()

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
        return (
            self._connected
            and self._handshake_complete
            and not self._callback_overloaded
            and self._comm_quality == "online"
        )

    @property
    def comm_quality(self) -> str:
        """ "online" / "degraded" / "offline"。"""
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
            "callback_queue_depth": self._callback_queues["data"].qsize(),
            "callback_dropped": self._callback_dropped,
            "callback_overloaded": self._callback_overloaded,
            "session_id": self._session_id,
            "handshake_complete": self._handshake_complete,
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
            if self.auto_reconnect and (self._reconnect_task is None or self._reconnect_task.done()):
                self._reconnect_task = asyncio.create_task(self._reconnect_loop(), name="hostcomm-reconnect")

    async def _open(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.command_timeout
            )
            self._parser = FrameParser()
            self._connected = True
            # 启动收帧任务
            self._reader_task = asyncio.create_task(self._reader_loop(), name="hostcomm-reader")
            # 握手
            self.hello_ack = await self._handshake()
            try:
                validate_hello(self.hello_ack)
            except ValueError as exc:
                raise HostCommProtocolError(str(exc)) from exc
            self._handshake_complete = True
            self._session_id = new_msg_id("session")
            logger.info("hostcomm.connected", host=self.host, port=self.port, caps=self.capabilities)
            await self._set_comm_quality("online")
            # 启动心跳
            self._last_heartbeat_ack = time.monotonic()
            self._missed_heartbeats = 0
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="hostcomm-hb")
        except BaseException:
            # open_connection 成功但握手失败时也必须回滚，防止旧 reader 在
            # 下一次重连后继续读取新连接。
            await self._teardown_connection()
            raise

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
        reconnect_task = self._reconnect_task
        if reconnect_task is not None and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)
        self._reconnect_task = None
        await self._set_comm_quality("offline")
        workers = list(self._callback_workers.values())
        for task in workers:
            task.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._callback_workers.clear()
        for queue in self._callback_queues.values():
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()

    async def _teardown_connection(self) -> None:
        self._connected = False
        self._handshake_complete = False
        tasks = [
            task
            for task in (self._heartbeat_task, self._reader_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        self._heartbeat_task = None
        self._reader_task = None
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # 解除所有挂起请求
        self._fail_pending(HostCommNotConnectedError("connection closed"))

    def _fail_pending(self, exc: Exception) -> None:
        for fut in list(self._pending_cmd.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending_cmd.clear()
        for futs in self._waiters.values():
            for _, fut in futs:
                if not fut.done():
                    fut.set_exception(exc)
        self._waiters.clear()

    # ----------------------------------------------------------- 收帧循环
    async def _reader_loop(self) -> None:
        reader = self._reader
        parser = self._parser
        assert reader is not None
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    raise ConnectionError("peer closed connection")
                for frame in parser.feed(data):
                    await self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.reader_lost", error=str(exc))
            # 旧连接的 reader 若延迟退出，不得拆掉刚建立的新连接。
            if self._reader_task is asyncio.current_task():
                await self._on_connection_lost()

    async def _on_connection_lost(self) -> None:
        async with self._connection_loss_lock:
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
                # _open 自身会回滚；这里再次保证未来实现变化也不会遗留半连接。
                await self._teardown_connection()
                logger.warning("hostcomm.reconnect_failed", error=str(exc))
                delay = min(delay * 2, self.reconnect_max)

    # ----------------------------------------------------------- 分发
    async def _dispatch(self, frame: dict[str, Any]) -> None:
        msg_type = frame.get("type")
        payload = frame.get("payload", {}) or {}
        if not isinstance(payload, dict):
            raise HostCommProtocolError("HostComm payload 必须是对象")

        if msg_type == "heartbeat_ack":
            self._last_heartbeat_ack = time.monotonic()
            self._missed_heartbeats = 0
            if self._handshake_complete:
                await self._set_comm_quality("online")
            return

        if msg_type == "status_snapshot":
            if not self._handshake_complete:
                return
            await self._set_comm_quality("online")
            # 先唤醒请求方，再把 DB/WS 回调放到独立任务，保持收帧循环畅通。
            self._resolve_waiter("status_snapshot", frame)
            self._schedule_callback(self.on_status, self._with_receipt(frame))
            return

        if msg_type in ("hello_ack", "parameters_snapshot"):
            self._resolve_waiter(msg_type, frame)
            return

        if msg_type == "command_result":
            req = payload.get("request_msg_id")
            fut = self._pending_cmd.pop(req, None)
            if fut is not None and not fut.done():
                try:
                    validate_result(payload, self._command_names.get(req, ""))
                except ValueError as exc:
                    fut.set_exception(HostCommProtocolError(str(exc)))
                else:
                    fut.set_result(frame)
            return

        if msg_type == "event":
            if not self._handshake_complete:
                return
            self._schedule_callback(self.on_event, self._with_receipt(frame))
            return

        if msg_type == "error":
            logger.warning("hostcomm.error_frame", payload=payload)
            request_id = payload.get("request_msg_id")
            error = HostCommProtocolError(str(payload.get("reason_code", "device_protocol_error")))
            fut = self._pending_cmd.pop(request_id, None)
            if fut is not None and not fut.done():
                fut.set_exception(error)
            for kind, waiters in self._waiters.items():
                for mid, waiter in waiters:
                    if not waiter.done() and (mid == request_id or (kind == "hello_ack" and not request_id)):
                        waiter.set_exception(error)
            return

        logger.debug("hostcomm.unhandled_frame", type=msg_type)

    def _with_receipt(self, frame: dict[str, Any]) -> dict[str, Any]:
        payload = dict(frame.get("payload") or {})
        payload["_hostcomm"] = {
            "msg_id": frame.get("msg_id"),
            "device_timestamp": frame.get("timestamp"),
            "received_at": now_iso(),
            "received_monotonic": time.monotonic(),
            "session_id": self._session_id,
            "dropped_callbacks": self._callback_dropped,
            "capabilities": self.capabilities.copy(),
        }
        return payload

    def _schedule_callback(self, cb: Callback | None, payload: dict[str, Any], *, channel: str = "data") -> None:
        if cb is None:
            return
        queue = self._callback_queues[channel]
        if queue.full():
            if channel == "data":
                self._callback_dropped += 1
                self._callback_overloaded = True
                self._comm_quality = "degraded"
                logger.error("hostcomm.callback_overflow", dropped=self._callback_dropped)
                self._schedule_callback(
                    self.on_comm_status,
                    {
                        "status": "degraded",
                        "reason": "callback_overload",
                        "dropped_callbacks": self._callback_dropped,
                    },
                    channel="comm",
                )
                return
            # 通信状态只保留最近变化；数据缺口计数在 stats 中持续保留。
            queue.get_nowait()
            queue.task_done()
        queue.put_nowait((cb, payload))
        worker = self._callback_workers.get(channel)
        if worker is None or worker.done():
            worker = asyncio.create_task(self._callback_worker(channel), name=f"hostcomm-callback-{channel}")
            self._callback_workers[channel] = worker
            worker.add_done_callback(lambda task: self._forget_callback_worker(channel, task))

    def _forget_callback_worker(self, channel: str, task: asyncio.Task[None]) -> None:
        if self._callback_workers.get(channel) is task:
            self._callback_workers.pop(channel, None)

    async def _callback_worker(self, channel: str) -> None:
        queue = self._callback_queues[channel]
        while True:
            cb, payload = await queue.get()
            try:
                await self._emit(cb, payload)
            finally:
                queue.task_done()
                if channel == "data" and queue.empty():
                    self._callback_overloaded = False

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
        request_id = (frame.get("payload") or {}).get("request_msg_id")
        correlated = expect_type != "hello_ack" and "request_correlation_v1" in self.capabilities
        for index, (mid, fut) in enumerate(futs):
            if (request_id is not None and request_id != mid) or (correlated and request_id is None):
                continue
            futs.pop(index)
            if not fut.done():
                fut.set_result(frame)
            return

    # ----------------------------------------------------------- 发送原语
    async def _send(self, frame: dict[str, Any]) -> None:
        if self._writer is None or not self._connected:
            raise HostCommNotConnectedError("not connected")
        self._writer.write(FrameParser.encode(frame))
        try:
            await asyncio.wait_for(self._writer.drain(), timeout=self.command_timeout)
        except asyncio.TimeoutError as exc:
            raise HostCommTimeoutError("HostComm 写入超时，执行结果未知") from exc

    async def _await_type(self, expect_type: str, frame: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """发送 frame 并等待下一帧 expect_type 类型的响应。"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        entry = (frame["msg_id"], fut)
        self._waiters.setdefault(expect_type, []).append(entry)
        try:
            await self._send(frame)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            if expect_type != "hello_ack" and "request_correlation_v1" not in self.capabilities:
                # 旧协议没有响应ID，超时后必须换会话，防止迟到回复冒充下一请求。
                await self._on_connection_lost()
            raise HostCommTimeoutError(f"等待 {expect_type} 超时（{timeout}s）") from exc
        finally:
            futs = self._waiters.get(expect_type)
            if futs and entry in futs:
                futs.remove(entry)

    # ----------------------------------------------------------- 公共接口
    async def get_status(self) -> dict[str, Any]:
        """请求并返回最新 status_snapshot 的 payload。"""
        frame = make_frame("get_status", prefix="pc-status")
        async with self._snapshot_locks.setdefault("status_snapshot", asyncio.Lock()):
            resp = await self._await_type("status_snapshot", frame, timeout=self.command_timeout)
        return resp.get("payload", {})

    async def get_parameters(self) -> dict[str, Any]:
        """请求并返回 parameters_snapshot 的 payload。"""
        frame = make_frame("get_parameters", prefix="pc-param")
        async with self._snapshot_locks.setdefault("parameters_snapshot", asyncio.Lock()):
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

        超时表示执行结果未知。稳定 msg_id 由持久事务提供；未经验证的
        固件去重不能作为自动重发依据，本客户端拒绝再次发送已发送ID。
        """
        if not self.is_online or "command" not in self.capabilities:
            raise HostCommNotConnectedError("未完成协商或数据通道过载，禁止新控制请求")
        if command == "sync_time" and "sync_time" not in self.capabilities:
            raise HostCommProtocolError("固件未声明 sync_time 能力")
        mid = msg_id or new_msg_id("pc-cmd")
        if mid in self._sent_command_ids:
            raise HostCommProtocolError("不能自动重发已发送的 msg_id；请查询持久操作状态")
        self._sent_command_ids.add(mid)
        self._command_names[mid] = command
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
            self._command_names.pop(mid, None)

    # ----------------------------------------------------------- 心跳
    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(self.heartbeat_interval)
                if not self._connected:
                    return
                try:
                    frame = make_frame("heartbeat", {"client_id": self.client_id}, prefix="pc-hb")
                    await self._send(frame)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("hostcomm.heartbeat_send_failed", error=str(exc))
                    await self._on_connection_lost()
                    return
                # 检查心跳新鲜度
                if self._last_heartbeat_ack is not None:
                    elapsed = time.monotonic() - self._last_heartbeat_ack
                    interval = max(self.heartbeat_interval, 0.001)
                    self._missed_heartbeats = int(elapsed / interval)
                    if elapsed >= interval * self.timeout_count:
                        self._missed_heartbeats = max(self._missed_heartbeats, self.timeout_count)
                        logger.warning(
                            "hostcomm.heartbeat_timeout",
                            missed=self._missed_heartbeats,
                            elapsed_s=round(elapsed, 3),
                        )
                        await self._on_connection_lost()
                        return
                    # 刚发送的本轮 heartbeat 尚未来得及被 reader 处理，不因
                    # 单个周期边界的调度抖动反复闪烁 degraded。
                    if self._missed_heartbeats > 1:
                        await self._set_comm_quality("degraded")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("hostcomm.heartbeat_error", error=str(exc))

    # ----------------------------------------------------------- comm_quality
    async def _set_comm_quality(self, quality: str) -> None:
        if quality == "online" and self._callback_overloaded:
            quality = "degraded"
        if quality == self._comm_quality:
            return
        self._comm_quality = quality
        logger.info("hostcomm.comm_quality", quality=quality)
        self._schedule_callback(self.on_comm_status, {"status": quality}, channel="comm")


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
