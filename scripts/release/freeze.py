"""构建三个独立入口；生成spec中的源码/数据路径与调用目录无关。"""

import argparse
import subprocess
import sys
from pathlib import Path

APPLICATIONS = (("service", "SmdService"), ("shell", "SmdDesktop"), ("updater", "SmdUpdate"))


def arguments(repo: Path, output: Path, work: Path, entry: str, name: str) -> list[str]:
    if (entry, name) not in APPLICATIONS:
        raise ValueError("unknown frozen application")
    repo, output, work = repo.resolve(), output.resolve(), work.resolve()
    args = ["--noconfirm", "--clean", "--onedir", "--name", name]
    for path in (repo / "backend", repo / "desktop"):
        args.extend(("--paths", str(path)))
    args.extend(("--distpath", str(output), "--workpath", str(work), "--specpath", str(work / "spec")))
    args.extend(("--collect-submodules", "app", "--collect-all", "webview", "--collect-all", "pythonnet"))
    for module in (
        "win32timezone",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
    ):
        args.extend(("--hidden-import", module))
    # PyInstaller resolves data sources relative to the generated spec, unlike script arguments.
    for source, destination in (("backend/alembic.ini", "."), ("backend/app/db/migrations", "app/db/migrations")):
        args.extend(("--add-data", f"{repo / source}:{destination}"))
    if entry == "shell":
        args.append("--windowed")
    args.append(str(repo / "desktop/entries" / f"{entry}.py"))
    return args


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    opts = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    for entry, name in APPLICATIONS:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", *arguments(repo, opts.output, opts.work, entry, name)], check=True
        )


if __name__ == "__main__":
    main()
