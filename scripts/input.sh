mkdir -p scripts

cat > scripts/load_chain_registry.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint

CHAIN_REGISTRY_DIR = Path("./chain-registry")
GIT_URL = "https://github.com/cosmos/chain-registry.git"


def ensure_repo() -> None:
    if CHAIN_REGISTRY_DIR.exists():
        subprocess.run(
            ["git", "-C", str(CHAIN_REGISTRY_DIR), "pull", "--ff-only"],
            check=True,
        )
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", GIT_URL, str(CHAIN_REGISTRY_DIR)],
            check=True,
        )


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def first_nonempty(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def parse_endpoints(chain_json: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    apis = chain_json.get("apis") or {}

    for endpoint_type in ("rpc", "rest", "grpc"):
        items = apis.get(endpoint_type) or []
        for idx, item in enumerate(items, start=1):
            address = item.get("address")
            if not address:
                continue
            result.append(
                {
                    "endpoint_type": endpoint_type,
                    "label": f"{endpoint_type}{idx}",
                    "url": address,
                }
            )
    return result


def main() -> None:
    ensure_repo()

    db = SessionLocal()
    try:
        chain_files = sorted(CHAIN_REGISTRY_DIR.glob("*/chain.json"))

        for chain_file in chain_files:
            if chain_file.parts[-2].startswith("."):
                continue

            data = load_json(chain_file)
            if not data:
                continue

            name = first_nonempty(
                data.get("name"),
                data.get("chain_name"),
                chain_file.parent.name,
            )
            if not name:
                continue

            display_name = first_nonempty(
                data.get("pretty_name"),
                data.get("display_name"),
                name,
            )

            chain_id = data.get("chain_id")
            chain_type = "cosmos"

            fees = data.get("fees") or {}
            fee_tokens = fees.get("fee_tokens") or []
            base_denom = None
            display_denom = None
            exponent = None

            if fee_tokens:
                fee0 = fee_tokens[0]
                base_denom = fee0.get("denom")
                display_denom = first_nonempty(
                    fee0.get("display_denom"),
                    fee0.get("symbol"),
                    fee0.get("denom"),
                )
                exponent = fee0.get("fixed_min_gas_price")  # placeholder source exists sometimes
                exponent = None  # intentionally keep clean until assetlist enrichment

            network = db.execute(
                select(Network).where(Network.name == name)
            ).scalar_one_or_none()

            if network is None:
                network = Network(
                    name=name,
                    display_name=display_name,
                    chain_id=chain_id,
                    chain_type=chain_type,
                    base_denom=base_denom,
                    display_denom=display_denom,
                    exponent=exponent,
                    is_enabled=1,
                )
                db.add(network)
                db.flush()
            else:
                network.display_name = display_name
                network.chain_id = chain_id
                network.chain_type = chain_type
                network.base_denom = base_denom
                network.display_denom = display_denom
                network.is_enabled = 1
                db.flush()

            db.execute(
                delete(NetworkEndpoint).where(NetworkEndpoint.network_id == network.id)
            )

            for ep in parse_endpoints(data):
                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type=ep["endpoint_type"],
                        label=ep["label"],
                        url=ep["url"],
                        priority=1,
                        is_public=1,
                        is_enabled=1,
                    )
                )

        db.commit()
        print("Chain registry import complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY

chmod +x scripts/load_chain_registry.py


mkdir -p app/collectors

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

TIMEOUT = 8.0
CONCURRENCY = 50


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
            "error_message": str(e),
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
        app_version = js.get("application_version") or {}
        _ = app_version

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
            "error_message": str(e),
        }


