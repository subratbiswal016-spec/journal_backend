"""Indian IPO calendar (open now + upcoming) from NSE's public JSON.

NSE gates its /api endpoints behind a browser cookie and sometimes blocks
datacenter IPs, so this degrades gracefully: on failure the UI links out to the
NSE IPO page instead. Cached ~30 min because IPO listings change slowly. This
is independent of Fyers — it does not touch the Fyers budget.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta

import requests

_IST = timezone(timedelta(hours=5, minutes=30))
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_TTL = 1800.0           # 30 min
_FAIL_TTL = 120.0       # retry a failure after 2 min

_cache: dict = {"ts": 0.0, "ok": False, "data": None}
_lock = threading.Lock()
_session: requests.Session | None = None
_sess_ts = 0.0
_sess_lock = threading.Lock()

_CURRENT = "https://www.nseindia.com/api/ipo-current-issue"
_UPCOMING = "https://www.nseindia.com/api/all-upcoming-issues?category=ipo"
NSE_PAGE = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"


def _session_get() -> requests.Session:
    global _session, _sess_ts
    with _sess_lock:
        now = time.time()
        if _session is not None and now - _sess_ts < 600:
            return _session
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE_PAGE,
        })
        for _ in range(2):              # prime NSE cookies (may need two hits)
            try:
                s.get(NSE_PAGE, timeout=10)
                if s.cookies:
                    break
            except requests.RequestException:
                continue
        _session, _sess_ts = s, now
        return s


def _rupees(v):
    if not v:
        return None
    return str(v).replace("Rs.", "₹").replace("  ", " ").replace(" to ", " – ").strip()


def _fmt_date(v):
    if not v:
        return None
    s = str(v).split(" ")[0]
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b")
        except ValueError:
            continue
    return s


def _norm(it: dict, status: str) -> dict:
    seg = (it.get("segment") or "")
    series = (it.get("series") or "")
    is_sme = "SME" in seg.upper() or series.upper() == "SME"
    return {
        "name": it.get("companyName") or it.get("symbol") or "—",
        "symbol": it.get("symbol"),
        "price_band": _rupees(it.get("issuePrice")),
        "open_date": _fmt_date(it.get("issueStartDate")),
        "close_date": _fmt_date(it.get("issueEndDate")),
        "segment": "SME" if is_sme else "Mainboard",
        "status": status,
        "sub": it.get("noOfTime"),               # times subscribed (open issues)
        "issue_size": it.get("issueSize"),
    }


def _fetch() -> dict:
    s = _session_get()
    out = {"open": [], "upcoming": []}
    r = s.get(_CURRENT, timeout=10)
    r.raise_for_status()
    for it in (r.json() or []):
        out["open"].append(_norm(it, "Open"))
    r = s.get(_UPCOMING, timeout=10)
    r.raise_for_status()
    for it in (r.json() or []):
        out["upcoming"].append(_norm(it, "Upcoming"))
    return out


def get() -> dict:
    now = time.time()
    with _lock:
        c = _cache
        if c["data"] is not None and (c["ok"] or now - c["ts"] < _FAIL_TTL) \
           and now - c["ts"] < _TTL:
            return c["data"]
    try:
        data = _fetch()
        data["ok"] = True
    except Exception as e:  # noqa: BLE001 - best-effort
        data = {"ok": False, "open": [], "upcoming": [],
                "note": "Live IPO list is temporarily unavailable — open the NSE page below.",
                "reason": f"{type(e).__name__}: {str(e)[:80]}"}
    data["source"] = "NSE"
    data["url"] = NSE_PAGE
    data["updated"] = datetime.now(_IST).strftime("%d %b %H:%M IST")
    with _lock:
        _cache.update(ts=now, ok=bool(data.get("ok")), data=data)
    return data


def probe() -> dict:
    """Un-gated diagnostic: does NSE answer from THIS host (e.g. Render)?"""
    out = {}
    s = _session_get()
    for kind, url in (("current", _CURRENT), ("upcoming", _UPCOMING)):
        try:
            r = s.get(url, timeout=10)
            try:
                n = len(r.json())
            except Exception:  # noqa: BLE001
                n = None
            out[kind] = {"status": r.status_code, "count": n,
                         "snippet": (r.text or "")[:70]}
        except Exception as e:  # noqa: BLE001
            out[kind] = {"error": f"{type(e).__name__}: {str(e)[:70]}"}
    return out


def demo() -> dict:
    return {
        "ok": True, "source": "Demo data", "url": NSE_PAGE, "updated": "now (demo)",
        "open": [
            {"name": "Shree Siddhivinayak Greentech Ltd", "symbol": "SHREESIDHI",
             "price_band": "₹130 – ₹142", "open_date": "25 Nov", "close_date": "27 Nov",
             "segment": "SME", "status": "Open", "sub": "3.42"},
            {"name": "Demo Capital Mainboard Ltd", "symbol": "DEMOCAP",
             "price_band": "₹540 – ₹570", "open_date": "24 Nov", "close_date": "26 Nov",
             "segment": "Mainboard", "status": "Open", "sub": "12.80"},
        ],
        "upcoming": [
            {"name": "Foodlink F&B Holdings (India) Ltd", "symbol": "FOODLINK",
             "price_band": "₹181 – ₹191", "open_date": "26 Nov", "close_date": "30 Nov",
             "segment": "Mainboard", "status": "Upcoming", "sub": None},
        ],
    }
