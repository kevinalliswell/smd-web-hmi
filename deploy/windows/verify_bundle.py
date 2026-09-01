"""Validate the assembled offline bundle before publishing it."""

from __future__ import annotations

import argparse
from pathlib import Path

REQUIRED = (
    "app/backend/app/main.py",
    "app/backend/alembic.ini",
    "app/backend/requirements.lock",
    "app/frontend_dist/index.html",
    "wheels",
    "install.bat",
    "upgrade.bat",
    "run_server.bat",
    "start_server.ps1",
    "register_autostart.ps1",
    "unregister_autostart.ps1",
    "health_check.ps1",
    "backup_database.py",
    ".env.example",
    "CHANGELOG.md",
    "README.md",
)


def verify(root: Path) -> None:
    missing = [item for item in REQUIRED if not (root / item).exists()]
    if missing:
        raise SystemExit(f"bundle is missing required entries: {', '.join(missing)}")
    if not any((root / "wheels").glob("*.whl")):
        raise SystemExit("bundle contains no Windows wheels")
    if (root / "app/backend/.env").exists():
        raise SystemExit("bundle must not contain a production .env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    verify(args.root.resolve())


if __name__ == "__main__":
    main()
