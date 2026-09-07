"""Independent fixed numeric oracles and real downloaded document checks."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

from .package import sha256


def report_metrics(database: Path, report_id: int) -> dict:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)) as connection:
        row = connection.execute("SELECT notes FROM report_export WHERE id=?", (report_id,)).fetchone()
    if row is None:
        raise AssertionError("generated report was not committed")
    return json.loads(row[0])["metrics"]


def check_metrics(metrics: dict, scenario: str) -> None:
    if metrics["compliance"] != "not_certified":
        raise AssertionError("synthetic reports must remain not_certified")
    if scenario == "recovered_unknown":
        if metrics.get("t10") is not None or metrics.get("t40") is not None:
            raise AssertionError("recovered run invented missing original height")
        if metrics.get("td_drip_temp") != 1290 or metrics.get("td_source") != "first_drip_event":
            raise AssertionError("recovered valid first-drip evidence was changed")
        return
    if scenario in {"abort", "invalid_sample", "source_gap"}:
        if metrics["measurement_data_integrity"] == "complete" or metrics["td_drip_temp"] is not None:
            raise AssertionError("aborted or invalid measurement acquired a fabricated valid no-drip result")
        return
    expected = {
        "measurement_data_integrity": "complete",
        "reference_displacement_mm": 20,
        "t10": 890,
        "t40": 1290,
        "ts": 1290,
        "delta_p_max": 1000,
    }
    expected.update(
        {"td_drip_temp": 1290, "td_source": "first_drip_event", "delta_h_mm": 0}
        if scenario == "valid_drip"
        else {"td_drip_temp": 1580, "td_source": "completed_without_drip", "delta_h_mm": 2}
    )
    for key, value in expected.items():
        if metrics.get(key) != value:
            raise AssertionError(f"fixed report oracle differed: {key}")


def inspect_document(path: Path, test_id: str) -> dict:
    if path.suffix == ".html":
        text = path.read_text(encoding="utf-8")
        if "<html" not in text.lower():
            raise AssertionError("download is not an HTML report")
    elif path.suffix == ".pdf":
        reader = PdfReader(path)
        if not reader.pages:
            raise AssertionError("download has no PDF pages")
        text = "\n".join(page.extract_text() for page in reader.pages)
    elif path.suffix == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            text = "\n".join(
                str(value) for sheet in workbook for row in sheet.values for value in row if value is not None
            )
        finally:
            workbook.close()
    else:
        raise ValueError("unsupported report evidence format")
    if test_id not in text or "not_certified" not in text or "模拟实验" not in text:
        raise AssertionError("report omitted test identity or synthetic provenance")
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size, "format": path.suffix[1:]}
