"""Company fundamentals for NSE stocks.

Primary source is Screener.in (rich Indian fundamentals: market cap, P/E, ROCE,
ROE, book value, dividend yield, pros & cons, plus a link to the full report).
Yahoo Finance is a secondary fallback for non-cloud hosts (Yahoo rate-limits
datacenter IPs like Render's with HTTP 429, so it is unreliable there).

This is SEPARATE from Fyers — it does not touch the Fyers rate-limit budget.
Results are cached per IST day; a failed fetch is negatively cached briefly.
Everything degrades gracefully: if fundamentals are unavailable the stock detail
view still shows the (Fyers-derived) technicals.
"""
from __future__ import annotations

import html as _html
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

_IST = timezone(timedelta(hours=5, minutes=30))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile"
_SUFFIX = ".NS"            # NSE cash symbols on Yahoo

# A handful of NSE tickers that differ on Yahoo (most map 1:1 to <TICKER>.NS).
_OVERRIDES: dict[str, str] = {
    # Yahoo lists these BSE-only / renamed names differently; fall back cleanly.
}

_OK_TTL = 0.0             # ok results live until the IST date rolls over
_FAIL_TTL = 90.0          # re-try a failed symbol after 90s (cloud hosts can
                          # fail a cold handshake — don't look broken for ages)
_LAST_REASON = ""         # short reason the last fetch failed (for diagnosis)

_cache: dict[str, dict] = {}      # sym -> {"date","ts","ok","data"}
_lock = threading.Lock()

_session: requests.Session | None = None
_crumb: str | None = None
_crumb_ts = 0.0
_sess_lock = threading.Lock()


def _ist_date() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _agree_consent(s: requests.Session, resp) -> None:
    """When Yahoo redirects to its EU/GDPR consent wall (guce.yahoo.com), accept
    once so the session gets the cookies needed for a crumb. Best-effort."""
    try:
        import re
        html = resp.text or ""
        if "consent" not in (resp.url or "") and "guce" not in (resp.url or ""):
            return
        csrf = re.search(r'name="csrfToken"[^>]*value="([^"]+)"', html)
        sess = re.search(r'name="sessionId"[^>]*value="([^"]+)"', html)
        if not (csrf and sess):
            return
        s.post("https://consent.yahoo.com/v2/collectConsent",
               data={"agree": ["agree", "agree"], "consentUUID": "default",
                     "sessionId": sess.group(1), "csrfToken": csrf.group(1),
                     "originalDoneUrl": "https://finance.yahoo.com/",
                     "namespace": "yahoo"}, timeout=8)
    except Exception:  # noqa: BLE001 - consent is best-effort
        pass


def _ensure_crumb(force: bool = False) -> str:
    """Prime cookies (handling the EU consent wall) + fetch a crumb, reusing the
    session for ~25 min. Raises with a short reason so failures are diagnosable."""
    global _session, _crumb, _crumb_ts
    with _sess_lock:
        now = time.time()
        if (not force) and _session is not None and _crumb and now - _crumb_ts < 1500:
            return _crumb
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        # Prime cookies — a real quote page triggers the consent redirect if any.
        for url in ("https://finance.yahoo.com/quote/RELIANCE.NS",
                    "https://fc.yahoo.com/",
                    "https://finance.yahoo.com/"):
            try:
                r = s.get(url, timeout=8, allow_redirects=True)
                _agree_consent(s, r)
                if s.cookies:
                    break
            except requests.RequestException:
                continue
        crumb = ""
        last = ""
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                r = s.get(f"https://{host}/v1/test/getcrumb", timeout=8)
                txt = (r.text or "").strip()
                last = f"crumb {r.status_code}"
                if r.status_code == 200 and txt and "<" not in txt and len(txt) < 40:
                    crumb = txt
                    break
            except requests.RequestException as e:
                last = "crumb " + type(e).__name__
                continue
        if not crumb:
            raise RuntimeError(f"no-crumb ({last or 'no cookies'})")
        _session, _crumb, _crumb_ts = s, crumb, now
        return crumb


def _num(d: dict | None, *path):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, dict):
        return cur.get("raw")
    return cur


def _str(d: dict | None, key: str):
    v = (d or {}).get(key)
    if isinstance(v, dict):
        v = v.get("raw") or v.get("fmt")
    return v if isinstance(v, (str, int, float)) else None


