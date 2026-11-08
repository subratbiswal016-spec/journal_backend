"""Import past trades from a Fyers CSV export (trade book or P&L statement).

Fyers' exported columns vary, so we detect them by header name rather than
position. Two shapes are handled:
  * Fill-level (has side + qty + price + date)  -> paired FIFO into round-trips.
  * P&L-level  (has a realized P&L column)       -> each row is a closed trade.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from . import store, trades

# header keyword -> field
_KEYS = {
    "symbol": ["tradingsymbol", "symbol", "scripname", "scrip", "instrument", "contract", "name"],
    "side":   ["buysell", "transactiontype", "side", "ordertype", "type", "bs"],
    "qty":    ["tradedqty", "filledqty", "tradeqty", "quantity", "qty"],
    "price":  ["tradeprice", "tradedprice", "avgprice", "price", "rate"],
    "date":   ["tradedate", "orderdate", "exitdate", "date"],
    "time":   ["tradetime", "ordertime", "exittime", "time"],
    "pnl":    ["realizedpnl", "realisedpnl", "netpnl", "pnl", "pl", "profit", "realised", "realized"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_field(header_cell: str):
    n = _norm(header_cell)
    for field, kws in _KEYS.items():
        if any(n == kw or kw in n for kw in kws):
            return field
    return None


def _find_header(rows: list[list[str]]):
    """Return (index, {field: col_index}) for the best header row."""
    best = (-1, {})
    for i, row in enumerate(rows[:30]):
        mapping = {}
        for j, cell in enumerate(row):
            f = _match_field(cell)
            if f and f not in mapping:
                mapping[f] = j
        if len(mapping) > len(best[1]):
            best = (i, mapping)
    return best


def _norm_date(s: str) -> str:
    s = (s or "").strip().split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s  # leave as-is if unknown


def _num(s) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(s)))
    except (ValueError, TypeError):
        return 0.0


def _side(s) -> str:
    t = str(s).strip().upper()
    if t in ("-1", "S", "SELL", "SE"):
        return "SELL"
    if t in ("1", "B", "BUY", "BU"):
        return "BUY"
    return "BUY" if t.startswith("B") else "SELL"


def import_csv(content: str) -> dict:
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return {"ok": False, "error": "Empty file."}
    hidx, cols = _find_header(rows)
    if hidx < 0 or "symbol" not in cols:
        return {"ok": False, "error": "Could not recognise the columns. "
                "Expected a Fyers trade book or P&L export with a symbol column.",
                "found_columns": rows[0] if rows else []}

    data = rows[hidx + 1:]

    def get(row, field):
        j = cols.get(field)
        return row[j] if (j is not None and j < len(row)) else ""

    has_fills = "side" in cols and "price" in cols and "qty" in cols
    has_pnl = "pnl" in cols

    store.clear_source("csv")
    created: list[dict] = []

    if has_fills:
        fills = []
        for row in data:
            if not any(c.strip() for c in row):
                continue
            sym = get(row, "symbol").strip()
            if not sym:
                continue
            fills.append({
                "symbol": sym,
                "side": _side(get(row, "side")),
                "qty": _num(get(row, "qty")),
                "price": _num(get(row, "price")),
                "date": _norm_date(get(row, "date")) or "unknown",
                "time": get(row, "time").strip(),
            })
        created = trades.pair_fifo(fills, "csv")
        shape = "fills"
    elif has_pnl:
        for k, row in enumerate(data):
            if not any(c.strip() for c in row):
                continue
            sym = get(row, "symbol").strip()
            if not sym:
                continue
            d = _norm_date(get(row, "date")) or "unknown"
            created.append({
                "id": trades._fid("csv", sym, d, k, get(row, "pnl")),
                "date": d, "time": get(row, "time").strip(), "symbol": sym,
                "side": "LONG", "qty": _num(get(row, "qty")),
                "price": _num(get(row, "price")), "value": 0.0,
                "pnl": round(_num(get(row, "pnl")), 2), "closed": 1, "source": "csv",
            })
        shape = "pnl"
    else:
        return {"ok": False, "error": "Found a symbol column but no side/price or P&L columns."}

    store.upsert_trades([{k: t.get(k) for k in
        ("id", "date", "time", "symbol", "side", "qty", "price", "value", "pnl", "closed", "source")}
        for t in created])
    store.rebuild_daily_from_trades()

    total_pnl = round(sum(float(t["pnl"] or 0) for t in created), 2)
    dates = sorted({t["date"] for t in created if t["date"] != "unknown"})
    no_dates = not dates
    msg = None
    if no_dates:
        msg = ("Imported, but this file has no trade dates (it's a scrip-wise P&L "
               "summary). Totals and win-rate work, but daily/weekly/monthly need a "
               "Trade Book export (with dates).")
    return {
        "ok": True, "shape": shape, "trades_created": len(created),
        "total_pnl": total_pnl,
        "date_range": [dates[0], dates[-1]] if dates else None,
        "no_dates": no_dates, "note": msg,
        "columns_detected": list(cols.keys()),
    }
