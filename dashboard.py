"""Local dashboard for the trader: trades, simulated equity, performance
stats, and start/stop for the dry-run bot.

    python dashboard.py            ->  http://127.0.0.1:8765

FastAPI + uvicorn. Binds to 127.0.0.1 so it is reachable from this
machine alone. It reads the same shark_bot_trade_log_*.csv files the bot
writes and the bot's console output, and starts/stops `main.py run` as a
subprocess. It never touches the exchange or any API key, and it refuses
to start a live (real-money) bot unless the mode was deliberately switched
to "real account" on the Settings page (typed confirmation, saved API keys)
and the Start click itself is confirmed. The mode lives in the gitignored
local_settings.json, never in the committed config.json.
"""

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, SecretStr  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

from shark.config import load_config as load_full_config  # noqa: E402
from shark.credentials import clear_credentials, get_credentials, key_hint, set_credentials  # noqa: E402
from shark.local_settings import set_dry_run  # noqa: E402
from shark.strategies import STRATEGIES  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "dashboard_bot.log"
CSV_PREFIX = "shark_bot_trade_log_"
LOG_TAIL_LINES = 60
MAX_TRADES_SHOWN = 50

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_proc_cache = {"at": 0.0, "pids": []}


def load_config() -> dict:
    """Effective config: config.json plus the saved mode override and credentials."""
    return load_full_config(str(CONFIG_PATH))


def find_bot_pids() -> list[int]:
    """PIDs of any `main.py run ...` python process, whoever started it.
    Cached a few seconds - this shells out to PowerShell."""
    now = time.time()
    if now - _proc_cache["at"] < 4:
        return _proc_cache["pids"]
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
           "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress")
    pids: list[int] = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW).stdout.strip()
        rows = json.loads(out) if out else []
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows:
            line = r.get("CommandLine") or ""
            if re.search(r"main\.py\"?\s+(--config\s+\S+\s+)?run\b", line):
                pids.append(int(r["ProcessId"]))
    except Exception:
        pids = _proc_cache["pids"]
    _proc_cache.update(at=now, pids=pids)
    return pids


def start_bot(strategy: str, confirm_live: bool = False) -> tuple[bool, str]:
    if strategy not in STRATEGIES:
        return False, f"Unknown strategy '{strategy}'."
    cfg = load_config()
    live = cfg.get("dry_run") is not True
    if live:
        if not (cfg["api_key"] and cfg["api_secret"]):
            return False, "Real-account mode needs API credentials - save them on the Settings page."
        if not confirm_live:
            return False, "Starting a live run needs explicit confirmation."
    _proc_cache["at"] = 0.0
    if find_bot_pids():
        return False, "A bot is already running."
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with open(LOG_PATH, "ab") as log:
        log.write(f"\n=== dashboard start {datetime.now().isoformat(timespec='seconds')} "
                  f"strategy={strategy} ===\n".encode())
        subprocess.Popen([sys.executable, "-u", "main.py", "run", "--strategy", strategy],
                         cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env,
                         creationflags=_NEW_GROUP | _NO_WINDOW)
    _proc_cache["at"] = 0.0
    return True, f"Started {strategy} " + ("(LIVE - real orders)." if live else "(dry run).")


def stop_bot() -> tuple[bool, str]:
    _proc_cache["at"] = 0.0
    pids = find_bot_pids()
    if not pids:
        return False, "No bot is running."
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=_NO_WINDOW)
    _proc_cache["at"] = 0.0
    return True, "Stopped. Any open simulated position was discarded; closed trades are kept in the CSV."


def read_trades() -> list[dict]:
    trades = []
    for path in sorted(ROOT.glob(f"{CSV_PREFIX}*.csv")):
        strategy = path.stem[len(CSV_PREFIX):]
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    trades.append({
                        "time": row["timestamp"], "strategy": strategy, "symbol": row["symbol"],
                        "entry": float(row["entry_price"]), "exit": float(row["exit_price"]),
                        "qty": float(row["qty"]), "reason": row["exit_reason"],
                        "gross": float(row["gross_pnl"]), "fees": float(row["fees"]),
                        "net": float(row["net_pnl"]),
                    })
                except (KeyError, ValueError):
                    continue
    trades.sort(key=lambda t: t["time"])
    return trades


