"""Offline design conformance: byte boundaries, artifact identity and replay completeness."""

import base64
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contracts.hostcomm.v2.codec import (  # noqa: E402
    FrameDecoder,
    RecipeAssembler,
    canonical_bytes,
    command_digest,
    decode_chunk,
    encode_message,
    split_chunks,
    strict_loads,
    validate_log_transfer,
    validate_message,
    validate_recipe_bytes,
)
from contracts.hostcomm.v2.messages import RunStatus  # noqa: E402

VECTORS = json.loads((ROOT / "contracts/hostcomm/v2/vectors.json").read_text(encoding="utf-8"))


def example(name):
    return copy.deepcopy(next(vector["value"] for vector in VECTORS["valid_messages"] if vector["name"] == name))


@pytest.mark.parametrize("vector", VECTORS["valid_messages"], ids=lambda vector: vector["name"])
def test_frozen_messages_survive_bytewise_tcp_fragmentation(vector):
    wire = vector["wire_utf8"].encode()
    assert encode_message(vector["value"]) == wire
    assert hashlib.sha256(wire).hexdigest() == vector["sha256"]
    decoder = FrameDecoder()
    received = []
    for byte in wire:
        received.extend(decoder.feed(bytes([byte])))
    assert len(received) == 1
    assert received[0].model_dump() == vector["value"]
    assert decoder.error_count == 0


@pytest.mark.parametrize("vector", VECTORS["invalid_frames"], ids=lambda vector: vector["name"])
def test_frozen_invalid_frames_never_become_messages(vector):
    decoder = FrameDecoder()
    assert decoder.feed(base64.b64decode(vector["raw_b64"])) == []
    assert decoder.error_count == 1


def test_max_length_excludes_delimiter_and_oversize_suffix_cannot_execute():
    wire = encode_message(example("hello"))
    exactly = b" " * (8192 - len(wire) + 1) + wire
    decoder = FrameDecoder()
    assert len(exactly) == 8193
    assert len(decoder.feed(exactly)) == 1
    assert not decoder.feed(b"x" * 8193)
    assert len(decoder.buffer) <= 8192
    assert not decoder.feed(wire)  # A valid-looking suffix still belongs to the oversized line.
    assert len(decoder.feed(wire + wire)) == 2
    assert decoder.error_count == 1


@pytest.mark.parametrize("token", [b"1.0", b"1e0", b"-0", b"NaN", b"Infinity", b"9007199254740992"])
def test_numeric_lexemes_fail_before_model_coercion(token):
    with pytest.raises(ValueError):
        strict_loads(b'{"value":' + token + b"}")


def test_recipe_hash_is_frozen_and_noncanonical_spelling_is_rejected():
    vector = VECTORS["recipe"]
    raw = vector["canonical_utf8"].encode()
    assert hashlib.sha256(raw).hexdigest() == "acf41dcfb17a1e0b1e57344d498db7146ecc92df80b17ce058f99ec0a248862f"
    assert canonical_bytes(validate_recipe_bytes(raw)) == raw
    with pytest.raises(ValueError):
        validate_recipe_bytes(raw + b"\n")
    with pytest.raises(ValueError):
        validate_recipe_bytes(raw.replace(b'"version":1', b'"version":1.0'))


def test_large_recipe_uses_bounded_chunks_and_only_completes_after_whole_digest():
    recipe = copy.deepcopy(VECTORS["recipe"]["value"])
    recipe["stages"] = [{**recipe["stages"][0], "name": "炉" * 80} for _ in range(63)] + recipe["stages"][-1:]
    raw = canonical_bytes(recipe)
    assert 8192 < len(raw) <= 65536
    digest = hashlib.sha256(raw).hexdigest()
    begin = example("recipe_begin")["payload"]
    begin.update(byte_length=len(raw), recipe_digest=digest)
    assembler = RecipeAssembler(begin)
    offset = 0
    encoded_chunks = split_chunks(raw)
    for index, encoded in enumerate(encoded_chunks):
        message = example("recipe_chunk")
        message["payload"].update(offset=offset, data_b64=encoded)
        assert len(encode_message(message)) <= 8193
        raw_chunk = decode_chunk(encoded)
        assert len(raw_chunk) <= 1536
        result = assembler.add(message["payload"])
        assert (result is not None) == (index == len(encoded_chunks) - 1)
        offset += len(raw_chunk)
    assert bytes(assembler.data) == raw


def test_upload_duplicate_is_idempotent_but_gap_or_changed_overlap_is_rejected():
    raw = VECTORS["recipe"]["canonical_utf8"].encode()
    begin = example("recipe_begin")["payload"]
    assembler = RecipeAssembler(begin)
    chunk = {"transfer_id": begin["transfer_id"], "offset": 0, "data_b64": base64.b64encode(raw[:100]).decode()}
    assert assembler.add(chunk) is None
    assert assembler.add(chunk) is None
    assert len(assembler.data) == 100
    with pytest.raises(ValueError):
        assembler.add({**chunk, "offset": 101})
    with pytest.raises(ValueError):
        assembler.add({**chunk, "data_b64": base64.b64encode(b"z" * 100).decode()})
    assert assembler.aborted
    with pytest.raises(ValueError, match="aborted"):
        assembler.add(chunk)


def test_command_identity_commits_lease_precondition_and_params():
    command = example("command")
    original = command["payload"]["request_digest"]
    for field, value in (("lease_id", "f" * 32), ("expected_state_revision", "13"), ("command_seq", "3")):
        changed = copy.deepcopy(command)
        changed["payload"][field] = value
        assert command_digest(changed["payload"]) != original
        with pytest.raises(ValueError):
            validate_message(changed)


