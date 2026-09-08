"""Host adapter exercises the actual framed network and SQLite, not mocked sends."""

import asyncio
import uuid
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, SamplePoint
from app.hostcomm.v2_client import V2Client
from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile
from app.services.recipe_definition import standard_template


@pytest.fixture
async def connected(tmp_path):
    async with AsyncExitStack() as cleanup:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'host.sqlite'}")
        cleanup.push_async_callback(engine.dispose)
        sim = V2Simulator(
            storage_path=tmp_path / "board.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True)
        )
        cleanup.push_async_callback(sim.close)
        await sim.start()
        async with engine.begin() as conn:
            # SQLite legacy mode does not begin for DDL; batch only this fresh test schema.
            await conn.exec_driver_sql("BEGIN")
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        p = sim.pairing
        client = V2Client(
            *sim.address,
            factory=factory,
            device_id=p.device_id,
            controller_id=p.controller_id,
            controller_epoch=p.controller_epoch,
            psk_file="",
            mock=True,
            client_version="test",
        )
        cleanup.push_async_callback(client.close)
        await client.start()
        try:
            # Ready includes profile/alarm reconciliation and committed source recovery,
            # not just the handshake. This fixture budget leaves protocol deadlines intact.
            async with asyncio.timeout(10):
                while not (client.is_online and client._ready):
                    await asyncio.sleep(0.01)
        except TimeoutError as exc:
            raise TimeoutError(
                f"V2 fixture recovery timed out: online={client.is_online}, ready={client._ready}, "
                f"error={client.last_error}, stats={client.stats}"
            ) from exc
        yield client, sim, factory


async def test_queried_status_never_allocates_samples_or_device_lease(connected):
    client, sim, factory = connected
    assert client.is_online
    await client.get_status()
    await client.get_status()
    assert client.transport.lease_id is None
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(SamplePoint)) == 0


async def test_recipe_atomic_activation_round_trips_original_saved_binding(connected):
    client, sim, factory = connected
    model = standard_template()
    value = dict(recipe_id=uuid.uuid4().hex, version=1, digest=model.digest(), definition=model.model_dump())
    # Source recipes are normally saved by the API before activation.
    import json

    from app.db.recipe_models import RecipeVersion

    async with factory() as db, db.begin():
        db.add(
            RecipeVersion(
                recipe_id=value["recipe_id"],
                version=1,
                digest=value["digest"],
                definition_json=json.dumps(value["definition"]),
                created_at="2026-09-06T00:00:00Z",
                created_by="admin",
            )
        )
    reply = await client.send_command(
        "set_parameters",
        {"values": {"recipe": value}},
        operator_id="admin",
        role="admin",
        msg_id="persisted-http-message",
    )
    assert reply["result"] == "accepted"
    assert (await client.get_parameters())["params"]["recipe"] == value
    first_sequence = sim.state.data["highwater"][sim.pairing.controller_epoch]
    assert (
        await client.send_command(
            "set_parameters",
            {"values": {"recipe": value}},
            operator_id="admin",
            role="admin",
            msg_id="persisted-http-message",
        )
    )["result"] == "accepted"
    assert sim.state.data["highwater"][sim.pairing.controller_epoch] == first_sequence
