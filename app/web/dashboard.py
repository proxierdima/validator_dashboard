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
    ValidatorStatusCurrent,
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


def bool_to_yes_no(value) -> str:
    if value in (1, True, "1", "true", "True"):
        return "Yes"
    if value in (0, False, "0", "false", "False"):
        return "No"
    return "—"


def normalize_validator_status(value: str | None) -> str:
    if not value:
        return "—"

    v = value.lower()
    if v in ("bonded", "bond_status_bonded", "active"):
        return "Bonded"
    if v in ("unbonding", "bond_status_unbonding"):
        return "Unbonding"
    if v in ("unbonded", "bond_status_unbonded", "inactive"):
        return "Unbonded"
    return value


def format_commission(rate: str | None) -> str:
    if not rate:
        return "—"
    try:
        pct = float(rate) * 100
        if pct.is_integer():
            return f"{int(pct)}%"
        return f"{pct:.2f}%"
    except Exception:
        return rate


def validator_status_reason(row) -> str:
    if row["validator_chain_status"] or row["validator_commission_rate"] or row["validator_last_seen_height"]:
        return ""
    return "No validator status data in validator_status_current. Check validator_status_collector and public REST for this network."


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
            NetworkEndpoint.url,
            EndpointCheck.status,
            EndpointCheck.http_status,
            EndpointCheck.latency_ms,
            EndpointCheck.remote_height,
            EndpointCheck.error_message,
            EndpointCheck.checked_at,
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
        .order_by(NetworkEndpoint.network_id.asc(), NetworkEndpoint.priority.asc(), NetworkEndpoint.id.asc())
    ).all()

    validator_rpc_status_map = defaultdict(list)
    validator_rpc_details_map = defaultdict(list)

    for network_id, url, status, http_status, latency_ms, remote_height, error_message, checked_at in latest_validator_rpc_rows:
        validator_rpc_status_map[network_id].append(status)
        validator_rpc_details_map[network_id].append(
            {
                "url": url,
                "status": status or "unknown",
                "http_status": http_status,
                "latency_ms": latency_ms,
                "remote_height": remote_height,
                "error_message": error_message or "",
                "checked_at": checked_at,
            }
        )

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
            ValidatorStatusCurrent.status.label("validator_chain_status"),
            ValidatorStatusCurrent.jailed.label("validator_jailed"),
            ValidatorStatusCurrent.commission_rate.label("validator_commission_rate"),
            ValidatorStatusCurrent.self_delegation_amount.label("validator_self_delegation_amount"),
            ValidatorStatusCurrent.last_seen_height.label("validator_last_seen_height"),
        )
        .join(Validator, Validator.network_id == Network.id)
        .join(NetworkStatusCurrent, NetworkStatusCurrent.network_id == Network.id, isouter=True)
        .join(ValidatorStatusCurrent, ValidatorStatusCurrent.validator_id == Validator.id, isouter=True)
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
        validator_rpc_status = collapse_status(validator_rpc_status_map.get(r["network_id"], []))
        rpc_details = validator_rpc_details_map.get(r["network_id"], [])

        items.append(
            {
                **dict(r),
                "validator_rpc_status": validator_rpc_status,
                "validator_emoji": status_emoji(r["validator_status"]),
                "validator_rpc_emoji": status_emoji(validator_rpc_status),
                "sync_emoji": status_emoji(r["sync_status"]),
                "snapshot_emoji": status_emoji(r["snapshot_status"]),
                "overall_emoji": status_emoji(r["overall_status"]),
                "validator_status_display": normalize_validator_status(r["validator_chain_status"]),
                "validator_jailed_display": bool_to_yes_no(r["validator_jailed"]),
                "validator_commission_display": format_commission(r["validator_commission_rate"]),
                "validator_self_delegation_display": r["validator_self_delegation_amount"] or "—",
                "validator_last_seen_height_display": r["validator_last_seen_height"] or "—",
                "validator_status_reason": validator_status_reason(r),
                "validator_rpc_details": rpc_details,
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


@router.get("/dashboard/rewards", response_class=HTMLResponse)
def dashboard_rewards(request: Request, db: Session = Depends(get_db)):
    try:
        rows = db.execute(
            select(
                func.coalesce(func.nullif(Network.display_name, ""), Network.name).label("network_title"),
                Validator.moniker,
                func.count().label("tx_count"),
            )
            .select_from(Validator)
            .join(Network, Network.id == Validator.network_id)
            .where(Validator.is_main == 1)
            .group_by(Network.display_name, Network.name, Validator.moniker)
            .order_by(func.count().desc(), Network.name.asc())
        ).mappings().all()
    except Exception:
        rows = []

    return templates.TemplateResponse(
        "rewards.html",
        {
            "request": request,
            "items": rows,
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