async def probe_one(endpoint_id: int, endpoint_type: str, url: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
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
    tasks = [
        probe_one(endpoint_id, endpoint_type, url, semaphore)
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

        for item in results:
            db.add(
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
            )

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
        run.error_message = str(e)
        db.add(run)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY


mkdir -p app/services

cat > app/services/network_status_aggregator.py <<'PY'
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

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


def endpoint_group_status(rows: list[EndpointCheck]) -> str:
    if not rows:
        return "unknown"
    statuses = [r.status for r in rows]
    return worst_status(*statuses)


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


def snapshot_status_from_row(row: SnapshotCheck | None) -> str:
    if row is None:
        return "unknown"
    return row.status or "unknown"


def sync_status_from_endpoint_rows(rows: list[EndpointCheck]) -> tuple[str, int | None]:
    heights = [r.remote_height for r in rows if r.remote_height is not None]
    if not heights:
        return "unknown", None

    max_h = max(heights)
    min_h = min(heights)
    diff = max_h - min_h

    if diff <= 5:
        return "ok", diff
    if diff <= 50:
        return "warning", diff
    return "critical", diff


def main() -> None:
    db = SessionLocal()
    try:
        networks = db.execute(
            select(Network).where(Network.is_enabled == 1).order_by(Network.name.asc())
        ).scalars().all()

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
        ).all()

        endpoint_map: dict[int, list[EndpointCheck]] = defaultdict(list)
        ref_height_map: dict[int, list[int]] = defaultdict(list)

        for check, endpoint in latest_endpoint_rows:
            endpoint_map[endpoint.network_id].append(check)
            if check.remote_height is not None and endpoint.endpoint_type == "rpc":
                ref_height_map[endpoint.network_id].append(check.remote_height)

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
        ).all()

        snapshot_map: dict[int, list[SnapshotCheck]] = defaultdict(list)
        for check, target in latest_snapshot_rows:
            snapshot_map[target.network_id].append(check)

        validator_rows = db.execute(
            select(ValidatorStatusCurrent, Validator)
            .join(Validator, Validator.id == ValidatorStatusCurrent.validator_id)
            .where(Validator.is_enabled == 1)
            .where(Validator.is_main == 1)
        ).all()

        validator_map: dict[int, ValidatorStatusCurrent] = {}
        for status_row, validator in validator_rows:
            validator_map[validator.network_id] = status_row

        alert_counts = dict(
            db.execute(
                select(Event.network_id, func.count(Event.id))
                .where(Event.status == "open")
                .group_by(Event.network_id)
            ).all()
        )

        db.execute(delete(NetworkStatusCurrent))

        now = datetime.now(timezone.utc)

        for network in networks:
            vrow = validator_map.get(network.id)
            validator_status = validator_status_from_row(vrow)

            eps = endpoint_map.get(network.id, [])
            endpoint_status = endpoint_group_status(eps)

            sync_status, sync_diff = sync_status_from_endpoint_rows(
                [x for x in eps if x.remote_height is not None]
            )

            snapshots = snapshot_map.get(network.id, [])
            snapshot_status = worst_status(*[x.status for x in snapshots]) if snapshots else "unknown"

            governance_status = "unknown"
            reward_status = "unknown"

            overall_status = worst_status(
                validator_status,
                endpoint_status,
                sync_status,
                snapshot_status,
                governance_status,
                reward_status,
            )

            reference_height = max(ref_height_map.get(network.id, []) or [0]) or None
            local_height = vrow.last_seen_height if vrow and vrow.last_seen_height is not None else None

            if local_height is not None and reference_height is not None:
                actual_sync_diff = reference_height - local_height
                if actual_sync_diff <= 5:
                    sync_status = "ok"
                elif actual_sync_diff <= 50:
                    sync_status = "warning"
                else:
                    sync_status = "critical"
                sync_diff = actual_sync_diff

            db.add(
                NetworkStatusCurrent(
                    network_id=network.id,
                    validator_status=validator_status,
                    endpoint_status=endpoint_status,
                    sync_status=sync_status,
                    snapshot_status=snapshot_status,
                    governance_status=governance_status,
                    reward_status=reward_status,
                    overall_status=overall_status,
                    local_height=local_height,
                    reference_height=reference_height,
                    sync_diff=sync_diff,
                    active_alerts_count=alert_counts.get(network.id, 0),
                    last_updated_at=now,
                )
            )

        db.commit()
        print("network_status_current updated")
    finally:
        db.close()


if __name__ == "__main__":
    main()
PY


mkdir -p app/tasks

cat > app/tasks/run_health_cycle.py <<'PY'
from app.collectors.endpoint_health_collector import main as endpoint_main
from app.services.network_status_aggregator import main as aggregator_main


def main() -> None:
    endpoint_main()
    aggregator_main()


if __name__ == "__main__":
    main()
PY



