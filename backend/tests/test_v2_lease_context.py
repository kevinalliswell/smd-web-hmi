import pytest

from app.hostcomm.v2_lease import LeaseContext
from tests.test_hostcomm_v2_transport import example


def status(lease="a" * 32, revision="12", uptime="12345", expiry="20000"):
    frame = example("status_snapshot")
    frame["uptime_ms"] = uptime
    frame["payload"]["run"]["state_revision"] = revision
    frame["payload"].update(
        lease_id=lease,
        lease_expires_uptime_ms=expiry if lease else None,
        lease_owner_controller_id="c" * 32 if lease else None,
        lease_owner_session_id=frame["session_id"] if lease else None,
    )
    return frame


def context():
    frame = status()
    lease = LeaseContext("c" * 32)
    lease.reset(frame["session_id"], frame["boot_id"])
    return lease


def test_status_receipt_cannot_extend_lease_by_network_delay():
    lease = context()
    assert not lease.confirm(status(), lease.token(), "a" * 32, started=100, now=108)
    assert lease.lease_id is None


def test_old_acquisition_cannot_adopt_after_context_changes():
    lease = context()
    token = lease.token()
    lease.reset("d" * 32, "e" * 32)
    assert not lease.confirm(status(), token, "a" * 32, started=100, now=100)
    assert lease.lease_id is None


def test_null_heartbeat_never_adopts_clears_or_replaces_lease():
    lease = context()
    token = lease.token()
    assert lease.confirm(status(), token, "a" * 32, started=100, now=100)
    original = lease.token()
    for value in (None, "b" * 32):
        ack = status(value)
        ack["payload"] = dict(lease_id=value, lease_expires_uptime_ms="20000" if value else None, state_revision="12")
        lease.heartbeat(ack, token, None, started=101, now=101)
        assert lease.token() == original


def test_newer_heartbeat_invalidates_old_phase_until_status_refresh():
    lease = context()
    lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    token = lease.token()
    ack = status(revision="13", uptime="14000", expiry="22000")
    ack["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms="22000", state_revision="13")
    lease.heartbeat(ack, token, "a" * 32, started=102, now=102)
    assert lease.minimum_revision == 13
    assert not lease.confirm(status(), token, "a" * 32, started=103, now=103)
    assert lease.expires_at == 110


def test_newer_renewal_does_not_invalidate_already_confirmed_same_revision_ownership():
    lease = context()
    assert lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    token = lease.token()
    ack = status(uptime="14000", expiry="22000")
    ack["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms="22000", state_revision="12")
    lease.heartbeat(ack, token, "a" * 32, started=102, now=102)
    deadline, order = lease.expires_at, lease.observed_order

    # A status read may finish application/database work after this later ACK.
    # It still names the owned lease and current phase, but may not regress the
    # newer renewal evidence or manufacture another lease lifetime.
    assert lease.confirm(status(), token, "a" * 32, started=100, now=103)
    assert lease.token() == token
    assert lease.expires_at == deadline
    assert lease.observed_order == order


@pytest.mark.parametrize(
    "mismatch", ["owner", "session", "boot", "lease", "revision", "expired", "generation", "unowned", "paused"]
)
def test_older_status_cannot_reuse_ownership_when_context_is_no_longer_valid(mismatch):
    lease = context()
    assert lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    token = lease.token()
    ack = status(uptime="14000", expiry="22000")
    ack["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms="22000", state_revision="12")
    lease.heartbeat(ack, token, "a" * 32, started=102, now=102)
    frame, now = status(), 103
    if mismatch == "owner":
        frame["payload"]["lease_owner_controller_id"] = "e" * 32
    elif mismatch == "session":
        frame["payload"]["lease_owner_session_id"] = "e" * 32
    elif mismatch == "boot":
        frame["boot_id"] = "e" * 32
    elif mismatch == "lease":
        frame["payload"]["lease_id"] = "e" * 32
    elif mismatch == "revision":
        frame["payload"]["run"]["state_revision"] = "11"
    elif mismatch == "expired":
        now = 110
    elif mismatch == "unowned":
        lease.clear(token)
        token = lease.token()
    elif mismatch == "paused":
        lease.renewals_paused = True
    else:
        lease.generation += 1
    assert not lease.confirm(frame, token, "a" * 32, started=100, now=now)


def test_old_heartbeat_and_release_cannot_mutate_new_generation():
    lease = context()
    lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    old = lease.token()
    lease.observe_status(status(None, revision="13"), started=101, now=101)
    lease.confirm(status("b" * 32, revision="14"), lease.token(), "b" * 32, started=102, now=102)
    ack = status()
    ack["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms="20000", state_revision="12")
    lease.heartbeat(ack, old, "a" * 32, started=100, now=103)
    assert not lease.clear(old)
    assert lease.lease_id == "b" * 32


@pytest.mark.parametrize("expiry", ["12345", "20346"])
def test_renewal_deadline_must_be_positive_and_within_eight_seconds(expiry):
    lease = context()
    lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    ack = status()
    ack["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms=expiry, state_revision="12")
    with pytest.raises(ValueError):
        lease.heartbeat(ack, lease.token(), "a" * 32, started=101, now=101)


@pytest.mark.parametrize("source", ["heartbeat", "status"])
def test_expired_context_cannot_be_revived_by_late_fresh_looking_evidence(source):
    lease = context()
    lease.confirm(status(), lease.token(), "a" * 32, started=100, now=100)
    token = lease.token()
    fresh = status(revision="13", uptime="21000", expiry="29000")
    if source == "heartbeat":
        fresh["payload"] = dict(lease_id="a" * 32, lease_expires_uptime_ms="29000", state_revision="13")
        with pytest.raises(ValueError, match="expired"):
            lease.heartbeat(fresh, token, "a" * 32, started=108, now=108)
    else:
        assert not lease.observe_status(fresh, started=108, now=108)
    assert lease.lease_id is None
    assert not lease.confirm(
        status(revision="14", uptime="22000", expiry="30000"), lease.token(), "a" * 32, started=109, now=109
    )
