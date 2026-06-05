"""
Web dashboard — FastAPI + pure HTML/CSS/JS (no frontend build step).

Access: http://YOUR_SERVER_IP:8080

Features:
  • Live stats cards (accounts, emails, replies)
  • Accounts table with status badges
  • Recipients breakdown
  • Recent logs with level colouring
  • Action buttons: Run Blast, Check Replies, Maintenance
  • Auto-refresh every 30 seconds
  • Manual log stream via SSE (Server-Sent Events)

Run:
  python main.py dashboard
  # or directly:
  uvicorn src.dashboard:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# ── lazy config load so the module is importable without config.json ─────────
_config = None
_db_path = "data/campaign.db"


def _get_config():
    global _config, _db_path
    if _config is None:
        from .config import Config
        _config = Config.load("config.json")
        _db_path = _config.paths.db_path
    return _config


# ─────────────────────────── FastAPI app ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_config()
    yield

app = FastAPI(title="Gmail Campaign Dashboard", lifespan=lifespan)


# ─────────────────────────── API endpoints ───────────────────────────────────

@app.get("/api/stats")
async def api_stats():
    from .database import get_daily_stats
    return get_daily_stats(_db_path)


@app.get("/api/accounts")
async def api_accounts():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT email, status, daily_sent, total_sent, last_sent_date, "
        "date_added, date_exhausted, first_login_completed "
        "FROM accounts ORDER BY status ASC, total_sent DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/recipients")
async def api_recipients():
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    summary = conn.execute("""
        SELECT
            SUM(CASE WHEN status='pending'     THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status='first_sent'  THEN 1 ELSE 0 END) as first_sent,
            SUM(CASE WHEN status='replied'     THEN 1 ELSE 0 END) as replied,
            SUM(CASE WHEN status='second_sent' THEN 1 ELSE 0 END) as second_sent,
            SUM(CASE WHEN status='bad'         THEN 1 ELSE 0 END) as bad,
            COUNT(*)                                               as total
        FROM recipients
    """).fetchone()
    conn.close()
    return dict(summary)


@app.get("/api/logs")
async def api_logs(limit: int = 100, level: str = ""):
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    if level:
        rows = conn.execute(
            "SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ?",
            (level.upper(), limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Action endpoints (run campaign tasks on demand) ──────────────────────────

_running_task: dict[str, bool] = {"blast": False, "replies": False, "maintenance": False}


@app.post("/api/action/{action}")
async def api_action(action: str):
    if action not in ("blast", "replies", "maintenance"):
        return JSONResponse({"error": "unknown action"}, status_code=400)

    if _running_task.get(action):
        return JSONResponse({"status": "already_running"})

    cfg = _get_config()
    from .campaign_runner import run_daily_blast, run_reply_check, run_maintenance

    async def run():
        _running_task[action] = True
        try:
            if action == "blast":
                await run_daily_blast(cfg)
            elif action == "replies":
                await run_reply_check(cfg)
            elif action == "maintenance":
                await run_maintenance(cfg)
        finally:
            _running_task[action] = False

    asyncio.create_task(run())
    return JSONResponse({"status": "started"})


@app.get("/api/running")
async def api_running():
    return _running_task


# ── SSE log stream (real-time last log line) ─────────────────────────────────

@app.get("/api/logs/stream")
async def log_stream():
    async def event_generator() -> AsyncGenerator[str, None]:
        last_id = 0
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            # Get current last id
            row = conn.execute("SELECT MAX(id) FROM logs").fetchone()
            last_id = row[0] or 0
            while True:
                rows = conn.execute(
                    "SELECT * FROM logs WHERE id > ? ORDER BY id ASC LIMIT 20",
                    (last_id,)
                ).fetchall()
                for r in rows:
                    last_id = r["id"]
                    data = json.dumps(dict(r), default=str)
                    yield f"data: {data}\n\n"
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        finally:
            conn.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─────────────────────────── Main HTML page ──────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


# ─────────────────────────── HTML template ───────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gmail Campaign Dashboard</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3250;
    --text: #e2e8f0;
    --muted: #8892a4;
    --accent: #4f8ef7;
    --green: #22c55e;
    --yellow: #f59e0b;
    --red: #ef4444;
    --orange: #f97316;
    --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex; align-items: center; justify-content: space-between;
  }
  header h1 { font-size: 18px; font-weight: 600; letter-spacing: .3px; }
  header h1 span { color: var(--accent); }
  #clock { color: var(--muted); font-size: 13px; }
  #refresh-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); display: inline-block; margin-left: 8px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

  /* ── Stats cards ─────────────────────────────── */
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 14px; margin-bottom: 22px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; }
  .card .value { font-size: 28px; font-weight: 700; }
  .card .sub { color: var(--muted); font-size: 11px; margin-top: 4px; }
  .c-blue   .value { color: var(--accent); }
  .c-green  .value { color: var(--green); }
  .c-yellow .value { color: var(--yellow); }
  .c-red    .value { color: var(--red); }
  .c-orange .value { color: var(--orange); }
  .c-purple .value { color: var(--purple); }

  /* ── Action buttons ──────────────────────────── */
  .actions { display: flex; gap: 10px; margin-bottom: 22px; flex-wrap: wrap; }
  .btn { padding: 9px 20px; border-radius: 7px; border: none; cursor: pointer; font-size: 13px; font-weight: 600; transition: opacity .15s, transform .1s; }
  .btn:active { transform: scale(.97); }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-blast    { background: var(--accent); color: #fff; }
  .btn-replies  { background: var(--green);  color: #fff; }
  .btn-maint    { background: var(--yellow); color: #000; }
  .btn-reload   { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }

  /* ── Recipient bar ───────────────────────────── */
  .rec-section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; margin-bottom: 22px; }
  .rec-section h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; margin-bottom: 14px; }
  .bar-wrap { display: flex; height: 18px; border-radius: 6px; overflow: hidden; gap: 2px; margin-bottom: 10px; }
  .bar-seg { transition: width .4s; height: 100%; }
  .seg-pending     { background: var(--muted); }
  .seg-first_sent  { background: var(--accent); }
  .seg-replied     { background: var(--purple); }
  .seg-second_sent { background: var(--green); }
  .seg-bad         { background: var(--red); }
  .rec-legend { display: flex; gap: 16px; flex-wrap: wrap; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; }

  /* ── Tables ──────────────────────────────────── */
  .section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 22px; overflow: hidden; }
  .section-header { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .section-header h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; }
  table { width: 100%; border-collapse: collapse; }
  th { padding: 10px 16px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .7px; color: var(--muted); border-bottom: 1px solid var(--border); font-weight: 500; }
  td { padding: 9px 16px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--surface2); }

  .badge { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .badge-active    { background: rgba(34,197,94,.15);  color: var(--green); }
  .badge-blocked   { background: rgba(239,68,68,.15);  color: var(--red); }
  .badge-exhausted { background: rgba(245,158,11,.15); color: var(--yellow); }
  .badge-INFO      { background: rgba(79,142,247,.12); color: var(--accent); }
  .badge-WARNING   { background: rgba(245,158,11,.12); color: var(--yellow); }
  .badge-ERROR     { background: rgba(239,68,68,.12);  color: var(--red); }

  .log-msg { max-width: 520px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .new-row { animation: highlight .8s; }
  @keyframes highlight { from{background:rgba(79,142,247,.18)} to{background:transparent} }

  /* ── Toast ───────────────────────────────────── */
  #toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface); border: 1px solid var(--border);
    padding: 12px 20px; border-radius: 8px; font-size: 13px; display: none; z-index: 999; box-shadow: 0 4px 20px rgba(0,0,0,.4); }
  #toast.show { display: block; }

  /* ── Live log ────────────────────────────────── */
  #live-logs { max-height: 260px; overflow-y: auto; }
</style>
</head>
<body>

<header>
  <h1>Gmail <span>Campaign</span> Dashboard</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <span id="status-text" style="color:var(--muted);font-size:13px;">Idle</span>
    <span id="refresh-dot"></span>
    <span id="clock"></span>
  </div>
</header>

<main>

  <!-- Stats cards -->
  <div class="cards" id="cards-area">
    <div class="card c-green">
      <div class="label">Active Accounts</div>
      <div class="value" id="s-active">—</div>
    </div>
    <div class="card c-red">
      <div class="label">Blocked</div>
      <div class="value" id="s-blocked">—</div>
    </div>
    <div class="card c-yellow">
      <div class="label">Exhausted</div>
      <div class="value" id="s-exhausted">—</div>
    </div>
    <div class="card c-blue">
      <div class="label">First Sent Today</div>
      <div class="value" id="s-first">—</div>
    </div>
    <div class="card c-purple">
      <div class="label">Replies Today</div>
      <div class="value" id="s-replied">—</div>
    </div>
    <div class="card c-orange">
      <div class="label">Second Sent Today</div>
      <div class="value" id="s-second">—</div>
    </div>
  </div>

  <!-- Action buttons -->
  <div class="actions">
    <button class="btn btn-blast"   onclick="runAction('blast',   this)">▶ Run Blast</button>
    <button class="btn btn-replies" onclick="runAction('replies', this)">🔍 Check Replies</button>
    <button class="btn btn-maint"   onclick="runAction('maintenance', this)">⚙ Maintenance</button>
    <button class="btn btn-reload"  onclick="loadAll()">↻ Refresh</button>
  </div>

  <!-- Recipients progress bar -->
  <div class="rec-section">
    <h2>Recipients</h2>
    <div class="bar-wrap" id="rec-bar">
      <div class="bar-seg seg-pending"     id="b-pending"     style="width:0%"></div>
      <div class="bar-seg seg-first_sent"  id="b-first_sent"  style="width:0%"></div>
      <div class="bar-seg seg-replied"     id="b-replied"     style="width:0%"></div>
      <div class="bar-seg seg-second_sent" id="b-second_sent" style="width:0%"></div>
      <div class="bar-seg seg-bad"         id="b-bad"         style="width:0%"></div>
    </div>
    <div class="rec-legend">
      <div class="legend-item"><div class="legend-dot" style="background:var(--muted)"></div> Pending <b id="l-pending">0</b></div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div> First Sent <b id="l-first_sent">0</b></div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--purple)"></div> Replied <b id="l-replied">0</b></div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div> Second Sent <b id="l-second_sent">0</b></div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--red)"></div> Bad <b id="l-bad">0</b></div>
      <div class="legend-item" style="margin-left:auto;color:var(--text)">Total: <b id="l-total">0</b></div>
    </div>
  </div>

  <!-- Accounts table -->
  <div class="section">
    <div class="section-header">
      <h2>Accounts</h2>
      <span id="acc-count" style="color:var(--muted);font-size:12px;"></span>
    </div>
    <div style="overflow-x:auto;">
    <table id="acc-table">
      <thead><tr>
        <th>Email</th><th>Status</th><th>Sent Today</th><th>Total Sent</th><th>Last Sent</th><th>Added</th><th>Login Done</th>
      </tr></thead>
      <tbody id="acc-body"></tbody>
    </table>
    </div>
  </div>

  <!-- Live log -->
  <div class="section">
    <div class="section-header">
      <h2>Live Logs</h2>
      <div style="display:flex;gap:10px;align-items:center;">
        <select id="log-level" onchange="loadLogs()" style="background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:5px;font-size:12px;">
          <option value="">All</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
        <label style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px;">
          <input type="checkbox" id="live-toggle" checked onchange="toggleLive(this)"> Live
        </label>
      </div>
    </div>
    <div style="overflow-x:auto;">
    <table>
      <thead><tr><th>Time</th><th>Level</th><th>Account</th><th>Recipient</th><th>Message</th></tr></thead>
      <tbody id="live-logs"></tbody>
    </table>
    </div>
  </div>

</main>

<div id="toast"></div>

<script>
let liveSource = null;

// ── Clock ─────────────────────────────────────────
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}, 1000);

// ── Load all data ─────────────────────────────────
async function loadAll() {
  await Promise.all([loadStats(), loadAccounts(), loadRecipients(), loadLogs()]);
  checkRunning();
}

async function loadStats() {
  const d = await fetchJSON('/api/stats');
  document.getElementById('s-active').textContent    = d.active_accounts;
  document.getElementById('s-blocked').textContent   = d.blocked_accounts;
  document.getElementById('s-exhausted').textContent = d.exhausted_accounts;
  document.getElementById('s-first').textContent     = d.first_sent;
  document.getElementById('s-replied').textContent   = d.replied;
  document.getElementById('s-second').textContent    = d.second_sent;
}

async function loadAccounts() {
  const rows = await fetchJSON('/api/accounts');
  document.getElementById('acc-count').textContent = rows.length + ' accounts';
  const tbody = document.getElementById('acc-body');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td style="font-family:monospace;">${r.email}</td>
      <td><span class="badge badge-${r.status}">${r.status}</span></td>
      <td>${r.daily_sent}</td>
      <td>${r.total_sent}</td>
      <td style="color:var(--muted)">${r.last_sent_date || '—'}</td>
      <td style="color:var(--muted)">${r.date_added}</td>
      <td>${r.first_login_completed ? '✅' : '⏳'}</td>
    </tr>
  `).join('');
}

async function loadRecipients() {
  const d = await fetchJSON('/api/recipients');
  const total = d.total || 1;
  ['pending','first_sent','replied','second_sent','bad'].forEach(k => {
    const pct = ((d[k] || 0) / total * 100).toFixed(1);
    document.getElementById('b-' + k).style.width = pct + '%';
    document.getElementById('l-' + k).textContent = d[k] || 0;
  });
  document.getElementById('l-total').textContent = d.total || 0;
}

async function loadLogs() {
  const level = document.getElementById('log-level').value;
  const rows = await fetchJSON('/api/logs?limit=80&level=' + level);
  renderLogs(rows, false);
}

function renderLogs(rows, animate) {
  const tbody = document.getElementById('live-logs');
  tbody.innerHTML = rows.map(r => `
    <tr class="${animate ? 'new-row' : ''}">
      <td style="color:var(--muted);white-space:nowrap;font-size:12px;">${(r.ts||'').replace('T',' ').slice(0,19)}</td>
      <td><span class="badge badge-${r.level}">${r.level}</span></td>
      <td style="font-size:12px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${r.account||''}</td>
      <td style="font-size:12px;color:var(--muted);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${r.recipient||''}</td>
      <td class="log-msg">${escHtml(r.message||'')}</td>
    </tr>
  `).join('');
}

// ── SSE live log ──────────────────────────────────
function startLive() {
  if (liveSource) return;
  liveSource = new EventSource('/api/logs/stream');
  liveSource.onmessage = (e) => {
    const row = JSON.parse(e.data);
    const tbody = document.getElementById('live-logs');
    const tr = document.createElement('tr');
    tr.className = 'new-row';
    tr.innerHTML = `
      <td style="color:var(--muted);white-space:nowrap;font-size:12px;">${(row.ts||'').replace('T',' ').slice(0,19)}</td>
      <td><span class="badge badge-${row.level}">${row.level}</span></td>
      <td style="font-size:12px;color:var(--muted);">${row.account||''}</td>
      <td style="font-size:12px;color:var(--muted);">${row.recipient||''}</td>
      <td class="log-msg">${escHtml(row.message||'')}</td>
    `;
    tbody.insertBefore(tr, tbody.firstChild);
    // Keep max 100 rows
    while (tbody.children.length > 100) tbody.removeChild(tbody.lastChild);
    // Update stats cards too
    loadStats();
  };
}

function stopLive() {
  if (liveSource) { liveSource.close(); liveSource = null; }
}

function toggleLive(cb) {
  cb.checked ? startLive() : stopLive();
}

// ── Action buttons ────────────────────────────────
async function runAction(action, btn) {
  btn.disabled = true;
  const labels = { blast: 'Running Blast…', replies: 'Checking Replies…', maintenance: 'Running Maintenance…' };
  const original = btn.textContent;
  btn.textContent = labels[action];
  showToast('🚀 ' + labels[action]);

  try {
    const r = await fetchJSON('/api/action/' + action, 'POST');
    if (r.status === 'already_running') {
      showToast('⚠️ Already running!');
    } else {
      showToast('✅ Task started in background');
      document.getElementById('status-text').textContent = labels[action];
    }
  } catch(e) {
    showToast('❌ Error: ' + e.message);
  }

  btn.textContent = original;
  btn.disabled = false;
}

async function checkRunning() {
  const r = await fetchJSON('/api/running');
  const labels = [];
  if (r.blast)       labels.push('Blast running');
  if (r.replies)     labels.push('Reply check running');
  if (r.maintenance) labels.push('Maintenance running');
  document.getElementById('status-text').textContent = labels.length ? labels.join(' · ') : 'Idle';
}

// ── Helpers ───────────────────────────────────────
async function fetchJSON(url, method='GET') {
  const r = await fetch(url, { method });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Auto-refresh every 30s ────────────────────────
setInterval(() => { loadStats(); loadRecipients(); checkRunning(); }, 30000);
setInterval(loadAccounts, 60000);

// ── Init ──────────────────────────────────────────
loadAll();
startLive();
</script>
</body>
</html>
"""
