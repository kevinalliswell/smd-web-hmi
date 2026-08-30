"""HostComm Mock TCP Server —— 无真实 STM32 控制板时用于开发/联调。

规格见开发规格说明书第 8 节。安全约束：不提供任何绕过安全回路、强制打开
CO 阀或强制恢复加热许可的接口（安全红线 1）。

可注入的行为（供测试与联调使用）：
- command_mode: "accept" | "reject" | "busy" | "ignore"
- inject_bad_json: 握手后额外发送一帧非法 JSON（验证客户端容错）
- status_interval: 周期推送 status_snapshot 的间隔秒；None 关闭主动推送
"""

from __future__ import annotations

import argparse
import asyncio
import binascii
import copy
import json
import math
import random
import time
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.hostcomm.protocol import FrameParser, make_frame, now_iso

logger = get_logger("hostcomm.mock")

# 默认参数集（结构见 docs/待确认事项与接口对齐清单.md §2，依据 SOP 工艺口径）
DEFAULT_PARAMS: dict[str, Any] = {
    "process": {
        "gas_switch_temp_deg_c": 500,
        "end_temp_deg_c": 1580,
        "hold_minutes": 30,
        "low_temp_end_hint_deg_c": 200,
        "total_flow_l_min": 5.0,
    },
    "mfc": {
        "n2_reduce_l_min": 3.5,
        "co_reduce_l_min": 1.5,
        "n2_purge_l_min": 2.0,
        "leak_check_n2_l_min": 5.0,
        "co_ratio_pct": 30,
        "deviation_pct": 0.5,
    },
    "temp_program": {
        "seg1_rate_deg_c_min": 10,
        "seg1_to_deg_c": 900,
        "seg2_rate_deg_c_min": 2,
        "seg2_to_deg_c": 1100,
        "seg3_rate_deg_c_min": 5,
        "seg3_to_deg_c": 1600,
    },
    "ai_calib": {"disp_zero": 0.0, "disp_span": 1.0, "dp_zero": 0.0, "dp_span": 1.0},
}


def _crc_hex(values: dict[str, Any]) -> str:
    """与上位机一致的参数 CRC：crc32(排序 JSON)，格式 0x 大写 8 位。"""
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "0x" + format(binascii.crc32(raw.encode("utf-8")) & 0xFFFFFFFF, "08X")


_STATE_SEQUENCE = [
    "Standby",
    "Precheck",
    "Heating",
    "Holding",
    "Reducing",
    "Cooling",
    "Standby",
]


