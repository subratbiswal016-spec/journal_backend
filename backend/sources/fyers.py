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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import config
from .base import AuthRequired, DataSource, FuturesRow, OptionChain, StrikeRow

IST = timezone(timedelta(hours=5, minutes=30))
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
        self._save_token(token)

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
        resp = self._check(self._model().tradebook())
        return resp.get("tradeBook") or resp.get("tradebook") or []

    def fetch_positions(self) -> dict:
        """Today's positions incl. realized P&L totals."""
        return self._check(self._model().positions())

    # ------------------------------------------------------------- option chain
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        symbol = symbol.upper()
        fsym = FYERS_INDEX.get(symbol)
        if not fsym:
            raise ValueError(f"Unknown symbol for Fyers: {symbol}")

        fy = self._model()
        self._refresh_expiry_cache_if_stale()
        ts = self._expiry_map.get(symbol, {}).get(expiry or "", "")

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
        out: list[FuturesRow] = []
        for sym in self._monthly_fut_symbols(symbol):
            try:
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
            except Exception:
                continue  # futures are best-effort
        return out
