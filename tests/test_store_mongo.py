"""Verify the MongoDB storage backend matches the SQLite one's behaviour.

Uses mongomock (in-memory) so no real MongoDB is needed.
"""
import mongomock
import pytest

from backend.journal import store_mongo as m


@pytest.fixture
def mdb(monkeypatch):
    # Point the backend at an in-memory Mongo and reset its cached handle.
    monkeypatch.setattr(m, "MongoClient", mongomock.MongoClient)
    monkeypatch.setattr(m, "_client", None)
    monkeypatch.setattr(m, "_db", None)
    m.init()
    return m


TRADES = [
    {"id": "a", "date": "2026-10-13", "time": "09:30", "symbol": "NIFTY", "side": "LONG",
     "qty": 50, "price": 100, "value": 5000, "pnl": 3000, "source": "dhan"},
    {"id": "b", "date": "2026-11-05", "time": "10:00", "symbol": "NIFTY", "side": "SHORT",
     "qty": 75, "price": 88, "value": 6600, "pnl": -1200, "source": "dhan"},
    {"id": "c", "date": "2026-11-05", "time": "11:00", "symbol": "BANKNIFTY", "side": "LONG",
     "qty": 15, "price": 200, "value": 3000, "pnl": 500, "source": "dhan"},
    {"id": "u", "date": "unknown", "time": "", "symbol": "NIFTY", "side": "LONG",
     "qty": 10, "price": 5, "value": 50, "pnl": 250, "source": "dhan"},
]


def test_upsert_and_queries(mdb):
    assert mdb.upsert_trades(TRADES) == 4
    # re-upsert same ids doesn't duplicate
    mdb.upsert_trades(TRADES)
    assert len(mdb.recent_trades()) == 4

    # trades_between excludes 'unknown' and respects the range
    nov = mdb.trades_between("2026-11-01", "2026-11-30")
    assert {t["id"] for t in nov} == {"b", "c"}
    # sorted date desc then time desc
    assert [t["id"] for t in nov] == ["c", "b"]

    # recent_trades: dated first (desc), undated last
    ids = [t["id"] for t in mdb.recent_trades()]
    assert ids[-1] == "u"
    assert ids[:3] == ["c", "b", "a"]

    # closed flag inferred from pnl
    assert all(t["closed"] == 1 for t in mdb.recent_trades())


def test_daily_rollup_and_range(mdb):
    mdb.upsert_trades(TRADES)
    mdb.rebuild_daily_from_trades()
    alld = {d["date"]: d for d in mdb.all_daily()}
    assert alld["2026-11-05"]["realized"] == -700.0   # -1200 + 500
    assert alld["2026-11-05"]["trades"] == 2
    assert alld["2026-11-05"]["wins"] == 1
    assert alld["2026-11-05"]["losses"] == 1
    assert alld["2026-10-13"]["realized"] == 3000.0
    # unknown-dated closed trade still rolled up under its own 'unknown' key
    assert "unknown" in alld

    nov = mdb.daily_between("2026-11-01", "2026-11-30")
    assert [d["date"] for d in nov] == ["2026-11-05"]


def test_settings_roundtrip(mdb):
    assert mdb.get_setting("missing", 42) == 42
    mdb.set_setting("opening_capital", 100000)
    mdb.set_setting("targets", {"weekly": 5000, "monthly": 20000})
    assert mdb.get_setting("opening_capital") == 100000
    assert mdb.get_setting("targets")["monthly"] == 20000


def test_tags(mdb):
    mdb.set_tag("a", setup="VWAP", note="clean", on_plan=1)
    mdb.set_tag("b", setup=None, note=None, on_plan=0)
    tm = mdb.tags_map()
    assert tm["a"] == {"setup": "VWAP", "note": "clean", "on_plan": 1}
    assert tm["b"]["on_plan"] == 0


def test_clear_source_range_keeps_other_months(mdb):
    mdb.upsert_trades(TRADES)
    mdb.clear_source_range("dhan", "2026-11-01", "2026-11-30")
    left = {t["id"] for t in mdb.recent_trades()}
    assert left == {"a", "u"}          # October + unknown survive, November gone