def compute_stats(trades: list[dict], start_capital: float) -> tuple[dict, list[dict]]:
    equity = start_capital
    peak = start_capital
    max_dd = 0.0
    curve = []
    for i, t in enumerate(trades, 1):
        equity += t["net"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        curve.append({"n": i, "time": t["time"], "equity": equity, "net": t["net"]})

    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [t["net"] for t in trades if t["net"] <= 0]
    gross_loss = abs(sum(losses))
    today = datetime.now().strftime("%Y-%m-%d")
    net = sum(t["net"] for t in trades)
    stats = {
        "start_capital": start_capital,
        "equity": equity,
        "net": net,
        "net_pct": net / start_capital * 100 if start_capital else 0.0,
        "gross": sum(t["gross"] for t in trades),
        "fees": sum(t["fees"] for t in trades),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": sum(wins) / gross_loss if gross_loss > 0 else None,
        "max_drawdown_pct": max_dd,
        "today_net": sum(t["net"] for t in trades if t["time"].startswith(today)),
    }
    return stats, curve


def read_log_tail() -> list[str]:
    if not LOG_PATH.exists():
        return []
    data = LOG_PATH.read_bytes()[-40000:]
    text = data.decode("utf-16", "replace") if data[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else data.decode("utf-8", "replace")
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()][-LOG_TAIL_LINES:]


def current_position(log_lines: list[str]) -> dict:
    status = next((ln for ln in reversed(log_lines) if "position=" in ln), None)
    if status is None:
        return {"status_line": None, "side": None, "detail": None}
    side = re.search(r"position=(\w+)\s*$", status)
    side = side.group(1) if side else None
    detail = None
    if side and side != "None":
        detail = next((ln.strip() for ln in reversed(log_lines)
                       if ln.strip().startswith("entry=")), None)
    return {"status_line": status, "side": side if side != "None" else None, "detail": detail}


def build_state() -> dict:
    cfg = load_config()
    trades = read_trades()
    stats, curve = compute_stats(trades, float(cfg["account_capital"]))
    log_lines = read_log_tail()
    pids = find_bot_pids()
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "bot": {"running": bool(pids), "pids": pids},
        "config": {"symbol": cfg["symbol"], "dry_run": cfg.get("dry_run") is True,
                   "account_capital": cfg["account_capital"],
                   "risk_per_trade_pct": cfg["risk_per_trade_pct"],
                   "leverage": cfg["leverage"]["value"]},
        "strategies": list(STRATEGIES),
        "stats": stats,
        "curve": curve,
        "trades": list(reversed(trades))[:MAX_TRADES_SHOWN],
        "position": current_position(log_lines),
        "log": log_lines,
    }


app = FastAPI(title="Shark Trader Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
# Only answer requests addressed to this machine (blocks DNS-rebinding from web pages).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


def require_dashboard_header(x_requested_with: str | None = Header(default=None)):
    # A custom header forces a CORS preflight from any other origin, which we never
    # answer, so other websites can't drive start/stop from the user's browser.
    if x_requested_with != "dashboard":
        raise HTTPException(status_code=403, detail="forbidden")


class StartRequest(BaseModel):
    strategy: str
    confirm_live: bool = False


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/state")
def state():
    return build_state()


@app.post("/api/start", dependencies=[Depends(require_dashboard_header)])
def api_start(req: StartRequest):
    ok, msg = start_bot(req.strategy, req.confirm_live)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 409)


@app.post("/api/stop", dependencies=[Depends(require_dashboard_header)])
def api_stop():
    ok, msg = stop_bot()
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 409)


class CredentialsRequest(BaseModel):
    api_key: SecretStr
    api_secret: SecretStr


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    return SETTINGS_PAGE


