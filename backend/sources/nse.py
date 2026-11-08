"""NSE public option-chain data source.

Unofficial endpoint. NSE blocks datacenter IPs and bare requests, so we:
  * use a real browser User-Agent and matching headers,
  * seed cookies by first hitting the option-chain page,
  * retry with a fresh cookie jar when a request is blocked.

This is reliable enough for a personal dashboard running on a home connection.
It is NOT suitable for a public server. For that, switch DATA_SOURCE=upstox.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from .base import DataSource, OptionChain, StrikeRow, FuturesRow

BASE = "https://www.nseindia.com"
OC_URL = BASE + "/api/option-chain-indices?symbol={symbol}"
FUT_URL = BASE + "/api/quote-derivative?symbol={symbol}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": BASE + "/option-chain",
    "Connection": "keep-alive",
}


class NSEDataSource(DataSource):
    name = "nse"

    def __init__(self) -> None:
        self._session: Optional[requests.Session] = None
        self._seeded_at = 0.0

    def _get_session(self, force: bool = False) -> requests.Session:
        # Cookies go stale; refresh the session periodically or on demand.
        if force or self._session is None or (time.time() - self._seeded_at) > 300:
            s = requests.Session()
            s.headers.update(HEADERS)
            # Seed cookies by visiting real pages first.
            try:
                s.get(BASE, timeout=10)
                s.get(BASE + "/option-chain", timeout=10)
            except requests.RequestException:
                pass
            self._session = s
            self._seeded_at = time.time()
        return self._session

    def _fetch_json(self, url: str) -> dict:
        last_err: Optional[Exception] = None
        for attempt in range(3):
            session = self._get_session(force=attempt > 0)
            try:
                resp = session.get(url, timeout=12)
                if resp.status_code == 200 and resp.text.strip().startswith("{"):
                    return resp.json()
                last_err = RuntimeError(f"HTTP {resp.status_code} from NSE")
            except requests.RequestException as e:
                last_err = e
            time.sleep(1.0 + attempt)
        raise RuntimeError(f"NSE request failed for {url}: {last_err}")

    # ------------------------------------------------------------------ #
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        symbol = symbol.upper()
        data = self._fetch_json(OC_URL.format(symbol=symbol))
        records = data.get("records", {})
        all_rows = records.get("data", [])
        expiries = records.get("expiryDates", []) or []
        spot = float(records.get("underlyingValue", 0) or 0)
        timestamp = records.get("timestamp", "")

        if not expiry:
            expiry = expiries[0] if expiries else ""

        by_strike: dict[float, StrikeRow] = {}
        for item in all_rows:
            if item.get("expiryDate") != expiry:
                continue
            strike = float(item.get("strikePrice", 0) or 0)
            row = by_strike.setdefault(strike, StrikeRow(strike=strike))
            ce = item.get("CE")
            pe = item.get("PE")
            if ce:
                row.ce_oi = float(ce.get("openInterest", 0) or 0)
                row.ce_chg_oi = float(ce.get("changeinOpenInterest", 0) or 0)
                row.ce_ltp = float(ce.get("lastPrice", 0) or 0)
                row.ce_iv = float(ce.get("impliedVolatility", 0) or 0)
                row.ce_volume = float(ce.get("totalTradedVolume", 0) or 0)
            if pe:
                row.pe_oi = float(pe.get("openInterest", 0) or 0)
                row.pe_chg_oi = float(pe.get("changeinOpenInterest", 0) or 0)
                row.pe_ltp = float(pe.get("lastPrice", 0) or 0)
                row.pe_iv = float(pe.get("impliedVolatility", 0) or 0)
                row.pe_volume = float(pe.get("totalTradedVolume", 0) or 0)

        rows = [by_strike[k] for k in sorted(by_strike)]

        chain = OptionChain(
            symbol=symbol,
            spot=spot,
            expiry=expiry,
            expiries=expiries,
            timestamp=timestamp,
            rows=rows,
            source=self.name,
        )
        chain.futures = self._fetch_futures(symbol)
        return chain

    def _fetch_futures(self, symbol: str) -> list[FuturesRow]:
        try:
            data = self._fetch_json(FUT_URL.format(symbol=symbol))
        except RuntimeError:
            return []
        out: list[FuturesRow] = []
        for stock in data.get("stocks", []) or []:
            meta = stock.get("metadata", {}) or {}
            itype = (meta.get("instrumentType") or "").lower()
            if "fut" not in itype:
                continue
            book = (stock.get("marketDeptOrderBook", {}) or {}).get("tradeInfo", {}) or {}
            out.append(
                FuturesRow(
                    expiry=meta.get("expiryDate", ""),
                    last_price=float(meta.get("lastPrice", 0) or 0),
                    oi=float(book.get("openInterest", meta.get("openInterest", 0)) or 0),
                    chg_oi=float(book.get("changeinOpenInterest", 0) or 0),
                    volume=float(book.get("vmap", 0) or meta.get("numberOfContractsTraded", 0) or 0),
                )
            )
        return out
