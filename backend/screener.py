"""Intraday stock screener.

Ranks a universe of liquid NSE F&O stocks by measurable intraday factors
(% change, gap, day range, volume) from live Fyers quotes. This surfaces where
the movement and liquidity are — it is NOT buy/sell advice.
"""
from __future__ import annotations

# NSE F&O (derivatives) stocks — the liquid intraday universe. Edit as SEBI
# adds/removes names; unknown tickers simply get skipped when quoted.
FO_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "HINDUNILVR",
    "ITC", "LT", "KOTAKBANK", "AXISBANK", "BHARTIARTL", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND",
    "ONGC", "NTPC", "POWERGRID", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "ADANIENT",
    "ADANIPORTS", "COALINDIA", "GRASIM", "HINDALCO", "BRITANNIA", "CIPLA", "DRREDDY",
    "DIVISLAB", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "BAJAJFINSV", "INDUSINDBK",
    "M&M", "TECHM", "UPL", "APOLLOHOSP", "BPCL", "IOC", "GAIL", "VEDL", "DLF",
    "GODREJCP", "DABUR", "MARICO", "PIDILITIND", "HAVELLS", "SIEMENS", "ABB",
    "AMBUJACEM", "ACC", "SAIL", "NMDC", "HINDPETRO", "PETRONET", "IGL", "MGL",
    "GUJGASLTD", "TORNTPHARM", "LUPIN", "AUROPHARMA", "BIOCON", "ALKEM", "GLENMARK",
    "LAURUSLABS", "PEL", "SBILIFE", "HDFCLIFE", "ICICIPRULI", "ICICIGI", "CHOLAFIN",
    "MUTHOOTFIN", "MANAPPURAM", "LICHSGFIN", "PFC", "RECLTD", "BANKBARODA", "PNB",
    "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK", "RBLBANK",
    "INDHOTEL", "JUBLFOOD", "TRENT", "DMART", "NAUKRI", "ZOMATO", "PAYTM", "NYKAA",
    "POLICYBZR", "IRCTC", "CONCOR", "BEL", "HAL", "BHEL", "BHARATFORG", "MOTHERSON",
    "BALKRISIND", "MRF", "APOLLOTYRE", "EXIDEIND", "ESCORTS", "ASHOKLEY", "TVSMOTOR",
    "PERSISTENT", "COFORGE", "LTIM", "LTTS", "MPHASIS", "OFSS", "TATAELXSI",
    "CYIENT", "SONACOMS", "DIXON", "VOLTAS", "CROMPTON", "POLYCAB", "ASTRAL",
    "SUPREMEIND", "APLAPOLLO", "JINDALSTEL", "TATAPOWER", "TORNTPOWER", "ADANIGREEN",
    "ADANIPOWER", "NHPC", "IDEA", "RAMCOCEM", "DALBHARAT", "JKCEMENT", "SUNTV",
    "GMRINFRA", "IRB", "RVNL", "IRFC", "OIL", "CUMMINSIND", "ABCAPITAL", "SRF",
    "DEEPAKNTR", "AARTIIND", "NAVINFLUOR", "ATUL", "COROMANDEL", "PIIND", "UBL",
    "COLPAL", "BERGEPAINT", "PAGEIND", "INDIGO", "MFSL", "MCX", "ANGELONE",
]


def fyers_symbol(ticker: str) -> str:
    return f"NSE:{ticker}-EQ"


def _metrics(q: dict) -> dict | None:
    """Turn one normalized quote into screener metrics, or None if unusable."""
    ltp = float(q.get("ltp") or 0)
    prev = float(q.get("prev_close") or 0)
    op = float(q.get("open") or 0)
    hi = float(q.get("high") or 0)
    lo = float(q.get("low") or 0)
    vol = float(q.get("volume") or 0)
    if ltp <= 0 or prev <= 0:
        return None
    chg_pct = round((ltp - prev) / prev * 100, 2)
    gap_pct = round((op - prev) / prev * 100, 2) if op > 0 else 0.0
    range_pct = round((hi - lo) / prev * 100, 2) if hi > 0 and lo > 0 else 0.0
    return {
        "symbol": q.get("ticker") or "",
        "ltp": round(ltp, 2),
        "chg_pct": chg_pct,
        "gap_pct": gap_pct,
        "range_pct": range_pct,
        "volume": int(vol),
    }


_SORTS = {
    "movers": lambda r: -abs(r["chg_pct"]),      # biggest absolute move
    "gainers": lambda r: -r["chg_pct"],
    "losers": lambda r: r["chg_pct"],
    "volatile": lambda r: -r["range_pct"],       # widest day range
    "volume": lambda r: -r["volume"],
    "gap": lambda r: -abs(r["gap_pct"]),
}


def rank_quotes(quotes: list[dict], sort: str = "movers", limit: int = 60) -> list[dict]:
    """Compute metrics for quotes and return them sorted by the chosen factor."""
    rows = [m for m in (_metrics(q) for q in quotes) if m]
    key = _SORTS.get(sort, _SORTS["movers"])
    rows.sort(key=key)
    return rows[:limit]


def demo_rows(sort: str = "movers", limit: int = 60) -> list[dict]:
    """Synthetic screener rows so the UI works without a Fyers login (demo mode)."""
    import random
    rng = random.Random(42)
    rows = []
    for t in FO_STOCKS:
        chg = round(rng.uniform(-6, 6), 2)
        rows.append({"symbol": t, "ltp": round(rng.uniform(100, 4000), 2),
                     "chg_pct": chg, "gap_pct": round(rng.uniform(-2.5, 2.5), 2),
                     "range_pct": round(rng.uniform(0.6, 7), 2),
                     "volume": rng.randint(100_000, 25_000_000)})
    rows.sort(key=_SORTS.get(sort, _SORTS["movers"]))
    return rows[:limit]
