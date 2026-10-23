"""SmdRender protocol: read one snapshot, write one exclusive temporary artifact, exit."""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.report_rendering import METRIC_ROWS, _render_html, _render_pdf, _render_xlsx


def render(snapshot: dict[str, Any], output: Path) -> None:
    if snapshot["schema_version"] != 1:
        raise ValueError("unsupported report snapshot")
    renderer = {"html": _render_html, "pdf": _render_pdf, "xlsx": _render_xlsx}[snapshot["format"]]
    content = renderer(
        SimpleNamespace(**snapshot["test"]),
        snapshot["metrics"],
        snapshot["sample_count"],
        [SimpleNamespace(**item) for item in snapshot["alarms"]],
        SimpleNamespace(**snapshot["params"]) if snapshot["params"] else None,
    )
    with output.open("xb") as stream:
        stream.write(content.encode("utf-8") if isinstance(content, str) else content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        # Exercise the frozen dependency closure, not just argument parsing.
        test = dict(
            test_id="P2-SMOKE",
            operator_id="ci",
            start_time=None,
            end_time=None,
            end_reason=None,
            sample_label=None,
            notes=None,
            measurement_basis_json="{}",
        )
        with tempfile.TemporaryDirectory(prefix="smd-render-smoke-") as directory:
            for fmt, magic in (("html", b"<!DOCTYPE html>"), ("pdf", b"%PDF"), ("xlsx", b"PK")):
                target = Path(directory) / fmt
                render(
                    dict(
                        schema_version=1,
                        format=fmt,
                        test=test,
                        metrics={key: None for _, key, _, _ in METRIC_ROWS},
                        sample_count=0,
                        alarms=[],
                        params=None,
                    ),
                    target,
                )
                if not target.read_bytes().startswith(magic):
                    raise RuntimeError("invalid self-check artifact")
        if any(name.startswith(("app.db", "app.hostcomm", "app.core.config")) for name in sys.modules):
            raise RuntimeError("renderer imported service capabilities")
        print("SmdRender snapshot protocol 1")
        return
    if args.snapshot is None or args.output is None:
        parser.error("snapshot and output required")
    render(json.loads(args.snapshot.read_text(encoding="utf-8")), args.output)


if __name__ == "__main__":
    main()
