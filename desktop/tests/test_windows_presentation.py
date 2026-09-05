"""用SCM/注册表/快捷方式适配替身验证升级与回退选择同一版本。"""

import sys
from types import ModuleType, SimpleNamespace

from smd_desktop.windows_platform import WindowsPlatform


def test_version_configuration_updates_registry_and_desktop_entry_together(tmp_path, monkeypatch):
    registry = {}
    shortcuts = {}
    winreg = ModuleType("winreg")
    winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WRITE, winreg.KEY_WOW64_64KEY, winreg.REG_SZ = 1, 2, 4, 5

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    winreg.CreateKeyEx = lambda root, path, reserved, access: Key(path)
    winreg.SetValueEx = lambda key, name, reserved, kind, value: registry.__setitem__((key.path, name), value)
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    client = ModuleType("win32com.client")

    class Shortcut:
        def __init__(self, path):
            self.path = path

        def Save(self):
            shortcuts[self.path] = self.TargetPath

    client.Dispatch = lambda _: SimpleNamespace(CreateShortcut=Shortcut)
    com = ModuleType("win32com")
    com.client = client
    monkeypatch.setitem(sys.modules, "win32com", com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "programdata"))
    platform = WindowsPlatform(tmp_path / "data")
    for version in ("0.4.0", "0.3.0"):
        target = tmp_path / "install/versions" / version
        platform.configure_presentation(target)
        assert registry[(r"Software\SmdHmi", "Version")] == version
        assert registry[(r"Software\SmdHmi", "InstallDir")] == str(tmp_path / "install")
        assert list(shortcuts.values()) == [str(target / "SmdDesktop/SmdDesktop.exe")]
        assert registry[(r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi", "DisplayVersion")] == version