def test_stop_does_not_need_lease_but_cannot_retarget_another_boot():
    command = example("stop_run")
    assert validate_message(command).payload.lease_id is None
    command["payload"]["expected_boot_id"] = "f" * 32
    command["payload"]["request_digest"] = command_digest(command["payload"])
    with pytest.raises(ValueError):
        validate_message(command)


def test_cooling_after_reboot_preserves_original_measurement_boundaries():
    state = example("status_snapshot")["payload"]["run"]
    state.update(
        state="completed",
        outcome="invalid",
        measurement_complete=False,
        safe_complete=True,
        measurement_end={"boot_id": state["measurement_start"]["boot_id"], "sample_seq": "50"},
        safe_boundary={"boot_id": "f" * 32, "sample_seq": "2"},
    )
    assert RunStatus.model_validate(state).safe_boundary.sample_seq == "2"
    state["measurement_end"]["sample_seq"] = "40"
    with pytest.raises(ValueError):
        RunStatus.model_validate(state)


def test_invalid_first_drip_is_preserved_but_cannot_become_valid_with_bad_temperature():
    event = example("invalid_first_drip_evidence")
    assert validate_message(event).payload.is_valid is False
    event["payload"]["is_valid"] = True
    with pytest.raises(ValueError):
        validate_message(event)


def test_profile_change_invalidates_digest():
    profile = example("profile_snapshot")
    validate_message(profile)
    profile["payload"]["engineering_config_digest"] = "f" * 64
    with pytest.raises(ValueError):
        validate_message(profile)


def test_log_chunks_may_split_records_and_duplicate_chunk_is_readonly():
    transfer = copy.deepcopy(VECTORS["log_transfer"])
    raw = transfer["canonical_jsonl_utf8"].encode()
    chunks = []
    for index, offset in enumerate(range(0, len(raw), 137)):
        piece = raw[offset : offset + 137]
        chunks.append(
            {
                **transfer["chunks"][0],
                "chunk_index": index,
                "offset": offset,
                "data_b64": base64.b64encode(piece).decode(),
                "chunk_digest": hashlib.sha256(piece).hexdigest(),
            }
        )
    chunks.insert(1, copy.deepcopy(chunks[0]))
    records = validate_log_transfer(transfer["request"], chunks, transfer["result"])
    assert [record.record_seq for record in records] == ["1", "2"]


def test_log_corruption_changed_cut_and_false_empty_success_are_rejected():
    transfer = copy.deepcopy(VECTORS["log_transfer"])
    for mutation in ("corrupt", "cut", "empty"):
        changed = copy.deepcopy(transfer)
        if mutation == "corrupt":
            changed["chunks"][0]["chunk_digest"] = "0" * 64
        elif mutation == "cut":
            changed["chunks"][0]["snapshot_highwater"] = "3"
        else:
            changed["chunks"] = []
            changed["result"].update(byte_length=0, record_count=0, content_digest=hashlib.sha256(b"").hexdigest())
        with pytest.raises(ValueError):
            validate_log_transfer(changed["request"], changed["chunks"], changed["result"])


def test_missing_log_range_must_account_for_every_requested_record():
    transfer = copy.deepcopy(VECTORS["log_transfer"])
    result = transfer["result"]
    result.update(
        status="unavailable",
        byte_length=0,
        record_count=0,
        content_digest=hashlib.sha256(b"").hexdigest(),
        available_first_seq=None,
        available_last_seq=None,
        missing=[{**result["requested"], "reason": "storage_fault"}],
    )
    assert validate_log_transfer(transfer["request"], [], result) == []
    result["missing"][0]["last_record_seq"] = "1"
    with pytest.raises(ValueError):
        validate_log_transfer(transfer["request"], [], result)


def test_checked_in_schemas_match_model_generation():
    from scripts.check_hostcomm_contract import CONTRACT, schema_files, verify_vectors

    for name, expected in schema_files().items():
        assert (CONTRACT / name).read_text(encoding="utf-8") == expected
    assert verify_vectors()["message_types"] == 26


def test_measuring_cannot_omit_pinned_configuration_or_source_origin():
    status = example("status_snapshot")
    for field in ("recipe_digest", "safety_profile_digest", "measurement_start"):
        changed = copy.deepcopy(status)
        changed["payload"]["run"]["state"] = "measuring"
        changed["payload"]["run"][field] = None
        with pytest.raises(ValueError):
            validate_message(changed)


def test_first_drip_cannot_refer_to_a_different_source_boot():
    from contracts.hostcomm.v2.messages import LOG_RECORD_ADAPTER

    event = example("event")
    event["payload"]["sample"]["boot_id"] = "f" * 32
    with pytest.raises(ValueError):
        validate_message(event)
    record = {
        "record_type": "event",
        "log_id": "a" * 32,
        "record_seq": "1",
        "boot_id": event["boot_id"],
        "timestamp": event["timestamp"],
        "uptime_ms": event["uptime_ms"],
        "data": event["payload"],
    }
    with pytest.raises(ValueError):
        LOG_RECORD_ADAPTER.validate_python(record)


def test_unknown_alarm_code_cannot_masquerade_as_a_supported_device_condition():
    event = example("alarm")
    event["payload"]["code"] = "unregistered_condition"
    with pytest.raises(ValueError):
        validate_message(event)


def test_protection_alarm_cannot_be_downgraded_to_informational():
    event = example("alarm")
    event["payload"]["severity"] = "info"
    with pytest.raises(ValueError):
        validate_message(event)