@app.get("/api/settings")
def settings_status():
    """Whether credentials exist and where from. Never returns the secret
    (or the full key) - only the last 4 characters of the key."""
    saved_key, saved_secret = get_credentials()
    cfg = load_config()
    mode = {"dry_run": cfg.get("dry_run") is True}
    if saved_key and saved_secret:
        return {"configured": True, "source": "credentials.json", "key_hint": key_hint(saved_key), **mode}
    if cfg["api_key"] and cfg["api_secret"]:
        return {"configured": True, "source": "environment / .env", "key_hint": key_hint(cfg["api_key"]), **mode}
    return {"configured": False, "source": None, "key_hint": "", **mode}


@app.post("/api/settings", dependencies=[Depends(require_dashboard_header)])
def save_settings(req: CredentialsRequest):
    key = req.api_key.get_secret_value().strip()
    secret = req.api_secret.get_secret_value().strip()
    for label, v in (("API key", key), ("API secret", secret)):
        if not (8 <= len(v) <= 512) or any(c.isspace() for c in v):
            return JSONResponse({"ok": False, "message": f"{label} looks invalid (8-512 characters, no spaces)."},
                                status_code=422)
    set_credentials(key, secret)
    return {"ok": True, "message": "Saved to credentials.json (gitignored). Restart a running bot to use the new keys.",
            "key_hint": key_hint(key)}


@app.post("/api/settings/clear", dependencies=[Depends(require_dashboard_header)])
def clear_settings():
    clear_credentials()
    return {"ok": True, "message": "Removed credentials.json."}


LIVE_CONFIRM_PHRASE = "REAL MONEY"


class ModeRequest(BaseModel):
    mode: str  # "dry_run" or "live"
    confirm: str = ""


@app.post("/api/settings/mode", dependencies=[Depends(require_dashboard_header)])
def set_mode(req: ModeRequest):
    _proc_cache["at"] = 0.0
    if find_bot_pids():
        return JSONResponse({"ok": False, "message": "Stop the running bot before changing the mode."},
                            status_code=409)
    if req.mode == "dry_run":
        set_dry_run(True)
        return {"ok": True, "message": "Switched to dry run - simulated trades only, no real orders."}
    if req.mode != "live":
        return JSONResponse({"ok": False, "message": "Unknown mode."}, status_code=422)
    cfg = load_config()
    if not (cfg["api_key"] and cfg["api_secret"]):
        return JSONResponse({"ok": False, "message": "Save your API key and secret before enabling real-account mode."},
                            status_code=409)
    if req.confirm.strip() != LIVE_CONFIRM_PHRASE:
        return JSONResponse({"ok": False, "message": f"Type {LIVE_CONFIRM_PHRASE} exactly to confirm."},
                            status_code=422)
    set_dry_run(False)
    return {"ok": True, "message": "Real-account mode is ON. The next Start will place real orders."}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shark Trader Dashboard</title>
