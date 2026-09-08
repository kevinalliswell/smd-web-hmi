"""PyInstaller console entry; source mode uses this same implementation."""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    repo = Path(__file__).resolve().parents[2]
    for folder in (repo / "backend", repo / "desktop"):
        sys.path.insert(0, str(folder))

from smd_bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