class MockHostCommServer:
    """轻量 TCP Server，模拟 STM32 HostComm 行为。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 34211,
        *,
        command_mode: str = "accept",
        inject_bad_json: bool = False,
        status_interval: float | None = 1.0,
        demo_alarms: bool = False,
        fw_version: str = "FW-MOCK-20260609-01",
    ) -> None:
        self.host = host
        self._requested_port = port
        self.command_mode = command_mode
        self.inject_bad_json = inject_bad_json
        self.status_interval = status_interval
        self.demo_alarms = demo_alarms
        self.fw_version = fw_version

        self._server: asyncio.AbstractServer | None = None
        self._state = "Standby"
        self._test_id: str | None = None
        self._t0 = time.monotonic()
        self._clients: set[asyncio.StreamWriter] = set()
        self._param_values: dict[str, Any] = copy.deepcopy(DEFAULT_PARAMS)

    # ----------------------------------------------------------- 生命周期
    @property
    def port(self) -> int:
        """实际绑定端口（传入 0 时为系统分配端口）。"""
        if self._server is None:
            return self._requested_port
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self._requested_port)
        logger.info("mock.started", host=self.host, port=self.port, mode=self.command_mode)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        for w in list(self._clients):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("mock.stopped")

    # ----------------------------------------------------------- 连接处理
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("mock.client_connected", peer=peer)
        self._clients.add(writer)
        parser = FrameParser()
        push_task: asyncio.Task | None = None
        alarm_task: asyncio.Task | None = None
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                for frame in parser.feed(data):
                    await self._on_frame(frame, writer)
                    # hello 之后启动周期状态推送（若开启）
                    if frame.get("type") == "hello" and self.status_interval and push_task is None:
                        push_task = asyncio.create_task(self._push_loop(writer))
                    # hello 之后启动演示报警循环（若开启）
                    if frame.get("type") == "hello" and self.demo_alarms and alarm_task is None:
                        alarm_task = asyncio.create_task(self._demo_alarm_loop(writer))
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("mock.handler_error", error=str(exc))
        finally:
            for t in (push_task, alarm_task):
                if t is not None:
                    t.cancel()
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info("mock.client_disconnected", peer=peer)

    async def _send(self, writer: asyncio.StreamWriter, frame: dict[str, Any]) -> None:
        writer.write(FrameParser.encode(frame))
        await writer.drain()

    async def _on_frame(self, frame: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        msg_type = frame.get("type")
        payload = frame.get("payload", {}) or {}

        if msg_type == "hello":
            await self._send(writer, self._hello_ack())
            if self.inject_bad_json:
                # 直接写入一行非法 JSON，验证客户端容错（不走 encode）
                writer.write(b"{ this is not valid json \n")
                await writer.drain()

        elif msg_type == "heartbeat":
            await self._send(writer, self._heartbeat_ack())

        elif msg_type == "get_status":
            await self._send(writer, self._status_snapshot())

        elif msg_type == "get_parameters":
            await self._send(writer, self._parameters_snapshot())

        elif msg_type == "command":
            await self._on_command(frame, payload, writer)

        elif msg_type in ("log_request",):
            await self._send(writer, make_frame("log_result", {"result": "no_data"}, prefix="mcu"))

        # 其它类型忽略（真实控制板会返回 error，此处保持容错）

    async def _on_command(self, frame: dict[str, Any], payload: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        command = payload.get("command", "")
        req_id = frame.get("msg_id")

        if self.command_mode == "ignore":
            return  # 模拟超时：不响应

        if self.command_mode == "reject":
            await self._send(
                writer,
                make_frame(
                    "command_result",
                    {
                        "request_msg_id": req_id,
                        "command": command,
                        "result": "rejected",
                        "reason_code": "invalid_state",
                        "reason_text": "mock 拒绝模式",
                        "current_state": self._state,
                    },
                    prefix="mcu",
                ),
            )
            return

        if self.command_mode == "busy":
            await self._send(
                writer,
                make_frame(
                    "command_result",
                    {
                        "request_msg_id": req_id,
                        "command": command,
                        "result": "busy",
                        "reason_code": "busy",
                        "reason_text": "mock 忙",
                        "current_state": self._state,
                    },
                    prefix="mcu",
                ),
            )
            return

        # accept 模式：根据命令推进状态机 / 保存参数
        if command == "start_test":
            self._test_id = payload.get("params", {}).get("test_id")
            self._state = "Precheck"
        elif command == "stop_test":
            self._state = "Cooling"
        elif command == "pause_hold":
            self._state = "Holding"
        elif command == "set_parameters":
            # 保存下发的参数，使后续 get_parameters 回读一致（模拟 STM32 保存+回读）
            values = payload.get("params", {}).get("values")
            if isinstance(values, dict):
                self._param_values = copy.deepcopy(values)

        await self._send(
            writer,
            make_frame(
                "command_result",
                {
                    "request_msg_id": req_id,
                    "command": command,
                    "result": "accepted",
                    "reason_code": "ok",
                    "reason_text": "command accepted",
                    "current_state": self._state,
                },
                prefix="mcu",
            ),
        )

    async def _push_loop(self, writer: asyncio.StreamWriter) -> None:
        """周期推送状态快照（模拟实时刷新）。"""
        try:
            while True:
                await asyncio.sleep(self.status_interval or 1.0)
                await self._send(writer, self._status_snapshot())
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            pass

    # 演示报警序列（对应 SOP §12 异常分级；level 2=L2, 3=L3）
    _DEMO_ALARMS = [
        ("ALM-CO-L1", 2, "CO 一级报警：室内 CO > 25 ppm"),
        ("ALM-DP-HIGH", 2, "料层压差高：> 30 kPa"),
        ("ALM-EXHAUST", 3, "排风故障：风量低于阈值"),
        ("ALM-OVERTEMP", 3, "独立超温报警：料层温度越限"),
    ]

    async def _demo_alarm_loop(self, writer: asyncio.StreamWriter) -> None:
        """演示用：周期性注入一条报警，数秒后消除，循环遍历多种等级。"""
        try:
            i = 0
            await asyncio.sleep(3.0)
            while True:
                code, level, text = self._DEMO_ALARMS[i % len(self._DEMO_ALARMS)]
                await self._send(writer, self._alarm_event(code, level, text, "alarm_new"))
                await asyncio.sleep(5.0)
                await self._send(writer, self._alarm_event(code, level, text, "alarm_clear"))
                await asyncio.sleep(4.0)
                i += 1
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            pass

    def _alarm_event(self, code: str, level: int, text: str, kind: str) -> dict[str, Any]:
        return make_frame(
            "event",
            {
                "event_code": code,
                "level": level,
                "current_state": self._state,
                "text": text,
                "latched": level >= 3,
                "ack_required": True,
                "test_id": self._test_id,
                "kind": kind,
            },
            prefix="mcu",
        )

    # ----------------------------------------------------------- 报文构造
    def _hello_ack(self) -> dict[str, Any]:
        return make_frame(
            "hello_ack",
            {
                "result": "accepted",
                "fw_version": self.fw_version,
                "hw_version": "HW-MOCK-V1.0",
                "device_profile_version": "DP-MOCK-V1.0",
                "parameter_crc": "0x1234ABCD",
                "capabilities": [
                    "status_snapshot",
                    "command",
                    "log_export",
                    "sync_time",
                    "event_push",
                ],
            },
            prefix="mcu",
        )

    def _heartbeat_ack(self) -> dict[str, Any]:
        return make_frame(
            "heartbeat_ack",
            {
                "uptime_s": int(time.monotonic() - self._t0),
                "current_state": self._state,
                "host_comm_status": "online",
            },
            prefix="mcu",
        )

    def _status_snapshot(self) -> dict[str, Any]:
        t = time.monotonic() - self._t0
        wobble = math.sin(t / 5.0)
        pv = 25.0 + (1200.0 if self._state in ("Heating", "Holding", "Reducing") else 0.0)
        pv += wobble * 2.0 + random.uniform(-0.3, 0.3)
        return make_frame(
            "status_snapshot",
            {
                "system": {
                    "fw_version": self.fw_version,
                    "hw_version": "HW-MOCK-V1.0",
                    "protocol_version": "1.0",
                    "device_profile_version": "DP-MOCK-V1.0",
                    "parameter_crc": "0x1234ABCD",
                    "uptime_s": int(t),
                    "rtc_valid": True,
                    "current_state": self._state,
                },
                "state_machine": {
                    "test_id": self._test_id,
                    "current_state": self._state,
                    "previous_state": "Standby",
                    "state_elapsed_s": int(t),
                    "operator_ack_required": False,
                    "fault_reason": None,
                },
                "temperature": {
                    "furnace_pv_deg_c": round(pv, 2),
                    "furnace_sv_deg_c": 25.0,
                    "program_no": 1,
                    "program_step": 0,
                    "temp_ctrl_run_state": "stop",
                    "temp_output_percent": round(max(0.0, wobble * 30.0), 1),
                },
                "gas": {
                    "n2_sp_l_min": 0.0,
                    "n2_pv_l_min": round(random.uniform(0.0, 0.05), 3),
                    "n2_status": "ok",
                    "co_sp_l_min": 0.0,
                    "co_pv_l_min": 0.0,
                    "co_status": "ok",
                    "mixing_mode": "n2_only",
                },
                "measurement": {
                    "drip_weight_g": round(random.uniform(0.0, 0.5), 3),
                    "balance_stable": True,
                    "tare_allowed": self._state == "Standby",
                    "burden_temp_deg_c": round(pv * 0.9, 2),
                    "burden_temp_valid": True,
                    "delta_p_pa": round(random.uniform(0.0, 50.0), 1),
                    "delta_p_valid": True,
                    "displacement_mm": round(random.uniform(0.0, 1.0), 3),
                    "displacement_valid": True,
                },
                "safety": {
                    "safety_relay_allowed": True,
                    "emergency_stop": False,
                    "co_alarm_l1": False,
                    "co_alarm_l2": False,
                    "exhaust_ok": True,
                    "scr_fault": False,
                    "overtemp_alarm": False,
                    "seal_box_overpressure": False,
                },
                "alarm": {
                    "alarm_level": 0,
                    "active_alarm_code": None,
                    "active_alarm_text": None,
                    "latched_alarm_count": 0,
                    "ack_required": False,
                },
                "log": {
                    "current_log_status": "idle",
                    "storage_free_bytes": 1_000_000_000,
                    "last_event_code": None,
                    "last_event_time": now_iso(),
                },
            },
            prefix="mcu",
        )

    def _parameters_snapshot(self) -> dict[str, Any]:
        return make_frame(
            "parameters_snapshot",
            {
                "fw_version": self.fw_version,
                "device_profile_version": "DP-MOCK-V1.0",
                "parameter_crc": _crc_hex(self._param_values),
                "params": copy.deepcopy(self._param_values),
            },
            prefix="mcu",
        )

    def inject_alarm(self, code: str = "ALM-CO-L1", level: int = 2, text: str = "模拟报警") -> None:
        """向所有连接广播一条报警事件（联调时手动触发）。"""
        frame = make_frame(
            "event",
            {
                "event_code": code,
                "level": level,
                "current_state": self._state,
                "text": text,
                "latched": True,
                "ack_required": True,
                "test_id": self._test_id,
                "kind": "alarm_new",
            },
            prefix="mcu",
        )
        for w in list(self._clients):
            try:
                w.write(FrameParser.encode(frame))
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------- CLI 入口
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HostComm Mock Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=34211)
    parser.add_argument(
        "--mode",
        default="normal",
        choices=["normal", "reject_all", "busy_all", "ignore_all"],
        help="命令响应模式",
    )
    parser.add_argument(
        "--fault",
        default=None,
        help="故障注入，例如 disconnect_after=10s（当前仅解析，占位）",
    )
    parser.add_argument("--status-interval", type=float, default=1.0)
    parser.add_argument("--demo-alarms", action="store_true", help="周期性注入演示报警（联调/演示用）")
    return parser.parse_args(argv)


_MODE_MAP = {
    "normal": "accept",
    "reject_all": "reject",
    "busy_all": "busy",
    "ignore_all": "ignore",
}


async def _amain(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    configure_logging()
    server = MockHostCommServer(
        host=args.host,
        port=args.port,
        command_mode=_MODE_MAP.get(args.mode, "accept"),
        status_interval=args.status_interval,
        demo_alarms=args.demo_alarms,
    )
    await server.start()
    logger.info("mock.listening", host=args.host, port=server.port, mode=args.mode)
    try:
        await server.serve_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        await server.stop()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
