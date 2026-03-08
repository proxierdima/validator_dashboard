cat > app/web/dashboard.py <<'PY'
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Event, Network, NetworkStatusCurrent, SnapshotCheck, SnapshotTarget

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


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Network.id.label("network_id"),
            Network.name,
            Network.display_name,
            NetworkStatusCurrent.validator_status,
            NetworkStatusCurrent.endpoint_status,
            NetworkStatusCurrent.sync_status,
            NetworkStatusCurrent.snapshot_status,
            NetworkStatusCurrent.overall_status,
            NetworkStatusCurrent.local_height,
            NetworkStatusCurrent.reference_height,
            NetworkStatusCurrent.sync_diff,
            NetworkStatusCurrent.active_alerts_count,
            NetworkStatusCurrent.last_updated_at,
        )
        .join(NetworkStatusCurrent, NetworkStatusCurrent.network_id == Network.id, isouter=True)
        .where(Network.is_enabled == 1)
        .order_by(
            func.coalesce(NetworkStatusCurrent.active_alerts_count, 0).desc(),
            Network.name.asc(),
        )
    ).mappings().all()

    items = []
    for r in rows:
        items.append(
            {
                **dict(r),
                "validator_emoji": status_emoji(r["validator_status"]),
                "endpoint_emoji": status_emoji(r["endpoint_status"]),
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

mkdir -p app/api
cat > app/api/deps.py <<'PY'
from collections.abc import Generator

from app.core.db import SessionLocal


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
PY

cat > app/main.py <<'PY'
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.web.dashboard import router as dashboard_router

app = FastAPI(title=settings.APP_NAME)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(dashboard_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
    }


@app.get("/")
def root():
    return {
        "message": "Validator Dashboard API is running",
        "health": "/health",
        "dashboard": "/dashboard",
        "alerts": "/dashboard/alerts",
        "snapshots": "/dashboard/snapshots",
    }
PY

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
        <a href="/dashboard/alerts">Alerts</a>
        <a href="/dashboard/snapshots">Snapshots</a>
      </nav>
    </header>

    {% block content %}{% endblock %}
  </div>
</body>
</html>
HTML

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
        <th>RPC/REST</th>
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
        <td><strong>{{ row.display_name or row.name }}</strong><br><span class="muted">{{ row.name }}</span></td>
        <td>{{ row.validator_emoji }} {{ row.validator_status or 'unknown' }}</td>
        <td>{{ row.endpoint_emoji }} {{ row.endpoint_status or 'unknown' }}</td>
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

cat > app/templates/alerts.html <<'HTML'
{% extends "base.html" %}

{% block content %}
<div class="panel">
  <div class="panel-title">Open alerts</div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Severity</th>
        <th>Type</th>
        <th>Network</th>
        <th>Title</th>
        <th>First seen</th>
        <th>Last seen</th>
      </tr>
    </thead>
    <tbody>
      {% for row in items %}
      <tr>
        <td>{{ row.id }}</td>
        <td>{{ row.severity }}</td>
        <td>{{ row.event_type }}</td>
        <td>{{ row.network_name or '' }}</td>
        <td>{{ row.title }}</td>
        <td>{{ row.first_seen_at }}</td>
        <td>{{ row.last_seen_at }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
HTML

cat > app/templates/snapshots.html <<'HTML'
{% extends "base.html" %}

{% block content %}
<div class="panel">
  <div class="panel-title">Snapshots</div>
  <table>
    <thead>
      <tr>
        <th>Network</th>
        <th>Status</th>
        <th>Path</th>
        <th>File</th>
        <th>Size bytes</th>
        <th>Age sec</th>
        <th>Checked</th>
      </tr>
    </thead>
    <tbody>
      {% for row in items %}
      <tr>
        <td>{{ row.network_name }}</td>
        <td>{{ status_emoji(row.status) }} {{ row.status or 'unknown' }}</td>
        <td>{{ row.snapshot_path }}</td>
        <td>{{ row.file_name or '' }}</td>
        <td>{{ row.file_size_bytes or '' }}</td>
        <td>{{ row.age_seconds or '' }}</td>
        <td>{{ row.checked_at or '' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
HTML

cat > app/static/css/dashboard.css <<'CSS'
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #0f1115;
  color: #e8e8e8;
}

.container {
  max-width: 1600px;
  margin: 0 auto;
  padding: 24px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.topbar h1 {
  margin: 0 0 6px;
  font-size: 28px;
}

.nav {
  display: flex;
  gap: 16px;
}

.nav a {
  color: #9ecbff;
  text-decoration: none;
  font-weight: 600;
}

.muted {
  color: #9aa4b2;
  font-size: 13px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(5, minmax(160px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.card {
  background: #171b22;
  border: 1px solid #242b36;
  border-radius: 14px;
  padding: 18px;
}

.card .label {
  color: #9aa4b2;
  font-size: 13px;
  margin-bottom: 10px;
}

.card .value {
  font-size: 30px;
  font-weight: 700;
}

.panel {
  background: #171b22;
  border: 1px solid #242b36;
  border-radius: 14px;
  padding: 18px;
}

.panel-title {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 16px;
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  text-align: left;
  font-size: 13px;
  color: #9aa4b2;
  border-bottom: 1px solid #2a3240;
  padding: 12px 10px;
}

tbody td {
  padding: 12px 10px;
  border-bottom: 1px solid #202734;
  vertical-align: top;
  font-size: 14px;
}

tbody tr:hover {
  background: #1b212b;
}
CSS