<style>
:root{
  --bg:#f6f7f9; --card:#fff; --ink:#14181f; --ink2:#4a5361; --muted:#7b8494;
  --line:#e3e6ec; --grid:#eceef2; --accent:#2b6cf0; --good:#12805c; --bad:#c2382f;
  --goodbg:#e4f4ec; --badbg:#fbe9e7; --warnbg:#fff3d6; --warn:#8a5a00;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0f1218; --card:#171b23; --ink:#e8ebf0; --ink2:#a9b1bf; --muted:#7c8697;
    --line:#2a303b; --grid:#222833; --accent:#6d9bff; --good:#4cc79a; --bad:#ff7d73;
    --goodbg:#16352a; --badbg:#3a1f1c; --warnbg:#3a2f12; --warn:#f0c15a;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:16px}
h1{font-size:20px;margin:0}
.sub{color:var(--muted);font-size:13px;margin-top:2px}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
select,button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:8px 12px}
button{cursor:pointer;font-weight:600}
button.start{background:var(--accent);border-color:var(--accent);color:#fff}
button.stop{background:var(--bad);border-color:var(--bad);color:#fff}
button:disabled{opacity:.45;cursor:not-allowed}
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:4px 10px;
  font-size:12px;font-weight:700;border:1px solid var(--line);background:var(--card)}
.pill.run{background:var(--goodbg);color:var(--good);border-color:transparent}
.pill.off{background:var(--badbg);color:var(--bad);border-color:transparent}
.pill.dry{background:var(--warnbg);color:var(--warn);border-color:transparent}
.dot{width:8px;height:8px;border-radius:50%;background:currentColor}
.msg{min-height:20px;font-size:13px;color:var(--ink2);margin:0 0 10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.k .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.k .v{font-size:24px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.k .s{color:var(--ink2);font-size:12px;margin-top:2px}
.good{color:var(--good)} .bad{color:var(--bad)}
h2{font-size:14px;margin:0 0 10px}
.chartbox{position:relative}
svg{display:block;width:100%;height:auto}
.tip{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);
  transform:translate(-50%,-110%);white-space:nowrap;display:none}
.empty{color:var(--muted);padding:28px 0;text-align:center}
.grid2{display:grid;grid-template-columns:1fr;gap:12px;margin-top:12px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--muted);font-size:12px;font-weight:600}
pre{margin:0;max-height:300px;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace;
  color:var(--ink2);white-space:pre-wrap;word-break:break-word}
.foot{color:var(--muted);font-size:12px;margin-top:14px}
a.navlink{color:var(--ink2);text-decoration:none;border:1px solid var(--line);border-radius:8px;
  padding:8px 12px;background:var(--card);font-weight:600}
label{display:block;font-size:12px;color:var(--muted);margin:14px 0 4px}
input[type=password],input[type=text]{width:100%;font:inherit;color:var(--ink);background:var(--bg);
  border:1px solid var(--line);border-radius:8px;padding:9px 12px}
.narrow{max-width:560px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Shark Trader</h1>
      <div class="sub" id="sub">loading...</div>
    </div>
    <div class="controls">
      <span class="pill" id="runpill"></span>
      <span class="pill dry" id="drypill" hidden>DRY RUN</span>
      <span class="pill off" id="livepill" hidden>LIVE - REAL MONEY</span>
      <a class="navlink" href="/settings">Settings</a>
      <select id="strategy" aria-label="Strategy"></select>
      <button class="start" id="startbtn">Start</button>
      <button class="stop" id="stopbtn">Stop</button>
    </div>
  </header>
  <p class="msg" id="msg" role="status"></p>

  <section class="kpis" id="kpis"></section>

  <section class="card">
    <h2>Equity (simulated: starting capital + cumulative net P&amp;L)</h2>
    <div class="chartbox" id="chartbox"></div>
  </section>

  <div class="grid2">
    <section class="card"><h2>Open position</h2><div id="position"></div></section>
    <section class="card"><h2>Recent trades</h2><div class="tablewrap" id="trades"></div></section>
    <section class="card"><h2>Bot output</h2><pre id="log"></pre></section>
  </div>
  <p class="foot">Balance here is calculated from the bot's trade log (starting capital + cumulative net P&amp;L), not read from your exchange wallet.
  In dry-run mode Stop discards any open simulated position; in real-account mode Stop does not close a real position (closed trades stay in the CSV).
  Switch between dry run and real account on the Settings page.</p>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = (n, d=2) => n == null ? "—" : Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const signed = (n, d=2) => n == null ? "—" : (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n), d);
const cls = n => n > 0 ? "good" : n < 0 ? "bad" : "";

function el(tag, props, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(props||{})) { if (k === "class") e.className = v; else e.setAttribute(k, v); }
  for (const kid of kids) e.append(kid);
  return e;
}

async function api(path, body){
  const r = await fetch(path, body === undefined ? {} : {
    method:"POST", headers:{"Content-Type":"application/json","X-Requested-With":"dashboard"},
    body: JSON.stringify(body)});
  return r.json();
}

let state = null, busy = false;

function renderKpis(s){
  const pf = s.profit_factor == null ? (s.wins > 0 ? "∞" : "—") : fmt(s.profit_factor);
  const items = [
    ["Equity", fmt(s.equity) + " ₹", "start " + fmt(s.start_capital) + " ₹", ""],
    ["Net P&L", signed(s.net) + " ₹", signed(s.net_pct) + "%  ·  today " + signed(s.today_net), cls(s.net)],
    ["Win rate", s.win_rate == null ? "—" : fmt(s.win_rate,1) + "%", s.wins + "W / " + s.losses + "L", ""],
    ["Profit factor", pf, "avg win " + signed(s.avg_win) + " / loss " + signed(s.avg_loss), ""],
    ["Max drawdown", fmt(s.max_drawdown_pct) + "%", "fees paid " + fmt(s.fees) + " ₹", ""],
    ["Trades", String(s.trades), "closed", ""],
  ];
  const root = $("kpis"); root.replaceChildren();
  for (const [l,v,sub,c] of items)
    root.append(el("div",{class:"card k"}, el("div",{class:"l"},l), el("div",{class:"v "+c},v), el("div",{class:"s"},sub)));
}

