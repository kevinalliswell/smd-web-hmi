"""End-to-end application evidence over real PSK TLS; all devices/keys are synthetic."""

import os
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from tests.v2_test_support import private_test_directory


@pytest.mark.skipif(not getattr(ssl, "HAS_PSK", False), reason="TLS-PSK requires Python 3.13/OpenSSL")
@pytest.mark.parametrize("disconnect_upload", [False, True])
def test_real_tls_recipe_run_stop_cooling_and_source_log_recovery(tmp_path, disconnect_upload):
    # OPENSSL_CONF must be set before ssl/OpenSSL initializes in a fresh process.
    from app.hostcomm.v2_security import OPENSSL_AES128_POLICY

    directory = private_test_directory(tmp_path)
    policy = directory / "openssl.cnf"
    policy.write_text(OPENSSL_AES128_POLICY, encoding="ascii")
    key = directory / "synthetic-pairing.psk"
    key.write_text("37" * 32 + "\n", encoding="ascii")
    key.chmod(0o600)
    environment = {**os.environ, "OPENSSL_CONF": str(policy)}
    # Run from backend and explicitly make app importable even when pytest was
    # invoked from the repository root or through a Windows workflow.
    backend = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(backend)
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child", str(directory), str(int(disconnect_upload))],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "TLS_APPLICATION_COMPLETE" in completed.stdout
    assert key.read_text(encoding="ascii").strip() not in completed.stdout + completed.stderr


