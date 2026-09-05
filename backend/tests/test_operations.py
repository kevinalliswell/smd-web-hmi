"""持久命令事务：请求先落库、重复不重发、不确定结果可追踪。"""

import json

import pytest
from sqlalchemy import select

from app.db.operation_models import Operation
from app.hostcomm.client import HostCommTimeoutError
from app.services.cache import StatusCache
from app.services.command_service import CommandService, compute_param_crc
from app.services.operations import OperationError, recover_interrupted_operations


class Board:
    is_online = True

    def __init__(self, db, timeout=False):
        self.db = db
        self.timeout = timeout
        self.sent = []

    async def send_command(self, command, params, **kwargs):
        async with self.db.bind.connect() as connection:
            status = await connection.scalar(select(Operation.status).where(Operation.msg_id == kwargs["msg_id"]))
        assert status == "sent", "发送前另一数据库连接必须已经读到持久记录"
        self.sent.append(kwargs["msg_id"])
        if self.timeout:
            raise HostCommTimeoutError("lost ack")
        return {"result": "accepted", "request_msg_id": kwargs["msg_id"], "command": command}


async def ready_cache():
    cache = StatusCache()
    await cache.update({"state_machine": {"current_state": "Standby"}})
    return cache


async def test_same_operation_returns_stored_ack_without_sending_again(db_session):
    board = Board(db_session)
    service = CommandService(board, await ready_cache())
    args = dict(operator_id="a", role="operator", db_session=db_session, operation_id="op-1")
    first = await service.execute("tare_balance", {}, **args)
    second = await service.execute("tare_balance", {}, **args)
    assert first["operation_id"] == second["operation_id"] == "op-1"
    assert first["result"] == second["result"] == "accepted"
    assert len(board.sent) == 1
    row = await db_session.get(Operation, "op-1")
    assert row.status == "accepted"
    assert json.loads(row.params_json) == {}


async def test_timeout_is_unknown_and_retry_never_sends(db_session):
    board = Board(db_session, timeout=True)
    service = CommandService(board, await ready_cache())
    args = dict(operator_id="a", role="operator", db_session=db_session, operation_id="op-unknown")
    with pytest.raises(HostCommTimeoutError):
        await service.execute("tare_balance", {}, **args)
    assert (await db_session.get(Operation, "op-unknown")).status == "unknown"
    repeated = await service.execute("tare_balance", {}, **args)
    assert repeated["operation_status"] == "unknown"
    assert repeated["result"] == "unknown"
    assert len(board.sent) == 1


async def test_operation_id_cannot_change_actor_or_payload(db_session):
    board = Board(db_session)
    service = CommandService(board, await ready_cache())
    await service.execute(
        "tare_balance", {}, operator_id="a", role="operator", db_session=db_session, operation_id="op-x"
    )
    for actor, params in [("b", {}), ("a", {"changed": True})]:
        with pytest.raises(OperationError) as error:
            await service.execute(
                "tare_balance", params, operator_id=actor, role="operator", db_session=db_session, operation_id="op-x"
            )
        assert error.value.status_code == 409
    assert len(board.sent) == 1


async def test_startup_marks_interrupted_sending_unknown_without_replay(db_session):
    db_session.add(
        Operation(
            operation_id="crashed",
            msg_id="pc-cmd-crashed",
            command="tare_balance",
            operator_id="a",
            operator_role="operator",
            request_hash="h",
            params_json="{}",
            status="sent",
            created_at="2026-09-05T00:00:00+00:00",
            updated_at="2026-09-05T00:00:00+00:00",
        )
    )
    await db_session.commit()
    assert await recover_interrupted_operations(db_session) == 1
    assert (await db_session.get(Operation, "crashed")).status == "unknown"


