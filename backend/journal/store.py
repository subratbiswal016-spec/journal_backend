"""SQLite storage for the trading journal (no external dependencies)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent.parent / ".journal.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id        TEXT PRIMARY KEY,   -- stable id (broker trade id or synthesized hash)
    date      TEXT NOT NULL,      -- YYYY-MM-DD (IST)
    time      TEXT,               -- HH:MM:SS
    symbol    TEXT,
    side      TEXT,               -- BUY / SELL
    qty       REAL,
    price     REAL,
    value     REAL,
    pnl       REAL,               -- realized P&L for a closed round-trip (nullable)
    closed    INTEGER DEFAULT 0,  -- 1 if this row is a closed round-trip with pnl
    source    TEXT                -- 'fyers' | 'csv'
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date      TEXT PRIMARY KEY,   -- YYYY-MM-DD
    realized  REAL DEFAULT 0,
    trades    INTEGER DEFAULT 0,
    wins      INTEGER DEFAULT 0,
    losses    INTEGER DEFAULT 0,
    source    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT                    -- JSON
);

CREATE TABLE IF NOT EXISTS trade_tags (
    trade_id TEXT PRIMARY KEY,    -- matches trades.id (stable across re-sync)
    setup    TEXT,
    note     TEXT,
    on_plan  INTEGER              -- 1 = on plan/setup, 0 = off plan, NULL = untagged
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _lock, _conn() as c:
        c.executescript(_SCHEMA)


# ----------------------------------------------------------------- trades
def upsert_trades(rows: list[dict]) -> int:
    """Insert or replace trade rows. Returns count written."""
    if not rows:
        return 0
    with _lock, _conn() as c:
        c.executemany(
            """INSERT OR REPLACE INTO trades
               (id, date, time, symbol, side, qty, price, value, pnl, closed, source)
               VALUES (:id, :date, :time, :symbol, :side, :qty, :price, :value, :pnl, :closed, :source)""",
            [_trade_defaults(r) for r in rows],
        )
    return len(rows)


def _trade_defaults(r: dict) -> dict:
    out = {k: r.get(k) for k in
           ("id", "date", "time", "symbol", "side", "qty", "price", "value", "pnl", "closed", "source")}
    out["closed"] = 1 if r.get("pnl") is not None else int(r.get("closed", 0) or 0)
    return out


def trades_between(start: str, end: str) -> list[dict]:
    with _lock, _conn() as c:
        cur = c.execute(
            "SELECT * FROM trades WHERE date >= ? AND date <= ? ORDER BY date DESC, time DESC",
            (start, end))
        return [dict(r) for r in cur.fetchall()]


def trades_on(date: str) -> list[dict]:
    with _lock, _conn() as c:
        cur = c.execute("SELECT * FROM trades WHERE date = ? ORDER BY time ASC", (date,))
        return [dict(r) for r in cur.fetchall()]


def recent_trades(limit: int = 300) -> list[dict]:
    """All trades, dated first then undated ('unknown'), most recent first."""
    with _lock, _conn() as c:
        cur = c.execute(
            "SELECT * FROM trades ORDER BY (date = 'unknown'), date DESC, time DESC LIMIT ?",
            (limit,))
        return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------- daily pnl
def set_daily(date: str, realized: float, trades: int, wins: int, losses: int, source: str) -> None:
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO daily_pnl (date, realized, trades, wins, losses, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (date, realized, trades, wins, losses, source))


def rebuild_daily_from_trades() -> None:
    """Recompute daily_pnl from closed trades (used after CSV import / sync)."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM daily_pnl")
        rows = c.execute(
            """SELECT date,
                      COALESCE(SUM(pnl), 0) AS realized,
                      COUNT(*) AS trades,
                      SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses
               FROM trades WHERE closed = 1 GROUP BY date""").fetchall()
        for r in rows:
            c.execute(
                """INSERT OR REPLACE INTO daily_pnl (date, realized, trades, wins, losses, source)
                   VALUES (?, ?, ?, ?, ?, 'derived')""",
                (r["date"], r["realized"], r["trades"], r["wins"], r["losses"]))


def daily_between(start: str, end: str) -> list[dict]:
    with _lock, _conn() as c:
        cur = c.execute(
            "SELECT * FROM daily_pnl WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (start, end))
        return [dict(r) for r in cur.fetchall()]


def all_daily() -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM daily_pnl ORDER BY date ASC").fetchall()]


# ----------------------------------------------------------------- settings
def get_setting(key: str, default: Any = None) -> Any:
    with _lock, _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value: Any) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  (key, json.dumps(value)))


def set_tag(trade_id: str, setup=None, note=None, on_plan=None) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO trade_tags (trade_id, setup, note, on_plan) VALUES (?, ?, ?, ?)",
                  (trade_id, setup, note, on_plan))


def tags_map() -> dict:
    with _lock, _conn() as c:
        return {r["trade_id"]: {"setup": r["setup"], "note": r["note"], "on_plan": r["on_plan"]}
                for r in c.execute("SELECT * FROM trade_tags").fetchall()}


def clear_source(source: str) -> None:
    """Remove all trades from a given source (e.g. re-importing a CSV)."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM trades WHERE source = ?", (source,))


def clear_source_date(source: str, date: str) -> None:
    """Remove a source's trades on one date (e.g. re-syncing today from Fyers)."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM trades WHERE source = ? AND date = ?", (source, date))


init()
