"""DhanHQ v2 client — pulls the user's dated trade history for the journal.

Auth: a 24h access token the user generates at web.dhan.co (My Profile → Access
DhanHQ APIs). Sent as the `access-token` header. Fetching trades needs NO static
IP whitelisting (only order placement does), so it works from a home connection.

Trade history (dated):  GET /v2/trades/{from-date}/{to-date}/{page}
  dates YYYY-MM-DD, page 0-indexed; paginate until an empty array.
"""
from __future__ import annotations

import requests

from .. import config
from ..sources.base import AuthRequired
from . import store

BASE = "https://api.dhan.co/v2"


def get_creds() -> tuple[str, str]:
    c = store.get_setting("dhan_creds", {}) or {}
    cid = (c.get("client_id") or config.DHAN_CLIENT_ID or "").strip()
    tok = (c.get("access_token") or config.DHAN_ACCESS_TOKEN or "").strip()
    return cid, tok


def set_creds(client_id: str, access_token: str) -> None:
    store.set_setting("dhan_creds", {
        "client_id": (client_id or "").strip(),
        "access_token": (access_token or "").strip(),
    })


def clear_creds() -> None:
    store.set_setting("dhan_creds", {})


def is_connected() -> bool:
    # Reading trades only needs the access token; client id is optional.
    _, tok = get_creds()
    return bool(tok)


def _is_auth_err(status_code: int, text: str) -> bool:
    if status_code in (401, 403):
        return True
    u = (text or "").upper()
    return any(k in u for k in ("DH-901", "DH-906", "INVALID TOKEN", "TOKEN EXPIRED", "UNAUTHORIZED"))


def fetch_profile() -> dict:
    """Validate token and return Dhan user profile (dhanClientId, tokenValidity, etc.)."""
    _, tok = get_creds()
    if not tok:
        raise AuthRequired("Connect your Dhan account first (paste your access token).")
    try:
        r = requests.get(f"{BASE}/profile", headers=_headers(tok), timeout=20)
    except requests.RequestException as e:
        raise RuntimeError(f"Dhan profile request failed: {e}")
    if _is_auth_err(r.status_code, r.text):
        raise AuthRequired("Dhan token invalid or expired. Generate a fresh token at web.dhan.co.")
    if r.status_code != 200:
        raise RuntimeError(f"Dhan HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    return body if isinstance(body, dict) else {}


def fetch_funds() -> dict:
    """Best-effort Dhan fund limit (sodLimit, availabelBalance, utilizedAmount)."""
    _, tok = get_creds()
    if not tok:
        return {}
    try:
        r = requests.get(f"{BASE}/fundlimit", headers=_headers(tok), timeout=10)
        if _is_auth_err(r.status_code, r.text):
            raise AuthRequired("Dhan token invalid or expired. Generate a fresh token at web.dhan.co.")
        if r.status_code != 200:
            return {}
        body = r.json()
        return body if isinstance(body, dict) else {}
    except AuthRequired:
        raise
    except Exception:  # noqa: BLE001
        return {}


def _client_id() -> str:
    cid, _ = get_creds()
    if cid:
        return cid
    try:
        cid = str((fetch_profile() or {}).get("dhanClientId") or "").strip()
    except Exception:  # noqa: BLE001
        cid = ""
    return cid


def _dhan_err_msg(r) -> str:
    try:
        j = r.json()
        if isinstance(j, dict):
            msg = j.get("errorMessage") or j.get("message") or j.get("remarks") or ""
            code = j.get("errorCode") or ""
            if msg or code:
                return f"{code}: {msg}".strip(": ")
    except Exception:  # noqa: BLE001
        pass
    return (r.text or "")[:200]


def _dhan_req(method: str, path: str, **kwargs) -> dict | list:
    _, tok = get_creds()
    if not tok:
        raise AuthRequired("Connect your Dhan account first.")
    try:
        r = requests.request(method, f"{BASE}{path}", headers=_headers(tok), timeout=15, **kwargs)
    except requests.RequestException as e:
        raise RuntimeError(f"Dhan request failed: {e}")
    if _is_auth_err(r.status_code, r.text):
        raise AuthRequired("Dhan token invalid or expired. Generate a fresh token at web.dhan.co.")
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"Dhan HTTP {r.status_code}: {_dhan_err_msg(r)}")
    try:
        return r.json()
    except ValueError:
        return {"ok": True}


