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

from . import analytics, config, screener, fundamentals, ipo, symbols
symbols.warm_async()
from .sources import get_source
from .sources.base import AuthRequired
from .journal import store as jstore, rules as jrules, analytics as janalytics
from .journal import csvimport as jcsv, sync as jsync, dhan as jdhan

_IST = timezone(timedelta(hours=5, minutes=30))

# NSE equity hours (IST) and weekday holidays — mirror of the frontend list.
_MARKET_OPEN_MIN = 9 * 60 + 15      # 09:15
_MARKET_CLOSE_MIN = 15 * 60 + 30    # 15:30
_NSE_HOLIDAYS = {
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
}


def _today_ist() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _market_open(now: Optional[datetime] = None) -> bool:
    """True only during NSE equity trading hours (Mon–Fri 09:15–15:30 IST,
    excluding holidays). Prices don't move otherwise, so we never scan then."""
    now = now or datetime.now(_IST)
    if now.weekday() >= 5:                       # Sat/Sun
        return False
    if now.strftime("%Y-%m-%d") in _NSE_HOLIDAYS:
        return False
    mins = now.hour * 60 + now.minute
    return _MARKET_OPEN_MIN <= mins < _MARKET_CLOSE_MIN

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
        allowed = path in ("/login", "/healthz", "/api/stock/fundamentals_diag", "/api/ipos_diag")
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


_BROKERS = ("fyers", "dhan", "upstox", "mock", "nse")
_ISOLATED_BROKERS = ("fyers", "dhan", "upstox")
_lock = threading.Lock()
# cache[symbol] = {"at": epoch, "chain": OptionChain, "error": str|None}
_cache: dict[str, dict] = {}


def _apply_source_scope(name: str) -> None:
    jstore.set_active_source(name if name in _ISOLATED_BROKERS else None)
    jstore.rebuild_daily_from_trades()


_saved_broker = (jstore.get_setting("active_broker") or "").strip().lower()
_init_name = _saved_broker if _saved_broker in _BROKERS else config.DATA_SOURCE
_source = get_source(_init_name)
_apply_source_scope(_source.name)

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


def _switch_source(name: str, logout_prev: bool = True):
    """Switch the active broker at runtime ('one time connect one only')."""
    global _source
    name = (name or "").strip().lower()
    if name not in _BROKERS:
        raise HTTPException(status_code=400, detail=f"Unsupported broker: {name}")
    with _lock:
        if logout_prev and _source.name != name:
            try:
                _source.logout()
            except Exception:  # noqa: BLE001
                pass
        _cache.clear()
        _source = get_source(name)
        jstore.set_setting("active_broker", _source.name)
        _apply_source_scope(_source.name)
    with _scanner.lock:
        _scanner.quotes.clear()
        _scanner.updated.clear()
        _scanner.auth_required = False
        _scanner.last_error = None
    _ticker_idx_cache.update(ts=0.0, data=[])
    return _source


@app.get("/api/config")
def api_config():
    return {
        "symbols": config.SYMBOLS,
        "refresh_seconds": config.REFRESH_SECONDS,
        "source": _source.name,
        "needs_login": _source.needs_login(),
        "login_label": getattr(_source, "login_label", "Log in"),
        "brokers": {
            "fyers": {"label": "Fyers", "configured": bool(config.FYERS_APP_ID and config.FYERS_SECRET_ID)},
            "dhan": {"label": "Dhan", "configured": True},
            "upstox": {"label": "Upstox", "configured": bool(config.UPSTOX_API_KEY and config.UPSTOX_API_SECRET)},
            "mock": {"label": "Demo", "configured": True},
        },
    }


@app.post("/api/auth/broker")
async def api_select_broker(request: Request):
    """Switch the active broker (logs out previous broker if switching)."""
    d = await request.json()
    _switch_source(d.get("broker", ""))
    return api_config()


@app.get("/auth/login")
def auth_login(broker: Optional[str] = Query(None)):
    """Redirect the browser to the active (or requested) broker's login/consent page."""
    if broker and broker.strip().lower() != _source.name:
        _switch_source(broker)
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


@app.get("/api/profile")
def api_profile():
    """Return the logged-in broker user's profile (name, id, email, avatar, details)."""
    custom = jstore.get_setting(f"profile_custom:{_source.name}", {}) or {}
    cname = (custom.get("name") or "").strip()
    cavatar = (custom.get("avatar") or "").strip()
    if _source.needs_login():
        return {"logged_in": False, "source": _source.name}
    try:
        prof = dict(_source.get_profile() or {})
    except AuthRequired as e:
        cid = jdhan.get_creds()[0] if _source.name == "dhan" else ""
        if _source.name == "dhan" and not cid:
            # decode client id claim from Dhan JWT payload if available
            try:
                import base64, json as _js
                tok = jdhan.get_creds()[1]
                part = tok.split(".")[1] + "=="
                cid = str((_js.loads(base64.urlsafe_b64decode(part)) or {}).get("dhanClientId") or "")
            except Exception:  # noqa: BLE001
                cid = ""
        return {
            "logged_in": True,
            "token_expired": True,
            "source": _source.name,
            "broker": _source.name.upper(),
            "name": cname or (f"{_source.name.upper()} {cid}".strip() if cid else f"{_source.name.upper()} Account"),
            "user_id": cid,
            "email": "",
            "avatar": cavatar,
            "details": [x for x in ([["Client ID", cid]] if cid else [])] + [["Session", "Token expired — reconnect"]],
            "error": str(e),
        }
    except Exception:  # noqa: BLE001 - best-effort profile metadata
        prof = {"name": _source.name.upper() + " Account", "user_id": "", "email": "", "avatar": "", "broker": _source.name.upper(), "details": []}

    details = list(prof.get("details") or [])
    funds = _cached_broker_funds()
    if funds and funds.get("available") is not None:
        avail = float(funds["available"])
        details.append(["Available Funds", f"₹{avail:,.2f}"])
    prof["details"] = details
    if cname:
        prof["name"] = cname
    if cavatar:
        prof["avatar"] = cavatar
    prof["needs_custom_name"] = bool(_source.name == "dhan" and not cname)
    return {"logged_in": True, "token_expired": False, "source": _source.name, **prof}


@app.post("/api/profile/custom")
async def api_profile_custom(request: Request):
    """Save optional display name & photo URL for the active broker profile."""
    data = await request.json()
    key = f"profile_custom:{_source.name}"
    cur = jstore.get_setting(key, {}) or {}
    if "name" in data:
        cur["name"] = str(data.get("name") or "").strip()[:60]
    if "avatar" in data:
        cur["avatar"] = str(data.get("avatar") or "").strip()[:45000]
    jstore.set_setting(key, cur)
    return {"ok": True}


@app.post("/api/auth/logout")
def api_auth_logout():
    """Clear the cached broker token so the user can sign out / switch accounts."""
    _source.logout()
    with _lock:
        _cache.clear()
    return {"ok": True}


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


# Daily cache of history-derived levels (52W/pivots/avg-vol). These barely
# change intraday, so we fetch each symbol once a day and reuse it.
_HIST_LEVELS: dict[str, tuple[str, dict]] = {}
_HIST_WARM_PER_REQ = 20   # max symbols to fetch history for in one request


