"""Discipline rules: evaluate a day's trades and flag broken rules."""
from __future__ import annotations

from . import store

DEFAULT_RULES = {
    "max_daily_loss":     {"enabled": True, "value": 5000,
                           "label": "Max daily loss", "unit": "₹"},
    "max_trades_per_day": {"enabled": True, "value": 10,
                           "label": "Max trades per day", "unit": "trades"},
    "stop_after_losses":  {"enabled": True, "value": 3,
                           "label": "Stop after consecutive losses", "unit": "losses"},
    "max_loss_per_trade": {"enabled": True, "value": 2000,
                           "label": "Max loss per trade", "unit": "₹"},
}


def get_rules() -> dict:
    saved = store.get_setting("rules", {}) or {}
    rules = {k: {**v} for k, v in DEFAULT_RULES.items()}
    for k, v in saved.items():
        if k in rules and isinstance(v, dict):
            rules[k].update(v)
    return rules


def set_rules(new: dict) -> dict:
    store.set_setting("rules", new)
    return get_rules()


def evaluate(date: str) -> dict:
    """Check the rules against the closed trades on `date`.
    Returns {date, pnl, trades, violations:[...], ok:bool}."""
    rules = get_rules()
    trades = [t for t in store.trades_on(date) if t.get("closed")]
    pnl = round(sum(float(t["pnl"] or 0) for t in trades), 2)
    n = len(trades)
    violations: list[dict] = []

    r = rules["max_daily_loss"]
    if r["enabled"] and pnl < -abs(r["value"]):
        violations.append(_v("max_daily_loss", r,
            f"Day's loss ₹{abs(pnl):,.0f} crossed your ₹{r['value']:,.0f} limit."))

    r = rules["max_trades_per_day"]
    if r["enabled"] and n > r["value"]:
        violations.append(_v("max_trades_per_day", r,
            f"Took {n} trades — over your limit of {r['value']} per day."))

    r = rules["stop_after_losses"]
    if r["enabled"]:
        streak = _max_losing_streak(trades)
        if streak >= r["value"]:
            violations.append(_v("stop_after_losses", r,
                f"Hit {streak} losing trades in a row — your stop is {r['value']}."))

    r = rules["max_loss_per_trade"]
    if r["enabled"]:
        worst = min([float(t["pnl"] or 0) for t in trades], default=0)
        if worst < -abs(r["value"]):
            violations.append(_v("max_loss_per_trade", r,
                f"A single trade lost ₹{abs(worst):,.0f} — over your ₹{r['value']:,.0f} cap."))

    return {"date": date, "pnl": pnl, "trades": n,
            "violations": violations, "ok": not violations}


def _v(key: str, rule: dict, message: str) -> dict:
    return {"rule": key, "label": rule["label"], "message": message}


def analyze_history() -> dict:
    """Evaluate every dated trading day and summarise rule adherence."""
    cfg = get_rules()
    days = sorted({d["date"] for d in store.all_daily() if d["date"] != "unknown"})
    per_rule = {k: {"label": v["label"], "broken_days": 0} for k, v in cfg.items()}
    daily_status: list[dict] = []
    clean = 0
    for date in days:
        ev = evaluate(date)
        if ev["ok"]:
            clean += 1
        for v in ev["violations"]:
            if v["rule"] in per_rule:
                per_rule[v["rule"]]["broken_days"] += 1
        daily_status.append({"date": date, "broke": not ev["ok"], "count": len(ev["violations"])})
    total = len(days)
    worst = max(per_rule.items(), key=lambda kv: kv[1]["broken_days"], default=(None, {}))
    return {
        "total_days": total,
        "clean_days": clean,
        "broken_days": total - clean,
        "compliance": round(clean / total * 100, 1) if total else 0.0,
        "per_rule": per_rule,
        "most_broken": per_rule[worst[0]]["label"] if worst[0] and worst[1].get("broken_days") else None,
        "daily_status": daily_status,
    }


def _max_losing_streak(trades: list[dict]) -> int:
    best = cur = 0
    for t in sorted(trades, key=lambda x: (x.get("time") or "")):
        if float(t["pnl"] or 0) < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
