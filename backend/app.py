"""FastAPI app: serves the dashboard and a cached JSON API.

The server refreshes each requested symbol from the data source at most once
every REFRESH_SECONDS and caches the result in memory, so the browser can poll
freely without hammering NSE.
"""
from __future__ import annotations

import csv as _csv
import hashlib
import io
import os
import threading
import time
from pathlib import Path
from typing import Optional

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles

from . import analytics, config
from .sources import get_source
from .sources.base import AuthRequired
from .journal import store as jstore, rules as jrules, analytics as janalytics
from .journal import csvimport as jcsv, sync as jsync, dhan as jdhan

_IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")

app = FastAPI(title="OI Dashboard")

# Optional single-password gate. Off unless JOURNAL_PASSWORD is set in the env
# (or .env). When off, behaviour is unchanged. Suitable for a personal tool —
# not a multi-user auth system.
_APP_PASSWORD = os.getenv("JOURNAL_PASSWORD", "").strip()


def _auth_token() -> str:
    return hashlib.sha256(("oi-journal:" + _APP_PASSWORD).encode()).hexdigest()


@app.middleware("http")
async def _gate_and_cache(request: Request, call_next):
    # 1) password gate (only when enabled)
    if _APP_PASSWORD:
        path = request.url.path
        allowed = path in ("/login", "/healthz")
        if not allowed and request.cookies.get("jpass") != _auth_token():
            if path.startswith("/api/") or path.startswith("/auth/"):
                return JSONResponse({"detail": "auth_required"}, status_code=401)
            return RedirectResponse("/login")
    resp = await call_next(request)
    # 2) always-revalidate JS/CSS so edits reach the browser without ?v= bumps
    if request.url.path.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.get("/login")
def login_page(bad: int = 0):
    err = '<p style="color:#f87171;margin:0 0 10px">Wrong password.</p>' if bad else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Login</title>
