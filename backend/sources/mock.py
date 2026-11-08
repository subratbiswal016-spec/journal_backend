"""Mock data source: realistic synthetic option chain, no network.

Lets you see the dashboard working and develop the UI offline. It also runs
when markets are closed. Enable with DATA_SOURCE=mock in .env.
Numbers are made up — do not trade on them.
"""
from __future__ import annotations

import math
import random
import time
from datetime import date, timedelta
from typing import Optional

from .base import DataSource, OptionChain, StrikeRow, FuturesRow

_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100, "BANKEX": 100, "MIDCPNIFTY": 25}
_BASE = {"NIFTY": 24000, "BANKNIFTY": 51000, "FINNIFTY": 23000, "SENSEX": 81000, "BANKEX": 58000, "MIDCPNIFTY": 12000}


def _next_thursdays(n: int) -> list[str]:
    out, d = [], date.today()
    while len(out) < n:
        if d.weekday() == 3 and d >= date.today():
            out.append(d.strftime("%d-%b-%Y"))
        d += timedelta(days=1)
    return out


class MockDataSource(DataSource):
    name = "mock"

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> OptionChain:
        symbol = symbol.upper()
        step = _STEP.get(symbol, 50)
        base = _BASE.get(symbol, 24000)
        rnd = random.Random(int(time.time()) // 20 + hash(symbol))  # shifts every 20s
        spot = base + rnd.uniform(-step * 3, step * 3)
        spot_change = round(spot - base, 2)                 # vs the day's "open"
        spot_change_pct = round(spot_change / base * 100, 2) if base else 0.0
        atm = round(spot / step) * step
        expiries = _next_thursdays(4)
        if not expiry:
            expiry = expiries[0]

        rows = []
        for i in range(-20, 21):
            k = atm + i * step
            dist = abs(k - spot) / step
            weight = math.exp(-(dist ** 2) / 40)
            ce_oi = int((80000 * weight + rnd.uniform(0, 40000)) * (1.4 if k > spot else 0.7))
            pe_oi = int((80000 * weight + rnd.uniform(0, 40000)) * (1.4 if k < spot else 0.7))
            rows.append(StrikeRow(
                strike=float(k),
                ce_oi=ce_oi, ce_chg_oi=int(rnd.uniform(-25000, 30000)),
                ce_ltp=round(max(0.5, (spot - k) + rnd.uniform(5, 120)), 2),
                ce_iv=round(rnd.uniform(10, 22), 2), ce_volume=int(rnd.uniform(0, 500000)),
                pe_oi=pe_oi, pe_chg_oi=int(rnd.uniform(-25000, 30000)),
                pe_ltp=round(max(0.5, (k - spot) + rnd.uniform(5, 120)), 2),
                pe_iv=round(rnd.uniform(10, 22), 2), pe_volume=int(rnd.uniform(0, 500000)),
            ))

        futures = [
            FuturesRow(expiry=e, last_price=round(spot + rnd.uniform(20, 120), 2),
                       oi=int(rnd.uniform(5e6, 12e6)), chg_oi=int(rnd.uniform(-8e5, 8e5)),
                       volume=int(rnd.uniform(1e5, 9e5)))
            for e in expiries[:2]
        ]

        return OptionChain(
            symbol=symbol, spot=round(spot, 2), expiry=expiry, expiries=expiries,
            timestamp=time.strftime("%d-%b-%Y %H:%M:%S") + " (DEMO)",
            rows=rows, futures=futures, source=self.name,
            spot_change=spot_change, spot_change_pct=spot_change_pct,
        )
