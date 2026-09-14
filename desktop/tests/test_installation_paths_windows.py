"""Native ACL checks for staging while the previous installation is still live."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows file security descriptors")


def test_staging_preserves_denies_and_webview_access_without_user_writes(tmp_path, monkeypatch):
    import ctypes

    security = pytest.importorskip("win32security")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("Changing ownership requires an elevated Windows runner")
    from smd_desktop import installation_paths as paths

    install, data = tmp_path / "program", tmp_path / "data"
    install.mkdir()
    # Explicit AppContainer denies must remain visible to the runtime ACL check.
    descriptor = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(D;;GX;;;S-1-15-2-1)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;BU)", 1
    )
    security.SetNamedSecurityInfo(
        str(install),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )
    monkeypatch.setattr(paths, "registered_paths", lambda: {})
    monkeypatch.setattr(paths, "space_check", lambda *args, **kwargs: None)
    stage = paths.prepare_stage(install, data, 1024)
    assert stage.is_dir()
    current = security.GetNamedSecurityInfo(
        str(install),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.OWNER_SECURITY_INFORMATION,
    )
    assert security.ConvertSidToStringSid(current.GetSecurityDescriptorOwner()) == "S-1-5-32-544"
    acl = current.GetSecurityDescriptorDacl()
    aces = [acl.GetAce(index) for index in range(acl.GetAceCount())]
    assert any(
        ace[0][0] == security.ACCESS_DENIED_ACE_TYPE and security.ConvertSidToStringSid(ace[2]) == "S-1-15-2-1"
        for ace in aces
    )
    for sid in ("S-1-15-2-1", "S-1-15-2-2"):
        assert any(
            ace[0][0] == security.ACCESS_ALLOWED_ACE_TYPE
            and security.ConvertSidToStringSid(ace[2]) == sid
            and ace[1] == 0x20
            and ace[0][1] == 0
            for ace in aces
        )
    forbidden = 0x0002 | 0x0004 | 0x0040 | 0x10000 | 0x40000 | 0x80000
    assert all(
        not ace[1] & forbidden
        for ace in aces
        if ace[0][0] == security.ACCESS_ALLOWED_ACE_TYPE and security.ConvertSidToStringSid(ace[2]) == "S-1-5-32-545"
    )