<style>body{{font-family:system-ui,sans-serif;background:#07070e;color:#eee;display:grid;
place-items:center;height:100vh;margin:0}}form{{background:#14141f;padding:30px;border-radius:16px;
border:1px solid #2a2a3a;min-width:300px;box-shadow:0 20px 60px -20px rgba(91,141,239,.4)}}
h3{{margin:0 0 14px}}input{{width:100%;box-sizing:border-box;padding:11px;margin:6px 0 12px;
border-radius:9px;border:1px solid #333;background:#0b0b12;color:#eee;font-size:15px}}
button{{width:100%;padding:11px;border:0;border-radius:9px;background:#5b8def;color:#fff;
font-weight:600;font-size:15px;cursor:pointer}}</style></head><body>
<form method="post" action="/login"><h3>\U0001f4c8 Option OI Dashboard</h3>{err}
<input type="password" name="password" placeholder="Password" autofocus>
<button>Log in</button></form></body></html>""")


@app.post("/login")
async def login_submit(request: Request):
    # Parse the urlencoded body manually so we don't need python-multipart.
    from urllib.parse import parse_qs
    raw = (await request.body()).decode("utf-8", errors="replace")
    password = (parse_qs(raw).get("password", [""])[0])
    if password == _APP_PASSWORD and _APP_PASSWORD:
        r = RedirectResponse("/", status_code=303)
        r.set_cookie("jpass", _auth_token(), httponly=True, samesite="lax", max_age=30 * 86400)
        return r
    return RedirectResponse("/login?bad=1", status_code=303)


_source = get_source()
_lock = threading.Lock()
# cache[symbol] = {"at": epoch, "chain": OptionChain, "error": str|None}
_cache: dict[str, dict] = {}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _get_chain(symbol: str, expiry: Optional[str] = None, force: bool = False):
    """Return a cached OptionChain, refreshing if older than REFRESH_SECONDS.

    Cache is keyed by (symbol, expiry) so switching expiry works but each
    distinct view is still throttled to one source call per REFRESH_SECONDS.
    """
    symbol = symbol.upper()
    key = f"{symbol}|{expiry or ''}"
    now = time.time()
    with _lock:
        entry = _cache.get(key)
        fresh = entry and (now - entry["at"] < config.REFRESH_SECONDS) and entry.get("chain")
        if fresh and not force:
            return entry["chain"], entry["at"], None
    # Fetch outside the lock so slow NSE calls don't block other symbols.
    try:
        chain = _source.get_option_chain(symbol, expiry)
        with _lock:
            _cache[key] = {"at": time.time(), "chain": chain, "error": None}
        return chain, time.time(), None
    except AuthRequired:
        # Propagate so the API layer can return a dedicated "log in" response.
        raise
    except Exception as e:  # noqa: BLE001 - surface source errors to the client
        with _lock:
            entry = _cache.get(key)
            if entry and entry.get("chain"):
                # Serve stale data but report the error.
                return entry["chain"], entry["at"], str(e)
            _cache[key] = {"at": now, "chain": None, "error": str(e)}
        return None, None, str(e)


@app.get("/api/config")
def api_config():
    return {
        "symbols": config.SYMBOLS,
        "refresh_seconds": config.REFRESH_SECONDS,
        "source": _source.name,
        "needs_login": _source.needs_login(),
        "login_label": getattr(_source, "login_label", "Log in"),
    }


@app.get("/auth/login")
def auth_login():
    """Redirect the browser to the active broker's login/consent page."""
    try:
        url = _source.login_url()
    except AuthRequired as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not url:
        raise HTTPException(status_code=400, detail="Active data source does not support login.")
    return RedirectResponse(url)


@app.get("/auth/callback")
def auth_callback(code: Optional[str] = None, auth_code: Optional[str] = None,
                  state: Optional[str] = None, error: Optional[str] = None):
    """Broker redirects here after login. Upstox sends ?code=, Fyers sends ?auth_code=
    (Fyers also sends a numeric status `code`, so auth_code must win)."""
    token_code = auth_code or code
    if error:
        return RedirectResponse(f"/?login_error={quote(str(error)[:180])}")
    if not token_code:
        return RedirectResponse("/?login_error=missing_auth_code")
    try:
        _source.exchange_code(token_code)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/?login_error={quote(str(e)[:180])}")
    # Drop any cached auth-error entries so the next poll fetches fresh data.
    with _lock:
        _cache.clear()
    return RedirectResponse("/?login=ok")


@app.get("/api/snapshot")
def api_snapshot(
    symbol: str = Query("NIFTY"),
    expiry: Optional[str] = Query(None),
    force: bool = Query(False),
):
    symbol = symbol.upper()
    try:
        chain, fetched_at, error = _get_chain(symbol, expiry=expiry, force=force)
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail="login_required") from e
    if chain is None:
        raise HTTPException(status_code=502, detail=error or "No data available")
    payload = chain.as_dict()
    payload["summary"] = analytics.summarize(chain)
    payload["fetched_at"] = fetched_at
    payload["stale_error"] = error  # non-null => showing cached data after a failed refresh
    return JSONResponse(payload)


@app.get("/healthz")
def healthz():
    return {"ok": True, "source": _source.name,
            "store": getattr(jstore, "BACKEND", "sqlite")}


# ----------------------------------------------------------------- journal
@app.get("/api/journal/overview")
def journal_overview(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    today = _today_ist()
    return {
        "source": _source.name,
        "can_sync": _source.name == "fyers",
        "dhan_connected": jdhan.is_connected(),
        "summary": janalytics.summary(start, end),
        "range": {"start": start, "end": end},
        "targets": janalytics.targets_progress(),
        "today": jrules.evaluate(today),
        "rules_config": jrules.get_rules(),
        "targets_config": janalytics.get_targets(),
        "recent_trades": _trades_with_tags(300, start, end),
    }


def _trades_with_tags(limit: int, start: Optional[str] = None, end: Optional[str] = None):
    tags = jstore.tags_map()
    if start or end:
        # within the selected range (undated 'unknown' trades fall outside any date range)
        rows = jstore.trades_between(start or "0000-01-01", end or "9999-12-31")
    else:
        rows = jstore.recent_trades(limit)
    for t in rows:
        t["tag"] = tags.get(t["id"])
    return rows


@app.get("/api/journal/export.csv")
def journal_export(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    """Download trades (respecting the active date range) as CSV."""
    rows = _trades_with_tags(1_000_000, start, end)
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["date", "time", "symbol", "side", "qty", "price", "value", "pnl",
                "closed", "source", "setup", "on_plan", "note"])
    for t in rows:
        tag = t.get("tag") or {}
        on = tag.get("on_plan")
        w.writerow([
            t.get("date", ""), t.get("time", ""), t.get("symbol", ""), t.get("side", ""),
            t.get("qty", ""), t.get("price", ""), t.get("value", ""), t.get("pnl", ""),
            t.get("closed", ""), t.get("source", ""), tag.get("setup", "") or "",
            "" if on is None else on, tag.get("note", "") or "",
        ])
    fn = "trades"
    if start or end:
        fn += f"_{start or 'start'}_to_{end or 'end'}"
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fn}.csv"'})


@app.get("/api/journal/analytics")
def journal_analytics(period: str = Query("all"), symbol: str = Query("all"),
                      start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    data = janalytics.insights(period, symbol, start, end)
    data["instruments_available"] = janalytics.available_instruments()
    return data


@app.post("/api/journal/trades/tag")
async def journal_tag_trade(request: Request):
    d = await request.json()
    tid = (d.get("id") or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="trade id required")
    on = d.get("on_plan")
    on_val = 1 if on in (1, True, "1", "on") else 0 if on in (0, False, "0", "off") else None
    jstore.set_tag(tid, setup=(d.get("setup") or None), note=(d.get("note") or None), on_plan=on_val)
    return {"ok": True}


@app.post("/api/journal/sync")
def journal_sync():
    try:
        return jsync.sync_today(_source)
    except AuthRequired:
        raise HTTPException(status_code=401, detail="login_required")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Sync failed: {e}")


@app.post("/api/journal/import")
async def journal_import(request: Request):
    content = (await request.body()).decode("utf-8", errors="replace")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty CSV.")
    result = jcsv.import_csv(content)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "Import failed"))
    return result


@app.get("/api/journal/dhan/status")
def dhan_status():
    return {"connected": jdhan.is_connected()}


@app.post("/api/journal/dhan/connect")
async def dhan_connect(request: Request):
    d = await request.json()
    jdhan.set_creds(d.get("client_id", ""), d.get("access_token", ""))
    return {"connected": jdhan.is_connected()}


@app.post("/api/journal/clear")
async def journal_clear(request: Request):
    """Delete trades from the journal, by source or all. Keeps settings/rules."""
    data = await request.json()
    src = (data.get("source") or "").strip().lower()
    if src in ("all", "", "*"):
        jstore.clear_all_trades()
    elif src in ("dhan", "fyers", "csv", "upstox"):
        jstore.clear_source(src)
    else:
        raise HTTPException(status_code=400, detail="unknown source")
    jstore.rebuild_daily_from_trades()
    return {"ok": True, "cleared": src or "all"}


@app.post("/api/journal/dhan/sync")
async def dhan_sync(request: Request):
    try:
        d = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        d = {}
    frm = (d.get("from") or jsync.fy_start()).strip()
    to = (d.get("to") or _today_ist()).strip()
    try:
        return jsync.sync_dhan(frm, to)
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Dhan sync failed: {e}")


@app.get("/api/journal/rules/analysis")
def journal_rules_analysis():
    return jrules.analyze_history()


@app.get("/api/journal/plan")
def journal_plan():
    """A plain-language action plan built from the trader's own history + rules."""
    return janalytics.action_plan(jrules.analyze_history(), jrules.get_rules())


@app.get("/api/journal/rules")
def journal_get_rules():
    return jrules.get_rules()


@app.post("/api/journal/rules")
async def journal_set_rules(request: Request):
    data = await request.json()
    return jrules.set_rules(data)


@app.get("/api/journal/targets")
def journal_get_targets():
    return janalytics.get_targets()


@app.post("/api/journal/targets")
async def journal_set_targets(request: Request):
    data = await request.json()
    return janalytics.set_targets(data)


@app.get("/api/journal/capital-tracker")
def journal_capital_tracker(month: Optional[str] = Query(None)):
    data = janalytics.capital_tracker(month)
    data["can_sync"] = _source.name == "fyers"      # Fyers = today-only sync
    data["dhan_connected"] = jdhan.is_connected()
    return data


@app.post("/api/journal/capital")
async def journal_set_capital(request: Request):
    data = await request.json()
    janalytics.set_capital(data.get("opening"))
    if "brokerage" in data or "charges" in data:
        month = (data.get("month") or _today_ist()[:7])
        janalytics.set_month_costs(month, data.get("brokerage"), data.get("charges"))
    if "brokerage_rate" in data:
        janalytics.set_brokerage_rate(data.get("brokerage_rate"))
    return janalytics.capital_tracker(data.get("month"))


@app.post("/api/journal/daynote")
async def journal_set_daynote(request: Request):
    data = await request.json()
    day = (data.get("date") or "").strip()
    if not day:
        raise HTTPException(status_code=400, detail="date required")
    janalytics.set_day_note(day, data.get("note") or "")
    return {"ok": True}


# Serve index.html with no-cache so structural HTML changes (and the ?v= asset
# bumps inside it) always reach the browser without a hard refresh. The versioned
# static assets themselves can still be cached normally.
@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# Serve the rest of the static dashboard.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
