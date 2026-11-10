"""Targets (weekly/monthly goals + suggested plan) and performance analytics."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone

from . import store

# Trade symbols look like "NIFTY 19 OCT 23600 CALL": underlying, expiry day,
# expiry month, strike, option type. We parse the underlying (for per-instrument
# breakdowns) and the expiry day/month (to flag expiry-day trades).
_SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{1,2})\s+([A-Z]{3})\b")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _parse_symbol(sym: str | None) -> tuple[str | None, int | None, int | None]:
    if not sym:
        return (None, None, None)
    s = sym.strip().upper()
    m = _SYM_RE.match(s)
    if not m:
        tok = s.split()
        return (tok[0] if tok else None, None, None)
    return (m.group(1), int(m.group(2)), _MONTHS.get(m.group(3)))


def _underlying(sym: str | None) -> str:
    return _parse_symbol(sym)[0] or "OTHER"


def _is_expiry_day(sym: str | None, date_str: str) -> bool | None:
    """True/False if we can tell, else None. A trade is on its expiry day when the
    expiry encoded in the symbol (day+month) matches the trade's date."""
    _, dd, mon = _parse_symbol(sym)
    if not dd or not mon:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return d.day == dd and d.month == mon

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_TARGETS = {"weekly": 10000, "monthly": 40000}


def _today() -> date:
    return datetime.now(IST).date()


# ------------------------------------------------------------------ targets
def get_targets() -> dict:
    saved = store.get_setting("targets", {}) or {}
    return {**DEFAULT_TARGETS, **{k: saved[k] for k in ("weekly", "monthly") if k in saved}}


def set_targets(new: dict) -> dict:
    cur = get_targets()
    for k in ("weekly", "monthly"):
        if k in new:
            cur[k] = float(new[k])
    store.set_setting("targets", cur)
    return cur


