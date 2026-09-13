"""The installer authorizes local maintenance without changing experiment evidence."""

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import __version__
from app.db.models import TestSession
from app.services.local_maintenance import LocalMaintenanceBridge, assess_maintenance
from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager


def intent(**values):
    return {
        "schema_version": 1,
        "transaction_id": "a" * 32,
        "operation": "upgrade",
        "current_version": __version__,
        "target_version": "0.4.0",
        "package_sha256": "b" * 64,
        "admin_sid": "S-1-5-21-1-2-3-1001",
        "physical_shutdown_confirmed": False,
        "created_at": time.time(),
        **values,
    }


def client_for(state="idle", online=True):
    return SimpleNamespace(
        protocol_version="2.0",
        device_id="1" * 32,
        is_online=online,
        get_status=AsyncMock(),
        _status_frame={"payload": {"run": {"state": state, "run_id": None if state == "idle" else "2" * 32}}},
    )


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.local_maintenance.verify_request_acl", lambda path: None)
    manager = MaintenanceManager()
    manager.configure_upgrade(tmp_path / "maintenance.json")
    result = LocalMaintenanceBridge(
        manager, tmp_path / "maintenance.json", tmp_path / "actual.db", __version__, None, None
    )
    result.assess = AsyncMock(return_value=("idle", "设备已确认待机"))
    return result


def write_request(bridge, value):
    bridge.request_path.write_text(json.dumps(value), encoding="utf-8")


async def test_authorization_binds_request_gate_and_both_command_channels(bridge):
    request = intent()
    write_request(bridge, request)
    reply = await bridge.process_once()
    assert reply["state"] == "authorized"
    gate = bridge.manager.upgrade_state()
    expected = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert reply["request_sha256"] == gate["request_sha256"] == expected
    assert gate["issuer"] == "windows_installer" and gate["schema_version"] == 2
    assert gate["upgrade_id"] == request["transaction_id"] and gate["db_path"].endswith("actual.db")
    for priority in (False, True):
        with pytest.raises(MaintenanceBlockedError):
            async with bridge.manager.command_guard(priority=priority):
                pytest.fail("authorized maintenance must block every new device write")


@pytest.mark.parametrize(
    "assessment,confirmed,expected",
    [
        ("busy", False, "blocked"),
        ("busy", True, "blocked"),
        ("unknown", False, "confirmation_required"),
        ("unknown", True, "authorized"),
        ("idle", False, "authorized"),
    ],
)
async def test_busy_cannot_be_overridden_but_unknown_requires_physical_confirmation(
    bridge, assessment, confirmed, expected
):
    bridge.assess.return_value = (assessment, "检查结果")
    write_request(bridge, intent(physical_shutdown_confirmed=confirmed))
    assert (await bridge.process_once())["state"] == expected
    assert (bridge.manager.upgrade_state()["state"] != "idle") == (expected == "authorized")


async def test_waits_for_both_inflight_channels_before_authorizing(bridge):
    write_request(bridge, intent())
    async with bridge.manager.command_guard(priority=True):
        task = asyncio.create_task(bridge.process_once())
        await asyncio.sleep(0)
        assert not task.done()
        bridge.assess.assert_not_awaited()
    assert (await task)["state"] == "authorized"


async def test_completed_request_never_recreates_a_removed_gate(bridge):
    write_request(bridge, intent())
    first = await bridge.process_once()
    assert await bridge.process_once() == first
    bridge.manager._upgrade_path.unlink()
    assert (await bridge.process_once())["state"] == "rejected"
    assert bridge.manager.upgrade_state()["state"] == "idle"
    assert bridge.assess.await_count == 1


async def test_same_id_different_request_and_new_request_with_existing_gate_are_rejected(bridge):
    original = intent()
    write_request(bridge, original)
    await bridge.process_once()
    gate = bridge.manager.upgrade_state()
    write_request(bridge, {**original, "target_version": "0.5.0"})
    assert (await bridge.process_once())["state"] == "rejected"
    write_request(bridge, intent(transaction_id="c" * 32))
    assert (await bridge.process_once())["state"] == "blocked"
    assert bridge.manager.upgrade_state() == gate


@pytest.mark.parametrize(
    "values",
    [
        {"created_at": 0},
        {"created_at": float("nan")},
        {"created_at": time.time() + 3600},
        {"current_version": "0.0.0"},
        {"admin_sid": "admin"},
        {"package_sha256": "short"},
        {"physical_shutdown_confirmed": "true"},
        {"unexpected": "field"},
        {"schema_version": True},
    ],
)
async def test_invalid_expired_or_wrong_version_intents_do_not_authorize(bridge, values):
    write_request(bridge, intent(**values))
    reply = await bridge.process_once()
    assert reply is None or reply["state"] == "rejected"
    assert bridge.manager.upgrade_state()["state"] == "idle"
    bridge.assess.assert_not_awaited()


async def test_unknown_open_archive_is_preserved_and_current_busy_is_authoritative(db_session):
    record = TestSession(test_id="UNKNOWN", operator_id="admin", phase="needs_review", start_time=None, end_time=None)
    db_session.add(record)
    await db_session.commit()
    assert (await assess_maintenance(client_for(online=False), db_session))[0] == "unknown"
    assert (await assess_maintenance(client_for("cooling"), db_session))[0] == "busy"
    assert (await assess_maintenance(client_for(), db_session))[0] == "unknown"
    await db_session.refresh(record)
    assert record.end_time is None and record.start_time is None and record.phase == "needs_review"


