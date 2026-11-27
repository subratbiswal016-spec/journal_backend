"""Full NSE + BSE cash-equity master and fuzzy symbol search.

Downloads the public Fyers NSE_CM and BSE_CM equity symbol masters once per
24 hours (cached on disk in `data/stocks_master.json`), merges dual-listed
equities so NSE and BSE share a clean entry while preserving BSE-only stocks
and BSE scrip codes, and provides fast prefix / substring / typo-tolerant search.
Falls back cleanly to `screener.BROAD` if offline.
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from . import screener

_NSE_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
_BSE_URL = "https://public.fyers.in/sym_details/BSE_CM.csv"
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "stocks_master.json"
_TTL_SEC = 86400  # refresh master once a day

_ALIASES = {
    "MOFSL": "MOTILALOFS",
    "MOSFL": "MOTILALOFS",
    "TATAMOTOR": "TATAMOTORS",
    "HDFC": "HDFCBANK",
    "ZOMATO": "ETERNAL",
    "ETERNAL": "ZOMATO",
}

_NSE_SERIES = ("EQ", "BE", "SM", "ST", "BZ")
_NSE_PRIO = {s: i for i, s in enumerate(_NSE_SERIES)}
_BSE_SERIES = ("A", "B", "T", "X", "XT", "Z", "M", "MT", "P")
_BSE_PRIO = {s: i for i, s in enumerate(_BSE_SERIES)}

_lock = threading.Lock()
_items: list[dict] = []
_by_key: dict[str, dict] = {}
_loaded_at: float = 0.0
_loading = False


def _fallback_items() -> list[dict]:
    return [
        {"t": t, "n": t, "e": "NSE", "f": f"NSE:{t}-EQ", "b": "", "c": ""}
        for t in screener.BROAD
    ]


def _parse_csv(text: str, exchange: str) -> list[dict]:
    """Parse Fyers NSE_CM.csv or BSE_CM.csv rows into equity candidates."""
    out: list[dict] = []
    prio_map = _NSE_PRIO if exchange == "NSE" else _BSE_PRIO
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 14:
            continue
        fyers_sym = (row[9] or "").strip().upper()
        if not fyers_sym.startswith(exchange + ":"):
            continue
        series = fyers_sym.rsplit("-", 1)[-1] if "-" in fyers_sym else ""
        if series not in prio_map:
            continue
        name = " ".join((row[1] or "").split())
        isin = (row[5] or "").strip().upper()
        scrip_code = (row[12] or "").strip() if exchange == "BSE" else ""
        ticker = (row[13] or "").strip().upper()
        if not ticker:
            base = fyers_sym.split(":", 1)[1]
            ticker = base.rsplit("-", 1)[0] if "-" in base else base
        if not ticker:
            continue
        out.append({
            "t": ticker,
            "n": name or ticker,
            "e": exchange,
            "f": fyers_sym,
            "isin": isin,
            "c": scrip_code,
            "prio": prio_map[series],
        })
    return out


def _merge_nse_bse(nse_rows: list[dict], bse_rows: list[dict]) -> list[dict]:
    """Keep best series per exchange and merge NSE + BSE rows into one catalog."""
    best_nse: dict[str, dict] = {}
    for r in nse_rows:
        prev = best_nse.get(r["t"])
        if prev is None or r["prio"] < prev["prio"]:
            best_nse[r["t"]] = r

    best_bse: dict[str, dict] = {}
    bse_by_isin: dict[str, dict] = {}
    for r in bse_rows:
        prev = best_bse.get(r["t"])
        if prev is None or r["prio"] < prev["prio"]:
            best_bse[r["t"]] = r
        if r["isin"]:
            prev_i = bse_by_isin.get(r["isin"])
            if prev_i is None or r["prio"] < prev_i["prio"]:
                bse_by_isin[r["isin"]] = r

    merged: dict[str, dict] = {}
    used_bse: set[str] = set()
    for t, r in best_nse.items():
        b = (bse_by_isin.get(r["isin"]) if r["isin"] else None) or best_bse.get(t)
        if b:
            used_bse.add(b["t"])
        merged[t] = {
            "t": t,
            "n": r["n"],
            "e": "NSE·BSE" if b else "NSE",
            "f": r["f"],
            "b": b["f"] if b else "",
            "c": b["c"] if b else "",
        }
    for t, r in best_bse.items():
        if t in used_bse or t in merged:
            continue
        merged[t] = {
            "t": t,
            "n": r["n"],
            "e": "BSE",
            "f": r["f"],
            "b": r["f"],
            "c": r["c"],
        }

    # Put liquid F&O / broad names near top alphabetically within groups for instant UX.
    broad_rank = {s: i for i, s in enumerate(screener.BROAD)}
    return sorted(merged.values(), key=lambda x: (0 if x["t"] in broad_rank else 1, broad_rank.get(x["t"], 999999), x["t"]))


def _index_items(items: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for it in items:
        idx[it["t"]] = it
        if it.get("f"):
            idx[it["f"]] = it
        if it.get("b"):
            idx[it["b"]] = it
        if it.get("c"):
            idx[str(it["c"])] = it
    return idx


def _refresh_from_network() -> list[dict]:
    r_nse = requests.get(_NSE_URL, timeout=15)
    r_nse.raise_for_status()
    nse_rows = _parse_csv(r_nse.text, "NSE")

    bse_rows: list[dict] = []
    try:
        r_bse = requests.get(_BSE_URL, timeout=15)
        if r_bse.status_code == 200:
            bse_rows = _parse_csv(r_bse.text, "BSE")
    except requests.RequestException:
        bse_rows = []

    items = _merge_nse_bse(nse_rows, bse_rows)
    if len(items) < 200:
        raise RuntimeError("Parsed stock master unexpectedly small")
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"ts": time.time(), "items": items}), encoding="utf-8")
    except OSError:
        pass
    return items


def load_master(allow_network: bool = True) -> list[dict]:
    """Return cached all-NSE + all-BSE equity list."""
    global _items, _by_key, _loaded_at
    now = time.time()
    if _items and (now - _loaded_at < _TTL_SEC):
        return _items

    with _lock:
        if _items and (now - _loaded_at < _TTL_SEC):
            return _items
        # 1) Try warm disk cache first (valid up to 24h)
        if _CACHE_FILE.exists():
            try:
                raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                cached_items = raw.get("items") or []
                cached_ts = float(raw.get("ts") or 0)
                if cached_items:
                    _items = cached_items
                    _by_key = _index_items(_items)
                    _loaded_at = cached_ts if (now - cached_ts < _TTL_SEC) else now - _TTL_SEC + 60
                    if now - cached_ts < _TTL_SEC:
                        return _items
            except (OSError, ValueError):
                pass
        # 2) Fetch fresh master from Fyers public CDN
        if allow_network:
            try:
                fresh = _refresh_from_network()
                _items = fresh
                _by_key = _index_items(_items)
                _loaded_at = time.time()
                return _items
            except Exception:  # noqa: BLE001 - fall back to disk cache or BROAD
                pass
        if not _items:
            _items = _fallback_items()
            _by_key = _index_items(_items)
            _loaded_at = time.time() - _TTL_SEC + 120
        return _items


def warm_async() -> None:
    """Kick off a background master load so the first search is instant."""
    global _loading
    if _loading or _items:
        return
    _loading = True

    def _run():
        global _loading
        try:
            load_master(allow_network=True)
        finally:
            _loading = False

    threading.Thread(target=_run, daemon=True).start()


def resolve(symbol: str) -> Optional[dict]:
    """Resolve ticker (`MOFSL`), Fyers symbol (`NSE:MOFSL-EQ`), or BSE code (`532892`)."""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    if not _items:
        load_master(allow_network=False)
    it = _by_key.get(s)
    if it:
        return it
    # Strip exchange prefix / -EQ / -BE / -A suffix and retry
    clean = s.split(":", 1)[1] if ":" in s else s
    clean = clean.rsplit("-", 1)[0] if "-" in clean else clean
    return _by_key.get(clean)


def _is_close(a: str, b: str) -> bool:
    """Damerau-Levenshtein distance <= 1 (catches adjacent swaps like MOSFL/MOFSL, CSDL/CDSL)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or min(la, lb) < 3:
        return False
    if a == b:
        return True
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            i, j = diffs
            return a[i] == b[j] and a[j] == b[i]
        return False
    # length differs by 1: one insertion/deletion
    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def search(q: str, limit: int = 20) -> list[dict]:
    """Search tickers, company names, BSE codes, plus aliases and 1-typo fuzzy matches."""
    query = (q or "").strip().upper()
    if not query:
        return []
    items = load_master(allow_network=True)
    alias_target = _ALIASES.get(query)
    toks = [w for w in query.split() if w]
    exact, starts, contains_t, contains_n, fuzzy = [], [], [], [], []
    for it in items:
        t = it["t"]
        n = it["n"].upper()
        c = str(it.get("c") or "")
        if alias_target and t == alias_target:
            exact.append(it)
        elif t == query or c == query:
            exact.append(it)
        elif t.startswith(query) or c.startswith(query):
            starts.append(it)
        elif query in t:
            contains_t.append(it)
        elif query in n or (len(toks) > 1 and all(w in t or w in n for w in toks)):
            contains_n.append(it)
        elif len(query) >= 3 and (_is_close(query, t) or (len(t) > len(query) and _is_close(query, t[:len(query)]))):
            fuzzy.append(it)
    return (exact + starts + contains_t + contains_n + fuzzy)[:limit]
