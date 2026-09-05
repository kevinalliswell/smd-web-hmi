import json

import pytest
from sqlalchemy import select

from app.db.models import OperatorAction
from app.services.control_ownership import assert_control_owner, change_owner, ownership_snapshot
from app.services.operations import OperationError


async def test_first_claim_is_persistent_and_other_operator_blocked(db_session):
    await assert_control_owner(db_session, "alice", "operator", "start_test")
    with pytest.raises(OperationError, match="control_owned"):
        await assert_control_owner(db_session, "bob", "operator", "set_parameters")
    assert (await ownership_snapshot(db_session))["username"] == "alice"
    await assert_control_owner(db_session, "bob", "operator", "stop_test")
    assert (await ownership_snapshot(db_session))["username"] == "alice"


async def test_admin_takeover_requires_reason_and_retains_audit(db_session):
    await change_owner(db_session, "alice", "operator")
    with pytest.raises(OperationError):
        await change_owner(db_session, "admin", "admin", takeover=True)
    await change_owner(db_session, "admin", "admin", takeover=True, reason="值班交接")
    await change_owner(db_session, "admin", "admin", release=True)
    rows = (await db_session.scalars(select(OperatorAction).order_by(OperatorAction.id))).all()
    assert len(rows) == 3
    assert json.loads(rows[1].params_json) == {"from": "alice", "to": "admin", "reason": "值班交接"}
    assert (await ownership_snapshot(db_session))["username"] is None
