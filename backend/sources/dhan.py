"""DhanHQ data source — connects Dhan for journal sync & profile, delegating
option-chain OI to NSE (with mock fallback) so Market view stays usable."""
from __future__ import annotations

from typing import Optional

from ..journal import dhan as jdhan
from .base import AuthRequired, DataSource, OptionChain
from .mock import MockDataSource
from .nse import NSEDataSource


class DhanDataSource(DataSource):
    name = "dhan"
    login_label = "Connect Dhan"

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
        _, tok = jdhan.get_creds()
        if not tok:
            raise AuthRequired("Connect your Dhan account first.")
        if self._profile and self._profile_token == tok:
            return self._profile
        info = jdhan.fetch_profile()
        cid = (info.get("dhanClientId") or jdhan.get_creds()[0] or "").strip()
        prof = {
            "name": f"Dhan {cid}" if cid else "Dhan Trader",
            "user_id": cid,
            "email": "",
            "avatar": "",
            "broker": "Dhan",
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
