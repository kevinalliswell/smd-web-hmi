import pytest

from app.core.network import tls_options


def test_local_access_and_lan_tls_have_distinct_requirements(monkeypatch, tmp_path):
    monkeypatch.delenv("SMD_TLS_CERTFILE", raising=False)
    monkeypatch.delenv("SMD_TLS_KEYFILE", raising=False)
    assert tls_options("127.0.0.1") == {}
    for host in ("0.0.0.0", "192.168.1.2", "::"):
        with pytest.raises(RuntimeError):
            tls_options(host)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("fixture")
    key.write_text("fixture")
    monkeypatch.setenv("SMD_TLS_CERTFILE", str(cert))
    monkeypatch.setenv("SMD_TLS_KEYFILE", str(key))
    assert tls_options("0.0.0.0") == {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


def test_source_server_uses_dotenv_tls_settings(tmp_path, monkeypatch):
    from app import server
    from app.core.config import Settings

    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_text("fixture")
    key.write_text("fixture")
    env = tmp_path / ".env"
    env.write_text(f'SMD_HOST=0.0.0.0\nSMD_TLS_CERTFILE="{cert}"\nSMD_TLS_KEYFILE="{key}"\n')
    for name in ("SMD_HOST", "SMD_TLS_CERTFILE", "SMD_TLS_KEYFILE"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=env)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    server.main()
    assert calls[0]["ssl_certfile"] == str(cert)
    assert calls[0]["ssl_keyfile"] == str(key)
    assert calls[0]["host"] == "0.0.0.0"
    assert calls[0]["workers"] == 1
