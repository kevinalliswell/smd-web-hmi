import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.api.routes.experiment_review import MetadataRequest, update_metadata
from app.db.models import EventLog, TestSession
from app.services.experiment_metadata import SpecimenMetadata, validate_metadata


def test_invalid_preparation_or_freeform_fields_rejected():
    for data in (
        {"h1_mm": 4, "h2_mm": 6},
        {"particle_min_mm": 12, "particle_max_mm": 10},
        {"sample_mass_g": float("nan")},
        {"io": "CO_ON"},
    ):
        with pytest.raises(ValidationError):
            SpecimenMetadata.model_validate(data)
    assert validate_metadata(None, None) == ({}, {})


async def test_missing_height_is_audited_and_existing_height_cannot_be_overwritten(db_session):
    row = TestSession(test_id="SAMPLE-1", operator_id="op", start_time="2026-09-05T00:00:00Z")
    db_session.add(row)
    await db_session.commit()
    result = await update_metadata(
        "SAMPLE-1",
        MetadataRequest(sample_metadata={"h1_mm": 15, "h2_mm": 5}, reason="按原始装样记录补录"),
        CurrentUser("op", "operator"),
        db_session,
    )
    assert result["data"]["original_height_mm"] == 10
    audit = await db_session.scalar(select(EventLog).where(EventLog.event_code == "METADATA_REVISED"))
    assert json.loads(audit.detail_json)["old_height"] is None
    with pytest.raises(HTTPException) as error:
        await update_metadata(
            "SAMPLE-1",
            MetadataRequest(sample_metadata={"h1_mm": 25}, reason="不一致高度"),
            CurrentUser("op", "operator"),
            db_session,
        )
    assert error.value.status_code == 409