# ----------------------------------------------------------------------
#  Background screener scanner
#  One thread continuously refreshes depth quotes for the selected index,
#  paced by the shared Fyers rate limiter (5/sec + 50/min). The endpoint
#  just serves the latest snapshot — ZERO Fyers calls per request — so the
#  UI can auto-refresh every 15s without ever hitting the plan limit.
# ----------------------------------------------------------------------
# Scanner only runs while someone is actively looking at the screener AND the
# market is open. A daily budget keeps us safely under Fyers' 5,000 calls/day.
_SCAN_ACTIVE_WINDOW = 30.0     # seconds since last client pull to stay "active"
_SCAN_DAILY_BUDGET = 4500      # headroom under the 5,000/day plan cap
# Don't re-fetch a stock whose quote is younger than this. Caps the scanner's
# call rate (universe / target) and leaves headroom under the 45/min limiter for
# the dashboard + ticker, so we never hit Fyers' 50/min cap. Quotes still refresh
# every ~90s, which is plenty for a movers screener.
_SCAN_STALE_TARGET = 90.0


class _ScreenerScanner:
    def __init__(self):
        self.lock = threading.Lock()
        self.quotes: dict[str, dict] = {}     # ticker -> normalized quote
        self.updated: dict[str, float] = {}   # ticker -> epoch seconds
        self.index = screener.DEFAULT_INDEX
        self.universe = screener.scan_universe(self.index)
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.last_error: Optional[str] = None
        self.auth_required = False
        self.last_pull = 0.0                  # when a client last asked (heartbeat)
        self.idle = True                      # currently paused (no scanning)

    def touch(self):
        """Register a client pull — keeps the scanner awake for a short window."""
        self.last_pull = time.time()

    def _budget_ok(self) -> bool:
        getter = getattr(_source, "calls_today", None)
        used = getter() if callable(getter) else 0
        return used < _SCAN_DAILY_BUDGET

    def should_scan(self) -> bool:
        if _source.name == "mock":
            return False
        if (time.time() - self.last_pull) >= _SCAN_ACTIVE_WINDOW:
            return False
        if _source.name == "fyers" and not self._budget_ok():
            return False
        # During live hours scan continuously; off-hours allow one initial pass
        # so the Screener still shows real latest exchange quotes instead of empty.
        if _market_open():
            return True
        with self.lock:
            return any(t not in self.quotes for t in self.universe)

    def set_index(self, index: str):
        valid = set(screener.INDEXES) | {"nse", "bse"}
        if index not in valid:
            return
        with self.lock:
            if index != self.index:
                self.index = index
                self.universe = screener.scan_universe(index)

    def ensure_started(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="screener-scan")
        self.thread.start()

    def snapshot(self):
        """Quotes for the CURRENT universe only, plus coverage info."""
        with self.lock:
            uni = list(self.universe)
            quotes = [self.quotes[t] for t in uni if t in self.quotes]
            oldest = min((self.updated[t] for t in uni if t in self.updated), default=None)
        return quotes, len(uni), oldest

    def _run(self):
        while self.running:
            try:
                # Pause entirely unless someone is watching AND the market is open
                # AND we're under the daily budget. This is what keeps us from
                # burning 5,000 calls/day scanning an idle/closed market.
                if not self.should_scan():
                    self.idle = True
                    time.sleep(2.0)
                    continue
                self.idle = False
                with self.lock:
                    uni = list(self.universe)
                    idx = self.index
                stale_syms = []
                now_ts = time.time()
                for t in uni:
                    with self.lock:
                        if self.index != idx:
                            break
                        last = self.updated.get(t, 0.0)
                    if now_ts - last >= _SCAN_STALE_TARGET:
                        stale_syms.append(t)
                batch_fn = getattr(_source, "fetch_batch_quotes", None)
                fetched_any = bool(stale_syms)
                if callable(batch_fn) and stale_syms:
                    for i in range(0, len(stale_syms), 50):
                        if not self.running or not self.should_scan():
                            break
                        with self.lock:
                            if self.index != idx:
                                break
                        chunk = stale_syms[i:i + 50]
                        try:
                            res_map = batch_fn(chunk)
                            self.auth_required = False
                            if res_map:
                                now_q = time.time()
                                with self.lock:
                                    for sym_k, q in res_map.items():
                                        self.quotes[sym_k] = q
                                        self.updated[sym_k] = now_q
                                self.last_error = None
                        except AuthRequired:
                            self.auth_required = True
                            time.sleep(3.0)
                            break
                        except Exception as e:  # noqa: BLE001
                            self.last_error = str(e)
                else:
                    for t in stale_syms:
                        if not self.running or not self.should_scan():
                            break
                        with self.lock:
                            if self.index != idx:   # universe changed mid-cycle → restart
                                break
                        try:
                            q = _source.fetch_one_quote(t)   # rate-limited + counted inside
                            self.auth_required = False
                            if q:
                                with self.lock:
                                    self.quotes[t] = q
                                    self.updated[t] = time.time()
                                self.last_error = None
                        except AuthRequired:
                            self.auth_required = True
                            time.sleep(3.0)
                            break
                        except Exception as e:  # noqa: BLE001 - skip symbol, keep scanning
                            self.last_error = str(e)
                # If every stock was still fresh, idle longer so we're not busy-
                # spinning over timestamps; otherwise the limiter paces the fetches.
                time.sleep(3.0 if not fetched_any else 0.5)
            except Exception as e:  # noqa: BLE001 - never let the thread die
                self.last_error = str(e)
                time.sleep(2.0)


_scanner = _ScreenerScanner()


_HIST_WARMING: set[str] = set()      # symbols currently being fetched
_HIST_WARM_LOCK = threading.Lock()


def _warm_hist_async(symbols: list[str], today: str):
    """Fetch daily levels for the given symbols in the background (paced by the
    shared rate limiter), caching them. Never blocks the request."""
    def run():
        for s in symbols:
            try:
                candles = _source.fetch_history(s, resolution="D", days=365,
                                                background=True)
                lv = screener.levels_from_history(candles)
            except Exception:  # noqa: BLE001 - skip, retry next refresh
                lv = None
            _HIST_LEVELS[s] = (today, lv or {})
            with _HIST_WARM_LOCK:
                _HIST_WARMING.discard(s)
    threading.Thread(target=run, daemon=True, name="hist-warm").start()


def _hist_levels_for(symbols: list[str], today: str) -> tuple[dict, int]:
    """Read cached {sym: levels} instantly and kick off a background warm-up for
    any uncached symbols. Second value = how many are still missing."""
    out, missing = {}, []
    for s in symbols:
        cached = _HIST_LEVELS.get(s)
        if cached and cached[0] == today:
            if cached[1]:
                out[s] = cached[1]
        else:
            missing.append(s)
    if missing:
        with _HIST_WARM_LOCK:
            todo = [s for s in missing if s not in _HIST_WARMING][:_HIST_WARM_PER_REQ]
            _HIST_WARMING.update(todo)
        if todo:
            _warm_hist_async(todo, today)
    return out, len(missing)


@app.get("/api/symbols")
def api_symbols(q: Optional[str] = Query(None)):
    """All NSE & BSE equity stocks for the header search box (supports ?q= search)."""
    if q and q.strip():
        items = symbols.search(q, limit=25)
        return {"stocks": [x["t"] for x in items], "items": items, "total": len(items)}
    items = symbols.load_master(allow_network=True)
    return {"stocks": [x["t"] for x in items], "items": items, "total": len(items)}


@app.get("/api/screener/meta")
def api_screener_meta():
    """Filter groups + index list so the UI can build its controls."""
    return {"groups": screener.FILTER_GROUPS, "indexes": screener.index_list(),
            "default_index": screener.DEFAULT_INDEX}


