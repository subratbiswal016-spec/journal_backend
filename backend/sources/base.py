"""Common data-source interface and shared data shapes.

Any data source (NSE public endpoint, Upstox, etc.) returns an `OptionChain`
with the same normalised shape, so the analytics and API layers never need to
know where the data came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class AuthRequired(Exception):
    """Raised by a source when the user must log in before data can be fetched
    (e.g. Upstox has no valid access token for today)."""


@dataclass
class StrikeRow:
    strike: float
    # Call side
    ce_oi: float = 0.0
    ce_chg_oi: float = 0.0
    ce_ltp: float = 0.0
    ce_iv: float = 0.0
    ce_volume: float = 0.0
    # Put side
    pe_oi: float = 0.0
    pe_chg_oi: float = 0.0
    pe_ltp: float = 0.0
    pe_iv: float = 0.0
    pe_volume: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class FuturesRow:
    expiry: str
    last_price: float = 0.0
    oi: float = 0.0
    chg_oi: float = 0.0
    volume: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OptionChain:
    symbol: str
    spot: float
    expiry: str
    expiries: list[str]
    timestamp: str
    rows: list[StrikeRow] = field(default_factory=list)
    futures: list[FuturesRow] = field(default_factory=list)
    source: str = "nse"

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "spot": self.spot,
            "expiry": self.expiry,
            "expiries": self.expiries,
            "timestamp": self.timestamp,
            "source": self.source,
            "rows": [r.as_dict() for r in self.rows],
            "futures": [f.as_dict() for f in self.futures],
        }


class DataSource:
    """Interface every data source implements."""

    name = "base"
    login_label = "Log in"  # shown on the login button for sources that need it

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        raise NotImplementedError

    # --- auth (only meaningful for sources that log in, e.g. Upstox/Fyers) ---
    def needs_login(self) -> bool:
        return False

    def login_url(self, state: str = "") -> Optional[str]:
        return None

    def exchange_code(self, code: str) -> None:
        raise NotImplementedError
