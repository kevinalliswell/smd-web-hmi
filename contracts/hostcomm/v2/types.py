"""Portable wire primitives. Constraints are representation limits, not equipment limits."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

SAFE_INTEGER = 9007199254740991
MAX_FRAME_BYTES = 8192  # Excludes the one LF delimiter.
MAX_BLOB_BYTES = 65536
MAX_CHUNK_BYTES = 1536


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


def checked_u64(value: str) -> str:
    if int(value) > 18446744073709551615:
        raise ValueError("uint64 overflow")
    return value


def checked_utc(value: str) -> str:
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


Identifier = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
U64 = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,19})$"), AfterValidator(checked_u64)]
UtcMillis = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"),
    AfterValidator(checked_utc),
]
SafeInt = Annotated[int, Field(ge=-SAFE_INTEGER, le=SAFE_INTEGER)]
UInt = Annotated[int, Field(ge=0, le=SAFE_INTEGER)]
PositiveInt = Annotated[int, Field(gt=0, le=SAFE_INTEGER)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class SampleRef(StrictModel):
    boot_id: Identifier
    sample_seq: U64
