"""One archive membership rule for original rows and reviewed recovery associations."""

from sqlalchemy import or_, select

from app.db.models import AlarmLog, EventLog
from app.db.v2_models import V2RecoveryAssociation, V2RunRecovery


def recovery_log_condition(model, test_id):
    if model not in (EventLog, AlarmLog):
        raise ValueError("recovery association supports EventLog and AlarmLog only")
    column = V2RecoveryAssociation.event_log_id if model is EventLog else V2RecoveryAssociation.alarm_log_id
    associated = (
        select(column)
        .join(V2RunRecovery, V2RunRecovery.id == V2RecoveryAssociation.recovery_id)
        .where(V2RunRecovery.test_id == test_id, column.is_not(None))
    )
    return or_(model.test_id == test_id, model.id.in_(associated))
