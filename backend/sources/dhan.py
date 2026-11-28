"""DhanHQ data source — connects Dhan for journal sync & profile, delegating
option-chain OI to NSE (with mock fallback) so Market view stays usable."""
from __future__ import annotations

from typing import Optional

from ..journal import dhan as jdhan
from .base import AuthRequired, DataSource, OptionChain
from .mock import MockDataSource
from .nse import NSEDataSource
from . import yahoo_feed as _yf


class DhanDataSource(DataSource):
    name = "dhan"
    login_label = "Connect Dhan"
    supports_trade_mode = True

    def __init__(self) -> None:
        self._profile: Optional[dict] = None
        self._profile_token: Optional[str] = None
        self._nse = NSEDataSource()
        self._mock = MockDataSource()

    def needs_login(self) -> bool:
        return not jdhan.is_connected()

    def logout(self) -> None:
        jdhan.clear_creds()
        self._profile = None
        self._profile_token = None

    def get_profile(self) -> Optional[dict]:
        cid_saved, tok = jdhan.get_creds()
        if not tok:
            raise AuthRequired("Connect your Dhan account first.")
        if self._profile and self._profile_token == tok:
            return self._profile
        info = jdhan.fetch_profile()
        cid = (info.get("dhanClientId") or cid_saved or "").strip()
        details = []
        if cid:
            details.append(["Client ID", cid])
        if info.get("activeSegment"):
            details.append(["Segments", str(info["activeSegment"])])
        if info.get("dataPlan"):
            dv = info.get("dataValidity")
            details.append(["Data Plan", f"{info['dataPlan']}" + (f" ({dv})" if dv and dv != "NA" else "")])
        if info.get("ddpi"):
            details.append(["DDPI", str(info["ddpi"])])
        if info.get("mtf"):
            details.append(["MTF", str(info["mtf"])])
        if info.get("tokenValidity"):
            details.append(["Token Valid", str(info["tokenValidity"])])
        prof = {
            "name": f"Dhan {cid}" if cid else "Dhan Account",
            "user_id": cid,
            "email": "",
            "avatar": "",
            "broker": "Dhan",
            "details": details,
        }
        self._profile = prof
        self._profile_token = tok
        return prof

    def get_funds(self) -> Optional[dict]:
        if not jdhan.is_connected():
            return None
        f = jdhan.fetch_funds()
        if not f:
            return None
        avail = float(f.get("availabelBalance") or f.get("availableBalance") or 0)
        util = float(f.get("utilizedAmount") or 0)
        sod = float(f.get("sodLimit") or 0)
        total = round(avail + util, 2)
        opening = round(sod if sod > 0 else total, 2)
        return {"opening": opening, "available": round(avail, 2), "total": total}

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        try:
            return self._nse.get_option_chain(symbol, expiry)
        except Exception:  # noqa: BLE001 - fallback if NSE blocks datacenter IP
            return self._mock.get_option_chain(symbol, expiry)

    fetch_one_quote = staticmethod(_yf.fetch_one_quote)
    fetch_batch_quotes = staticmethod(_yf.fetch_batch_quotes)
    fetch_index_quotes = staticmethod(_yf.fetch_index_quotes)
    fetch_history = staticmethod(_yf.fetch_history)

    # ------------------------------------------------------------ Trade Mode
    def get_trade_snapshot(self) -> dict:
        if not jdhan.is_connected():
            raise AuthRequired("Connect your Dhan account first.")
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
        f_raw = jdhan.fetch_funds() or {}
        avail = float(f_raw.get("availabelBalance") or f_raw.get("availableBalance") or 0)
        util = float(f_raw.get("utilizedAmount") or 0)
        sod = float(f_raw.get("sodLimit") or 0)
        total_bal = round(avail + util, 2)

        def _safe(fn, default):
            try:
                return fn()
            except AuthRequired:
                raise
            except Exception:  # noqa: BLE001
                return default

        raw_pos = _safe(jdhan.fetch_positions, [])
        raw_ord = _safe(jdhan.fetch_orders, [])
        raw_trd = _safe(jdhan.fetch_today_trades, [])
        raw_hld = _safe(jdhan.fetch_holdings, [])
        ks = _safe(jdhan.fetch_killswitch, {})
        pnl_ex = _safe(jdhan.fetch_pnl_exit, {})

        positions, realized_tot, unrealized_tot, open_cnt = [], 0.0, 0.0, 0
        for p in raw_pos:
            nq = int(p.get("netQty") or 0)
            bq, sq = int(p.get("buyQty") or 0), int(p.get("sellQty") or 0)
            if nq == 0 and bq == 0 and sq == 0:
                continue
            r_pl = float(p.get("realizedProfit") or 0)
            u_pl = float(p.get("unrealizedProfit") or 0)
            pl = round(r_pl + u_pl, 2)
            realized_tot += r_pl
            unrealized_tot += u_pl
            if nq != 0:
                open_cnt += 1
            side = "LONG" if nq > 0 else ("SHORT" if nq < 0 else "CLOSED")
            b_avg = float(p.get("buyAvg") or p.get("costPrice") or 0)
            s_avg = float(p.get("sellAvg") or p.get("costPrice") or 0)
            avg = float((p.get("buyAvg") if nq > 0 else (p.get("sellAvg") if nq < 0 else (b_avg or s_avg))) or p.get("costPrice") or 0)
            ltp = float(p.get("lastTradedPrice") or p.get("costPrice") or avg)
            buy_val = round(float(p.get("dayBuyValue") or p.get("buyValue") or (bq * b_avg)), 2)
            sell_val = round(float(p.get("daySellValue") or p.get("sellValue") or (sq * s_avg)), 2)
            cap_used = round(abs(nq) * avg, 2) if nq != 0 else round(max(buy_val, sell_val, bq * b_avg, sq * s_avg), 2)
            pnl_pct = round((pl / cap_used) * 100.0, 2) if cap_used > 0 else 0.0
            pts = round((ltp - avg) if nq > 0 else ((avg - ltp) if nq < 0 else 0.0), 2)
            mv_pct = round((pts / avg) * 100.0, 2) if (avg > 0 and nq != 0) else 0.0
            sec_id = str(p.get("securityId") or "")
            ex_seg = str(p.get("exchangeSegment") or "")
            positions.append({
                "id": sec_id,
                "security_id": sec_id,
                "symbol": str(p.get("tradingSymbol") or ""),
                "exchange": ex_seg,
                "exchange_segment": ex_seg,
                "side": side,
                "product": str(p.get("productType") or ""),
                "net_qty": nq,
                "buy_qty": bq,
                "sell_qty": sq,
                "avg_price": round(avg, 2),
                "buy_avg": round(b_avg, 2),
                "sell_avg": round(s_avg, 2),
                "buy_value": buy_val,
                "sell_value": sell_val,
                "capital_used": cap_used,
                "ltp": round(ltp, 2),
                "points": pts,
                "move_pct": mv_pct,
                "pnl_pct": pnl_pct,
                "realized": round(r_pl, 2),
                "unrealized": round(u_pl, 2),
                "pnl": pl,
                "is_open": nq != 0,
                "multiplier": int(p.get("multiplier") or 1),
                "drv_option_type": str(p.get("drvOptionType") or ""),
                "drv_strike": float(p.get("drvStrikePrice") or 0),
                "drv_expiry": str(p.get("drvExpiryDate") or ""),
            })

        pos_ltp = {p["symbol"]: p["ltp"] for p in positions if p.get("ltp")}
        orders = []
        pending_cnt = 0
        for o in raw_ord:
            st = str(o.get("orderStatus") or "").upper()
            pend = st in ("PENDING", "TRANSIT", "PART_TRADED")
            if pend:
                pending_cnt += 1
            sym_o = str(o.get("tradingSymbol") or "")
            qty_o = int(o.get("quantity") or 0)
            px_o = round(float(o.get("price") or o.get("tradedPrice") or 0), 2)
            stp_o = round(float(o.get("triggerPrice") or 0), 2)
            ltp_o = pos_ltp.get(sym_o, 0.0)
            ref_px = px_o or stp_o or ltp_o
            orders.append({
                "id": str(o.get("orderId") or ""),
                "security_id": str(o.get("securityId") or ""),
                "symbol": sym_o,
                "exchange_segment": str(o.get("exchangeSegment") or ""),
                "side": str(o.get("transactionType") or "").upper(),
                "qty": qty_o,
                "filled_qty": int(o.get("filledQty") or 0),
                "price": px_o,
                "stop_price": stp_o,
                "ltp": round(ltp_o, 2),
                "capital_used": round(qty_o * ref_px, 2),
                "order_type": str(o.get("orderType") or ""),
                "product": str(o.get("productType") or ""),
                "validity": str(o.get("validity") or "DAY"),
                "take_profit": float(o.get("boProfitValue") or 0),
                "stop_loss": float(o.get("boStopLossValue") or 0),
                "status": st,
                "message": str(o.get("omsErrorDescription") or ""),
                "time": str(o.get("updateTime") or o.get("createTime") or ""),
                "is_pending": pend,
                "can_modify": pend,
                "drv_option_type": str(o.get("drvOptionType") or ""),
                "drv_strike": float(o.get("drvStrikePrice") or 0),
                "drv_expiry": str(o.get("drvExpiryDate") or ""),
            })

        trades = []
        for tr in raw_trd:
            q = int(tr.get("tradedQuantity") or 0)
            px = float(tr.get("tradedPrice") or 0)
            trades.append({
                "id": str(tr.get("exchangeTradeId") or tr.get("orderId") or ""),
                "order_id": str(tr.get("orderId") or ""),
                "symbol": str(tr.get("tradingSymbol") or ""),
                "side": str(tr.get("transactionType") or "").upper(),
                "qty": q,
                "price": round(px, 2),
                "value": round(q * px, 2),
                "product": str(tr.get("productType") or ""),
                "time": str(tr.get("exchangeTime") or tr.get("updateTime") or ""),
            })

        holdings = []
        hld_val, hld_pnl = 0.0, 0.0
        for h in raw_hld:
            q = int(h.get("totalQty") or 0)
            avg = float(h.get("avgCostPrice") or 0)
            ltp = float(h.get("lastTradedPrice") or avg)
            val = round(q * ltp, 2)
            pl = round((ltp - avg) * q, 2) if avg and ltp else 0.0
            hld_val += val
            hld_pnl += pl
            holdings.append({
                "symbol": str(h.get("tradingSymbol") or ""),
                "qty": q,
                "avg_price": round(avg, 2),
                "ltp": round(ltp, 2),
                "value": val,
                "pnl": pl,
            })

        tot_pnl = round(realized_tot + unrealized_tot, 2)
        return {
            "supported": True,
            "source": "dhan",
            "broker_label": "Dhan Free Trading API",
            "today": today,
            "pnl": {
                "total": tot_pnl,
                "realized": round(realized_tot, 2),
                "unrealized": round(unrealized_tot, 2),
                "holdings_pnl": round(hld_pnl, 2),
            },
            "funds": {
                "available": round(avail, 2),
                "utilized": round(util, 2),
                "total": total_bal,
                "sod": round(sod, 2),
                "withdrawable": round(float(f_raw.get("withdrawableBalance") or 0), 2),
                "holdings_value": round(hld_val, 2),
            },
            "counts": {
                "open_positions": open_cnt,
                "total_positions": len(positions),
                "orders": len(orders),
                "pending_orders": pending_cnt,
                "trades": len(trades),
                "holdings": len(holdings),
            },
            "positions": positions,
            "orders": orders,
            "trades": trades,
            "holdings": holdings,
            "controls": {
                "can_exit_all": True,
                "can_exit_single": True,
                "can_reverse_position": True,
                "can_set_sl_target": True,
                "can_cancel_order": True,
                "can_modify_order": True,
                "can_place_order": True,
                "can_killswitch": True,
                "can_pnl_exit": True,
                "kill_switch": str(ks.get("killSwitchStatus") or "INACTIVE").upper(),
                "kill_switch_active": str(ks.get("killSwitchStatus") or "").upper() in ("ACTIVE", "ACTIVATE", "ACTIVATED"),
                "pnl_exit": pnl_ex,
            },
        }

    def get_trade_reports(self) -> dict:
        if not jdhan.is_connected():
            raise AuthRequired("Connect your Dhan account first.")
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        to_d = datetime.now(ist).strftime("%Y-%m-%d")
        frm_d = (datetime.now(ist) - timedelta(days=30)).strftime("%Y-%m-%d")
        try:
            ledger = jdhan.fetch_ledger(frm_d, to_d)
        except Exception:  # noqa: BLE001
            ledger = []
        return {"supported": True, "source": "dhan", "charges": [], "realised": [], "ledger": ledger[:80]}

    def _find_open_position(self, pos_id: Optional[str] = None, symbol: Optional[str] = None,
                            security_id: Optional[str] = None) -> dict:
        for p in jdhan.fetch_positions():
            nq = int(p.get("netQty") or 0)
            if nq == 0:
                continue
            sid = str(p.get("securityId") or "")
            sym = str(p.get("tradingSymbol") or "")
            if (security_id and sid == str(security_id)) or (pos_id and sid == str(pos_id)) or (symbol and sym == str(symbol)):
                return p
        raise ValueError(f"No open Dhan position found for {symbol or security_id or pos_id}")

    def cancel_order(self, order_id: str) -> dict:
        return jdhan.cancel_order(order_id)

    def exit_position(self, pos_id: Optional[str] = None, symbol: Optional[str] = None,
                      security_id: Optional[str] = None, **_kw) -> dict:
        if not (pos_id or symbol or security_id):
            return jdhan.exit_all_positions()
        p = self._find_open_position(pos_id=pos_id, symbol=symbol, security_id=security_id)
        nq = int(p.get("netQty") or 0)
        return jdhan.place_order(
            security_id=str(p["securityId"]),
            exchange_segment=str(p["exchangeSegment"]),
            transaction_type="SELL" if nq > 0 else "BUY",
            quantity=abs(nq),
            product_type=str(p.get("productType") or "INTRADAY"),
            order_type="MARKET",
        )

    def reverse_position(self, pos_id: Optional[str] = None, symbol: Optional[str] = None,
                         security_id: Optional[str] = None, qty: int = 0, **_kw) -> dict:
        p = self._find_open_position(pos_id=pos_id, symbol=symbol, security_id=security_id)
        nq = int(p.get("netQty") or 0)
        abs_q = abs(nq)
        target_q = int(qty) if qty and int(qty) > 0 else abs_q
        order_qty = abs_q + target_q
        tx = "SELL" if nq > 0 else "BUY"
        res = jdhan.place_order(
            security_id=str(p["securityId"]),
            exchange_segment=str(p["exchangeSegment"]),
            transaction_type=tx,
            quantity=order_qty,
            product_type=str(p.get("productType") or "INTRADAY"),
            order_type="MARKET",
        )
        return {"action": "REVERSE", "symbol": p.get("tradingSymbol"), "side": tx, "qty": order_qty, "order": res}

    def place_sl_target(self, *, pos_id: Optional[str] = None, symbol: Optional[str] = None,
                        security_id: Optional[str] = None, exchange_segment: Optional[str] = None,
                        product: Optional[str] = None, side: Optional[str] = None, qty: int = 0,
                        target_price: float = 0.0, sl_price: float = 0.0,
                        sl_limit_price: float = 0.0, trailing_jump: float = 0.0) -> dict:
        p = self._find_open_position(pos_id=pos_id, symbol=symbol, security_id=security_id)
        nq = int(p.get("netQty") or 0)
        exit_tx = "SELL" if nq > 0 else "BUY"
        exit_qty = int(qty) if qty and int(qty) > 0 else abs(nq)
        sid = str(p.get("securityId") or security_id or "")
        seg = str(p.get("exchangeSegment") or exchange_segment or "NSE_FNO")
        prod = str(p.get("productType") or product or "INTRADAY")
        if float(target_price or 0) <= 0 and float(sl_price or 0) <= 0:
            raise ValueError("Enter a Target price and/or Stop Loss price.")
        placed = []
        if float(target_price or 0) > 0:
            r_tp = jdhan.place_order(
                security_id=sid, exchange_segment=seg, transaction_type=exit_tx,
                quantity=exit_qty, product_type=prod, order_type="LIMIT",
                price=float(target_price),
            )
            placed.append({"leg": "TARGET", "price": float(target_price), "res": r_tp})
        if float(sl_price or 0) > 0:
            use_lim = float(sl_limit_price or 0) > 0
            r_sl = jdhan.place_order(
                security_id=sid, exchange_segment=seg, transaction_type=exit_tx,
                quantity=exit_qty, product_type=prod,
                order_type="STOP_LOSS" if use_lim else "STOP_LOSS_MARKET",
                price=float(sl_limit_price) if use_lim else 0.0,
                trigger_price=float(sl_price),
            )
            placed.append({"leg": "SL", "trigger_price": float(sl_price), "res": r_sl})
        note = ""
        if float(trailing_jump or 0) > 0:
            note = "Trailing Jump only applies on new Dhan Super Orders; Target/SL exit legs placed for open position."
        return {"placed": placed, "note": note}

    def modify_order(self, *, order_id: str, order_type: str = "LIMIT", qty: int = 1,
                     price: float = 0.0, stop_price: float = 0.0, validity: str = "DAY", **_kw) -> dict:
        ot_map = {"SL-M": "STOP_LOSS_MARKET", "SL-L": "STOP_LOSS", "SL": "STOP_LOSS"}
        ot = ot_map.get(str(order_type).upper(), str(order_type or "LIMIT").upper())
        return jdhan.modify_order(
            order_id=order_id, order_type=ot, quantity=int(qty),
            price=float(price or 0), trigger_price=float(stop_price or 0), validity=validity or "DAY",
        )

    def place_order(self, *, symbol: str = "", security_id: str = "", exchange_segment: str = "",
                    side: str = "BUY", qty: int = 1, product: str = "INTRADAY",
                    order_type: str = "MARKET", price: float = 0.0, stop_price: float = 0.0,
                    target_price: float = 0.0, sl_price: float = 0.0, trailing_jump: float = 0.0) -> dict:
        if not security_id:
            raise ValueError("Dhan security_id is required to place an order.")
        seg = exchange_segment or "NSE_FNO"
        if float(target_price or 0) > 0 or float(sl_price or 0) > 0:
            return jdhan.place_super_order(
                security_id=security_id, exchange_segment=seg,
                transaction_type=side, quantity=int(qty), product_type=product or "INTRADAY",
                order_type=order_type or "MARKET", price=float(price or 0),
                target_price=float(target_price or 0), sl_price=float(sl_price or 0),
                trailing_jump=float(trailing_jump or 0),
            )
        return jdhan.place_order(
            security_id=security_id, exchange_segment=seg,
            transaction_type=side, quantity=int(qty), product_type=product or "INTRADAY",
            order_type=order_type or "MARKET", price=float(price or 0), trigger_price=float(stop_price or 0),
        )

    def get_live_quote(self, symbol: str = "", security_id: str = "", exchange_segment: str = "") -> dict | None:
        if security_id and exchange_segment:
            return jdhan.fetch_market_quote(exchange_segment, security_id)
        return None

    def set_killswitch(self, status: str) -> dict:
        return jdhan.set_killswitch(status)

    def set_pnl_exit(self, profit_value: float, loss_value: float,
                     enable_kill_switch: bool = False, active: bool = True) -> dict:
        if not active:
            return jdhan.clear_pnl_exit()
        return jdhan.set_pnl_exit(profit_value, loss_value, enable_kill_switch)
