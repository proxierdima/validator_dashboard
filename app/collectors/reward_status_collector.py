from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CollectorRun, Event, Network

DATA_FILE = Path(os.getenv("COMMISSION_SNAPSHOT_PATH", "commission_snapshot.json"))
MAX_AGE_HOURS = int(os.getenv("REWARD_SNAPSHOT_MAX_AGE_HOURS", "24"))


def normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def open_or_update_event(db, network_id: int, severity: str, title: str, message: str, now: datetime) -> None:
    event_key = f"reward_status:{network_id}"
    existing = db.execute(
        select(Event)
        .where(Event.event_key == event_key)
        .where(Event.status == "open")
        .limit(1)
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Event(
                network_id=network_id,
                validator_id=None,
                event_type="reward_status",
                severity=severity,
                title=title,
                message=message,
                event_key=event_key,
                status="open",
                first_seen_at=now,
                last_seen_at=now,
                resolved_at=None,
                metadata_json=None,
            )
        )
        return

    existing.severity = severity
    existing.title = title
    existing.message = message
    existing.last_seen_at = now
    existing.resolved_at = None


def resolve_event(db, network_id: int, now: datetime) -> None:
    event_key = f"reward_status:{network_id}"
    existing = db.execute(
        select(Event)
        .where(Event.event_key == event_key)
        .where(Event.status == "open")
        .limit(1)
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.status = "resolved"
    existing.last_seen_at = now
    existing.resolved_at = now


def build_snapshot_lookup(data: dict) -> dict[str, dict]:
    rows = data.get("rows") or []
    totals_by_network = data.get("totals_by_network") or {}
    lookup: dict[str, dict] = {}
    for name, total in totals_by_network.items():
        lookup[normalize(name)] = {"total": total}
    for row in rows:
        network_name = row.get("network") or row.get("chain") or row.get("name")
        if network_name:
            key = normalize(str(network_name))
            entry = lookup.setdefault(key, {})
            entry.setdefault("rows", []).append(row)
    return lookup


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)
    run = CollectorRun(
        collector_name="reward_status_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        networks = db.execute(
            select(Network).where(Network.is_enabled == 1).order_by(Network.name.asc())
        ).scalars().all()
        now = datetime.now(timezone.utc)

        if not DATA_FILE.exists():
            for network in networks:
                open_or_update_event(
                    db,
                    network.id,
                    "critical",
                    "Rewards snapshot missing",
                    f"Expected rewards snapshot file not found: {DATA_FILE}",
                    now,
                )
            run.status = "success"
            run.finished_at = now
            run.duration_ms = int((now - started).total_seconds() * 1000)
            run.items_processed = len(networks)
            db.commit()
            print("Reward status collector complete: snapshot missing")
            return

        mtime = datetime.fromtimestamp(DATA_FILE.stat().st_mtime, tz=timezone.utc)
        snapshot_age = now - mtime
        is_stale = snapshot_age > timedelta(hours=MAX_AGE_HOURS)

        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        lookup = build_snapshot_lookup(data)

        processed = 0
        for network in networks:
            aliases = {
                normalize(network.name),
                normalize(network.display_name),
                normalize(network.directory),
                normalize(network.chain_id),
            }
            matched = next((lookup[key] for key in aliases if key and key in lookup), None)

            if is_stale:
                open_or_update_event(
                    db,
                    network.id,
                    "warning",
                    "Rewards snapshot is stale",
                    f"Rewards snapshot age is {int(snapshot_age.total_seconds())} seconds, exceeding {MAX_AGE_HOURS} hours.",
                    now,
                )
            elif matched is None:
                open_or_update_event(
                    db,
                    network.id,
                    "warning",
                    "Network missing in rewards snapshot",
                    f"Network '{network.name}' was not found in {DATA_FILE.name}.",
                    now,
                )
            else:
                resolve_event(db, network.id, now)
            processed += 1

        run.status = "success"
        run.finished_at = now
        run.duration_ms = int((now - started).total_seconds() * 1000)
        run.items_processed = processed
        db.commit()
        print(f"Reward status collector complete: {processed} networks")
    except Exception as exc:
        db.rollback()
        finished = datetime.now(timezone.utc)
        run.status = "failed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(exc)[:2000]
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
