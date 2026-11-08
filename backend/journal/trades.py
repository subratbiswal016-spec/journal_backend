"""Turn raw buy/sell fills into closed round-trip trades with realized P&L.

Fills (from Fyers tradebook or a CSV) are just executions. To get per-trade
profit/loss we pair them FIFO per symbol: a SELL closes earlier BUYs (long) and
a BUY closes earlier SELLs (short). Each closed match becomes one journal trade
with a realized `pnl`. Positions left open at the end carry no pnl yet.
"""
from __future__ import annotations

import hashlib
from typing import Optional

# A fill is a dict: {date, time, symbol, side ('BUY'/'SELL'), qty, price}


def _fid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def pair_fifo(fills: list[dict], source: str) -> list[dict]:
    """Return closed round-trip trades (with pnl) for a list of fills."""
    # Group by symbol, process each symbol in time order.
    by_symbol: dict[str, list[dict]] = {}
    for f in fills:
        by_symbol.setdefault(f.get("symbol", "?"), []).append(f)

    closed: list[dict] = []
    for symbol, flist in by_symbol.items():
        flist.sort(key=lambda f: (f.get("date", ""), f.get("time", "")))
        queue: list[dict] = []  # open lots: {qty(signed), price, date, time}
        seq = 0
        for f in flist:
            sgn = 1 if str(f.get("side", "")).upper().startswith("B") else -1
            remaining = abs(float(f.get("qty", 0) or 0))
            price = float(f.get("price", 0) or 0)
            # First, close opposite-direction open lots (FIFO).
            while remaining > 1e-9 and queue and (queue[0]["qty"] * sgn) < 0:
                lot = queue[0]
                m = min(remaining, abs(lot["qty"]))
                if lot["qty"] > 0:              # closing a long (entry BUY, exit SELL)
                    pnl = (price - lot["price"]) * m
                    side = "LONG"
                else:                           # closing a short (entry SELL, exit BUY)
                    pnl = (lot["price"] - price) * m
                    side = "SHORT"
                seq += 1
                closed.append({
                    "id": _fid(source, symbol, f.get("date"), f.get("time"), seq, lot["price"], price),
                    "date": f.get("date"),
                    "time": f.get("time"),
                    "symbol": symbol,
                    "side": side,
                    "qty": round(m, 4),
                    "price": round(price, 4),            # exit price
                    "value": round(price * m, 2),
                    "pnl": round(pnl, 2),
                    "closed": 1,
                    "source": source,
                    "entry_price": round(lot["price"], 4),  # extra (not stored in schema)
                })
                lot["qty"] -= m * (1 if lot["qty"] > 0 else -1)
                if abs(lot["qty"]) < 1e-9:
                    queue.pop(0)
                remaining -= m
            # Whatever remains opens a new lot.
            if remaining > 1e-9:
                queue.append({"qty": remaining * sgn, "price": price,
                              "date": f.get("date"), "time": f.get("time")})
    return closed


def open_positions(fills: list[dict]) -> list[dict]:
    """Net still-open quantity per symbol after FIFO (for info / warnings)."""
    net: dict[str, float] = {}
    for f in fills:
        sgn = 1 if str(f.get("side", "")).upper().startswith("B") else -1
        net[f.get("symbol", "?")] = net.get(f.get("symbol", "?"), 0) + sgn * abs(float(f.get("qty", 0) or 0))
    return [{"symbol": k, "qty": v} for k, v in net.items() if abs(v) > 1e-9]