def _fetch_raw(ysym: str) -> dict:
    crumb = _ensure_crumb()
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ysym}"
    params = {"modules": _MODULES, "crumb": crumb}
    sess = _session
    r = sess.get(url, params=params, timeout=10)
    if r.status_code in (401, 403, 404):
        # crumb/cookie likely stale — refresh once and retry.
        crumb = _ensure_crumb(force=True)
        params["crumb"] = crumb
        sess = _session
        r = sess.get(url, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()
    res = (((js or {}).get("quoteSummary") or {}).get("result") or [None])[0]
    if not res:
        raise RuntimeError("no fundamentals in response")
    return res


def _map(res: dict, sym: str) -> dict:
    price = res.get("price") or {}
    sd = res.get("summaryDetail") or {}
    ks = res.get("defaultKeyStatistics") or {}
    fd = res.get("financialData") or {}
    ap = res.get("assetProfile") or {}

    de = _num(fd, "debtToEquity")
    out = {
        "ok": True,
        "symbol": sym,
        "source": "Yahoo Finance",
        "name": _str(price, "longName") or _str(price, "shortName"),
        "exchange": _str(price, "exchangeName"),
        "currency": _str(price, "currency") or "INR",
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "website": ap.get("website"),
        "employees": ap.get("fullTimeEmployees"),
        "summary": ap.get("longBusinessSummary"),
        # valuation
        "market_cap": _num(price, "marketCap"),
        "enterprise_value": _num(ks, "enterpriseValue"),
        "pe": _num(sd, "trailingPE"),
        "forward_pe": _num(sd, "forwardPE"),
        "peg": _num(ks, "pegRatio"),
        "pb": _num(ks, "priceToBook"),
        "eps": _num(ks, "trailingEps"),
        "forward_eps": _num(ks, "forwardEps"),
        "book_value": _num(ks, "bookValue"),
        "div_yield": _num(sd, "dividendYield"),          # fraction
        "div_rate": _num(sd, "dividendRate"),            # ₹/share
        "beta": _num(sd, "beta"),
        # range
        "week52_high": _num(sd, "fiftyTwoWeekHigh"),
        "week52_low": _num(sd, "fiftyTwoWeekLow"),
        "day_high": _num(sd, "dayHigh"),
        "day_low": _num(sd, "dayLow"),
        "avg_vol": _num(sd, "averageVolume"),
        "price_yahoo": _num(price, "regularMarketPrice"),
        "chg_pct_yahoo": _num(price, "regularMarketChangePercent"),
        "change_52w": _num(ks, "52WeekChange"),          # fraction
        # profitability / health (fractions where %)
        "roe": _num(fd, "returnOnEquity"),
        "roa": _num(fd, "returnOnAssets"),
        "profit_margin": _num(fd, "profitMargins"),
        "oper_margin": _num(fd, "operatingMargins"),
        "gross_margin": _num(fd, "grossMargins"),
        "rev_growth": _num(fd, "revenueGrowth"),
        "earn_growth": _num(fd, "earningsGrowth"),
        "debt_to_equity": (de / 100.0) if isinstance(de, (int, float)) else None,
        "current_ratio": _num(fd, "currentRatio"),
        "total_revenue": _num(fd, "totalRevenue"),
        "ebitda": _num(fd, "ebitda"),
        "total_cash": _num(fd, "totalCash"),
        "total_debt": _num(fd, "totalDebt"),
        "shares_out": _num(ks, "sharesOutstanding"),
        # analyst view
        "recommendation": fd.get("recommendationKey"),
        "num_analysts": _num(fd, "numberOfAnalystOpinions"),
        "target_mean": _num(fd, "targetMeanPrice"),
        "target_high": _num(fd, "targetHighPrice"),
        "target_low": _num(fd, "targetLowPrice"),
    }
    return out


# ----------------------------------------------------------- Screener.in
def _clean(frag: str) -> str:
    """Strip HTML tags + entities, collapse whitespace."""
    txt = re.sub(r"<[^>]+>", " ", frag or "")
    txt = _html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _screener_list(html_text: str, cls: str) -> list[str]:
    """Extract the <li> texts of a pros/cons block."""
    m = re.search(r'<div[^>]*class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>.*?<ul[^>]*>(.*?)</ul>',
                  html_text, re.S)
    if not m:
        return []
    items = [_clean(x) for x in re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), re.S)]
    return [x for x in items if x][:8]


