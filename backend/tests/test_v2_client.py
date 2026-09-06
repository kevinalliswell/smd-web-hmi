"""Host adapter exercises the actual framed network and SQLite, not mocked sends."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, SamplePoint
from app.hostcomm.v2_client import V2Client
from app.hostcomm.v2_simulator import V2Simulator, synthetic_profile
from app.services.recipe_definition import standard_template


@pytest.fixture
async def connected(tmp_path):
    sim = V2Simulator(
        storage_path=tmp_path / "board.sqlite", test_plaintext=True, profile=synthetic_profile(approved=True)
    )
    await sim.start()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'host.sqlite'}")
    async with engine.begin() as conn:
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
    await client.start()
    for _ in range(100):
        if client.is_online and client._ready:
            break
        await asyncio.sleep(0.01)
    assert client.is_online and client._ready, client.stats
    yield client, sim, factory
    await client.close()
    await sim.close()
    await engine.dispose()


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