# ------------------------------------------------------------------ capital
def get_capital() -> float:
    """The user's opening (Day 1) trading capital, 0 if not set yet."""
    try:
        return max(0.0, float(store.get_setting("opening_capital", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def set_capital(value) -> float:
    v = max(0.0, float(value or 0))
    store.set_setting("opening_capital", v)
    return v


# ---- per-month costs (brokerage + charges), to match the broker's Net P&L ----
def get_month_costs(month: str) -> dict:
    allc = store.get_setting("monthly_costs", {}) or {}
    c = allc.get(month, {}) or {}
    return {"brokerage": float(c.get("brokerage") or 0),
            "charges": float(c.get("charges") or 0)}


def set_month_costs(month: str, brokerage, charges) -> None:
    allc = store.get_setting("monthly_costs", {}) or {}
    allc[month] = {"brokerage": max(0.0, float(brokerage or 0)),
                   "charges": max(0.0, float(charges or 0))}
    store.set_setting("monthly_costs", allc)


# Approx NSE options charges for a discount broker. Rates shift with the Budget;
# this is a 2024-25 estimate so Net P&L shows automatically. Enter the broker's
# exact Brokerage/Charges for a month to override the estimate.
_DEFAULT_BROKERAGE_PER_ORDER = 20.0


def get_brokerage_rate() -> float:
    try:
        v = float(store.get_setting("brokerage_per_order", _DEFAULT_BROKERAGE_PER_ORDER))
        return max(0.0, v)
    except (TypeError, ValueError):
        return _DEFAULT_BROKERAGE_PER_ORDER


def set_brokerage_rate(value) -> float:
    r = max(0.0, float(value if value is not None else _DEFAULT_BROKERAGE_PER_ORDER))
    store.set_setting("brokerage_per_order", r)
    return r


def estimate_month_charges(month: str) -> dict:
    rate = get_brokerage_rate()
    y, mo = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, mo)[1]
    trades = [t for t in store.trades_between(f"{y}-{mo:02d}-01", f"{y}-{mo:02d}-{last:02d}")
              if t.get("closed")]
    brokerage = stt = txn = stamp = sebi = 0.0
    for t in trades:
        value = float(t.get("value") or 0)          # exit-leg premium value
        pnl = float(t.get("pnl") or 0)
        side = (t.get("side") or "").upper()
        if side in ("LONG", "BUY"):                 # bought then sold
            buy_v, sell_v = max(0.0, value - pnl), value
        else:                                       # SHORT: sold then bought
            buy_v, sell_v = value, max(0.0, value + pnl)
        turnover = buy_v + sell_v
        brokerage += 2 * rate                       # one buy + one sell order
        stt += 0.001 * sell_v                       # 0.1% on options sell premium
        txn += 0.0003503 * turnover                 # NSE options exchange txn charge
        sebi += 0.000001 * turnover                 # ₹10 / crore
        stamp += 0.00003 * buy_v                    # 0.003% on buy
    gst = 0.18 * (brokerage + txn + sebi)
    charges = round(stt + txn + sebi + stamp + gst, 2)
    return {"brokerage": round(brokerage, 2), "charges": charges, "estimated": True}


def month_costs_resolved(month: str) -> dict:
    """Exact costs the user saved for the month, else an auto-estimate."""
    allc = store.get_setting("monthly_costs", {}) or {}
    if month in allc:
        c = allc[month] or {}
        return {"brokerage": float(c.get("brokerage") or 0),
                "charges": float(c.get("charges") or 0), "estimated": False}
    return estimate_month_charges(month)


def capital_summary() -> dict:
    """Opening capital + all-time realized P&L -> closing capital and return %."""
    opening = get_capital()
    total_pnl = round(sum(float(r["realized"] or 0) for r in store.all_daily()
                          if r["date"] != "unknown"), 2)
    closing = round(opening + total_pnl, 2)
    return_pct = round(total_pnl / opening * 100, 2) if opening else 0.0
    return {"opening": opening, "total_pnl": total_pnl,
            "closing": closing, "return_pct": return_pct}


# ---- per-day journal notes (e.g. "Gap up"), keyed by date ----
def get_day_notes() -> dict:
    return store.get_setting("day_notes", {}) or {}


def set_day_note(day: str, note: str) -> None:
    notes = get_day_notes()
    note = (note or "").strip()
    if note:
        notes[day] = note
    else:
        notes.pop(day, None)
    store.set_setting("day_notes", notes)


def _months_with_data() -> list[str]:
    """Sorted YYYY-MM keys that have trades, always including the current month."""
    months = {r["date"][:7] for r in store.all_daily()
              if r["date"] != "unknown" and len(r["date"]) >= 7}
    months.add(_today().strftime("%Y-%m"))
    return sorted(months)


def capital_tracker(month: str | None = None) -> dict:
    """Month-scoped capital growth: opening capital + each day's synced P&L,
    running cumulative capital, and a per-day journal. `month` is 'YYYY-MM'."""
    today = _today()
    if month and len(month) == 7:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        year, mon = today.year, today.month
    last_day = calendar.monthrange(year, mon)[1]
    m_start, m_end = date(year, mon, 1), date(year, mon, last_day)
    opening = get_capital()

    rows = {r["date"]: float(r["realized"] or 0)
            for r in store.daily_between(m_start.isoformat(), m_end.isoformat())
            if r["date"] != "unknown"}
    notes = get_day_notes()

    days, cumulative = [], 0.0
    for i in range(1, last_day + 1):
        d = date(year, mon, i)
        ds = d.isoformat()
        traded = ds in rows
        if traded:
            cumulative += rows[ds]
        days.append({
            "date": ds, "n": i, "label": f"D{i}",
            "pnl": round(rows[ds], 2) if traded else None, "traded": traded,
            "weekend": d.weekday() >= 5, "future": d > today,
            "cumulative": round(cumulative, 2),
            "capital": round(opening + cumulative, 2),
            "note": notes.get(ds, ""),
        })

    month_key = f"{year}-{mon:02d}"
    gross_pnl = round(cumulative, 2)
    costs = month_costs_resolved(month_key)
    brokerage, charges = costs["brokerage"], costs["charges"]
    total_cost = round(brokerage + charges, 2)
    net_pnl = round(gross_pnl - total_cost, 2)        # matches broker's Net P&L
    closing = round(opening + net_pnl, 2)             # closing reflects net
    return_pct = round(net_pnl / opening * 100, 2) if opening else 0.0
    return {
        "month": month_key,
        "month_label": f"{_MON_FULL[mon - 1]} {year}",
        "opening": opening,
        "gross_pnl": gross_pnl, "brokerage": brokerage, "charges": charges,
        "total_cost": total_cost, "net_pnl": net_pnl,
        "costs_estimated": costs.get("estimated", False),
        "brokerage_rate": get_brokerage_rate(),
        "total_pnl": net_pnl,                         # headline P&L is net
        "closing": closing, "return_pct": return_pct, "days": days,
        "trade_days": [d for d in days if d["traded"]],
        "months": _months_with_data(),
    }


def _weekdays_between(a: date, b: date) -> int:
    """Count Mon-Fri days in [a, b] inclusive (a simple trading-day proxy)."""
    if b < a:
        return 0
    return sum(1 for i in range((b - a).days + 1)
               if (a + timedelta(days=i)).weekday() < 5)


def _sum_realized(start: date, end: date) -> float:
    rows = store.daily_between(start.isoformat(), end.isoformat())
    return round(sum(float(r["realized"] or 0) for r in rows), 2)


def targets_progress() -> dict:
    t = get_targets()
    today = _today()

    week_start = today - timedelta(days=today.weekday())        # Monday
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    out = {}
    for name, target, start, end in (
        ("weekly", t["weekly"], week_start, week_end),
        ("monthly", t["monthly"], month_start, month_end),
    ):
        achieved = _sum_realized(start, today)
        remaining = round(target - achieved, 2)
        days_left = _weekdays_between(today, end)  # includes today
        per_day = round(max(0.0, remaining) / days_left, 2) if days_left > 0 else 0.0
        pct = round(min(100.0, max(0.0, achieved / target * 100)), 1) if target else 0.0
        out[name] = {
            "target": target, "achieved": achieved, "remaining": remaining,
            "pct": pct, "days_left": days_left, "per_day": per_day,
            "plan": _plan_text(name, achieved, target, remaining, days_left, per_day),
        }
    return out


def _plan_text(name, achieved, target, remaining, days_left, per_day) -> str:
    if achieved >= target:
        return f"\U0001f389 {name.capitalize()} target hit! ₹{achieved:,.0f} of ₹{target:,.0f}."
    if days_left <= 0:
        return f"No trading days left this {name[:-2] if name.endswith('ly') else name}. ₹{remaining:,.0f} short."
    return (f"₹{remaining:,.0f} to go. Aim ~₹{per_day:,.0f}/day "
            f"over the next {days_left} trading day{'s' if days_left != 1 else ''}.")


# ---------------------------------------------------------------- analytics
def summary(start: str | None = None, end: str | None = None) -> dict:
    """Performance summary over all data, or restricted to a [start, end] range.

    When a date range is given, only *dated* days inside it are counted; undated
    summary imports (date = 'unknown') are excluded, since they can't be placed
    in a period.
    """
    if start or end:
        all_rows = store.daily_between(start or "0000-01-01", end or "9999-12-31")
        untagged = None
    else:
        all_rows = store.all_daily()
        untagged = next((d for d in all_rows if d["date"] == "unknown"), None)
    dated = [d for d in all_rows if d["date"] != "unknown"]

    # Totals / win-rate include everything (dated + undated summary imports).
    total = round(sum(float(d["realized"] or 0) for d in all_rows), 2)
    wins = sum(int(d["wins"] or 0) for d in all_rows)
    losses = sum(int(d["losses"] or 0) for d in all_rows)
    n_tr = sum(int(d["trades"] or 0) for d in all_rows)
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0

    # Day-level views use only DATED trades.
    profit_days = [d for d in dated if float(d["realized"] or 0) > 0]
    loss_days = [d for d in dated if float(d["realized"] or 0) < 0]
    best = max(dated, key=lambda d: float(d["realized"] or 0), default=None)
    worst = min(dated, key=lambda d: float(d["realized"] or 0), default=None)

    # Net P&L = gross − brokerage/charges (exact if entered, else auto-estimated).
    months_in_range = sorted({d["date"][:7] for d in dated if len(d["date"]) >= 7})
    _resolved = [month_costs_resolved(m) for m in months_in_range]
    total_brokerage = round(sum(c["brokerage"] for c in _resolved), 2)
    total_charges = round(sum(c["charges"] for c in _resolved), 2)
    total_costs = round(total_brokerage + total_charges, 2)
    costs_estimated = any(c.get("estimated") for c in _resolved)
    net_realized = round(total - total_costs, 2)

    return {
        "total_realized": total,
        "total_costs": total_costs,
        "total_brokerage": total_brokerage,
        "total_charges": total_charges,
        "costs_estimated": costs_estimated,
        "net_realized": net_realized,
        "trading_days": len(dated),
        "profit_days": len(profit_days),
        "loss_days": len(loss_days),
        "total_trades": n_tr,
        "win_rate": win_rate,
        "wins": wins, "losses": losses,
        "avg_day": round(sum(float(d["realized"] or 0) for d in dated) / len(dated), 2) if dated else 0.0,
        "net_avg_day": round(net_realized / len(dated), 2) if dated else 0.0,
        "best_day": {"date": best["date"], "realized": round(float(best["realized"]), 2)} if best else None,
        "worst_day": {"date": worst["date"], "realized": round(float(worst["realized"]), 2)} if worst else None,
        "current_streak": _streak(dated),
        "untagged_realized": round(float(untagged["realized"]), 2) if untagged else 0.0,
        "untagged_trades": int(untagged["trades"]) if untagged else 0,
        "daily": [{"date": d["date"], "realized": round(float(d["realized"] or 0), 2),
                   "trades": int(d["trades"] or 0)} for d in dated],
    }


_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MON_FULL = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]


def _period_dates(period: str) -> tuple[str, str]:
    today = _today()
    if period == "this_month":
        start = today.replace(day=1)
    elif period == "3m":
        start = today - timedelta(days=90)
    elif period == "1m":
        start = today - timedelta(days=30)
    else:  # all
        start = date(2000, 1, 1)
    return start.isoformat(), today.isoformat()


def _range(period: str = "all", start: str | None = None, end: str | None = None) -> tuple[str, str]:
    """Resolve the active date window: an explicit start/end (custom range) wins;
    otherwise fall back to the named preset period."""
    if start or end:
        return (start or "0000-01-01", end or "9999-12-31")
    return _period_dates(period)


def _closed_trades(period: str = "all", symbol: str | None = None,
                   start: str | None = None, end: str | None = None) -> list[dict]:
    """Closed, dated round-trips inside the window, optionally one instrument."""
    start, end = _range(period, start, end)
    out = []
    for t in store.recent_trades(100000):
        if not t.get("closed") or t.get("date") == "unknown":
            continue
        if not (start <= t["date"] <= end):
            continue
        if symbol and symbol != "all" and _underlying(t.get("symbol")) != symbol:
            continue
        out.append(t)
    return out


def _daily_rows(period: str = "all", symbol: str | None = None,
                start: str | None = None, end: str | None = None) -> list[dict]:
    """Per-day P&L rows. For 'all' instruments use the stored daily totals; for a
    single instrument, aggregate that instrument's trades by date."""
    s, e = _range(period, start, end)
    if not symbol or symbol == "all":
        return [r for r in store.daily_between(s, e) if r["date"] != "unknown"]
    agg: dict[str, dict] = {}
    for t in _closed_trades(period, symbol, start, end):
        d = t["date"]
        p = float(t.get("pnl") or 0)
        g = agg.setdefault(d, {"date": d, "realized": 0.0, "trades": 0, "wins": 0, "losses": 0})
        g["realized"] += p
        g["trades"] += 1
        if p > 0:
            g["wins"] += 1
        elif p < 0:
            g["losses"] += 1
    return sorted(agg.values(), key=lambda r: r["date"])


def _weekday_from(rows: list[dict]) -> list[dict]:
    """P&L grouped by day of week from a list of daily rows."""
    buckets = {i: {"pnl": 0.0, "days": 0, "wins": 0} for i in range(7)}
    for r in rows:
        if r["date"] == "unknown":
            continue
        w = datetime.strptime(r["date"], "%Y-%m-%d").weekday()
        v = float(r["realized"] or 0)
        buckets[w]["pnl"] += v
        buckets[w]["days"] += 1
        if v > 0:
            buckets[w]["wins"] += 1
    out = []
    for i in range(7):
        b = buckets[i]
        if i >= 5 and b["days"] == 0:   # hide weekend rows when empty
            continue
        out.append({"day": _WD[i], "pnl": round(b["pnl"], 2), "days": b["days"],
                    "avg": round(b["pnl"] / b["days"], 2) if b["days"] else 0.0,
                    "win_rate": round(b["wins"] / b["days"] * 100, 1) if b["days"] else 0.0})
    return out


def weekday_pnl(period: str = "all", symbol: str | None = None,
                start: str | None = None, end: str | None = None) -> list[dict]:
    """P&L grouped by day of week, within the period/instrument/range."""
    return _weekday_from(_daily_rows(period, symbol, start, end))


def _grp_summary(vals: list[float]) -> dict:
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    return {"trades": n, "pnl": round(sum(vals), 2),
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "avg": round(sum(vals) / n, 2) if n else 0.0}


def instrument_breakdown(period: str = "all", start: str | None = None,
                         end: str | None = None) -> list[dict]:
    """P&L per underlying instrument (NIFTY, SENSEX, BANKNIFTY, …) in the window."""
    by: dict[str, list[float]] = {}
    for t in _closed_trades(period, None, start, end):
        by.setdefault(_underlying(t.get("symbol")), []).append(float(t.get("pnl") or 0))
    out = [{"symbol": k, **_grp_summary(v)} for k, v in by.items()]
    out.sort(key=lambda r: r["pnl"], reverse=True)
    return out


def expiry_breakdown(period: str = "all", symbol: str | None = None,
                     start: str | None = None, end: str | None = None) -> dict:
    """Expiry-day trades vs trades on all other days."""
    groups = {"expiry": [], "other": [], "unknown": []}
    for t in _closed_trades(period, symbol, start, end):
        flag = _is_expiry_day(t.get("symbol"), t["date"])
        key = "expiry" if flag else "other" if flag is False else "unknown"
        groups[key].append(float(t.get("pnl") or 0))
    return {"expiry_day": _grp_summary(groups["expiry"]),
            "other_days": _grp_summary(groups["other"]),
            "unknown": _grp_summary(groups["unknown"])}


def available_instruments() -> list[str]:
    """Distinct underlyings seen across all closed trades, most-traded first."""
    counts: dict[str, int] = {}
    for t in _closed_trades("all", None):
        u = _underlying(t.get("symbol"))
        counts[u] = counts.get(u, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def insights(period: str = "all", symbol: str | None = None,
             start: str | None = None, end: str | None = None) -> dict:
    rows = _daily_rows(period, symbol, start, end)
    total = round(sum(float(r["realized"] or 0) for r in rows), 2)
    prof = sorted((r for r in rows if float(r["realized"] or 0) > 0),
                  key=lambda r: -float(r["realized"]))[:6]
    loss = sorted((r for r in rows if float(r["realized"] or 0) < 0),
                  key=lambda r: float(r["realized"]))[:6]
    wd = _weekday_from(rows)
    _traded_wd = [w for w in wd if w["days"] > 0]   # ignore zero-trade weekdays
    best_wd = max(_traded_wd, key=lambda d: d["pnl"], default=None)
    worst_wd = min(_traded_wd, key=lambda d: d["pnl"], default=None)
    s, e = _range(period, start, end)
    weekly = float(get_targets().get("weekly") or 0)
    result = {
        "period": period, "symbol": symbol or "all", "total": total, "days": len(rows),
        "range": {"start": s, "end": e},
        "per_day_target": round(weekly / 5, 2) if weekly else 0.0,
        "weekday": wd,
        "best_weekday": best_wd, "worst_weekday": worst_wd,
        "top_profit_days": [{"date": r["date"], "realized": round(float(r["realized"]), 2)} for r in prof],
        "top_loss_days": [{"date": r["date"], "realized": round(float(r["realized"]), 2)} for r in loss],
        "setup": setup_analysis(period, symbol, start, end),
        "instruments": instrument_breakdown(period, start, end),
        "expiry": expiry_breakdown(period, symbol, start, end),
    }
    result["narrative"] = narrative(result)
    return result


def _inr(n: float) -> str:
    n = round(float(n or 0))
    return ("-" if n < 0 else "") + "₹" + f"{abs(n):,}"


def narrative(data: dict) -> list[str]:
    """Plain-English insights derived purely from an insights() result dict.

    Kept side-effect free (takes the computed data, returns strings) so it is
    easy to unit-test without touching the database.
    """
    out: list[str] = []
    days = data.get("days", 0)
    if not days:
        return ["No trades in this range yet — pick a wider window or log some trades."]

    total = data.get("total", 0)
    if total >= 0:
        out.append(f"You're net positive at {_inr(total)} across {days} trading day"
                   f"{'s' if days != 1 else ''}.")
    else:
        out.append(f"You're net negative at {_inr(total)} over {days} trading day"
                   f"{'s' if days != 1 else ''} — focus on cutting losing days.")

    bw, ww = data.get("best_weekday"), data.get("worst_weekday")
    if bw and ww and bw.get("day") != ww.get("day"):
        if ww.get("pnl", 0) < 0:
            out.append(f"You lose the most on {_WD_FULL.get(ww['day'], ww['day'])} "
                       f"({_inr(ww['pnl'])}) and do best on {_WD_FULL.get(bw['day'], bw['day'])} "
                       f"({_inr(bw['pnl'])}). Trade lighter on your weak day.")
        else:
            out.append(f"Your strongest day is {_WD_FULL.get(bw['day'], bw['day'])} ({_inr(bw['pnl'])}).")

    instruments = data.get("instruments") or []
    if len(instruments) > 1:
        best_i, worst_i = instruments[0], instruments[-1]
        if best_i.get("pnl", 0) > 0:
            out.append(f"{best_i['symbol']} is your most profitable instrument "
                       f"({_inr(best_i['pnl'])}, {best_i.get('win_rate', 0)}% win).")
        if worst_i.get("pnl", 0) < 0:
            out.append(f"{worst_i['symbol']} is costing you ({_inr(worst_i['pnl'])}) — "
                       f"review whether it fits your edge.")

    exp = data.get("expiry") or {}
    ed, od = exp.get("expiry_day") or {}, exp.get("other_days") or {}
    if ed.get("trades") and od.get("trades"):
        if ed.get("avg", 0) < od.get("avg", 0):
            out.append(f"Expiry-day trades average {_inr(ed['avg'])} vs {_inr(od['avg'])} on other days "
                       f"— expiry day is hurting you.")
        else:
            out.append(f"You actually do better on expiry days "
                       f"(avg {_inr(ed['avg'])} vs {_inr(od['avg'])}).")

    setup = data.get("setup") or {}
    on, off = setup.get("on_plan") or {}, setup.get("off_plan") or {}
    if on.get("trades") and off.get("trades"):
        if off.get("pnl", 0) < 0 <= on.get("pnl", 0):
            out.append(f"On-plan trades make {_inr(on['pnl'])} while off-plan trades lose "
                       f"{_inr(off['pnl'])}. Discipline is your edge — stick to your setups.")
    return out


def setup_analysis(period: str = "all", symbol: str | None = None,
                   start: str | None = None, end: str | None = None) -> dict:
    """Compare trades taken 'on plan/setup' vs not, from per-trade tags."""
    tags = store.tags_map()
    trades = _closed_trades(period, symbol, start, end)

    groups = {"on": [], "off": [], "untagged": []}
    per_setup: dict[str, dict] = {}
    for t in trades:
        tag = tags.get(t["id"], {})
        on = tag.get("on_plan")
        pnl = float(t.get("pnl") or 0)
        key = "on" if on == 1 else "off" if on == 0 else "untagged"
        groups[key].append(pnl)
        setup = (tag.get("setup") or "").strip()
        if setup:
            s = per_setup.setdefault(setup, {"pnl": 0.0, "trades": 0, "wins": 0})
            s["pnl"] += pnl
            s["trades"] += 1
            if pnl > 0:
                s["wins"] += 1

    def summ(vals):
        n = len(vals)
        wins = sum(1 for v in vals if v > 0)
        return {"trades": n, "pnl": round(sum(vals), 2),
                "win_rate": round(wins / n * 100, 1) if n else 0.0}

    setups = [{"setup": k, "pnl": round(v["pnl"], 2), "trades": v["trades"],
               "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0.0}
              for k, v in sorted(per_setup.items(), key=lambda kv: kv[1]["pnl"], reverse=True)]
    return {"on_plan": summ(groups["on"]), "off_plan": summ(groups["off"]),
            "untagged": summ(groups["untagged"]), "by_setup": setups}


def _streak(daily: list[dict]) -> dict:
    """Current run of consecutive profit or loss days (most recent first)."""
    if not daily:
        return {"type": "none", "count": 0}
    last = sorted(daily, key=lambda d: d["date"], reverse=True)
    sign = 1 if float(last[0]["realized"] or 0) > 0 else (-1 if float(last[0]["realized"] or 0) < 0 else 0)
    if sign == 0:
        return {"type": "flat", "count": 0}
    count = 0
    for d in last:
        v = float(d["realized"] or 0)
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            count += 1
        else:
            break
    return {"type": "profit" if sign > 0 else "loss", "count": count}


# -------------------------------------------------------------- action plan
_WD_FULL = {"Mon": "Mondays", "Tue": "Tuesdays", "Wed": "Wednesdays",
            "Thu": "Thursdays", "Fri": "Fridays", "Sat": "Saturdays", "Sun": "Sundays"}


def _money(n: float, signed: bool = False) -> str:
    n = round(float(n or 0))
    if signed:
        sign = "+" if n > 0 else "-" if n < 0 else ""
        return f"{sign}₹{abs(n):,}"
    return f"₹{abs(n):,}"


def _pi(tone: str, icon: str, title: str, detail: str) -> dict:
    return {"tone": tone, "icon": icon, "title": title, "detail": detail}


def action_plan(rules_analysis: dict | None = None, rules_config: dict | None = None) -> dict:
    """Turn the trader's own history into a plain-language game plan: where the
    money leaks (weekday / expiry / instrument / discipline) and what to do."""
    ins = insights("all")
    tg = targets_progress()
    items: list[dict] = []

    # 1. Monthly target context
    m = tg["monthly"]
    if m["achieved"] >= m["target"] and m["target"]:
        items.append(_pi("good", "\U0001f3af", "Monthly target hit",
            f"You're at {_money(m['achieved'], True)} of {_money(m['target'])}. "
            f"Protect it — don't give it back chasing more."))
    else:
        items.append(_pi("info", "\U0001f3af", "This month's target", m["plan"]))

    # 2. Weekday losses / edge
    wd = ins["weekday"]
    worst_wd, best_wd = ins["worst_weekday"], ins["best_weekday"]
    losers = sorted([w for w in wd if w["pnl"] < 0 and w["days"] >= 2], key=lambda w: w["pnl"])
    if losers:
        w = losers[0]
        items.append(_pi("bad", "\U0001f4c9", f"{_WD_FULL[w['day']]} are hurting you",
            f"{_money(w['pnl'], True)} over {w['days']} {_WD_FULL[w['day']]} "
            f"({w['win_rate']}% win, avg {_money(w['avg'], True)}/day). "
            f"Trade half size on {_WD_FULL[w['day']]} — or skip them — and your curve steadies."))
    if best_wd and best_wd["pnl"] > 0 and best_wd["days"] >= 2 and \
            (not worst_wd or best_wd["day"] != worst_wd["day"]):
        items.append(_pi("good", "\U0001f4c8", f"{_WD_FULL[best_wd['day']]} are your edge",
            f"{_money(best_wd['pnl'], True)} over {best_wd['days']} days "
            f"({best_wd['win_rate']}% win). Put your biggest size here."))

    # 3. Expiry day vs other days
    ex = ins["expiry"]
    ed, od = ex["expiry_day"], ex["other_days"]
    if ed["trades"] >= 5 and od["trades"] >= 5:
        if ed["avg"] < 0 and ed["avg"] < od["avg"]:
            items.append(_pi("bad", "\U0001f4c5", "Expiry day is costing you",
                f"Avg {_money(ed['avg'], True)}/trade on expiry vs {_money(od['avg'], True)} on other days. "
                f"Cut size on expiry, or only take A+ setups."))
        elif ed["avg"] > od["avg"]:
            items.append(_pi("good", "\U0001f4c5", "Expiry isn't the problem",
                f"You do better on expiry (avg {_money(ed['avg'], True)}/trade vs {_money(od['avg'], True)}). "
                f"Your damage comes from non-expiry days — tighten those instead."))

    # 4. Instrument edge / drag
    inst = ins["instruments"]
    edge = [i for i in inst if i["pnl"] > 0 and i["trades"] >= 5]
    drag = [i for i in inst if i["pnl"] < 0 and i["trades"] >= 5]
    if edge:
        b = max(edge, key=lambda i: i["pnl"])
        items.append(_pi("good", "\U0001f3af", f"{b['symbol']} is where you make money",
            f"{_money(b['pnl'], True)} over {b['trades']} trades ({b['win_rate']}% win). Lean into it."))
    if drag:
        w = min(drag, key=lambda i: i["pnl"])
        items.append(_pi("bad", "\U0001f3af", f"{w['symbol']} is draining the account",
            f"{_money(w['pnl'], True)} over {w['trades']} trades ({w['win_rate']}% win). "
            f"Trade it minimum size — or stop — until you fix what's going wrong there."))

    # 5. Rule discipline
    if rules_analysis and rules_analysis.get("total_days"):
        comp = rules_analysis["compliance"]
        mb = rules_analysis.get("most_broken")
        if comp < 70:
            detail = (f"You kept every rule on only {rules_analysis['clean_days']}/"
                      f"{rules_analysis['total_days']} days ({comp}%).")
            if mb:
                detail += f" Most broken: <b>{mb}</b> — fix that one first."
            items.append(_pi("bad", "⚖", "Discipline is your #1 lever", detail))
        else:
            items.append(_pi("good", "⚖", "Discipline is solid",
                f"{comp}% rule compliance over {rules_analysis['total_days']} days. Keep it up."))

    # 6. What your daily stop would have saved
    if rules_config:
        r = rules_config.get("max_daily_loss", {})
        if r.get("enabled"):
            limit = abs(float(r.get("value") or 0))
            if limit:
                excess = sum(max(0.0, -float(d["realized"] or 0) - limit)
                             for d in store.all_daily()
                             if d["date"] != "unknown" and float(d["realized"] or 0) < -limit)
                if excess > 0:
                    items.append(_pi("warn", "\U0001f6d1", "Your daily stop would have saved money",
                        f"Honouring your {_money(limit)} daily max-loss would have saved about "
                        f"{_money(excess)} on the days you blew past it. Set the alert and obey it."))

    return {"headline": _plan_headline(ins, tg, drag, losers),
            "total": ins["total"], "days": ins["days"], "items": items,
            "week": week_plan(),
            "month": month_plan(),
            "charts": {
                "weekday": ins["weekday"],
                "instruments": ins["instruments"],
                "expiry": {"Expiry day": ins["expiry"]["expiry_day"]["pnl"],
                           "Other days": ins["expiry"]["other_days"]["pnl"]},
            }}


def week_plan() -> dict:
    """This week's day-by-day plan: the per-day target needed to hit the weekly
    goal, each weekday's actual P&L, and whether that day is done / on target."""
    today = _today()
    monday = today - timedelta(days=today.weekday())
    weekly = float(get_targets().get("weekly") or 0)
    per_day = round(weekly / 5, 2) if weekly else 0.0
    rows = {r["date"]: float(r["realized"] or 0)
            for r in store.daily_between(monday.isoformat(), (monday + timedelta(days=6)).isoformat())
            if r["date"] != "unknown"}
    days = []
    achieved = 0.0
    for i in range(5):   # Mon–Fri
        d = monday + timedelta(days=i)
        ds = d.isoformat()
        traded = ds in rows
        actual = rows.get(ds, 0.0)
        if traded:
            achieved += actual
        days.append({
            "day": _WD[i], "date": ds, "target": per_day, "actual": round(actual, 2),
            "traded": traded, "future": d > today, "today": d == today,
            "met": bool(traded and per_day and actual >= per_day),
        })
    pct = round(min(100.0, max(0.0, achieved / weekly * 100)), 1) if weekly else 0.0
    return {"weekly_target": weekly, "per_day_target": per_day,
            "achieved": round(achieved, 2), "pct": pct,
            "week_start": monday.isoformat(), "days": days}


def _weekdays_in(start, end) -> int:
    """Count Mon–Fri days in the inclusive date range [start, end]."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def month_plan() -> dict:
    """This month's week-by-week plan: the monthly goal split across the weeks of
    the month (weighted by trading days), with each week's actual P&L and status."""
    today = _today()
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    monthly = float(get_targets().get("monthly") or 0)

    rows = {r["date"]: float(r["realized"] or 0)
            for r in store.daily_between(month_start.isoformat(), month_end.isoformat())
            if r["date"] != "unknown"}

    total_weekdays = _weekdays_in(month_start, month_end) or 1

    # Split the month into calendar weeks (Mon–Sun), clipped to the month.
    weeks = []
    achieved = 0.0
    cur = month_start
    idx = 0
    while cur <= month_end:
        wk_monday = cur - timedelta(days=cur.weekday())
        seg_start = max(month_start, wk_monday)
        seg_end = min(month_end, wk_monday + timedelta(days=6))
        cur = wk_monday + timedelta(days=7)
        wk_days = _weekdays_in(seg_start, seg_end)
        if wk_days == 0:          # a weekend-only sliver (e.g. month starts on Sat) — skip
            continue
        idx += 1
        target = round(monthly * wk_days / total_weekdays, 2) if monthly else 0.0
        actual = round(sum(v for ds, v in rows.items()
                           if seg_start.isoformat() <= ds <= seg_end.isoformat()), 2)
        traded = any(seg_start.isoformat() <= ds <= seg_end.isoformat() for ds in rows)
        if traded:
            achieved += actual
        future = seg_start > today
        current = seg_start <= today <= seg_end
        weeks.append({
            "label": f"Week {idx}",
            "range": f"{seg_start.day}–{seg_end.day} {_MON[month_start.month - 1]}",
            "target": target, "actual": actual, "traded": traded,
            "future": future, "current": current,
            "met": bool(traded and target and actual >= target),
        })

    pct = round(min(100.0, max(0.0, achieved / monthly * 100)), 1) if monthly else 0.0
    return {"monthly_target": monthly, "achieved": round(achieved, 2), "pct": pct,
            "month_label": f"{_MON_FULL[month_start.month - 1]} {month_start.year}",
            "weeks": weeks}


def _plan_headline(ins: dict, tg: dict, drag: list, losers: list) -> str:
    if drag:
        w = min(drag, key=lambda i: i["pnl"])
        return (f"Your biggest leak is <b>{w['symbol']}</b> ({_money(w['pnl'], True)}). "
                f"Plug that and the month turns around.")
    if losers:
        w = losers[0]
        return (f"Most of your damage lands on <b>{_WD_FULL[w['day']]}</b> "
                f"({_money(w['pnl'], True)}). Fix that day first.")
    if ins["total"] >= 0:
        return "You're net positive — now the job is to protect it and press your edge."
    return "You're net negative overall — the plan below is the fastest way back."
