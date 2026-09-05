"""升级门禁必须与命令互斥，并在后端重启后保持关闭。"""

import asyncio

import pytest

from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager


async def safe():
    return None


async def test_prepare_waits_for_command_and_blocks_next_command(tmp_path):
    manager = MaintenanceManager()
    manager.configure_upgrade(tmp_path / "maintenance.json")
    held, release = asyncio.Event(), asyncio.Event()

    async def command():
        async with manager.command_guard():
            held.set()
            await release.wait()

    task = asyncio.create_task(command())
    await held.wait()
    preparing = asyncio.create_task(
        manager.prepare_upgrade(
            target_version="0.4.0-rc.1",
            current_version="0.3.0-rc.1",
            db_path=tmp_path / "actual.db",
            operator_id="admin",
            validate=safe,
        )
    )
    await asyncio.sleep(0)
    assert not preparing.done()
    release.set()
    await task
    state = await preparing
    assert state["state"] == "prepared"
    with pytest.raises(MaintenanceBlockedError):
        async with manager.command_guard():
            pytest.fail("command must not run during maintenance")


async def test_restart_preserves_gate_and_claim_requires_fresh_validation(tmp_path):
    state_path = tmp_path / "maintenance.json"
    manager = MaintenanceManager()
    manager.configure_upgrade(state_path)
    state = await manager.prepare_upgrade(
        target_version="0.4.0",
        current_version="0.3.0",
        db_path=tmp_path / "actual.db",
        operator_id="admin",
        validate=safe,
    )
    restarted = MaintenanceManager()
    restarted.configure_upgrade(state_path)
    with pytest.raises(MaintenanceBlockedError):
        async with restarted.command_guard():
            pass

    async def stale():
        raise MaintenanceBlockedError("设备状态已过期")

    with pytest.raises(MaintenanceBlockedError, match="过期"):
        await restarted.claim_upgrade(state["token"], validate=stale)
    assert restarted.upgrade_state()["state"] == "prepared"
    with pytest.raises(MaintenanceBlockedError):
        await restarted.claim_upgrade("wrong-token", validate=safe)
    claimed = await restarted.claim_upgrade(state["token"], validate=safe)
    assert claimed["db_path"] == str((tmp_path / "actual.db").resolve())
    assert claimed["state"] == "claimed"
    with pytest.raises(MaintenanceBlockedError):
        await restarted.cancel_upgrade()


async def test_invalid_state_fails_closed_and_cancel_prepared_reopens(tmp_path):
    manager = MaintenanceManager()
    path = tmp_path / "maintenance.json"
    manager.configure_upgrade(path)
    await manager.prepare_upgrade(
        target_version="0.4.0",
        current_version="0.3.0",
        db_path=tmp_path / "actual.db",
        operator_id="admin",
        validate=safe,
    )
    await manager.cancel_upgrade()
    async with manager.command_guard():
        pass
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(MaintenanceBlockedError):
        async with manager.command_guard():
            pass
