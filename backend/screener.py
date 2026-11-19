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


def _vwap(intraday: list[list]) -> float | None:
    """Volume-weighted average price from intraday candles ([ts,o,h,l,c,v])."""
    num = den = 0.0
    for c in intraday:
        if len(c) < 6:
            continue
        tp = (float(c[2]) + float(c[3]) + float(c[4])) / 3.0   # typical price
        v = float(c[5] or 0)
        num += tp * v
        den += v
    return round(num / den, 2) if den else None


def _today_candles(intraday: list[list]) -> list[list]:
    """Keep only the most recent trading day's candles (by date of the epoch ts)."""
    if not intraday:
        return []
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    last_day = datetime.fromtimestamp(int(intraday[-1][0]), ist).date()
    return [c for c in intraday
            if datetime.fromtimestamp(int(c[0]), ist).date() == last_day]


def analyze(symbol: str, daily: list[list], intraday: list[list] | None = None) -> dict:
    """Day-trading read: intraday (VWAP, 5-min trend/RSI, opening range, levels)
    plus a daily swing context. All mechanical — data, not advice."""
    daily = [c for c in (daily or []) if c and len(c) >= 5]
    dcloses = [float(c[4]) for c in daily]
    if len(dcloses) < 5 and not intraday:
        return {"symbol": symbol, "ok": False,
                "note": "Not enough data to analyse this stock right now."}

    out = {"symbol": symbol, "ok": True, "notes": []}
    notes = out["notes"]

    # ---- daily swing context ----
    if len(dcloses) >= 20:
        price_d = dcloses[-1]
        ema20, ema50 = _ema(dcloses, 20), _ema(dcloses, 50)
        d_rsi = _rsi(dcloses)
        above20 = ema20 and price_d > ema20
        above50 = ema50 and price_d > ema50
        if above20 and above50 and (not ema50 or (ema20 or 0) > (ema50 or 0)):
            dtrend = "Uptrend"
        elif (not above20) and (not above50) and (not ema50 or (ema20 or 0) < (ema50 or 0)):
            dtrend = "Downtrend"
        else:
            dtrend = "Sideways"
        out.update({"daily_trend": dtrend, "daily_rsi": d_rsi,
                    "ema20": round(ema20, 2) if ema20 else None,
                    "ema50": round(ema50, 2) if ema50 else None})
        notes.append(f"Daily trend is {dtrend.lower()} (price vs 20/50-EMA)"
                     + (f", RSI {d_rsi}" if d_rsi is not None else "") + ".")

    # ---- intraday (5-min) day-trading read ----
    today = _today_candles(intraday or [])
    if len(today) >= 3:
        icloses = [float(c[4]) for c in today]
        price = icloses[-1]
        d_open = float(today[0][1])
        d_high = max(float(c[2]) for c in today)
        d_low = min(float(c[3]) for c in today)
        vwap = _vwap(today)
        iema9, iema20 = _ema(icloses, 9), _ema(icloses, 20)
        i_rsi = _rsi(icloses)
        # opening range = first 15 min (three 5-min candles)
        orb = today[:3]
        or_high = max(float(c[2]) for c in orb)
        or_low = min(float(c[3]) for c in orb)
        prev_close = dcloses[-2] if len(dcloses) >= 2 else d_open
        chg_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0

        # intraday signal score
        score = 0
        if vwap: score += 1 if price > vwap else -1
        if iema9: score += 1 if price > iema9 else -1
        if iema9 and iema20: score += 1 if iema9 > iema20 else -1
        if i_rsi is not None:
            score += 1 if i_rsi > 58 else -1 if i_rsi < 42 else 0
        signal = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Neutral"

        out.update({
            "signal": signal, "trend": ("Uptrend" if score >= 2 else "Downtrend" if score <= -2 else "Sideways"),
            "price": round(price, 2), "chg_pct": chg_pct,
            "vwap": vwap, "above_vwap": bool(vwap and price > vwap),
            "day_open": round(d_open, 2), "day_high": round(d_high, 2), "day_low": round(d_low, 2),
            "or_high": round(or_high, 2), "or_low": round(or_low, 2),
            "prev_close": round(prev_close, 2),
            "i_ema9": round(iema9, 2) if iema9 else None,
            "i_ema20": round(iema20, 2) if iema20 else None,
            "i_rsi": i_rsi, "intraday": True,
        })
        if vwap:
            notes.append(f"Price ₹{price:,.2f} is {'ABOVE' if price > vwap else 'BELOW'} VWAP (₹{vwap:,.2f}) "
                         + ("— intraday buyers in control." if price > vwap else "— intraday sellers in control."))
        notes.append(f"5-min trend: price {'above' if iema9 and price > iema9 else 'below'} the 9-EMA"
                     + (f", 9-EMA {'>' if iema9 and iema20 and iema9 > iema20 else '<'} 20-EMA" if iema20 else "") + ".")
        if i_rsi is not None:
            tag = "strong" if i_rsi >= 70 else "weak" if i_rsi <= 30 else "balanced"
            notes.append(f"5-min RSI(14) is {i_rsi} — {tag} intraday momentum.")
        notes.append(f"Opening-range (first 15 min): ₹{or_low:,.2f}–₹{or_high:,.2f}. "
                     + (f"A 5-min close above ₹{or_high:,.2f} / below ₹{or_low:,.2f} is the breakout trigger."))
    else:
        out["intraday"] = False
        notes.append("No intraday (5-min) data available yet — showing daily context only.")
        out.setdefault("signal", out.get("daily_trend", "Neutral").replace("Uptrend", "Bullish").replace("Downtrend", "Bearish").replace("Sideways", "Neutral"))
        out.setdefault("trend", out.get("daily_trend", "Sideways"))

    return out


def demo_analysis(symbol: str) -> dict:
    """Synthetic analysis so the detail view works in demo mode."""
    import random
    from datetime import datetime, timezone, timedelta
    rng = random.Random(sum(ord(c) for c in symbol))
    p = rng.uniform(150, 3000)
    daily = []
    for i in range(80):
        p *= 1 + rng.uniform(-0.02, 0.023)
        o = p * (1 + rng.uniform(-0.006, 0.006))
        daily.append([i, o, max(o, p) * 1.01, min(o, p) * 0.99, p, rng.randint(1e5, 5e6)])
    # today's 5-min candles
    ist = timezone(timedelta(hours=5, minutes=30))
    base = int(datetime.now(ist).replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    intr = []
    ip = p
    for i in range(60):
        ip *= 1 + rng.uniform(-0.004, 0.0045)
        o = ip * (1 + rng.uniform(-0.002, 0.002))
        intr.append([base + i * 300, o, max(o, ip) * 1.003, min(o, ip) * 0.997, ip, rng.randint(1e4, 3e5)])
    return analyze(symbol, daily, intr)


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
