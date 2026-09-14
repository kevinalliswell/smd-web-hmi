"""Upgrade health failures must be useful without disclosing HTTP or installation secrets."""

import json
import logging
import socket
import threading
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from smd_desktop import windows_platform
from smd_desktop.windows_platform import WindowsPlatform

TARGET = "0.3.0-rc.5"
SECRET = "private-health-token-do-not-log"


def health(*, version=TARGET, status="ready", **checks):
    return {
        "data": {
            "version": version,
            "status": status,
            "checks": {
                "database": "ok",
                "schema": "ok",
                "storage": "ok",
                "backup": "ok",
                "hostcomm": "offline",
                "storage_free_bytes": 2**32,
                **checks,
            },
            "private": SECRET,
        }
    }


@contextmanager
def endpoint(tmp_path, replies):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            stage = "backend" if self.path.endswith("/api/system/health") else "frontend"
            received.append(stage)
            status, content_type, body = replies(stage, received.count(stage))
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    worker.start()
    (tmp_path / "client.json").write_text(
        json.dumps({"url": f"http://127.0.0.1:{server.server_port}/{SECRET}"}), encoding="utf-8"
    )
    try:
        yield received
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
        assert not worker.is_alive()


@pytest.fixture
def clock(monkeypatch):
    elapsed = [0.0]
    sleeps = []

    def sleep(duration):
        sleeps.append(duration)
        elapsed[0] += duration

    monkeypatch.setattr(windows_platform, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    return sleeps


def failed(platform, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as failure:
        platform.healthy(TARGET)
    message = str(failure.value)
    assert "health_diagnostic=" in message
    evidence = json.loads(message.split("health_diagnostic=", 1)[1])
    assert SECRET not in message + caplog.text
    assert "127.0.0.1" not in message + caplog.text
    return evidence


def test_health_failure_identifies_backend_checks_without_private_payload(tmp_path, clock, caplog):
    def replies(stage, attempt):
        return 200, "application/json", health(status="not_ready", storage="low", backup="error", token=SECRET)

    with endpoint(tmp_path, replies) as received:
        evidence = failed(WindowsPlatform(tmp_path, timeout=1), caplog)
    assert evidence["phase"] == "backend"
    assert evidence["reason"] == "backend_not_ready"
    assert evidence["expected_version"] == evidence["observed_version"] == TARGET
    assert evidence["http_status"] == 200
    assert evidence["checks"] == {
        "database": "ok",
        "schema": "ok",
        "storage": "low",
        "backup": "error",
        "hostcomm": "offline",
        "storage_free_bytes": 2**32,
    }
    assert received == ["backend", "backend"]
    assert clock == [0.5, 0.5]
    assert len(caplog.records) == 1  # Repeated identical failures do not flood the updater log.


@pytest.mark.parametrize(
    "stage,reply,reason",
    [
        ("backend", (503, "application/json", {"error": SECRET}), "http_status"),
        ("backend", (200, "application/json", b'{"private": "' + SECRET.encode()), "invalid_response"),
        ("backend", (200, "application/json", {"data": [SECRET]}), "invalid_response"),
        ("backend", (200, "application/json", health(version="0.3.0-rc.4")), "version_mismatch"),
        ("frontend", (401, "text/html", SECRET.encode()), "http_status"),
        ("frontend", (200, "application/json", {"token": SECRET}), "frontend_not_html"),
    ],
)
def test_health_distinguishes_http_version_and_response_failures(tmp_path, clock, caplog, stage, reply, reason):
    def replies(actual_stage, attempt):
        return reply if actual_stage == stage else (200, "application/json", health())

    with endpoint(tmp_path, replies):
        evidence = failed(WindowsPlatform(tmp_path, timeout=0.5), caplog)
    assert evidence["phase"] == stage
    assert evidence["reason"] == reason
    assert evidence["http_status"] == reply[0]
    if reason == "version_mismatch":
        assert evidence["observed_version"] == "0.3.0-rc.4"


def test_health_unknown_values_and_malformed_config_are_redacted(tmp_path, clock, caplog):
    def replies(stage, attempt):
        return 200, "application/json", health(version=SECRET, status=SECRET, storage=[SECRET])

    with endpoint(tmp_path, replies):
        evidence = failed(WindowsPlatform(tmp_path, timeout=0.5), caplog)
    assert evidence["observed_version"] == evidence["observed_status"] == "unknown"
    assert evidence["checks"]["storage"] == "unknown"
    (tmp_path / "client.json").write_text('{"url": "' + SECRET, encoding="utf-8")
    evidence = failed(WindowsPlatform(tmp_path, timeout=0.5), caplog)
    assert evidence["phase"] == "client"
    assert evidence["reason"] == "invalid_client_config"


def test_health_refused_connection_reports_type_without_address(tmp_path, clock, caplog):
    # Obtain a real local port, then close it before exercising urllib connection refusal.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (tmp_path / "client.json").write_text(json.dumps({"url": f"http://127.0.0.1:{port}/{SECRET}"}), encoding="utf-8")
    evidence = failed(WindowsPlatform(tmp_path, timeout=0.5), caplog)
    assert evidence["phase"] == "backend"
    assert evidence["reason"] == "network_error"
    assert evidence["network_type"] == "connection_refused"


def test_health_retries_then_accepts_ready_application_with_offline_hostcomm(tmp_path, clock, caplog):
    def replies(stage, attempt):
        if stage == "frontend":
            return 200, "text/html; charset=utf-8", b"<!doctype html><title>HMI</title>"
        if attempt == 1:
            return 503, "application/json", {"private": SECRET}
        return (
            200,
            "application/json",
            health(status="not_ready" if attempt == 2 else "ready", backup="error" if attempt == 2 else "ok"),
        )

    with endpoint(tmp_path, replies) as received, caplog.at_level(logging.WARNING):
        assert WindowsPlatform(tmp_path, timeout=2).healthy(TARGET) is None
    assert received == ["backend", "backend", "backend", "frontend"]
    assert clock == [0.5, 0.5]
    assert SECRET not in caplog.text
    assert len(caplog.records) == 2


@pytest.mark.parametrize("free_bytes", [True, -1, 2**64, SECRET])
def test_health_free_space_only_records_bounded_integers(tmp_path, clock, caplog, free_bytes):
    def replies(stage, attempt):
        return 200, "application/json", health(status="not_ready", storage_free_bytes=free_bytes)

    with endpoint(tmp_path, replies):
        evidence = failed(WindowsPlatform(tmp_path, timeout=0.5), caplog)
    assert evidence["checks"]["storage_free_bytes"] == "unknown"


@pytest.mark.parametrize(
    "error,kind", [(TimeoutError(SECRET), "timeout"), (urllib.error.URLError(SECRET), "network_error")]
)
def test_health_network_exceptions_do_not_echo_arbitrary_details(tmp_path, clock, caplog, monkeypatch, error, kind):
    (tmp_path / "client.json").write_text(json.dumps({"url": "http://127.0.0.1/" + SECRET}), encoding="utf-8")
    platform = WindowsPlatform(tmp_path, timeout=0.5)

    def unavailable(request, *, timeout):
        assert timeout == 3
        raise error

    monkeypatch.setattr(platform.opener, "open", unavailable)
    evidence = failed(platform, caplog)
    assert evidence["network_type"] == kind


def test_health_frontend_can_recover_on_next_attempt(tmp_path, clock, caplog):
    def replies(stage, attempt):
        if stage == "backend":
            return 200, "application/json", health()
        return (503, "text/plain", SECRET.encode()) if attempt == 1 else (200, "text/html", b"<html>Ready</html>")

    with endpoint(tmp_path, replies) as received, caplog.at_level(logging.WARNING):
        WindowsPlatform(tmp_path, timeout=1).healthy(TARGET)
    assert received == ["backend", "frontend", "backend", "frontend"]
    assert clock == [0.5]
    assert SECRET not in caplog.text
    assert '"phase": "frontend"' in caplog.text
