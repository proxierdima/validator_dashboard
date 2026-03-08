cat > app/collectors/endpoint_health_collector.py <<'PY'
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CollectorRun, EndpointCheck, NetworkEndpoint

RPC_PATH = "/status"
REST_PATH = "/cosmos/base/tendermint/v1beta1/node_info"

TIMEOUT = httpx.Timeout(connect=2.5, read=4.0, write=4.0, pool=4.0)
LIMITS = httpx.Limits(max_connections=200, max_keepalive_connections=100)
CONCURRENCY = 120


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


async def probe_rpc(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    url = normalize_base_url(base_url) + RPC_PATH
    started = time.perf_counter()

    try:
        r = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if r.status_code != 200:
            return {
                "status": "critical",
                "http_status": r.status_code,
                "latency_ms": latency_ms,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"HTTP {r.status_code}",
            }

        js = r.json()
        result = js.get("result") or {}
        sync_info = result.get("sync_info") or {}
        node_info = result.get("node_info") or {}

        latest_block_height = sync_info.get("latest_block_height")
        try:
            latest_block_height = int(latest_block_height) if latest_block_height is not None else None
        except Exception:
            latest_block_height = None

        return {
            "status": "ok",
            "http_status": r.status_code,
            "latency_ms": latency_ms,
            "remote_height": latest_block_height,
            "chain_id_reported": node_info.get("network"),
            "error_message": None,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "http_status": None,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": None,
            "error_message": str(e)[:1000],
        }


async def probe_rest(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    url = normalize_base_url(base_url) + REST_PATH
    started = time.perf_counter()

    try:
        r = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if r.status_code != 200:
            return {
                "status": "critical",
                "http_status": r.status_code,
                "latency_ms": latency_ms,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"HTTP {r.status_code}",
            }

        js = r.json()
        default_info = js.get("default_node_info") or {}

        return {
            "status": "ok",
            "http_status": r.status_code,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": default_info.get("network"),
            "error_message": None,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "critical",
            "http_status": None,
            "latency_ms": latency_ms,
            "remote_height": None,
            "chain_id_reported": None,
            "error_message": str(e)[:1000],
        }


async def probe_one(
    client: httpx.AsyncClient,
    endpoint_id: int,
    endpoint_type: str,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        if endpoint_type == "rpc":
            result = await probe_rpc(client, url)
        elif endpoint_type == "rest":
            result = await probe_rest(client, url)
        else:
            result = {
                "status": "warning",
                "http_status": None,
                "latency_ms": None,
                "remote_height": None,
                "chain_id_reported": None,
                "error_message": f"Unsupported endpoint_type={endpoint_type}",
            }

        result["endpoint_id"] = endpoint_id
        return result


async def run_async(endpoints: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=LIMITS,
        headers={"User-Agent": "validator-dashboard/1.0"},
    ) as client:
        tasks = [
            probe_one(client, endpoint_id, endpoint_type, url, semaphore)
            for endpoint_id, endpoint_type, url in endpoints
        ]
        return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="endpoint_health_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        endpoints = db.execute(
            select(NetworkEndpoint.id, NetworkEndpoint.endpoint_type, NetworkEndpoint.url)
            .where(NetworkEndpoint.is_enabled == 1)
            .where(NetworkEndpoint.endpoint_type.in_(["rpc", "rest"]))
        ).all()

        results = asyncio.run(run_async(list(endpoints)))
        now = datetime.now(timezone.utc)

        rows = [
            EndpointCheck(
                endpoint_id=item["endpoint_id"],
                status=item["status"],
                http_status=item["http_status"],
                latency_ms=item["latency_ms"],
                remote_height=item["remote_height"],
                chain_id_reported=item["chain_id_reported"],
                error_message=item["error_message"],
                checked_at=now,
            )
            for item in results
        ]

        db.add_all(rows)

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = len(results)

        db.commit()
        print(f"Endpoint collector complete: {len(results)} checks")
    except Exception as e:
        db.rollback()
        finished = datetime.now(timezone.utc)
        run.status = "failed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(e)[:2000]
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY


cat > app/services/network_status_aggregator.py <<'PY'
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import (
    EndpointCheck,
    Event,
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


def endpoint_group_status(rows: list[EndpointCheck]) -> str:
    if not rows:
        return "unknown"
    statuses = [r.status for r in rows]
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


def sync_status_from_heights(local_height: int | None, reference_height: int | None) -> tuple[str, int | None]:
    if local_height is None or reference_height is None:
        return "unknown", None

    diff = reference_height - local_height
    if diff <= 5:
        return "ok", diff
    if diff <= 50:
        return "warning", diff
    return "critical", diff


def main() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

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

            governance_status = "unknown"
            reward_status = "unknown"

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
PY


cat > app/models/tracked_network.py <<'PY'
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TrackedNetwork(Base):
    __tablename__ = "tracked_networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True, unique=True)

    custom_name: Mapped[str | None] = mapped_column(String(150))
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)

    use_for_validator_search: Mapped[int] = mapped_column(Integer, default=1)
    use_for_validator_rpc_checks: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

    network = relationship("Network")
PY


cat > app/models/__init__.py <<'PY'
from app.models.base import Base
from app.models.network import Network
from app.models.endpoint import NetworkEndpoint, EndpointCheck
from app.models.validator import Validator, ValidatorStatusCurrent, ValidatorStatusHistory
from app.models.snapshot import SnapshotTarget, SnapshotCheck
from app.models.event import Event
from app.models.collector_run import CollectorRun
from app.models.network_status import NetworkStatusCurrent
from app.models.public_rpc import PublicRpcEndpoint, PublicRpcCheck
from app.models.tracked_network import TrackedNetwork

__all__ = [
    "Base",
    "Network",
    "NetworkEndpoint",
    "EndpointCheck",
    "Validator",
    "ValidatorStatusCurrent",
    "ValidatorStatusHistory",
    "SnapshotTarget",
    "SnapshotCheck",
    "Event",
    "CollectorRun",
    "NetworkStatusCurrent",
    "PublicRpcEndpoint",
    "PublicRpcCheck",
    "TrackedNetwork",
]
PY


cat > scripts/load_tracked_networks.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, TrackedNetwork

NAMES_FILE = Path("config/posthuman_network_names.txt")


def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_names() -> list[str]:
    raw = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    result = []
    seen = set()

    for line in raw:
        name = line.strip()
        if not name:
            continue
        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)
        result.append(name)

    return result


def main() -> None:
    if not NAMES_FILE.exists():
        raise FileNotFoundError(f"Missing file: {NAMES_FILE}")

    names = load_names()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        networks = db.execute(
            select(Network).where(Network.is_enabled == 1)
        ).scalars().all()

        matched = []
        missing = []

        for wanted in names:
            wanted_norm = normalize(wanted)
            found = None

            for net in networks:
                if normalize(net.name or "") == wanted_norm or normalize(net.display_name or "") == wanted_norm:
                    found = net
                    break

            if not found:
                missing.append(wanted)
                continue

            row = db.execute(
                select(TrackedNetwork).where(TrackedNetwork.network_id == found.id)
            ).scalars().first()

            if row is None:
                row = TrackedNetwork(
                    network_id=found.id,
                    custom_name=wanted,
                    is_enabled=1,
                    use_for_validator_search=1,
                    use_for_validator_rpc_checks=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.custom_name = wanted
                row.is_enabled = 1
                row.use_for_validator_search = 1
                row.use_for_validator_rpc_checks = 1
                row.updated_at = now

            matched.append((wanted, found.chain_id, found.name, found.display_name))

        db.commit()

        print(f"Tracked networks loaded: {len(matched)}")

        if matched:
            print("\nMatched:")
            for wanted, chain_id, name, display_name in matched:
                print(f"  - {wanted} -> {display_name or name} | {chain_id}")

        if missing:
            print("\nNot matched:")
            for name in missing:
                print(f"  - {name}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY

chmod +x scripts/load_tracked_networks.py


cat > app/collectors/validator_status_collector.py <<'PY'
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import (
    CollectorRun,
    Network,
    NetworkEndpoint,
    TrackedNetwork,
    Validator,
    ValidatorStatusCurrent,
    ValidatorStatusHistory,
)

TARGET_MONIKER_RE = re.compile(os.getenv("TARGET_MONIKER_RE", "posthuman"), re.IGNORECASE)
TIMEOUT = float(os.getenv("VALIDATOR_COLLECTOR_TIMEOUT", "10"))
CONCURRENCY = int(os.getenv("VALIDATOR_COLLECTOR_CONCURRENCY", "25"))

REST_VALIDATORS_PATH = "/cosmos/staking/v1beta1/validators"
RPC_STATUS_PATH = "/status"


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def parse_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def validator_status_normalized(status: str | None) -> str | None:
    if not status:
        return None
    s = status.upper()
    if s in ("BOND_STATUS_BONDED", "BONDED", "ACTIVE"):
        return "bonded"
    if s in ("BOND_STATUS_UNBONDING", "UNBONDING"):
        return "unbonding"
    if s in ("BOND_STATUS_UNBONDED", "UNBONDED", "INACTIVE"):
        return "unbonded"
    return status.lower()


async def fetch_rpc_height(client: httpx.AsyncClient, rpc_url: str) -> int | None:
    try:
        r = await client.get(normalize_base_url(rpc_url) + RPC_STATUS_PATH)
        if r.status_code != 200:
            return None
        js = r.json()
        result = js.get("result") or {}
        sync_info = result.get("sync_info") or {}
        return parse_int(sync_info.get("latest_block_height"))
    except Exception:
        return None


async def fetch_all_validators_from_rest(client: httpx.AsyncClient, rest_url: str) -> list[dict[str, Any]]:
    validators: list[dict[str, Any]] = []
    next_key: str | None = None

    while True:
        params: dict[str, Any] = {
            "pagination.limit": 1000,
        }
        if next_key:
            params["pagination.key"] = next_key

        r = await client.get(
            normalize_base_url(rest_url) + REST_VALIDATORS_PATH,
            params=params,
        )
        r.raise_for_status()
        js = r.json()

        batch = js.get("validators") or []
        validators.extend(batch)

        pagination = js.get("pagination") or {}
        next_key = pagination.get("next_key")
        if not next_key:
            break

    return validators


def choose_target_validator(validators: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for v in validators:
        desc = v.get("description") or {}
        moniker = desc.get("moniker") or ""
        operator_address = v.get("operator_address") or ""

        if TARGET_MONIKER_RE.search(moniker) or TARGET_MONIKER_RE.search(operator_address):
            candidates.append(v)

    if not candidates:
        return None

    candidates.sort(key=lambda x: parse_int(x.get("tokens")) or 0, reverse=True)
    return candidates[0]


def compute_rank(validators: list[dict[str, Any]], operator_address: str | None) -> int | None:
    if not operator_address:
        return None

    sorted_validators = sorted(
        validators,
        key=lambda x: parse_int(x.get("tokens")) or 0,
        reverse=True,
    )

    for idx, v in enumerate(sorted_validators, start=1):
        if (v.get("operator_address") or "") == operator_address:
            return idx
    return None


def get_active_set_flag(status_value: str | None) -> int:
    normalized = validator_status_normalized(status_value)
    return 1 if normalized == "bonded" else 0


async def collect_one_network(
    network_id: int,
    network_name: str,
    rest_urls: list[str],
    rpc_urls: list[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            reference_height = None
            for rpc_url in rpc_urls:
                h = await fetch_rpc_height(client, rpc_url)
                if h is not None:
                    reference_height = max(reference_height or 0, h)

            last_error: str | None = None
            validators: list[dict[str, Any]] = []

            for rest_url in rest_urls:
                try:
                    validators = await fetch_all_validators_from_rest(client, rest_url)
                    if validators:
                        break
                except Exception as e:
                    last_error = str(e)

            if not validators:
                return {
                    "network_id": network_id,
                    "network_name": network_name,
                    "ok": False,
                    "error": last_error or "No validators fetched from REST",
                    "reference_height": reference_height,
                }

            target = choose_target_validator(validators)
            if target is None:
                return {
                    "network_id": network_id,
                    "network_name": network_name,
                    "ok": False,
                    "error": "Target validator not found by moniker regex",
                    "reference_height": reference_height,
                }

            operator_address = target.get("operator_address")
            desc = target.get("description") or {}
            commission = ((target.get("commission") or {}).get("commission_rates") or {})

            return {
                "network_id": network_id,
                "network_name": network_name,
                "ok": True,
                "reference_height": reference_height,
                "validator": {
                    "moniker": desc.get("moniker"),
                    "operator_address": operator_address,
                    "consensus_address": target.get("consensus_pubkey", {}).get("key"),
                    "status": validator_status_normalized(target.get("status")),
                    "in_active_set": get_active_set_flag(target.get("status")),
                    "jailed": 1 if bool(target.get("jailed")) else 0,
                    "tombstoned": 0,
                    "tokens": target.get("tokens"),
                    "delegator_shares": target.get("delegator_shares"),
                    "commission_rate": commission.get("rate"),
                    "commission_max_rate": commission.get("max_rate"),
                    "commission_max_change_rate": commission.get("max_change_rate"),
                    "min_self_delegation": target.get("min_self_delegation"),
                    "self_delegation_amount": None,
                    "rank": compute_rank(validators, operator_address),
                    "voting_power": target.get("tokens"),
                    "last_seen_height": reference_height,
                    "raw_json": target,
                },
            }


async def run_async(network_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        collect_one_network(
            network_id=item["network_id"],
            network_name=item["network_name"],
            rest_urls=item["rest_urls"],
            rpc_urls=item["rpc_urls"],
            semaphore=semaphore,
        )
        for item in network_inputs
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="validator_status_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        tracked = db.execute(
            select(TrackedNetwork, Network)
            .join(Network, Network.id == TrackedNetwork.network_id)
            .where(TrackedNetwork.is_enabled == 1)
            .where(TrackedNetwork.use_for_validator_search == 1)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).all()

        network_inputs: list[dict[str, Any]] = []

        for tracked_row, network in tracked:
            endpoints = db.execute(
                select(NetworkEndpoint)
                .where(NetworkEndpoint.network_id == network.id)
                .where(NetworkEndpoint.is_enabled == 1)
            ).scalars().all()

            rest_urls = [e.url for e in endpoints if e.endpoint_type == "rest" and e.is_public == 1]
            rpc_urls = [e.url for e in endpoints if e.endpoint_type == "rpc" and e.is_public == 0]

            if not rest_urls:
                continue

            network_inputs.append(
                {
                    "network_id": network.id,
                    "network_name": network.name,
                    "rest_urls": rest_urls,
                    "rpc_urls": rpc_urls,
                }
            )

        results = asyncio.run(run_async(network_inputs))
        now = datetime.now(timezone.utc)

        processed = 0

        for item in results:
            if not item["ok"]:
                processed += 1
                continue

            network_id = item["network_id"]
            v = item["validator"]

            validator = db.execute(
                select(Validator)
                .where(Validator.network_id == network_id)
                .where(Validator.operator_address == v["operator_address"])
            ).scalars().first()

            raw_json_str = json.dumps(v["raw_json"], ensure_ascii=False)

            if validator is None:
                validator = Validator(
                    network_id=network_id,
                    moniker=v["moniker"],
                    operator_address=v["operator_address"],
                    consensus_address=v["consensus_address"],
                    is_main=1,
                    is_enabled=1,
                    created_at=now,
                    updated_at=now,
                )
                db.add(validator)
                db.flush()
            else:
                validator.moniker = v["moniker"]
                validator.consensus_address = v["consensus_address"]
                validator.updated_at = now
                db.flush()

            current = db.execute(
                select(ValidatorStatusCurrent)
                .where(ValidatorStatusCurrent.validator_id == validator.id)
            ).scalars().first()

            if current is None:
                current = ValidatorStatusCurrent(
                    validator_id=validator.id,
                    status=v["status"],
                    in_active_set=v["in_active_set"],
                    jailed=v["jailed"],
                    tombstoned=v["tombstoned"],
                    tokens=v["tokens"],
                    delegator_shares=v["delegator_shares"],
                    commission_rate=v["commission_rate"],
                    commission_max_rate=v["commission_max_rate"],
                    commission_max_change_rate=v["commission_max_change_rate"],
                    min_self_delegation=v["min_self_delegation"],
                    self_delegation_amount=v["self_delegation_amount"],
                    rank=v["rank"],
                    voting_power=v["voting_power"],
                    last_seen_height=v["last_seen_height"],
                    last_checked_at=now,
                    raw_json=raw_json_str,
                )
                db.add(current)
            else:
                current.status = v["status"]
                current.in_active_set = v["in_active_set"]
                current.jailed = v["jailed"]
                current.tombstoned = v["tombstoned"]
                current.tokens = v["tokens"]
                current.delegator_shares = v["delegator_shares"]
                current.commission_rate = v["commission_rate"]
                current.commission_max_rate = v["commission_max_rate"]
                current.commission_max_change_rate = v["commission_max_change_rate"]
                current.min_self_delegation = v["min_self_delegation"]
                current.self_delegation_amount = v["self_delegation_amount"]
                current.rank = v["rank"]
                current.voting_power = v["voting_power"]
                current.last_seen_height = v["last_seen_height"]
                current.last_checked_at = now
                current.raw_json = raw_json_str

            db.add(
                ValidatorStatusHistory(
                    validator_id=validator.id,
                    status=v["status"],
                    in_active_set=v["in_active_set"],
                    jailed=v["jailed"],
                    tombstoned=v["tombstoned"],
                    tokens=v["tokens"],
                    commission_rate=v["commission_rate"],
                    rank=v["rank"],
                    voting_power=v["voting_power"],
                    last_seen_height=v["last_seen_height"],
                    collected_at=now,
                    raw_json=raw_json_str,
                )
            )

            processed += 1

        finished = datetime.now(timezone.utc)
        run.status = "success"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.items_processed = processed

        db.commit()
        print(f"validator_status_collector complete: {processed} networks updated")
    except Exception as e:
        db.rollback()
        finished = datetime.now(timezone.utc)
        run.status = "failed"
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY



cat > scripts/load_public_rpcs.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, PublicRpcEndpoint


def main() -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()

    try:
        networks = db.execute(
            select(Network)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).scalars().all()

        added = 0
        updated = 0

        for network in networks:
            first_rpc = db.execute(
                select(NetworkEndpoint)
                .where(NetworkEndpoint.network_id == network.id)
                .where(NetworkEndpoint.endpoint_type == "rpc")
                .where(NetworkEndpoint.is_enabled == 1)
                .where(NetworkEndpoint.is_public == 1)
                .order_by(NetworkEndpoint.priority.asc(), NetworkEndpoint.id.asc())
            ).scalars().first()

            if first_rpc is None:
                continue

            row = db.execute(
                select(PublicRpcEndpoint)
                .where(PublicRpcEndpoint.network_id == network.id)
                .order_by(PublicRpcEndpoint.id.asc())
            ).scalars().first()

            if row is None:
                db.add(
                    PublicRpcEndpoint(
                        network_id=network.id,
                        label="public_rpc1",
                        url=first_rpc.url,
                        priority=1,
                        is_enabled=1,
                        source="chain-registry",
                        created_at=now,
                        updated_at=now,
                    )
                )
                added += 1
            else:
                row.label = "public_rpc1"
                row.url = first_rpc.url
                row.priority = 1
                row.is_enabled = 1
                row.source = "chain-registry"
                row.updated_at = now
                updated += 1

        db.commit()

        print(f"Public RPC rows added: {added}")
        print(f"Public RPC rows updated: {updated}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY




