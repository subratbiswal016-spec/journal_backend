"""Tests for Trade Mode snapshot enrichment, inspect, SL/Target, reverse, and order APIs."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from backend.sources.dhan import DhanDataSource
from backend.sources.fyers import FyersDataSource


def _import_app_isolated():
    import os
    os.environ.pop("MONGO_URL", None)
    from backend.journal import store
    tmp = Path(tempfile.gettempdir()) / "pytest_journal_isolated.db"
    orig_db = store._DB_PATH
    try:
        store._DB_PATH = tmp
        store.init()
        from backend import app as app_mod
        return app_mod
    finally:
        store._DB_PATH = orig_db


class _FakeReq:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def test_extract_underlying_symbol():
    app_mod = _import_app_isolated()
    assert app_mod._extract_underlying_symbol("SENSEX-Dec2026-83700-CE") == "SENSEX"
    assert app_mod._extract_underlying_symbol("BSE:SENSEX26DEC83700CE") == "SENSEX"
    assert app_mod._extract_underlying_symbol("NSE:NIFTY26NOV24500PE") == "NIFTY"
    assert app_mod._extract_underlying_symbol("BANKNIFTY 27 NOV 52000 CE") == "BANKNIFTY"
    assert app_mod._extract_underlying_symbol("NSE:RELIANCE-EQ") == "RELIANCE"
    assert app_mod._extract_underlying_symbol("SBIN") == "SBIN"


def test_dhan_snapshot_enrichment_and_controls():
    src = DhanDataSource()
    pos_raw = [{
        "securityId": "837001",
        "tradingSymbol": "SENSEX-Dec2026-83700-CE",
        "exchangeSegment": "BSE_FNO",
        "productType": "INTRADAY",
        "netQty": 20,
        "buyQty": 20,
        "sellQty": 0,
        "buyAvg": 250.0,
        "sellAvg": 0.0,
        "lastTradedPrice": 275.0,
        "realizedProfit": 0.0,
        "unrealizedProfit": 500.0,
    }]
    ord_raw = [{
        "orderId": "ORD123",
        "securityId": "837001",
        "tradingSymbol": "SENSEX-Dec2026-83700-CE",
        "exchangeSegment": "BSE_FNO",
        "transactionType": "BUY",
        "quantity": 20,
        "filledQty": 0,
        "price": 0,
        "triggerPrice": 0,
        "orderType": "MARKET",
        "productType": "INTRADAY",
        "orderStatus": "REJECTED",
        "omsErrorDescription": "RMS: Please add Rs.15312.74 to trade.",
        "updateTime": "2026-11-27 12:01:45",
    }]
    with patch("backend.sources.dhan.jdhan.is_connected", return_value=True), \
         patch("backend.sources.dhan.jdhan.fetch_funds", return_value={"availabelBalance": 10000, "utilizedAmount": 5000, "sodLimit": 15000}), \
         patch("backend.sources.dhan.jdhan.fetch_positions", return_value=pos_raw), \
         patch("backend.sources.dhan.jdhan.fetch_orders", return_value=ord_raw), \
         patch("backend.sources.dhan.jdhan.fetch_today_trades", return_value=[]), \
         patch("backend.sources.dhan.jdhan.fetch_holdings", return_value=[]), \
         patch("backend.sources.dhan.jdhan.fetch_killswitch", return_value={"killSwitchStatus": "DEACTIVATE"}), \
         patch("backend.sources.dhan.jdhan.fetch_pnl_exit", return_value={}):
        snap = src.get_trade_snapshot()

    p = snap["positions"][0]
    assert p["capital_used"] == 5000.0
    assert p["pnl"] == 500.0
    assert p["pnl_pct"] == 10.0
    assert p["points"] == 25.0
    assert p["move_pct"] == 10.0
    assert p["security_id"] == "837001"
    assert p["exchange_segment"] == "BSE_FNO"
    assert snap["orders"][0]["ltp"] == 275.0
    assert snap["orders"][0]["capital_used"] == 5500.0
    assert snap["controls"]["can_exit_single"] is True
    assert snap["controls"]["can_reverse_position"] is True
    assert snap["controls"]["can_set_sl_target"] is True


def test_dhan_reverse_and_sl_target():
    src = DhanDataSource()
    pos_raw = [{
        "securityId": "837001",
        "tradingSymbol": "SENSEX-Dec2026-83700-CE",
        "exchangeSegment": "BSE_FNO",
        "productType": "INTRADAY",
        "netQty": 20,
    }]
    with patch("backend.sources.dhan.jdhan.fetch_positions", return_value=pos_raw), \
         patch("backend.sources.dhan.jdhan.place_order", return_value={"orderId": "O1"}) as m_place:
        rev = src.reverse_position(pos_id="837001")
        assert rev["side"] == "SELL"
        assert rev["qty"] == 40
        assert m_place.call_args.kwargs["quantity"] == 40

        m_place.reset_mock()
        sltp = src.place_sl_target(pos_id="837001", target_price=320.0, sl_price=210.0, trailing_jump=5.0)
        assert len(sltp["placed"]) == 2
        assert sltp["placed"][0]["leg"] == "TARGET"
        assert sltp["placed"][1]["leg"] == "SL"
        assert "Trailing Jump" in sltp["note"]


def test_fyers_reverse_and_sl_target():
    src = FyersDataSource()
    fy = MagicMock()
    fy.positions.return_value = {
        "s": "ok",
        "netPositions": [{
            "id": "NSE:NIFTY26NOV24500CE-INTRADAY",
            "symbol": "NSE:NIFTY26NOV24500CE",
            "netQty": -50,
            "productType": "INTRADAY",
        }],
    }
    fy.place_order.return_value = {"s": "ok", "id": "FY123"}
    with patch.object(src, "_model", return_value=fy):
        rev = src.reverse_position(pos_id="NSE:NIFTY26NOV24500CE-INTRADAY")
        assert rev["side"] == "BUY"
        assert rev["qty"] == 100
        assert fy.place_order.call_args.kwargs["data"]["side"] == 1

        fy.place_order.reset_mock()
        sltp = src.place_sl_target(symbol="NSE:NIFTY26NOV24500CE", target_price=80.0, sl_price=145.0)
        assert len(sltp["placed"]) == 2
        assert fy.place_order.call_count == 2


def test_trademode_api_routes():
    app_mod = _import_app_isolated()
    fake_src = MagicMock()
    fake_src.name = "dhan"
    fake_src.get_live_quote.return_value = {"ltp": 215.5, "open": 200.0, "high": 225.0, "low": 195.0, "close": 210.0}
    fake_src.reverse_position.return_value = {"action": "REVERSE", "side": "SELL", "qty": 40}
    fake_src.place_sl_target.return_value = {"placed": [{"leg": "TARGET", "price": 260.0}], "note": ""}
    fake_src.modify_order.return_value = {"orderId": "O99", "orderStatus": "TRANSIT"}

    with patch.object(app_mod, "_source", fake_src), \
         patch.object(app_mod, "_yahoo_intraday", return_value={"symbol": "SENSEX", "points": [{"ts": 1, "t": "09:15", "o": 83000, "h": 83100, "l": 82950, "c": 83050}, {"ts": 301, "t": "09:20", "o": 83050, "h": 83120, "l": 83010, "c": 83090}], "pivots": {"p": 83000}}), \
         patch.object(app_mod, "_dhan_has_data_plan", return_value=False), \
         patch.object(app_mod, "_fetch_public_option_data", return_value={
             "expired": False, "expiry": "2099-12-31", "contract_id": "SENSEX26D0383700CE",
             "candles": [{"ts": 1, "t": "09:15", "o": 500, "h": 520, "l": 495, "c": 515}, {"ts": 301, "t": "09:20", "o": 515, "h": 530, "l": 510, "c": 525}],
             "quote": {"ltp": 525.0},
         }):
        d1 = app_mod.api_trademode_inspect(
            symbol="SENSEX-Dec2026-83700-CE", date="2026-11-27",
            timeframe="5", sec_id="822645", exchange_segment="BSE_FNO", expiry="2099-12-31",
        )
        assert d1["underlying_symbol"] == "SENSEX"
        assert d1["is_expired"] is False
        assert len(d1["candles"]) == 2
        assert d1["quote"]["ltp"] == 215.5
        assert len(d1["underlying"]["points"]) == 2

    with patch.object(app_mod, "_source", fake_src), \
         patch.object(app_mod, "_yahoo_intraday", return_value={"symbol": "SENSEX", "points": [{"ts": 1, "t": "09:15", "o": 83000, "h": 83100, "l": 82950, "c": 83050}, {"ts": 301, "t": "09:20", "o": 83050, "h": 83120, "l": 83010, "c": 83090}], "pivots": {"p": 83000}}), \
         patch.object(app_mod, "_dhan_has_data_plan", return_value=False), \
         patch.object(app_mod, "_fetch_public_option_data", return_value={"expired": True, "expiry": "2020-01-01", "candles": [], "quote": None}):
        d_exp = app_mod.api_trademode_inspect(
            symbol="SENSEX-Jan2020-83700-CE", date="2020-01-02",
            timeframe="5", sec_id="822645", exchange_segment="BSE_FNO", expiry="2020-01-01",
        )
        assert d_exp["is_expired"] is True
        assert d_exp["candles"] == []
        assert len(d_exp["underlying"]["points"]) == 2

    spec_active = app_mod._parse_option_spec("SENSEX-Dec2026-83700-CE", expiry_hint="2099-12-03")
    assert spec_active["is_expired"] is False and spec_active["strike"] == "83700" and spec_active["opt_type"] == "CE"
    spec_old = app_mod._parse_option_spec("NIFTY 19 OCT 23600 CALL", expiry_hint="2020-10-19")
    assert spec_old["is_expired"] is True

    with patch.object(app_mod, "_source", fake_src):
        d2 = asyncio.run(app_mod.api_trademode_reverse_position(_FakeReq({"id": "837001", "symbol": "SENSEX-Dec2026-83700-CE"})))
        assert d2["result"]["qty"] == 40

        d3 = asyncio.run(app_mod.api_trademode_sl_target(_FakeReq({"id": "837001", "target_price": 260})))
        assert d3["ok"] is True

        d4 = asyncio.run(app_mod.api_trademode_modify_order(_FakeReq({"id": "O99", "order_type": "LIMIT", "qty": 20, "price": 210})))
        assert d4["ok"] is True
