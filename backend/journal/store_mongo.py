"""MongoDB storage backend — same interface as the SQLite store.

Enabled when MONGO_URL is set (see store.py). Lets a hosted deploy (e.g. Render
free tier, whose disk is wiped on restart) keep journal data in MongoDB Atlas's
free tier so trades, tags, settings and capital survive restarts.

Collections mirror the SQLite tables:
  trades(_id=id), daily_pnl(_id=date), settings(_id=key), trade_tags(_id=trade_id)
"""
from __future__ import annotations

import os
from typing import Any

from pymongo import MongoClient, ReplaceOne

_client: MongoClient | None = None
_db = None

_TRADE_FIELDS = ("id", "date", "time", "symbol", "side", "qty", "price",
                 "value", "pnl", "closed", "source")


def _database():
    global _client, _db
    if _db is None:
        url = os.getenv("MONGO_URL")
        _client = MongoClient(url, serverSelectionTimeoutMS=8000, tz_aware=False)
        _db = _client[os.getenv("MONGO_DB", "oi_journal")]
    return _db


def _coll(name: str):
    return _database()[name]


def _no_id(doc: dict) -> dict:
    """Drop Mongo's _id from a returned document (callers want plain fields)."""
    doc.pop("_id", None)
    return doc


def init() -> None:
    _coll("trades").create_index("date")
    _coll("trades").create_index("source")


# ----------------------------------------------------------------- trades
def _trade_defaults(r: dict) -> dict:
    out = {k: r.get(k) for k in _TRADE_FIELDS}
    out["closed"] = 1 if r.get("pnl") is not None else int(r.get("closed", 0) or 0)
    return out


def upsert_trades(rows: list[dict]) -> int:
    if not rows:
        return 0
    ops = []
    for r in rows:
        d = _trade_defaults(r)
        ops.append(ReplaceOne({"_id": d["id"]}, {"_id": d["id"], **d}, upsert=True))
    if ops:
        _coll("trades").bulk_write(ops, ordered=False)
    return len(rows)


def trades_between(start: str, end: str) -> list[dict]:
    cur = _coll("trades").find(
        {"date": {"$gte": start, "$lte": end, "$ne": "unknown"}})
    rows = [_no_id(d) for d in cur]
    rows.sort(key=lambda d: (d.get("date") or "", d.get("time") or ""), reverse=True)
    return rows


def trades_on(date: str) -> list[dict]:
    rows = [_no_id(d) for d in _coll("trades").find({"date": date})]
    rows.sort(key=lambda d: (d.get("time") or ""))
    return rows


def recent_trades(limit: int = 300) -> list[dict]:
    docs = [_no_id(d) for d in _coll("trades").find()]
    dated = sorted((d for d in docs if d.get("date") != "unknown"),
                   key=lambda d: (d.get("date") or "", d.get("time") or ""), reverse=True)
    undated = [d for d in docs if d.get("date") == "unknown"]
    return (dated + undated)[:limit]


# --------------------------------------------------------------- daily pnl
def set_daily(date: str, realized: float, trades: int, wins: int, losses: int, source: str) -> None:
    _coll("daily_pnl").replace_one(
        {"_id": date},
        {"_id": date, "date": date, "realized": realized, "trades": trades,
         "wins": wins, "losses": losses, "source": source},
        upsert=True)


def rebuild_daily_from_trades() -> None:
    _coll("daily_pnl").delete_many({})
    agg: dict[str, dict] = {}
    for t in _coll("trades").find({"closed": 1}):
        d = t.get("date")
        a = agg.setdefault(d, {"realized": 0.0, "trades": 0, "wins": 0, "losses": 0})
        pnl = float(t.get("pnl") or 0)
        a["realized"] += pnl
        a["trades"] += 1
        if pnl > 0:
            a["wins"] += 1
        elif pnl < 0:
            a["losses"] += 1
    docs = [{"_id": d, "date": d, "source": "derived",
             "realized": round(v["realized"], 2), "trades": v["trades"],
             "wins": v["wins"], "losses": v["losses"]} for d, v in agg.items()]
    if docs:
        _coll("daily_pnl").insert_many(docs)


def daily_between(start: str, end: str) -> list[dict]:
    rows = [_no_id(d) for d in _coll("daily_pnl").find({"date": {"$gte": start, "$lte": end}})]
    rows.sort(key=lambda d: d.get("date") or "")
    return rows


def all_daily() -> list[dict]:
    rows = [_no_id(d) for d in _coll("daily_pnl").find()]
    rows.sort(key=lambda d: d.get("date") or "")
    return rows


# ----------------------------------------------------------------- settings
def get_setting(key: str, default: Any = None) -> Any:
    doc = _coll("settings").find_one({"_id": key})
    return doc["value"] if doc and "value" in doc else default


def set_setting(key: str, value: Any) -> None:
    _coll("settings").replace_one({"_id": key}, {"_id": key, "value": value}, upsert=True)


def set_tag(trade_id: str, setup=None, note=None, on_plan=None) -> None:
    _coll("trade_tags").replace_one(
        {"_id": trade_id},
        {"_id": trade_id, "setup": setup, "note": note, "on_plan": on_plan},
        upsert=True)


def tags_map() -> dict:
    return {d["_id"]: {"setup": d.get("setup"), "note": d.get("note"),
                       "on_plan": d.get("on_plan")}
            for d in _coll("trade_tags").find()}


def clear_source(source: str) -> None:
    _coll("trades").delete_many({"source": source})


def clear_source_date(source: str, date: str) -> None:
    _coll("trades").delete_many({"source": source, "date": date})


def clear_source_range(source: str, start: str, end: str) -> None:
    _coll("trades").delete_many(
        {"source": source, "date": {"$gte": start, "$lte": end, "$ne": "unknown"}})
