"""Pull today's executed trades from Fyers into the journal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import store, trades, dhan

IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def fy_start() -> str:
    """Start of the current Indian financial year (1 April)."""
    now = datetime.now(IST)
    year = now.year if now.month >= 4 else now.year - 1
    return f"{year}-04-01"


def _split_dt(val) -> tuple[str, str]:
    """Fyers trade time -> (YYYY-MM-DD, HH:MM:SS). Handles epoch or string."""
    today = _today()
    if val in (None, "", 0):
        return today, ""
    # epoch seconds?
    try:
        n = int(val)
        if n > 1_000_000_000:
            dt = datetime.fromtimestamp(n if n < 1_000_000_000_000 else n / 1000, IST)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        pass
    s = str(val)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except ValueError:
            continue
    # time only, or unknown -> assume today
    return today, s[-8:] if ":" in s else ""


def _normalize(t: dict) -> dict | None:
    sym = t.get("symbol") or t.get("tradingsymbol") or t.get("tradingSymbol")
    if not sym:
        return None
    side_raw = t.get("side", t.get("transactionType", t.get("transaction_type")))
    side = "BUY" if str(side_raw).strip().upper() in ("1", "B", "BUY") else "SELL"
    qty = t.get("tradedQty") or t.get("qty") or t.get("filledQty") or t.get("tradeQty") or 0
    price = (t.get("tradePrice") or t.get("tradedPrice") or t.get("price")
             or t.get("tradeValue", 0) and t.get("tradeValue") / (float(qty) or 1) or 0)
    dt = (t.get("orderDateTime") or t.get("tradeTime") or t.get("orderTime")
          or t.get("exchOrdTime") or t.get("tradeDateTime"))
    date, time = _split_dt(dt)
    return {"symbol": sym, "side": side, "qty": float(qty or 0),
            "price": float(price or 0), "date": date, "time": time}


def sync_today(source) -> dict:
    """Fetch today's fills from the active Fyers source and store round-trips."""
    if getattr(source, "name", "") != "fyers":
        return {"ok": False,
                "error": "Live sync needs the Fyers data source (DATA_SOURCE=fyers)."}

    raw = source.fetch_tradebook()
    fills = [f for f in (_normalize(t) for t in raw) if f]
    today = _today()
    today_fills = [f for f in fills if f["date"] == today] or fills

    closed = trades.pair_fifo(today_fills, "fyers")
    store.clear_source_date("fyers", today)
    store.upsert_trades([{k: t.get(k) for k in
        ("id", "date", "time", "symbol", "side", "qty", "price", "value", "pnl", "closed", "source")}
        for t in closed])
    store.rebuild_daily_from_trades()

    realized = round(sum(float(t["pnl"] or 0) for t in closed), 2)
    return {"ok": True, "fills": len(fills), "round_trips": len(closed),
            "realized_today": realized, "open_positions": trades.open_positions(today_fills)}


# ---------------------------------------------------------------- Dhan
def _normalize_dhan(t: dict) -> dict | None:
    sym = t.get("customSymbol") or t.get("tradingSymbol") or t.get("securityId")
    if not sym:
        return None
    side = "BUY" if str(t.get("transactionType", "")).upper().startswith("B") else "SELL"
    et = str(t.get("exchangeTime") or t.get("createTime") or t.get("updateTime") or "").strip()
    if et in ("", "NA"):
        date, time = "unknown", ""
    else:
        parts = et.replace("T", " ").split(" ")
        date = parts[0]
        time = parts[1][:8] if len(parts) > 1 else ""
    return {"symbol": str(sym), "side": side,
            "qty": float(t.get("tradedQuantity", 0) or 0),
            "price": float(t.get("tradedPrice", 0) or 0),
            "date": date, "time": time}


def sync_dhan(from_date: str, to_date: str) -> dict:
    """Pull dated trade history from Dhan, pair FIFO, store as the 'dhan' source."""
    raw = dhan.fetch_trade_history(from_date, to_date)
    fills = [f for f in (_normalize_dhan(t) for t in raw) if f]
    closed = trades.pair_fifo(fills, "dhan")

    # Safety: never wipe existing data when the fetch came back empty
    # (e.g. expired token or no trades in range).
    if not closed:
        return {"ok": True, "raw_trades": len(raw), "fills": len(fills),
                "round_trips": 0, "realized": 0.0, "date_range": None,
                "from": from_date, "to": to_date,
                "note": "No trades returned for this range — your existing data was kept. "
                        "If you expected trades, your Dhan token may have expired; reconnect."}

    # Clear only the synced date range so syncing one month doesn't wipe others.
    store.clear_source_range("dhan", from_date, to_date)
    store.upsert_trades([{k: t.get(k) for k in
        ("id", "date", "time", "symbol", "side", "qty", "price", "value", "pnl", "closed", "source")}
        for t in closed])
    store.rebuild_daily_from_trades()

    realized = round(sum(float(t["pnl"] or 0) for t in closed), 2)
    dates = sorted({f["date"] for f in fills if f["date"] != "unknown"})
    return {"ok": True, "raw_trades": len(raw), "fills": len(fills),
            "round_trips": len(closed), "realized": realized,
            "date_range": [dates[0], dates[-1]] if dates else None,
            "from": from_date, "to": to_date}
