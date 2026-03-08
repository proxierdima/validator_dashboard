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
        <th>Local height</th>
        <th>Reference height</th>
        <th>Diff</th>
        <th>Alerts</th>
        <th>Updated</th>
      </tr>
    </thead>
    <tbody>
      {% for row in items %}
      <tr class="dashboard-row">
        <td>{{ row.overall_emoji }} {{ row.overall_status or 'unknown' }}</td>

        <td class="hover-anchor">
          <strong>{{ row.display_name or row.name }}</strong><br>
          <span class="muted">{{ row.name }}</span>

          <div class="hover-card">
            <div class="hover-card-title">
              {{ row.display_name or row.name }}
            </div>

            <div class="hover-grid">
              <div class="hover-label">Validator</div>
              <div>{{ row.validator_emoji }} {{ row.validator_status or 'unknown' }}</div>

              <div class="hover-label">Moniker</div>
              <div>{{ row.moniker or 'PostHuman' }}</div>

              <div class="hover-label">Validator RPC</div>
              <div>{{ row.validator_rpc_emoji }} {{ row.validator_rpc_status or 'unknown' }}</div>

              <div class="hover-label">Sync</div>
              <div>{{ row.sync_emoji }} {{ row.sync_status or 'unknown' }}</div>

              <div class="hover-label">Local height</div>
              <div>{{ row.local_height or '—' }}</div>

              <div class="hover-label">Reference height</div>
              <div>{{ row.reference_height or '—' }}</div>

              <div class="hover-label">Diff</div>
              <div>{{ row.sync_diff if row.sync_diff is not none else '—' }}</div>

              <div class="hover-label">Alerts</div>
              <div>{{ row.active_alerts_count or 0 }}</div>

              <div class="hover-label">Updated</div>
              <div>{{ row.last_updated_at or '—' }}</div>
            </div>
          </div>
        </td>

        <td>
          {{ row.validator_emoji }} {{ row.validator_status or 'unknown' }}<br>
          <span class="muted">{{ row.moniker or '' }}</span>
        </td>

        <td>{{ row.validator_rpc_emoji }} {{ row.validator_rpc_status }}</td>

        <td>{{ row.sync_emoji }} {{ row.sync_status or 'unknown' }}</td>

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

cat >> app/static/css/dashboard.css <<'CSS'

.dashboard-row {
  position: relative;
}

.hover-anchor {
  position: relative;
}

.hover-card {
  display: none;
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 8px;
  min-width: 340px;
  max-width: 460px;
  background: #11161d;
  border: 1px solid #2a3240;
  border-radius: 12px;
  padding: 14px 16px;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
  z-index: 50;
}

.dashboard-row:hover .hover-card {
  display: block;
}

.hover-card-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
  color: #ffffff;
}

.hover-grid {
  display: grid;
  grid-template-columns: 130px 1fr;
  gap: 8px 12px;
  font-size: 13px;
  line-height: 1.35;
}

.hover-label {
  color: #9aa4b2;
}
CSS
