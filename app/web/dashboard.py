#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_FILE = ROOT_DIR / "validator_dashboard.db"
TEMPLATES = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))

SCRIPT_PATH = ROOT_DIR / "scripts" / "commission_report_from_db.py"
SNAPSHOT_PATH = ROOT_DIR / "commission_snapshot.json"
MISSING_PATH = ROOT_DIR / "missing_networks.json"

DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

JOB_STATUS_PATH = DATA_DIR / "rewards_job_status.json"
JOB_LOCK_PATH = DATA_DIR / "rewards_job.lock"


def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def format_utc(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%H:%M %d-%m-%Y UTC")
    except Exception:
        return ts


def to_int_safe(value):
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def get_majority_vote_label(yes_votes, no_votes, abstain_votes, no_with_veto_votes):
    votes = {
        "YES": to_int_safe(yes_votes),
        "NO": to_int_safe(no_votes),
        "ABSTAIN": to_int_safe(abstain_votes),
        "NO_WITH_VETO": to_int_safe(no_with_veto_votes),
    }

    max_value = max(votes.values()) if votes else 0
    if max_value <= 0:
        return "—"

    leaders = [k for k, v in votes.items() if v == max_value]
    if len(leaders) == 1:
        return leaders[0]

    return " / ".join(leaders)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_job_status(payload: dict) -> None:
    with JOB_STATUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def read_job_status() -> dict:
    return load_json(
        JOB_STATUS_PATH,
        {
            "status": "idle",
            "last_started_at": None,
            "last_finished_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_returncode": None,
        },
    )


def run_commission_report() -> None:
    if JOB_LOCK_PATH.exists():
        return

    JOB_LOCK_PATH.write_text("running", encoding="utf-8")

    status = read_job_status()
    status["status"] = "running"
    status["last_started_at"] = utc_now_iso()
    status["last_error"] = None
    save_job_status(status)

    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
        )

        status["last_finished_at"] = utc_now_iso()
        status["last_returncode"] = result.returncode

        if result.returncode == 0:
            status["status"] = "ok"
            status["last_success_at"] = status["last_finished_at"]
            status["last_error"] = None
        else:
            status["status"] = "error"
            err_text = (result.stderr or result.stdout or "unknown error").strip()
            status["last_error"] = err_text[-4000:]

        save_job_status(status)

    except Exception as e:
        status["status"] = "error"
        status["last_finished_at"] = utc_now_iso()
        status["last_error"] = str(e)
        save_job_status(status)

    finally:
        try:
            JOB_LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass


def get_scalar(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return row[0]


def get_dashboard_totals(conn):
    totals = {
        "networks": get_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM networks
            WHERE COALESCE(is_enabled, 1) = 1
            """
        ),
        "ok": get_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM network_status_current nsc
            JOIN networks n ON n.id = nsc.network_id
            WHERE COALESCE(n.is_enabled, 1) = 1
              AND COALESCE(nsc.overall_status, 'unknown') = 'ok'
            """
        ),
        "warning": get_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM network_status_current nsc
            JOIN networks n ON n.id = nsc.network_id
            WHERE COALESCE(n.is_enabled, 1) = 1
              AND COALESCE(nsc.overall_status, 'unknown') = 'warning'
            """
        ),
        "critical": get_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM network_status_current nsc
            JOIN networks n ON n.id = nsc.network_id
            WHERE COALESCE(n.is_enabled, 1) = 1
              AND COALESCE(nsc.overall_status, 'unknown') = 'critical'
            """
        ),
        "alerts": 0,
    }
    return totals


def get_latest_rpc_status_by_network(conn):
    rows = conn.execute(
        """
        SELECT
            ne.network_id,
            ec.status,
            ec.http_status,
            ec.latency_ms,
            ec.remote_height,
            ec.error_message,
            ec.checked_at
        FROM network_endpoints ne
        LEFT JOIN (
            SELECT endpoint_id, MAX(checked_at) AS max_checked_at
            FROM endpoint_checks
            GROUP BY endpoint_id
        ) latest
          ON latest.endpoint_id = ne.id
        LEFT JOIN endpoint_checks ec
          ON ec.endpoint_id = ne.id
         AND ec.checked_at = latest.max_checked_at
        WHERE ne.endpoint_type = 'rpc'
          AND COALESCE(ne.is_enabled, 1) = 1
          AND COALESCE(ne.is_public, 0) = 0
        ORDER BY ne.network_id ASC, COALESCE(ne.priority, 999999) ASC, ne.id ASC
        """
    ).fetchall()

    result = {}
    for row in rows:
        network_id = row["network_id"]
        if network_id not in result:
            result[network_id] = row
    return result

