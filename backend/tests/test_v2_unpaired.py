"""An unpaired production v2 installation starts its application and remains uncontrollable."""

import os
import subprocess
import sys
from pathlib import Path


def test_migrated_unpaired_production_installation_is_application_ready(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "SMD_DB_PATH": str(tmp_path / "fresh.db"),
        "PROTOCOL_VERSION": "2.0",
        "HOSTCOMM_MOCK": "false",
        "HOSTCOMM_HOST": "127.0.0.1",
        "HOSTCOMM_PORT": "34211",
        "HOSTCOMM_DEVICE_ID": "",
        "HOSTCOMM_CONTROLLER_ID": "",
        "HOSTCOMM_CONTROLLER_EPOCH": "",
        "HOSTCOMM_PSK_FILE": "",
        "SMD_MAINTENANCE_FILE": str(tmp_path / "maintenance.json"),
        "SMD_BOOTSTRAP_ADMIN_PASSWORD": "test-initial-password-for-local-fixture",
        "SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE": "",
        "SMD_JWT_SECRET": "local-test-secret-at-least-thirty-two-bytes",
    }
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    script = """
import asyncio, os
from pathlib import Path
from httpx import ASGITransport, AsyncClient
import app.main as main
from app.services.gateway_lease import GatewayLease

lock_root = Path(os.environ["SMD_DB_PATH"]).parent / "locks"
lock_root.mkdir(mode=0o700)
main.GatewayLease = lambda host, port: GatewayLease(host, port, lock_root=lock_root)

async def run():
    app = main.create_app()
    async with main.lifespan(app):
        assert app.state.hostcomm_client.protocol_version == "2.0"
        assert not app.state.hostcomm_client.is_online
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
            response = await http.get("/api/system/health")
            assert response.status_code == 200, response.text
            health = response.json()["data"]
            assert health["status"] == "ready", health
            assert health["checks"]["hostcomm"] == "offline", health

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=backend, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
