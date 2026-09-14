"""Application adapter for an explicitly selected HostComm 2 device.

Only this adapter owns the authenticated connection. HTTP operations keep their
existing audit IDs; a stable UUID maps each durable HTTP message to one wire
operation. Reads and log recovery never acquire control or replay a command.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.cancellation import finish_db_work
from app.db.recipe_models import RecipeVersion
from app.db.v2_models import V2LogCursor, V2Operation, V2RecipeBinding, V2RunBinding
from app.hostcomm.client import HostCommNotConnectedError, HostCommProtocolError, HostCommTimeoutError
from app.hostcomm.protocol import now_iso
from app.hostcomm.v2_contract.codec import canonical_bytes, decode_chunk, digest, split_chunks, validate_recipe_bytes
from app.hostcomm.v2_transport import V2CapacityError, V2OfflineError, V2ProtocolError, V2RequestTimeout, V2Transport
from app.services.v2_operations import V2OperationCoordinator, V2OperationError, ensure_v2_identity
from app.services.v2_recipe_compiler import compile_recipe
from app.services.v2_source_logs import V2SourceLogStore, read_alarm_snapshot

logger = get_logger("hostcomm.v2_client")


def fail(code: str, text: str, status: int = 409):
    from app.services.command_service import CommandError

    raise CommandError(status, code, text)


def _is_local_read_capacity(error: Exception) -> bool:
    from app.services.command_service import CommandError

    return isinstance(error, V2CapacityError) or (
        isinstance(error, CommandError) and error.status_code == 503 and error.error_code == "device_read_capacity"
    )


class V2Client:
    protocol_version = "2.0"

    def __init__(
        self,
        host,
        port,
        *,
        factory,
        device_id,
        controller_id=None,
        controller_epoch=None,
        psk_file="",
        mock=False,
        client_version,
        on_status=None,
        on_event=None,
        on_comm_status=None,
    ):
        self.host, self.port, self.factory, self.device_id = host, port, factory, device_id
        self.controller_id, self.controller_epoch = controller_id or None, controller_epoch or None
        self.psk_file, self.mock, self.client_version = psk_file, mock, client_version
        self.on_status, self.on_event, self.on_comm_status = on_status, on_event, on_comm_status
        self.transport = self.operations = self.logs = self.archive = None
        self.profile = self._profile_frame = self._status_frame = self._telemetry_frame = None
        self._write_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._log_lock = asyncio.Lock()
        self._alarm_lock = asyncio.Lock()
        self._recovery_task = self._poll_task = None
        self._source_recovery_task = None
        self._source_recovery_pending = False
        self._refresh_task = None
        self._refresh_pending = self._refresh_needs_status = False
        self._closed = False
        self._ready = False
        self._recovery_failures = 0
        self._next_recovery_at = 0.0
        self.alarms = {"revision": "0", "items": []}
        self.last_error = None

    @property
    def is_online(self):
        return bool(self.transport and self.transport.is_online)

    @property
    def current_run_identity(self):
        frame = self._status_frame
        if (
            not self.is_online
            or frame is None
            or frame["session_id"] != self.transport.session_id
            or frame["boot_id"] != self.transport.boot_id
            or frame["payload"]["run"]["run_id"] is None
        ):
            return None
        return {
            "device_id": self.device_id,
            "run_id": frame["payload"]["run"]["run_id"],
            "boot_id": frame["boot_id"],
            "session_id": frame["session_id"],
        }

    @property
    def comm_quality(self):
        return "good" if self.is_online and self._ready else "offline"

    @property
    def capabilities(self):
        return list((self.transport.hello_payload or {}).get("capabilities", [])) if self.is_online else []

    @property
    def hello_ack(self):
        return {"protocol_version": "2.0", "payload": self.transport.hello_payload} if self.is_online else None

    @property
    def stats(self):
        return {
            **(self.transport.stats if self.transport else {}),
            "protocol_version": "2.0",
            "status": self.comm_quality,
            "last_error": self.last_error,
        }

    async def start(self):
        try:
            identity = await ensure_v2_identity(
                self.factory, self.device_id, self.controller_id, self.controller_epoch, write_lock=self._write_lock
            )
            self.controller_id, self.controller_epoch = identity["controller_id"], identity["controller_epoch"]
            self.transport = V2Transport(
                self.host,
                self.port,
                device_id=self.device_id,
                controller_id=self.controller_id,
                controller_epoch=self.controller_epoch,
                psk_file=self.psk_file,
                client_version=self.client_version,
                mock=self.mock,
                on_message=self._on_message,
                on_connection=self._on_connection,
                on_state_revision=self._on_state_revision,
            )
            self.operations = V2OperationCoordinator(
                self.factory,
                self.transport,
                device_id=self.device_id,
                controller_id=self.controller_id,
                controller_epoch=self.controller_epoch,
                write_lock=self._write_lock,
            )
            await self.operations.initialize()
            from app.services.v2_archive import V2ArchiveProjector

            self.archive = V2ArchiveProjector(self.device_id)
            self.logs = V2SourceLogStore(
                self.factory, device_id=self.device_id, on_record=self.archive.on_record, write_lock=self._write_lock
            )
            await self.transport.start()
            self._poll_task = asyncio.create_task(self._poll())
        except Exception as exc:
            # Installation/history remain available without a paired device.
            self.last_error = type(exc).__name__
            logger.warning("v2.start_unavailable", reason=self.last_error)
            if self.on_comm_status:
                await self.on_comm_status({"status": "offline", "reason": "v2_pairing_or_runtime_unavailable"})

    async def close(self):
        self._closed = True
        tasks = [
            task
            for task in (self._recovery_task, self._poll_task, self._source_recovery_task, self._refresh_task)
            if task
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.transport:
            await self.transport.close()

    async def connect(self):
        # Persistent reconnect belongs to V2Transport; never open a second gateway.
        return self.is_online

    async def _request(self, kind, payload, **kwargs):
        if not self.is_online:
            raise HostCommNotConnectedError("HostComm 2 未建立认证会话")
        try:
            return await self.transport.request(kind, payload, **kwargs)
        except V2RequestTimeout as exc:
            raise HostCommTimeoutError("HostComm 2 响应超时") from exc
        except V2OfflineError as exc:
            raise HostCommNotConnectedError("HostComm 2 会话已断开") from exc
        except V2ProtocolError as exc:
            raise HostCommProtocolError("HostComm 2 响应不符合契约") from exc
        except V2CapacityError as exc:
            fail("device_read_capacity", "设备读取请求繁忙，请稍后查询", 503)

    async def _on_connection(self, event):
        if self._closed:
            return
        self._ready = False
        self._status_frame = self._telemetry_frame = self._profile_frame = self.profile = None
        tasks = [task for task in (self._recovery_task, self._refresh_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_pending = self._refresh_needs_status = False
        if self.on_comm_status:
            await self.on_comm_status(event)
        if event["status"] == "online":
            # A separate task lets the transport callback queue service log ACKs.
            self._recovery_task = asyncio.create_task(self._recover())

    async def _recover(self):
        try:
            await self.operations.reconcile_highwater(self.transport.hello_payload["last_command_seq"])
            await self.get_profile()
            await self.refresh_operations()
            await self.get_status()
            await self.recover_logs()
            self._source_recovery_pending = False
            self._ready = True
            self._recovery_failures = 0
            self.last_error = None
            await self.get_status()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._recovery_failures += 1
            self._next_recovery_at = time.monotonic() + min(2 ** min(self._recovery_failures, 5), 30)
            self.last_error = type(exc).__name__
            logger.warning("v2.recovery_incomplete", reason=self.last_error)
            if self.on_comm_status:
                await self.on_comm_status({"status": "degraded", "reason": "v2_recovery_incomplete"})

    async def _poll(self):
        while not self._closed:
            await asyncio.sleep(2)
            if self.is_online:
                try:
                    await self.get_status()
                    if (
                        not self._ready
                        and time.monotonic() >= self._next_recovery_at
                        and (self._recovery_task is None or self._recovery_task.done())
                    ):
                        self._recovery_task = asyncio.create_task(self._recover())
                    run = self._status_frame["payload"]["run"]
                    if (
                        self._ready
                        and (run["measurement_complete"] or run["safe_complete"] or self._source_recovery_pending)
                        and (self._source_recovery_task is None or self._source_recovery_task.done())
                    ):
                        self._source_recovery_task = asyncio.create_task(self._recover_completed_sources())
                except Exception as exc:
                    self.last_error = type(exc).__name__

    async def _recover_completed_sources(self):
        try:
            await self.recover_logs()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _is_local_read_capacity(exc):
                # Local backpressure does not disconnect. Retain the read-only
                # scan even if ack_run returns the live device to idle meanwhile.
                self._source_recovery_pending = True
            self.last_error = type(exc).__name__
            logger.warning("v2.source_recovery_incomplete", reason=self.last_error)
        else:
            self._source_recovery_pending = False

    async def _on_message(self, frame):
        kind = frame["type"]
        if kind == "log_chunk":
            ack = await self.logs.append_chunk(frame)
            await self.transport.send("log_ack", ack)
        elif kind in {"telemetry", "event"}:
            await self.logs.ingest_live(frame)
            if kind == "telemetry":
                previous = self._telemetry_frame
                if (
                    previous
                    and previous["boot_id"] == frame["boot_id"]
                    and int(frame["payload"]["sample"]["sample_seq"])
                    <= int(previous["payload"]["sample"]["sample_seq"])
                ):
                    return
                self._telemetry_frame = frame
                self._schedule_refresh(
                    needs_status=(
                        self._status_frame is None
                        or frame["payload"]["state_revision"] != self._status_frame["payload"]["run"]["state_revision"]
                    )
                )
            else:
                if self.on_event:
                    await self.on_event(
                        {
                            "_v2_persisted": True,
                            "kind": frame["payload"]["kind"],
                            "source": "HostComm 2",
                            "payload": frame["payload"],
                        }
                    )
                self._schedule_refresh(needs_status=True)

    def _on_state_revision(self, revision):
        if self._status_frame is None or revision > int(self._status_frame["payload"]["run"]["state_revision"]):
            self._schedule_refresh(needs_status=True)

    def _schedule_refresh(self, *, needs_status):
        # Optional reads/publishing must never hold the durable source callback
        # lane: the next log chunk has an independent 3s progress deadline.
        if self._closed or not self.is_online:
            return
        self._refresh_pending = True
        self._refresh_needs_status |= needs_status
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_status())

    async def _refresh_status(self):
        try:
            while self._refresh_pending and not self._closed:
                needs_status = self._refresh_needs_status
                self._refresh_pending = self._refresh_needs_status = False
                if needs_status:
                    await self._optional_status()
                else:
                    await self._publish()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Polling owns recovery. A failed optional worker must not leave
            # control advertised as ready or create unobserved task failures.
            self._ready = False
            self.last_error = type(exc).__name__
            logger.warning("v2.status_refresh_incomplete", reason=self.last_error)
            if self.on_comm_status:
                await self.on_comm_status({"status": "degraded", "reason": "v2_status_refresh_incomplete"})

    async def _optional_status(self):
        try:
            await self.get_status()
        except Exception as exc:
            if not _is_local_read_capacity(exc):
                raise
            # The source is already durable. A full read window must not turn an
            # optional UI refresh into a disconnect that could hide a stop ACK.

    async def _archive_alarms(self):
        await self._persist_alarms(self.alarms, self.transport.boot_id)
        if self.on_event:
            await self.on_event({"kind": "alarms_resynced", "_v2_persisted": True})

    @finish_db_work
    async def _persist_alarms(self, snapshot, boot_id):
        async with self._write_lock, self.factory() as db, db.begin():
            await self.archive.reconcile_alarms(db, snapshot, boot_id=boot_id)

    async def get_profile(self):
        frame = await self._request("get_profile", {})
        if frame["payload"]["profile_digest"] != self.transport.hello_payload["profile_digest"]:
            raise HostCommProtocolError("工程配置与握手摘要不一致，必须重新握手")
        self._profile_frame, self.profile = frame, frame["payload"]
        return self.profile

    async def get_status(self):
        frame = await self._request("get_status", {})
        if self.profile is not None and frame["payload"]["profile_digest"] != self.profile["profile_digest"]:
            self._ready = False
            raise HostCommProtocolError("运行期间工程配置身份变化")
        previous = self._status_frame
        if previous and previous["boot_id"] == frame["boot_id"]:
            current_revision = int(frame["payload"]["run"]["state_revision"])
            previous_revision = int(previous["payload"]["run"]["state_revision"])
            if current_revision < previous_revision or (
                current_revision == previous_revision and int(frame["uptime_ms"]) < int(previous["uptime_ms"])
            ):
                return await self._publish()
        self._status_frame = frame
        async with self._alarm_lock:
            if (
                self.alarms.get("boot_id") != frame["boot_id"]
                or self.alarms["revision"] != frame["payload"]["active_alarm_revision"]
            ):
                self.alarms = await read_alarm_snapshot(self.transport)
                await self._archive_alarms()
        await self._persist_status(frame)
        return await self._publish()

    @finish_db_work
    async def _persist_status(self, frame):
        async with self._write_lock, self.factory() as db, db.begin():
            await self.archive.reconcile_status(
                db,
                frame["payload"],
                boot_id=frame["boot_id"],
                received_at=self.transport.receipt_metadata(frame["msg_id"]).get("received_at"),
            )

    async def _publish(self):
        from app.hostcomm.v2_projection import project_status

        frame = self._status_frame
        if frame is None:
            return {}
        sample = self._telemetry_frame
        snapshot = project_status(
            frame,
            sample,
            self._profile_frame,
            hello_payload=self.transport.hello_payload,
            online=self.is_online,
            control_ready=self._ready,
            status_receipt=self.transport.receipt_metadata(frame["msg_id"]),
            telemetry_receipt=self.transport.receipt_metadata(sample["msg_id"]) if sample else None,
            alarm_snapshot=self.alarms,
            lease_evidence=self.transport.lease_evidence(),
        )
        snapshot.setdefault("state_machine", {})["test_id"] = await self._test_id_for_run(
            frame["payload"]["run"]["run_id"]
        )
        from app.services.v2_run_recovery import recovery_for_run

        recovery = await recovery_for_run(self.factory, self.device_id, frame["payload"]["run"]["run_id"])
        required = bool(recovery and (recovery["review_state"] != "bound" or recovery["replay_status"] != "complete"))
        snapshot["_v2"]["recovery"] = recovery
        snapshot["system"]["run_recovery_required"] = required
        if recovery and (recovery["review_state"] != "bound" or recovery["replay_status"] == "conflict"):
            snapshot["system"]["can_ack_run"] = False
        snapshot["_v2_persisted"] = True
        if self.on_status:
            await self.on_status(snapshot)
        return snapshot

    @finish_db_work
    async def _test_id_for_run(self, run_id):
        async with self.factory() as db:
            binding = await db.get(V2RunBinding, (self.device_id, run_id)) if run_id else None
            return binding.test_id if binding else None

    @finish_db_work
    async def _operation_ids(self, statuses, *, exclude_stop=False, limit=128):
        async with self.factory() as db:
            query = select(V2Operation.operation_id).where(
                V2Operation.device_id == self.device_id,
                V2Operation.status.in_(statuses),
                V2Operation.reconciled == 0,
            )
            if exclude_stop:
                query = query.where(V2Operation.command != "stop_run")
            return list(await db.scalars(query.limit(limit)))

    async def refresh_operations(self):
        ids = await self._operation_ids(["accepted", "unknown", "sent", "pending"])
        for operation_id in ids:
            await self.operations.query(operation_id)

    def wire_operation_id(self, msg_id):
        return uuid.uuid5(uuid.UUID(hex=self.controller_epoch), msg_id).hex

    @staticmethod
    def lease_operation_id(parent_id):
        return uuid.uuid5(uuid.UUID(hex=parent_id), "acquire_lease").hex

    async def has_business_intent(self, msg_id):
        return bool(self.operations and await self.operations.get(self.wire_operation_id(msg_id)))

    async def preflight(self, command):
        if command == "stop_test":
            return
        if command not in {"start_test", "set_parameters", "ack_run", "ack_alarm", "reset_fault"}:
            fail("unsupported_v2_command", "该操作不在 HostComm 2 设备契约中", 422)
        if not self._ready:
            fail("device_recovery_pending", "设备会话或日志恢复尚未完成")
        await self.refresh_operations()
        unresolved = await self._operation_ids(
            ["pending", "sent", "accepted", "unknown", "result_expired", "not_found"], exclude_stop=True, limit=1
        )
        if unresolved:
            fail("operation_unresolved", "必须先查询并核查上一条设备操作")
        snapshot = await self.get_status()
        if self._status_frame["payload"]["lease_id"] and not self.transport.lease_id:
            if await self.operations.confirm_owned_lease(self._status_frame):
                snapshot = await self._publish()
        permission = {
            "start_test": "can_start_test",
            "set_parameters": "can_activate_recipe",
            "ack_run": "can_ack_run",
            "ack_alarm": "can_ack_alarm",
            "reset_fault": "can_reset_fault",
        }[command]
        if snapshot.get("system", {}).get(permission) is not True:
            fail("state_not_allowed", "当前设备状态、租约或安全条件不允许该操作")

    async def _ensure_lease(self, actor, role, parent_id):
        if not self._ready:
            fail("device_recovery_pending", "设备会话或日志恢复尚未完成")
        await self.refresh_operations()
        await self.get_status()
        status = self._status_frame["payload"]
        if status["lease_id"]:
            if (
                status["lease_owner_controller_id"] != self.controller_id
                or status["lease_owner_session_id"] != self.transport.session_id
            ):
                fail("device_control_owned", "设备控制租约属于其他会话")
            if not await self.operations.confirm_owned_lease(self._status_frame):
                fail("lease_unconfirmed", "设备租约缺少当前持久结果和有效回读证据")
        else:
            row = await self.operations.acquire_lease(
                actor=actor, role=role, operation_id=self.lease_operation_id(parent_id)
            )
            self._result(row)
            if not self.transport.lease_id:
                fail("lease_unconfirmed", "设备控制租约尚未回读确认")
        await self.get_status()
        if not self.transport.lease_evidence()["valid"]:
            fail("lease_unconfirmed", "设备控制租约已失效，必须重新核查")

    @staticmethod
    def _result(row):
        if row["status"] in {"unknown", "result_expired", "not_found", "sent", "pending"}:
            raise HostCommTimeoutError("设备执行结果未知；请查询操作记录，禁止重新发送")
        return {
            "result": "accepted" if row["status"] in {"accepted", "applied"} else "rejected",
            "reason_code": row["reason"],
            "wire_operation_id": row["operation_id"],
            "wire_status": row["status"],
            "command_seq": row["command_seq"],
        }

    async def send_command(self, command, params=None, *, operator_id, role, confirm_token=None, msg_id=None):
        if not self.is_online:
            raise HostCommNotConnectedError("HostComm 2 未连接")
        supported = {"start_test", "stop_test", "set_parameters", "ack_run", "ack_alarm", "reset_fault"}
        if command not in supported:
            fail("unsupported_v2_command", "该操作不在 HostComm 2 设备契约中", 422)
        if role not in {"operator", "maintainer", "admin"}:
            fail("permission_denied", "无设备控制权限", 403)
        if self.transport.hello_payload.get("granted_role") != "control":
            fail("device_diagnostic_only", "配对身份仅允许读取设备诊断", 403)
        operation_id = self.wire_operation_id(msg_id or uuid.uuid4().hex)
        existing = await self.operations.get(operation_id)
        if existing:
            # HTTP layer detects request-body conflicts before calling the adapter.
            return self._result(existing)
        try:
            if command == "stop_test":
                # Stop has its own slot and never waits for recipe/ordinary commands.
                frame = self._status_frame
                if (
                    not frame
                    or frame["session_id"] != self.transport.session_id
                    or frame["boot_id"] != self.transport.boot_id
                ):
                    await self.get_status()
                    frame = self._status_frame
                run_id = frame["payload"]["run"]["run_id"]
                if run_id is None:
                    fail("no_active_run", "设备没有可停止的运行身份")
                row = await self.operations.submit(
                    "stop_run",
                    {"run_id": run_id, "reason": "operator_stop"},
                    actor=operator_id,
                    role=role,
                    operation_id=operation_id,
                )
                return self._result(row)
            async with self._control_lock:
                existing = await self.operations.get(operation_id)
                if existing:
                    return self._result(existing)
                await self._ensure_lease(operator_id, role, operation_id)
                params = params or {}
                if command == "set_parameters":
                    return await self._activate(params, operator_id, role, operation_id)
                run = self._status_frame["payload"]["run"]
                if command == "start_test":
                    wire_params = await self._start_binding(params)
                    wire_command = "start_run"
                    run = self._status_frame["payload"]["run"]
                elif command == "ack_run":
                    wire_command, wire_params = "ack_run", {"run_id": run["run_id"]}
                elif command == "reset_fault":
                    wire_command, wire_params = "reset_fault", {
                        "fault_revision": run["fault_revision"],
                        "reason": params.get("reason") or "operator_reset",
                    }
                else:
                    wire_command, wire_params = "ack_alarm", {
                        key: params.get(key) for key in ("alarm_id", "occurrence_seq")
                    }
                row = await self.operations.submit(
                    wire_command,
                    wire_params,
                    actor=operator_id,
                    role=role,
                    operation_id=operation_id,
                    lease_id=self.transport.lease_id,
                    state_revision=run["state_revision"],
                )
                await self.get_status()
                return self._result(row)
        except V2OperationError as exc:
            fail(exc.code, str(exc))

    async def _start_binding(self, params):
        status = self._status_frame["payload"]
        if status["run"]["state"] != "idle" or status["run"]["run_id"] is not None:
            fail("device_not_idle", "设备尚未结束并确认上一实验")
        snapshot = await self.get_parameters()
        bundle = snapshot["params"].get("recipe")
        expected = params.get("expected_recipe") or {}
        if not bundle or any(bundle.get(key) != expected.get(key) for key in ("recipe_id", "version", "digest")):
            fail("active_recipe_mismatch", "设备可执行配方与启动快照不一致")
        compiled = compile_recipe(bundle, self.profile)
        if compiled.digest != status["active_recipe_digest"]:
            fail("active_recipe_mismatch", "设备配方摘要与本地编译版本不一致")
        return await self._persist_run_binding(params, compiled)

    @finish_db_work
    async def _persist_run_binding(self, params, compiled):
        async with self._write_lock, self.factory() as db, db.begin():
            row = await db.scalar(select(V2RunBinding).where(V2RunBinding.test_id == params["test_id"]))
            if row is None:
                row = V2RunBinding(
                    device_id=self.device_id,
                    run_id=uuid.uuid4().hex,
                    test_id=params["test_id"],
                    recipe_digest=compiled.digest,
                    profile_digest=self.profile["profile_digest"],
                    created_at=now_iso(),
                )
                db.add(row)
            elif row.device_id != self.device_id or row.recipe_digest != compiled.digest:
                fail("run_identity_conflict", "实验身份已绑定其他设备或配方")
            return {
                "run_id": row.run_id,
                "recipe_digest": row.recipe_digest,
                "safety_profile_digest": row.profile_digest,
            }

    async def _activate(self, params, actor, role, operation_id):
        if role not in {"admin", "maintainer"}:
            fail("permission_denied", "配方激活需要维护权限", 403)
        values = params.get("values")
        if not isinstance(values, dict) or set(values) != {"recipe"}:
            fail("v2_parameters_read_only", "HostComm 2 仅允许校验并激活完整配方；工程配置只读", 422)
        status = self._status_frame["payload"]
        if status["run"]["state"] != "idle" or status["run"]["run_id"] is not None:
            fail("device_not_idle", "设备须在无运行身份的待机状态激活配方")
        bundle = values["recipe"]
        try:
            compiled = compile_recipe(bundle, await self.get_profile())
        except (ValueError, KeyError) as exc:
            fail("recipe_not_executable", str(exc), 422)
        await self._persist_recipe_binding(bundle, compiled)
        transfer_id = uuid.uuid4().hex
        reply = (
            await self._request(
                "recipe_begin",
                {
                    "transfer_id": transfer_id,
                    "lease_id": self.transport.lease_id,
                    "recipe_digest": compiled.digest,
                    "byte_length": len(compiled.data),
                },
            )
        )["payload"]
        self._check_upload(reply, transfer_id, compiled.digest, 0, "receiving")
        offset = 0
        for encoded in split_chunks(compiled.data):
            size = len(decode_chunk(encoded))
            reply = (
                await self._request("recipe_chunk", {"transfer_id": transfer_id, "offset": offset, "data_b64": encoded})
            )["payload"]
            self._check_upload(
                reply,
                transfer_id,
                compiled.digest,
                offset + size,
                "validated" if offset + size == len(compiled.data) else "receiving",
            )
            offset += size
        await self.get_status()
        row = await self.operations.submit(
            "activate_recipe",
            {
                "transfer_id": transfer_id,
                "recipe_digest": compiled.digest,
                "expected_active_digest": status["active_recipe_digest"],
            },
            actor=actor,
            role=role,
            operation_id=operation_id,
            lease_id=self.transport.lease_id,
            state_revision=self._status_frame["payload"]["run"]["state_revision"],
        )
        result = self._result(row)
        if result["result"] == "accepted":
            readback = await self._read_recipe(compiled.digest)
            if canonical_bytes(readback) != compiled.data:
                raise HostCommProtocolError("配方回读内容不一致")
            await self.get_status()
            if self._status_frame["payload"]["active_recipe_digest"] != compiled.digest:
                fail("recipe_activation_unconfirmed", "配方已受理，但尚未确认激活")
        return result

    @finish_db_work
    async def _persist_recipe_binding(self, bundle, compiled):
        async with self._write_lock, self.factory() as db, db.begin():
            saved = await db.get(RecipeVersion, (bundle["recipe_id"], bundle["version"]))
            if saved is None or saved.digest != compiled.source_digest:
                fail("recipe_not_saved", "须先保存此配方版本")
            binding = await db.get(V2RecipeBinding, (self.device_id, compiled.digest))
            if binding is None:
                db.add(
                    V2RecipeBinding(
                        device_id=self.device_id,
                        recipe_digest=compiled.digest,
                        recipe_id=bundle["recipe_id"],
                        recipe_version=bundle["version"],
                        source_digest=compiled.source_digest,
                        recipe_json=compiled.data.decode(),
                        created_at=now_iso(),
                    )
                )

    @staticmethod
    def _check_upload(reply, transfer_id, recipe_digest, offset, status):
        if any(
            reply.get(key) != value
            for key, value in {
                "transfer_id": transfer_id,
                "recipe_digest": recipe_digest,
                "next_offset": offset,
                "status": status,
            }.items()
        ):
            fail("recipe_upload_rejected", "设备未确认配方分块或完整校验")

    async def _read_recipe(self, recipe_digest):
        data = bytearray()
        size = None
        while size is None or len(data) < size:
            p = (await self._request("get_recipe", {"recipe_digest": recipe_digest, "offset": len(data)}))["payload"]
            if (
                p["recipe_digest"] != recipe_digest
                or p["offset"] != len(data)
                or (size is not None and size != p["byte_length"])
            ):
                raise HostCommProtocolError("配方回读分块身份不一致")
            size = p["byte_length"]
            chunk = decode_chunk(p["data_b64"])
            if not chunk or len(data) + len(chunk) > size:
                raise HostCommProtocolError("配方回读长度无效")
            data.extend(chunk)
        recipe = validate_recipe_bytes(bytes(data)).model_dump()
        if digest(recipe) != recipe_digest:
            raise HostCommProtocolError("配方回读摘要不一致")
        return recipe

    async def get_parameters(self):
        from app.services.command_service import compute_param_crc

        await self.get_status()
        await self.get_profile()
        active = self._status_frame["payload"]["active_recipe_digest"]
        wire = await self._read_recipe(active) if active else None
        values = await self._bound_recipe(active, wire) if wire else {}
        return {
            "params": values,
            "parameter_crc": compute_param_crc(values),
            "safety_profile": self.profile,
            "wire_recipe": wire,
            "active_recipe_digest": active,
            "protocol_version": "2.0",
            "fw_version": self.transport.hello_payload["fw_version"],
        }

    @finish_db_work
    async def _bound_recipe(self, active, wire):
        async with self.factory() as db:
            binding = await db.get(V2RecipeBinding, (self.device_id, active))
            if binding and canonical_bytes(wire).decode() == binding.recipe_json:
                row = await db.get(RecipeVersion, (binding.recipe_id, binding.recipe_version))
                if row and row.digest == binding.source_digest:
                    return {
                        "recipe": {
                            "recipe_id": row.recipe_id,
                            "version": row.version,
                            "digest": row.digest,
                            "definition": json.loads(row.definition_json),
                        }
                    }
            return {}

    @finish_db_work
    async def _log_start(self, log_id):
        async with self.factory() as db:
            cursor = await db.get(V2LogCursor, (self.device_id, log_id))
            scanned = getattr(cursor, "scanned_through_seq", None) if cursor else None
            return int(scanned) + 1 if scanned else 1

    async def recover_logs(self, *, first_record_seq=None):
        async with self._log_lock:
            await self.get_status()
            session_id, boot_id = self._status_frame["session_id"], self._status_frame["boot_id"]
            catalog = self._status_frame["payload"]["log"]
            if catalog["newest_record_seq"] is None:
                return
            first = await self._log_start(catalog["log_id"])
            if first_record_seq is not None:
                from pydantic import TypeAdapter

                from app.hostcomm.v2_contract.types import U64

                first = max(1, int(TypeAdapter(U64).validate_python(first_record_seq)))
            last = int(catalog["newest_record_seq"])
            while first <= last:
                admission = await self.transport.wait_for_callbacks()
                if (admission.session_id, admission.boot_id) != (session_id, boot_id):
                    raise V2OfflineError("Source log catalog belongs to a previous connection")
                stop = min(last, first + 999)
                request_id = uuid.uuid4().hex
                query = {
                    "transfer_id": uuid.uuid4().hex,
                    "requested": {
                        "log_id": catalog["log_id"],
                        "first_record_seq": str(first),
                        "last_record_seq": str(stop),
                    },
                    "max_records": stop - first + 1,
                    "max_bytes": 16777216,
                }
                await self.logs.begin(
                    query,
                    request_msg_id=request_id,
                    session_id=admission.session_id,
                    boot_id=admission.boot_id,
                )
                self.transport.check_callback_context(admission)
                result = await self._request("log_request", query, msg_id=request_id, timeout=30)
                await self.logs.finish(result)
                first = stop + 1