def get_dashboard_rows(conn):
    latest_validator_rpc_rows = get_latest_rpc_status_by_network(conn)

    rows = conn.execute(
        """
        SELECT
            n.id AS network_id,
            n.name,
            COALESCE(n.display_name, n.name) AS display_name,
            v.operator_address AS valoper_address,
            COALESCE(v.moniker, 'unknown') AS moniker,
            COALESCE(nsc.validator_status, 'unknown') AS validator_status,
            COALESCE(nsc.sync_status, 'unknown') AS sync_status,
            COALESCE(nsc.snapshot_status, 'unknown') AS snapshot_status,
            COALESCE(nsc.overall_status, 'unknown') AS overall_status,
            nsc.local_height,
            nsc.reference_height,
            nsc.sync_diff,
            COALESCE(nsc.active_alerts_count, 0) AS active_alerts_count,
            nsc.last_updated_at,
            vsc.status AS validator_chain_status,
            vsc.jailed AS validator_jailed,
            vsc.commission_rate AS validator_commission_rate,
            vsc.self_delegation_amount AS validator_self_delegation_amount,
            vsc.tokens AS validator_total_delegation_amount,
            vsc.last_seen_height AS validator_last_seen_height
        FROM networks n
        JOIN validators v
          ON v.network_id = n.id
        LEFT JOIN network_status_current nsc
          ON nsc.network_id = n.id
        LEFT JOIN validator_status_current vsc
          ON vsc.validator_id = v.id
        WHERE COALESCE(n.is_enabled, 1) = 1
          AND COALESCE(v.is_enabled, 1) = 1
        ORDER BY COALESCE(nsc.active_alerts_count, 0) DESC, n.name ASC, v.operator_address ASC
        """
    ).fetchall()

    def status_emoji(value):
        if value == "ok":
            return "🟢"
        if value == "warning":
            return "🟡"
        if value == "critical":
            return "🔴"
        return "⚪"

    def bool_display(value):
        if value is None:
            return "—"
        try:
            return "yes" if int(value) else "no"
        except Exception:
            return str(value)

    def display_value(value):
        if value in (None, "", 0, "0"):
            return "—"
        return str(value)

    result = []
    for row in rows:
        item = dict(row)

        validator_rpc = latest_validator_rpc_rows.get(row["network_id"])
        if validator_rpc:
            item["validator_rpc_status"] = validator_rpc["status"] or "unknown"
            item["validator_rpc_http_status"] = validator_rpc["http_status"]
            item["validator_rpc_latency_ms"] = validator_rpc["latency_ms"]
            item["validator_rpc_remote_height"] = validator_rpc["remote_height"]
            item["validator_rpc_error_message"] = validator_rpc["error_message"]
            item["validator_rpc_checked_at"] = validator_rpc["checked_at"]
        else:
            item["validator_rpc_status"] = "unknown"
            item["validator_rpc_http_status"] = None
            item["validator_rpc_latency_ms"] = None
            item["validator_rpc_remote_height"] = None
            item["validator_rpc_error_message"] = None
            item["validator_rpc_checked_at"] = None

        item["overall_status"] = item.get("overall_status") or "unknown"
        item["validator_status"] = item.get("validator_status") or "unknown"
        item["sync_status"] = item.get("sync_status") or "unknown"
        item["snapshot_status"] = item.get("snapshot_status") or "unknown"
        item["validator_rpc_status"] = item.get("validator_rpc_status") or "unknown"

        item["overall_emoji"] = status_emoji(item["overall_status"])
        item["validator_emoji"] = status_emoji(item["validator_status"])
        item["sync_emoji"] = status_emoji(item["sync_status"])
        item["validator_rpc_emoji"] = status_emoji(item["validator_rpc_status"])

        item["validator_status_display"] = display_value(item.get("validator_chain_status"))
        item["validator_jailed_display"] = bool_display(item.get("validator_jailed"))
        item["validator_commission_display"] = format_percent_from_ratio(item.get("validator_commission_rate"))
        item["validator_self_delegation_display"] = format_number(item.get("validator_self_delegation_amount"))
        item["validator_total_delegation_display"] = format_number(item.get("validator_total_delegation_amount"))
        item["validator_last_seen_height_display"] = format_number(item.get("validator_last_seen_height"), decimals=0)

        item["validator_status_reason"] = None
        item["validator_rpc_details"] = []

        result.append(item)

    return result

def get_public_rpc_rows(conn):
    rows = conn.execute(
        """
        SELECT
            n.id AS network_id,
            n.name AS network_name,
            COALESCE(n.display_name, n.pretty_name, n.name) AS network_display_name,
            ne.url,
            ne.priority,
            ec.status,
            ec.http_status,
            ec.latency_ms,
            ec.remote_height,
            ec.error_message,
            ec.checked_at
        FROM network_endpoints ne
        JOIN networks n ON n.id = ne.network_id
        LEFT JOIN (
            SELECT endpoint_id, MAX(checked_at) AS max_checked_at
            FROM endpoint_checks
            GROUP BY endpoint_id
        ) latest
          ON latest.endpoint_id = ne.id
        LEFT JOIN endpoint_checks ec
          ON ec.endpoint_id = ne.id
         AND ec.checked_at = latest.max_checked_at
        WHERE ne.endpoint_type = 'rpc'
          AND COALESCE(ne.is_enabled, 1) = 1
          AND COALESCE(ne.is_public, 0) = 1
          AND COALESCE(n.is_enabled, 1) = 1
        ORDER BY n.name ASC, COALESCE(ne.priority, 999999) ASC, ne.id ASC
        """
    ).fetchall()
    return rows


