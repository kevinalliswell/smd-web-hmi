"""Reference validation/serialization only; no sockets, device I/O, or application imports."""

import base64
import hashlib
import json
from typing import Any

from .messages import MESSAGE_ADAPTER
from .recipe import Recipe
from .types import MAX_BLOB_BYTES, MAX_CHUNK_BYTES, MAX_FRAME_BYTES, SAFE_INTEGER, StrictModel


def _integer(token: str) -> int:
    if token == "-0" or len(token) > 17:
        raise ValueError("nonportable integer spelling or length")
    value = int(token)
    if abs(value) > SAFE_INTEGER:
        raise ValueError("integer outside exact JSON safe range; use uint64 decimal string")
    return value


def _no_float(token: str):
    raise ValueError("floating-point, exponent, and nonfinite tokens are forbidden")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _depth_limit(text: str) -> None:
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 16:
                raise ValueError("JSON nesting exceeds 16")
        elif char in "]}":
            depth -= 1


def _portable(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("JSON nesting exceeds 16")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > SAFE_INTEGER:
            raise ValueError("integer outside exact JSON safe range")
        return
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")  # Reject unpaired surrogate codepoints.
        return
    if isinstance(value, list):
        for item in value:
            _portable(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128 or any(not 32 <= ord(c) < 127 for c in key):
                raise ValueError("object keys must be printable ASCII, 1..128 characters")
            _portable(item, depth + 1)
        return
    raise ValueError("only portable JSON primitives are allowed")


def strict_loads(raw: bytes, *, limit: int = MAX_FRAME_BYTES) -> Any:
    if len(raw) > limit or not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("empty, over-limit, or BOM-prefixed JSON")
    text = raw.decode("utf-8", errors="strict")
    _depth_limit(text)
    value = json.loads(
        text, object_pairs_hook=_pairs, parse_int=_integer, parse_float=_no_float, parse_constant=_no_float
    )
    _portable(value)
    return value


def canonical_bytes(value: Any) -> bytes:
    """RFC8785 subset: ASCII keys, safe integers, valid Unicode, no floats or normalization."""
    if isinstance(value, StrictModel):
        value = value.model_dump(mode="python")
    _portable(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def command_digest(payload: dict) -> str:
    return digest({key: value for key, value in payload.items() if key != "request_digest"})


def decode_chunk(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid standard padded base64") from exc
    if not 1 <= len(raw) <= MAX_CHUNK_BYTES or base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("noncanonical or over-limit base64 chunk")
    return raw


def validate_message(value: dict):
    _portable(value)
    message = MESSAGE_ADAPTER.validate_python(value)
    payload = message.payload.model_dump(mode="python")
    if message.type == "command" and command_digest(payload) != payload["request_digest"]:
        raise ValueError("command request_digest mismatch")
    if (
        message.type == "profile_snapshot"
        and digest({k: v for k, v in payload.items() if k != "profile_digest"}) != payload["profile_digest"]
    ):
        raise ValueError("profile_digest mismatch")
    if message.type in {"recipe_chunk", "recipe_snapshot", "log_chunk"}:
        raw = decode_chunk(payload["data_b64"])
        if message.type == "log_chunk" and hashlib.sha256(raw).hexdigest() != payload["chunk_digest"]:
            raise ValueError("log chunk_digest mismatch")
        if message.type == "recipe_chunk" and payload["offset"] + len(raw) > MAX_BLOB_BYTES:
            raise ValueError("recipe chunk exceeds artifact limit")
        if message.type == "recipe_snapshot" and payload["offset"] + len(raw) > payload["byte_length"]:
            raise ValueError("recipe readback chunk exceeds artifact length")
    return message


def encode_message(value: dict) -> bytes:
    validate_message(value)
    raw = canonical_bytes(value)
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("frame exceeds 8192 bytes excluding LF")
    return raw + b"\n"


class FrameDecoder:
    """Bounded test/reference decoder; an oversized line is discarded through its LF."""

    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.error_count = 0

    def feed(self, data: bytes) -> list:
        messages = []
        for byte in data:
            if self.discarding:
                if byte == 10:
                    self.discarding = False
                continue
            if byte == 10:
                try:
                    if b"\r" in self.buffer:
                        raise ValueError("CRLF and unescaped CR are forbidden")
                    messages.append(validate_message(strict_loads(bytes(self.buffer))))
                except (ValueError, UnicodeError, RecursionError):
                    self.error_count += 1
                self.buffer.clear()
            elif len(self.buffer) == MAX_FRAME_BYTES:
                self.buffer.clear()
                self.discarding = True
                self.error_count += 1
            else:
                self.buffer.append(byte)
        return messages


def validate_recipe_bytes(raw: bytes) -> Recipe:
    value = strict_loads(raw, limit=MAX_BLOB_BYTES)
    recipe = Recipe.model_validate(value)
    if canonical_bytes(recipe) != raw:
        raise ValueError("recipe artifact must be exact canonical bytes, without LF")
    return recipe


def split_chunks(raw: bytes) -> list[str]:
    if not 1 <= len(raw) <= MAX_BLOB_BYTES:
        raise ValueError("recipe artifact length outside 1..65536")
    return [base64.b64encode(raw[i : i + MAX_CHUNK_BYTES]).decode("ascii") for i in range(0, len(raw), MAX_CHUNK_BYTES)]


class RecipeAssembler:
    """Ephemeral reference transfer, without activation or durable-storage claims."""

    def __init__(self, begin: dict):
        from .messages import RecipeBegin

        self.begin = RecipeBegin.model_validate(begin)
        self.data = bytearray()
        self.aborted = False

    def add(self, chunk: dict) -> Recipe | None:
        if self.aborted:
            raise ValueError("transfer is aborted; create a new transfer")
        try:
            return self._add(chunk)
        except (ValueError, UnicodeError):
            self.aborted = True
            raise

    def _add(self, chunk: dict) -> Recipe | None:
        from .messages import RecipeChunk

        part = RecipeChunk.model_validate(chunk)
        if part.transfer_id != self.begin.transfer_id:
            raise ValueError("transfer identity mismatch")
        raw = decode_chunk(part.data_b64)
        end = part.offset + len(raw)
        if end > self.begin.byte_length:
            raise ValueError("chunk exceeds declared artifact length")
        if part.offset < len(self.data):
            if end > len(self.data) or bytes(self.data[part.offset : end]) != raw:
                raise ValueError("duplicate/overlapping chunk differs from retained bytes")
        elif part.offset != len(self.data):
            raise ValueError("chunk offset gap")
        else:
            self.data.extend(raw)
        if len(self.data) == self.begin.byte_length:
            raw = bytes(self.data)
            if hashlib.sha256(raw).hexdigest() != self.begin.recipe_digest:
                raise ValueError("complete recipe digest mismatch")
            return validate_recipe_bytes(raw)
        return None


def validate_log_transfer(request: dict, chunks: list[dict], result: dict) -> list:
    """Check a complete immutable transfer, including explicit coverage of every requested record."""
    from .messages import LOG_RECORD_ADAPTER, LogChunk, LogRequest, LogResult

    query = LogRequest.model_validate(request)
    terminal = LogResult.model_validate(result)
    if query.transfer_id != terminal.transfer_id or query.requested != terminal.requested:
        raise ValueError("log result does not match the fixed requested cut")
    data = bytearray()
    seen = {}
    next_index = 0
    for value in chunks:
        part = LogChunk.model_validate(value)
        raw = decode_chunk(part.data_b64)
        if (
            part.transfer_id != query.transfer_id
            or part.log_id != query.requested.log_id
            or part.snapshot_highwater != terminal.snapshot_highwater
            or hashlib.sha256(raw).hexdigest() != part.chunk_digest
        ):
            raise ValueError("log chunk identity, fixed highwater, or digest mismatch")
        if part.chunk_index in seen:
            if seen[part.chunk_index] != part:
                raise ValueError("retransmitted chunk differs from original bytes or metadata")
            continue
        if part.chunk_index != next_index or part.offset != len(data):
            raise ValueError("log chunk offset/index gap")
        if len(data) + len(raw) > query.max_bytes:
            raise ValueError("log transfer exceeds requested byte budget")
        seen[part.chunk_index] = part
        next_index += 1
        data.extend(raw)
    if len(data) != terminal.byte_length or hashlib.sha256(data).hexdigest() != terminal.content_digest:
        raise ValueError("log transfer final length or digest mismatch")
    if data and not data.endswith(b"\n"):
        raise ValueError("log transfer ends with an incomplete JSONL record")
    records = []
    previous = int(query.requested.first_record_seq) - 1
    for raw in bytes(data).splitlines(keepends=True):
        if not raw.endswith(b"\n") or raw.endswith(b"\r\n"):
            raise ValueError("log record requires one LF")
        value = strict_loads(raw[:-1])
        record = LOG_RECORD_ADAPTER.validate_python(value)
        if canonical_bytes(record) + b"\n" != raw:
            raise ValueError("log record is not canonical JSONL")
        seq = int(record.record_seq)
        if (
            record.log_id != query.requested.log_id
            or seq <= previous
            or seq < int(query.requested.first_record_seq)
            or seq > min(int(query.requested.last_record_seq), int(terminal.snapshot_highwater))
        ):
            raise ValueError("log record identity, ordering, or cut mismatch")
        previous = seq
        records.append(record)
    if len(records) != terminal.record_count or len(records) > query.max_records:
        raise ValueError("log record count does not match declared/requested count")
    intervals = [(int(record.record_seq), int(record.record_seq)) for record in records]
    intervals.extend((int(gap.first_record_seq), int(gap.last_record_seq)) for gap in terminal.missing)
    next_seq = int(query.requested.first_record_seq)
    for start, end in sorted(intervals):
        if start != next_seq:
            raise ValueError("log result has an undeclared gap or overlapping coverage")
        next_seq = end + 1
    if next_seq != int(query.requested.last_record_seq) + 1:
        raise ValueError("log result does not account for the complete fixed cut")
    return records
