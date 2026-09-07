"""Real service processes bound HTTP draining and recover durable command identity."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing

import pytest
from smd_desktop.single_instance import AlreadyRunning, single_instance

from app.hostcomm.v2_contract.codec import command_digest

SERVER_PROCESS = r"""
import asyncio,json,logging,os,sys,threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
import aiosqlite
from app.core.config import get_settings
from app.db.database import get_sessionmaker
from app.main import app
from app.services.v2_operations import V2OperationCoordinator
from smd_desktop.log_stream import LogStream
from smd_desktop.service import create_server
from smd_desktop.single_instance import single_instance
root,mode,lock_name=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
handler=RotatingFileHandler(root/'service.log',encoding='utf-8')
logging.basicConfig(handlers=[handler],level=logging.INFO,force=True)
sys.stdout=sys.stderr=LogStream(logging.getLogger('backend.stdout'))
release=threading.Event()
received=False
async def request(kind,payload,**kwargs):
    global received
    with (root/'wire-requests.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'type':kind,'payload':payload,'msg_id':kwargs.get('msg_id')})+'\n')
    if kind=='command':
        assert mode=='accepted'
        received=True
        (root/'receipt.json').write_text(json.dumps({'request':payload,'msg_id':kwargs['msg_id']}),encoding='utf-8')
        original=payload
    else:
        assert kind=='get_operation' and mode=='recover'
        original=json.loads((root/'receipt.json').read_text(encoding='utf-8'))['request']
        assert payload=={key:original[key] for key in ('operation_id','controller_epoch','command_seq')}
    return {'type':'command_result' if kind=='command' else 'operation_snapshot','payload':{
        **{key:original[key] for key in ('operation_id','controller_epoch','command_seq','request_digest')},
        'result_boot_id':'1'*32,'status':'accepted' if kind=='command' else 'applied','reason':'ok',
        'state_revision':'2','run_id':original['params']['run_id'],
        'lease_id':None,'lease_expires_uptime_ms':None}}
def coordinator():
    return V2OperationCoordinator(get_sessionmaker(),
        SimpleNamespace(is_online=True,boot_id='1'*32,request=request),
        device_id='3'*32,controller_id='4'*32,controller_epoch='5'*32)
execute=aiosqlite.Cursor.execute
async def hold_update(cursor,sql,parameters=None):
    value=await execute(cursor,sql,parameters)
    if received and sql.startswith('UPDATE v2_operation SET') and not (root/'writing').exists():
        (root/'writing').touch()
        try:
            await asyncio.to_thread(release.wait)
        except asyncio.CancelledError:
            (root/'db-cancelled').touch()
            raise
    return value
if mode=='accepted':
    aiosqlite.Cursor.execute=hold_update
@app.post('/api/service-shutdown-test')
async def accepted():
    target=coordinator()
    await target.initialize()
    return await target.submit('stop_run',{'run_id':'6'*32,'reason':'operator_stop'},
        actor='admin',role='admin',operation_id='7'*32)
server=create_server(get_settings())
original_startup=server.startup
async def startup(sockets=None):
    await original_startup(sockets)
    if mode=='recover':
        target=coordinator()
        await target.initialize()
        before=await target.get('7'*32)
        result=await target.query('7'*32)
        repeated=await target.submit('stop_run',{'run_id':'6'*32,'reason':'operator_stop'},
            actor='admin',role='admin',operation_id='7'*32)
        (root/'reconciled.json').write_text(json.dumps({'before':before,'result':result,'repeated':repeated}),encoding='utf-8')
        server.should_exit=True
        return
    (root/'ready.json').write_text(json.dumps({'port':server.servers[0].sockets[0].getsockname()[1],
        'drain_seconds':server.config.timeout_graceful_shutdown}),encoding='utf-8')
    if mode=='body':
        async def observe_request():
            while not any((getattr(c,'scope',None) or {}).get('path')=='/api/auth/login' for c in server.server_state.connections):
                await asyncio.sleep(.01)
            (root/'request-entered').touch()
        asyncio.create_task(observe_request())
server.startup=startup
def stop_request():
    os.read(sys.stdin.fileno(),1)
    server.should_exit=True
    if mode=='accepted':
        os.read(sys.stdin.fileno(),1)
        release.set()
threading.Thread(target=stop_request,daemon=True).start()
with single_instance(lock_name,root/'backend.lock'):
    server.run()
    (root/'run-returned').touch()
(root/'lock-released').touch()
"""


def start_service(root, mode, lock_name):
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(path) for path in sys.path if path),
        "HOSTCOMM_MOCK": "true",
        "PROTOCOL_VERSION": "2.0",
        "HOSTCOMM_DEVICE_ID": "",
        "HOSTCOMM_CONTROLLER_ID": "",
        "HOSTCOMM_CONTROLLER_EPOCH": "",
        "HOSTCOMM_PSK_FILE": "",
        "SMD_HOST": "127.0.0.1",
        "SMD_PORT": "0",
        "SMD_DB_PATH": str(root / "app.sqlite"),
        "SMD_MAINTENANCE_FILE": str(root / "maintenance.json"),
        "SMD_JWT_SECRET": "local-service-shutdown-test-key-" * 2,
        "SMD_BOOTSTRAP_ADMIN_PASSWORD": "Local-shutdown-test-password-1!",
        "SMD_TLS_CERTFILE": "",
        "SMD_TLS_KEYFILE": "",
    }
    return subprocess.Popen(
        [sys.executable, "-c", SERVER_PROCESS, str(root), mode, lock_name],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_file(path, child, *, timeout=30):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert child.poll() is None, "server exited before the test observation"
        if time.monotonic() >= deadline:
            pytest.fail("service test observation did not arrive")
        time.sleep(0.02)


def finish_child(child, connection=None):
    if connection:
        connection.close()  # Release only this test's client if an assertion fails.
    if child.poll() is None:
        try:
            child.stdin.write(b"sr")  # Stop and release this test's bounded DB pause; never kill the process.
            child.stdin.flush()
        except BrokenPipeError:
            pass  # It may exit between poll() and this test's stop request.
        child.wait(timeout=25)
    child.communicate(timeout=5)


def test_actual_service_config_exits_with_an_incomplete_http_body(tmp_path):
    child = start_service(tmp_path, "body", "SmdHmi.ServiceShutdownTest." + uuid.uuid4().hex)
    connection = None
    try:
        wait_file(tmp_path / "ready.json", child)
        ready = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
        assert ready["drain_seconds"] == 15
        connection = socket.create_connection(("127.0.0.1", ready["port"]), timeout=3)
        connection.sendall(
            b"POST /api/auth/login HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\nContent-Length: 999\r\n\r\n{"
        )
        wait_file(tmp_path / "request-entered", child)
        child.stdin.write(b"s")  # Same should_exit assignment as the SCM stop callback.
        child.stdin.flush()
        child.wait(timeout=25)
        assert child.returncode == 0
        log = (tmp_path / "service.log").read_text(encoding="utf-8")
        assert "Waiting for connections to close" in log and "timeout graceful shutdown exceeded" in log
        assert log.index("timeout graceful shutdown exceeded") < log.index("app.stopped")
        assert "Application shutdown complete." in log
    finally:
        finish_child(child, connection)


def test_service_process_preserves_unknown_identity_and_queries_after_restart(tmp_path):
    lock_name = "SmdHmi.ServiceShutdownTest." + uuid.uuid4().hex
    child = start_service(tmp_path, "accepted", lock_name)
    connection = None
    try:
        wait_file(tmp_path / "ready.json", child)
        ready = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
        assert ready["drain_seconds"] == 15
        connection = socket.create_connection(("127.0.0.1", ready["port"]), timeout=3)
        connection.sendall(b"POST /api/service-shutdown-test HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        wait_file(tmp_path / "writing", child)
        receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
        original = receipt["request"]
        assert command_digest(original) == original["request_digest"]
        child.stdin.write(b"s")
        child.stdin.flush()
        wait_file(tmp_path / "db-cancelled", child, timeout=20)
        assert child.poll() is None and not (tmp_path / "run-returned").exists()
        with pytest.raises(AlreadyRunning), single_instance(lock_name, tmp_path / "backend.lock"):
            pytest.fail("the service released its outer process lock while cleanup was pending")
        child.stdin.write(b"r")
        child.stdin.flush()
        child.wait(timeout=15)
        assert child.returncode == 0 and (tmp_path / "lock-released").exists()
    finally:
        finish_child(child, connection)

    with single_instance(lock_name, tmp_path / "backend.lock"), closing(sqlite3.connect(tmp_path / "app.sqlite")) as db:
        db.row_factory = sqlite3.Row
        stored = dict(db.execute("SELECT * FROM v2_operation WHERE operation_id=?", ("7" * 32,)).fetchone())
        assert stored["status"] == "unknown" and not stored["reconciled"]
        assert stored["result_json"] is None  # An uncommitted receipt cannot prove success.
        for key in ("operation_id", "controller_epoch", "command_seq", "request_digest"):
            assert stored[key] == original[key]
        assert stored["msg_id"] == receipt["msg_id"] and json.loads(stored["request_json"]) == original
        assert stored["command_seq"] == "1"
        db.execute("UPDATE v2_operation SET status=status")  # No abandoned SQLite writer survives service exit.
        db.commit()

    restarted = start_service(tmp_path, "recover", lock_name)
    try:
        restarted.wait(timeout=30)
        assert restarted.returncode == 0
        recovered = json.loads((tmp_path / "reconciled.json").read_text(encoding="utf-8"))
        assert recovered["before"]["status"] == "unknown" and recovered["before"]["result"] is None
        assert recovered["result"]["status"] == recovered["repeated"]["status"] == "applied"
        for key in ("operation_id", "controller_epoch", "command_seq", "request_digest"):
            assert recovered["result"][key] == recovered["result"]["result"][key] == original[key]
        assert recovered["result"]["msg_id"] == receipt["msg_id"]
        calls = [
            json.loads(line) for line in (tmp_path / "wire-requests.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [call["type"] for call in calls] == ["command", "get_operation"]
        assert calls[1]["payload"] == {
            key: original[key] for key in ("controller_epoch", "operation_id", "command_seq")
        }
    finally:
        finish_child(restarted)
