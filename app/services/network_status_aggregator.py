from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import (
    CollectorRun,
    EndpointCheck,
    Event,
    GovernanceProposal,
    Network,
    NetworkEndpoint,
    NetworkStatusCurrent,
    SnapshotCheck,
    SnapshotTarget,
    Validator,
    ValidatorStatusCurrent,
)

STATUS_ORDER = {
    "critical": 3,
    "warning": 2,
    "ok": 1,
    "unknown": 0,
    None: 0,
}


def worst_status(*statuses: str | None) -> str:
    current = "unknown"
    score = -1
    for s in statuses:
        s_score = STATUS_ORDER.get(s, 0)
        if s_score > score:
            current = s or "unknown"
            score = s_score
    return current


def final_overall_status(*statuses: str | None) -> str:
    normalized = [s or "unknown" for s in statuses]
    if any(s == "critical" for s in normalized):
        return "critical"
    if any(s == "warning" for s in normalized):
        return "warning"
    if all(s == "ok" for s in normalized if s != "unknown") and any(s == "unknown" for s in normalized):
        return "warning"
    if any(s == "unknown" for s in normalized):
        return "unknown"
    return "ok"


def endpoint_group_status(rows: list[object]) -> str:
    if not rows:
        return "unknown"
    statuses = [getattr(r, 'status', None) for r in rows]
    if any(s == "critical" for s in statuses):
        if all(s == "critical" for s in statuses):
            return "critical"
        return "warning"
    if any(s == "warning" for s in statuses):
        return "warning"
    if any(s == "ok" for s in statuses):
        return "ok"
    return "unknown"


def validator_status_from_row(row: ValidatorStatusCurrent | None) -> str:
    if row is None:
        return "unknown"
    if row.jailed == 1:
        return "critical"
    if row.in_active_set == 1 and row.status in ("bonded", "BOND_STATUS_BONDED", "active"):
        return "ok"
    if row.status:
        return "warning"
    return "unknown"


def sync_status_from_heights(local_height: int | None, reference_height: int | None):
    if local_height is None or reference_height is None:
        return "unknown", None

    diff = reference_height - local_height
    if diff <= 5:
        return "ok", diff
    if diff <= 50:
        return "warning", diff
    return "critical", diff


def governance_status_from_rows(rows: list[GovernanceProposal], collector_ok: bool) -> str:
    if not collector_ok:
        return "unknown"
    if not rows:
        return "ok"
    if any((row.validator_voted or 0) != 1 for row in rows):
        return "warning"
    return "ok"


def reward_status_from_events(rows: list[Event], collector_ok: bool) -> str:
    if not collector_ok:
        return "unknown"
    if not rows:
        return "ok"
    severities = [row.severity for row in rows]
    if any(s == "critical" for s in severities):
        return "critical"
    if any(s == "warning" for s in severities):
        return "warning"
    return "ok"


def latest_collector_success_map(db, collector_names: list[str]) -> dict[str, bool]:
    rows = db.execute(
        select(CollectorRun.collector_name, func.max(CollectorRun.finished_at))
        .where(CollectorRun.collector_name.in_(collector_names))
        .where(CollectorRun.status == "success")
        .group_by(CollectorRun.collector_name)
    ).all()
    result = {name: False for name in collector_names}
    for name, _ in rows:
        result[name] = True
    return result