def _fetch_screener(sym: str, bse_code: str = "") -> dict:
    """Scrape the key ratios + pros/cons + about from Screener.in. Tries ticker
    first, then BSE scrip code for BSE-only stocks."""
    candidates = [sym]
    if bse_code and bse_code != sym:
        candidates.append(bse_code)
    t = ""
    url = ""
    last_err: Exception | None = None
    for code in candidates:
        url = f"https://www.screener.in/company/{code}/"
        try:
            r = requests.get(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*",
                                           "Accept-Language": "en-US,en;q=0.9"}, timeout=12)
            r.raise_for_status()
            t = r.text or ""
            if 'id="top-ratios"' in t:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
            t = ""
    if not t:
        raise last_err or RuntimeError("screener: empty")

    nm = re.search(r'<h1[^>]*>(.*?)</h1>', t, re.S)
    name = _clean(nm.group(1)) if nm else sym

    block = re.search(r'<ul id="top-ratios".*?</ul>', t, re.S)
    ratios: list[dict] = []
    if block:
        for li in re.findall(r'<li[^>]*>(.*?)</li>', block.group(0), re.S):
            nmm = re.search(r'class="name">(.*?)</span>', li, re.S)
            if not nmm:
                continue
            rname = _clean(nmm.group(1))
            rval = _clean(li.replace(nmm.group(0), ""))
            if rname and rval and rname.lower() != rval.lower():
                ratios.append({"name": rname, "value": rval})
    if not ratios:
        raise RuntimeError("screener: no ratios parsed")

    about = None
    for pat in (r'<div class="company-profile">.*?<p[^>]*>(.*?)</p>',
                r'class="[^"]*about[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>'):
        ab = re.search(pat, t, re.S)
        if ab:
            about = _clean(ab.group(1))[:650]
            if about:
                break

    return {
        "ok": True, "symbol": sym, "name": name, "source": "Screener.in",
        "url": url, "ratios": ratios, "about": about or None,
        "pros": _screener_list(t, "pros"), "cons": _screener_list(t, "cons"),
    }


def _yahoo_ratios(sym: str, exch: str = "NSE") -> dict:
    """Yahoo fallback, mapped into the same display shape as Screener."""
    suffixes = (".BO", ".NS") if exch == "BSE" else (".NS", ".BO")
    y = None
    ysym = _OVERRIDES.get(sym, sym + suffixes[0])
    last_err: Exception | None = None
    candidates = [_OVERRIDES[sym]] if sym in _OVERRIDES else [sym + sfx for sfx in suffixes]
    for cand in candidates:
        try:
            y = _map(_fetch_raw(cand), sym)
            ysym = cand
            if y and y.get("ok"):
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
            y = None
    if not y or not y.get("ok"):
        raise last_err or RuntimeError("yahoo: no data")

    def _cr(v):
        return f"₹ {v/1e7:,.0f} Cr." if isinstance(v, (int, float)) else None

    def _pc(v):
        return f"{v*100:.2f}%" if isinstance(v, (int, float)) else None

    def _rs(v):
        return f"₹ {v:,.2f}" if isinstance(v, (int, float)) else None

    def _n(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else None

    pairs = [("Market Cap", _cr(y.get("market_cap"))), ("Stock P/E", _n(y.get("pe"))),
             ("EPS (TTM)", _rs(y.get("eps"))), ("Book Value", _rs(y.get("book_value"))),
             ("Price / Book", _n(y.get("pb"))), ("Dividend Yield", _pc(y.get("div_yield"))),
             ("ROE", _pc(y.get("roe"))), ("Profit Margin", _pc(y.get("profit_margin"))),
             ("Beta", _n(y.get("beta"))),
             ("52W High", _rs(y.get("week52_high"))), ("52W Low", _rs(y.get("week52_low")))]
    ratios = [{"name": k, "value": v} for k, v in pairs if v]
    if not ratios:
        raise RuntimeError("yahoo: empty")
    return {"ok": True, "symbol": sym, "name": y.get("name") or sym,
            "source": "Yahoo Finance", "url": f"https://finance.yahoo.com/quote/{ysym}",
            "ratios": ratios, "about": y.get("summary"), "pros": [], "cons": []}


def get(ticker: str) -> dict:
    """Return a fundamentals dict for an NSE or BSE ticker. Never raises."""
    from . import symbols as _syms
    raw = (ticker or "").strip().upper()
    if not raw:
        return {"ok": False, "note": "No symbol."}
    resolved = _syms.resolve(raw)
    sym = (resolved.get("t") if resolved else None) or raw.split(":")[-1].rsplit("-", 1)[0]
    bse_code = str(resolved.get("c") or "") if resolved else ""
    exch = "BSE" if (resolved and resolved.get("e") == "BSE") or raw.startswith("BSE:") else "NSE"
    today = _ist_date()
    now = time.time()

    with _lock:
        c = _cache.get(sym)
        if c and c["date"] == today:
            if c["ok"] or (now - c["ts"] < _FAIL_TTL):
                return c["data"]

    global _LAST_REASON
    data = None
    reasons = []
    for fn, args in ((_fetch_screener, (sym, bse_code)), (_yahoo_ratios, (sym, exch))):
        try:
            d = fn(*args)
            if d and d.get("ok"):
                data = d
                break
        except Exception as e:  # noqa: BLE001 - try the next source
            reasons.append(f"{fn.__name__}: {type(e).__name__} {str(e)[:60]}")
    if data is None:
        _LAST_REASON = " | ".join(reasons)
        data = {"ok": False, "symbol": sym, "reason": _LAST_REASON,
                "note": "Fundamentals are temporarily unavailable for this stock."}
    else:
        _LAST_REASON = ""

    with _lock:
        _cache[sym] = {"date": today, "ts": now,
                       "ok": bool(data.get("ok")), "data": data}
    return data


def diag(ticker: str = "RELIANCE") -> dict:
    """Uncached multi-probe diagnostic: tries several data sources and reports
    which actually work from THIS host (e.g. Render). Used to pick a source that
    isn't IP-blocked. Un-gated endpoint; returns no user data."""
    sym = (ticker or "RELIANCE").strip().upper()
    ysym = _OVERRIDES.get(sym, sym + _SUFFIX)
    out: dict = {"symbol": sym, "probes": {}}
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "*/*",
                      "Accept-Language": "en-US,en;q=0.9"})

    # 1) Yahoo crumb with a few retries (is the 429 persistent or transient?)
    statuses = []
    crumb = ""
    try:
        s.get("https://finance.yahoo.com/quote/" + ysym, timeout=8)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(4):
        try:
            r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8)
            statuses.append(r.status_code)
            t = (r.text or "").strip()
            if r.status_code == 200 and t and "<" not in t and len(t) < 40:
                crumb = t
                break
        except Exception as e:  # noqa: BLE001
            statuses.append(type(e).__name__)
        time.sleep(1.5)
    out["probes"]["yahoo_crumb"] = {"statuses": statuses, "got": bool(crumb)}

    # 2) Yahoo quoteSummary (needs crumb)
    if crumb:
        try:
            r = s.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ysym}",
                      params={"modules": _MODULES, "crumb": crumb}, timeout=10)
            out["probes"]["yahoo_quotesummary"] = {"status": r.status_code,
                "has_result": bool((((r.json() or {}).get("quoteSummary") or {}).get("result")))}
        except Exception as e:  # noqa: BLE001
            out["probes"]["yahoo_quotesummary"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}

    # 3) Yahoo chart (no crumb) — price + 52w only
    try:
        r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}", timeout=8)
        meta = (((r.json() or {}).get("chart") or {}).get("result") or [{}])[0].get("meta", {})
        out["probes"]["yahoo_chart"] = {"status": r.status_code,
            "price": meta.get("regularMarketPrice"), "name": meta.get("longName") or meta.get("shortName")}
    except Exception as e:  # noqa: BLE001
        out["probes"]["yahoo_chart"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}

    # 4) Screener.in (rich Indian fundamentals) — reachable + parseable?
    try:
        r = s.get(f"https://www.screener.in/company/{sym}/consolidated/", timeout=10)
        html = r.text or ""
        out["probes"]["screener_in"] = {"status": r.status_code,
            "has_ratios": ("top-ratios" in html), "len": len(html)}
    except Exception as e:  # noqa: BLE001
        out["probes"]["screener_in"] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}

    return out


