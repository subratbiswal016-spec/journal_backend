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

# Max names the background scanner cycles through for one universe. The scanner
# runs continuously (decoupled from the 15s UI refresh), so it can cover a big
# universe over a few minutes. Capped so a full pass (and daily budget) stays
# reasonable — bigger universes just mean each name refreshes a bit less often.
SCAN_LIMIT = 180

# --- index universes you can point the scanner at -----------------------
NIFTY50 = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "HINDUNILVR",
    "ITC", "LT", "KOTAKBANK", "AXISBANK", "BHARTIARTL", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND",
    "ONGC", "NTPC", "POWERGRID", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "ADANIENT",
    "ADANIPORTS", "COALINDIA", "GRASIM", "HINDALCO", "BRITANNIA", "CIPLA", "DRREDDY",
    "DIVISLAB", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO", "BAJAJFINSV", "INDUSINDBK",
    "M&M", "TECHM", "APOLLOHOSP", "BPCL", "HDFCLIFE", "SBILIFE", "TATACONSUM",
    "LTIM", "SHRIRAMFIN",
]
BANKNIFTY = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    "BANKBARODA", "PNB", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK",
]
SENSEX = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "HINDUNILVR",
    "ITC", "LT", "KOTAKBANK", "AXISBANK", "BHARTIARTL", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "NTPC",
    "POWERGRID", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "M&M", "TECHM", "BAJAJFINSV",
    "INDUSINDBK", "WIPRO",
]


