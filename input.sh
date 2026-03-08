mkdir -p config
cat > config/posthuman_network_names.txt <<'TXT'
BeeZee
Cosmos Hub
Stargaze
Akash
Elys Network
Dungeon Chain
Secret Network
Shentu
AtomOne
Fetch.ai
KYVE
Quicksilver
Planq
Nolus
Warden
Juno
OmniFlix
Axelar
Babylon Genesis
Oraichain
ZetaChain
Union
Intento
Nibiru
Lava
Agoric
bostrom
bostrom
AssetMantle
Persistence
Bitway
Celestia
Osmosis
Axone
Aura Network
Dymension Hub
Humans.ai
Sunrise
FIRMACHAIN
Symphony
TXT

cat > app/models/public_rpc.py <<'PY'
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PublicRpcEndpoint(Base):
    __tablename__ = "public_rpc_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"), index=True)
    label: Mapped[str | None] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(500), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    is_enabled: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str | None] = mapped_column(String(100))  # chain-registry, manual, etc.
    created_at: Mapped[DateTime | None] = mapped_column(DateTime)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime)

    network = relationship("Network")
    checks = relationship(
        "PublicRpcCheck",
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class PublicRpcCheck(Base):
    __tablename__ = "public_rpc_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("public_rpc_endpoints.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    remote_height: Mapped[int | None] = mapped_column(Integer)
    chain_id_reported: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    checked_at: Mapped[DateTime] = mapped_column(DateTime, index=True)

    endpoint = relationship("PublicRpcEndpoint", back_populates="checks")
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
]
PY

cat > scripts/load_public_rpcs.py <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkEndpoint, PublicRpcEndpoint

NAMES_FILE = Path("config/posthuman_network_names.txt")
TIMEOUT = 8.0
CONCURRENCY = 30


def normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def load_names() -> list[str]:
    raw = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    names = []
    for x in raw:
        x = x.strip()
        if not x:
            continue
        names.append(x)
    return names


async def check_rpc(client: httpx.AsyncClient, url: str) -> bool:
    try:
        started = time.perf_counter()
        r = await client.get(url.rstrip("/") + "/status")
        _ = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return False
        js = r.json()
        return bool(js.get("result"))
    except Exception:
        return False


async def filter_working(urls: list[str]) -> list[str]:
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(url: str) -> tuple[str, bool]:
        async with sem:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                ok = await check_rpc(client, url)
                return url, ok

    results = await asyncio.gather(*(one(u) for u in urls))
    return [u for u, ok in results if ok]


def find_networks(db, wanted_names: list[str]) -> list[Network]:
    all_networks = db.execute(select(Network).where(Network.is_enabled == 1)).scalars().all()

    wanted_norm = {normalize(x) for x in wanted_names}
    found = []

    for n in all_networks:
        candidates = {
            normalize(n.name or ""),
            normalize(n.display_name or ""),
        }
        if candidates & wanted_norm:
            found.append(n)

    return found


