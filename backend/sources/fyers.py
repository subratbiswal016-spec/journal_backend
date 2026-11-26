"""Fyers data source — official, free, live option-chain OI (Fyers API v3).

Uses the official `fyers-apiv3` SDK so the endpoint URLs and the
`Authorization: app_id:access_token` header format are handled for us.

Flow:
  * OAuth login:  SessionModel.generate_authcode()  -> user logs in ->
    Fyers redirects to our callback with ?auth_code=... ; we exchange it with
    SessionModel.generate_token() for an access_token.
  * Option chain: FyersModel.optionchain({symbol, strikecount, timestamp})
      - per-strike: oi, oich (change in OI), ltp, volume, greeks.iv
      - expiryData: [{date: "DD-MM-YYYY", expiry: "<epoch secs>"}]; pass an
        expiry's epoch via `timestamp` to pick it.
  * Futures OI: best-effort via FyersModel.depth() on the monthly-future symbol.

Access tokens expire at 06:30 IST daily, so the dashboard shows a
"Login with Fyers" button each morning. The token is cached to
.fyers_token.json for same-day restarts.

Setup (one time):
  1. Create an app at https://myapi.fyers.in/dashboard/ (App ID like "XXXXXX-100"
     and a Secret). Set the app's Redirect URL to exactly
       http://127.0.0.1:8787/auth/callback
  2. Put FYERS_APP_ID and FYERS_SECRET_ID in .env, set DATA_SOURCE=fyers.
  3. Start the server and click "Login with Fyers".
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import config
from .base import AuthRequired, DataSource, FuturesRow, OptionChain, StrikeRow

IST = timezone(timedelta(hours=5, minutes=30))

# --- account-wide API rate limiter --------------------------------------
# Fyers' Standard plan caps calls at 5/second AND 50/minute. We enforce BOTH
# across every Fyers call (dashboard + screener) so we never get throttled.
# Short bursts up to the per-second cap are allowed (snappy dashboard), while
# sustained load is held under the per-minute cap (the background scanner).
import collections  # noqa: E402

_RL_LOCK = threading.Lock()
_RL_CALLS: "collections.deque[float]" = collections.deque()
_RL_LAST = 0.0
_RL_MIN_GAP = 0.25  # min seconds between any two calls → ≤4/sec (hard cap)
_RL_PER_MIN = 45    # hard cap for everyone (headroom under 50/min)
_RL_BG_LIMIT = 34   # background work (scanner/ticker/warm/futures) stops here,
                    # reserving ~11/min so user-facing calls (dashboard option
                    # chain, stock analysis) never queue behind the scanner
_RL_DAY_COUNT = 0   # every Fyers call made today (dashboard + screener + history)
_RL_DAY = ""        # IST date the counter belongs to


def _ist_date() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def calls_today() -> int:
    """Total Fyers API calls made so far today (resets at IST midnight)."""
    global _RL_DAY_COUNT, _RL_DAY
    with _RL_LOCK:
        if _RL_DAY != _ist_date():
            _RL_DAY, _RL_DAY_COUNT = _ist_date(), 0
        return _RL_DAY_COUNT


def _rate_limit(background: bool = False):
    """Block until a call fits under BOTH the min-spacing (≤4/sec) and the
    per-minute cap. Also counts the call toward the daily total. Background
    callers stop at a lower cap so foreground (user-facing) calls keep headroom
    and don't wait behind the scanner."""
    global _RL_LAST, _RL_DAY_COUNT, _RL_DAY
    cap = _RL_BG_LIMIT if background else _RL_PER_MIN
    while True:
        with _RL_LOCK:
            now = time.monotonic()
            while _RL_CALLS and now - _RL_CALLS[0] > 60.0:
                _RL_CALLS.popleft()
            gap_ok = (now - _RL_LAST) >= _RL_MIN_GAP
            min_ok = len(_RL_CALLS) < cap
            if gap_ok and min_ok:
                _RL_LAST = now
                _RL_CALLS.append(now)
                today = _ist_date()
                if _RL_DAY != today:
                    _RL_DAY, _RL_DAY_COUNT = today, 0
                _RL_DAY_COUNT += 1
                return
            waits = []
            if not gap_ok:
                waits.append(_RL_MIN_GAP - (now - _RL_LAST))
            if not min_ok:
                waits.append(60.0 - (now - _RL_CALLS[0]))
            wait = max(0.02, min(waits)) if waits else 0.05
        time.sleep(wait)
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Fyers symbol strings for the index underlyings.
# Strikes to fetch on each side of ATM. Wider = captures more deep-OTM OI so
# PCR / Max Pain / Support / Resistance reflect the fuller chain (closer to the
# broker). Fyers caps this at 50.
_STRIKE_COUNT = 50

