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
