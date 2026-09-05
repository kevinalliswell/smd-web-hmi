"""样品/报告输入的有限结构；不推断缺失的实验条件。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Positive = Annotated[FiniteFloat, Field(gt=0, strict=True)]
Nonnegative = Annotated[FiniteFloat, Field(ge=0, strict=True)]
Label = Annotated[str, Field(min_length=1, max_length=200)]
Note = Annotated[str, Field(max_length=2000)]


class SpecimenMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch: Label | None = None
    preparation: Note | None = None
    particle_min_mm: Positive | None = None
    particle_max_mm: Positive | None = None
    dry_temperature_c: Positive | None = None
    dry_duration_min: Positive | None = None
    sample_mass_g: Positive | None = None
    coke_upper_g: Nonnegative | None = None
    coke_lower_g: Nonnegative | None = None
    h1_mm: Positive | None = None
    h2_mm: Nonnegative | None = None
    load_kg_cm2: Positive | None = None
    sealed_leak_pressure_pa: Nonnegative | None = None
    loaded_pressure_pa: Nonnegative | None = None
    leak_n2_l_min: Positive | None = None
    preparation_confirmed: bool | None = None
    remarks: Note | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.particle_min_mm is not None and self.particle_max_mm is not None:
            if self.particle_min_mm > self.particle_max_mm:
                raise ValueError("粒度下限不能高于上限")
        if self.h1_mm is not None and self.h2_mm is not None and self.h1_mm <= self.h2_mm:
            raise ValueError("H1必须大于H2，原始料层高度H=H1-H2")
        return self

    @property
    def original_height(self):
        return self.h1_mm - self.h2_mm if self.h1_mm is not None and self.h2_mm is not None else None


class ReportContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    laboratory_name: Label | None = None
    laboratory_address: Annotated[str, Field(max_length=500)] | None = None
    test_date: date | None = None
    abnormal_operations: Note | None = None
    additional_operations: Note | None = None


def validate_metadata(specimen: dict | None, report: dict | None) -> tuple[dict, dict]:
    return (
        SpecimenMetadata.model_validate(specimen or {}).model_dump(exclude_none=True),
        ReportContext.model_validate(report or {}).model_dump(exclude_none=True, mode="json"),
    )