async def test_parameter_entry_points_share_idempotency(db_session):
    from app.services.parameter_service import ParameterService

    class ParameterBoard(Board):
        async def get_parameters(self):
            return {"params": {"process": {"end_temp_deg_c": 1580}}, "parameter_crc": "device"}

    board, cache = ParameterBoard(db_session), await ready_cache()
    values = {"process": {"end_temp_deg_c": 1580}}
    crc = compute_param_crc(values)
    args = dict(operator_id="a", role="admin", db_session=db_session, operation_id="parameter-op")
    first = await ParameterService(board, cache).set_parameters(values, crc, **args)
    second = await CommandService(board, cache).execute("set_parameters", {"values": values, "param_crc": crc}, **args)
    assert first["readback_ok"] is second["readback_ok"] is True
    assert first["operation_status"] == second["operation_status"] == "verified"
    assert len(board.sent) == 1


async def test_maintenance_gate_prevents_command_and_parameter_writes(db_session, monkeypatch, tmp_path):
    from app.services import command_service, parameter_service
    from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager

    gate = MaintenanceManager()
    gate.configure_upgrade(tmp_path / "maintenance.json")

    async def validate():
        pass

    await gate.prepare_upgrade(
        target_version="1.0.0", current_version="0.3.0", db_path=tmp_path / "db", operator_id="a", validate=validate
    )
    monkeypatch.setattr(command_service, "maintenance_manager", gate)
    monkeypatch.setattr(parameter_service, "maintenance_manager", gate)
    board, cache = Board(db_session), await ready_cache()
    for command, params in [
        ("tare_balance", {}),
        ("set_parameters", {"values": {}, "param_crc": compute_param_crc({})}),
    ]:
        with pytest.raises(MaintenanceBlockedError):
            await CommandService(board, cache).execute(
                command, params, operator_id="a", role="admin", db_session=db_session
            )
    assert board.sent == []


async def test_concurrent_duplicate_requests_send_once(db_session, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services import command_service
    from app.services.maintenance_service import MaintenanceManager

    monkeypatch.setattr(command_service, "maintenance_manager", MaintenanceManager())
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    board, cache = Board(db_session), await ready_cache()

    async def invoke():
        async with sessions() as db:
            return await CommandService(board, cache).execute(
                "tare_balance", {}, operator_id="a", role="operator", db_session=db, operation_id="concurrent"
            )

    results = await asyncio.gather(invoke(), invoke())
    assert all(result["operation_status"] == "accepted" for result in results)
    assert len(board.sent) == 1


async def test_patch_parameters_preserves_other_fields_and_does_not_reread_on_retry(db_session):
    from app.services.parameter_service import ParameterService

    class PatchBoard(Board):
        values = {"process": {"end_temp_deg_c": 1580}, "gas": {"n2": 5}}
        reads = 0

        async def get_parameters(self):
            self.reads += 1
            return {"params": self.values}

        async def send_command(self, command, params, **kwargs):
            result = await super().send_command(command, params, **kwargs)
            self.values = params["values"]
            return result

    board = PatchBoard(db_session)
    service = ParameterService(board, await ready_cache())
    args = dict(operator_id="a", role="admin", db_session=db_session, operation_id="patch-1")
    first = await service.patch_parameters({"recipe": {"recipe_id": "std-1"}}, **args)
    second = await service.patch_parameters({"recipe": {"recipe_id": "std-1"}}, **args)
    assert first["params"] == {"recipe": {"recipe_id": "std-1"}, "process": {"end_temp_deg_c": 1580}, "gas": {"n2": 5}}
    assert second["operation_status"] == "verified"
    assert board.reads == 2
    assert len(board.sent) == 1


async def test_device_timeout_result_is_unknown_not_rejected(db_session):
    class TimeoutBoard(Board):
        async def send_command(self, command, params, **kwargs):
            result = await super().send_command(command, params, **kwargs)
            return {**result, "result": "timeout"}

    result = await CommandService(TimeoutBoard(db_session), await ready_cache()).execute(
        "tare_balance", {}, operator_id="a", role="operator", db_session=db_session, operation_id="device-timeout"
    )
    assert result["operation_status"] == "unknown"
    assert result["device_result"]["result"] == "timeout"