FYERS_INDEX = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "BANKEX": "BSE:BANKEX-INDEX",
}
# (exchange, stem) used to build monthly futures symbols, e.g. NSE:NIFTY26SEPFUT.
FYERS_FUT_STEM = {
    "NIFTY": ("NSE", "NIFTY"),
    "BANKNIFTY": ("NSE", "BANKNIFTY"),
    "FINNIFTY": ("NSE", "FINNIFTY"),
    "MIDCPNIFTY": ("NSE", "MIDCPNIFTY"),
    "SENSEX": ("BSE", "SENSEX"),
    "BANKEX": ("BSE", "BANKEX"),
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOKEN_FILE = _PROJECT_ROOT / ".fyers_token.json"
_CACHE_DIR = _PROJECT_ROOT / ".cache"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _fmt_epoch(v) -> Optional[str]:
    """Format an epoch (seconds or ms) as DD-Mon-YYYY; None if not an epoch."""
    try:
        n = int(v)
    except (ValueError, TypeError):
        return None
    if n <= 0:
        return None
    if n > 1_000_000_000_000:  # milliseconds
        n //= 1000
    try:
        return datetime.fromtimestamp(n, IST).strftime("%d-%b-%Y")
    except (ValueError, OverflowError, OSError):
        return None


class FyersDataSource(DataSource):
    name = "fyers"
    login_label = "Login with Fyers"

    def __init__(self) -> None:
        self.app_id = config.FYERS_APP_ID
        self.secret_id = config.FYERS_SECRET_ID
        self.redirect_uri = config.FYERS_REDIRECT_URI
        self._token: Optional[str] = None
        self._token_date: Optional[str] = None
        # per-symbol expiry label -> epoch-seconds string
        self._expiry_map: dict[str, dict[str, str]] = {}
        self._expiry_map_date: Optional[str] = None
        # per-symbol futures cache (slow-moving) -> {"at": ts, "rows": [...]}
        self._fut_cache: dict[str, dict] = {}
        self._fut_fetching: set[str] = set()   # symbols being refreshed in bg
        self._profile: Optional[dict] = None
        self._profile_token: Optional[str] = None
        self._load_token()

    # ----------------------------------------------------------------- token
    def _load_token(self) -> None:
        if config.FYERS_ACCESS_TOKEN:
            self._token = config.FYERS_ACCESS_TOKEN
            self._token_date = _ist_now().strftime("%Y-%m-%d")
            return
        try:
            if _TOKEN_FILE.exists():
                data = json.loads(_TOKEN_FILE.read_text())
                self._token = data.get("access_token")
                self._token_date = data.get("date")
        except (OSError, ValueError):
            self._token = self._token_date = None

    def _save_token(self, token: str) -> None:
        self._token = token
        self._token_date = _ist_now().strftime("%Y-%m-%d")
        try:
            _TOKEN_FILE.write_text(json.dumps({"date": self._token_date, "access_token": token}))
        except OSError:
            pass

    def _token_valid(self) -> bool:
        if not self._token or not self._token_date:
            return False
        now = _ist_now()
        today = now.strftime("%Y-%m-%d")
        if self._token_date == today:
            return True
        # Fyers tokens die at 06:30 IST the next day.
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if self._token_date == yesterday and (now.hour < 6 or (now.hour == 6 and now.minute < 30)):
            return True
        return False

    def needs_login(self) -> bool:
        return not self._token_valid()

    # ------------------------------------------------------------------ oauth
    def _session(self):
        from fyers_apiv3 import fyersModel
        if not (self.app_id and self.secret_id):
            raise AuthRequired("FYERS_APP_ID / FYERS_SECRET_ID not set in .env")
        return fyersModel.SessionModel(
            client_id=self.app_id,
            secret_key=self.secret_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )

    def login_url(self, state: str = "") -> str:
        url = self._session().generate_authcode()
        if state and "state=" not in url:
            url += ("&" if "?" in url else "?") + "state=" + state
        return url

    def exchange_code(self, code: str) -> None:
        session = self._session()
        session.set_token(code)
        resp = session.generate_token()
        token = resp.get("access_token") if isinstance(resp, dict) else None
        if not token:
            raise AuthRequired(f"Fyers token exchange failed: {resp}")
        self._profile = None
        self._profile_token = None
        self._save_token(token)

    def logout(self) -> None:
        self._token = None
        self._token_date = None
        self._profile = None
        self._profile_token = None
        try:
            _TOKEN_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def get_profile(self) -> Optional[dict]:
        if not self._token_valid():
            raise AuthRequired("No valid Fyers access token — please log in.")
        if self._profile and self._profile_token == self._token:
            return self._profile
        _rate_limit()
        resp = self._check(self._model().get_profile())
        d = resp.get("data") or {}
        name = (d.get("name") or d.get("display_name") or d.get("fy_id") or "Fyers Trader").strip()
        prof = {
            "name": name,
            "user_id": (d.get("fy_id") or "").strip(),
            "email": (d.get("email_id") or "").strip(),
            "avatar": (d.get("image") or "").strip(),
            "broker": "Fyers",
        }
        self._profile = prof
        self._profile_token = self._token
        return prof

    # ------------------------------------------------------------------ model
    def _model(self):
        from fyers_apiv3 import fyersModel
        if not self._token_valid():
            raise AuthRequired("No valid Fyers access token — please log in.")
        _CACHE_DIR.mkdir(exist_ok=True)
        return fyersModel.FyersModel(
            client_id=self.app_id, token=self._token,
            is_async=False, log_path=str(_CACHE_DIR),
        )

    @staticmethod
    def _check(resp: dict) -> dict:
        if not isinstance(resp, dict):
            raise RuntimeError(f"Unexpected Fyers response: {resp!r}")
        if resp.get("s") == "error" or resp.get("code", 200) < 0:
            msg = str(resp.get("message", ""))
            if resp.get("code") in (-8, -15, -16, -17) or "token" in msg.lower():
                raise AuthRequired(f"Fyers token rejected: {msg}")
            raise RuntimeError(f"Fyers error: {msg or resp}")
        return resp

    # --------------------------------------------------------------- journal
    def fetch_tradebook(self) -> list[dict]:
        """Raw list of today's executed trades (fills) from Fyers."""
        _rate_limit()             # respect 5/sec + 50/min + daily caps
        resp = self._check(self._model().tradebook())
        return resp.get("tradeBook") or resp.get("tradebook") or []

    def fetch_positions(self) -> dict:
        """Today's positions incl. realized P&L totals."""
        _rate_limit()             # respect 5/sec + 50/min + daily caps
        return self._check(self._model().positions())

    def fetch_trade_history(self, from_date: str | None = None,
                            to_date: str | None = None) -> list[dict]:
        """All executed trades in a date range via Fyers Reports → Trade History.
        Defaults to the current financial year. Paginated (100/page)."""
        fy = self._model()
        getter = getattr(fy, "tradehistory", None)
        if not callable(getter):
            raise RuntimeError("This fyers-apiv3 version has no trade-history API; "
                               "update the package or import a CSV.")
        out: list[dict] = []
        page = 1
        while page <= 200:                      # safety cap (~20k trades)
            data = {"page_size": "100", "page_no": str(page)}
            if from_date:
                data["from_date"] = from_date
            if to_date:
                data["to_date"] = to_date
            _rate_limit()
            resp = getter(data=data)
            if not isinstance(resp, dict):
                break
            if resp.get("s") == "error":
                msg = str(resp.get("message", ""))
                if resp.get("code") in (-8, -15, -16, -17) or "token" in msg.lower():
                    raise AuthRequired(f"Fyers token rejected: {msg}")
                raise RuntimeError(f"Fyers error: {msg or resp}")
            rows = resp.get("data") or resp.get("tradeBook") or []
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 100:
                break
            page += 1
        return out

    # ----------------------------------------------------------- screener
    def calls_today(self) -> int:
        """Total Fyers calls made today (shared across dashboard + screener)."""
        return calls_today()

    def _depth_quote(self, full_symbol: str) -> dict | None:
        """One rate-limited depth quote for any Fyers symbol (equity or index).
        Returns normalized {ltp, open, high, low, prev_close, volume} or None."""
        fy = self._model()        # raises AuthRequired if the token is invalid
        _rate_limit(background=True)   # scanner/ticker — yield to the dashboard
        resp = fy.depth(data={"symbol": full_symbol, "ohlcv_flag": 1})
        if not isinstance(resp, dict):
            return None
        if resp.get("s") == "error":
            msg = str(resp.get("message", ""))
            if resp.get("code") in (-8, -15, -16, -17) or "token" in msg.lower():
                raise AuthRequired(f"Fyers token rejected: {msg}")
            return None
        d = (resp.get("d") or {}).get(full_symbol) or {}
        g = lambda *ks: next((d[k] for k in ks if d.get(k) not in (None, "")), None)
        ltp = g("ltp", "lp")
        if ltp in (None, 0):
            return None
        ch = g("ch")
        prev = (ltp - ch) if ch is not None else g("prev_close_price", "c")
        return {"ltp": ltp, "open": g("o", "open_price"), "high": g("h", "high_price"),
                "low": g("l", "low_price"), "prev_close": prev,
                "volume": g("v", "volume", "vol")}

    def fetch_one_quote(self, ticker: str) -> dict | None:
        """One live quote for an NSE equity (used by the background scanner)."""
        q = self._depth_quote(f"NSE:{ticker}-EQ")
        if q:
            q["ticker"] = ticker
        return q

    def fetch_index_quotes(self, names: list[str]) -> list[dict]:
        """Live spot + % change for index underlyings (NIFTY, BANKNIFTY, SENSEX…).
        A few rate-limited depth calls — used by the ticker tape."""
        out = []
        for nm in names:
            fsym = FYERS_INDEX.get(nm)
            if not fsym:
                continue
            try:
                q = self._depth_quote(fsym)
            except AuthRequired:
                raise
            except Exception:  # noqa: BLE001 - skip this index
                q = None
            if q and q.get("prev_close"):
                chg = (q["ltp"] - q["prev_close"]) / q["prev_close"] * 100
                out.append({"name": nm, "ltp": round(q["ltp"], 2), "chg_pct": round(chg, 2)})
        return out

    def fetch_quotes(self, tickers: list[str]) -> list[dict]:
        """Live quotes for a list of NSE equities (best effort; failures skipped).
        Kept for compatibility — the screener now scans in the background."""
        out = []
        for t in tickers:
            try:
                q = self.fetch_one_quote(t)
            except AuthRequired:
                raise
            except Exception:  # noqa: BLE001 - best effort per symbol
                q = None
            if q:
                out.append(q)
        if not out:
            raise RuntimeError("No quotes returned from the depth endpoint "
                               "(check the Fyers token / data permissions).")
        return out

    def fetch_history(self, ticker: str, resolution: str = "D", days: int = 120,
                      background: bool = False) -> list[list]:
        """Candles [ts,o,h,l,c,v] for an NSE equity. resolution 'D' (daily) or a
        minute string like '5' (5-min). `days` is the lookback window.
        `background=True` yields to user-facing calls (used by the screener's
        level warm-up); the stock-analysis popup leaves it False for priority."""
        fy = self._model()
        end = _ist_now().date()
        start = end - timedelta(days=days)
        _rate_limit(background=background)
        resp = self._check(fy.history(data={
            "symbol": f"NSE:{ticker}-EQ", "resolution": resolution, "date_format": "1",
            "range_from": start.isoformat(), "range_to": end.isoformat(),
            "cont_flag": "1",
        }))
        return resp.get("candles") or []

    # ------------------------------------------------------------- option chain
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        symbol = symbol.upper()
        fsym = FYERS_INDEX.get(symbol)
        if not fsym:
            raise ValueError(f"Unknown symbol for Fyers: {symbol}")

        fy = self._model()
        self._refresh_expiry_cache_if_stale()
        ts = self._expiry_map.get(symbol, {}).get(expiry or "", "")

        _rate_limit()             # respect 5/sec + 50/min + daily caps
        resp = self._check(fy.optionchain(data={"symbol": fsym, "strikecount": _STRIKE_COUNT, "timestamp": ts}))
        data = resp.get("data", {}) or {}

        # Update expiry map (date label -> epoch) for this symbol.
        emap = {e.get("date"): str(e.get("expiry")) for e in data.get("expiryData", []) or [] if e.get("date")}
        if emap:
            self._expiry_map[symbol] = emap
            self._expiry_map_date = _ist_now().strftime("%Y-%m-%d")
        # expiries sorted chronologically by epoch
        expiries = sorted(emap.keys(), key=lambda d: int(emap.get(d, "0") or 0))

        # If a specific expiry was requested but we hadn't cached its epoch yet,
        # re-fetch once with the correct timestamp.
        if expiry and not ts and expiry in emap:
            _rate_limit()
            resp = self._check(fy.optionchain(
                data={"symbol": fsym, "strikecount": _STRIKE_COUNT, "timestamp": emap[expiry]}))
            data = resp.get("data", {}) or {}

        spot = 0.0
        spot_change = spot_change_pct = None
        by_strike: dict[float, StrikeRow] = {}
        for item in data.get("optionsChain", []) or []:
            otype = item.get("option_type", "")
            if otype not in ("CE", "PE"):
                # underlying row carries the index spot price + day change
                if float(item.get("strike_price", -1) or -1) == -1:
                    spot = float(item.get("ltp", spot) or spot)
                    if item.get("ltpch") is not None:
                        spot_change = float(item.get("ltpch") or 0)
                    if item.get("ltpchp") is not None:
                        spot_change_pct = float(item.get("ltpchp") or 0)
                continue
            strike = float(item.get("strike_price", 0) or 0)
            row = by_strike.setdefault(strike, StrikeRow(strike=strike))
            oi = float(item.get("oi", 0) or 0)
            chg = float(item.get("oich", 0) or 0)
            ltp = float(item.get("ltp", 0) or 0)
            vol = float(item.get("volume", 0) or 0)
            iv = float((item.get("greeks") or {}).get("iv", item.get("iv", 0)) or 0)
            if otype == "CE":
                row.ce_oi, row.ce_chg_oi, row.ce_ltp, row.ce_iv, row.ce_volume = oi, chg, ltp, iv, vol
            else:
                row.pe_oi, row.pe_chg_oi, row.pe_ltp, row.pe_iv, row.pe_volume = oi, chg, ltp, iv, vol

        rows = [by_strike[k] for k in sorted(by_strike)]
        cur_expiry = expiry if (expiry and expiry in emap) else (expiries[0] if expiries else "")

        chain = OptionChain(
            symbol=symbol, spot=spot, expiry=cur_expiry, expiries=expiries,
            timestamp=_ist_now().strftime("%d-%b-%Y %H:%M:%S IST"),
            rows=rows, source=self.name,
            spot_change=spot_change, spot_change_pct=spot_change_pct,
        )
        chain.futures = self._fetch_futures(symbol, fy)
        return chain

    def _refresh_expiry_cache_if_stale(self) -> None:
        if self._expiry_map_date != _ist_now().strftime("%Y-%m-%d"):
            self._expiry_map = {}
            self._expiry_map_date = None

    # ----------------------------------------------------------------- futures
    def _monthly_fut_symbols(self, symbol: str) -> list[str]:
        """Build this-month and next-month futures symbols, e.g. NSE:NIFTY26SEPFUT."""
        info = FYERS_FUT_STEM.get(symbol)
        if not info:
            return []
        exch, stem = info
        now = _ist_now()
        out = []
        for add in (0, 1):
            m = now.month - 1 + add
            y = now.year + m // 12
            mon = _MONTHS[m % 12]
            out.append(f"{exch}:{stem}{y % 100:02d}{mon}FUT")
        return out

    def _fetch_futures(self, symbol: str, fy) -> list[FuturesRow]:
        # Futures OI moves slowly and is secondary to the option chain. Serve the
        # cache immediately and refresh in the BACKGROUND so the option-chain
        # response (e.g. when the user switches index) never waits on 2 extra
        # depth calls. First view of a symbol shows no futures; the next poll has
        # them. Also a big saving against the rate limit.
        cached = self._fut_cache.get(symbol)
        if cached and time.time() - cached["at"] < 45:
            return cached["rows"]
        if symbol not in self._fut_fetching:
            self._fut_fetching.add(symbol)
            threading.Thread(target=self._refresh_futures_bg, args=(symbol,),
                             daemon=True, name=f"fut-{symbol}").start()
        return cached["rows"] if cached else []

    def _refresh_futures_bg(self, symbol: str) -> None:
        try:
            fy = self._model()
            out: list[FuturesRow] = []
            for sym in self._monthly_fut_symbols(symbol):
                try:
                    _rate_limit(background=True)   # yield to the dashboard
                    resp = fy.depth(data={"symbol": sym, "ohlcv_flag": 1})
                    if not isinstance(resp, dict) or resp.get("s") == "error":
                        continue
                    d = (resp.get("d") or {}).get(sym) or {}
                    oi = float(d.get("oi", 0) or 0)
                    if oi <= 0:
                        continue
                    out.append(FuturesRow(
                        expiry=_fmt_epoch(d.get("expiry")) or sym.split(":")[-1].replace("FUT", " FUT"),
                        last_price=float(d.get("ltp", 0) or 0),
                        oi=oi,
                        chg_oi=oi - float(d.get("pdoi", 0) or 0),
                        volume=0.0,
                    ))
                except Exception:  # noqa: BLE001
                    continue  # futures are best-effort
            self._fut_cache[symbol] = {"at": time.time(), "rows": out}
        except Exception:  # noqa: BLE001 - never let the bg thread crash
            pass
        finally:
            self._fut_fetching.discard(symbol)