@app.get("/api/screener")
def api_screener(sort: str = Query("volume"), index: str = Query(screener.DEFAULT_INDEX),
                 hist: int = Query(0)):
    """Serve the latest background snapshot for the chosen index (all non-mock
    sources scan real exchange quotes: Fyers quotes or free Yahoo Finance feed)."""
    row_cap = 6000 if index in ("nse", "bse") else 250
    if _source.name == "mock":
        rows = screener.demo_screen(index, [], sort, limit=row_cap)
        return {"available": True, "sort": sort, "index": index,
                "scanned": len(screener.scan_universe(index)), "covered": len(rows),
                "rows": rows, "warming": 0, "stale_secs": 0, "live": False}

    _scanner.set_index(index)
    _scanner.ensure_started()
    _scanner.touch()                         # heartbeat: keep the scanner awake
    if _scanner.auth_required:
        raise HTTPException(status_code=401, detail="login_required")

    quotes, total, oldest = _scanner.snapshot()
    # On initial cold pull, if scanner hasn't fetched the first batch yet, wait
    # briefly (up to ~1.5s) on small universes or kick off cleanly.
    if not quotes and index not in ("nse", "bse") and hasattr(_source, "fetch_batch_quotes"):
        try:
            uni = screener.scan_universe(index)
            first = _source.fetch_batch_quotes(uni[:50])
            if first:
                now_q = time.time()
                with _scanner.lock:
                    for k, q in first.items():
                        _scanner.quotes[k] = q
                        _scanner.updated[k] = now_q
                quotes, total, oldest = _scanner.snapshot()
        except AuthRequired:
            raise HTTPException(status_code=401, detail="login_required")
        except Exception:  # noqa: BLE001
            pass
    hist_map, warming = ({}, 0)
    if hist and index not in ("nse", "bse"):
        hist_map, warming = _hist_levels_for(screener.scan_universe(index), _today_ist())
    rows = screener.screen(quotes, hist_map, [], sort, limit=row_cap)
    stale = int(time.time() - oldest) if oldest else 0
    return {"available": True, "sort": sort, "index": index,
            "scanned": total, "covered": len(quotes), "rows": rows,
            "warming": warming, "stale_secs": stale,
            "market_open": _market_open(),
            "budget_left": max(0, _SCAN_DAILY_BUDGET - (
                _source.calls_today() if hasattr(_source, "calls_today") else 0)) if _source.name == "fyers" else 9999,
            "scan_error": _scanner.last_error if not quotes else None, "live": True}


# ---- ticker tape (indices + high-volume stocks) ----------------------
_TICKER_INDEXES = ["NIFTY", "BANKNIFTY", "SENSEX"]
_ticker_idx_cache: dict = {"ts": 0.0, "data": []}
_TICKER_IDX_TTL = 20.0   # seconds; index spot is cheap but still cached


def _ticker_indices():
    """Index spot + % change, cached ~20s (seed once even after market close)."""
    now = time.time()
    if now - _ticker_idx_cache["ts"] < _TICKER_IDX_TTL and _ticker_idx_cache["data"]:
        return _ticker_idx_cache["data"]
    if _ticker_idx_cache["data"] and not _market_open():
        return _ticker_idx_cache["data"]
    if _source.name == "fyers" and (_source.calls_today() if hasattr(_source, "calls_today") else 0) >= _SCAN_DAILY_BUDGET:
        return _ticker_idx_cache["data"]
    try:
        data = _source.fetch_index_quotes(_TICKER_INDEXES)
        if data:
            _ticker_idx_cache.update(ts=now, data=data)
    except Exception:  # noqa: BLE001 - keep last values on failure
        pass
    return _ticker_idx_cache["data"]


@app.get("/api/ticker")
def api_ticker():
    """Scrolling ticker data: indices + top high-volume stocks. Reuses the
    screener scanner's snapshot for stocks across all live brokers."""
    if _source.name == "mock":
        import random
        rng = random.Random(int(time.time() // 20))
        idx = [{"name": n, "ltp": round(rng.uniform(20000, 55000), 2),
                "chg_pct": round(rng.uniform(-1.5, 1.5), 2)} for n in _TICKER_INDEXES]
        stocks = []
        for t in screener.scan_universe("nifty50")[:18]:
            r = random.Random(sum(ord(c) for c in t))
            stocks.append({"symbol": t, "ltp": round(r.uniform(100, 4000), 2),
                           "chg_pct": round(r.uniform(-4, 4), 2)})
        return {"indices": idx, "stocks": stocks, "live": False, "market_open": True}

    _scanner.ensure_started()
    _scanner.touch()                         # viewing the ticker keeps data warm
    quotes, _total, _oldest = _scanner.snapshot()
    if not quotes and hasattr(_source, "fetch_batch_quotes") and _source.name != "fyers":
        try:
            first = _source.fetch_batch_quotes(screener.scan_universe("nifty50")[:24])
            if first:
                now_q = time.time()
                with _scanner.lock:
                    for k, q in first.items():
                        _scanner.quotes[k] = q
                        _scanner.updated[k] = now_q
                quotes, _total, _oldest = _scanner.snapshot()
        except Exception:  # noqa: BLE001
            pass
    rows = []
    for q in quotes:
        prev = float(q.get("prev_close") or 0)
        ltp = float(q.get("ltp") or 0)
        if prev > 0 and ltp > 0:
            rows.append({"symbol": q.get("ticker"), "ltp": round(ltp, 2),
                         "chg_pct": round((ltp - prev) / prev * 100, 2),
                         "volume": int(float(q.get("volume") or 0))})
    rows.sort(key=lambda r: -r["volume"])
    stocks = [{"symbol": r["symbol"], "ltp": r["ltp"], "chg_pct": r["chg_pct"]} for r in rows[:18]]
    return {"indices": _ticker_indices(), "stocks": stocks,
            "live": True, "market_open": _market_open()}


def _yahoo_stock_ohlc(ticker: str, interval: str = "1d", range_str: str = "1y", exch: str = "NSE") -> list[list]:
    """Best-effort [ts, o, h, l, c, v] candles from Yahoo Finance for any NSE/BSE equity."""
    import requests as _req
    suffixes = (".BO", ".NS") if exch == "BSE" else (".NS", ".BO")
    for sfx in suffixes:
        try:
            r = _req.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker + sfx)}",
                params={"interval": interval, "range": range_str},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=7,
            )
            if r.status_code != 200:
                continue
            res = (((r.json() or {}).get("chart") or {}).get("result") or [None])[0]
            if not res:
                continue
            ts = res.get("timestamp") or []
            q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            os_, hs, ls, cs, vs = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
            out = []
            for t0, o0, h0, l0, c0, v0 in zip(ts, os_, hs, ls, cs, vs):
                if None in (o0, h0, l0, c0):
                    continue
                out.append([int(t0), round(float(o0), 2), round(float(h0), 2),
                            round(float(l0), 2), round(float(c0), 2), int(v0 or 0)])
            if len(out) >= 5:
                return out
        except Exception:  # noqa: BLE001
            continue
    return []


@app.get("/api/screener/analyze")
def api_screener_analyze(symbol: str = Query(...)):
    """Trend/momentum analysis for any NSE/BSE stock (Fyers or Yahoo OHLC)."""
    raw = (symbol or "").strip().upper()
    if not raw:
        raise HTTPException(status_code=400, detail="symbol required")
    resolved = symbols.resolve(raw)
    sym = (resolved.get("t") if resolved else None) or raw.split(":")[-1].rsplit("-", 1)[0]
    fsym = raw if ":" in raw else ((resolved.get("f") if resolved else None) or sym)
    exch = "BSE" if (resolved and resolved.get("e") == "BSE") or raw.startswith("BSE:") else "NSE"
    if _source.name == "mock":
        return screener.demo_analysis(sym)

    daily: list[list] = []
    intraday: list[list] = []
    if _source.name == "fyers":
        try:
            daily = _source.fetch_history(fsym, resolution="D", days=365)
            try:
                intraday = _source.fetch_history(fsym, resolution="5", days=3)
            except Exception:  # noqa: BLE001
                intraday = []
        except AuthRequired:
            pass  # still fall back cleanly to Yahoo real OHLC below
        except Exception:  # noqa: BLE001
            daily = []
    if len(daily) < 5:
        daily = _yahoo_stock_ohlc(sym, "1d", "1y", exch)
    if not intraday:
        intraday = _yahoo_stock_ohlc(sym, "5m", "5d", exch)
    if len(daily) >= 5 or intraday:
        return screener.analyze(sym, daily, intraday)
    return screener.demo_analysis(sym) if _source.name == "mock" else {
        "symbol": sym, "ok": False, "note": f"No technical history found for {sym}."
    }


