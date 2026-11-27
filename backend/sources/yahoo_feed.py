"""Free live/EOD equity & index quotes via Yahoo Finance (used by non-Fyers
live brokers such as Dhan, Upstox, and NSE so Screener, Ticker, and
daily levels work with zero extra paid Data API subscription).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

_IDX_YAHOO = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MIDCAP_100.NS",
}

_session: requests.Session | None = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": _UA})
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        s.mount("https://", adapter)
        _session = s
    return _session


def _to_yahoo(ticker: str) -> str:
    raw = (ticker or "").strip().upper()
    if raw in _IDX_YAHOO:
        return _IDX_YAHOO[raw]
    is_bse = raw.startswith("BSE:")
    clean = raw.split(":", 1)[1] if ":" in raw else raw
    clean = clean.rsplit("-", 1)[0] if "-" in clean else clean
    return f"{clean}.BO" if is_bse else f"{clean}.NS"


def _chart_json(ysym: str, interval: str = "1d", rng: str = "5d") -> dict | None:
    try:
        r = _sess().get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ysym)}",
            params={"interval": interval, "range": rng},
            timeout=6,
        )
        if r.status_code != 200:
            return None
        return (((r.json() or {}).get("chart") or {}).get("result") or [None])[0]
    except Exception:  # noqa: BLE001
        return None


def fetch_one_quote(ticker: str) -> dict | None:
    res = _chart_json(_to_yahoo(ticker), "1d", "5d")
    if not res:
        return None
    meta = res.get("meta") or {}
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []
    bars = [
        (o, h, l, c, v)
        for o, h, l, c, v in zip(opens, highs, lows, closes, vols or [0] * len(closes))
        if None not in (o, h, l, c)
    ]
    if not bars:
        return None
    last = bars[-1]
    prev = float(bars[-2][3]) if len(bars) >= 2 else float(meta.get("chartPreviousClose") or last[0])
    ltp = float(meta.get("regularMarketPrice") or last[3])
    if not ltp:
        return None
    hi = float(meta.get("regularMarketDayHigh") or last[1])
    lo = float(meta.get("regularMarketDayLow") or last[2])
    vol = int(meta.get("regularMarketVolume") or last[4] or 0)
    return {
        "ticker": ticker,
        "ltp": round(ltp, 2),
        "open": round(float(last[0]), 2),
        "high": round(hi, 2),
        "low": round(lo, 2),
        "prev_close": round(prev, 2),
        "volume": vol,
    }


def fetch_batch_quotes(tickers: list[str]) -> dict[str, dict]:
    batch = tickers[:50]
    if not batch:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for t, q in zip(batch, pool.map(fetch_one_quote, batch)):
            if q:
                out[t] = q
    return out


def fetch_index_quotes(names: list[str]) -> list[dict]:
    out = []
    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        for nm, q in zip(names, pool.map(fetch_one_quote, names)):
            if q and q.get("prev_close"):
                chg = (q["ltp"] - q["prev_close"]) / q["prev_close"] * 100
                out.append({"name": nm, "ltp": round(q["ltp"], 2), "chg_pct": round(chg, 2)})
    return out


def fetch_history(ticker: str, resolution: str = "D", days: int = 365,
                  background: bool = False) -> list[list]:
    is_daily = str(resolution).upper() == "D"
    interval = "1d" if is_daily else "5m"
    rng = ("1y" if days >= 180 else "6mo" if days >= 60 else "1mo") if is_daily else "5d"
    res = _chart_json(_to_yahoo(ticker), interval, rng)
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    os_, hs, ls, cs, vs = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    out = []
    for t0, o0, h0, l0, c0, v0 in zip(ts, os_, hs, ls, cs, vs or [0] * len(ts)):
        if None in (o0, h0, l0, c0):
            continue
        out.append([int(t0), round(float(o0), 2), round(float(h0), 2),
                    round(float(l0), 2), round(float(c0), 2), int(v0 or 0)])
    return out
