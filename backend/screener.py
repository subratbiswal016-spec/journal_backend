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


def _quote_signal(chg_pct: float, pos: float, above_open: bool) -> tuple[str, str]:
    """A light intraday read from the live quote (no history needed).
    pos = where LTP sits in the day's range (0 = at low, 1 = at high)."""
    if chg_pct > 0.3 and pos >= 0.6 and above_open:
        return "Bullish", f"Up {chg_pct:+.1f}%, holding the upper part of the day's range, above the open"
    if chg_pct < -0.3 and pos <= 0.4 and not above_open:
        return "Bearish", f"Down {chg_pct:+.1f}%, stuck in the lower part of the day's range, below the open"
    return "Neutral", f"{chg_pct:+.1f}%, mid-range — no clear intraday edge"


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
    pos = (ltp - lo) / (hi - lo) if hi > lo else 0.5     # position in day range
    signal, why = _quote_signal(chg_pct, pos, ltp > op if op else False)
    return {
        "symbol": q.get("ticker") or "",
        "ltp": round(ltp, 2),
        "chg_pct": chg_pct,
        "gap_pct": gap_pct,
        "range_pct": range_pct,
        "volume": int(vol),
        "signal": signal,
        "why": why,
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


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period        # seed with SMA
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def analyze(symbol: str, candles: list[list]) -> dict:
    """Trend + momentum read from daily candles ([ts,o,h,l,c,v]). Data, not advice."""
    closes = [float(c[4]) for c in candles if c and len(c) >= 5]
    if len(closes) < 20:
        return {"symbol": symbol, "ok": False,
                "note": "Not enough history to analyse this stock."}
    price = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    rsi = _rsi(closes)
    look = candles[-20:]
    hi20 = max(float(c[2]) for c in look)
    lo20 = min(float(c[3]) for c in look)

    above20 = ema20 is not None and price > ema20
    above50 = ema50 is not None and price > ema50
    if above20 and above50 and (ema50 is None or (ema20 or 0) > (ema50 or 0)):
        trend = "Uptrend"
    elif (not above20) and (not above50) and (ema50 is None or (ema20 or 0) < (ema50 or 0)):
        trend = "Downtrend"
    else:
        trend = "Sideways"

    notes = []
    notes.append(f"Price ₹{price:,.2f} is {'above' if above20 else 'below'} the 20-EMA"
                 + (f" (₹{ema20:,.0f})" if ema20 else "")
                 + f" and {'above' if above50 else 'below'} the 50-EMA"
                 + (f" (₹{ema50:,.0f})" if ema50 else "") + ".")
    if rsi is not None:
        tag = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
        notes.append(f"RSI(14) is {rsi} — {tag} momentum.")
    rng = hi20 - lo20
    pos = (price - lo20) / rng * 100 if rng > 0 else 50
    notes.append(f"Trading at {pos:.0f}% of its 20-day range (₹{lo20:,.0f}–₹{hi20:,.0f}).")

    # Signal = trend filtered by momentum extremes
    if trend == "Uptrend":
        signal = "Bullish" if (rsi is None or rsi < 75) else "Bullish (overbought)"
    elif trend == "Downtrend":
        signal = "Bearish" if (rsi is None or rsi > 25) else "Bearish (oversold)"
    else:
        signal = "Neutral"

    return {"symbol": symbol, "ok": True, "trend": trend, "signal": signal,
            "price": round(price, 2),
            "ema20": round(ema20, 2) if ema20 else None,
            "ema50": round(ema50, 2) if ema50 else None,
            "rsi": rsi, "high20": round(hi20, 2), "low20": round(lo20, 2),
            "range_pos": round(pos, 0), "notes": notes}


def demo_analysis(symbol: str) -> dict:
    """Synthetic analysis so the detail view works in demo mode."""
    import random
    rng = random.Random(sum(ord(c) for c in symbol))
    p = rng.uniform(150, 3000)
    candles = []
    for i in range(80):
        p *= 1 + rng.uniform(-0.02, 0.023)
        o = p * (1 + rng.uniform(-0.006, 0.006))
        candles.append([i, o, max(o, p) * 1.01, min(o, p) * 0.99, p, rng.randint(1e5, 5e6)])
    return analyze(symbol, candles)


def demo_rows(sort: str = "movers", limit: int = 60) -> list[dict]:
    """Synthetic screener rows so the UI works without a Fyers login (demo mode)."""
    import random
    rng = random.Random(42)
    rows = []
    for t in FO_STOCKS:
        chg = round(rng.uniform(-6, 6), 2)
        pos = rng.uniform(0, 1)
        signal, why = _quote_signal(chg, pos, chg > 0)
        rows.append({"symbol": t, "ltp": round(rng.uniform(100, 4000), 2),
                     "chg_pct": chg, "gap_pct": round(rng.uniform(-2.5, 2.5), 2),
                     "range_pct": round(rng.uniform(0.6, 7), 2),
                     "volume": rng.randint(100_000, 25_000_000),
                     "signal": signal, "why": why})
    rows.sort(key=_SORTS.get(sort, _SORTS["movers"]))
    return rows[:limit]
