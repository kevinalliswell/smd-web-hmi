import asyncio
import json
import secrets
import socket
from uuid import uuid4

import pytest
from smd_bench.browser import eventually
from smd_bench.worker import Worker

from app.hostcomm.v2_security import create_client_context, psk_identity
from app.hostcomm.v2_transport import V2Transport


@pytest.mark.asyncio
async def test_private_worker_proves_wrong_key_attempt_then_real_tls_hello(tmp_path):
    import ssl

    if not getattr(ssl, "HAS_PSK", False):
        pytest.skip("This compatibility interpreter has no TLS-PSK; frozen Windows Python3.13 must execute this test")
    key = tmp_path / "device.psk"
    key.write_text(secrets.token_hex(32))
    key.chmod(0o600)
    ids = {name: uuid4().hex for name in ("device_id", "controller_id", "controller_epoch")}
    pairing = tmp_path / "firmware-pairing.json"
    pairing.write_text(json.dumps({**ids, "psk_file": key.name}))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    worker = Worker(tmp_path, pairing, port=port)
    try:
        await worker.start(wrong_psk=True)
        context = create_client_context(key, psk_identity(**ids))
        with pytest.raises((ssl.SSLError, ConnectionError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port, ssl=context, server_hostname=None), 5)
        snapshot = await worker.request("snapshot")
        assert snapshot["psk_attempts"] > 0 and snapshot["tls_handshakes"] == []
        await worker.close()
        await worker.start()
        transport = V2Transport("127.0.0.1", port, **ids, psk_file=key, client_version="SmdBench-test")
        try:
            await transport.start()
            assert transport.is_online
            snapshot = await worker.request("snapshot")
            assert snapshot["tls_handshakes"][-1]["cipher"] == "TLS_AES_128_GCM_SHA256"
            await worker.request("disconnect")
            try:
                await eventually(
                    lambda: worker.request("snapshot"), lambda value: len(value["tls_handshakes"]) > 1, timeout=10
                )
            except TimeoutError:
                raise AssertionError({"transport": transport.last_error, "driver": await worker.request("snapshot")})
        finally:
            await transport.close()
    finally:
        await worker.close()