@app.get("/api/stock/fundamentals")
def api_stock_fundamentals(symbol: str = Query(...)):
    """Company fundamentals (Screener.in + Yahoo Finance) for all non-mock sources."""
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    if _source.name == "mock":
        return fundamentals.demo(sym)
    return fundamentals.get(sym)


@app.get("/api/stock/fundamentals_diag")
def api_stock_fundamentals_diag(symbol: str = Query("RELIANCE")):
    """Temporary, un-gated diagnostic for Yahoo fundamentals on the cloud host.
    Reveals only whether the Yahoo handshake works (no user data)."""
    return fundamentals.diag(symbol)


@app.get("/api/ipos")
def api_ipos():
    """Open + upcoming IPOs (NSE). Independent of Fyers."""
    if _source.name == "fyers":
        return ipo.get()
    return ipo.demo()


@app.get("/api/ipo/detail")
def api_ipo_detail(symbol: str = Query(""), name: str = Query("")):
    """Company introduction, pre-IPO ratios, pros/cons & category bids for an IPO."""
    if _source.name == "mock":
        nm = (name or symbol or "Demo Company").strip()
        return {
            "ok": True, "symbol": symbol, "name": nm,
            "about": f"{nm} is shown in Demo mode. Connect Fyers to fetch live Screener & NSE company details.",
            "sector": "", "ratios": [], "pros": [], "cons": [], "bids": [],
            "screener_url": "", "source": "Demo",
        }
    return ipo.detail(symbol=symbol, name=name)


@app.get("/api/ipos_diag")
def api_ipos_diag():
    """Un-gated diagnostic: does NSE's IPO API answer from this host?"""
    return ipo.probe()


@app.get("/healthz")
def healthz():
    return {"ok": True, "source": _source.name,
            "store": getattr(jstore, "BACKEND", "sqlite")}


# ----------------------------------------------------------------- journal
_FUNDS_CACHE: dict[tuple[str, str], tuple[float, Optional[dict]]] = {}


def _source_auth_sig() -> str:
    if _source.name == "dhan":
        return jdhan.get_creds()[1][:16]
    tok = getattr(_source, "_token", None) or ""
    return str(tok)[:16]


def _cached_broker_funds() -> Optional[dict]:
    import time as _t
    key = (_source.name, _source_auth_sig())
    cached = _FUNDS_CACHE.get(key)
    if cached and (_t.time() - cached[0]) < 45:
        return cached[1]
    try:
        res = _source.get_funds()
    except Exception:  # noqa: BLE001
        res = None
    _FUNDS_CACHE[key] = (_t.time(), res)
    return res


def _effective_capital() -> tuple[float, str, Optional[dict]]:
    saved = janalytics.get_capital()
    funds = _cached_broker_funds()
    if saved > 0:
        return saved, "saved", funds
    if funds:
        live = float(funds.get("opening") or funds.get("total") or 0)
        if live >= 1.0:
            return round(live, 2), "broker", funds
    return 0.0, "unset", funds


@app.get("/api/journal/overview")
def journal_overview(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    today = _today_ist()
    summary = janalytics.summary(start, end)
    cap, cap_src, funds = _effective_capital()
    summary["opening_capital"] = cap
    summary["capital_source"] = cap_src
    summary["broker_funds"] = funds
    return {
        "source": _source.name,
        "can_sync": _source.name in _ISOLATED_BROKERS,
        "dhan_connected": _source.name == "dhan" and jdhan.is_connected(),
        "summary": summary,
        "range": {"start": start, "end": end},
        "targets": janalytics.targets_progress(),
        "today": jrules.evaluate(today),
        "rules_config": jrules.get_rules(),
        "targets_config": janalytics.get_targets(),
        "recent_trades": _trades_with_tags(300, start, end),
    }


@app.get("/api/journal/trades")
def journal_trades(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    """All trades (with tags) for a specific day or date range."""
    rows = _trades_with_tags(5000, start, end)
    closed = [t for t in rows if t.get("closed")]
    total_pnl = round(sum(float(t.get("pnl") or 0) for t in closed), 2)
    wins = sum(1 for t in closed if float(t.get("pnl") or 0) > 0)
    losses = sum(1 for t in closed if float(t.get("pnl") or 0) < 0)
    return {"start": start, "end": end, "trades": rows,
            "total_pnl": total_pnl, "wins": wins, "losses": losses}


def _trades_with_tags(limit: int, start: Optional[str] = None, end: Optional[str] = None):
    tags = jstore.tags_map()
    if start or end:
        # within the selected range (undated 'unknown' trades fall outside any date range)
        rows = jstore.trades_between(start or "0000-01-01", end or "9999-12-31")
    else:
        rows = jstore.recent_trades(limit)
    for t in rows:
        t["tag"] = tags.get(t["id"])
        if t.get("entry_price") is None and t.get("pnl") is not None and float(t.get("qty") or 0) > 0:
            q = float(t["qty"])
            ex = float(t.get("price") or 0)
            pl = float(t["pnl"])
            is_long = str(t.get("side") or "").upper() in ("LONG", "BUY")
            t["entry_price"] = round(max(0.0, ex - pl / q) if is_long else max(0.0, ex + pl / q), 2)
    return rows


_IDX_YAHOO = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MIDCAP_100.NS",
}


def _extract_underlying_symbol(sym: str) -> str:
    """Extract clean spot/underlying symbol from Fyers, Dhan, or spaced F&O names."""
    import re as _re
    s = (sym or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    s = _re.sub(r"-(EQ|BE|BL)$", "", s)
    for idx in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "BANKEX", "SENSEX", "NIFTY"):
        if s.startswith(idx):
            return idx
    if " " in s:
        return s.split()[0]
    if "-" in s:
        return s.split("-")[0]
    m = _re.match(r"^([A-Z]+?)(?:\d{2}[A-Z]{3}|\d{4,}(?:CE|PE|FUT)?$)", s)
    if m:
        return m.group(1)
    return s or "OTHER"


def _yahoo_intraday(underlying: str, day: str, interval: str = "5m") -> dict | None:
    """Best-effort intraday OHLC candles and prior-day classic pivots for `underlying` on `day`."""
    import requests as _req
    ysym = _IDX_YAHOO.get(underlying, f"{underlying}.NS")
    iv = interval if interval in ("1m", "2m", "5m", "15m", "60m") else "5m"
    try:
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_IST)
        if (datetime.now(_IST) - dt).days > 58:
            return None
        p1 = int((dt - timedelta(days=5)).timestamp())
        p2 = int((dt + timedelta(days=1)).timestamp())
        r = _req.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
            params={"interval": iv, "period1": p1, "period2": p2},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=7,
        )
        if r.status_code != 200:
            return None
        res = (((r.json() or {}).get("chart") or {}).get("result") or [None])[0]
        if not res:
            return None
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        by_day: dict[str, list[dict]] = {}
        for t0, o0, h0, l0, c0 in zip(ts, opens, highs, lows, closes):
            if None in (o0, h0, l0, c0):
                continue
            ts_int = int(t0)
            d_ist = datetime.fromtimestamp(ts_int, _IST)
            d_str = d_ist.strftime("%Y-%m-%d")
            by_day.setdefault(d_str, []).append({
                "ts": ts_int,
                "t": d_ist.strftime("%H:%M"),
                "o": round(float(o0), 2),
                "h": round(float(h0), 2),
                "l": round(float(l0), 2),
                "c": round(float(c0), 2),
            })
        pts = by_day.get(day) or []
        if len(pts) < 2:
            return None
        pivots = None
        prev_days = sorted(d for d in by_day if d < day and len(by_day[d]) >= 2)
        if prev_days:
            prev_pts = by_day[prev_days[-1]]
            ph = max(c["h"] for c in prev_pts)
            pl = min(c["l"] for c in prev_pts)
            pc = prev_pts[-1]["c"]
            pp = (ph + pl + pc) / 3.0
            pivots = {
                "based_on": prev_days[-1],
                "p": round(pp, 2),
                "r1": round(2 * pp - pl, 2),
                "r2": round(pp + (ph - pl), 2),
                "r3": round(ph + 2 * (pp - pl), 2),
                "s1": round(2 * pp - ph, 2),
                "s2": round(pp - (ph - pl), 2),
            }
        return {"symbol": underlying, "points": pts, "pivots": pivots}
    except Exception:  # noqa: BLE001
        return None


