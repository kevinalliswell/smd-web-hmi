import asyncio

from app.hostcomm.mock_recipe_runtime import MockRecipeRuntime
from app.hostcomm.mock_server import MockHostCommServer
from app.services.recipe_definition import standard_template


def recipe():
    definition = standard_template()
    return {
        "recipe_id": "standard-candidate",
        "version": 1,
        "digest": definition.digest(),
        "definition": definition.model_dump(),
    }


def test_board_simulator_temperature_gas_and_measurement_end():
    runtime = MockRecipeRuntime()
    runtime.start(recipe())
    runtime.advance(3 + 60 * 47)  # 25->495℃，尚未进入还原气氛
    assert runtime.co == 0
    runtime.advance(60)
    assert runtime.furnace >= 500
    assert runtime.co == 1.5
    assert not runtime.measurement_complete
    runtime.advance(60 * 500)
    assert runtime.measurement_complete and runtime.safe_complete
    assert runtime.co == 0 and runtime.burden < 200


def test_operator_stop_cools_without_claiming_measurement_completed():
    runtime = MockRecipeRuntime()
    runtime.start(recipe())
    runtime.advance(3 + 60 * 100)
    runtime.stop()
    assert runtime.running and not runtime.safe_complete
    runtime.advance(60 * 100)
    assert runtime.safe_complete and not runtime.measurement_complete
    assert runtime.co == 0


async def test_disconnected_simulator_keeps_running_and_sampling():
    server = MockHostCommServer(port=0, extended_contract=True, status_interval=0.1, time_scale=1000)
    server.runtime.start(recipe())
    await server.start()
    try:
        first = server._status_snapshot()["payload"]
        await asyncio.sleep(0.4)
        second = server._status_snapshot()["payload"]
        assert not server._clients
        assert second["telemetry"]["sequence"] > first["telemetry"]["sequence"] + 1
        assert second["temperature"]["furnace_pv_deg_c"] > first["temperature"]["furnace_pv_deg_c"]
    finally:
        await server.stop()


async def test_extended_mock_does_not_accept_unimplemented_pause():
    from tests.conftest import make_client

    server = MockHostCommServer(port=0, extended_contract=True, status_interval=None)
    await server.start()
    client = await make_client(server)
    await client.start()
    try:
        result = await client.send_command("pause_hold", {}, operator_id="test", role="operator")
        assert result["result"] == "unsupported"
        assert result["reason_code"] == "mock_action_not_implemented"
    finally:
        await client.close()
        await server.stop()