function renderChart(curve, start){
  const box = $("chartbox"); box.replaceChildren();
  if (curve.length < 1){ box.append(el("div",{class:"empty"},"No closed trades yet.")); return; }
  const pts = [{n:0,time:"start",equity:start,net:0}, ...curve];
  const W=800,H=240,L=58,R=64,T=14,B=26;
  const ys = pts.map(p=>p.equity), lo0 = Math.min(...ys), hi0 = Math.max(...ys);
  const pad = (hi0-lo0) * 0.15 || Math.max(1, hi0*0.001);
  const lo = lo0 - pad, hi = hi0 + pad;
  const x = i => L + (W-L-R) * (pts.length === 1 ? 0 : i/(pts.length-1));
  const y = v => T + (H-T-B) * (1 - (v-lo)/(hi-lo));
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k,v] of Object.entries(attrs)) e.setAttribute(k,v); return e; };
  const svg = s("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"Equity curve"});
  for (let i=0;i<=3;i++){
    const v = lo + (hi-lo)*i/3, yy = y(v);
    svg.append(s("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:"var(--grid)","stroke-width":1}));
    const t = s("text",{x:L-8,y:yy+4,"text-anchor":"end","font-size":11,fill:"var(--muted)"}); t.textContent = fmt(v,0); svg.append(t);
  }
  if (start >= lo && start <= hi)
    svg.append(s("line",{x1:L,x2:W-R,y1:y(start),y2:y(start),stroke:"var(--muted)","stroke-width":1,"stroke-dasharray":"4 4"}));
  svg.append(s("path",{d:pts.map((p,i)=>(i?"L":"M")+x(i).toFixed(1)+" "+y(p.equity).toFixed(1)).join(" "),
    fill:"none",stroke:"var(--accent)","stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"}));
  const last = pts[pts.length-1];
  svg.append(s("circle",{cx:x(pts.length-1),cy:y(last.equity),r:4,fill:"var(--accent)",stroke:"var(--card)","stroke-width":2}));
  const lab = s("text",{x:x(pts.length-1)+8,y:y(last.equity)+4,"font-size":12,"font-weight":700,fill:"var(--ink)"});
  lab.textContent = fmt(last.equity); svg.append(lab);
  const xt = s("text",{x:L,y:H-6,"font-size":11,fill:"var(--muted)"}); xt.textContent = "start"; svg.append(xt);
  const xe = s("text",{x:W-R,y:H-6,"font-size":11,"text-anchor":"end",fill:"var(--muted)"}); xe.textContent = "trade " + (pts.length-1); svg.append(xe);

  const cross = s("line",{y1:T,y2:H-B,stroke:"var(--muted)","stroke-width":1,visibility:"hidden"});
  const dot = s("circle",{r:4,fill:"var(--accent)",stroke:"var(--card)","stroke-width":2,visibility:"hidden"});
  svg.append(cross, dot);
  const tip = el("div",{class:"tip"});
  const hit = s("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"});
  hit.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) / r.width * W;
    const i = Math.max(0, Math.min(pts.length-1, Math.round((px-L)/(W-L-R)*(pts.length-1))));
    const p = pts[i];
    cross.setAttribute("x1",x(i)); cross.setAttribute("x2",x(i)); cross.setAttribute("visibility","visible");
    dot.setAttribute("cx",x(i)); dot.setAttribute("cy",y(p.equity)); dot.setAttribute("visibility","visible");
    tip.replaceChildren(el("b",{},fmt(p.equity)+" ₹"), document.createElement("br"),
      document.createTextNode(i===0 ? "starting capital" : `trade ${p.n}: ${signed(p.net)}  ·  ${p.time.replace("T"," ")}`));
    tip.style.display = "block";
    tip.style.left = (x(i)/W*100) + "%"; tip.style.top = (y(p.equity)/H*100) + "%";
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility","hidden"); dot.setAttribute("visibility","hidden"); tip.style.display="none"; });
  svg.append(hit);
  box.append(svg, tip);
}