@router.get("/dashboard/proposals")
def dashboard_proposals(request: Request):
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT
            n.name AS network,
            gp.proposal_id,
            gp.title,
            gp.status,
            gp.voting_end_time,
            gp.yes_votes,
            gp.no_votes,
            gp.abstain_votes,
            gp.no_with_veto_votes,
            gp.validator_voted,
            gp.validator_vote_option
        FROM governance_proposals gp
        JOIN networks n ON n.id = gp.network_id
        WHERE COALESCE(gp.is_latest, 1) = 1
        ORDER BY gp.voting_end_time ASC, n.name ASC, gp.proposal_id ASC
        """
    ).fetchall()

    result = []
    for row in rows:
        item = dict(row)

        validator_voted = 0
        try:
            validator_voted = int(item.get("validator_voted") or 0)
        except Exception:
            validator_voted = 0

        if validator_voted == 1:
            item["our_vote"] = item.get("validator_vote_option") or "VOTED"
            item["our_vote_class"] = "ok"
        else:
            item["our_vote"] = "warning"
            item["our_vote_class"] = "warning"

        item["majority_vote"] = get_majority_vote_label(
            item.get("yes_votes"),
            item.get("no_votes"),
            item.get("abstain_votes"),
            item.get("no_with_veto_votes"),
        )

        result.append(item)

    conn.close()

    return TEMPLATES.TemplateResponse(
        "proposals.html",
        {
            "request": request,
            "rows": result,
        },
    )


@router.get("/dashboard")
def dashboard(request: Request):
    conn = db_connect()
    try:
        totals = get_dashboard_totals(conn)
        rows = get_dashboard_rows(conn)
    finally:
        conn.close()

    return TEMPLATES.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "totals": totals,
            "rows": rows,
        },
    )

@router.get("/dashboard/rewards")
def dashboard_rewards(request: Request):
    snapshot = load_json(
        SNAPSHOT_PATH,
        {
            "timestamp": None,
            "rows": [],
            "totals_by_network": {},
            "grand_total": 0,
        },
    )
    missing = load_json(MISSING_PATH, [])
    job_status = read_job_status()

    job_status["last_started_at_fmt"] = format_utc(job_status.get("last_started_at"))
    job_status["last_finished_at_fmt"] = format_utc(job_status.get("last_finished_at"))
    job_status["last_success_at_fmt"] = format_utc(job_status.get("last_success_at"))

    snapshot["timestamp_fmt"] = format_utc(snapshot.get("timestamp"))

    rows = snapshot.get("rows", []) or []
    rows = sorted(rows, key=lambda r: float(r.get("total") or 0), reverse=True)

    network_totals = {}

    for r in rows:
        network = r.get("network") or "unknown"
        amount = float(r.get("amount") or 0)
        total = float(r.get("total") or 0)

        if network not in network_totals:
            network_totals[network] = {
                "network": network,
                "amount": 0.0,
                "total": 0.0,
            }

        network_totals[network]["amount"] += amount
        network_totals[network]["total"] += total

    totals_sorted = sorted(
        network_totals.values(),
        key=lambda x: x["total"],
        reverse=True,
    )

    return TEMPLATES.TemplateResponse(
        "rewards.html",
        {
            "request": request,
            "job_status": job_status,
            "snapshot": snapshot,
            "totals_sorted": totals_sorted,
            "rows": rows,
            "missing": missing,
            "lock_exists": JOB_LOCK_PATH.exists(),
        },
    )


@router.post("/dashboard/rewards/run")
def dashboard_rewards_run(background_tasks: BackgroundTasks):
    if not JOB_LOCK_PATH.exists():
        background_tasks.add_task(run_commission_report)
    return RedirectResponse(url="/dashboard/rewards", status_code=303)


@router.get("/dashboard/public-rpc")
def dashboard_public_rpc(request: Request):
    conn = db_connect()
    try:
        rows = get_public_rpc_rows(conn)
    finally:
        conn.close()

    return TEMPLATES.TemplateResponse(
        "public_rpc.html",
        {
            "request": request,
            "rows": rows,
        },
    )


@router.get("/dashboard/alerts")
def dashboard_alerts(request: Request):
    return TEMPLATES.TemplateResponse(
        "alerts.html",
        {
            "request": request,
            "rows": [],
        },
    )


@router.get("/dashboard/snapshots")
def dashboard_snapshots(request: Request):
    return TEMPLATES.TemplateResponse(
        "snapshots.html",
        {
            "request": request,
            "rows": [],
        },
    )