def demo(ticker: str) -> dict:
    """Synthetic fundamentals (Screener-shaped) so the detail view populates in
    demo mode."""
    import random
    sym = (ticker or "DEMO").strip().upper()
    rng = random.Random(sum(ord(c) for c in sym) * 7 + 1)
    cap = rng.uniform(8_000, 19_00_000)          # ₹ Cr
    eps = rng.uniform(8, 120)
    pe = rng.uniform(9, 65)
    price = round(eps * pe, 2)
    bv = round(price / rng.uniform(1.2, 8), 2)
    ratios = [
        {"name": "Market Cap", "value": f"₹ {cap:,.0f} Cr."},
        {"name": "Current Price", "value": f"₹ {price:,.2f}"},
        {"name": "High / Low", "value": f"₹ {price*rng.uniform(1.1,1.5):,.0f} / {price*rng.uniform(0.6,0.9):,.0f}"},
        {"name": "Stock P/E", "value": f"{pe:.1f}"},
        {"name": "Book Value", "value": f"₹ {bv:,.0f}"},
        {"name": "Dividend Yield", "value": f"{rng.uniform(0,3):.2f} %"},
        {"name": "ROCE", "value": f"{rng.uniform(8,28):.1f} %"},
        {"name": "ROE", "value": f"{rng.uniform(6,32):.1f} %"},
        {"name": "Face Value", "value": f"₹ {rng.choice([1,2,5,10]):.2f}"},
    ]
    return {
        "ok": True, "symbol": sym, "source": "Demo data",
        "name": sym.title() + " Ltd (demo)",
        "url": "https://www.screener.in/company/" + sym + "/",
        "about": (f"{sym.title()} Ltd is a demo company shown while the app runs "
                  "without a live connection. Figures are synthetic, for layout only."),
        "ratios": ratios,
        "pros": ["Company has delivered good profit growth (demo).",
                 "Maintaining healthy dividend payout (demo)."],
        "cons": ["Stock is trading at a high valuation (demo)."],
    }