async def _application_scenario(directory, disconnect_upload=False):
    import asyncio
    import json
    import uuid

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base, SamplePoint, TestSession
    from app.db.recipe_models import RecipeVersion
    from app.db.v2_models import V2LogCursor, V2LogTransfer, V2SourceRecord
    from app.hostcomm.v2_client import V2Client
    from app.hostcomm.v2_security import verify_tls
    from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile
    from app.services.recipe_definition import standard_template

    key = directory / "synthetic-pairing.psk"
    board = V2Simulator(
        directory / "inert-board.sqlite",
        psk_file=key,
        profile=synthetic_profile(approved=True),  # Only this inert simulator's fixture approval.
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'host.sqlite'}")
    client = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await board.start()
        p = board.pairing
        client = V2Client(
            *board.address,
            factory=factory,
            device_id=p.device_id,
            controller_id=p.controller_id,
            controller_epoch=p.controller_epoch,
            psk_file=str(key),
            mock=False,
            client_version="tls-application-test",
        )
        await client.start()
        async with asyncio.timeout(6):
            while not (client.is_online and client._ready):
                await asyncio.sleep(0.01)
        assert not client.mock and not board.test_plaintext
        verify_tls(client.transport._writer)
        assert client.transport._writer.get_extra_info("ssl_object").cipher()[0] == "TLS_AES_128_GCM_SHA256"
        assert client.transport.lease_id is None  # Reading/recovery never claims device control.

        definition = standard_template()
        recipe = dict(
            recipe_id=uuid.uuid4().hex, version=1, digest=definition.digest(), definition=definition.model_dump()
        )
        test_id = "TLS-INERT-RUN-1"
        async with factory() as db, db.begin():
            db.add(
                RecipeVersion(
                    recipe_id=recipe["recipe_id"],
                    version=1,
                    digest=recipe["digest"],
                    definition_json=json.dumps(recipe["definition"]),
                    created_at="2026-09-06T00:00:00Z",
                    created_by="admin",
                )
            )
            db.add(
                TestSession(
                    test_id=test_id, operator_id="admin", start_time="2026-09-06T00:00:00Z", original_height_mm=20.0
                )
            )
        if disconnect_upload:
            from app.hostcomm.client import HostCommNotConnectedError

            original_dispatch = board._dispatch
            dropped = False
            prior_session = client.transport.session_id

            def close_during_upload(session, message):
                nonlocal dropped
                if message["type"] == "recipe_chunk" and not dropped:
                    dropped = True
                    session.writer.close()
                    return
                return original_dispatch(session, message)

            board._dispatch = close_during_upload
            with pytest.raises(HostCommNotConnectedError):
                await client.send_command(
                    "set_parameters",
                    {"values": {"recipe": recipe}},
                    operator_id="admin",
                    role="admin",
                    msg_id="tls-interrupted-upload",
                )
            assert dropped
            async with asyncio.timeout(10):
                while not (client.is_online and client._ready and client.transport.session_id != prior_session):
                    if client.transport._main_task.done():
                        raise RuntimeError(
                            "TLS connection manager stopped"
                        ) from client.transport._main_task.exception()
                    await asyncio.sleep(0.01)
            assert client._status_frame["payload"]["active_recipe_digest"] is None

        activated = await client.send_command(
            "set_parameters", {"values": {"recipe": recipe}}, operator_id="admin", role="admin", msg_id="tls-activate"
        )
        assert activated["wire_status"] == "applied"
        assert (await client.get_parameters())["params"]["recipe"] == recipe
        started = await client.send_command(
            "start_test",
            {"test_id": test_id, "expected_recipe": recipe},
            operator_id="admin",
            role="admin",
            msg_id="tls-start",
        )
        assert started["wire_status"] == "accepted"
        await board.tick()
        run = (await client.get_status())["state_machine"]
        assert run["current_state"] == "measuring" and run["measurement_start"]
        start_boundary = run["measurement_start"]
        queried = await client.operations.query(started["wire_operation_id"])
        assert queried["status"] == "applied"
        board.set_measurements(burden_mc=300000)  # Cooling cannot complete until the later synthetic safe reading.
        stopped = await client.send_command("stop_test", operator_id="admin", role="admin", msg_id="tls-stop")
        assert stopped["wire_status"] == "applied"
        after_stop = (await client.get_status())["state_machine"]
        assert after_stop["current_state"] == "safe_disposal" and after_stop["outcome"] == "aborted"
        assert not after_stop["measurement_complete"] and not after_stop["safe_complete"]
        await board.tick()
        await board.complete_purge()
        await board.complete_cooling()
        terminal = (await client.get_status())["state_machine"]
        assert terminal["current_state"] == "completed" and terminal["safe_complete"]
        assert terminal["measurement_start"] == start_boundary
        assert int(terminal["safe_boundary"]["sample_seq"]) > int(start_boundary["sample_seq"])

        await client.recover_logs()
        catalog = client._status_frame["payload"]["log"]
        async with factory() as db:
            cursor = await db.get(V2LogCursor, (p.device_id, catalog["log_id"]))
            assert cursor.verified_through_seq == catalog["newest_record_seq"]
            assert (
                await db.scalar(
                    select(func.count()).select_from(V2LogTransfer).where(V2LogTransfer.status == "complete")
                )
                > 0
            )
            source_count = await db.scalar(select(func.count()).select_from(V2SourceRecord))
            samples = list((await db.scalars(select(SamplePoint).where(SamplePoint.test_id == test_id))).all())
            assert samples and all(point.source_run_id == terminal["run_id"] for point in samples)
            assert any(point.source_sequence == terminal["safe_boundary"]["sample_seq"] for point in samples)
        await client.recover_logs()
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(V2SourceRecord)) == source_count
        acknowledged = await client.send_command("ack_run", operator_id="admin", role="admin", msg_id="tls-ack")
        assert acknowledged["wire_status"] == "applied"
        assert (await client.get_status())["state_machine"]["current_state"] == "idle"
        assert client.is_online
        print("TLS_APPLICATION_COMPLETE")
    finally:
        if client:
            await client.close()
        await board.close()
        await engine.dispose()


if __name__ == "__main__":
    import asyncio

    assert len(sys.argv) == 4 and sys.argv[1] == "--child"
    asyncio.run(_application_scenario(Path(sys.argv[2]), sys.argv[3] == "1"))
