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