def main() -> None:
    if not NAMES_FILE.exists():
        raise FileNotFoundError(f"Missing {NAMES_FILE}")

    wanted_names = load_names()
    db = SessionLocal()

    try:
        matched_networks = find_networks(db, wanted_names)
        matched_ids = {n.id for n in matched_networks}

        print(f"Matched networks: {len(matched_networks)}")

        for network in matched_networks:
            source_eps = db.execute(
                select(NetworkEndpoint)
                .where(NetworkEndpoint.network_id == network.id)
                .where(NetworkEndpoint.endpoint_type == "rpc")
                .where(NetworkEndpoint.is_enabled == 1)
                .where(NetworkEndpoint.is_public == 1)
            ).scalars().all()

            urls = [e.url for e in source_eps]
            working = asyncio.run(filter_working(urls)) if urls else []

            now = datetime.utcnow()

            for idx, url in enumerate(working, start=1):
                exists = db.execute(
                    select(PublicRpcEndpoint)
                    .where(PublicRpcEndpoint.network_id == network.id)
                    .where(PublicRpcEndpoint.url == url)
                ).scalar_one_or_none()

                if exists is None:
                    db.add(
                        PublicRpcEndpoint(
                            network_id=network.id,
                            label=f"public_rpc{idx}",
                            url=url,
                            priority=idx,
                            is_enabled=1,
                            source="chain-registry",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    exists.label = f"public_rpc{idx}"
                    exists.priority = idx
                    exists.is_enabled = 1
                    exists.updated_at = now

        db.commit()

        print("Public RPC endpoints loaded")

        print("\nMatched networks:")
        for n in sorted(matched_networks, key=lambda x: (x.display_name or x.name or "").lower()):
            print(f"  - {n.display_name or n.name} | {n.chain_id}")

        missing = []
        all_norm = set()
        for n in matched_networks:
            all_norm.add(normalize(n.name or ""))
            all_norm.add(normalize(n.display_name or ""))

        for wanted in wanted_names:
            if normalize(wanted) not in all_norm:
                missing.append(wanted)

        if missing:
            print("\nNames not matched in DB:")
            for x in missing:
                print(f"  - {x}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
PY

chmod +x scripts/load_public_rpcs.py

cat > app/collectors/public_rpc_collector.py <<'PY'
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CollectorRun, PublicRpcCheck, PublicRpcEndpoint

TIMEOUT = 8.0
CONCURRENCY = 50


async def probe_rpc(client: httpx.AsyncClient, base_url: str) -> dict:
    url = base_url.rstrip("/") + "/status"
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


async def probe_one(endpoint_id: int, url: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            result = await probe_rpc(client, url)
            result["endpoint_id"] = endpoint_id
            return result


async def run_async(endpoints: list[tuple[int, str]]) -> list[dict]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [probe_one(endpoint_id, url, semaphore) for endpoint_id, url in endpoints]
    return await asyncio.gather(*tasks)


def main() -> None:
    db = SessionLocal()
    started = datetime.now(timezone.utc)

    run = CollectorRun(
        collector_name="public_rpc_collector",
        status="running",
        started_at=started,
        items_processed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        endpoints = db.execute(
            select(PublicRpcEndpoint.id, PublicRpcEndpoint.url)
            .where(PublicRpcEndpoint.is_enabled == 1)
        ).all()

        results = asyncio.run(run_async(list(endpoints)))
        now = datetime.now(timezone.utc)

        for item in results:
            db.add(
                PublicRpcCheck(
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
        print(f"public_rpc_collector complete: {len(results)} checks")
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



cat > app/web/dashboard.py <<'PY'
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import (
    EndpointCheck,
    Event,
    Network,
    NetworkEndpoint,
    NetworkStatusCurrent,
    PublicRpcCheck,
    PublicRpcEndpoint,
    SnapshotCheck,
    SnapshotTarget,
    Validator,
)

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


def status_emoji(status: str | None) -> str:
    if status == "ok":
        return "🟢"
    if status == "warning":
        return "🟡"
    if status == "critical":
        return "🔴"
    return "⚪"


def collapse_status(statuses: list[str | None]) -> str:
    statuses = [s for s in statuses if s is not None]
    if not statuses:
        return "unknown"
    if all(s == "critical" for s in statuses):
        return "critical"
    if any(s == "critical" for s in statuses):
        return "warning"
    if any(s == "warning" for s in statuses):
        return "warning"
    if any(s == "ok" for s in statuses):
        return "ok"
    return "unknown"


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    latest_endpoint_subq = (
        select(
            EndpointCheck.endpoint_id,
            func.max(EndpointCheck.checked_at).label("max_checked_at"),
        )
        .group_by(EndpointCheck.endpoint_id)
        .subquery()
    )

    latest_validator_rpc_rows = db.execute(
        select(
            NetworkEndpoint.network_id,
            EndpointCheck.status,
        )
        .join(
            latest_endpoint_subq,
            latest_endpoint_subq.c.endpoint_id == NetworkEndpoint.id,
            isouter=True,
        )
        .join(
            EndpointCheck,
            (EndpointCheck.endpoint_id == NetworkEndpoint.id)
            & (EndpointCheck.checked_at == latest_endpoint_subq.c.max_checked_at),
            isouter=True,
        )
        .where(NetworkEndpoint.endpoint_type == "rpc")
        .where(NetworkEndpoint.is_enabled == 1)
        .where(NetworkEndpoint.is_public == 0)
    ).all()

    validator_rpc_map = defaultdict(list)
    for network_id, status in latest_validator_rpc_rows:
        validator_rpc_map[network_id].append(status)

    rows = db.execute(
        select(
            Network.id.label("network_id"),
            Network.name,
            Network.display_name,
            Validator.moniker,
            Validator.operator_address,
            NetworkStatusCurrent.validator_status,
            NetworkStatusCurrent.sync_status,
            NetworkStatusCurrent.snapshot_status,
            NetworkStatusCurrent.overall_status,
            NetworkStatusCurrent.local_height,
            NetworkStatusCurrent.reference_height,
            NetworkStatusCurrent.sync_diff,
            NetworkStatusCurrent.active_alerts_count,
            NetworkStatusCurrent.last_updated_at,
        )
        .join(Validator, Validator.network_id == Network.id)
        .join(NetworkStatusCurrent, NetworkStatusCurrent.network_id == Network.id, isouter=True)
        .where(Network.is_enabled == 1)
        .where(Validator.is_enabled == 1)
        .where(Validator.is_main == 1)
        .order_by(
            func.coalesce(NetworkStatusCurrent.active_alerts_count, 0).desc(),
            Network.name.asc(),
        )
    ).mappings().all()

    items = []
    for r in rows:
        validator_rpc_status = collapse_status(validator_rpc_map.get(r["network_id"], []))

        items.append(
            {
                **dict(r),
                "validator_rpc_status": validator_rpc_status,
                "validator_emoji": status_emoji(r["validator_status"]),
                "validator_rpc_emoji": status_emoji(validator_rpc_status),
                "sync_emoji": status_emoji(r["sync_status"]),
                "snapshot_emoji": status_emoji(r["snapshot_status"]),
                "overall_emoji": status_emoji(r["overall_status"]),
            }
        )

    totals = {
        "networks": len(items),
        "critical": sum(1 for x in items if x["overall_status"] == "critical"),
        "warning": sum(1 for x in items if x["overall_status"] == "warning"),
        "ok": sum(1 for x in items if x["overall_status"] == "ok"),
        "alerts": sum((x["active_alerts_count"] or 0) for x in items),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "items": items,
            "totals": totals,
        },
    )


@router.get("/dashboard/public-rpc", response_class=HTMLResponse)
def dashboard_public_rpc(request: Request, db: Session = Depends(get_db)):
    latest_public_subq = (
        select(
            PublicRpcCheck.endpoint_id,
            func.max(PublicRpcCheck.checked_at).label("max_checked_at"),
        )
        .group_by(PublicRpcCheck.endpoint_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Network.name.label("network_name"),
            Network.display_name.label("network_display_name"),
            PublicRpcEndpoint.url,
            PublicRpcEndpoint.label,
            PublicRpcCheck.status,
            PublicRpcCheck.http_status,
            PublicRpcCheck.latency_ms,
            PublicRpcCheck.remote_height,
            PublicRpcCheck.chain_id_reported,
            PublicRpcCheck.error_message,
            PublicRpcCheck.checked_at,
        )
        .join(PublicRpcEndpoint, PublicRpcEndpoint.network_id == Network.id)
        .join(
            latest_public_subq,
            latest_public_subq.c.endpoint_id == PublicRpcEndpoint.id,
            isouter=True,
        )
        .join(
            PublicRpcCheck,
            (PublicRpcCheck.endpoint_id == PublicRpcEndpoint.id)
            & (PublicRpcCheck.checked_at == latest_public_subq.c.max_checked_at),
            isouter=True,
        )
        .order_by(Network.name.asc(), PublicRpcEndpoint.priority.asc(), PublicRpcEndpoint.url.asc())
    ).mappings().all()

    return templates.TemplateResponse(
        "public_rpc.html",
        {
            "request": request,
            "items": rows,
            "status_emoji": status_emoji,
        },
    )


@router.get("/dashboard/alerts", response_class=HTMLResponse)
def dashboard_alerts(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Event.id,
            Event.event_type,
            Event.severity,
            Event.title,
            Event.status,
            Event.first_seen_at,
            Event.last_seen_at,
            Network.name.label("network_name"),
        )
        .join(Network, Network.id == Event.network_id, isouter=True)
        .where(Event.status == "open")
        .order_by(Event.last_seen_at.desc())
        .limit(300)
    ).mappings().all()

    return templates.TemplateResponse(
        "alerts.html",
        {
            "request": request,
            "items": rows,
        },
    )


@router.get("/dashboard/snapshots", response_class=HTMLResponse)
def dashboard_snapshots(request: Request, db: Session = Depends(get_db)):
    latest_snapshot_subq = (
        select(
            SnapshotCheck.snapshot_target_id,
            func.max(SnapshotCheck.checked_at).label("max_checked_at"),
        )
        .group_by(SnapshotCheck.snapshot_target_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Network.name.label("network_name"),
            SnapshotTarget.snapshot_path,
            SnapshotCheck.file_name,
            SnapshotCheck.file_size_bytes,
            SnapshotCheck.age_seconds,
            SnapshotCheck.status,
            SnapshotCheck.checked_at,
        )
        .join(Validator, Validator.network_id == Network.id)
        .join(SnapshotTarget, SnapshotTarget.network_id == Network.id)
        .join(
            latest_snapshot_subq,
            latest_snapshot_subq.c.snapshot_target_id == SnapshotTarget.id,
            isouter=True,
        )
        .join(
            SnapshotCheck,
            (SnapshotCheck.snapshot_target_id == SnapshotTarget.id)
            & (SnapshotCheck.checked_at == latest_snapshot_subq.c.max_checked_at),
            isouter=True,
        )
        .where(Network.is_enabled == 1)
        .where(Validator.is_enabled == 1)
        .where(Validator.is_main == 1)
        .order_by(Network.name.asc())
    ).mappings().all()

    return templates.TemplateResponse(
        "snapshots.html",
        {
            "request": request,
            "items": rows,
            "status_emoji": status_emoji,
        },
    )
PY


cat > app/templates/dashboard.html <<'HTML'
{% extends "base.html" %}

{% block content %}
<div class="cards">
  <div class="card">
    <div class="label">Networks</div>
    <div class="value">{{ totals.networks }}</div>
  </div>
  <div class="card">
    <div class="label">OK</div>
    <div class="value">{{ totals.ok }}</div>
  </div>
  <div class="card">
    <div class="label">Warning</div>
    <div class="value">{{ totals.warning }}</div>
  </div>
  <div class="card">
    <div class="label">Critical</div>
    <div class="value">{{ totals.critical }}</div>
  </div>
  <div class="card">
    <div class="label">Open alerts</div>
    <div class="value">{{ totals.alerts }}</div>
  </div>
</div>

<div class="panel">
  <div class="panel-title">Networks overview</div>
  <table>
    <thead>
      <tr>
        <th>Overall</th>
        <th>Network</th>
        <th>Validator</th>
        <th>Validator RPC</th>
        <th>Sync</th>
        <th>Snapshot</th>
        <th>Local height</th>
        <th>Reference height</th>
        <th>Diff</th>
        <th>Alerts</th>
        <th>Updated</th>
      </tr>
    </thead>
    <tbody>
      {% for row in items %}
      <tr>
        <td>{{ row.overall_emoji }} {{ row.overall_status or 'unknown' }}</td>
        <td>
          <strong>{{ row.display_name or row.name }}</strong><br>
          <span class="muted">{{ row.name }}</span>
        </td>
        <td>
          {{ row.validator_emoji }} {{ row.validator_status or 'unknown' }}<br>
          <span class="muted">{{ row.moniker or '' }}</span>
        </td>
        <td>{{ row.validator_rpc_emoji }} {{ row.validator_rpc_status }}</td>
        <td>{{ row.sync_emoji }} {{ row.sync_status or 'unknown' }}</td>
        <td>{{ row.snapshot_emoji }} {{ row.snapshot_status or 'unknown' }}</td>
        <td>{{ row.local_height or '' }}</td>
        <td>{{ row.reference_height or '' }}</td>
        <td>{{ row.sync_diff if row.sync_diff is not none else '' }}</td>
        <td>{{ row.active_alerts_count or 0 }}</td>
        <td>{{ row.last_updated_at or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
HTML


cat > app/templates/public_rpc.html <<'HTML'
{% extends "base.html" %}

{% block content %}
<div class="panel">
  <div class="panel-title">Public RPC endpoints</div>
  <table>
    <thead>
      <tr>
        <th>Network</th>
        <th>Label</th>
        <th>Status</th>
        <th>URL</th>
        <th>HTTP</th>
        <th>Latency ms</th>
        <th>Height</th>
        <th>Reported chain_id</th>
        <th>Error</th>
        <th>Checked</th>
      </tr>
    </thead>
    <tbody>
      {% for row in items %}
      <tr>
        <td>{{ row.network_display_name or row.network_name }}</td>
        <td>{{ row.label or '' }}</td>
        <td>{{ status_emoji(row.status) }} {{ row.status or 'unknown' }}</td>
        <td>{{ row.url }}</td>
        <td>{{ row.http_status or '' }}</td>
        <td>{{ row.latency_ms or '' }}</td>
        <td>{{ row.remote_height or '' }}</td>
        <td>{{ row.chain_id_reported or '' }}</td>
        <td>{{ row.error_message or '' }}</td>
        <td>{{ row.checked_at or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
HTML

cat > app/templates/base.html <<'HTML'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Validator Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <link rel="stylesheet" href="/static/css/dashboard.css">
</head>
<body>
  <div class="container">
    <header class="topbar">
      <div>
        <h1>Validator Dashboard</h1>
        <p class="muted">PostHuman monitoring panel</p>
      </div>
      <nav class="nav">
        <a href="/dashboard">Overview</a>
        <a href="/dashboard/public-rpc">Public RPC</a>
        <a href="/dashboard/alerts">Alerts</a>
        <a href="/dashboard/snapshots">Snapshots</a>
      </nav>
    </header>

    {% block content %}{% endblock %}
  </div>
</body>
</html>
HTML



