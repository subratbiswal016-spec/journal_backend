"""Derived metrics computed from an OptionChain: PCR, Max Pain, ATM, etc."""
from __future__ import annotations

from .sources.base import OptionChain


def _total(rows, attr) -> float:
    return float(sum(getattr(r, attr) for r in rows))


def compute_pcr(chain: OptionChain) -> float:
    ce_oi = _total(chain.rows, "ce_oi")
    pe_oi = _total(chain.rows, "pe_oi")
    if ce_oi <= 0:
        return 0.0
    return round(pe_oi / ce_oi, 3)


def compute_pcr_change(chain: OptionChain) -> float:
    ce = _total(chain.rows, "ce_chg_oi")
    pe = _total(chain.rows, "pe_chg_oi")
    if ce == 0:
        return 0.0
    return round(pe / ce, 3)


def compute_max_pain(chain: OptionChain) -> float:
    """Strike at which total payout to option buyers is smallest (writers' pain
    point). For each candidate expiry price E (= each listed strike) sum the
    intrinsic value of all calls and puts, and pick the E with the minimum."""
    rows = chain.rows
    if not rows:
        return 0.0
    strikes = [r.strike for r in rows]
    best_strike = strikes[0]
    best_loss = None
    for e in strikes:
        call_pay = sum(max(0.0, e - r.strike) * r.ce_oi for r in rows)
        put_pay = sum(max(0.0, r.strike - e) * r.pe_oi for r in rows)
        total = call_pay + put_pay
        if best_loss is None or total < best_loss:
            best_loss = total
            best_strike = e
    return best_strike


def nearest_atm(chain: OptionChain) -> float:
    if not chain.rows or chain.spot <= 0:
        return 0.0
    return min((r.strike for r in chain.rows), key=lambda k: abs(k - chain.spot))


def key_oi_levels(chain: OptionChain) -> dict:
    """Highest-OI call strike (resistance) and put strike (support)."""
    rows = chain.rows
    resistance = max(rows, key=lambda r: r.ce_oi).strike if rows else 0.0
    support = max(rows, key=lambda r: r.pe_oi).strike if rows else 0.0
    return {"resistance": resistance, "support": support}


def summarize(chain: OptionChain) -> dict:
    levels = key_oi_levels(chain)
    pcr = compute_pcr(chain)
    return {
        "symbol": chain.symbol,
        "spot": chain.spot,
        "expiry": chain.expiry,
        "timestamp": chain.timestamp,
        "source": chain.source,
        "pcr": pcr,
        "pcr_change": compute_pcr_change(chain),
        "max_pain": compute_max_pain(chain),
        "atm": nearest_atm(chain),
        "total_ce_oi": _total(chain.rows, "ce_oi"),
        "total_pe_oi": _total(chain.rows, "pe_oi"),
        "total_ce_chg_oi": _total(chain.rows, "ce_chg_oi"),
        "total_pe_chg_oi": _total(chain.rows, "pe_chg_oi"),
        "resistance": levels["resistance"],
        "support": levels["support"],
        "bias": _bias(pcr),
    }


def _bias(pcr: float) -> str:
    if pcr >= 1.3:
        return "Bullish"
    if pcr <= 0.7:
        return "Bearish"
    return "Neutral"
