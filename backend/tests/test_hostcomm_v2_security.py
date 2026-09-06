"""Real TLS-PSK interoperability and fail-closed key-file validation."""

import asyncio
import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.hostcomm.v2_security import (
    OPENSSL_AES128_POLICY,
    V2SecurityError,
    _validate_windows_acl,
    create_client_context,
    create_server_context,
    load_psk,
    psk_identity,
    verify_tls,
)
from tests.v2_test_support import private_test_directory


@pytest.mark.skipif(sys.version_info < (3, 13), reason="Python 3.11 verifies only the explicit legacy baseline")
def test_release_python_has_required_tls_psk_capability():
    assert (
        getattr(ssl, "HAS_PSK", False)
        and hasattr(ssl.SSLContext, "set_psk_client_callback")
        and hasattr(ssl.SSLContext, "set_psk_server_callback")
    ), (
        "The Python 3.13+ release runtime lacks TLS-PSK support. "
        "HostComm 2.0 requires ssl.HAS_PSK and both PSK callback APIs; "
        f"do not publish this runtime or downgrade transport. Python={sys.version.split()[0]}, "
        f"OpenSSL={ssl.OPENSSL_VERSION}"
    )


@pytest.fixture
def key_file(tmp_path):
    path = private_test_directory(tmp_path) / "pairing.psk"
    path.write_text("37" * 32 + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


def test_key_reader_accepts_exact_32_bytes_hex_without_exposing_value(key_file):
    assert load_psk(key_file) == bytes.fromhex("37" * 32)
    key_file.write_text("not-a-key\n", encoding="ascii")
    with pytest.raises(V2SecurityError) as error:
        load_psk(key_file)
    assert "not-a-key" not in str(error.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink semantics")
def test_key_file_rejects_broad_permissions_and_symlink(key_file):
    key_file.chmod(0o644)
    with pytest.raises(V2SecurityError):
        load_psk(key_file)
    key_file.chmod(0o600)
    link = key_file.with_suffix(".link")
    link.symlink_to(key_file)
    with pytest.raises(V2SecurityError):
        load_psk(link)


@pytest.mark.skipif(os.name != "nt", reason="Requires a real Windows file DACL")
def test_windows_key_file_rejects_everyone_read_then_accepts_private_acl(key_file):
    # Exercise OWNER RIGHTS explicitly, including Python versions whose mkdir
    # does not yet install this ACE. It must resolve only to a verified owner.
    subprocess.run(
        ["icacls.exe", str(key_file), "/grant", "*S-1-3-4:F"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert load_psk(key_file) == bytes.fromhex("37" * 32)
    subprocess.run(
        ["icacls.exe", str(key_file), "/grant", "*S-1-1-0:R"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    try:
        with pytest.raises(V2SecurityError, match="ACL grants access"):
            load_psk(key_file)
    finally:
        subprocess.run(
            ["icacls.exe", str(key_file), "/remove:g", "*S-1-1-0"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    assert load_psk(key_file) == bytes.fromhex("37" * 32)


@pytest.mark.parametrize("owner", ["S-1-5-21-100-200-300-1001", "S-1-5-18", "S-1-5-32-544", "S-1-5-80-1-2-3-4-5"])
def test_owner_rights_accepts_only_verified_operator_system_admin_or_service_owner(owner):
    allowed = {"S-1-5-21-100-200-300-1001", "S-1-5-18", "S-1-5-32-544", "S-1-5-80-1-2-3-4-5"}
    _validate_windows_acl(owner, allowed, [("S-1-3-4", 0x1F01FF), ("S-1-5-32-544", 0x1F01FF)])


@pytest.mark.parametrize("grants", [[], [("S-1-3-4", 0x1F01FF)], [("S-1-5-32-544", 0x1F01FF)]])
def test_untrusted_owner_cannot_use_owner_rights_or_replace_a_private_dacl(grants):
    # A foreign owner can replace even an otherwise administrator-only DACL.
    with pytest.raises(V2SecurityError, match="owner is outside"):
        _validate_windows_acl("S-1-5-21-100-200-300-1002", {"S-1-5-32-544"}, grants)


@pytest.mark.parametrize("principal", ["S-1-1-0", "S-1-5-11", "S-1-5-32-545", "S-1-5-80-9-8-7-6-5"])
def test_trusted_owner_does_not_authorize_broad_groups_or_an_unrelated_service(principal):
    with pytest.raises(V2SecurityError, match="ACL grants access"):
        _validate_windows_acl("S-1-5-32-544", {"S-1-5-32-544"}, [("S-1-3-4", 0x1F01FF), (principal, 0x120089)])


def test_missing_psk_support_never_falls_back(key_file, monkeypatch):
    monkeypatch.setattr(ssl, "HAS_PSK", False, raising=False)
    with pytest.raises(V2SecurityError, match="PSK"):
        create_client_context(key_file, psk_identity("1" * 32, "2" * 32, "3" * 32))


@pytest.mark.skipif(not getattr(ssl, "HAS_PSK", False), reason="Requires the real PSK context API")
async def test_credential_load_evidence_is_local_only_and_not_repeated_for_network_retries(key_file, monkeypatch):
    from app.hostcomm import v2_transport

    notices = []
    monkeypatch.setattr(
        v2_transport, "logger", SimpleNamespace(info=lambda event, **fields: notices.append((event, fields)))
    )

    async def refused(*args, **kwargs):
        raise ConnectionRefusedError("synthetic closed endpoint")

    monkeypatch.setattr(v2_transport.asyncio, "open_connection", refused)
    client = v2_transport.V2Transport(
        "127.0.0.1",
        1,
        device_id="1" * 32,
        controller_id="2" * 32,
        controller_epoch="3" * 32,
        psk_file=key_file,
        client_version="credential-evidence-test",
    )
    for _ in range(2):
        with pytest.raises(ConnectionRefusedError):
            await client._open()
    assert notices == [("v2.tls_credentials_loaded", {"device_id": "1" * 32, "handshake": "not_started"})]
    assert not client.is_online
    assert key_file.read_text(encoding="ascii").strip() not in str(notices)
    key_file.write_text("invalid-key\n", encoding="ascii")
    client.controller_epoch = "4" * 32
    with pytest.raises(V2SecurityError):
        await client._open()
    assert len(notices) == 1  # A failed credential read cannot become successful evidence.


@pytest.mark.skipif(not getattr(ssl, "HAS_PSK", False), reason="TLS-PSK runtime requires Python 3.13/OpenSSL PSK")
@pytest.mark.parametrize("failure", ["none", "key", "identity", "certificate"])
def test_real_tls13_psk_roundtrip_or_authentication_rejection(key_file, tmp_path, failure):
    policy = tmp_path / "openssl.cnf"
    policy.write_text(OPENSSL_AES128_POLICY, encoding="ascii")
    if failure == "certificate":
        executable = shutil.which("openssl")
        if executable is None:
            pytest.skip("openssl CLI is needed only to create the ephemeral certificate attack fixture")
        subprocess.run(
            [
                executable,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(key_file.with_name("server.key")),
                "-out",
                str(key_file.with_name("server.crt")),
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
    program = r"""
import asyncio, pathlib, ssl, sys
from app.hostcomm.v2_security import create_client_context, create_server_context, psk_identity, verify_tls, V2SecurityError
async def main():
    key_file=pathlib.Path(sys.argv[1]); failure=sys.argv[2]
    identity=psk_identity("1"*32,"2"*32,"3"*32)
    accepted=asyncio.Event()
    async def serve(reader,writer):
        try:
            if failure!="certificate": verify_tls(writer)
            data=await reader.readexactly(4);accepted.set()
            writer.write(data);await writer.drain()
        except (asyncio.IncompleteReadError,ConnectionError): pass
        finally:
            writer.close(); await writer.wait_closed()
    if failure=="certificate":
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version=server_context.maximum_version=ssl.TLSVersion.TLSv1_3
        server_context.num_tickets=0
        server_context.load_cert_chain(key_file.with_name("server.crt"),key_file.with_name("server.key"))
    else: server_context=create_server_context(key_file,identity)
    server=await asyncio.start_server(serve,"127.0.0.1",0,ssl=server_context)
    other=key_file.with_name("other.psk");other.write_text("38"*32+"\n", encoding="ascii");other.chmod(0o600)
    context=create_client_context(other if failure=="key" else key_file,identity+"wrong" if failure=="identity" else identity)
    writer=None
    try:
        try:
            reader,writer=await asyncio.wait_for(asyncio.open_connection("127.0.0.1",server.sockets[0].getsockname()[1],ssl=context,server_hostname=""),3)
            verify_tls(writer)
            writer.write(b"ping");await writer.drain()
            assert await reader.readexactly(4)==b"ping"
            assert failure=="none" and accepted.is_set()
            print("TLSv1.3 TLS_AES_128_GCM_SHA256 external-PSK roundtrip verified")
        except (ssl.SSLError,ConnectionError,asyncio.IncompleteReadError,V2SecurityError):
            assert failure!="none" and not accepted.is_set()
            print("PSK authentication rejected",failure)
    finally:
        if writer:
            writer.close()
            try: await writer.wait_closed()
            except (ssl.SSLError,ConnectionError): pass
        server.close();await server.wait_closed()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(key_file), failure],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "OPENSSL_CONF": str(policy)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verified" in result.stdout if failure == "none" else "rejected" in result.stdout
