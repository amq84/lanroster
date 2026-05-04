import json
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config as cfg_mod
from . import devices as dev_mod
from . import network as net_mod
from . import seen as seen_mod
from . import vendor as vendor_mod

_lock = threading.Lock()       # guards _state reads/writes
_scan_lock = threading.Lock()  # prevents concurrent scans
_state: dict | None = None
_interval_seconds: int = 30

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>LanRoster</title>
<style>
:root {
  --bg:      #0f1117;
  --bg2:     #161b22;
  --border:  #21262d;
  --text:    #c9d1d9;
  --dim:     #6e7681;
  --green:   #3fb950;
  --red:     #f85149;
  --cyan:    #79c0ff;
  --yellow:  #e3b341;
  --purple:  #bc8cff;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
  font-size: 14px;
  padding: 32px 40px;
  min-height: 100vh;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 16px;
}
h1 { font-size: 18px; color: var(--purple); letter-spacing: 0.04em; }
.meta { font-size: 12px; color: var(--dim); display: flex; align-items: center; gap: 10px; }
.badge {
  padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;
  letter-spacing: 0.05em; text-transform: uppercase;
}
.badge-ok  { background: rgba(63,185,80,0.12);  color: var(--green);  border: 1px solid rgba(63,185,80,0.25); }
.badge-deg { background: rgba(227,179,65,0.12); color: var(--yellow); border: 1px solid rgba(227,179,65,0.25); }
.badge-err { background: rgba(248,81,73,0.12);  color: var(--red);    border: 1px solid rgba(248,81,73,0.25); }
.stats { display: flex; gap: 32px; margin-bottom: 24px; }
.stat-num  { font-size: 32px; font-weight: 700; line-height: 1; }
.stat-label { font-size: 11px; color: var(--dim); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.08em; }
.n-online  { color: var(--green); }
.n-offline { color: var(--red); }
.n-total   { color: var(--dim); }
.progress-row {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 24px;
}
.progress-track {
  flex: 1; height: 2px; background: var(--border); border-radius: 1px; overflow: hidden;
}
.progress-fill {
  height: 100%; background: var(--purple); border-radius: 1px;
  transition: width 1s linear;
}
.countdown-label { font-size: 12px; color: var(--dim); white-space: nowrap; min-width: 120px; }
button {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  padding: 4px 14px; border-radius: 5px; cursor: pointer;
  font-family: inherit; font-size: 12px; white-space: nowrap;
}
button:hover { border-color: var(--purple); color: var(--purple); }
table { width: 100%; border-collapse: collapse; margin-bottom: 32px; }
thead th {
  text-align: left; color: var(--dim); font-weight: 500;
  padding: 8px 14px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  border-bottom: 1px solid var(--border);
}
tbody td { padding: 11px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
tbody tr:hover td { background: var(--bg2); }
tbody tr:last-child td { border-bottom: none; }
.dot { font-size: 14px; }
.dot-on  { color: var(--green); }
.dot-off { color: var(--border); }
.dev-name { color: #e6edf3; font-weight: 600; }
.dev-ip   { color: var(--cyan); font-variant-numeric: tabular-nums; }
.dev-dim  { color: var(--dim); }
.section-title {
  font-size: 11px; color: var(--dim); text-transform: uppercase;
  letter-spacing: 0.1em; margin-bottom: 10px;
}
.log-list { display: flex; flex-direction: column; gap: 0; }
.log-entry {
  font-size: 12px; padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
.log-entry:last-child { border-bottom: none; }
.log-on  { color: var(--green); }
.log-off { color: var(--red); }
.log-dim { color: var(--dim); }
.scanning-msg { text-align: center; padding: 32px; color: var(--dim); }
</style>
</head>
<body>

<header>
  <h1>⬡ LanRoster</h1>
  <div class="meta">
    <span id="scan-time">—</span>
    <span id="scan-badge" class="badge badge-deg">—</span>
  </div>
</header>

<div class="stats">
  <div>
    <div class="stat-num n-online"  id="n-online">—</div>
    <div class="stat-label">online</div>
  </div>
  <div>
    <div class="stat-num n-offline" id="n-offline">—</div>
    <div class="stat-label">offline</div>
  </div>
  <div>
    <div class="stat-num n-total"   id="n-total">—</div>
    <div class="stat-label">registered</div>
  </div>
</div>

<div class="progress-row">
  <span class="countdown-label" id="countdown-label">scanning…</span>
  <div class="progress-track"><div class="progress-fill" id="progress-fill" style="width:100%"></div></div>
  <button onclick="triggerRefresh()">↻ Scan now</button>
</div>

<table>
  <thead>
    <tr>
      <th style="width:28px"></th>
      <th>Device</th>
      <th>SSH</th>
      <th>IP Address</th>
      <th>MAC Address</th>
      <th>Vendor</th>
      <th>Last Seen</th>
    </tr>
  </thead>
  <tbody id="device-tbody">
    <tr><td colspan="6" class="scanning-msg">Waiting for first scan…</td></tr>
  </tbody>
</table>

<div class="section-title">Transitions</div>
<div class="log-list" id="log-list">
  <div class="log-entry log-dim">No transitions yet.</div>
</div>

<script>
const INTERVAL_MS = INTERVAL_SECONDS * 1000;
let nextScanAt = Date.now() + INTERVAL_MS;
let scanInProgress = false;
let logEntries = [];
let prevOnline = {};

function relativeTime(isoTs) {
  if (!isoTs) return '—';
  const secs = Math.round((Date.now() - new Date(isoTs)) / 1000);
  if (secs < 60)    return secs + 's ago';
  if (secs < 3600)  return Math.round(secs / 60) + 'm ago';
  if (secs < 86400) return Math.round(secs / 3600) + 'h ago';
  return Math.round(secs / 86400) + 'd ago';
}

function badgeClass(method) {
  if (method === 'scapy') return 'badge-ok';
  if (method === 'error' || method === '—') return 'badge-err';
  return 'badge-deg';
}

function render(data) {
  document.getElementById('scan-time').textContent = data.scanned_at || '—';
  const badge = document.getElementById('scan-badge');
  badge.textContent = data.scan_method || '—';
  badge.className = 'badge ' + badgeClass(data.scan_method);

  document.getElementById('n-online').textContent  = data.online_count ?? '—';
  document.getElementById('n-offline').textContent = (data.total_count - data.online_count) || 0;
  document.getElementById('n-total').textContent   = data.total_count ?? '—';

  // detect transitions
  const now = data.scanned_at || new Date().toLocaleTimeString();
  for (const d of (data.devices || [])) {
    if (d.name in prevOnline && prevOnline[d.name] !== d.online) {
      logEntries.unshift({ ts: now, name: d.name, online: d.online, ip: d.ip });
    }
    prevOnline[d.name] = d.online;
  }

  // render table
  const tbody = document.getElementById('device-tbody');
  if (!data.devices || !data.devices.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="scanning-msg">Roster is empty — register devices with <code>lanroster register</code>.</td></tr>';
  } else {
    tbody.innerHTML = data.devices.map(d => `
      <tr>
        <td class="dot ${d.online ? 'dot-on' : 'dot-off'}">●</td>
        <td class="dev-name">${esc(d.name)}</td>
        <td class="dev-dim">${d.ssh_target ? `<span class="dev-ip">${esc(d.ssh_user)}</span>@${esc(d.ip)}` : (d.ssh_user ? esc(d.ssh_user) : '—')}</td>
        <td class="${d.ip ? 'dev-ip' : 'dev-dim'}">${esc(d.ip || '—')}</td>
        <td class="dev-dim">${esc(d.mac)}</td>
        <td class="dev-dim">${esc(d.vendor || '—')}</td>
        <td class="dev-dim">${relativeTime(d.last_seen)}</td>
      </tr>
    `).join('');
  }

  // render log
  const logList = document.getElementById('log-list');
  if (!logEntries.length) {
    logList.innerHTML = '<div class="log-entry log-dim">No transitions yet.</div>';
  } else {
    logList.innerHTML = logEntries.slice(0, 20).map(e =>
      `<div class="log-entry ${e.online ? 'log-on' : 'log-off'}">[${esc(e.ts)}] ${e.online ? '▲' : '▼'} ${esc(e.name)} ${e.online ? 'came online (' + esc(e.ip || '') + ')' : 'went offline'}</div>`
    ).join('');
  }
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    render(data);
  } catch(e) {
    document.getElementById('scan-time').textContent = 'fetch error';
  }
}

async function triggerRefresh() {
  const btn = document.querySelector('button');
  const label = document.getElementById('countdown-label');
  const fill = document.getElementById('progress-fill');
  btn.disabled = true;
  btn.textContent = '⟳ Scanning…';
  label.textContent = 'scanning…';
  fill.style.transition = 'none';
  fill.style.width = '100%';
  try {
    const r = await fetch('/api/scan');
    const data = await r.json();
    nextScanAt = Date.now() + INTERVAL_MS;
    fill.style.transition = 'width 1s linear';
    render(data);
  } catch(e) {
    label.textContent = 'scan error';
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ Scan now';
  }
}

function tick() {
  const remaining = Math.max(0, Math.round((nextScanAt - Date.now()) / 1000));
  document.getElementById('countdown-label').textContent = 'next scan in ' + remaining + 's';
  const pct = Math.max(0, (nextScanAt - Date.now()) / INTERVAL_MS * 100);
  document.getElementById('progress-fill').style.width = pct + '%';
  if (Date.now() >= nextScanAt) {
    nextScanAt = Date.now() + INTERVAL_MS;
    fetchStatus();
  }
}

fetchStatus();
setInterval(tick, 1000);
</script>
</body>
</html>
"""


def _do_scan() -> dict:
    cfg = cfg_mod.get_config()
    if cfg is None:
        return {
            "devices": [], "scan_method": "error",
            "scanned_at": datetime.now().strftime("%H:%M:%S"),
            "online_count": 0, "total_count": 0,
            "error": "Not initialized",
        }
    roster = dev_mod.load_devices(cfg["devices_file"])
    _, cidr = net_mod.get_local_ip_and_network()
    result = net_mod.scan_network(cidr)
    seen_mod.update_from_scan(result.hosts)
    network_map = {mac: ip for ip, mac in result.hosts}
    devices = []
    for d in roster:
        mac = d["mac"]
        ip = network_map.get(mac)
        ssh_user = d.get("ssh_user")
        devices.append({
            "name": d["name"],
            "mac": mac,
            "ssh_user": ssh_user,
            "ssh_target": f"{ssh_user}@{ip}" if ssh_user and ip else None,
            "vendor": vendor_mod.get_vendor(mac),
            "online": ip is not None,
            "ip": ip,
            "last_seen": seen_mod.get_last_seen(mac),
        })
    online = sum(1 for d in devices if d["online"])
    return {
        "devices": devices,
        "scan_method": result.method,
        "scanned_at": datetime.now().strftime("%H:%M:%S"),
        "online_count": online,
        "total_count": len(devices),
    }


def _run_scan() -> dict:
    global _state
    with _scan_lock:
        try:
            state = _do_scan()
        except Exception as exc:
            state = {
                "devices": [], "scan_method": "error",
                "scanned_at": datetime.now().strftime("%H:%M:%S"),
                "online_count": 0, "total_count": 0,
                "error": str(exc),
            }
        with _lock:
            _state = state
        return state


def _scan_loop(interval: int, stop: threading.Event) -> None:
    while not stop.is_set():
        _run_scan()
        stop.wait(interval)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/api/status":
            self._serve_json()
        elif self.path == "/api/scan":
            self._serve_scan()
        else:
            self.send_error(404)

    def _serve_html(self):
        body = _HTML.replace("INTERVAL_SECONDS", str(_interval_seconds)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        with _lock:
            state = _state
        if state is None:
            payload = {
                "devices": [], "scan_method": "scanning",
                "scanned_at": "—", "online_count": 0, "total_count": 0,
            }
        else:
            payload = state
        self._write_json(payload)

    def _serve_scan(self):
        # Runs a live scan (blocks 10-30s) and returns fresh state.
        # If another scan is already running, waits for it to finish.
        self._write_json(_run_scan())

    def _write_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def run_web(port: int = 5577, interval: int = 30, open_browser: bool = True) -> None:
    global _interval_seconds
    _interval_seconds = interval

    stop = threading.Event()
    threading.Thread(target=_scan_loop, args=(interval, stop), daemon=True).start()

    server = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://localhost:{port}"

    if open_browser:
        threading.Timer(0.8, webbrowser.open, args=[url]).start()

    print(f"LanRoster web dashboard → {url}  (scan every {interval}s, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
