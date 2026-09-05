"""Use the system Windows PowerShell 5.1 without inherited PS7/user modules."""

import ctypes
import os
import subprocess
from pathlib import Path


def system_directory() -> Path:
    from ctypes import wintypes

    get_directory = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW
    get_directory.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    get_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def run(arguments: list[str], **options):
    directory = system_directory() / "WindowsPowerShell/v1.0"
    environment = dict(options.pop("env", os.environ))
    for key in list(environment):
        if key.lower() in {"psmodulepath", "winpsmodulepath"}:
            del environment[key]
    environment["PSModulePath"] = str(directory / "Modules")
    return subprocess.run(
        [str(directory / "powershell.exe"), "-NoProfile", "-NonInteractive", *arguments],
        env=environment,
        **options,
    )
