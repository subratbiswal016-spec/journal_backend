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


def is_connected() -> bool:
    # Reading trades only needs the access token; client id is optional.
    _, tok = get_creds()
    return bool(tok)


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
        if r.status_code in (401, 403):
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
