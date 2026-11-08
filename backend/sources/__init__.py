"""Data source layer: swap NSE <-> Upstox behind one interface."""
from .. import config
from .base import DataSource, OptionChain
from .nse import NSEDataSource


def get_source() -> DataSource:
    """Factory: returns the configured data source instance."""
    if config.DATA_SOURCE == "upstox":
        from .upstox import UpstoxDataSource
        return UpstoxDataSource()
    if config.DATA_SOURCE == "fyers":
        from .fyers import FyersDataSource
        return FyersDataSource()
    if config.DATA_SOURCE == "mock":
        from .mock import MockDataSource
        return MockDataSource()
    return NSEDataSource()