_DHAN_SEC_ID = {"NIFTY": ("13", "NSE_FNO"), "BANKNIFTY": ("25", "NSE_FNO"), "SENSEX": ("51", "BSE_FNO")}
_DHAN_PLAN_CACHE: dict[str, tuple[float, bool]] = {}
_GROWW_CONTRACT_CACHE: dict[tuple[str, str], tuple[float, tuple[str, str, str] | None]] = {}
_MON_ABBR = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
             "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def _parse_option_spec(sym: str, expiry_hint: str = "", ref_day: str = "") -> dict | None:
    """Parse underlying, strike, option type, exchange, and expiry status from any F&O symbol."""
    import re as _re
    s = (sym or "").strip().upper()
    if not s:
        return None
    und = _extract_underlying_symbol(s)
    exch = "BSE" if (und in ("SENSEX", "BANKEX") or s.startswith(("BSE:", "BFO:"))) else "NSE"

    # Match option type + strike
    strike = ""
    opt_type = ""
    # 1. Dhan hyphenated: SENSEX-Dec2026-83700-CE
    m_dhan = _re.search(r"-([A-Z]{3})(\d{4})-(\d+(?:\.\d+)?)-(CE|PE)$", s)
    if m_dhan:
        opt_type = m_dhan.group(4)
        strike = m_dhan.group(3)
        mon_num = _MON_ABBR.get(m_dhan.group(1), 0)
        yr_num = int(m_dhan.group(2))
    else:
        mon_num, yr_num = 0, 0
        # 2. Spaced: NIFTY 19 OCT 23600 CALL
        m_sp = _re.search(r"\b(\d{1,2})\s+([A-Z]{3})\s+(\d+(?:\.\d+)?)\s+(CALL|PUT|CE|PE)\b", s)
        if m_sp:
            opt_type = "CE" if m_sp.group(4) in ("CALL", "CE") else "PE"
            strike = m_sp.group(3)
            mon_num = _MON_ABBR.get(m_sp.group(2), 0)
        else:
            # 3. Fyers / NSE compact: SENSEX26DEC83700CE or NIFTY26D0125800CE or NIFTY26NOV25800CE
            m_cmp = _re.search(r"(\d{2})([A-Z]{3}|[OND]\d{2}|[1-9]\d{2})(\d{3,6})(CE|PE)$", s)
            if m_cmp:
                yr_num = 2000 + int(m_cmp.group(1))
                code = m_cmp.group(2)
                if code in _MON_ABBR:
                    mon_num = _MON_ABBR[code]
                elif code[0] in ("O", "N", "D"):
                    mon_num = {"O": 10, "N": 11, "D": 12}[code[0]]
                elif code[0].isdigit():
                    mon_num = int(code[0])
                strike = m_cmp.group(3)
                opt_type = m_cmp.group(4)

    if not opt_type or not strike:
        return None
    if strike.endswith(".0"):
        strike = strike[:-2]

    now_d = datetime.now(_IST).strftime("%Y-%m-%d")
    exp = (expiry_hint or "").strip()
    is_expired = False
    if len(exp) == 10 and exp[4] == "-":
        is_expired = exp < now_d
    elif ref_day and len(ref_day) == 10 and mon_num:
        # e.g. spaced "19 OCT" + ref_day
        m_dd = _re.search(r"\b(\d{1,2})\s+[A-Z]{3}\b", s)
        if m_dd:
            y_guess = int(ref_day[:4]) if not yr_num else yr_num
            exp = f"{y_guess:04d}-{mon_num:02d}-{int(m_dd.group(1)):02d}"
            is_expired = exp < now_d
    elif yr_num and mon_num:
        now_dt = datetime.now(_IST)
        is_expired = (yr_num, mon_num) < (now_dt.year, now_dt.month)

    return {
        "underlying": und,
        "exchange": exch,
        "strike": strike,
        "opt_type": opt_type,
        "expiry": exp,
        "month": mon_num,
        "year": yr_num,
        "is_expired": is_expired,
    }


