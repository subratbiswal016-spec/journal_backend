"""Data source layer: swap NSE / Fyers / Upstox / Dhan / Mock behind one interface."""
from typing import Optional

from .. import config
from .base import DataSource, OptionChain
from .nse import NSEDataSource


def get_source(name: Optional[str] = None) -> DataSource:
    """Factory: returns the configured (or requested) data source instance."""
    src = (name or config.DATA_SOURCE).strip().lower()
    if src == "upstox":
        from .upstox import UpstoxDataSource
        return UpstoxDataSource()
    if src == "fyers":
        from .fyers import FyersDataSource
        return FyersDataSource()
    if src == "dhan":
        from .dhan import DhanDataSource
        return DhanDataSource()
    if src == "mock":
        from .mock import MockDataSource
        return MockDataSource()
    return NSEDataSource()