function renderPosition(p){
  const box = $("position"); box.replaceChildren();
  if (!p.status_line){ box.append(el("div",{class:"empty"},"No bot output yet.")); return; }
  box.append(el("div",{}, el("b",{}, p.side ? "In a " + p.side + " position" : "Flat (no open position)")));
  if (p.detail) box.append(el("div",{class:"sub"},p.detail));
  box.append(el("div",{class:"sub"},"Last status: " + p.status_line.trim()));
}

function renderTrades(trades){
  const box = $("trades"); box.replaceChildren();
  if (!trades.length){ box.append(el("div",{class:"empty"},"No closed trades yet.")); return; }
  const head = ["Closed","Strategy","Symbol","Entry","Exit","Qty","Exit reason","Fees","Net P&L"];
  const tbl = el("table"), thead = el("thead"), hr = el("tr");
  head.forEach(h => hr.append(el("th",{},h))); thead.append(hr); tbl.append(thead);
  const tb = el("tbody");
  for (const t of trades){
    const tr = el("tr");
    [t.time.replace("T"," "), t.strategy, t.symbol, fmt(t.entry), fmt(t.exit), fmt(t.qty,4), t.reason, fmt(t.fees)]
      .forEach(v => tr.append(el("td",{},v)));
    tr.append(el("td",{class:cls(t.net)}, signed(t.net)));
    tb.append(tr);
  }
  tbl.append(tb); box.append(tbl);
}

function render(d){
  state = d;
  const c = d.config;
  $("sub").textContent = `${c.symbol} · capital ${fmt(c.account_capital,0)} ₹ · risk ${c.risk_per_trade_pct}% · ${c.leverage}x · updated ${d.now.replace("T"," ")}`;
  const rp = $("runpill"); rp.className = "pill " + (d.bot.running ? "run" : "off");
  rp.replaceChildren(el("span",{class:"dot"}), document.createTextNode(d.bot.running ? "Running" : "Stopped"));
  $("drypill").hidden = !c.dry_run;
  $("livepill").hidden = c.dry_run;
  $("startbtn").textContent = c.dry_run ? "Start" : "Start LIVE";
  const sel = $("strategy");
  if (!sel.options.length) d.strategies.forEach(n => sel.append(new Option(n, n)));
  if (!sel.dataset.set){ sel.value = d.strategies.includes("psar_trend") ? "psar_trend" : d.strategies[0]; sel.dataset.set = "1"; }
  $("startbtn").disabled = busy || d.bot.running;
  $("stopbtn").disabled = busy || !d.bot.running;
  renderKpis(d.stats);
  renderChart(d.curve, d.stats.start_capital);
  renderPosition(d.position);
  renderTrades(d.trades);
  const log = $("log"), atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.textContent = d.log.length ? d.log.join("\n") : "No dashboard-started bot output yet.";
  if (atBottom) log.scrollTop = log.scrollHeight;
}

async function refresh(){
  try { render(await api("/api/state")); }
  catch(e){ $("msg").textContent = "Can't reach the dashboard server."; }
}

async function act(path, body){
  busy = true; if (state) render(state);
  try { const r = await api(path, body); $("msg").textContent = r.message || r.error || ""; }
  catch(e){ $("msg").textContent = "Request failed."; }
  busy = false; setTimeout(refresh, 1500); refresh();
}

$("startbtn").addEventListener("click", () => {
  const live = state && !state.config.dry_run;
  if (live && !confirm("Start a LIVE run? This will place REAL orders on your Shark Exchange account.")) return;
  act("/api/start", {strategy: $("strategy").value, confirm_live: live});
});
$("stopbtn").addEventListener("click", () => {
  const live = state && !state.config.dry_run;
  const warn = live ? "Stop the bot? It will NOT close any real open position - check the exchange."
                    : "Stop the bot? An open simulated position will be discarded.";
  if (confirm(warn)) act("/api/stop", {});
});
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""