def _groww_resolve_contract(spec: dict) -> tuple[str, str, str] | None:
    """Resolve an active index/equity option contract ID on Groww public search."""
    import time as _t
    import requests as _req
    exp_hint = spec.get("expiry") or ""
    key = (f"{spec['underlying']}:{spec['strike']}:{spec['opt_type']}:{spec.get('month', 0)}:{spec.get('year', 0)}", exp_hint)
    cached = _GROWW_CONTRACT_CACHE.get(key)
    if cached and (_t.time() - cached[0]) < 1800:
        return cached[1]
    q = f"{spec['underlying']} {spec['strike']} {spec['opt_type']}"
    today_s = datetime.now(_IST).strftime("%Y-%m-%d")
    try:
        r = _req.get(
            "https://groww.in/v1/api/search/v1/entity",
            params={"q": q, "size": 10},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        if r.status_code != 200:
            return None
        items = [
            c for c in ((r.json() or {}).get("content") or [])
            if str(c.get("entity_type") or "") == "Option"
            and str(c.get("option_type") or "").upper() == spec["opt_type"]
            and str(c.get("exchange") or "").upper() == spec["exchange"]
            and (c.get("groww_contract_id") or c.get("id"))
        ]
        if not items:
            _GROWW_CONTRACT_CACHE[key] = (_t.time(), None)
            return None
        chosen = None
        if exp_hint:
            chosen = next((c for c in items if c.get("expiry") == exp_hint), None)
        if not chosen and spec.get("year") and spec.get("month"):
            ym = f"{spec['year']:04d}-{spec['month']:02d}-"
            same_ym = [c for c in items if str(c.get("expiry") or "").startswith(ym)]
            if same_ym:
                future_ym = sorted((c for c in same_ym if (c.get("expiry") or "") >= today_s), key=lambda x: x.get("expiry") or "")
                chosen = future_ym[0] if future_ym else same_ym[0]
        if not chosen:
            future = sorted((c for c in items if (c.get("expiry") or "") >= today_s), key=lambda x: x.get("expiry") or "")
            chosen = future[0] if future else items[0]
        res = (
            str(chosen.get("exchange") or spec["exchange"]).upper(),
            str(chosen.get("groww_contract_id") or chosen.get("id")),
            str(chosen.get("expiry") or ""),
        )
        _GROWW_CONTRACT_CACHE[key] = (_t.time(), res)
        return res
    except Exception:  # noqa: BLE001
        return None


def _fetch_public_option_data(sym: str, day: str, interval: str = "5", expiry_hint: str = "") -> dict | None:
    """Fetch live/intraday option OHLC candles + LTP for an active (non-expired) contract."""
    import requests as _req
    spec = _parse_option_spec(sym, expiry_hint=expiry_hint, ref_day=day)
    if not spec:
        return None
    if spec["is_expired"]:
        return {"expired": True, "expiry": spec["expiry"], "candles": [], "quote": None}
    resolved = _groww_resolve_contract(spec)
    if not resolved:
        return None
    exch, cid, res_exp = resolved
    today_s = datetime.now(_IST).strftime("%Y-%m-%d")
    if res_exp and res_exp < today_s:
        return {"expired": True, "expiry": res_exp, "candles": [], "quote": None}

    iv = int(interval) if str(interval) in ("1", "5", "15", "60") else 5
    try:
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_IST)
    except ValueError:
        dt = datetime.now(_IST)
    p1 = int((dt - timedelta(days=3)).timestamp() * 1000)
    p2 = int((dt + timedelta(days=1)).timestamp() * 1000)
    headers = {"User-Agent": "Mozilla/5.0"}

    candles: list[dict] = []
    try:
        rc = _req.get(
            f"https://groww.in/v1/api/stocks_fo_data/v1/charting_service/chart/exchange/{exch}/segment/FNO/{cid}",
            params={"startTimeInMillis": p1, "endTimeInMillis": p2, "intervalInMinutes": iv},
            headers=headers,
            timeout=7,
        )
        if rc.status_code == 200:
            raw = (rc.json() or {}).get("candles") or []
            by_day: dict[str, list[dict]] = {}
            for row in raw:
                if not isinstance(row, (list, tuple)) or len(row) < 5:
                    continue
                if None in row[1:5]:
                    continue
                ts_val = int(row[0])
                if ts_val > 10_000_000_000:
                    ts_val //= 1000
                d_ist = datetime.fromtimestamp(ts_val, _IST)
                d_str = d_ist.strftime("%Y-%m-%d")
                by_day.setdefault(d_str, []).append({
                    "ts": ts_val,
                    "t": d_ist.strftime("%H:%M"),
                    "o": round(float(row[1]), 2),
                    "h": round(float(row[2]), 2),
                    "l": round(float(row[3]), 2),
                    "c": round(float(row[4]), 2),
                })
            candles = by_day.get(day) or (by_day[max(by_day)] if by_day else [])
    except Exception:  # noqa: BLE001
        candles = []

    quote = None
    try:
        rq = _req.get(
            f"https://groww.in/v1/api/stocks_fo_data/v1/tr_live_prices/exchange/{exch}/segment/FNO/{cid}/latest",
            headers=headers,
            timeout=5,
        )
        if rq.status_code == 200:
            q = rq.json() or {}
            if q.get("ltp") is not None:
                quote = {
                    "ltp": round(float(q.get("ltp") or 0), 2),
                    "open": round(float(q.get("open") or 0), 2) if q.get("open") is not None else None,
                    "high": round(float(q.get("high") or 0), 2) if q.get("high") is not None else None,
                    "low": round(float(q.get("low") or 0), 2) if q.get("low") is not None else None,
                    "close": round(float(q.get("close") or 0), 2) if q.get("close") is not None else None,
                }
    except Exception:  # noqa: BLE001
        quote = None

    return {"expired": False, "expiry": res_exp or spec["expiry"], "contract_id": cid, "candles": candles, "quote": quote}


def _dhan_has_data_plan() -> bool:
    import time as _t
    _, tok = jdhan.get_creds()
    if not tok:
        return False
    key = tok[:16]
    cached = _DHAN_PLAN_CACHE.get(key)
    if cached and (_t.time() - cached[0]) < 180:
        return cached[1]
    try:
        prof = jdhan.fetch_profile() or {}
        ok = str(prof.get("dataPlan") or "").lower() == "active"
    except Exception:  # noqa: BLE001
        ok = False
    _DHAN_PLAN_CACHE[key] = (_t.time(), ok)
    return ok


def _dhan_option_ohlc(sym: str, day: str) -> list[dict]:
    """Best-effort Dhan rollingoption 5m OHLC (only when Dhan Data API plan is active)."""
    import re as _re, requests as _req
    und = janalytics._underlying(sym)
    if und not in _DHAN_SEC_ID or not _dhan_has_data_plan():
        return []
    m = _re.search(r"\b(\d+(?:\.\d+)?)\s+(CALL|PUT|CE|PE)\b", sym)
    if not m:
        return []
    opt_type = "CALL" if m.group(2) in ("CALL", "CE") else "PUT"
    sec_id, ex_seg = _DHAN_SEC_ID[und]
    tok, _ = jdhan.get_creds()
    try:
        next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        r = _req.post(
            "https://api.dhan.co/v2/charts/rollingoption",
            headers={"access-token": tok, "Content-Type": "application/json"},
            json={
                "exchangeSegment": ex_seg, "interval": "5", "securityId": int(sec_id),
                "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1,
                "strike": "ATM", "drvOptionType": opt_type,
                "requiredData": ["open", "high", "low", "close", "timestamp"],
                "fromDate": day, "toDate": next_day,
            },
            timeout=6,
        )
        if r.status_code != 200:
            return []
        side_data = ((r.json() or {}).get("data") or {}).get("ce" if opt_type == "CALL" else "pe") or {}
        ts = side_data.get("timestamp") or []
        os_, hs, ls, cs = (side_data.get(k) or [] for k in ("open", "high", "low", "close"))
        out = []
        for t0, o0, h0, l0, c0 in zip(ts, os_, hs, ls, cs):
            if None in (o0, h0, l0, c0):
                continue
            ts_int = int(t0)
            d_ist = datetime.fromtimestamp(ts_int, _IST)
            if d_ist.strftime("%Y-%m-%d") == day:
                out.append({
                    "ts": ts_int, "t": d_ist.strftime("%H:%M"),
                    "o": round(float(o0), 2), "h": round(float(h0), 2),
                    "l": round(float(l0), 2), "c": round(float(c0), 2),
                })
        return out
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/journal/trade-chart")
def journal_trade_chart(
    symbol: str = Query(...),
    date: str = Query(""),
    entry_price: Optional[float] = Query(None),
    exit_price: Optional[float] = Query(None),
    entry_time: str = Query(""),
    exit_time: str = Query(""),
):
    """Best-effort intraday OHLC candles + underlying context & pivots for a trade popup."""
    sym = (symbol or "").strip().upper()
    candles: list[dict] = []
    note: Optional[str] = None

    if _source.name == "fyers" and sym and date and date != "unknown":
        try:
            is_fno = (" " in sym) or sym.endswith(("CE", "PE", "FUT")) or sym.endswith(("CALL", "PUT"))
            if is_fno and " " not in sym and hasattr(_source, "fetch_fno_history"):
                raw = _source.fetch_fno_history(sym, date, resolution="5")
            elif " " not in sym:
                dt = datetime.strptime(date, "%Y-%m-%d").date()
                days_back = max(2, (datetime.now(_IST).date() - dt).days + 1)
                raw = _source.fetch_history(sym, resolution="5", days=days_back) if days_back <= 90 else []
            else:
                raw = []
            for c in raw:
                if len(c) >= 5:
                    ts_int = int(c[0])
                    d_ist = datetime.fromtimestamp(ts_int, _IST)
                    if d_ist.strftime("%Y-%m-%d") == date:
                        candles.append({
                            "ts": ts_int, "t": d_ist.strftime("%H:%M"),
                            "o": round(float(c[1]), 2), "h": round(float(c[2]), 2),
                            "l": round(float(c[3]), 2), "c": round(float(c[4]), 2),
                        })
        except Exception:  # noqa: BLE001
            candles = []
    elif _source.name == "dhan" and sym and date and date != "unknown":
        candles = _dhan_option_ohlc(sym, date)
        if not candles:
            pub = _fetch_public_option_data(sym, date, interval="5")
            if pub and not pub.get("expired"):
                candles = pub.get("candles") or []
    elif _source.name == "mock" and entry_price is not None and exit_price is not None:
        import random
        rng = random.Random(sum(ord(c) for c in sym + date))
        ep, xp = float(entry_price), float(exit_price)
        base_dt = datetime.strptime(date if date and date != "unknown" else datetime.now(_IST).strftime("%Y-%m-%d"), "%Y-%m-%d").replace(tzinfo=_IST)
        steps = 36
        prev_c = ep
        for i in range(steps):
            frac = (i + 1) / steps
            target = ep + (xp - ep) * frac
            wobble = 0.0 if i in (0, steps - 1) else target * rng.uniform(-0.025, 0.025)
            o_val = round(max(0.05, prev_c), 2)
            c_val = round(max(0.05, xp if i == steps - 1 else target + wobble), 2)
            wick = max(0.2, abs(c_val - o_val) * rng.uniform(0.3, 1.1) + max(o_val, c_val) * 0.008)
            h_val = round(max(o_val, c_val) + wick * rng.uniform(0.3, 1.0), 2)
            l_val = round(max(0.05, min(o_val, c_val) - wick * rng.uniform(0.3, 1.0)), 2)
            bar_dt = base_dt + timedelta(hours=9, minutes=15 + i * 5)
            candles.append({
                "ts": int(bar_dt.timestamp()), "t": bar_dt.strftime("%H:%M"),
                "o": o_val, "h": h_val, "l": l_val, "c": c_val,
            })
            prev_c = c_val

    underlying = None
    if date and date != "unknown":
        und = janalytics._underlying(sym)
        if und and und != "OTHER" and (" " in sym or sym.endswith(("CE", "PE", "FUT")) or not candles):
            underlying = _yahoo_intraday(und, date)
    if not candles:
        note = (
            "Expired option OHLC isn't available without a paid Data API — "
            "showing underlying 5m OHLC candles with Entry/Exit timing & Pivot levels."
            if underlying else
            "Option OHLC candles aren't archived after expiry — showing Entry → Exit trajectory."
        )

    return {"symbol": sym, "date": date, "candles": candles, "underlying": underlying, "note": note}


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