def _dedup(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


# Nifty 100 ≈ Nifty 50 + the next most-liquid F&O names (approximate).
NIFTY100 = _dedup(NIFTY50 + FO_STOCKS)[:100]

# Liquid NSE cash stocks beyond the F&O list — mid/small-caps that often move
# and are tradeable intraday. Unknown tickers are simply skipped when quoted.
BROAD_EXTRA = [
    # banks / financials
    "YESBANK", "UNIONBANK", "INDIANB", "IOB", "UCOBANK", "CENTRALBK", "MAHABANK",
    "BANKINDIA", "KARURVYSYA", "CUB", "DCBBANK", "EQUITASBNK", "UJJIVANSFB",
    "POONAWALLA", "BAJAJHLDNG", "SBICARD", "CDSL", "BSE", "CAMS", "IEX", "360ONE",
    "KFINTECH", "ANGELONE", "IIFL", "MANAPPURAM",
    # pharma / healthcare
    "ZYDUSLIFE", "MANKIND", "JBCHEPHARM", "IPCALAB", "ABBOTINDIA", "FORTIS",
    "MAXHEALTH", "NH", "METROPOLIS", "LALPATHLAB", "SYNGENE", "GRANULES",
    "NATCOPHARM", "AJANTPHARM", "GLAND",
    # auto / ancillary
    "TIINDIA", "BOSCHLTD", "UNOMINDA", "ENDURANCE", "SUNDRMFAST",
    # IT
    "KPITTECH", "BIRLASOFT", "MASTEK", "ZENSARTECH",
    # consumer / FMCG
    "VBL", "GODREJIND", "EMAMILTD", "RADICO", "JYOTHYLAB", "PATANJALI",
    "GILLETTE", "PGHH", "HONASA",
    # realty
    "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "LODHA", "BRIGADE",
    "SOBHA", "NCC",
    # PSU / energy / infra
    "SJVN", "IREDA", "COCHINSHIP", "MAZDOCK", "BEML", "BDL", "RITES", "RAILTEL",
    "NBCC", "NLCINDIA", "GSPL", "HUDCO", "IRCON", "RVNL",
    # metals / chemicals
    "HINDZINC", "NATIONALUM", "RATNAMANI", "JINDALSAW", "WELCORP", "FINEORG",
    "VINATIORGA", "GNFC", "GSFC", "CHAMBLFERT", "TATACHEM", "LINDEINDIA", "SOLARINDS",
    # new-age / retail / misc
    "DELHIVERY", "MAPMYINDIA", "LATENTVIEW", "VMART", "ABFRL", "RAYMOND",
]
# Broad market (~230 liquid names): F&O universe + the extra cash stocks.
BROAD = _dedup(FO_STOCKS + BROAD_EXTRA)

INDEXES = {
    "nifty50":   {"label": "Nifty 50",     "symbols": NIFTY50},
    "banknifty": {"label": "Bank Nifty",   "symbols": BANKNIFTY},
    "sensex":    {"label": "Sensex",       "symbols": SENSEX},
    "nifty100":  {"label": "Nifty 100",    "symbols": NIFTY100},
    "fno":       {"label": "F&O Stocks",   "symbols": FO_STOCKS},
    "broad":     {"label": "All Stocks (broad)", "symbols": BROAD},
}
DEFAULT_INDEX = "nifty50"


def _exchange_symbols(exch: str) -> list[str]:
    from . import symbols as _syms
    items = _syms.load_master(allow_network=True)
    if exch == "NSE":
        return [it["f"] for it in items if "NSE" in it.get("e", "") and it.get("f")]
    return [it["b"] for it in items if "BSE" in it.get("e", "") and it.get("b")]


def index_list() -> list[dict]:
    """[{id,label,count}] for the index/universe picker."""
    out = [{"id": k, "label": v["label"], "count": len(v["symbols"])}
           for k, v in INDEXES.items()]
    out.append({"id": "nse", "label": "All NSE Stocks", "count": len(_exchange_symbols("NSE"))})
    out.append({"id": "bse", "label": "All BSE Stocks", "count": len(_exchange_symbols("BSE"))})
    return out


def scan_universe(index: str = DEFAULT_INDEX) -> list[str]:
    """Symbols to scan for the chosen index or full NSE/BSE universe."""
    if index == "nse":
        return _exchange_symbols("NSE")
    if index == "bse":
        return _exchange_symbols("BSE")
    syms = INDEXES.get(index, INDEXES[DEFAULT_INDEX])["symbols"]
    return syms[:SCAN_LIMIT]


def fyers_symbol(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    return t if ":" in t else f"NSE:{t}-EQ"


def _display_symbol(sym: str) -> str:
    s = (sym or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.rsplit("-", 1)[0] if "-" in s else s


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
    # distance from today's extremes (for rise/fall-from & near-high/low filters)
    from_high_pct = round((hi - ltp) / hi * 100, 2) if hi > 0 else None
    from_low_pct = round((ltp - lo) / lo * 100, 2) if lo > 0 else None
    sym = q.get("ticker") or ""
    return {
        "symbol": sym,
        "display": q.get("display") or _display_symbol(sym),
        "ltp": round(ltp, 2),
        "day_high": round(hi, 2), "day_low": round(lo, 2),
        "open": round(op, 2), "prev_close": round(prev, 2),
        "chg_pct": chg_pct,
        "gap_pct": gap_pct,
        "range_pct": range_pct,
        "from_high_pct": from_high_pct,
        "from_low_pct": from_low_pct,
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


# ======================================================================
#  Filters (ScanX-style). Each filter is a predicate over a metrics dict.
#  Filters marked hist=True need the daily history cache (52W levels,
#  pivots, average volume) — everything else works off the live quote.
# ======================================================================
NEAR_PCT = 0.3        # "near the day high/low" tolerance (%)
NEAR_52W_PCT = 2.5    # "near the 52-week high/low" tolerance (%)
GAP_PCT = 0.5         # gap up/down threshold (%)

# UI groups → the chips shown above the table (Bullish/Bearish live as quick pills).
FILTER_GROUPS = [
    {"id": "high", "label": "Near High", "icon": "🔺", "filters": [
        {"id": "near_day_high", "label": "Near Day High"},
        {"id": "new_week_high", "label": "New Weekly High", "hist": True},
        {"id": "new_month_high", "label": "New Monthly High", "hist": True},
        {"id": "near_52w_high", "label": "Near 52W High", "hist": True},
    ]},
    {"id": "low", "label": "Near Low", "icon": "🔻", "filters": [
        {"id": "near_day_low", "label": "Near Day Low"},
        {"id": "new_week_low", "label": "New Weekly Low", "hist": True},
        {"id": "new_month_low", "label": "New Monthly Low", "hist": True},
        {"id": "near_52w_low", "label": "Near 52W Low", "hist": True},
    ]},
    {"id": "risefall", "label": "Rise & Fall", "icon": "⇅", "filters": [
        {"id": "fall1", "label": "1% Fall from High"},
        {"id": "fall2_5", "label": "2.5% Fall from High"},
        {"id": "fall5", "label": "5% Fall from High"},
        {"id": "rise1", "label": "1% Rise from Low"},
        {"id": "rise2_5", "label": "2.5% Rise from Low"},
        {"id": "rise5", "label": "5% Rise from Low"},
    ]},
    {"id": "volume", "label": "Volume", "icon": "📊", "filters": [
        {"id": "high_volume", "label": "High Volume"},
        {"id": "unusual_volume", "label": "Unusual Volume", "hist": True},
    ]},
    {"id": "pivot", "label": "Pivots & Gap", "icon": "🎯", "filters": [
        {"id": "r1", "label": "R1 Breakout", "hist": True},
        {"id": "r2", "label": "R2 Breakout", "hist": True},
        {"id": "r3", "label": "R3 Breakout", "hist": True},
        {"id": "gap_up", "label": "Gap Up"},
        {"id": "gap_down", "label": "Gap Down"},
    ]},
]


def _ge(v, t):  # safe ">=" that tolerates None
    return v is not None and v >= t


# predicate(metrics) -> bool
_PREDICATES = {
    "bullish":        lambda m: m["chg_pct"] > 0.3,
    "bearish":        lambda m: m["chg_pct"] < -0.3,
    "near_day_high":  lambda m: m["from_high_pct"] is not None and m["from_high_pct"] <= NEAR_PCT,
    "near_day_low":   lambda m: m["from_low_pct"] is not None and m["from_low_pct"] <= NEAR_PCT,
    "fall1":          lambda m: _ge(m["from_high_pct"], 1.0),
    "fall2_5":        lambda m: _ge(m["from_high_pct"], 2.5),
    "fall5":          lambda m: _ge(m["from_high_pct"], 5.0),
    "rise1":          lambda m: _ge(m["from_low_pct"], 1.0),
    "rise2_5":        lambda m: _ge(m["from_low_pct"], 2.5),
    "rise5":          lambda m: _ge(m["from_low_pct"], 5.0),
    "gap_up":         lambda m: m["gap_pct"] >= GAP_PCT,
    "gap_down":       lambda m: m["gap_pct"] <= -GAP_PCT,
    "high_volume":    lambda m: bool(m.get("high_volume")),
    # history-backed
    "near_52w_high":  lambda m: _ge(m.get("high_52w"), 0) and m["ltp"] >= m["high_52w"] * (1 - NEAR_52W_PCT / 100),
    "near_52w_low":   lambda m: _ge(m.get("low_52w"), 0) and m["ltp"] <= m["low_52w"] * (1 + NEAR_52W_PCT / 100),
    "new_week_high":  lambda m: _ge(m.get("week_high"), 0) and m["ltp"] >= m["week_high"],
    "new_month_high": lambda m: _ge(m.get("month_high"), 0) and m["ltp"] >= m["month_high"],
    "new_week_low":   lambda m: _ge(m.get("week_low"), 0) and m["ltp"] <= m["week_low"],
    "new_month_low":  lambda m: _ge(m.get("month_low"), 0) and m["ltp"] <= m["month_low"],
    "unusual_volume": lambda m: _ge(m.get("avg_vol20"), 1) and m["volume"] >= m["avg_vol20"],
    "r1":             lambda m: _ge(m.get("r1"), 0) and m["ltp"] > m["r1"],
    "r2":             lambda m: _ge(m.get("r2"), 0) and m["ltp"] > m["r2"],
    "r3":             lambda m: _ge(m.get("r3"), 0) and m["ltp"] > m["r3"],
}
_LABELS = {"bullish": "Bullish", "bearish": "Bearish",
           **{f["id"]: f["label"] for g in FILTER_GROUPS for f in g["filters"]}}
HIST_FILTERS = {f["id"] for g in FILTER_GROUPS for f in g["filters"] if f.get("hist")}


def levels_from_history(candles: list[list]) -> dict | None:
    """Static-for-the-day levels from daily candles [ts,o,h,l,c,v]:
    52-week & period highs/lows, classic pivots (prev day), 20-day avg volume."""
    rows = [c for c in (candles or []) if c and len(c) >= 6]
    if len(rows) < 20:
        return None
    highs = [float(c[2]) for c in rows]
    lows = [float(c[3]) for c in rows]
    vols = [float(c[5] or 0) for c in rows]
    # period highs/lows EXCLUDING today's forming candle (rows[-1])
    done_h, done_l, done_v = highs[:-1], lows[:-1], vols[:-1]
    def hi(n): return max(done_h[-n:]) if done_h else None
    def lo(n): return min(done_l[-n:]) if done_l else None
    # classic pivots from the last completed day
    ph, pl, pc = highs[-2], lows[-2], float(rows[-2][4])
    piv = (ph + pl + pc) / 3.0
    r1 = 2 * piv - pl; s1 = 2 * piv - ph
    r2 = piv + (ph - pl); s2 = piv - (ph - pl)
    r3 = ph + 2 * (piv - pl); s3 = pl - 2 * (ph - piv)
    avg20 = sum(done_v[-20:]) / min(20, len(done_v)) if done_v else None
    return {
        "high_52w": round(max(done_h[-252:]), 2) if done_h else None,
        "low_52w": round(min(done_l[-252:]), 2) if done_l else None,
        "week_high": round(hi(5), 2) if done_h else None,
        "week_low": round(lo(5), 2) if done_l else None,
        "month_high": round(hi(21), 2) if done_h else None,
        "month_low": round(lo(21), 2) if done_l else None,
        "pivot": round(piv, 2),
        "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
        "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
        "avg_vol20": round(avg20) if avg20 else None,
    }


def screen(quotes: list[dict], hist_map: dict[str, dict] | None = None,
           filters: list[str] | None = None, sort: str = "volume",
           limit: int = 80) -> list[dict]:
    """Compute metrics, merge daily levels, apply filters (OR with matched
    tags), and sort. No active filters → every stock, ranked."""
    hist_map = hist_map or {}
    rows = []
    for q in quotes:
        m = _metrics(q)
        if not m:
            continue
        lv = hist_map.get(m["symbol"])
        if lv:
            m.update(lv)
        rows.append(m)
    # "High Volume" = top 20% of the scanned set by volume
    if rows:
        vols = sorted((m["volume"] for m in rows), reverse=True)
        thr = vols[max(0, int(len(vols) * 0.2) - 1)] if vols else 0
        for m in rows:
            m["high_volume"] = m["volume"] >= thr and m["volume"] > 0

    active = [f for f in (filters or []) if f in _PREDICATES]
    if active:
        out = []
        for m in rows:
            matched = [_LABELS[f] for f in active if _safe_pred(f, m)]
            if matched:
                m["matched"] = matched
                out.append(m)
        rows = out
    else:
        for m in rows:
            m["matched"] = []

    rows.sort(key=_SORTS.get(sort, _SORTS["volume"]))
    return rows[:limit]


def _safe_pred(fid: str, m: dict) -> bool:
    try:
        return bool(_PREDICATES[fid](m))
    except Exception:  # noqa: BLE001 - a missing field just means "no match"
        return False


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


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _daily_stats(daily: list[list]) -> dict:
    """Swing/positional technicals from daily candles: 52-week range,
    50/200 SMAs, ATR(14) volatility, trailing returns, relative volume."""
    rows = [c for c in (daily or []) if c and len(c) >= 6]
    if len(rows) < 20:
        return {}
    closes = [float(c[4]) for c in rows]
    highs = [float(c[2]) for c in rows]
    lows = [float(c[3]) for c in rows]
    vols = [float(c[5] or 0) for c in rows]
    price = closes[-1]
    win = rows[-252:] if len(rows) >= 252 else rows
    hi52 = max(float(c[2]) for c in win)
    lo52 = min(float(c[3]) for c in win)
    out: dict = {
        "week52_high": round(hi52, 2),
        "week52_low": round(lo52, 2),
        "from_52w_high": round((price - hi52) / hi52 * 100, 1) if hi52 else None,
        "from_52w_low": round((price - lo52) / lo52 * 100, 1) if lo52 else None,
        "sma50": round(_sma(closes, 50), 2) if _sma(closes, 50) else None,
        "sma200": round(_sma(closes, 200), 2) if _sma(closes, 200) else None,
    }
    # ATR(14) on daily — volatility as % of price
    trs = []
    for i in range(1, len(rows)):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    if len(trs) >= 14:
        atr = sum(trs[-14:]) / 14
        out["atr14"] = round(atr, 2)
        out["atr_pct"] = round(atr / price * 100, 2) if price else None
    # trailing returns (sessions ≈ periods)
    def _ret(n):
        if len(closes) > n and closes[-1 - n]:
            return round((price - closes[-1 - n]) / closes[-1 - n] * 100, 1)
        return None
    out["ret_1w"], out["ret_1m"] = _ret(5), _ret(21)
    out["ret_3m"], out["ret_6m"], out["ret_1y"] = _ret(63), _ret(126), _ret(252)
    # relative volume: today vs 20-day average
    if len(vols) >= 21:
        avg20 = sum(vols[-21:-1]) / 20
        out["avg_vol20"] = int(avg20)
        out["rel_vol"] = round(vols[-1] / avg20, 2) if avg20 else None
    return out


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


def _ema_series(values: list[float], period: int) -> list[float | None]:
    """Running EMA, one value per input point (None until seeded)."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out: list[float | None] = []
    e: float | None = None
    for i, v in enumerate(values):
        e = v if e is None else v * k + e * (1 - k)
        out.append(round(e, 2) if i >= period - 1 else None)
    return out


def _vwap_series(candles: list[list]) -> list[float | None]:
    """Running (cumulative) VWAP, one value per candle."""
    out: list[float | None] = []
    num = den = 0.0
    for c in candles:
        if len(c) < 6:
            out.append(round(num / den, 2) if den else None)
            continue
        tp = (float(c[2]) + float(c[3]) + float(c[4])) / 3.0
        v = float(c[5] or 0)
        num += tp * v
        den += v
        out.append(round(num / den, 2) if den else None)
    return out


def _ist_label(ts, intraday: bool) -> str:
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    dt = datetime.fromtimestamp(int(ts), ist)
    return dt.strftime("%H:%M") if intraday else dt.strftime("%d %b")


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

    # ---- positional / swing technicals (52w range, SMAs, ATR, returns) ----
    dstats = _daily_stats(daily)
    if dstats:
        out["daily_stats"] = dstats
        price_d = dcloses[-1]
        s50, s200 = dstats.get("sma50"), dstats.get("sma200")
        if s50 and s200:
            cross = "above" if s50 > s200 else "below"
            notes.append(f"50-DMA is {cross} the 200-DMA"
                         + (" — long-term uptrend (golden-cross side)." if s50 > s200
                            else " — long-term downtrend (death-cross side)."))
        if dstats.get("from_52w_high") is not None:
            fh = dstats["from_52w_high"]
            if fh >= -3:
                notes.append(f"Trading near its 52-week high ({fh:+.1f}% from it) — strong relative strength.")
            elif fh <= -40:
                notes.append(f"Deep below its 52-week high ({fh:+.1f}%) — weak / beaten-down.")
        if dstats.get("atr_pct") is not None:
            notes.append(f"Daily ATR is {dstats['atr_pct']}% of price — "
                         + ("high volatility (bigger swings)." if dstats['atr_pct'] >= 3
                            else "moderate volatility." if dstats['atr_pct'] >= 1.5
                            else "low volatility (tight ranges)."))
        if dstats.get("rel_vol") is not None and dstats["rel_vol"] >= 1.5:
            notes.append(f"Today's volume is {dstats['rel_vol']}× the 20-day average — unusual activity.")

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
        # chart: today's 5-min price with a running VWAP overlay
        out["chart"] = {
            "kind": "intraday",
            "title": "Today · 5-min",
            "labels": [_ist_label(c[0], True) for c in today],
            "price": [round(float(c[4]), 2) for c in today],
            "vwap": _vwap_series(today),
            "prev_close": round(prev_close, 2) if prev_close else None,
        }
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

    # daily chart (last ~60 sessions) when no intraday series is available
    if "chart" not in out and len(daily) >= 10:
        tail = daily[-60:]
        tcloses = [float(c[4]) for c in tail]
        out["chart"] = {
            "kind": "daily",
            "title": "Last 60 sessions · close",
            "labels": [_ist_label(c[0], False) if str(c[0]).isdigit() or isinstance(c[0], (int, float)) else str(i)
                       for i, c in enumerate(tail)],
            "price": [round(v, 2) for v in tcloses],
            "ema20": _ema_series(tcloses, 20),
        }

    out["setup"] = _trade_setup(out)
    return out


def _trade_setup(o: dict) -> dict:
    """Mechanical trade setup from the computed indicators — bias, a lower-risk
    entry zone, the breakout trigger and the invalidation/stop. NOT advice: it is
    a rules-based read of levels, not a personalised buy/sell call."""
    sig = (o.get("signal") or o.get("daily_trend") or "Neutral")
    sig = sig.replace("Uptrend", "Bullish").replace("Downtrend", "Bearish").replace("Sideways", "Neutral")
    price = o.get("price")
    vwap, orh, orl = o.get("vwap"), o.get("or_high"), o.get("or_low")
    dlow, dhigh = o.get("day_low"), o.get("day_high")
    ds = o.get("daily_stats") or {}
    from_hi = ds.get("from_52w_high")
    bull = "bull" in sig.lower()
    bear = "bear" in sig.lower()

    def rs(v):
        return f"₹{v:,.2f}" if isinstance(v, (int, float)) else None

    s = {"bias": sig}
    support = vwap or orl or dlow
    trigger = orh or dhigh
    if bull:
        s["entry_zone"] = (f"Pullback toward {rs(support)} (VWAP / intraday support) that holds"
                           if support else "On a pullback that holds support")
        s["trigger"] = (f"5-min close above {rs(trigger)}" if trigger else "A fresh intraday high")
        s["stop"] = (f"Below {rs(orl or dlow)}" if (orl or dlow) else "Below intraday support")
        if from_hi is not None and from_hi >= -2:
            s["timing"] = ("Momentum is strong (near the 52-week high). Mechanically, the lower-risk "
                           "entry is a shallow pullback that holds above VWAP — chasing an extended "
                           "breakout carries more risk.")
        else:
            s["timing"] = ("Setup is constructive. A pullback into the support zone that holds, or a "
                           "confirmed move above the trigger, is the mechanically lower-risk entry — "
                           "rather than buying mid-move.")
    elif bear:
        s["entry_zone"] = "No long setup — trend is down."
        resist = vwap or orh or dhigh
        s["trigger"] = (f"Only on a reclaim of {rs(resist)}" if resist else "Only once it stabilises")
        s["stop"] = None
        s["timing"] = ("Not a low-risk time to buy: price is below VWAP in a downtrend. Mechanically, "
                       "waiting for stabilisation above the trigger avoids catching a falling move.")
    else:
        s["entry_zone"] = (f"Range-bound — better near support {rs(orl or dlow)}" if (orl or dlow)
                           else "Range-bound — better near support")
        s["trigger"] = (f"A 5-min close above {rs(orh)} tilts it bullish" if orh else "A break of the range")
        s["stop"] = (f"Below {rs(orl or dlow)}" if (orl or dlow) else "Below the range low")
        s["timing"] = ("No clear edge right now (sideways). Buying mid-range has poor risk-reward; a break "
                       "of the trigger or a tested support bounce is more favourable.")
    return s


def demo_analysis(symbol: str) -> dict:
    """Synthetic analysis so the detail view works in demo mode."""
    import random
    from datetime import datetime, timezone, timedelta
    rng = random.Random(sum(ord(c) for c in symbol))
    p = rng.uniform(150, 3000)
    daily = []
    for i in range(260):
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


def demo_screen(index: str = DEFAULT_INDEX, filters: list[str] | None = None,
                sort: str = "volume", limit: int = 80) -> list[dict]:
    """Synthetic screener (quotes + daily levels) so the whole filter UI works
    without a Fyers login — routed through the real screen() for parity."""
    import random
    quotes, hist = [], {}
    uni = scan_universe(index)
    use_hist = len(uni) <= 400
    for t in uni:
        disp = _display_symbol(t)
        rng = random.Random(sum(ord(c) for c in t))
        prev = round(rng.uniform(100, 4000), 2)
        chg = rng.uniform(-6, 6)
        ltp = round(prev * (1 + chg / 100), 2)
        op = round(prev * (1 + rng.uniform(-2.5, 2.5) / 100), 2)
        hi = round(max(ltp, op) * (1 + rng.uniform(0, 3) / 100), 2)
        lo = round(min(ltp, op) * (1 - rng.uniform(0, 3) / 100), 2)
        quotes.append({"ticker": t, "display": disp, "ltp": ltp, "open": op, "high": hi, "low": lo,
                       "prev_close": prev, "volume": rng.randint(100_000, 25_000_000)})
        if use_hist:
            hist[t] = {
                "high_52w": round(hi * (1 + rng.uniform(0, 0.25)), 2),
                "low_52w": round(lo * (1 - rng.uniform(0, 0.25)), 2),
                "week_high": round(hi * (1 + rng.uniform(-0.02, 0.03)), 2),
                "week_low": round(lo * (1 + rng.uniform(-0.03, 0.02)), 2),
                "month_high": round(hi * (1 + rng.uniform(0, 0.06)), 2),
                "month_low": round(lo * (1 - rng.uniform(0, 0.06)), 2),
                "pivot": round((hi + lo + prev) / 3, 2),
                "r1": round(prev * (1 + rng.uniform(-0.5, 1.5) / 100), 2),
                "r2": round(prev * (1 + rng.uniform(1, 3) / 100), 2),
                "r3": round(prev * (1 + rng.uniform(2.5, 4.5) / 100), 2),
                "avg_vol20": rng.randint(100_000, 20_000_000),
            }
    return screen(quotes, hist, filters, sort, limit)