def main() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        collector_success = latest_collector_success_map(
            db,
            ["governance_collector", "reward_status_collector"],
        )

        networks = db.execute(
            select(Network).where(Network.is_enabled == 1).order_by(Network.name.asc())
        ).scalars().all()

        network_ids = [n.id for n in networks]
        if not network_ids:
            print("No enabled networks")
            return

        latest_endpoint_subq = (
            select(
                EndpointCheck.endpoint_id,
                func.max(EndpointCheck.checked_at).label("max_checked_at"),
            )
            .group_by(EndpointCheck.endpoint_id)
            .subquery()
        )

        latest_endpoint_rows = db.execute(
            select(EndpointCheck, NetworkEndpoint)
            .join(
                latest_endpoint_subq,
                (EndpointCheck.endpoint_id == latest_endpoint_subq.c.endpoint_id)
                & (EndpointCheck.checked_at == latest_endpoint_subq.c.max_checked_at),
            )
            .join(NetworkEndpoint, NetworkEndpoint.id == EndpointCheck.endpoint_id)
            .where(NetworkEndpoint.network_id.in_(network_ids))
            .where(NetworkEndpoint.is_enabled == 1)
        ).all()

        endpoint_map: dict[int, list[EndpointCheck]] = defaultdict(list)
        validator_rpc_height_map: dict[int, list[int]] = defaultdict(list)
        public_rpc_height_map: dict[int, list[int]] = defaultdict(list)

        for check, endpoint in latest_endpoint_rows:
            endpoint_map[endpoint.network_id].append(check)
            if check.remote_height is not None and endpoint.endpoint_type == "rpc":
                if endpoint.is_public == 0:
                    validator_rpc_height_map[endpoint.network_id].append(check.remote_height)
                else:
                    public_rpc_height_map[endpoint.network_id].append(check.remote_height)

        latest_snapshot_subq = (
            select(
                SnapshotCheck.snapshot_target_id,
                func.max(SnapshotCheck.checked_at).label("max_checked_at"),
            )
            .group_by(SnapshotCheck.snapshot_target_id)
            .subquery()
        )

        latest_snapshot_rows = db.execute(
            select(SnapshotCheck, SnapshotTarget)
            .join(
                latest_snapshot_subq,
                (SnapshotCheck.snapshot_target_id == latest_snapshot_subq.c.snapshot_target_id)
                & (SnapshotCheck.checked_at == latest_snapshot_subq.c.max_checked_at),
            )
            .join(SnapshotTarget, SnapshotTarget.id == SnapshotCheck.snapshot_target_id)
            .where(SnapshotTarget.network_id.in_(network_ids))
        ).all()

        snapshot_map: dict[int, list[SnapshotCheck]] = defaultdict(list)
        for check, target in latest_snapshot_rows:
            snapshot_map[target.network_id].append(check)

        validator_rows = db.execute(
            select(ValidatorStatusCurrent, Validator)
            .join(Validator, Validator.id == ValidatorStatusCurrent.validator_id)
            .where(Validator.is_enabled == 1)
            .where(Validator.is_main == 1)
            .where(Validator.network_id.in_(network_ids))
        ).all()

        validator_map: dict[int, ValidatorStatusCurrent] = {}
        for status_row, validator in validator_rows:
            validator_map[validator.network_id] = status_row

        alert_counts = dict(
            db.execute(
                select(Event.network_id, func.count(Event.id))
                .where(Event.status == "open")
                .where(Event.network_id.in_(network_ids))
                .group_by(Event.network_id)
            ).all()
        )

        governance_rows = db.execute(
            select(GovernanceProposal)
            .where(GovernanceProposal.network_id.in_(network_ids))
            .where(GovernanceProposal.is_latest == 1)
        ).scalars().all()
        governance_map: dict[int, list[GovernanceProposal]] = defaultdict(list)
        for row in governance_rows:
            governance_map[row.network_id].append(row)

        reward_event_rows = db.execute(
            select(Event)
            .where(Event.network_id.in_(network_ids))
            .where(Event.event_type == "reward_status")
            .where(Event.status == "open")
        ).scalars().all()
        reward_event_map: dict[int, list[Event]] = defaultdict(list)
        for row in reward_event_rows:
            if row.network_id is not None:
                reward_event_map[row.network_id].append(row)

        existing_rows = db.execute(
            select(NetworkStatusCurrent).where(NetworkStatusCurrent.network_id.in_(network_ids))
        ).scalars().all()
        existing_map = {row.network_id: row for row in existing_rows}

        for network in networks:
            vrow = validator_map.get(network.id)
            validator_status = validator_status_from_row(vrow)

            eps = endpoint_map.get(network.id, [])
            endpoint_status = endpoint_group_status(eps)

            snapshots = snapshot_map.get(network.id, [])
            snapshot_status = endpoint_group_status(snapshots) if snapshots else "unknown"

            local_height = None
            if validator_rpc_height_map.get(network.id):
                local_height = max(validator_rpc_height_map[network.id])
            elif vrow and vrow.last_seen_height is not None:
                local_height = vrow.last_seen_height

            reference_height = max(public_rpc_height_map.get(network.id, []) or [0]) or None
            sync_status, sync_diff = sync_status_from_heights(local_height, reference_height)

            governance_status = governance_status_from_rows(
                governance_map.get(network.id, []),
                collector_success["governance_collector"],
            )
            reward_status = reward_status_from_events(
                reward_event_map.get(network.id, []),
                collector_success["reward_status_collector"],
            )

            overall_status = final_overall_status(
                validator_status,
                endpoint_status,
                sync_status,
                snapshot_status,
                governance_status,
                reward_status,
            )

            row = existing_map.get(network.id)
            if row is None:
                row = NetworkStatusCurrent(network_id=network.id)
                db.add(row)

            row.validator_status = validator_status
            row.endpoint_status = endpoint_status
            row.sync_status = sync_status
            row.snapshot_status = snapshot_status
            row.governance_status = governance_status
            row.reward_status = reward_status
            row.overall_status = overall_status
            row.local_height = local_height
            row.reference_height = reference_height
            row.sync_diff = sync_diff
            row.active_alerts_count = alert_counts.get(network.id, 0)
            row.last_updated_at = now

        db.commit()
        print("network_status_current updated")
    finally:
        db.close()


if __name__ == "__main__":
    main()