@app.post("/api/journal/sync-history")
def journal_sync_history(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    """Pull dated trade history from Fyers (Reports → Trade History) for a date
    range — so the dashboard shows past trades without a CSV import."""
    try:
        return jsync.sync_fyers_history(_source, start, end)
    except AuthRequired:
        raise HTTPException(status_code=401, detail="login_required")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"History sync failed: {e}")


@app.get("/api/journal/history_diag")
def journal_history_diag(start: Optional[str] = Query(None), end: Optional[str] = Query(None)):
    """Temporary, un-gated diagnostic for the Fyers trade-history sync. Reports
    only counts, field names and errors — never trade values/symbols."""
    out = {"source": _source.name}
    if _source.name != "fyers":
        out["note"] = "not on the fyers source"
        return out
    out["has_method"] = hasattr(_source, "fetch_trade_history")
    try:
        fy = _source._model()
        out["sdk_has_tradehistory"] = callable(getattr(fy, "tradehistory", None))
    except AuthRequired:
        out["auth"] = "login_required"
        return out
    except Exception as e:  # noqa: BLE001
        out["model_error"] = f"{type(e).__name__}: {str(e)[:150]}"
        return out
    try:
        raw = _source.fetch_trade_history(start, end)
        out["raw_count"] = len(raw)
        out["field_names"] = sorted(raw[0].keys()) if raw else []
        fills = [f for f in (jsync._normalize(t) for t in raw) if f]
        out["fills"] = len(fills)
        dates = sorted({f["date"] for f in fills if f.get("date") != "unknown"})
        out["date_span"] = [dates[0], dates[-1]] if dates else None
    except AuthRequired:
        out["auth"] = "login_required"
    except Exception as e:  # noqa: BLE001
        out["fetch_error"] = f"{type(e).__name__}: {str(e)[:220]}"
    return out


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
    tok = (d.get("access_token") or "").strip()
    cid = (d.get("client_id") or "").strip()
    if not tok:
        raise HTTPException(status_code=400, detail="Dhan access token is required.")
    jdhan.set_creds(cid, tok)
    try:
        prof = jdhan.fetch_profile()
    except AuthRequired as e:
        jdhan.clear_creds()
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        jdhan.clear_creds()
        raise HTTPException(status_code=502, detail=f"Dhan verification failed: {e}")
    if not cid and prof.get("dhanClientId"):
        jdhan.set_creds(str(prof["dhanClientId"]), tok)
    # Switch active broker to Dhan (logs out any previous Fyers/Upstox session).
    _switch_source("dhan")
    return {"connected": True, "source": "dhan", "client_id": prof.get("dhanClientId") or cid}


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
    cap, cap_src, funds = _effective_capital()
    data = janalytics.capital_tracker(month, opening_override=cap if cap_src == "broker" else None)
    data["capital_source"] = cap_src
    data["broker_funds"] = funds
    data["source"] = _source.name
    data["can_sync"] = _source.name in _ISOLATED_BROKERS
    data["dhan_connected"] = _source.name == "dhan" and jdhan.is_connected()
    return data


@app.post("/api/journal/capital")
async def journal_set_capital(request: Request):
    data = await request.json()
    janalytics.set_capital(data.get("opening"))
    _FUNDS_CACHE.pop((_source.name, _source_auth_sig()), None)
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


# ---------------------------------------------------------------- Trade Mode
@app.get("/api/trademode")
def api_trademode():
    """Live trading snapshot (today P&L, funds, positions, orders, trades, holdings)
    for supported brokers (Fyers & Dhan)."""
    try:
        return _source.get_trade_snapshot()
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Trade Mode failed: {e}")


@app.get("/api/trademode/reports")
def api_trademode_reports():
    try:
        return _source.get_trade_reports()
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Reports failed: {e}")


