"""Upstox data source — official, free, reliable live option-chain OI.

Uses the Upstox v2 REST API:
  * OAuth code flow for a daily access token
      GET  https://api.upstox.com/v2/login/authorization/dialog   (login page)
      POST https://api.upstox.com/v2/login/authorization/token    (code -> token)
  * GET  https://api.upstox.com/v2/option/chain      (strike-wise OI, LTP, IV, greeks)
  * GET  https://api.upstox.com/v2/option/contract   (list of expiries)
  * GET  https://api.upstox.com/v2/market-quote/quotes (futures OI / last price)
  * Instrument master (for futures instrument keys):
      https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz

Access tokens expire at 03:30 IST the next day, so the dashboard shows a
"Login with Upstox" button to mint a fresh one each morning. The token is cached
to .upstox_token.json so restarting the server the same day doesn't require
logging in again.

Setup (one time):
  1. Open a free Upstox account (KYC with PAN + Aadhaar).
  2. Create an API app at https://account.upstox.com/developer/apps with the
     redirect URL set to exactly  http://127.0.0.1:8787/upstox/callback
  3. Put the API key + secret in your .env file (UPSTOX_API_KEY / UPSTOX_API_SECRET).
  4. Set DATA_SOURCE=upstox in .env, start the server, click "Login with Upstox".
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from .. import config
from .base import AuthRequired, DataSource, FuturesRow, OptionChain, StrikeRow

API = "https://api.upstox.com/v2"
DIALOG_URL = API + "/login/authorization/dialog"
TOKEN_URL = API + "/login/authorization/token"
CHAIN_URL = API + "/option/chain"
CONTRACT_URL = API + "/option/contract"
QUOTE_URL = API + "/market-quote/quotes"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

IST = timezone(timedelta(hours=5, minutes=30))

# Underlying instrument keys used by the option endpoints.
INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
}
# underlying_symbol as it appears in the instrument master, for futures lookup.
FUT_UNDERLYING = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOKEN_FILE = _PROJECT_ROOT / ".upstox_token.json"
_CACHE_DIR = _PROJECT_ROOT / ".cache"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _ist_today_str() -> str:
    return _ist_now().strftime("%Y-%m-%d")


class UpstoxDataSource(DataSource):
    name = "upstox"
    login_label = "Login with Upstox"

    def __init__(self) -> None:
        self.api_key = config.UPSTOX_API_KEY
        self.api_secret = config.UPSTOX_API_SECRET
        self.redirect_uri = config.UPSTOX_REDIRECT_URI
        self._token: Optional[str] = None
        self._token_date: Optional[str] = None  # IST date the token was issued
        self._fut_index: Optional[dict] = None   # underlying -> [(expiry_ms, key, tsym)]
        self._fut_index_date: Optional[str] = None
        self._load_token()

    # ----------------------------------------------------------------- token
    def _load_token(self) -> None:
        # Explicit token in .env wins (advanced use); assume it's for today.
        if config.UPSTOX_ACCESS_TOKEN:
            self._token = config.UPSTOX_ACCESS_TOKEN
            self._token_date = _ist_today_str()
            return
        try:
            if _TOKEN_FILE.exists():
                data = json.loads(_TOKEN_FILE.read_text())
                self._token = data.get("access_token")
                self._token_date = data.get("date")
        except (OSError, ValueError):
            self._token = None
            self._token_date = None

    def _save_token(self, token: str) -> None:
        self._token = token
        self._token_date = _ist_today_str()
        try:
            _TOKEN_FILE.write_text(json.dumps({"date": self._token_date, "access_token": token}))
        except OSError:
            pass  # in-memory token still works for this run

    def _token_valid(self) -> bool:
        if not self._token or not self._token_date:
            return False
        now = _ist_now()
        today = now.strftime("%Y-%m-%d")
        if self._token_date == today:
            return True
        # Tokens live until 03:30 IST the next day.
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if self._token_date == yesterday and (now.hour < 3 or (now.hour == 3 and now.minute < 30)):
            return True
        return False

    def needs_login(self) -> bool:
        return not self._token_valid()

    # ------------------------------------------------------------------ oauth
    def login_url(self, state: str = "") -> str:
        if not self.api_key:
            raise AuthRequired("UPSTOX_API_KEY is not set in .env")
        from urllib.parse import urlencode
        params = {
            "response_type": "code",
            "client_id": self.api_key,
            "redirect_uri": self.redirect_uri,
        }
        if state:
            params["state"] = state
        return DIALOG_URL + "?" + urlencode(params)

    def exchange_code(self, code: str) -> None:
        if not (self.api_key and self.api_secret):
            raise AuthRequired("UPSTOX_API_KEY / UPSTOX_API_SECRET not set in .env")
        resp = requests.post(
            TOKEN_URL,
            headers={"accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "code": code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise AuthRequired(f"Upstox token exchange failed: {body}")
        self._save_token(token)

    # ------------------------------------------------------------------ http
    def _auth_headers(self) -> dict:
        if not self._token_valid():
            raise AuthRequired("No valid Upstox access token — please log in.")
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, url: str, params: dict) -> dict:
        resp = requests.get(url, headers=self._auth_headers(), params=params, timeout=20)
        if resp.status_code == 401:
            self._token = None
            raise AuthRequired("Upstox token rejected (expired). Please log in again.")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------- option chain
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        symbol = symbol.upper()
        key = INDEX_KEYS.get(symbol)
        if not key:
            raise ValueError(f"Unknown symbol for Upstox: {symbol}")

        expiries = self._get_expiries(key)
        if not expiry:
            expiry = expiries[0] if expiries else "current_week"

        body = self._get(CHAIN_URL, {"instrument_key": key, "expiry_date": expiry})
        items = body.get("data", []) or []

        rows: list[StrikeRow] = []
        spot = 0.0
        for it in items:
            spot = float(it.get("underlying_spot_price", spot) or spot)
            strike = float(it.get("strike_price", 0) or 0)
            ce = it.get("call_options") or {}
            pe = it.get("put_options") or {}
            ce_md, ce_g = ce.get("market_data") or {}, ce.get("option_greeks") or {}
            pe_md, pe_g = pe.get("market_data") or {}, pe.get("option_greeks") or {}

            def chg(md):
                return float(md.get("oi", 0) or 0) - float(md.get("prev_oi", 0) or 0)

            rows.append(StrikeRow(
                strike=strike,
                ce_oi=float(ce_md.get("oi", 0) or 0), ce_chg_oi=chg(ce_md),
                ce_ltp=float(ce_md.get("ltp", 0) or 0), ce_iv=float(ce_g.get("iv", 0) or 0),
                ce_volume=float(ce_md.get("volume", 0) or 0),
                pe_oi=float(pe_md.get("oi", 0) or 0), pe_chg_oi=chg(pe_md),
                pe_ltp=float(pe_md.get("ltp", 0) or 0), pe_iv=float(pe_g.get("iv", 0) or 0),
                pe_volume=float(pe_md.get("volume", 0) or 0),
            ))

        rows.sort(key=lambda r: r.strike)
        chain = OptionChain(
            symbol=symbol, spot=spot, expiry=expiry, expiries=expiries,
            timestamp=_ist_now().strftime("%d-%b-%Y %H:%M:%S IST"),
            rows=rows, source=self.name,
        )
        chain.futures = self._fetch_futures(symbol)
        return chain

    def _get_expiries(self, key: str) -> list[str]:
        try:
            body = self._get(CONTRACT_URL, {"instrument_key": key})
        except AuthRequired:
            raise
        except Exception:
            return []
        today = _ist_today_str()
        seen = sorted({c.get("expiry") for c in body.get("data", []) or [] if c.get("expiry")})
        return [e for e in seen if e >= today] or seen

    # ----------------------------------------------------------------- futures
    def _fetch_futures(self, symbol: str) -> list[FuturesRow]:
        try:
            index = self._load_futures_index()
            underlying = FUT_UNDERLYING.get(symbol, symbol)
            contracts = index.get(underlying, [])[:2]  # nearest 2 expiries
            if not contracts:
                return []
            keys = [c[1] for c in contracts]
            body = self._get(QUOTE_URL, {"instrument_key": ",".join(keys)})
            quotes = body.get("data", {}) or {}
            # Map by instrument_token inside each quote (outer keys use ':' not '|').
            by_token = {}
            for q in quotes.values():
                tok = q.get("instrument_token")
                if tok:
                    by_token[tok] = q
            out: list[FuturesRow] = []
            for exp_ms, k, tsym in contracts:
                q = by_token.get(k, {})
                out.append(FuturesRow(
                    expiry=_ms_to_date(exp_ms),
                    last_price=float(q.get("last_price", 0) or 0),
                    oi=float(q.get("oi", 0) or 0),
                    chg_oi=float(q.get("oi", 0) or 0) - float((q.get("ohlc") or {}).get("oi", 0) or 0),
                    volume=float(q.get("volume", 0) or 0),
                ))
            return out
        except AuthRequired:
            raise
        except Exception:
            return []  # futures are best-effort; never break the option chain

    def _load_futures_index(self) -> dict:
        today = _ist_today_str()
        if self._fut_index is not None and self._fut_index_date == today:
            return self._fut_index

        cache_file = _CACHE_DIR / f"upstox_futs_{today}.json"
        if cache_file.exists():
            try:
                self._fut_index = json.loads(cache_file.read_text())
                self._fut_index_date = today
                return self._fut_index
            except (OSError, ValueError):
                pass

        # Download + filter the instrument master to just index futures.
        resp = requests.get(MASTER_URL, timeout=60)
        resp.raise_for_status()
        raw = gzip.decompress(resp.content)
        master = json.loads(raw)

        index: dict[str, list] = {}
        wanted = set(FUT_UNDERLYING.values())
        for row in master:
            if row.get("segment") != "NSE_FO" or row.get("instrument_type") != "FUT":
                continue
            us = row.get("underlying_symbol")
            if us not in wanted:
                continue
            index.setdefault(us, []).append([
                _as_ms(row.get("expiry")), row.get("instrument_key"), row.get("trading_symbol", ""),
            ])
        for us in index:
            index[us].sort(key=lambda x: x[0])

        self._fut_index = index
        self._fut_index_date = today
        try:
            _CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(index))
            for old in _CACHE_DIR.glob("upstox_futs_*.json"):
                if old.name != cache_file.name:
                    old.unlink(missing_ok=True)
        except OSError:
            pass
        return index


def _as_ms(expiry) -> int:
    """Instrument master 'expiry' may be epoch-ms (int) or 'YYYY-MM-dd' string."""
    if isinstance(expiry, (int, float)):
        return int(expiry)
    try:
        dt = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").replace(tzinfo=IST)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _ms_to_date(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b-%Y")
    except (ValueError, OverflowError, OSError):
        return ""
