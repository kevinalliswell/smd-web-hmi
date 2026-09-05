"""不同进程对同一设备只能有一个网关；所有测试只使用自有临时目录。"""

import multiprocessing
import os
import uuid

import pytest

from app.services.gateway_lease import GatewayLease, GatewayLeaseError, endpoint_identity


def hold_gateway(root, port, ready, release):
    try:
        with GatewayLease("192.0.2.40", port, lock_root=root):
            ready.send("acquired")
            release.wait(15)
    except Exception as exc:
        ready.send(type(exc).__name__ + ":" + str(exc))
    finally:
        ready.close()


def test_process_lease_blocks_second_instance_and_releases_after_process_death(tmp_path):
    context = multiprocessing.get_context("spawn")
    port = 1024 + int(uuid.uuid4().hex[:4], 16) % 64000
    receive, send = context.Pipe(duplex=False)
    release = context.Event()
    first = context.Process(target=hold_gateway, args=(str(tmp_path), port, send, release))
    first.start()
    try:
        assert receive.poll(10)
        assert receive.recv() == "acquired"
        with pytest.raises(GatewayLeaseError, match="占用"):
            GatewayLease("::ffff:192.0.2.40", port, lock_root=tmp_path).acquire()
        first.terminate()
        first.join(10)
        assert not first.is_alive()
        with GatewayLease("192.0.2.40", port, lock_root=tmp_path):
            pass
    finally:
        if first.is_alive():
            first.terminate()
            first.join(10)
        receive.close()
        send.close()


def test_endpoint_identity_has_no_data_path_and_normalizes_addresses():
    assert endpoint_identity(" 192.0.2.4 ", 34211) == endpoint_identity("[::ffff:192.0.2.4]", 34211)
    assert endpoint_identity("2001:0db8::1", 34211) == endpoint_identity("2001:db8:0:0:0:0:0:1", 34211)
    with pytest.raises(GatewayLeaseError, match="字面"):
        endpoint_identity("device.example", 34211)


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem lock security")
def test_symlink_lock_file_is_never_followed(tmp_path):
    lease = GatewayLease("192.0.2.40", 34211, lock_root=tmp_path)
    victim = tmp_path / "unrelated"
    victim.write_text("unchanged")
    lease.lock_path.symlink_to(victim)
    with pytest.raises(GatewayLeaseError):
        lease.acquire()
    assert victim.read_text() == "unchanged"


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem lock security")
def test_lock_file_remains_after_release_to_preserve_inode_identity(tmp_path):
    lease = GatewayLease("192.0.2.40", 34211, lock_root=tmp_path)
    with lease:
        inode = lease.lock_path.stat().st_ino
    assert lease.lock_path.stat().st_ino == inode
    with GatewayLease("192.0.2.40", 34211, lock_root=tmp_path):
        assert lease.lock_path.stat().st_ino == inode


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem lock security")
def test_failed_shared_file_permission_setup_closes_descriptor(tmp_path, monkeypatch):
    opened = []

    def fail_permission_setup(descriptor, mode):
        opened.append(descriptor)
        raise OSError("permission setup failed")

    monkeypatch.setattr(os, "fchmod", fail_permission_setup)
    with pytest.raises(GatewayLeaseError, match="permission setup failed"):
        GatewayLease("192.0.2.41", 34211, lock_root=tmp_path).acquire()
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_same_process_cannot_use_windows_mutex_recursion_to_open_two_gateways(tmp_path):
    with GatewayLease("192.0.2.99", 34211, lock_root=tmp_path):
        with pytest.raises(GatewayLeaseError, match="占用"):
            GatewayLease("192.0.2.99", 34211, lock_root=tmp_path).acquire()


async def test_production_lifespan_acquires_lease_before_database_or_connection(monkeypatch):
    from types import SimpleNamespace

    from app import main
    from app.core.config import Settings

    events = []

    class Lease:
        def __init__(self, *args):
            pass

        def __enter__(self):
            events.append("lease_acquired")

        def __exit__(self, *args):
            events.append("lease_released")

    async def database_gate():
        events.append("database_gate")
        raise RuntimeError("controlled startup failure")

    async def noop():
        pass

    settings = Settings(_env_file=None, hostcomm_mock=False, smd_jwt_secret="test-secret-at-least-32-bytes-long")
    monkeypatch.setattr(main, "GatewayLease", Lease, raising=False)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "assert_schema_current", database_gate)
    monkeypatch.setattr(main.maintenance_manager, "stop", noop)
    monkeypatch.setattr(main.background_jobs, "shutdown", noop)
    monkeypatch.setattr(main, "dispose_engine", noop)
    with pytest.raises(RuntimeError, match="controlled"):
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            raise AssertionError("must not start")
    assert events == ["lease_acquired", "database_gate", "lease_released"]
