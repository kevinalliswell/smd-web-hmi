"""Bounded immutable log cuts and session-local recipe upload state."""

import base64
import hashlib
from dataclasses import dataclass

from app.hostcomm.v2_contract.codec import RecipeAssembler

from .state import DeviceError


@dataclass
class Upload:
    assembler: RecipeAssembler
    progressed_ms: int


class LogTransfer:
    def __init__(self, state, request, request_id):
        self.request, self.request_id = request, request_id
        wanted = request["requested"]
        first, last = int(wanted["first_record_seq"]), int(wanted["last_record_seq"])
        self.highwater = state.data["record_seq"]
        if wanted["log_id"] != state.data["log_id"] or last > self.highwater:
            raise DeviceError("range_unavailable")
        rows, gaps = state.store.cut(first, last)
        present = {seq for seq, _ in rows}
        self.raw = b"".join(raw for _, raw in rows)
        if len(self.raw) > request["max_bytes"]:
            raise DeviceError("range_unavailable")
        missing = []
        for seq in range(first, last + 1):
            if seq in present:
                continue
            reason = gaps.get(seq, "not_recorded")
            if missing and int(missing[-1]["last_record_seq"]) == seq - 1 and missing[-1]["reason"] == reason:
                missing[-1]["last_record_seq"] = str(seq)
            else:
                missing.append(
                    {
                        "log_id": wanted["log_id"],
                        "first_record_seq": str(seq),
                        "last_record_seq": str(seq),
                        "reason": reason,
                    }
                )
        if len(missing) > 32:
            raise DeviceError("range_unavailable")
        oldest, newest = state.store.catalog()
        self.result = {
            "transfer_id": request["transfer_id"],
            "requested": wanted,
            "snapshot_highwater": str(self.highwater),
            "available_first_seq": str(oldest) if oldest else None,
            "available_last_seq": str(newest) if newest else None,
            "status": "unavailable" if not rows else "partial" if missing else "complete",
            "byte_length": len(self.raw),
            "content_digest": hashlib.sha256(self.raw).hexdigest(),
            "record_count": len(rows),
            "missing": missing,
        }
        self.boundaries = []
        end = 0
        for seq, raw in rows:
            end += len(raw)
            self.boundaries.append((end, str(seq)))
        self.offset = self.index = self.retries = 0
        self.sent_ms = 0
        self.last_ack = None

    def chunk(self):
        if self.offset >= len(self.raw):
            return None
        raw = self.raw[self.offset : self.offset + 1536]
        return {
            "transfer_id": self.request["transfer_id"],
            "log_id": self.request["requested"]["log_id"],
            "chunk_index": self.index,
            "snapshot_highwater": str(self.highwater),
            "offset": self.offset,
            "data_b64": base64.b64encode(raw).decode(),
            "chunk_digest": hashlib.sha256(raw).hexdigest(),
        }

    def ack(self, payload):
        if self.last_ack == payload:
            return False
        part = self.chunk()
        if part is None:
            raise DeviceError("offset_mismatch")
        end = min(len(self.raw), self.offset + 1536)
        committed = next((seq for boundary, seq in reversed(self.boundaries) if boundary <= end), None)
        if any(payload[key] != part[key] for key in ("transfer_id", "log_id", "chunk_index", "chunk_digest")) or (
            payload["next_offset"] != end or payload["committed_record_seq"] != committed
        ):
            raise DeviceError("offset_mismatch")
        self.last_ack = payload.copy()
        self.offset, self.index, self.retries = end, self.index + 1, 0
        return True
