import json

from app.api.routes.analytics import RepeatabilityRequest, repeatability
from app.db.models import TestSession


async def test_mismatched_or_incomplete_runs_do_not_produce_average(db_session):
    for i in (1, 2):
        db_session.add(
            TestSession(
                test_id=f"R{i}",
                operator_id="op",
                start_time="2026-09-05T00:00:00Z",
                measurement_basis_json=json.dumps({"sample_metadata": {"batch": f"batch{i}"}}),
                recipe_snapshot_json=json.dumps({"digest": "abc"}),
            )
        )
    await db_session.commit()
    result = (await repeatability(RepeatabilityRequest(test_ids=["R1", "R2"]), db_session))["data"]
    assert not result["eligible"] and not result["results"]
    assert "sample_or_recipe_mismatch" in result["errors"]
    assert "R1:experiment_not_validated" in result["errors"]


async def test_repeated_identifier_cannot_count_as_repeated_experiment(db_session):
    result = (await repeatability(RepeatabilityRequest(test_ids=["R1", "R1"]), db_session))["data"]
    assert "duplicate_test_id" in result["errors"] and not result["results"]