def _dhan_list(path: str, **kwargs) -> list[dict]:
    body = _dhan_req("GET", path, **kwargs)
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        return body.get("data") or []
    return []


def fetch_positions() -> list[dict]:
    return _dhan_list("/positions")


def fetch_orders() -> list[dict]:
    return _dhan_list("/orders")


def fetch_today_trades() -> list[dict]:
    return _dhan_list("/trades")


def fetch_holdings() -> list[dict]:
    return _dhan_list("/holdings")


def fetch_ledger(from_date: str, to_date: str) -> list[dict]:
    return _dhan_list("/ledger", params={"from-date": from_date, "to-date": to_date})


def fetch_killswitch() -> dict:
    try:
        b = _dhan_req("GET", "/killswitch")
        return b if isinstance(b, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def set_killswitch(status: str) -> dict:
    st = "ACTIVATE" if str(status).upper() == "ACTIVATE" else "DEACTIVATE"
    b = _dhan_req("POST", f"/killswitch?killSwitchStatus={st}")
    return b if isinstance(b, dict) else {"killSwitchStatus": st}


def fetch_pnl_exit() -> dict:
    try:
        b = _dhan_req("GET", "/pnlExit")
        return b if isinstance(b, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def set_pnl_exit(profit_value: float, loss_value: float, enable_kill_switch: bool = False) -> dict:
    cid, _ = get_creds()
    if not cid:
        try:
            cid = str((fetch_profile() or {}).get("dhanClientId") or "")
        except Exception:  # noqa: BLE001
            cid = ""
    payload = {
        "dhanClientId": cid,
        "profitValue": float(profit_value or 0),
        "lossValue": abs(float(loss_value or 0)),
        "productType": ["INTRADAY", "MARGIN", "CNC"],
        "enableKillSwitch": bool(enable_kill_switch),
    }
    b = _dhan_req("POST", "/pnlExit", json=payload)
    return b if isinstance(b, dict) else {"ok": True}


def clear_pnl_exit() -> dict:
    b = _dhan_req("DELETE", "/pnlExit")
    return b if isinstance(b, dict) else {"ok": True}


def cancel_order(order_id: str) -> dict:
    b = _dhan_req("DELETE", f"/orders/{order_id}")
    return b if isinstance(b, dict) else {"ok": True}


def exit_all_positions() -> dict:
    b = _dhan_req("DELETE", "/positions")
    return b if isinstance(b, dict) else {"ok": True}


def place_order(
    *,
    security_id: str,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    product_type: str = "INTRADAY",
    order_type: str = "MARKET",
    price: float = 0.0,
    trigger_price: float = 0.0,
    validity: str = "DAY",
    disclosed_quantity: int = 0,
    correlation_id: str = "",
) -> dict:
    """Place a single order on DhanHQ v2 (`POST /v2/orders`)."""
    cid = _client_id()
    ot = str(order_type or "MARKET").upper()
    payload = {
        "dhanClientId": cid,
        "transactionType": str(transaction_type or "BUY").upper(),
        "exchangeSegment": str(exchange_segment or "NSE_EQ"),
        "productType": str(product_type or "INTRADAY").upper(),
        "orderType": ot,
        "validity": str(validity or "DAY").upper(),
        "securityId": str(security_id),
        "quantity": int(quantity),
        "disclosedQuantity": int(disclosed_quantity or 0),
        "price": float(price or 0) if ot in ("LIMIT", "STOP_LOSS") else 0.0,
        "triggerPrice": float(trigger_price or 0) if ot in ("STOP_LOSS", "STOP_LOSS_MARKET") else 0.0,
        "afterMarketOrder": False,
    }
    if correlation_id:
        payload["correlationId"] = correlation_id[:30]
    b = _dhan_req("POST", "/orders", json=payload)
    return b if isinstance(b, dict) else {"ok": True}


def place_super_order(
    *,
    security_id: str,
    exchange_segment: str,
    transaction_type: str,
    quantity: int,
    product_type: str = "INTRADAY",
    order_type: str = "MARKET",
    price: float = 0.0,
    target_price: float = 0.0,
    sl_price: float = 0.0,
    trailing_jump: float = 0.0,
    correlation_id: str = "",
) -> dict:
    """Place a DhanHQ v2 Super Order (`POST /v2/super/orders`) with Target, SL, and optional Trailing Jump."""
    cid = _client_id()
    payload: dict = {
        "dhanClientId": cid,
        "transactionType": str(transaction_type or "BUY").upper(),
        "exchangeSegment": str(exchange_segment or "NSE_EQ"),
        "productType": str(product_type or "INTRADAY").upper(),
        "orderType": "LIMIT" if str(order_type).upper() == "LIMIT" else "MARKET",
        "securityId": str(security_id),
        "quantity": int(quantity),
        "price": float(price or 0) if str(order_type).upper() == "LIMIT" else 0.0,
    }
    if target_price > 0:
        payload["targetPrice"] = float(target_price)
    if sl_price > 0:
        payload["stopLossPrice"] = float(sl_price)
    if trailing_jump > 0:
        payload["trailingJump"] = float(trailing_jump)
    if correlation_id:
        payload["correlationId"] = correlation_id[:30]
    b = _dhan_req("POST", "/super/orders", json=payload)
    return b if isinstance(b, dict) else {"ok": True}


def modify_order(
    *,
    order_id: str,
    order_type: str = "LIMIT",
    quantity: int = 1,
    price: float = 0.0,
    trigger_price: float = 0.0,
    validity: str = "DAY",
    leg_name: str = "NA",
) -> dict:
    """Modify a pending DhanHQ v2 order (`PUT /v2/orders/{orderId}`)."""
    cid = _client_id()
    ot = str(order_type or "LIMIT").upper()
    payload = {
        "dhanClientId": cid,
        "orderId": str(order_id),
        "orderType": ot,
        "quantity": int(quantity),
        "price": float(price or 0) if ot in ("LIMIT", "STOP_LOSS") else 0.0,
        "disclosedQuantity": 0,
        "triggerPrice": float(trigger_price or 0) if ot in ("STOP_LOSS", "STOP_LOSS_MARKET") else 0.0,
        "validity": str(validity or "DAY").upper(),
        "legName": leg_name or "NA",
    }
    b = _dhan_req("PUT", f"/orders/{order_id}", json=payload)
    return b if isinstance(b, dict) else {"ok": True}


def fetch_market_quote(exchange_segment: str, security_id: str) -> dict | None:
    """Best-effort REST snapshot (LTP + day OHLC) via `/v2/marketfeed/ohlc` or `/v2/marketfeed/ltp`."""
    _, tok = get_creds()
    cid = _client_id()
    if not tok or not cid or not exchange_segment or not security_id:
        return None
    try:
        sid = int(security_id)
    except ValueError:
        return None
    headers = {"access-token": tok, "client-id": cid, "Accept": "application/json", "Content-Type": "application/json"}
    for ep in ("/marketfeed/ohlc", "/marketfeed/ltp"):
        try:
            r = requests.post(f"{BASE}{ep}", headers=headers, json={exchange_segment: [sid]}, timeout=6)
            if r.status_code != 200:
                continue
            seg_data = ((r.json() or {}).get("data") or {}).get(exchange_segment) or {}
            item = seg_data.get(str(sid)) or seg_data.get(sid)
            if isinstance(item, dict) and item.get("last_price") is not None:
                ohlc = item.get("ohlc") or {}
                return {
                    "ltp": round(float(item.get("last_price") or 0), 2),
                    "open": round(float(ohlc.get("open") or 0), 2) if ohlc.get("open") is not None else None,
                    "high": round(float(ohlc.get("high") or 0), 2) if ohlc.get("high") is not None else None,
                    "low": round(float(ohlc.get("low") or 0), 2) if ohlc.get("low") is not None else None,
                    "close": round(float(ohlc.get("close") or 0), 2) if ohlc.get("close") is not None else None,
                }
        except Exception:  # noqa: BLE001
            continue
    return None


def fetch_intraday_candles(
    security_id: str,
    exchange_segment: str,
    instrument: str,
    from_date: str,
    to_date: str,
    interval: str = "5",
) -> list[dict]:
    """Dhan `/v2/charts/intraday` (available when user has an active Data API subscription)."""
    _, tok = get_creds()
    if not tok or not security_id or not exchange_segment:
        return []
    try:
        r = requests.post(
            f"{BASE}/charts/intraday",
            headers=_headers(tok),
            json={
                "securityId": str(security_id),
                "exchangeSegment": str(exchange_segment),
                "instrument": str(instrument),
                "interval": str(interval),
                "oi": False,
                "fromDate": from_date,
                "toDate": to_date,
            },
            timeout=8,
        )
        if r.status_code != 200:
            return []
        body = r.json() or {}
        ts = body.get("timestamp") or []
        os_, hs, ls, cs = (body.get(k) or [] for k in ("open", "high", "low", "close"))
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        out = []
        for t0, o0, h0, l0, c0 in zip(ts, os_, hs, ls, cs):
            if None in (o0, h0, l0, c0):
                continue
            tsi = int(t0)
            dt = datetime.fromtimestamp(tsi, ist)
            out.append({
                "ts": tsi,
                "t": dt.strftime("%H:%M"),
                "o": round(float(o0), 2),
                "h": round(float(h0), 2),
                "l": round(float(l0), 2),
                "c": round(float(c0), 2),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


def _headers(tok: str) -> dict:
    return {"access-token": tok, "Accept": "application/json", "Content-Type": "application/json"}


def fetch_trade_history(from_date: str, to_date: str) -> list[dict]:
    """All executed trades between from_date and to_date (inclusive), paginated."""
    _, tok = get_creds()
    if not tok:
        raise AuthRequired("Connect your Dhan account first (paste your access token).")

    def _key(t: dict) -> tuple:
        # exchangeTradeId is often "0" for all trades, so key on a full composite.
        return (t.get("orderId"), t.get("exchangeOrderId"), t.get("exchangeTime"),
                t.get("securityId"), t.get("tradedQuantity"), t.get("tradedPrice"),
                t.get("transactionType"))

    out: list[dict] = []
    seen: set[tuple] = set()
    for page in range(0, 400):  # safety cap; the empty-page break ends it normally
        url = f"{BASE}/trades/{from_date}/{to_date}/{page}"
        try:
            r = requests.get(url, headers=_headers(tok), timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"Dhan request failed: {e}")
        if _is_auth_err(r.status_code, r.text):
            raise AuthRequired("Dhan token invalid or expired (DH-901). "
                               "Generate a fresh token at web.dhan.co and reconnect.")
        if r.status_code != 200:
            raise RuntimeError(f"Dhan HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        rows = body if isinstance(body, list) else (body.get("data") or [])
        if not rows:
            break
        new = [t for t in rows if _key(t) not in seen]
        if not new:
            break  # genuine repeat (API returning same data past the end)
        for t in new:
            seen.add(_key(t))
        out.extend(new)
    return out
