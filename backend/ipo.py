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
_detail_cache: dict[str, dict] = {}
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


def _parse_date(v):
    if not v:
        return None
    s = str(v).split(" ")[0]
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _fmt_date(v):
    d = _parse_date(v)
    return d.strftime("%d %b") if d else (str(v).split(" ")[0] if v else None)


def _is_today(v) -> bool:
    d = _parse_date(v)
    return bool(d and d == datetime.now(_IST).date())


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
        "opens_today": _is_today(it.get("issueStartDate")),
        "closes_today": _is_today(it.get("issueEndDate")),
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
             "segment": "SME", "status": "Open", "sub": "3.42",
             "opens_today": True, "closes_today": False},
            {"name": "Demo Capital Mainboard Ltd", "symbol": "DEMOCAP",
             "price_band": "₹540 – ₹570", "open_date": "24 Nov", "close_date": "26 Nov",
             "segment": "Mainboard", "status": "Open", "sub": "12.80",
             "opens_today": False, "closes_today": True},
        ],
        "upcoming": [
            {"name": "Foodlink F&B Holdings (India) Ltd", "symbol": "FOODLINK",
             "price_band": "₹181 – ₹191", "open_date": "26 Nov", "close_date": "30 Nov",
             "segment": "Mainboard", "status": "Upcoming", "sub": None,
             "opens_today": False, "closes_today": False},
        ],
    }


def _screener_ipo_profile(symbol: str, name: str) -> dict:
    """Best-effort company introduction, pre-IPO ratios, sector, and pros/cons from Screener.in."""
    import re
    from . import fundamentals as F

    hdrs = {"User-Agent": _UA, "Accept": "application/json,text/html,*/*", "Accept-Language": "en-US,en;q=0.9"}
    clean_name = re.sub(r"\b(India|Private|Pvt\.?|Limited|Ltd\.?)\b", " ", name or "", flags=re.I)
    clean_name = re.sub(r"[()]", " ", clean_name)
    clean_name = re.sub(r"\s+", " ", clean_name).strip()
    words = clean_name.split()
    queries = []
    for q in (name, clean_name, " ".join(words[:2]) if len(words) >= 2 else "", symbol):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    target_url = ""
    matched_name = ""
    for q in queries:
        try:
            r = requests.get("https://www.screener.in/api/company/search/", params={"q": q}, headers=hdrs, timeout=8)
            if r.status_code != 200:
                continue
            hits = r.json() or []
            if not hits:
                continue
            sym_u = (symbol or "").strip().upper()
            best = next((h for h in hits if sym_u and sym_u in str(h.get("url") or "").upper()), None) or hits[0]
            if best.get("url"):
                target_url = "https://www.screener.in" + best["url"]
                matched_name = str(best.get("name") or "")
                break
        except Exception:  # noqa: BLE001
            continue
    if not target_url and symbol:
        target_url = f"https://www.screener.in/company/{symbol}/"
    if not target_url:
        return {}

    try:
        page = requests.get(target_url, headers=hdrs, timeout=10)
        if page.status_code != 200:
            return {}
        html_t = page.text or ""
    except Exception:  # noqa: BLE001
        return {}

    about = None
    for pat in (r'<div class="company-profile">.*?<p[^>]*>(.*?)</p>',
                r'class="[^"]*about[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>'):
        m = re.search(pat, html_t, re.S)
        if m:
            txt = F._clean(m.group(1))
            txt = re.sub(r"\s*\[\d+\]\s*", " ", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                about = txt[:900]
                break

    ratios: list[dict] = []
    block = re.search(r'<ul id="top-ratios".*?</ul>', html_t, re.S)
    if block:
        for li in re.findall(r'<li[^>]*>(.*?)</li>', block.group(0), re.S):
            nmm = re.search(r'class="name">(.*?)</span>', li, re.S)
            if not nmm:
                continue
            rname = F._clean(nmm.group(1))
            rval = F._clean(li.replace(nmm.group(0), ""))
            if rname and rval and re.search(r"\d", rval):
                ratios.append({"name": rname, "value": rval})

    sector = ""
    peers_m = re.search(r'title="(?:Sector|Broad Sector|Industry)"[^>]*>(.*?)</a>', html_t, re.S)
    if peers_m:
        sector = F._clean(peers_m.group(1))

    return {
        "company_name": matched_name,
        "screener_url": target_url,
        "about": about,
        "sector": sector,
        "ratios": ratios[:9],
        "pros": F._screener_list(html_t, "pros"),
        "cons": F._screener_list(html_t, "cons"),
        "source": "Screener.in" if (about or ratios) else "",
    }


def _nse_ipo_bids(symbol: str) -> list[dict]:
    """Fetch live QIB/NII/Retail category subscription multiples from NSE."""
    if not symbol:
        return []
    try:
        s = _session_get()
        r = s.get("https://www.nseindia.com/api/ipo-detail", params={"symbol": symbol}, timeout=8)
        if r.status_code != 200:
            return []
        raw = (r.json() or {}).get("bidDetails") or []
        out = []
        for row in raw:
            cat = str(row.get("category") or "").strip()
            times_raw = row.get("noOfTime")
            sr = str(row.get("srNo") or "")
            if not cat or times_raw in (None, ""):
                continue
            # Skip duplicate nested sub-rows like 1(a) unless needed
            if "(" in sr:
                continue
            try:
                t_val = round(float(times_raw), 2)
            except (TypeError, ValueError):
                continue
            out.append({"category": cat, "times": t_val})
        return out[:6]
    except Exception:  # noqa: BLE001
        return []


def _wiki_about(name: str) -> str | None:
    """Fallback company summary from Wikipedia when Screener profile isn't published yet."""
    import re
    q = re.sub(r"\b(India|Private|Pvt\.?|Limited|Ltd\.?)\b", " ", name or "", flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    if len(q) < 4:
        return None
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": q, "srlimit": 1, "format": "json"},
            headers={"User-Agent": _UA},
            timeout=6,
        )
        hits = ((r.json() or {}).get("query") or {}).get("search") or []
        if not hits:
            return None
        title = hits[0].get("title") or ""
        if not title:
            return None
        rs = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}",
            headers={"User-Agent": _UA},
            timeout=6,
        )
        if rs.status_code != 200:
            return None
        extract = str((rs.json() or {}).get("extract") or "").strip()
        return extract[:600] if extract else None
    except Exception:  # noqa: BLE001
        return None


def detail(symbol: str = "", name: str = "") -> dict:
    """Fetch company introduction, key ratios, pros/cons, and NSE bid breakdown for an IPO."""
    sym = (symbol or "").strip().upper()
    nm = (name or "").strip()
    key = f"{sym}|{nm.lower()}"
    now = time.time()
    with _lock:
        cached = _detail_cache.get(key)
        if cached and (now - cached["ts"]) < _TTL:
            return cached["data"]

    prof = _screener_ipo_profile(sym, nm)
    bids = _nse_ipo_bids(sym) if sym else []
    about = prof.get("about")
    source = prof.get("source") or ""
    if not about and nm:
        w = _wiki_about(nm)
        if w:
            about = w
            source = source or "Wikipedia"

    res = {
        "ok": True,
        "symbol": sym,
        "name": nm or prof.get("company_name") or sym,
        "about": about,
        "sector": prof.get("sector") or "",
        "ratios": prof.get("ratios") or [],
        "pros": prof.get("pros") or [],
        "cons": prof.get("cons") or [],
        "bids": bids,
        "screener_url": prof.get("screener_url") or "",
        "source": source,
    }
    with _lock:
        _detail_cache[key] = {"ts": now, "data": res}
    return res
