#!/usr/bin/env python3
"""Verify checked-in HostComm 2.0 schemas and frozen golden vectors without device access."""

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from contracts.hostcomm.v2.codec import (  # noqa: E402
    FrameDecoder,
    canonical_bytes,
    encode_message,
    validate_log_transfer,
    validate_recipe_bytes,
)
from contracts.hostcomm.v2.messages import LOG_RECORD_ADAPTER, MESSAGE_ADAPTER, PAYLOADS  # noqa: E402
from contracts.hostcomm.v2.recipe import Recipe  # noqa: E402

CONTRACT = ROOT / "contracts" / "hostcomm" / "v2"


def schema_files() -> dict[str, str]:
    schemas = {
        "message.schema.json": MESSAGE_ADAPTER.json_schema(),
        "recipe.schema.json": Recipe.model_json_schema(),
        "log-record.schema.json": LOG_RECORD_ADAPTER.json_schema(),
    }
    return {
        name: json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:smd:hostcomm:2.0:" + name,
                **schema,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
        for name, schema in schemas.items()
    }


def verify_vectors() -> dict:
    vectors = json.loads((CONTRACT / "vectors.json").read_text(encoding="utf-8"))
    recipe = vectors["recipe"]
    raw = recipe["canonical_utf8"].encode("utf-8")
    if canonical_bytes(recipe["value"]) != raw or hashlib.sha256(raw).hexdigest() != recipe["sha256"]:
        raise ValueError("frozen recipe canonical bytes/hash mismatch")
    validate_recipe_bytes(raw)
    covered = set()
    for vector in vectors["valid_messages"]:
        wire = vector["wire_utf8"].encode("utf-8")
        if encode_message(vector["value"]) != wire or hashlib.sha256(wire).hexdigest() != vector["sha256"]:
            raise ValueError("frozen message bytes/hash mismatch: " + vector["name"])
        decoder = FrameDecoder()
        decoded = decoder.feed(wire)
        if len(decoded) != 1 or decoder.error_count:
            raise ValueError("valid message rejected: " + vector["name"])
        covered.add(decoded[0].type)
    if covered != set(PAYLOADS):
        raise ValueError("golden vectors do not cover every declared message type")
    for vector in vectors["invalid_frames"]:
        decoder = FrameDecoder()
        if decoder.feed(base64.b64decode(vector["raw_b64"])) or decoder.error_count != 1:
            raise ValueError("invalid frame accepted or incorrectly counted: " + vector["name"])
    transfer = vectors["log_transfer"]
    validate_log_transfer(transfer["request"], transfer["chunks"], transfer["result"])
    return {
        "message_types": len(covered),
        "valid_vectors": len(vectors["valid_messages"]),
        "invalid_vectors": len(vectors["invalid_frames"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-schemas", action="store_true", help="Explicitly regenerate schemas after reviewed model changes"
    )
    args = parser.parse_args()
    for name, expected in schema_files().items():
        path = CONTRACT / name
        if args.write_schemas:
            path.write_text(expected, encoding="utf-8")
        elif not path.exists() or path.read_text(encoding="utf-8") != expected:
            raise SystemExit("Schema drift: " + str(path.relative_to(ROOT)))
    print(json.dumps({"status": "pass", **verify_vectors()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
