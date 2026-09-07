"""Pure database evidence collection; callers own transaction and authenticated input."""

import json
import uuid

from sqlalchemy import select

from app.db.v2_models import V2RunBinding, V2RunRecovery
from app.hostcomm.protocol import now_iso


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


async def discover_run(
    db, device_id, run_id, *, boot_id, origin, run=None, observed_at=None, authoritative=False, status=None
):
    if run_id is None:
        return None
    case = await db.scalar(
        select(V2RunRecovery).where(V2RunRecovery.device_id == device_id, V2RunRecovery.run_id == run_id)
    )
    if case is None and await db.get(V2RunBinding, (device_id, run_id)) is not None:
        return None
    observed_at = observed_at or now_iso()
    if case is None:
        case = V2RunRecovery(
            id=uuid.uuid4().hex,
            device_id=device_id,
            run_id=run_id,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            review_revision=1,
            review_state="unreviewed",
            evidence_json="{}",
            replay_status="not_bound",
            replay_through_id=0,
        )
        db.add(case)
        await db.flush()
    evidence = json.loads(case.evidence_json)
    before = json_text(evidence)
    origins = evidence.setdefault("origins", [])
    if origin not in origins:
        origins.append(origin)
    boots = evidence.setdefault("boot_ids", [])
    if boot_id not in boots:
        boots.append(boot_id)
    if run is not None:
        for name in ("recipe_digest", "safety_profile_digest", "measurement_start", "measurement_end", "safe_boundary"):
            incoming = run.get(name)
            previous = evidence.get(name)
            if incoming is not None and previous is not None and incoming != previous:
                conflict = {"field": name, "previous": previous, "incoming": incoming, "boot_id": boot_id}
                if conflict not in evidence.setdefault("conflicts", []):
                    evidence["conflicts"].append(conflict)
                case.review_state = "conflict"
            elif incoming is not None:
                evidence[name] = incoming
        prior = evidence.get("latest_run")
        same_boot = prior is None or prior["boot_id"] == boot_id
        newer = prior is None or (same_boot and int(run["state_revision"]) >= int(prior["run"]["state_revision"]))
        if newer or (authoritative and not same_boot):
            candidate = {"boot_id": boot_id, "run": run, "origin": origin}
            if prior is None or any(candidate[k] != prior.get(k) for k in candidate):
                evidence["latest_run"] = {**candidate, "observed_at": observed_at}
                if status is not None:
                    evidence["latest_run"]["status"] = status
    if json_text(evidence) != before:
        case.evidence_json = json_text(evidence)
        case.review_revision += 1
    case.last_seen_at = max(case.last_seen_at, observed_at)
    return case


def summary(case):
    evidence = json.loads(case.evidence_json)
    return {
        "id": case.id,
        "device_id": case.device_id,
        "run_id": case.run_id,
        "first_seen_at": case.first_seen_at,
        "last_seen_at": case.last_seen_at,
        "review_revision": case.review_revision,
        "review_state": case.review_state,
        "test_id": case.test_id,
        "replay_status": case.replay_status,
        "replay_through_id": case.replay_through_id,
        "replay_error": case.replay_error,
        "state": evidence.get("latest_run", {}).get("run", {}).get("state"),
        "evidence": evidence,
    }
