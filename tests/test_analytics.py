"""Unit tests for journal analytics: symbol parsing, range resolution,
the plain-English narrative, and date-range filtering against a temp DB.

Run:  .venv\\Scripts\\python.exe -m pytest -q
"""
import pytest

from backend.journal import analytics as A
from backend.journal import store


# ----------------------------------------------------------- pure helpers
def test_parse_symbol():
    assert A._parse_symbol("NIFTY 15 OCT 24000 CALL") == ("NIFTY", 15, 10)
    assert A._underlying("BANKNIFTY 30 DEC 52000 PUT") == "BANKNIFTY"
    assert A._underlying(None) == "OTHER"
    assert A._parse_symbol("")[0] is None


def test_is_expiry_day():
    sym = "NIFTY 15 OCT 24000 CALL"
    assert A._is_expiry_day(sym, "2026-10-15") is True
    assert A._is_expiry_day(sym, "2026-10-13") is False
    assert A._is_expiry_day("JUNK", "2026-10-15") is None


def test_range_resolution():
    # explicit custom range wins
    assert A._range("all", "2026-10-01", "2026-10-20") == ("2026-10-01", "2026-10-20")
    # partial range fills open ends
    assert A._range("all", "2026-10-01", None) == ("2026-10-01", "9999-12-31")
    assert A._range("all", None, "2026-10-20") == ("0000-01-01", "2026-10-20")
    # named preset returns a (start, today) window
    s, e = A._range("3m")
    assert s < e


# ----------------------------------------------------------- narrative
def test_narrative_empty():
    assert A.narrative({"days": 0}) == \
        ["No trades in this range yet — pick a wider window or log some trades."]


def test_narrative_rich():
    data = {
        "days": 10, "total": 12253,
        "best_weekday": {"day": "Thu", "pnl": 17000},
        "worst_weekday": {"day": "Mon", "pnl": -14000},
        "instruments": [
            {"symbol": "SENSEX", "pnl": 11232, "win_rate": 44.1},
            {"symbol": "NIFTY", "pnl": -22380, "win_rate": 34.2},
        ],
        "expiry": {"expiry_day": {"trades": 76, "avg": 39},
                   "other_days": {"trades": 56, "avg": -252}},
        "setup": {"on_plan": {"trades": 20, "pnl": 5000},
                  "off_plan": {"trades": 10, "pnl": -3000}},
    }
    lines = A.narrative(data)
    blob = " ".join(lines)
    assert "net positive" in blob
    assert "Mondays" in blob and "Thursdays" in blob
    assert "SENSEX" in blob and "NIFTY" in blob
    assert "expiry" in blob.lower()
    assert "Discipline" in blob


# ----------------------------------------------------------- DB-backed
@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "test.db")
    store.init()
    rows = [
        _t("1", "2026-10-13", "NIFTY 15 OCT 24000 CALL", 5000),
        _t("2", "2026-10-14", "NIFTY 15 OCT 24000 CALL", -3000),
        _t("3", "2026-10-15", "SENSEX 15 OCT 80000 CALL", 8000),
        _t("4", "2026-11-05", "NIFTY 19 NOV 25000 PUT", -2000),
    ]
    store.upsert_trades(rows)
    store.rebuild_daily_from_trades()
    return store


def _t(tid, date, symbol, pnl):
    return {"id": tid, "date": date, "time": "10:00:00", "symbol": symbol,
            "side": "BUY", "qty": 50, "price": 100, "value": 5000,
            "pnl": pnl, "closed": 1, "source": "csv"}


def test_summary_full_vs_range(db):
    full = A.summary()
    assert full["trading_days"] == 4
    assert full["total_realized"] == pytest.approx(8000)

    ranged = A.summary("2026-10-13", "2026-10-15")
    assert ranged["trading_days"] == 3
    assert ranged["total_realized"] == pytest.approx(10000)   # 5000 - 3000 + 8000


def test_trades_between(db):
    rows = store.trades_between("2026-10-13", "2026-10-15")
    assert len(rows) == 3
    assert {r["date"] for r in rows} == {"2026-10-13", "2026-10-14", "2026-10-15"}
    assert all(r["date"] != "2026-11-05" for r in rows)


def test_insights_range(db):
    ins = A.insights("all", "all", "2026-10-13", "2026-10-15")
    assert ins["days"] == 3
    assert ins["total"] == pytest.approx(10000)
    assert ins["range"] == {"start": "2026-10-13", "end": "2026-10-15"}
    assert isinstance(ins["narrative"], list) and ins["narrative"]
