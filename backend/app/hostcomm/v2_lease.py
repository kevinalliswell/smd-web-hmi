"""Session-scoped lease evidence; old replies cannot acquire or revoke a newer lease."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LeaseToken:
    session_id: str | None
    boot_id: str | None
    lease_id: str | None
    generation: int


class LeaseContext:
    def __init__(self, controller_id: str):
        self.controller_id = controller_id
        self.generation = 0
        self.reset()

    def reset(self, session_id: str | None = None, boot_id: str | None = None) -> None:
        self.session_id, self.boot_id = session_id, boot_id
        self.lease_id = None
        self.expires_at = 0.0
        self.minimum_revision = 0
        self.status_revision = 0
        self.observed_order = (-1, -1)
        self.renewals_paused = False
        self.revoked = False
        self.generation += 1

    def token(self) -> LeaseToken:
        return LeaseToken(self.session_id, self.boot_id, self.lease_id, self.generation)

    def clear(self, token: LeaseToken) -> bool:
        if token != self.token():
            return False
        self.lease_id = None
        self.expires_at = 0.0
        self.generation += 1
        self.renewals_paused = False
        return True

    def expire(self, now: float) -> bool:
        if self.lease_id is not None and self.expires_at <= now:
            self.clear(self.token())
            self.revoked = True
        return self.revoked

    def _same_connection(self, frame: dict) -> bool:
        return frame["session_id"] == self.session_id and frame["boot_id"] == self.boot_id

    @staticmethod
    def _deadline(frame: dict, started: float) -> float:
        remaining = int(frame["payload"]["lease_expires_uptime_ms"] or "0") - int(frame["uptime_ms"])
        if not 0 < remaining <= 8000:
            raise ValueError("Lease lifetime must be positive and at most eight seconds")
        # The peer generated its timestamp after request start. Subtracting the
        # entire round trip is conservative, unlike extending from receipt time.
        return started + remaining / 1000

    def observe_status(self, frame: dict, *, started: float, now: float) -> bool:
        if not self._same_connection(frame) or self.expire(now):
            return False
        payload = frame["payload"]
        order = (int(payload["run"]["state_revision"]), int(frame["uptime_ms"]))
        if order < self.observed_order or order[0] < self.minimum_revision:
            return False
        self.minimum_revision = max(self.minimum_revision, order[0])
        self.observed_order = order
        self.status_revision = order[0]
        owned = bool(
            payload["lease_id"]
            and payload["lease_owner_controller_id"] == self.controller_id
            and payload["lease_owner_session_id"] == self.session_id
        )
        if self.lease_id is not None and (not owned or payload["lease_id"] != self.lease_id):
            self.clear(self.token())
        if owned and self.lease_id == payload["lease_id"]:
            self.expires_at = self._deadline(frame, started)
        return True

    def confirm(self, frame: dict, token: LeaseToken, lease_id: str | None, *, started: float, now: float) -> bool:
        if self.expire(now) or token != self.token() or not self._same_connection(frame) or self.renewals_paused:
            return False
        payload = frame["payload"]
        order = (int(payload["run"]["state_revision"]), int(frame["uptime_ms"]))
        if (
            order[0] < self.minimum_revision
            or not lease_id
            or payload["lease_id"] != lease_id
            or payload["lease_owner_controller_id"] != self.controller_id
            or payload["lease_owner_session_id"] != self.session_id
        ):
            return False
        deadline = self._deadline(frame, started)
        if order < self.observed_order:
            # A later same-revision renewal may arrive while the status read
            # is being archived. Reuse only this already-confirmed live lease;
            # never let the older read adopt ownership or replace its deadline.
            return self.lease_id == lease_id
        if deadline <= now:
            return False
        if self.lease_id != lease_id:
            self.generation += 1
        self.lease_id, self.expires_at = lease_id, deadline
        self.minimum_revision = max(self.minimum_revision, order[0])
        self.observed_order = order
        self.status_revision = order[0]
        return True

    def heartbeat(
        self, frame: dict, token: LeaseToken, requested_lease: str | None, *, started: float, now: float
    ) -> None:
        if not self._same_connection(frame):
            return
        if self.expire(now):
            raise ValueError("Local lease context expired; a new session is required")
        payload = frame["payload"]
        self.minimum_revision = max(self.minimum_revision, int(payload["state_revision"]))
        if requested_lease is None or token != self.token():
            return  # Connectivity-only or obsolete context never changes ownership.
        if payload["lease_id"] != requested_lease:
            raise ValueError("Heartbeat did not confirm the requested lease")
        deadline = self._deadline(frame, started)
        if deadline <= now:
            raise ValueError("Renewal was already expired when received")
        order = (int(payload["state_revision"]), int(frame["uptime_ms"]))
        if order < self.observed_order:
            return
        self.observed_order = order
        self.expires_at = deadline

    def evidence(self, now: float) -> dict:
        self.expire(now)
        return {
            "session_id": self.session_id,
            "boot_id": self.boot_id,
            "lease_id": self.lease_id,
            "minimum_state_revision": self.minimum_revision,
            "valid": bool(self.lease_id and self.expires_at > now and not self.renewals_paused),
            "renewals_paused": self.renewals_paused,
            "expires_monotonic": self.expires_at,
        }
