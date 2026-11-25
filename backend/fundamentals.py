"""Company fundamentals for NSE stocks via Yahoo Finance (free, public).

This is SEPARATE from Fyers — it does not touch the Fyers rate-limit budget.
Results are cached per IST day because fundamentals change slowly, and a failed
fetch is negatively cached for a short while so a blocked/rate-limited Yahoo
does not get hammered. Everything degrades gracefully: if Yahoo is unavailable
the stock detail view still shows the (Fyers-derived) technicals.

Yahoo requires a per-session "crumb" + cookie handshake; we do it once and
reuse the session.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta

import requests

_IST = timezone(timedelta(hours=5, minutes=30))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile"
_SUFFIX = ".NS"            # NSE cash symbols on Yahoo

# A handful of NSE tickers that differ on Yahoo (most map 1:1 to <TICKER>.NS).
_OVERRIDES: dict[str, str] = {
    # Yahoo lists these BSE-only / renamed names differently; fall back cleanly.
}

_OK_TTL = 0.0             # ok results live until the IST date rolls over
_FAIL_TTL = 1800.0        # re-try a failed symbol after 30 min

_cache: dict[str, dict] = {}      # sym -> {"date","ts","ok","data"}
_lock = threading.Lock()

_session: requests.Session | None = None
_crumb: str | None = None
_crumb_ts = 0.0
_sess_lock = threading.Lock()


def _ist_date() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _ensure_crumb(force: bool = False) -> str:
    """Prime cookies + fetch a crumb, reusing the session for ~25 min."""
    global _session, _crumb, _crumb_ts
    with _sess_lock:
        now = time.time()
        if (not force) and _session is not None and _crumb and now - _crumb_ts < 1500:
            return _crumb
        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Accept": "*/*",
                          "Accept-Language": "en-US,en;q=0.9"})
        # Prime cookies — try fc.yahoo.com first, then a real quote page.
        for url in ("https://fc.yahoo.com/",
                    "https://finance.yahoo.com/quote/RELIANCE.NS"):
            try:
                s.get(url, timeout=8)
                if s.cookies:
                    break
            except requests.RequestException:
                continue
        crumb = ""
        for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
            try:
                r = s.get(f"https://{host}/v1/test/getcrumb", timeout=8)
                txt = (r.text or "").strip()
                if txt and "<" not in txt and len(txt) < 40:
                    crumb = txt
                    break
            except requests.RequestException:
                continue
        if not crumb:
            raise RuntimeError("could not obtain Yahoo crumb")
        _session, _crumb, _crumb_ts = s, crumb, now
        return crumb


def _num(d: dict | None, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, dict):
        return cur.get("raw")
    return cur


def _str(d: dict | None, key: str):
    v = (d or {}).get(key)
    if isinstance(v, dict):
        v = v.get("raw") or v.get("fmt")
    return v if isinstance(v, (str, int, float)) else None


def _fetch_raw(ysym: str) -> dict:
    crumb = _ensure_crumb()
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ysym}"
    params = {"modules": _MODULES, "crumb": crumb}
    sess = _session
    r = sess.get(url, params=params, timeout=10)
    if r.status_code in (401, 403, 404):
        # crumb/cookie likely stale — refresh once and retry.
        crumb = _ensure_crumb(force=True)
        params["crumb"] = crumb
        sess = _session
        r = sess.get(url, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()
    res = (((js or {}).get("quoteSummary") or {}).get("result") or [None])[0]
    if not res:
        raise RuntimeError("no fundamentals in response")
    return res


def _map(res: dict, sym: str) -> dict:
    price = res.get("price") or {}
    sd = res.get("summaryDetail") or {}
    ks = res.get("defaultKeyStatistics") or {}
    fd = res.get("financialData") or {}
    ap = res.get("assetProfile") or {}

    de = _num(fd, "debtToEquity")
    out = {
        "ok": True,
        "symbol": sym,
        "source": "Yahoo Finance",
        "name": _str(price, "longName") or _str(price, "shortName"),
        "exchange": _str(price, "exchangeName"),
        "currency": _str(price, "currency") or "INR",
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "website": ap.get("website"),
        "employees": ap.get("fullTimeEmployees"),
        "summary": ap.get("longBusinessSummary"),
        # valuation
        "market_cap": _num(price, "marketCap"),
        "enterprise_value": _num(ks, "enterpriseValue"),
        "pe": _num(sd, "trailingPE"),
        "forward_pe": _num(sd, "forwardPE"),
        "peg": _num(ks, "pegRatio"),
        "pb": _num(ks, "priceToBook"),
        "eps": _num(ks, "trailingEps"),
        "forward_eps": _num(ks, "forwardEps"),
        "book_value": _num(ks, "bookValue"),
        "div_yield": _num(sd, "dividendYield"),          # fraction
        "div_rate": _num(sd, "dividendRate"),            # ₹/share
        "beta": _num(sd, "beta"),
        # range
        "week52_high": _num(sd, "fiftyTwoWeekHigh"),
        "week52_low": _num(sd, "fiftyTwoWeekLow"),
        "day_high": _num(sd, "dayHigh"),
        "day_low": _num(sd, "dayLow"),
        "avg_vol": _num(sd, "averageVolume"),
        "price_yahoo": _num(price, "regularMarketPrice"),
        "chg_pct_yahoo": _num(price, "regularMarketChangePercent"),
        "change_52w": _num(ks, "52WeekChange"),          # fraction
        # profitability / health (fractions where %)
        "roe": _num(fd, "returnOnEquity"),
        "roa": _num(fd, "returnOnAssets"),
        "profit_margin": _num(fd, "profitMargins"),
        "oper_margin": _num(fd, "operatingMargins"),
        "gross_margin": _num(fd, "grossMargins"),
        "rev_growth": _num(fd, "revenueGrowth"),
        "earn_growth": _num(fd, "earningsGrowth"),
        "debt_to_equity": (de / 100.0) if isinstance(de, (int, float)) else None,
        "current_ratio": _num(fd, "currentRatio"),
        "total_revenue": _num(fd, "totalRevenue"),
        "ebitda": _num(fd, "ebitda"),
        "total_cash": _num(fd, "totalCash"),
        "total_debt": _num(fd, "totalDebt"),
        "shares_out": _num(ks, "sharesOutstanding"),
        # analyst view
        "recommendation": fd.get("recommendationKey"),
        "num_analysts": _num(fd, "numberOfAnalystOpinions"),
        "target_mean": _num(fd, "targetMeanPrice"),
        "target_high": _num(fd, "targetHighPrice"),
        "target_low": _num(fd, "targetLowPrice"),
    }
    return out


def get(ticker: str) -> dict:
    """Return a fundamentals dict for an NSE ticker. Never raises."""
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"ok": False, "note": "No symbol."}
    ysym = _OVERRIDES.get(sym, sym + _SUFFIX)
    today = _ist_date()
    now = time.time()

    with _lock:
        c = _cache.get(sym)
        if c and c["date"] == today:
            if c["ok"] or (now - c["ts"] < _FAIL_TTL):
                return c["data"]

    try:
        data = _map(_fetch_raw(ysym), sym)
    except Exception as e:  # noqa: BLE001 - fundamentals are best-effort
        data = {"ok": False, "symbol": sym,
                "note": "Fundamentals are temporarily unavailable for this stock."}
        _ = e

    with _lock:
        _cache[sym] = {"date": today, "ts": now,
                       "ok": bool(data.get("ok")), "data": data}
    return data


def demo(ticker: str) -> dict:
    """Synthetic fundamentals so the detail view is populated in demo mode."""
    import random
    sym = (ticker or "DEMO").strip().upper()
    rng = random.Random(sum(ord(c) for c in sym) * 7 + 1)
    sectors = [("Financial Services", "Banks - Regional"),
               ("Technology", "Information Technology Services"),
               ("Consumer Cyclical", "Auto Manufacturers"),
               ("Healthcare", "Drug Manufacturers - Specialty"),
               ("Energy", "Oil & Gas Refining & Marketing"),
               ("Basic Materials", "Steel")]
    sec, ind = rng.choice(sectors)
    cap = rng.uniform(8_000, 1_900_000) * 1e7   # ₹8k Cr – ₹19L Cr
    eps = rng.uniform(8, 120)
    pe = rng.uniform(9, 65)
    price = round(eps * pe, 2)
    return {
        "ok": True, "symbol": sym, "source": "Demo data",
        "name": sym.title() + " Ltd (demo)", "exchange": "NSI", "currency": "INR",
        "sector": sec, "industry": ind,
        "summary": (f"{sym.title()} Ltd is a demo company used while the app runs "
                    "without a live Fyers/Yahoo connection. Figures here are "
                    "synthetic and for layout testing only."),
        "website": "https://example.com", "employees": rng.randint(2_000, 120_000),
        "market_cap": cap, "enterprise_value": cap * rng.uniform(0.9, 1.3),
        "pe": round(pe, 2), "forward_pe": round(pe * rng.uniform(0.8, 1.05), 2),
        "peg": round(rng.uniform(0.6, 3.2), 2), "pb": round(rng.uniform(0.8, 12), 2),
        "eps": round(eps, 2), "forward_eps": round(eps * rng.uniform(1.0, 1.25), 2),
        "book_value": round(price / rng.uniform(1.2, 8), 2),
        "div_yield": round(rng.uniform(0, 0.04), 4), "div_rate": round(rng.uniform(0, 30), 2),
        "beta": round(rng.uniform(0.4, 1.8), 2),
        "week52_high": round(price * rng.uniform(1.05, 1.6), 2),
        "week52_low": round(price * rng.uniform(0.5, 0.92), 2),
        "avg_vol": rng.randint(2_00_000, 90_00_000),
        "price_yahoo": price, "chg_pct_yahoo": round(rng.uniform(-3, 3), 2),
        "change_52w": round(rng.uniform(-0.3, 0.8), 3),
        "roe": round(rng.uniform(0.05, 0.35), 3), "roa": round(rng.uniform(0.02, 0.18), 3),
        "profit_margin": round(rng.uniform(0.04, 0.3), 3),
        "oper_margin": round(rng.uniform(0.08, 0.4), 3),
        "gross_margin": round(rng.uniform(0.2, 0.7), 3),
        "rev_growth": round(rng.uniform(-0.1, 0.4), 3),
        "earn_growth": round(rng.uniform(-0.2, 0.6), 3),
        "debt_to_equity": round(rng.uniform(0.0, 1.8), 2),
        "current_ratio": round(rng.uniform(0.8, 3.0), 2),
        "total_revenue": cap * rng.uniform(0.2, 1.1), "ebitda": cap * rng.uniform(0.05, 0.3),
        "total_cash": cap * rng.uniform(0.02, 0.2), "total_debt": cap * rng.uniform(0.0, 0.5),
        "shares_out": cap / price if price else None,
        "recommendation": rng.choice(["buy", "hold", "strong_buy", "sell", "underperform"]),
        "num_analysts": rng.randint(4, 42),
        "target_mean": round(price * rng.uniform(0.95, 1.3), 2),
        "target_high": round(price * rng.uniform(1.3, 1.7), 2),
        "target_low": round(price * rng.uniform(0.6, 0.95), 2),
    }