async def test_missing_request_is_noop_and_reparse_request_is_rejected(bridge, tmp_path):
    assert await bridge.process_once() is None
    other = tmp_path / "user.json"
    other.write_text(json.dumps(intent()), encoding="utf-8")
    try:
        bridge.request_path.symlink_to(other)
    except OSError:
        pytest.skip("symlink creation requires Windows developer mode or privilege")
    assert await bridge.process_once() is None
    bridge.assess.assert_not_awaited()


async def test_gate_survives_reply_write_crash_without_reassessing_or_unlocking(bridge, monkeypatch):
    from app.services import local_maintenance as local

    write_request(bridge, intent())
    writer = local.write_protected_json

    def fail_reply(path, value):
        if path == bridge.reply_path:
            raise OSError("simulated disk error during reply")
        writer(path, value)

    monkeypatch.setattr(local, "write_protected_json", fail_reply)
    with pytest.raises(OSError):
        await bridge.process_once()
    assert bridge.manager.upgrade_state()["state"] == "claimed"
    monkeypatch.setattr(local, "write_protected_json", writer)
    assert (await bridge.process_once())["state"] == "authorized"
    assert bridge.assess.await_count == 1


async def test_transport_timeout_is_unknown_and_does_not_touch_archive(db_session):
    from app.hostcomm.client import HostCommTimeoutError

    client = client_for()
    client.get_status.side_effect = HostCommTimeoutError()
    assert (await assess_maintenance(client, db_session))[0] == "unknown"


async def test_bridge_loop_is_single_and_closes_cleanly(bridge):
    await bridge.start()
    task = bridge._task
    await bridge.start()
    assert bridge._task is task
    await bridge.close()
    assert task.done() and bridge._task is None


@pytest.mark.parametrize("age,revision,minimum", [(6, 2, 2), (0, 1, 2)])
async def test_stale_status_never_counts_as_idle(db_session, age, revision, minimum):
    client = client_for()
    client._status_frame.update(session_id="session", boot_id="boot", msg_id="message")
    client._status_frame["payload"]["run"]["state_revision"] = str(revision)
    client.transport = SimpleNamespace(
        session_id="session",
        boot_id="boot",
        leases=SimpleNamespace(minimum_revision=minimum),
        receipt_metadata=lambda msg_id: {"received_monotonic": time.monotonic() - age},
    )
    assert (await assess_maintenance(client, db_session))[0] == "unknown"


@pytest.mark.parametrize(
    "owner,protected,rights,parent",
    [
        ("S-1-5-21-1-2-3-1000", True, [], False),
        ("S-1-5-32-544", False, [], False),
        ("S-1-5-32-544", True, [(0x2, "S-1-5-32-545")], False),
        ("S-1-5-32-544", True, [(0x10000, "S-1-5-80-1")], False),
        ("S-1-5-32-544", True, [(0x40000, "S-1-5-80-1")], False),
        ("S-1-5-32-544", True, [(0x80000, "S-1-5-80-1")], False),
        ("S-1-5-32-544", True, [(0x40, "S-1-5-80-1")], True),
        ("S-1-5-32-544", True, [(0x10000000, "S-1-1-0")], True),
    ],
)
def test_request_permissions_reject_write_or_parent_replace_paths(owner, protected, rights, parent):
    from app.services.local_maintenance import validate_request_permissions

    with pytest.raises(ValueError):
        validate_request_permissions(owner, protected, rights, parent=parent)


def test_request_permissions_allow_admin_writer_service_reader_and_no_parent_delete_child():
    from app.services.local_maintenance import validate_request_permissions

    allowed = [(0x1F01FF, "S-1-5-32-544"), (0x1F01FF, "S-1-5-18"), (0x120089, "S-1-5-80-1")]
    validate_request_permissions("S-1-5-32-544", True, allowed)
    validate_request_permissions("S-1-5-18", True, [*allowed, (0x1301BF, "S-1-5-80-1")], parent=True)


async def test_request_with_untrusted_acl_never_authorizes(bridge, monkeypatch):
    write_request(bridge, intent())

    def denied(path):
        raise ValueError("untrusted ownership")

    monkeypatch.setattr("app.services.local_maintenance.verify_request_acl", denied)
    assert await bridge.process_once() is None
    assert bridge.manager.upgrade_state()["state"] == "idle"
    bridge.assess.assert_not_awaited()


@pytest.mark.skipif(__import__("os").name != "nt", reason="requires actual Windows ACL enforcement")
def test_windows_request_acl_reads_native_owner_and_rejects_user_write(tmp_path):
    import ctypes

    import win32security as security

    from app.services.local_maintenance import verify_request_acl

    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("native administrator ownership test requires elevation")
    directory = tmp_path / "protected-maintenance"
    directory.mkdir()
    path = directory / "maintenance-request.json"
    path.write_text("{}", encoding="utf-8")

    def protect(target, extra=""):
        descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)" + extra, security.SDDL_REVISION_1
        )
        security.SetNamedSecurityInfo(
            str(target),
            security.SE_FILE_OBJECT,
            security.OWNER_SECURITY_INFORMATION
            | security.DACL_SECURITY_INFORMATION
            | security.PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor.GetSecurityDescriptorOwner(),
            None,
            descriptor.GetSecurityDescriptorDacl(),
            None,
        )

    protect(directory, "(A;;FR;;;BU)")
    protect(path, "(A;;FR;;;BU)")
    verify_request_acl(path)
    protect(path, "(A;;FW;;;BU)")
    with pytest.raises(ValueError, match="non-administrator"):
        verify_request_acl(path)
    protect(path)
    protect(directory, "(A;;DC;;;BU)")
    with pytest.raises(ValueError, match="non-administrator"):
        verify_request_acl(path)
