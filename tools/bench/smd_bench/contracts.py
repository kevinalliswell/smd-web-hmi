"""Strict local driver inputs; these are never HostComm or production HTTP commands."""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def validate_run_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("run_id must be 32 lowercase hexadecimal characters")
    return value


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    schema_version: Literal[1]
    platform: Literal["windows-x64"]
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    compatibility: dict


class DriverAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    action: Literal[
        "seed_unknown_run",
        "snapshot",
        "sample",
        "first_drip",
        "finish_measurement",
        "complete_purge",
        "complete_cooling",
        "raise_alarm",
        "clear_alarm",
        "reboot",
        "disconnect",
        "drop_reply",
        "half_recipe",
        "log_gap",
        "shutdown",
    ]
    values: dict[str, int | bool | dict] = Field(default_factory=dict, max_length=30)
    valid: bool = True
    code: str | None = Field(default=None, max_length=80)
    alarm_id: str | None = Field(default=None, max_length=80)
    command: Literal["start_run", "stop_run", "activate_recipe", "ack_alarm", "acquire_lease"] | None = None
    sequences: list[str] = Field(default_factory=list, max_length=256)


class DriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage: Path
    pairing_file: Path
    port: int = Field(default=34212, ge=1024, le=65535)
    wrong_psk: bool = False
