"""Pairwise TLS 1.3 PSK policy; never falls back to plaintext or certificate-less TCP."""

from __future__ import annotations

import hmac
import os
import re
import ssl
import stat
from pathlib import Path

OPENSSL_AES128_POLICY = """openssl_conf = openssl_init
[openssl_init]
ssl_conf = ssl_settings
[ssl_settings]
system_default = hostcomm_policy
[hostcomm_policy]
MinProtocol = TLSv1.3
MaxProtocol = TLSv1.3
Ciphersuites = TLS_AES_128_GCM_SHA256
Groups = P-256
"""


class V2SecurityError(ValueError):
    """Security configuration is unusable; error text never includes key material."""


def psk_identity(device_id: str, controller_id: str, controller_epoch: str) -> str:
    if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in (device_id, controller_id, controller_epoch)):
        raise V2SecurityError("Pairing identifiers must be full lowercase UUID hex")
    return f"smd2/{device_id}/{controller_id}/{controller_epoch}"


def _windows_permissions(fd: int) -> None:
    """Inspect the already opened file DACL; allow this identity/service, SYSTEM and Administrators."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.LookupAccountNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        pointer,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.LookupAccountNameW.restype = wintypes.BOOL
    advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD] + [ctypes.POINTER(pointer)] * 5
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        pointer,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [pointer]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def sid_text(sid):
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise V2SecurityError("Cannot inspect pairing-file ACL principal")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, pointer))

    def token_information(token, kind):
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, kind, data, size, ctypes.byref(size)):
            raise V2SecurityError("Cannot inspect pairing-file process identity")
        return data

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise V2SecurityError("Cannot inspect pairing-file process identity")
    try:
        user = token_information(token, 1)
        allowed = {"S-1-5-18", "S-1-5-32-544", sid_text(pointer.from_buffer(user).value)}

        # An elevated offline installer is not a member of the service token.
        # Permit only this application's fixed service account, never arbitrary service SIDs.
        sid_size, domain_size, use = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        advapi.LookupAccountNameW(
            None, "NT SERVICE\\SmdHmi", None, ctypes.byref(sid_size), None, ctypes.byref(domain_size), ctypes.byref(use)
        )
        if sid_size.value:
            service_sid = ctypes.create_string_buffer(sid_size.value)
            domain = ctypes.create_unicode_buffer(max(1, domain_size.value))
            if not advapi.LookupAccountNameW(
                None,
                "NT SERVICE\\SmdHmi",
                service_sid,
                ctypes.byref(sid_size),
                domain,
                ctypes.byref(domain_size),
                ctypes.byref(use),
            ):
                raise V2SecurityError("Cannot inspect SmdHmi service pairing-file identity")
            allowed.add(sid_text(service_sid))

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", pointer), ("attributes", wintypes.DWORD)]

        class TokenGroups(ctypes.Structure):
            _fields_ = [("count", wintypes.DWORD), ("first", SidAndAttributes)]

        groups = token_information(token, 2)
        count = wintypes.DWORD.from_buffer(groups).value
        for index in range(count):
            group = SidAndAttributes.from_buffer(
                groups, TokenGroups.first.offset + index * ctypes.sizeof(SidAndAttributes)
            )
            sid = sid_text(group.sid)
            if sid.startswith("S-1-5-80-") and group.attributes & 4:
                allowed.add(sid)
    finally:
        kernel.CloseHandle(token)
    dacl, descriptor = pointer(), pointer()
    code = advapi.GetSecurityInfo(
        msvcrt.get_osfhandle(fd), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
    )
    if code or not dacl.value:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        raise V2SecurityError("Pairing file requires a restricted DACL")
    try:
        ace_count = ctypes.c_ushort.from_address(dacl.value + 4).value
        for index in range(ace_count):
            ace = pointer()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise V2SecurityError("Cannot inspect pairing-file DACL")
            kind, flags = ctypes.string_at(ace, 2)
            if flags & 8 or kind == 1:  # Inherit-only or a deny ACE cannot grant key access.
                continue
            if kind != 0:
                raise V2SecurityError("Pairing-file ACL contains an unsupported access grant")
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            if mask & 0xD00D01BF and sid_text(ace.value + 8) not in allowed:
                raise V2SecurityError("Pairing-file ACL grants access outside its service and administrators")
    finally:
        kernel.LocalFree(descriptor)


def load_psk(path: str | Path) -> bytes:
    try:
        location = Path(path)
        if location.is_symlink():
            raise V2SecurityError("Pairing file must not be a symlink")
        fd = os.open(location, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode) or details.st_size not in (64, 65, 66):
                raise V2SecurityError("Pairing file must contain one 64-character hexadecimal key")
            if os.name == "nt":
                _windows_permissions(fd)
            elif details.st_mode & 0o077 or details.st_uid not in {os.getuid(), 0}:
                raise V2SecurityError("Pairing file must be private to its owner (mode 0600 or 0400)")
            raw = os.read(fd, 67)
        finally:
            os.close(fd)
        if not re.fullmatch(rb"[0-9a-fA-F]{64}(?:\r?\n)?", raw):
            raise V2SecurityError("Pairing file must contain one 64-character hexadecimal key")
        return bytes.fromhex(raw.decode("ascii").strip())
    except (OSError, UnicodeError) as exc:
        raise V2SecurityError("Cannot securely read the configured pairing file") from exc


def _context(*, server: bool) -> ssl.SSLContext:
    if not getattr(ssl, "HAS_PSK", False) or not hasattr(ssl.SSLContext, "set_psk_client_callback"):
        raise V2SecurityError("HostComm 2.0 requires Python 3.13 with OpenSSL TLS-PSK support")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Authentication is pairwise external PSK, never an anonymous fallback.
    context.set_ecdh_curve("prime256v1")
    context.options |= ssl.OP_NO_TICKET
    if server:
        context.num_tickets = 0
    return context


def create_client_context(psk_file: str | Path, identity: str) -> ssl.SSLContext:
    context = _context(server=False)
    key = load_psk(psk_file)
    context.set_psk_client_callback(lambda hint: (identity, key))
    return context


def create_server_context(psk_file: str | Path, identity: str) -> ssl.SSLContext:
    context = _context(server=True)
    key = load_psk(psk_file)
    context.set_psk_server_callback(
        lambda presented: key if presented and hmac.compare_digest(presented, identity) else b""
    )
    return context


def verify_tls(writer) -> None:
    connection = writer.get_extra_info("ssl_object")
    if (
        connection is None
        or connection.version() != "TLSv1.3"
        or connection.cipher()[0] != "TLS_AES_128_GCM_SHA256"
        or connection.session.has_ticket
        # OpenSSL reports an external-PSK handshake as session_reused even on
        # its first connection. With a fresh context, no cached/passed session,
        # no tickets and no peer certificate this proves PSK was selected;
        # CERT_NONE must never permit a certificate-authentication fallback.
        or not connection.session_reused
        or connection.getpeercert(binary_form=True) is not None
    ):
        raise V2SecurityError("Peer did not establish the required fresh TLS 1.3 AES-128-GCM PSK session")
