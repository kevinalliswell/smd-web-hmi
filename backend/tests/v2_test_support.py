"""Private synthetic-key fixtures with real platform permission enforcement."""

import os
import re
import subprocess
from pathlib import Path


def private_test_directory(parent: Path) -> Path:
    """Create a fresh directory whose children inherit a restricted Windows DACL.

    chmod only changes the read-only attribute on Windows. Restrict the actual
    DACL before writing any fixture key so the production reader can validate it.
    This helper deliberately does not change or mock the production ACL check.
    """
    directory = parent / "private-pairing"
    directory.mkdir(mode=0o700)
    if os.name == "nt":
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        # SID bytes are ASCII regardless of the account name or Windows locale.
        sids = re.findall(rb"S-\d+(?:-\d+)+", result.stdout)
        if len(sids) != 1:
            raise AssertionError("Could not identify the Windows test-process SID")
        process_sid = sids[0].decode("ascii")
        subprocess.run(
            [
                "icacls.exe",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                f"*{process_sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    return directory