@app.post("/api/trademode/cancel-order")
async def api_trademode_cancel_order(request: Request):
    data = await request.json()
    oid = str(data.get("id") or "").strip()
    if not oid:
        raise HTTPException(status_code=400, detail="order id required")
    fn = getattr(_source, "cancel_order", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Order cancellation not supported on this broker")
    try:
        return {"ok": True, "result": fn(oid)}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Cancel failed: {e}")


@app.post("/api/trademode/exit-position")
async def api_trademode_exit_position(request: Request):
    data = await request.json()
    pid = (str(data.get("id")).strip() if data.get("id") else None)
    fn = getattr(_source, "exit_position", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Position exit not supported on this broker")
    try:
        return {"ok": True, "result": fn(
            pid,
            symbol=data.get("symbol"),
            security_id=data.get("security_id"),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Exit position failed: {e}")


@app.post("/api/trademode/reverse-position")
async def api_trademode_reverse_position(request: Request):
    data = await request.json()
    fn = getattr(_source, "reverse_position", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Position reversal not supported on this broker")
    try:
        return {"ok": True, "result": fn(
            pos_id=data.get("id"),
            symbol=data.get("symbol"),
            security_id=data.get("security_id"),
            qty=int(data.get("qty") or 0),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Reverse position failed: {e}")


@app.post("/api/trademode/sl-target")
async def api_trademode_sl_target(request: Request):
    data = await request.json()
    fn = getattr(_source, "place_sl_target", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="SL/Target placement not supported on this broker")
    try:
        return {"ok": True, "result": fn(
            pos_id=data.get("id"),
            symbol=data.get("symbol"),
            security_id=data.get("security_id"),
            exchange_segment=data.get("exchange_segment"),
            product=data.get("product"),
            side=data.get("side"),
            qty=int(data.get("qty") or 0),
            target_price=float(data.get("target_price") or 0),
            sl_price=float(data.get("sl_price") or 0),
            sl_limit_price=float(data.get("sl_limit_price") or 0),
            trailing_jump=float(data.get("trailing_jump") or 0),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"SL/Target failed: {e}")


@app.post("/api/trademode/modify-order")
async def api_trademode_modify_order(request: Request):
    data = await request.json()
    oid = str(data.get("id") or "").strip()
    if not oid:
        raise HTTPException(status_code=400, detail="order id required")
    fn = getattr(_source, "modify_order", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Order modification not supported on this broker")
    try:
        return {"ok": True, "result": fn(
            order_id=oid,
            order_type=str(data.get("order_type") or "LIMIT"),
            qty=int(data.get("qty") or 1),
            price=float(data.get("price") or 0),
            stop_price=float(data.get("stop_price") or 0),
            validity=str(data.get("validity") or "DAY"),
            take_profit=float(data.get("take_profit") or 0),
            stop_loss=float(data.get("stop_loss") or 0),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Modify order failed: {e}")


@app.post("/api/trademode/place-order")
async def api_trademode_place_order(request: Request):
    data = await request.json()
    fn = getattr(_source, "place_order", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Order placement not supported on this broker")
    try:
        return {"ok": True, "result": fn(
            symbol=str(data.get("symbol") or ""),
            security_id=str(data.get("security_id") or ""),
            exchange_segment=str(data.get("exchange_segment") or ""),
            side=str(data.get("side") or "BUY"),
            qty=int(data.get("qty") or 1),
            product=str(data.get("product") or "INTRADAY"),
            order_type=str(data.get("order_type") or "MARKET"),
            price=float(data.get("price") or 0),
            stop_price=float(data.get("stop_price") or 0),
            target_price=float(data.get("target_price") or 0),
            sl_price=float(data.get("sl_price") or 0),
            trailing_jump=float(data.get("trailing_jump") or 0),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Place order failed: {e}")


@app.get("/api/trademode/inspect")
def api_trademode_inspect(
    symbol: str = Query(...),
    date: str = Query(""),
    timeframe: str = Query("5"),
    sec_id: str = Query(""),
    exchange_segment: str = Query(""),
    expiry: str = Query(""),
):
    """Fetch live intraday chart candles, underlying chart/pivots, and live quote
    for inspecting any Trade Mode position, order, or trade."""
    sym = (symbol or "").strip()
    day = (date or "").strip() or datetime.now(_IST).strftime("%Y-%m-%d")
    tf = timeframe if timeframe in ("1", "5", "15", "60") else "5"
    und = _extract_underlying_symbol(sym)
    is_fno = (" " in sym) or sym.upper().endswith(("CE", "PE", "FUT", "CALL", "PUT")) or ("-" in sym and any(c.isdigit() for c in sym))

    quote = None
    qfn = getattr(_source, "get_live_quote", None)
    if callable(qfn):
        try:
            quote = qfn(symbol=sym, security_id=sec_id, exchange_segment=exchange_segment)
        except Exception:  # noqa: BLE001
            quote = None

    candles: list[dict] = []
    contract_exp = (expiry or "").strip()
    is_expired = False

    if _source.name == "fyers" and sym:
        try:
            raw = (_source.fetch_fno_history(sym, day, resolution=tf)
                   if is_fno and hasattr(_source, "fetch_fno_history")
                   else _source.fetch_history(sym, resolution=tf, days=2))
            for c in raw or []:
                if len(c) >= 5:
                    ts_int = int(c[0])
                    d_ist = datetime.fromtimestamp(ts_int, _IST)
                    if d_ist.strftime("%Y-%m-%d") == day:
                        candles.append({
                            "ts": ts_int, "t": d_ist.strftime("%H:%M"),
                            "o": round(float(c[1]), 2), "h": round(float(c[2]), 2),
                            "l": round(float(c[3]), 2), "c": round(float(c[4]), 2),
                        })
        except Exception:  # noqa: BLE001
            candles = []
    elif _source.name == "dhan" and sec_id and exchange_segment:
        try:
            if _dhan_has_data_plan():
                inst = "OPTIDX" if ("FNO" in exchange_segment and is_fno) else ("FUTIDX" if "FUT" in sym.upper() else "EQUITY")
                next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                candles = jdhan.fetch_intraday_candles(sec_id, exchange_segment, inst, day, next_day, interval=tf)
        except Exception:  # noqa: BLE001
            candles = []

    if is_fno:
        pub = _fetch_public_option_data(sym, day, interval=tf, expiry_hint=contract_exp)
        if pub:
            is_expired = bool(pub.get("expired"))
            contract_exp = pub.get("expiry") or contract_exp
            if not is_expired and not candles:
                candles = pub.get("candles") or []
            if (not quote or not quote.get("ltp")) and pub.get("quote"):
                quote = pub["quote"]
    if is_expired:
        candles = []

    underlying = None
    if und and und != "OTHER":
        underlying = _yahoo_intraday(und, day, interval=f"{tf}m")
        if not candles and not is_fno and underlying:
            candles = underlying.get("points") or []

    note = ""
    if candles and is_fno:
        exp_txt = f" · Exp {contract_exp}" if contract_exp else ""
        note = f"Showing live {tf}m Option Chart{exp_txt} (switches to {und} Spot Chart after expiry)."
    elif not candles and underlying:
        note = (
            f"Contract expired ({contract_exp}) — showing {und} {tf}m Spot OHLC & classic Pivots."
            if is_expired and contract_exp else
            f"Showing {und} {tf}m Spot OHLC & classic Pivots"
            + (f" · Contract LTP ₹{quote['ltp']}" if quote and quote.get("ltp") else "")
        )
    elif not candles:
        note = "Intraday candles unavailable right now (market closed or symbol not charted)."

    return {
        "symbol": sym,
        "underlying_symbol": und,
        "date": day,
        "timeframe": tf,
        "expiry": contract_exp,
        "is_expired": is_expired,
        "quote": quote,
        "candles": candles,
        "underlying": underlying,
        "note": note,
    }


@app.post("/api/trademode/killswitch")
async def api_trademode_killswitch(request: Request):
    data = await request.json()
    st = str(data.get("status") or "ACTIVATE").upper()
    fn = getattr(_source, "set_killswitch", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="Kill Switch is only supported on Dhan")
    try:
        return {"ok": True, "result": fn(st)}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Kill Switch failed: {e}")


@app.post("/api/trademode/pnl-exit")
async def api_trademode_pnl_exit(request: Request):
    data = await request.json()
    fn = getattr(_source, "set_pnl_exit", None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail="P&L Auto-Exit is only supported on Dhan")
    try:
        return {"ok": True, "result": fn(
            float(data.get("profit_value") or 0),
            float(data.get("loss_value") or 0),
            bool(data.get("enable_kill_switch")),
            active=bool(data.get("active", True)),
        )}
    except AuthRequired as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"P&L Exit update failed: {e}")


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