_STYLE = re.search(r"<style>.*?</style>", PAGE, re.S).group(0)

SETTINGS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shark Trader Settings</title>
__STYLE__
</head>
<body>
<div class="wrap narrow">
  <header>
    <div><h1>Settings</h1><div class="sub">Exchange API credentials</div></div>
    <a class="navlink" href="/">&larr; Dashboard</a>
  </header>
  <section class="card">
    <h2>Current status</h2>
    <div id="status" class="sub">loading...</div>
  </section>
  <section class="card" style="margin-top:12px">
    <h2>Trading mode</h2>
    <div id="modestatus" class="sub">loading...</div>
    <div id="toreal" hidden>
      <label for="phrase">To switch to the real account, type REAL MONEY</label>
      <input id="phrase" type="text" autocomplete="off" spellcheck="false">
      <div class="controls" style="margin-top:10px"><button class="stop" type="button" id="golive">Switch to real account</button></div>
    </div>
    <div id="todry" class="controls" style="margin-top:10px" hidden>
      <button class="start" type="button" id="godry">Switch to dry run (safe)</button>
    </div>
    <p class="msg" id="modemsg" role="status"></p>
    <p class="foot">Dry run only simulates trades. Real account places real orders with the saved API keys.
    The mode is stored in the gitignored <code>local_settings.json</code>, not in <code>config.json</code>, and can only be
    changed while no bot is running. The real-account path of this bot has never been verified end to end.</p>
  </section>
  <section class="card" style="margin-top:12px">
    <h2>Save API key and secret</h2>
    <form id="form" autocomplete="off">
      <label for="k">API key</label>
      <input id="k" type="password" autocomplete="off" spellcheck="false" required>
      <label for="s">API secret</label>
      <input id="s" type="password" autocomplete="off" spellcheck="false" required>
      <p class="msg" id="msg" role="status"></p>
      <div class="controls">
        <button class="start" type="submit">Save</button>
        <button class="stop" type="button" id="clear">Remove saved credentials</button>
      </div>
    </form>
    <p class="foot">Saved as plain text in <code>credentials.json</code> next to the code, on the machine running
    this dashboard. The file is gitignored, so it is never committed or pushed. The secret is never shown again
    after saving. A bot that is already running keeps its old keys until restarted.</p>
  </section>
</div>
<script>
const $ = id => document.getElementById(id);
async function call(path, body){
  const opt = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json","X-Requested-With":"dashboard"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  return r.json();
}
async function refresh(){
  const d = await call("/api/settings");
  $("status").textContent = d.configured
    ? "Configured (key " + d.key_hint + ", from " + d.source + ")"
    : "No credentials saved yet.";
  const dry = d.dry_run;
  $("modestatus").textContent = dry ? "Current mode: DRY RUN (simulated, no real orders)" : "Current mode: REAL ACCOUNT (live orders)";
  $("toreal").hidden = !dry;
  $("todry").hidden = dry;
}
async function setMode(mode, confirmText){
  const r = await call("/api/settings/mode", {mode, confirm: confirmText || ""});
  $("modemsg").textContent = r.message || "Could not change mode.";
  $("phrase").value = "";
  refresh();
}
$("golive").addEventListener("click", () => setMode("live", $("phrase").value));
$("godry").addEventListener("click", () => setMode("dry_run"));
$("form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const r = await call("/api/settings", {api_key: $("k").value, api_secret: $("s").value});
  $("msg").textContent = r.message || "Could not save.";
  if (r.ok){ $("k").value = ""; $("s").value = ""; }
  refresh();
});
$("clear").addEventListener("click", async () => {
  if (!confirm("Remove the saved credentials file?")) return;
  const r = await call("/api/settings/clear", {});
  $("msg").textContent = r.message || "";
  refresh();
});
refresh();
</script>
</body>
</html>
""".replace("__STYLE__", _STYLE)


def main():
    print(f"Dashboard running at http://{HOST}:{PORT}  (Ctrl+C to stop; the bot keeps running)")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
