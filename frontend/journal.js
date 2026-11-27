"use strict";
// Trading Journal view: tabs, rules status, targets, analytics, trades, settings.
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 0) =>
    n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d });
  const inr = (n) => (n < 0 ? "-" : "") + "₹" + fmt(Math.abs(n));
  const signed = (n) => (n > 0 ? "+" : "") + inr(n);

  // Global loading indicator — shown whenever a filter/date change refetches.
  // A counter handles overlapping loads so it hides only when all are done.
  let _loadingCount = 0;
  function showLoading(on) {
    _loadingCount = Math.max(0, _loadingCount + (on ? 1 : -1));
    const el = $("loadingPill");
    if (el) el.classList.toggle("hidden", _loadingCount === 0);
  }

  let pnlChart = null;
  let lastOverview = null;
  let JDAILY = [];            // dated daily P&L series
  let chartGran = "daily";    // daily | weekly | monthly
  let chartType = "bar";      // bar | line (cumulative)
  let dhanConnected = false;  // whether a Dhan token is saved server-side
  let dhanAutoSynced = false; // one Dhan auto-sync per page load
  let fyersAutoSynced = false;// one Fyers auto-sync per page load

  // ---- period filter (All time / Year / Month / Custom range) ----
  let jfMode = "all";         // all | year | month | range
  let jfYear = new Date().getFullYear();
  let jfMonth = new Date().getMonth() + 1;   // 1..12
  let jfFrom = "", jfTo = "";                // custom range (YYYY-MM-DD)
  let yearsPopulated = false;

  function filterRange() {
    const pad = (n) => String(n).padStart(2, "0");
    if (jfMode === "year") return { start: `${jfYear}-01-01`, end: `${jfYear}-12-31` };
    if (jfMode === "month") {
      const lastDay = new Date(jfYear, jfMonth, 0).getDate();   // real last day of month
      return { start: `${jfYear}-${pad(jfMonth)}-01`, end: `${jfYear}-${pad(jfMonth)}-${pad(lastDay)}` };
    }
    if (jfMode === "range") return { start: jfFrom || null, end: jfTo || null };
    return { start: null, end: null };   // all time
  }
  function filterQuery() {
    const r = filterRange();
    const p = [];
    if (r.start) p.push(`start=${r.start}`);
    if (r.end) p.push(`end=${r.end}`);
    return p.length ? `?${p.join("&")}` : "";
  }

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll("#navMain .tab[data-view]").forEach((btn) =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)));
  // sidebar: ☰ opens (mobile) or re-opens a collapsed sidebar (desktop);
  // « collapses it. A resize nudge lets the charts refit to the new width.
  const sbIsMobile = () => window.matchMedia("(max-width: 860px)").matches;
  const sbNudge = () => setTimeout(() => { try { window.dispatchEvent(new Event("resize")); } catch (e) { } }, 240);
  if ($("sbToggle")) $("sbToggle").addEventListener("click", () => {
    if (sbIsMobile()) document.body.classList.toggle("sb-open");
    else { document.body.classList.remove("sb-collapsed"); sbNudge(); }
  });
  if ($("sbCollapse")) $("sbCollapse").addEventListener("click", () => {
    if (sbIsMobile()) document.body.classList.remove("sb-open");
    else { document.body.classList.add("sb-collapsed"); sbNudge(); }
  });
  if ($("sbBackdrop")) $("sbBackdrop").addEventListener("click", () => document.body.classList.remove("sb-open"));
  (window.__authReady || Promise.resolve(true)).then((ok) => { if (ok) refreshIpoBadge(); });

  let journalSub = "journal";          // "journal" | "capital" — sub-tab inside Journal
  let lastNonTradeView = "home";
  function switchView(view) {
    if (document.body.classList.contains("needs-login")) return;
    if (view !== "trademode") lastNonTradeView = view;
    document.body.classList.remove("sb-open");   // close the mobile sidebar on nav
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view));
    const isJ = view === "journal";
    if ($("viewHome")) $("viewHome").classList.toggle("hidden", view !== "home");
    $("viewOI").classList.toggle("hidden", view !== "oi");
    if ($("jSub")) $("jSub").classList.toggle("hidden", !isJ);
    $("viewJournal").classList.toggle("hidden", !(isJ && journalSub === "journal"));
    $("viewCapital").classList.toggle("hidden", !(isJ && journalSub === "capital"));
    $("viewScreener").classList.toggle("hidden", view !== "screener");
    if ($("viewIpo")) $("viewIpo").classList.toggle("hidden", view !== "ipo");
    $("viewRules").classList.toggle("hidden", view !== "rules");
    $("viewInsights").classList.toggle("hidden", view !== "insights");
    if ($("viewTradeMode")) $("viewTradeMode").classList.toggle("hidden", view !== "trademode");
    if ($("navMain")) $("navMain").classList.toggle("hidden", view === "trademode");
    if ($("navTrade")) $("navTrade").classList.toggle("hidden", view !== "trademode");
    if ($("tradeModeBtn")) $("tradeModeBtn").classList.toggle("active", view === "trademode");
    if ($("tradeModeLbl")) $("tradeModeLbl").textContent = view === "trademode" ? "Exit Trade Mode" : "Trade Mode";
    const oiCtl = $("oiControls");
    if (oiCtl) oiCtl.style.display = view === "oi" ? "" : "none";
    if (view === "home") loadHome();
    if (view === "oi") requestAnimationFrame(() => {   // charts were sized while hidden → refit
      try {
        if (typeof chgChart !== "undefined" && chgChart) chgChart.resize();
        if (typeof oiChart !== "undefined" && oiChart) oiChart.resize();
      } catch (e) { /* ignore */ }
    });
    if (isJ) { if (journalSub === "capital") loadCapital(); else loadJournal(); }
    if (view === "screener") loadScreener();
    if (view === "ipo") loadIpos();
    if (view === "rules") loadRules();
    if (view === "insights") loadInsights();
    if (view === "trademode") loadTradeMode();
  }
  // Journal ↔ Capital sub-tabs (Capital now lives inside the Journal page)
  document.querySelectorAll("#jSub .subtab").forEach((b) =>
    b.addEventListener("click", () => setJournalSub(b.dataset.sub)));
  function setJournalSub(sub) {
    journalSub = sub;
    document.querySelectorAll("#jSub .subtab").forEach((b) =>
      b.classList.toggle("active", b.dataset.sub === sub));
    $("viewJournal").classList.toggle("hidden", sub !== "journal");
    $("viewCapital").classList.toggle("hidden", sub !== "capital");
    if (sub === "capital") loadCapital(); else loadJournal();
  }

  // ---------------------------------------------------------- IPO calendar
  let ipoBound = false;
  async function loadIpos() {
    if (!ipoBound) {
      ipoBound = true;
      if ($("ipoRefresh")) $("ipoRefresh").addEventListener("click", () => loadIpos());
    }
    try {
      const d = await (await fetch("/api/ipos")).json();
      renderIpos(d);
    } catch (e) {
      if ($("ipoBody")) $("ipoBody").innerHTML = `<p class="jnote err">Could not load IPOs: ${esc(e.message)}</p>`;
    }
  }
  function ipoSubTxt(x) {
    return (x.sub != null && x.sub !== "" && !isNaN(x.sub)) ? (+x.sub).toFixed(2) + "×" : null;
  }
  function ipoCard(x, i) {
    const seg = x.segment === "SME" ? `<span class="ipo-seg sme">SME</span>` : `<span class="ipo-seg mb">Mainboard</span>`;
    const st = ipoSubTxt(x);
    const sub = st ? `<span class="ipo-sub">${st} subscribed</span>` : "";
    const dates = [x.open_date, x.close_date].filter(Boolean).join(" → ");
    const flag = x.opens_today ? `<span class="ipo-flag today">🟢 Opens today</span>`
      : x.closes_today ? `<span class="ipo-flag last">⏰ Last day today</span>` : "";
    return `<div class="ipo-card${x.opens_today ? " hl" : ""}" data-ii="${i}" title="Click for details">` +
      `<div class="ipo-top"><b class="ipo-name">${esc(x.name)}</b>${seg}</div>` +
      (flag ? `<div class="ipo-flags">${flag}</div>` : "") +
      `<div class="ipo-meta">` +
      (x.price_band ? `<span>💰 ${esc(x.price_band)}</span>` : "") +
      (dates ? `<span>📅 ${esc(dates)}</span>` : "") +
      (x.symbol ? `<span>🔖 ${esc(x.symbol)}</span>` : "") +
      sub + `</div></div>`;
  }
  let ipoAll = [];
  let ipoClickBound = false;
  function bindIpoClicks() {
    if (ipoClickBound) return; ipoClickBound = true;
    if ($("ipoBody")) $("ipoBody").addEventListener("click", (e) => {
      const card = e.target.closest(".ipo-card[data-ii]");
      if (card) openIpoModal(+card.dataset.ii);
    });
    if ($("ipoModalClose")) $("ipoModalClose").addEventListener("click", closeIpoModal);
    if ($("ipoModal")) $("ipoModal").addEventListener("click", (e) => { if (e.target === $("ipoModal")) closeIpoModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("ipoModal") && !$("ipoModal").classList.contains("hidden")) closeIpoModal(); });
  }
  function closeIpoModal() { if ($("ipoModal")) $("ipoModal").classList.add("hidden"); }
  function openIpoModal(i) {
    const x = ipoAll[i]; if (!x) return;
    $("ipoModalTitle").textContent = x.name || x.symbol || "IPO";
    const row = (k, v, cls) => v != null && v !== "" ? `<div class="ana-row"><span>${k}</span><b class="${cls || ""}">${v}</b></div>` : "";
    const seg = x.segment === "SME" ? `<span class="ipo-seg sme">SME</span>` : `<span class="ipo-seg mb">Mainboard</span>`;
    const flag = x.opens_today ? `<span class="ipo-flag today">🟢 Opens today</span>`
      : x.closes_today ? `<span class="ipo-flag last">⏰ Last day today</span>` : "";
    const st = ipoSubTxt(x);
    let html = `<div class="ipo-mhead">${seg}<span class="ipo-mstatus">${esc(x.status || "")}</span>${flag}</div>`;
    html += `<div class="ana-grid">` +
      row("Symbol", esc(x.symbol || "—")) +
      row("Segment", esc(x.segment || "—")) +
      row("Price band", esc(x.price_band || "—")) +
      row("Opens", esc(x.open_date || "—")) +
      row("Closes", esc(x.close_date || "—")) +
      row("Subscription", st, (x.sub >= 1 ? "pos" : "")) +
      row("Issue size", x.issue_size ? Number(x.issue_size).toLocaleString("en-IN") + " shares" : "—") +
      `</div>`;
    // Jump straight to the IPO's detail page (GMP, lot size, subscription, allotment)
    const q = encodeURIComponent((x.name || x.symbol || "") + " IPO GMP lot size chittorgarh");
    const url = "https://duckduckgo.com/?q=!ducky+" + q;
    html += `<a class="ana-report" href="${url}" target="_blank" rel="noopener noreferrer">🔎 Full details, GMP &amp; lot size, allotment ↗</a>`;
    html += `<p class="ana-src">Opens the IPO's detail page (GMP, lot size, dates). Data as published — <b>not an offer or advice</b> to apply.</p>`;
    $("ipoModalBody").innerHTML = html;
    $("ipoModal").classList.remove("hidden");
  }
  function renderIpos(d) {
    bindIpoClicks();
    if ($("ipoUpdated")) $("ipoUpdated").textContent = d.updated ? "Updated " + d.updated : "";
    const open = d.open || [], up = d.upcoming || [];
    const all = open.concat(up);
    ipoAll = all;                           // for the detail popup (index into this)
    const todays = all.filter((x) => x.opens_today);
    const closing = open.filter((x) => x.closes_today);
    let html = "";
    if (!d.ok && !all.length)
      html += `<p class="jnote">${esc(d.note || "IPO data is unavailable right now.")}</p>`;
    // "today" banner — the quick answer to "any IPO today?"
    if (todays.length || closing.length) {
      const bits = [];
      if (todays.length) bits.push(`<b>${todays.length}</b> opening today`);
      if (closing.length) bits.push(`<b>${closing.length}</b> closing today (last day to apply)`);
      html += `<div class="ipo-banner">🔔 ${bits.join(" · ")}</div>`;
    }
    html += `<div class="ipo-sect">🟢 Open now <span class="ipo-count">${open.length}</span></div>`;
    html += open.length ? `<div class="ipo-grid">${open.map((x, i) => ipoCard(x, i)).join("")}</div>`
      : `<p class="modal-sub">No IPOs open for subscription right now.</p>`;
    html += `<div class="ipo-sect">🔜 Upcoming <span class="ipo-count">${up.length}</span></div>`;
    html += up.length ? `<div class="ipo-grid">${up.map((x, i) => ipoCard(x, open.length + i)).join("")}</div>`
      : `<p class="modal-sub">No upcoming IPOs announced yet.</p>`;
    if (d.url) html += `<a class="ana-report" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">📋 Full IPO details on NSE ↗</a>`;
    $("ipoBody").innerHTML = html;
    setIpoBadge(d);
  }
  // Notification badge on the IPO tab — visible from any screen.
  function setIpoBadge(d) {
    const b = $("ipoBadge"); if (!b) return;
    const open = (d.open || []), up = (d.upcoming || []);
    const todays = open.concat(up).filter((x) => x.opens_today).length;
    const n = open.length;
    if (!n && !todays) { b.classList.add("hidden"); return; }
    b.textContent = todays ? "today" : String(n);
    b.classList.toggle("today", todays > 0);
    b.classList.remove("hidden");
  }
  async function refreshIpoBadge() {
    try { setIpoBadge(await (await fetch("/api/ipos")).json()); } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------- Dashboard (home)
  let homePeriod = "this_month";
  let homeData = null;          // last overview payload
  let calMonth = null;          // {y, m} shown in the calendar
  let homeBound = false;
  const homeCharts = {};
  const _MONF = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  const round2 = (v) => Math.round((+v || 0) * 100) / 100;
  function moneyShort(v) {
    v = +v || 0; const a = Math.abs(v), sg = v < 0 ? "-" : "";
    if (a >= 1e7) return sg + "₹" + fmt(a / 1e7, 2) + "Cr";
    if (a >= 1e5) return sg + "₹" + fmt(a / 1e5, 2) + "L";
    if (a >= 1e3) return sg + "₹" + fmt(a / 1e3, 1) + "k";
    return sg + "₹" + fmt(a, 0);
  }
  let homeCustStart = "", homeCustEnd = "";
  function homeRange(p) {
    const pad = (n) => String(n).padStart(2, "0");
    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, t = new Date();
    if (p === "custom") return { start: homeCustStart || "", end: homeCustEnd || "" };
    if (p === "this_month") return { start: iso(new Date(t.getFullYear(), t.getMonth(), 1)), end: iso(t) };
    if (p === "3m") { const s = new Date(t); s.setDate(s.getDate() - 90); return { start: iso(s), end: iso(t) }; }
    if (p === "1m") { const s = new Date(t); s.setDate(s.getDate() - 30); return { start: iso(s), end: iso(t) }; }
    if (p === "1w") { const s = new Date(t); s.setDate(s.getDate() - 7); return { start: iso(s), end: iso(t) }; }
    if (p && p.indexOf("fy:") === 0) { const y = +p.slice(3); return { start: `${y}-04-01`, end: `${y + 1}-03-31` }; }
    return { start: "", end: "" };
  }
  function bindHomeOnce() {
    if (homeBound) return; homeBound = true;
    // Inject financial-year presets (current FY + 3 prior) before the Custom button.
    const slot = $("fySlot");
    if (slot && !slot.childElementCount) {
      const now = new Date(), cfy = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
      let html = "";
      for (let i = 0; i < 4; i++) { const y = cfy - i; html += `<button class="dper" data-p="fy:${y}">FY ${String(y).slice(2)}-${String(y + 1).slice(2)}</button>`; }
      slot.innerHTML = html;
    }
    // Filter dropdown (FY + Custom): open/close + outside-click / Escape close.
    const dfMenu = $("dfMenu"), dfBtn = $("dfBtn");
    function syncPeriodUI(activePeriod) {
      document.querySelectorAll("#dashPeriodBar .dper").forEach((x) =>
        x.classList.toggle("active", x.dataset.p === activePeriod));
      const inMenu = activePeriod && (activePeriod.indexOf("fy:") === 0 || activePeriod === "custom");
      if (dfBtn) dfBtn.classList.toggle("active", !!inMenu);
      if (!inMenu && $("dfLabel")) $("dfLabel").textContent = "Filter";
      if ($("homeCustom")) $("homeCustom").classList.toggle("hidden", activePeriod !== "custom");
    }
    function closeDf() {
      if (!dfMenu || dfMenu.classList.contains("hidden")) return;
      dfMenu.classList.add("hidden");
      syncPeriodUI(homePeriod);
    }
    if (dfBtn) dfBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!dfMenu) return;
      if (dfMenu.classList.contains("hidden")) {
        dfMenu.classList.remove("hidden");
        if (homePeriod === "custom" && $("homeCustom")) $("homeCustom").classList.remove("hidden");
      } else closeDf();
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".date-filter")) closeDf();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDf(); });
    // Delegate clicks across quick pills + FY grid + Custom button.
    if ($("dashPeriodBar")) $("dashPeriodBar").addEventListener("click", (e) => {
      const b = e.target.closest(".dper"); if (!b) return;
      const p = b.dataset.p;
      if (p === "custom") {
        document.querySelectorAll("#dashPeriodBar .dper").forEach((x) => x.classList.toggle("active", x === b));
        if ($("homeCustom")) $("homeCustom").classList.remove("hidden");
        return; // wait for Apply
      }
      homePeriod = p; calMonth = null;
      if (p.indexOf("fy:") === 0 && $("dfLabel")) $("dfLabel").textContent = b.textContent.trim();
      if (dfMenu) dfMenu.classList.add("hidden");
      syncPeriodUI(homePeriod);
      loadHome();
    });
    if ($("homeCustApply")) $("homeCustApply").addEventListener("click", () => {
      homeCustStart = $("homeCustStart").value; homeCustEnd = $("homeCustEnd").value;
      if (!homeCustStart && !homeCustEnd) { homeMsg("Pick a From and/or To date.", true); return; }
      if (homeCustStart && homeCustEnd && homeCustStart > homeCustEnd) { homeMsg("From date must be on or before To date.", true); return; }
      if ($("dfLabel")) $("dfLabel").textContent = (homeCustStart || "…") + " → " + (homeCustEnd || "…");
      homePeriod = "custom"; calMonth = null;
      if (dfMenu) dfMenu.classList.add("hidden");
      syncPeriodUI(homePeriod);
      loadHome();
    });
    if ($("calPrev")) $("calPrev").addEventListener("click", () => shiftCal(-1));
    if ($("calNext")) $("calNext").addEventListener("click", () => shiftCal(1));
    if ($("homeSyncBtn")) $("homeSyncBtn").addEventListener("click", homeSync);
    if ($("homeCapBtn")) $("homeCapBtn").addEventListener("click", () => {
      const pop = $("homeCapEdit"); if (!pop) return;
      pop.classList.toggle("hidden");
      if (!pop.classList.contains("hidden") && $("homeCapIn")) {
        $("homeCapIn").value = (homeData?.summary?.opening_capital || 0) || "";
        $("homeCapIn").focus();
      }
    });
    const saveHomeCap = async () => {
      const v = Math.max(0, Number($("homeCapIn")?.value) || 0);
      try {
        await fetch("/api/journal/capital", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ opening: v }) });
        if ($("homeCapEdit")) $("homeCapEdit").classList.add("hidden");
        if ($("openingCapital")) $("openingCapital").value = v || "";
        await loadHome();
      } catch (e) { homeMsg("Could not save capital: " + e.message, true); }
    };
    if ($("homeCapSave")) $("homeCapSave").addEventListener("click", saveHomeCap);
    if ($("homeCapIn")) $("homeCapIn").addEventListener("keydown", (e) => { if (e.key === "Enter") saveHomeCap(); });
  }
  function homeMsg(t, err) {
    const m = $("homeSyncMsg"); if (!m) return;
    m.className = "jnote" + (err ? " err" : ""); m.textContent = t; m.classList.remove("hidden");
    setTimeout(() => m.classList.add("hidden"), 6000);
  }
  async function homeSync() {
    const btn = $("homeSyncBtn"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Syncing history…";
    try {
      // Pull dated trade history for the selected range (Fyers Reports API) — not
      // just today — so past trades show without a CSV. All-time → last ~2 years.
      let r = homeRange(homePeriod);
      if (!r.start) {
        const s = new Date(); s.setFullYear(s.getFullYear() - 2);
        r = { start: s.toISOString().slice(0, 10), end: new Date().toISOString().slice(0, 10) };
      }
      const resp = await fetch(`/api/journal/sync-history?start=${r.start}&end=${r.end}`, { method: "POST" });
      const d = await resp.json().catch(() => ({}));
      const srcName = (homeData && homeData.source) ? homeData.source.replace(/^\w/, (c) => c.toUpperCase()) : "broker";
      if (!resp.ok) {
        if (resp.status === 401) { homeMsg(`Log in to ${srcName} before syncing.`, true); return; }
        throw new Error(d.detail || d.error || "Sync failed");
      }
      if (!d.round_trips) { homeMsg(d.note || "No trades found in this range.", true); }
      else homeMsg(`✅ Synced ${d.round_trips} trades from ${srcName}` +
        (d.date_range ? ` (${d.date_range[0]} → ${d.date_range[1]})` : "") + ` · P&L ${signed(d.realized || 0)}`);
      loadHome();
    } catch (e) { homeMsg("Sync failed: " + e.message, true); }
    finally { btn.disabled = false; btn.textContent = old; }
  }
  let homeLoadSeq = 0;
  async function loadHome() {
    bindHomeOnce();
    const seq = ++homeLoadSeq;                 // guard against stale responses
    const dash = document.querySelector(".dash");
    if (dash) dash.classList.add("loading");
    if ($("homeBusy")) $("homeBusy").classList.remove("hidden");
    const r = homeRange(homePeriod);
    const q = (r.start || r.end) ? `?start=${r.start}&end=${r.end}` : "";
    try {
      const d = await (await fetch("/api/journal/overview" + q)).json();
      if (seq !== homeLoadSeq) return;         // a newer filter was applied; drop this
      homeData = d;
      renderHome(homeData);
    } catch (e) {
      if (seq === homeLoadSeq && $("homeEmpty")) $("homeEmpty").textContent = "Could not load dashboard: " + e.message;
    } finally {
      if (seq === homeLoadSeq) {
        if (dash) dash.classList.remove("loading");
        if ($("homeBusy")) $("homeBusy").classList.add("hidden");
      }
    }
  }
  function renderHome(d) {
    const s = d.summary || {};
    const daily = (s.daily || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    let gp = 0, gl = 0, pdays = 0, ldays = 0;
    daily.forEach((x) => { const v = +x.realized || 0; if (v > 0) { gp += v; pdays++; } else if (v < 0) { gl += -v; ldays++; } });
    const pf = gl > 0 ? gp / gl : (gp > 0 ? Infinity : 0);
    const dayWin = (pdays + ldays) ? pdays / (pdays + ldays) * 100 : 0;
    const net = s.net_realized != null ? s.net_realized : (s.total_realized || 0);
    const avgWin = pdays ? gp / pdays : 0, avgLoss = ldays ? gl / ldays : 0;
    const st = s.current_streak || {}; const stTxt = st.count ? `${st.count}${st.type === "profit" ? "🟢" : st.type === "loss" ? "🔴" : ""}` : "—";
    const gcol = (v) => v >= 55 ? "var(--put)" : v >= 40 ? "#f5a623" : "var(--call)";
    const kcard = (l, v, cls, sub, viz) =>
      `<div class="kpi${viz ? " khas" : ""}"><div class="kpi-main"><div class="kpi-l">${l}</div>` +
      `<div class="kpi-v ${cls || ""}">${v}</div><div class="kpi-s">${sub}</div></div>${viz ? `<div class="kpi-g">${viz}</div>` : ""}</div>`;
    $("homeKpis").innerHTML =
      kcard("Net P&L", signed(net), net >= 0 ? "pos" : "neg", s.total_costs ? "after ₹" + fmt(s.total_costs, 0) + " costs" : "gross", "") +
      kcard("Trade win %", (s.win_rate || 0) + "%", "", `${s.wins || 0}W · ${s.losses || 0}L`, gArc(s.win_rate || 0, gcol(s.win_rate || 0))) +
      kcard("Profit factor", pf === Infinity ? "∞" : fmt(pf, 2), pf >= 1 ? "pos" : "neg", "profit ÷ loss", gDonut(gp, gl)) +
      kcard("Day win %", fmt(dayWin, 1) + "%", "", `${pdays}W · ${ldays}L days`, gArc(dayWin, gcol(dayWin))) +
      kcard("Avg win/loss", avgLoss > 0 ? fmt(avgWin / avgLoss, 2) : (avgWin > 0 ? "∞" : "—"), "", `${moneyShort(avgWin)} / ${moneyShort(avgLoss)}`, gBar(avgWin, avgLoss)) +
      kcard("Total trades", String(s.total_trades || 0), "", `streak ${stTxt}`, "");

    if ($("homeEmpty")) $("homeEmpty").textContent = daily.length ? "" :
      "No trades yet — sync from Fyers/Dhan or add trades in the Journal, and your dashboard fills in automatically.";

    // P&L & charges breakdown (like a broker's Realised-P&L summary)
    if ($("homePnl")) {
      const totDays = pdays + ldays, profPct = totDays ? Math.round(pdays / totDays * 100) : 0;
      const neg = (v) => "−₹" + fmt(Math.abs(+v || 0), 0);
      const pItem = (k, v, cls, sub) => `<div class="pnl-i"><div class="pnl-k">${k}</div><div class="pnl-v ${cls || ""}">${v}</div>${sub ? `<div class="pnl-sub">${sub}</div>` : ""}</div>`;
      $("homePnl").innerHTML = daily.length ? (
        pItem("Gross P&L", signed(s.total_realized || 0), (s.total_realized || 0) >= 0 ? "pos" : "neg") +
        pItem("Brokerage", neg(s.total_brokerage), "neg", "excl. taxes") +
        pItem("Total charges", neg(s.total_costs), "neg", s.costs_estimated ? "STT·GST·stamp (est.)" : "STT·GST·stamp") +
        `<div class="pnl-i pnl-days"><div class="pnl-krow"><span class="pnl-k">Profitable days</span><span class="pnl-badge ${profPct >= 50 ? "pos" : "neg"}">${profPct}% win</span></div>` +
        `<div class="pnl-bar">${profPct > 0 ? `<span class="pb-g" style="width:${profPct}%"></span>` : ""}${profPct < 100 ? `<span class="pb-r" style="width:${100 - profPct}%"></span>` : ""}</div>` +
        `<div class="pnl-sub">${pdays} win · ${ldays} loss days (${100 - profPct}% loss)</div></div>`
      ) : "";
    }

    let cum = 0, peak = 0; const labels = [], equity = [], eqOpen = [], dd = [];
    const opening = +s.opening_capital || 0;
    if ($("homeCapVal")) $("homeCapVal").textContent = opening > 0 ? moneyShort(opening) : "₹0";
    if ($("homeCapBtn")) {
      const bf = s.broker_funds && (s.broker_funds.total || s.broker_funds.opening);
      $("homeCapBtn").title = s.capital_source === "broker"
        ? `Auto-loaded from ${(d.source || "broker").toUpperCase()} funds (${inr(opening)}) — click to override`
        : opening > 0
          ? `Day-1 opening capital (${inr(opening)}) — rolls forward daily with P&L`
          : `Click to set Day-1 opening capital${bf != null ? ` (broker balance: ${inr(bf)})` : ""}`;
    }
    daily.forEach((x) => {
      eqOpen.push(round2(cum));
      cum += (+x.realized || 0);
      labels.push(x.date);
      equity.push(round2(cum));
      peak = Math.max(peak, cum);
      dd.push(round2(cum - peak));
    });
    const dayOpen = eqOpen.map((v) => round2(opening + v));
    drawHomeLine("homeEquity", labels, equity, dayOpen, "equity");
    drawHomeLine("homeDrawdown", labels, dd, dayOpen, "dd");
    drawBalance(labels, equity, dayOpen, opening);
    drawHomeBars("homeDaily", daily, dayOpen);
    drawWinLoss("homeWinLoss", pdays, ldays);

    if (!calMonth) {
      const base = daily.length ? new Date(daily[daily.length - 1].date) : new Date();
      calMonth = { y: base.getFullYear(), m: base.getMonth() };
    }
    renderCal(daily);
    renderHomeTrades(d.recent_trades || []);
    drawRadar(s, daily, pf, dayWin);
    drawScatter(d.recent_trades || [], opening);
    drawHeatmap(daily);
    if ($("homeSyncBtn")) {
      const bLabel = { fyers: "Fyers", dhan: "Dhan", upstox: "Upstox" }[d.source];
      $("homeSyncBtn").style.display = d.can_sync && bLabel ? "" : "none";
      if (bLabel) $("homeSyncBtn").textContent = "⬇ Sync from " + bLabel;
    }
  }
  // KPI mini-gauges
  function gArc(pct, color) {
    pct = Math.max(0, Math.min(100, pct || 0));
    const C = Math.PI * 25, off = (C - C * pct / 100).toFixed(1), cs = C.toFixed(1);
    return `<svg viewBox="0 0 64 40" class="kg-arc" style="--gcol:${color}">` +
      `<path d="M7,33 A25,25 0 0 1 57,33" fill="none" stroke="var(--line)" stroke-width="5.5" stroke-linecap="round" opacity=".55"/>` +
      `<path d="M12,33 A20,20 0 0 1 52,33" fill="none" stroke="var(--line)" stroke-width="1" stroke-dasharray="1.5 3.5" opacity=".45"/>` +
      `<path class="kg-fill" d="M7,33 A25,25 0 0 1 57,33" fill="none" stroke="${color}" stroke-width="5.5" stroke-linecap="round" stroke-dasharray="${cs}" stroke-dashoffset="${off}" style="--off:${off};--c:${cs}"/>` +
      `<text x="32" y="31" text-anchor="middle" class="kg-t">${Math.round(pct)}<tspan class="kg-u">%</tspan></text></svg>`;
  }
  function gDonut(green, red) {
    const tot = green + red, gp = tot ? green / tot : 0, C = 2 * Math.PI * 13.5;
    const off = (C * (1 - gp)).toFixed(1), cs = C.toFixed(1);
    return `<svg viewBox="0 0 38 38" class="kg-d"><circle cx="19" cy="19" r="13.5" fill="none" stroke="var(--call)" stroke-width="5" opacity=".85"/>` +
      `<circle class="kg-fill" cx="19" cy="19" r="13.5" fill="none" stroke="var(--put)" stroke-width="5" stroke-linecap="round" stroke-dasharray="${cs}" stroke-dashoffset="${off}" transform="rotate(-90 19 19)" style="--off:${off};--c:${cs}"/></svg>`;
  }
  function gBar(green, red) {
    const tot = green + red, gp = tot ? Math.round(green / tot * 100) : 50;
    return `<div class="kg-bar">${gp > 0 ? `<span class="kb-g" style="width:${gp}%"></span>` : ""}${gp < 100 ? `<span class="kb-r" style="width:${100 - gp}%"></span>` : ""}</div>`;
  }
  function drawHeatmap(daily) {
    const el = $("homeHeat"); if (!el) return;
    if (!daily.length) { el.innerHTML = `<p class="modal-sub">No trades yet.</p>`; return; }
    const pad = (n) => String(n).padStart(2, "0");
    const byDate = {}; daily.forEach((x) => byDate[x.date] = (byDate[x.date] || 0) + (+x.realized || 0));
    const dts = daily.map((x) => x.date).sort();
    const maxAbs = Math.max(1, ...Object.values(byDate).map((v) => Math.abs(v)));
    const start = new Date(dts[0] + "T00:00:00"); start.setDate(start.getDate() - start.getDay());
    const end = new Date(dts[dts.length - 1] + "T00:00:00");
    const weeks = []; let cur = new Date(start);
    while (cur <= end) {
      const col = [];
      for (let i = 0; i < 7; i++) {
        const ds = `${cur.getFullYear()}-${pad(cur.getMonth() + 1)}-${pad(cur.getDate())}`;
        col.push({ ds, v: byDate[ds] });
        cur.setDate(cur.getDate() + 1);
      }
      weeks.push(col);
    }
    const dows = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    // first row of month labels aligned to week columns
    let months = `<div class="hlbl"></div>`, lastM = -1;
    for (let w = 0; w < weeks.length; w++) {
      const d0 = weeks[w][0];
      const mo = d0.ds ? +d0.ds.slice(5, 7) - 1 : -1;
      months += (mo !== lastM) ? `<div class="hmon">${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][mo]}</div>` : `<div class="hmon"></div>`;
      lastM = mo;
    }
    const tipDate = (ds) => {
      const dt = new Date(ds + "T00:00:00");
      return isNaN(dt) ? ds : `${dows[dt.getDay()]}, ${dt.getDate()} ${MON[dt.getMonth()]} ${dt.getFullYear()}`;
    };
    const cellPx = weeks.length <= 10 ? 18 : 14;
    let html = `<div class="heat-scroll"><div class="heat" style="--hc:${cellPx}px;grid-template-columns:auto repeat(${weeks.length},${cellPx}px)">` + months;
    for (let r = 0; r < 7; r++) {
      html += `<div class="hlbl">${dows[r]}</div>`;              // weekday label
      for (let w = 0; w < weeks.length; w++) {
        const c = weeks[w][r];
        const td = tipDate(c.ds);
        if (c.v == null) {
          html += `<div class="hc" data-tip-title="${td}" data-tip-val="No trades" data-tip-cls="flat"></div>`;
          continue;
        }
        // intensity scales with |P&L|: bigger profit/loss → stronger colour
        const op = (0.2 + Math.min(1, Math.abs(c.v) / maxAbs) * 0.8).toFixed(2);
        const cls = c.v >= 0 ? "pos" : "neg";
        html += `<div class="hc ${c.v >= 0 ? "g" : "r"}" style="opacity:${op}" data-tip-title="${td}" data-tip-val="${signed(c.v)}" data-tip-cls="${cls}" data-range-start="${c.ds}" data-range-end="${c.ds}" data-range-title="${td}"></div>`;
      }
    }
    html += `</div></div><div class="heat-leg"><span>More loss</span>` +
      `<i class="r" style="opacity:1"></i><i class="r" style="opacity:.5"></i><i></i>` +
      `<i class="g" style="opacity:.5"></i><i class="g" style="opacity:1"></i><span>More profit</span></div>`;
    el.innerHTML = html;
    const sc = el.querySelector(".heat-scroll");
    if (sc) {
      sc.scrollLeft = sc.scrollWidth;
      sc.addEventListener("scroll", hideTip, { passive: true });
    }
  }
  // Floating animated tooltip for heatmap & calendar cells
  let _tipEl = null;
  function hideTip() { if (_tipEl) _tipEl.classList.remove("show"); }
  function showTip(target) {
    if (!target) return;
    if (!_tipEl) {
      _tipEl = document.createElement("div");
      _tipEl.className = "ui-tip";
      _tipEl.setAttribute("role", "tooltip");
      document.body.appendChild(_tipEl);
    }
    const title = target.dataset.tipTitle || "";
    const val = target.dataset.tipVal || "";
    const cls = target.dataset.tipCls || "flat";
    const sub = target.dataset.tipSub || "";
    _tipEl.innerHTML =
      `<div class="ut-date">${esc(title)}</div>` +
      `<div class="ut-val ${esc(cls)}">${esc(val)}</div>` +
      (sub ? `<div class="ut-sub">${esc(sub)}</div>` : "");
    const r = target.getBoundingClientRect();
    const cx = Math.max(84, Math.min(window.innerWidth - 84, r.left + r.width / 2));
    const flip = r.top < 72;
    _tipEl.classList.toggle("flip", flip);
    _tipEl.style.left = cx + "px";
    _tipEl.style.top = (flip ? r.bottom : r.top) + "px";
    _tipEl.classList.add("show");
  }
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest("[data-tip-title]");
    if (t) showTip(t); else hideTip();
  });
  document.addEventListener("mouseout", (e) => {
    if (!e.relatedTarget || !e.relatedTarget.closest || !e.relatedTarget.closest("[data-tip-title]")) hideTip();
  });
  window.addEventListener("scroll", hideTip, { passive: true });
  const _clamp = (v) => Math.max(0, Math.min(100, v));
  function drawRadar(s, daily, pf, dayWin) {
    const el = $("homeRadar"); if (!el || typeof Chart === "undefined") return;
    let winSum = 0, winN = 0, lossSum = 0, lossN = 0, cum = 0, peak = 0, maxDD = 0;
    daily.forEach((x) => {
      const v = +x.realized || 0;
      if (v > 0) { winSum += v; winN++; } else if (v < 0) { lossSum += -v; lossN++; }
      cum += v; peak = Math.max(peak, cum); maxDD = Math.max(maxDD, peak - cum);
    });
    const avgWin = winN ? winSum / winN : 0, avgLoss = lossN ? lossSum / lossN : 0;
    const wl = avgLoss > 0 ? avgWin / avgLoss : (avgWin > 0 ? 3 : 0);
    const net = cum, recovery = maxDD > 0 ? net / maxDD : (net > 0 ? 3 : 0);
    const axes = [
      _clamp(s.win_rate || 0),
      _clamp(pf === Infinity ? 100 : pf * 33.3),
      _clamp(wl * 50),
      _clamp(dayWin),
      _clamp(maxDD > 0 ? (net > 0 ? net / (net + maxDD) * 100 : 0) : (net > 0 ? 100 : 50)),
      _clamp(recovery * 33.3),
    ];
    const overall = Math.round(axes.reduce((a, b) => a + b, 0) / axes.length);
    if ($("scoreVal")) $("scoreVal").textContent = daily.length ? overall : "—";
    if ($("scoreTag")) $("scoreTag").textContent = !daily.length ? "" :
      overall >= 70 ? "Strong" : overall >= 50 ? "Solid" : overall >= 30 ? "Developing" : "Needs work";
    const c = homeColors();
    if (homeCharts.homeRadar) homeCharts.homeRadar.destroy();
    homeCharts.homeRadar = new Chart(el, {
      type: "radar",
      data: {
        labels: ["Win %", "Profit factor", "Avg win/loss", "Day win %", "Drawdown ctrl", "Recovery"],
        datasets: [{ data: axes, borderColor: c.put, backgroundColor: c.put + "33", borderWidth: 2, pointRadius: 2, pointBackgroundColor: c.put }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (x) => x.label + ": " + Math.round(x.parsed.r) } } },
        scales: { r: { min: 0, max: 100, grid: { color: c.grid }, angleLines: { color: c.grid }, pointLabels: { color: c.tick, font: { size: 10 } }, ticks: { display: false, stepSize: 25 } } }
      }
    });
  }
  function _capRefDs(label, data, yAxisID) {
    const ds = {
      type: "line", label, data, borderColor: "#f0b429", borderWidth: 1.5,
      borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 3, fill: false,
      tension: .35, cubicInterpolationMode: "monotone", order: 0, hidden: true,
    };
    if (yAxisID) ds.yAxisID = yAxisID;
    return ds;
  }
  function _y1Scale() {
    return {
      position: "right",
      display: false,
      grid: { display: false },
      ticks: { color: "#f0b429", maxTicksLimit: 5, font: { size: 9.5 }, callback: (v) => moneyShort(v) },
    };
  }
  function _capLegend(color, size) {
    return {
      display: true,
      labels: { color, boxWidth: 12, font: { size: size || 10 } },
      onClick(e, item, legend) {
        Chart.defaults.plugins.legend.onClick.call(this, e, item, legend);
        const ch = legend.chart;
        if (ch.options.scales && ch.options.scales.y1) {
          ch.options.scales.y1.display = ch.isDatasetVisible(1);
          ch.update();
        }
      },
    };
  }
  function drawScatter(trades, opening) {
    const el = $("homeScatter"); if (!el || typeof Chart === "undefined") return;
    const c = homeColors(), pts = [], cols = [];
    (trades || []).forEach((t) => {
      if (t.pnl == null || !t.time) return;
      const m = String(t.time).match(/(\d{1,2}):(\d{2})/);
      if (!m) return;
      const h = +m[1] + (+m[2]) / 60;
      if (h < 8 || h > 16.5) return;
      pts.push({ x: Math.round(h * 100) / 100, y: +t.pnl }); cols.push((+t.pnl) >= 0 ? c.put : c.call);
    });
    if (homeCharts.homeScatter) homeCharts.homeScatter.destroy();
    const op = +opening || 0;
    const capDs = _capRefDs(op > 0 ? `Opening capital (${moneyShort(op)} · ₹0)` : "Opening capital (₹0)", [{ x: 9, y: 0 }, { x: 15.75, y: 0 }]);
    homeCharts.homeScatter = new Chart(el, {
      type: "scatter",
      data: { datasets: [{ label: "Trade P&L", data: pts, pointBackgroundColor: cols, pointBorderColor: cols, pointRadius: 3, pointHoverRadius: 5 }, capDs] },
      options: {
        responsive: true, maintainAspectRatio: false, plugins: {
          legend: { display: true, labels: { color: c.tick, boxWidth: 12, font: { size: 10 } } },
          tooltip: {
            filter: (x) => x.datasetIndex === 0,
            callbacks: { label: (x) => { const hh = Math.floor(x.parsed.x), mm = Math.round((x.parsed.x - hh) * 60); return `${hh}:${String(mm).padStart(2, "0")} · ₹${fmt(x.parsed.y, 0)}`; } }
          }
        },
        scales: {
          x: { min: 9, max: 15.75, grid: { color: c.grid }, ticks: { color: c.tick, stepSize: 1, font: { size: 10 }, callback: (v) => v + ":00" } },
          y: { grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 }, callback: (v) => moneyShort(v) } }
        }
      }
    });
  }
  function homeColors() {
    const css = getComputedStyle(document.body);
    const g = (n, f) => (css.getPropertyValue(n) || f).trim();
    return {
      grid: g("--line", "rgba(127,127,127,.15)"), tick: g("--muted", "#9aa4b2"),
      put: g("--put", "#26a69a"), call: g("--call", "#ef5350")
    };
  }
  function drawHomeLine(id, labels, data, dayOpen, kind) {
    const el = $(id); if (!el || typeof Chart === "undefined") return;
    if (homeCharts[id]) homeCharts[id].destroy();
    const c = homeColors(), isDd = kind === "dd";
    const last = data.length ? data[data.length - 1] : 0;
    const color = isDd ? c.call : (last >= 0 ? c.put : c.call);
    const mainLbl = isDd ? "Drawdown" : "Cumulative P&L";
    const capDs = _capRefDs("Opening capital", dayOpen || labels.map(() => 0), "y1");
    const mainDs = {
      label: mainLbl, data, borderColor: color,
      backgroundColor: color + "22",
      borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 4,
      fill: isDd ? true : { target: { value: 0 }, above: c.put + "22", below: c.call + "22" },
      tension: .4, cubicInterpolationMode: "monotone", yAxisID: "y",
    };
    if (!isDd) {
      mainDs.segment = { borderColor: (ctx) => (ctx.p1.parsed.y >= ctx.p0.parsed.y ? c.put : c.call) };
    }
    homeCharts[id] = new Chart(el, {
      type: "line",
      data: { labels, datasets: [mainDs, capDs] },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, elements: { line: { borderJoinStyle: "round", borderCapStyle: "round" } },
        plugins: {
          legend: _capLegend(c.tick, 10),
          tooltip: { callbacks: { title: (x) => x[0] ? x[0].label : "", label: (x) => x.dataset.label + ": ₹" + fmt(x.parsed.y, 0) } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: c.tick, maxTicksLimit: 6, font: { size: 10 } } },
          y: { position: "left", grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 }, callback: (v) => moneyShort(v) } },
          y1: _y1Scale(),
        }
      }
    });
  }
  function drawBalance(labels, equity, dayOpen, opening) {
    const el = $("homeBalance"); if (!el || typeof Chart === "undefined") return;
    if (homeCharts.homeBalance) homeCharts.homeBalance.destroy();
    const c = homeColors();
    const op = +opening || 0;
    const bal = equity.map((v) => round2(op + v));
    const up = bal.length ? bal[bal.length - 1] >= (op || bal[0]) : true;
    const col = up ? c.put : c.call;
    const ds = [
      {
        label: "Balance", data: bal, borderColor: col, backgroundColor: col + "22",
        borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 4,
        fill: { target: { value: op }, above: c.put + "22", below: c.call + "22" },
        segment: { borderColor: (ctx) => (ctx.p1.parsed.y >= ctx.p0.parsed.y ? c.put : c.call) },
        tension: .4, cubicInterpolationMode: "monotone",
      },
      _capRefDs("Opening capital", dayOpen || labels.map(() => op)),
    ];
    homeCharts.homeBalance = new Chart(el, {
      type: "line", data: { labels, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: true, labels: { color: c.tick, boxWidth: 12, font: { size: 10 } } }, tooltip: { callbacks: { label: (x) => x.dataset.label + ": ₹" + fmt(x.parsed.y, 0) } } },
        scales: { x: { grid: { display: false }, ticks: { color: c.tick, maxTicksLimit: 6, font: { size: 10 } } }, y: { grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 }, callback: (v) => moneyShort(v) } } }
      }
    });
  }
  function drawHomeBars(id, daily, dayOpen) {
    const el = $(id); if (!el || typeof Chart === "undefined") return;
    if (homeCharts[id]) homeCharts[id].destroy();
    const c = homeColors();
    const labels = daily.map((x) => x.date), data = daily.map((x) => +x.realized || 0);
    const capDs = _capRefDs("Opening capital", dayOpen || labels.map(() => 0), "y1");
    homeCharts[id] = new Chart(el, {
      type: "bar",
      data: { labels, datasets: [{ label: "Daily P&L", data, backgroundColor: data.map((v) => v >= 0 ? c.put : c.call), borderWidth: 0, yAxisID: "y" }, capDs] },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: {
          legend: _capLegend(c.tick, 10),
          tooltip: { callbacks: { label: (x) => x.dataset.label + ": ₹" + fmt(x.parsed.y, 0) } }
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: c.tick, maxTicksLimit: 8, font: { size: 10 } } },
          y: { position: "left", grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 }, callback: (v) => moneyShort(v) } },
          y1: _y1Scale(),
        }
      }
    });
  }
  function drawWinLoss(id, w, l) {
    const el = $(id); if (!el || typeof Chart === "undefined") return;
    if (homeCharts[id]) homeCharts[id].destroy();
    const c = homeColors();
    homeCharts[id] = new Chart(el, {
      type: "doughnut",
      data: { labels: ["Win days", "Loss days"], datasets: [{ data: [w, l], backgroundColor: [c.put, c.call], borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: "60%", plugins: { legend: { position: "bottom", labels: { color: c.tick, boxWidth: 12, font: { size: 11 } } } } }
    });
  }
  function shiftCal(delta) {
    if (!calMonth) return;
    let m = calMonth.m + delta, y = calMonth.y;
    if (m < 0) { m = 11; y--; } if (m > 11) { m = 0; y++; }
    calMonth = { y, m };
    renderCal((homeData && homeData.summary && homeData.summary.daily) || []);
  }
  function renderCal(daily) {
    if (!calMonth || !$("homeCal")) return;
    const { y, m } = calMonth;
    const pad = (n) => String(n).padStart(2, "0");
    $("calTitle").textContent = _MONF[m] + " " + y;
    const byDate = {}; daily.forEach((x) => { byDate[x.date] = (byDate[x.date] || 0) + (+x.realized || 0); });
    const firstDow = new Date(y, m, 1).getDay();            // 0 = Sun
    const days = new Date(y, m + 1, 0).getDate();
    const dows = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let html = dows.map((d) => `<div class="cal-dow">${d}</div>`).join("") + `<div class="cal-dow cal-wkh">Week</div>`;
    let mtot = 0, mdays = 0, day = 1 - firstDow, week = 1;
    while (day <= days) {
      let row = "", wtot = 0, wdays = 0, wStart = null, wEnd = null;
      for (let i = 0; i < 7; i++, day++) {
        if (day < 1 || day > days) { row += `<div class="cal-cell empty"></div>`; continue; }
        const ds = `${y}-${pad(m + 1)}-${pad(day)}`;
        if (!wStart) wStart = ds;
        wEnd = ds;
        const v = byDate[ds];
        const dTitle = `${dows[i]}, ${day} ${_MONF[m].slice(0, 3)} ${y}`;
        let cls = "cal-cell", inner = `<span class="cal-d">${day}</span>`, attrs = "";
        if (v != null) {
          cls += v > 0 ? " pos" : v < 0 ? " neg" : " flat";
          inner += `<span class="cal-pl">${moneyShort(v)}</span>`;
          attrs = ` data-tip-title="${dTitle}" data-tip-val="${signed(v)}" data-tip-cls="${v >= 0 ? "pos" : "neg"}" data-range-start="${ds}" data-range-end="${ds}" data-range-title="${dTitle}"`;
          wtot += v; wdays++; mtot += v; mdays++;
        }
        row += `<div class="${cls}"${attrs}>${inner}</div>`;
      }
      const wTitle = `Week ${week} · ${_MONF[m].slice(0, 3)} ${y}`;
      const wAttrs = wdays
        ? ` data-tip-title="${wTitle}" data-tip-val="${signed(wtot)}" data-tip-cls="${wtot >= 0 ? "pos" : "neg"}" data-tip-sub="${wdays} trading day${wdays === 1 ? "" : "s"}" data-range-start="${wStart}" data-range-end="${wEnd}" data-range-title="${wTitle}"`
        : "";
      row += `<div class="cal-week ${wdays ? (wtot >= 0 ? "pos" : "neg") : ""}"${wAttrs}>` +
        `<div class="cw-l">Week ${week}</div>` +
        `<div class="cw-v">${wdays ? moneyShort(wtot) : "₹0"}</div>` +
        `<div class="cw-d">${wdays} day${wdays === 1 ? "" : "s"}</div></div>`;
      html += row; week++;
    }
    $("homeCal").innerHTML = html;
    $("homeCalSum").innerHTML = mdays
      ? `Month: <b class="${mtot >= 0 ? "pos" : "neg"}">${signed(mtot)}</b> · ${mdays} trading day${mdays > 1 ? "s" : ""}`
      : `<span class="modal-sub">No trades this month</span>`;
  }
  // Trade details modal for clicking a Calendar day/week or Heatmap cell
  function closeDayTradesModal() {
    if ($("dayTradesModal")) $("dayTradesModal").classList.add("hidden");
  }
  let _dtBound = false;
  function bindDayTradesModal() {
    if (_dtBound) return; _dtBound = true;
    if ($("dtModalClose")) $("dtModalClose").addEventListener("click", closeDayTradesModal);
    if ($("dayTradesModal")) $("dayTradesModal").addEventListener("click", (e) => {
      if (e.target === $("dayTradesModal")) closeDayTradesModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && $("dayTradesModal") && !$("dayTradesModal").classList.contains("hidden")) closeDayTradesModal();
    });
    if ($("viewHome")) $("viewHome").addEventListener("click", (e) => {
      const card = e.target.closest("[data-range-start]");
      if (!card) return;
      hideTip();
      openDayTradesModal(card.dataset.rangeStart, card.dataset.rangeEnd, card.dataset.rangeTitle || card.dataset.rangeStart);
    });
  }
  bindDayTradesModal();
  async function openDayTradesModal(start, end, title) {
    const modal = $("dayTradesModal");
    if (!modal) return;
    const isWeek = start && end && start !== end;
    $("dtModalTitle").textContent = title || start;
    $("dtModalSub").textContent = isWeek ? `${start} → ${end}` : start;
    $("dtModalStats").innerHTML = "";
    $("dtModalBody").innerHTML = `<p class="modal-sub" style="padding:12px 0">Loading trades…</p>`;
    modal.classList.remove("hidden");
    try {
      const d = await (await fetch(`/api/journal/trades?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)).json();
      const rows = d.trades || [];
      const tot = d.total_pnl || 0, wins = d.wins || 0, losses = d.losses || 0;
      const wr = (wins + losses) ? (wins / (wins + losses) * 100).toFixed(1) + "%" : "—";
      const pnls = rows.map((t) => +t.pnl || 0);
      const best = pnls.length ? Math.max(...pnls) : 0;
      const worst = pnls.length ? Math.min(...pnls) : 0;
      const stat = (k, v, cls) => `<div class="dt-stat"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`;
      $("dtModalStats").innerHTML =
        stat("Net / Gross P&L", signed(tot), tot >= 0 ? "pos" : "neg") +
        stat("Total Trades", `${rows.length} (${wins}W · ${losses}L)`, "") +
        stat("Win Rate", wr, (wins + losses) && wins >= losses ? "pos" : "neg") +
        stat("Best / Worst", pnls.length ? `${signed(best)} / ${signed(worst)}` : "—", "");
      if (!rows.length) {
        $("dtModalBody").innerHTML = `<p class="empty">No trades recorded for this period.</p>`;
        return;
      }
      $("dtModalBody").innerHTML =
        `<table class="data"><thead><tr>` +
        (isWeek ? `<th style="text-align:left">Date</th>` : "") +
        `<th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Side</th><th>Qty</th><th>Exit</th><th>P&amp;L</th><th style="text-align:left">Setup</th><th style="text-align:left">Note</th></tr></thead><tbody>` +
        rows.map((t) => {
          _tradeCache.set(String(t.id), t);
          const pl = +t.pnl || 0, cls = pl > 0 ? "pos" : pl < 0 ? "neg" : "";
          const sc = (t.side === "LONG" || t.side === "BUY") ? "pos" : "neg";
          const tag = t.tag || {};
          const plan = tag.on_plan === 1 ? " ✓" : tag.on_plan === 0 ? " ✗" : "";
          return `<tr class="tr-click" data-tid="${esc(t.id)}">` +
            (isWeek ? `<td style="text-align:left">${esc(t.date || "")}</td>` : "") +
            `<td style="text-align:left">${esc(t.time || "—")}</td>` +
            `<td style="text-align:left"><b>${esc(t.symbol || "")}</b></td>` +
            `<td class="${sc}">${esc(t.side || "")}</td>` +
            `<td>${fmt(t.qty)}</td>` +
            `<td>${fmt(t.price, 2)}</td>` +
            `<td class="${cls}"><b>${t.pnl == null ? "—" : signed(pl)}</b></td>` +
            `<td style="text-align:left">${esc((tag.setup || "—") + plan)}</td>` +
            `<td style="text-align:left;color:var(--muted)">${esc(tag.note || "")}</td>` +
            `</tr>`;
        }).join("") + `</tbody></table>`;
    } catch (err) {
      $("dtModalBody").innerHTML = `<p class="jnote err">Could not load trades: ${esc(err.message)}</p>`;
    }
  }
  function renderHomeTrades(trades) {
    const rows = (trades || []).filter((t) => t.pnl != null && t.date !== "unknown").slice(0, 7);
    if (!rows.length) { $("homeTrades").innerHTML = `<p class="modal-sub">No closed trades in this period yet.</p>`; return; }
    $("homeTrades").innerHTML =
      `<table class="dash-tt"><thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th class="r">Qty</th><th class="r">P&L</th></tr></thead><tbody>` +
      rows.map((t) => {
        _tradeCache.set(String(t.id), t);
        const pl = +t.pnl || 0, sc = (t.side === "LONG" || t.side === "BUY") ? "pos" : "neg";
        return `<tr class="tr-click" data-tid="${esc(t.id)}"><td>${esc(t.date || "")}</td><td>${esc(t.symbol || "")}</td><td class="${sc}">${esc(t.side || "")}</td>` +
          `<td class="r">${fmt(t.qty)}</td><td class="r ${pl >= 0 ? "pos" : "neg"}"><b>${signed(pl)}</b></td></tr>`;
      }).join("") + `</tbody></table>`;
  }

  // ---- Trade detail popup with Entry/Exit chart ----
  const _tradeCache = new Map();
  let _tmChart = null, _tmTv = null, _tmRo = null, _tmState = null;
  function _deriveEntry(t) {
    if (t.entry_price != null && !isNaN(t.entry_price)) return +t.entry_price;
    const q = +t.qty || 0, ex = +t.price || 0, pl = +t.pnl;
    if (!q || isNaN(pl)) return ex;
    const isLong = t.side === "LONG" || t.side === "BUY";
    return Math.max(0, round2(isLong ? ex - pl / q : ex + pl / q));
  }
  function _clearTradeChart() {
    if (_tmRo) { try { _tmRo.disconnect(); } catch (e) { } _tmRo = null; }
    if (_tmTv) { try { _tmTv.remove(); } catch (e) { } _tmTv = null; }
    if (_tmChart) { try { _tmChart.destroy(); } catch (e) { } _tmChart = null; }
  }
  function closeTradeModal() {
    if ($("tradeModal")) $("tradeModal").classList.add("hidden");
    _clearTradeChart();
    _tmState = null;
  }
  (function bindTradeModal() {
    if ($("tmClose")) $("tmClose").addEventListener("click", closeTradeModal);
    if ($("tradeModal")) $("tradeModal").addEventListener("click", (e) => {
      if (e.target === $("tradeModal")) closeTradeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && $("tradeModal") && !$("tradeModal").classList.contains("hidden")) closeTradeModal();
    });
    document.addEventListener("click", (e) => {
      if (e.target.closest("select, input, button, a")) return;
      const tr = e.target.closest("tr.tr-click[data-tid]");
      if (!tr) return;
      const t = _tradeCache.get(tr.dataset.tid);
      if (t) openTradeModal(t);
    });
  })();
  function _closestIdx(labels, targetTime) {
    if (!targetTime || !labels.length) return -1;
    const toMin = (s) => { const m = String(s).match(/(\d{1,2}):(\d{2})/); return m ? (+m[1]) * 60 + (+m[2]) : null; };
    const tm = toMin(targetTime);
    if (tm == null) return -1;
    let best = 0, bestD = Infinity;
    labels.forEach((l, i) => { const lm = toMin(l); if (lm != null && Math.abs(lm - tm) < bestD) { bestD = Math.abs(lm - tm); best = i; } });
    return best;
  }
  const _istFmt = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
  function _setOhlcBar(label, bar) {
    const el = $("tmOhlcBar");
    if (!el) return;
    if (!bar) { el.innerHTML = esc(label || ""); return; }
    const chg = bar.close - bar.open;
    const pct = bar.open ? (chg / bar.open) * 100 : 0;
    const cls = chg >= 0 ? "pos" : "neg";
    const tStr = typeof bar.time === "number" ? _istFmt.format(new Date(bar.time * 1000)) : (bar.t || "");
    el.innerHTML =
      `<b>${esc(label)}</b>` +
      (tStr ? `<span>${esc(tStr)} IST</span>` : "") +
      `<span>O <b class="${cls}">${fmt(bar.open, 2)}</b></span>` +
      `<span>H <b class="${cls}">${fmt(bar.high, 2)}</b></span>` +
      `<span>L <b class="${cls}">${fmt(bar.low, 2)}</b></span>` +
      `<span>C <b class="${cls}">${fmt(bar.close, 2)}</b></span>` +
      `<span class="${cls}">${chg >= 0 ? "+" : ""}${fmt(chg, 2)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)</span>`;
  }
  function _drawTrajectoryFallback(t, ep, ex, candles) {
    const tvEl = $("tmTvChart"), cvEl = $("tradeDetailChart");
    if (tvEl) tvEl.classList.add("hidden");
    if (!cvEl || typeof Chart === "undefined") return;
    cvEl.classList.remove("hidden");
    const c = homeColors(), win = (+t.pnl || 0) >= 0, lineCol = win ? c.put : c.call;
    let labels, data, eIdx, xIdx;
    if (candles && candles.length >= 2) {
      labels = candles.map((p) => p.t);
      data = candles.map((p) => p.c ?? p.p);
      eIdx = t.entry_time ? _closestIdx(labels, t.entry_time) : 0;
      xIdx = t.time ? _closestIdx(labels, t.time) : labels.length - 1;
      if (eIdx === xIdx && labels.length > 1) eIdx = Math.max(0, xIdx - 1);
      if (eIdx >= 0) data[eIdx] = ep;
      if (xIdx >= 0) data[xIdx] = ex;
    } else {
      const eLbl = t.entry_time ? t.entry_time.slice(0, 5) : "Entry";
      const xLbl = t.time ? t.time.slice(0, 5) : "Exit";
      labels = [eLbl, "Mid", xLbl];
      data = [ep, round2((ep + ex) / 2), ex];
      eIdx = 0; xIdx = 2;
    }
    const ePts = data.map((_, i) => (i === eIdx ? ep : null));
    const xPts = data.map((_, i) => (i === xIdx ? ex : null));
    const markerPlugin = {
      id: "entryExitTags",
      afterDatasetsDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;
        const drawTag = (dsIndex, idx, text, bg) => {
          const pt = chart.getDatasetMeta(dsIndex)?.data?.[idx];
          if (!pt) return;
          ctx.save();
          ctx.font = "700 10.5px system-ui, sans-serif";
          const padX = 6, h = 18, w = ctx.measureText(text).width + padX * 2;
          const x = Math.max(chartArea.left + 2, Math.min(chartArea.right - w - 2, pt.x - w / 2));
          let y = pt.y - h - 8;
          if (y < chartArea.top + 2) y = pt.y + 8;
          ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(x, y, w, h, 5); ctx.fill();
          ctx.fillStyle = "#fff"; ctx.textBaseline = "middle"; ctx.fillText(text, x + padX, y + h / 2);
          ctx.restore();
        };
        drawTag(2, eIdx, "ENTRY ₹" + fmt(ep, 2), "#3b82f6");
        drawTag(3, xIdx, "EXIT ₹" + fmt(ex, 2), lineCol);
      },
    };
    _tmChart = new Chart(cvEl, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Price", data, borderColor: lineCol, backgroundColor: lineCol + "22", borderWidth: 2.5, tension: .28, pointRadius: 0, fill: true },
          { label: "Entry level", data: labels.map(() => ep), borderColor: "#3b82f6", borderWidth: 1.2, borderDash: [5, 4], pointRadius: 0, fill: false },
          { label: "Entry", data: ePts, borderColor: "#fff", backgroundColor: "#3b82f6", borderWidth: 2, pointRadius: 6, showLine: false },
          { label: "Exit", data: xPts, borderColor: "#fff", backgroundColor: lineCol, borderWidth: 2, pointRadius: 6, showLine: false },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        layout: { padding: { top: 20, right: 10, left: 6, bottom: 4 } },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { filter: (i) => i.datasetIndex !== 1, callbacks: { label: (ctx) => `${ctx.dataset.label}: ₹${fmt(ctx.parsed.y, 2)}` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: c.tick, maxTicksLimit: 7, font: { size: 10 } } },
          y: { grid: { color: c.grid }, ticks: { color: c.tick, font: { size: 10 }, callback: (v) => "₹" + fmt(v, 2) } },
        },
      },
      plugins: [markerPlugin],
    });
    _setOhlcBar(`${t.symbol || "Option"} · Entry ₹${fmt(ep, 2)} → Exit ₹${fmt(ex, 2)}`, null);
  }
  function _drawTvCandles(opts) {
    const tvEl = $("tmTvChart"), cvEl = $("tradeDetailChart");
    if (!tvEl || typeof LightweightCharts === "undefined") {
      return _drawTrajectoryFallback(_tmState.t, _tmState.ep, _tmState.ex, opts.rawBars || []);
    }
    if (cvEl) cvEl.classList.add("hidden");
    tvEl.classList.remove("hidden");
    const cs = getComputedStyle(document.documentElement);
    const bg = (cs.getPropertyValue("--panel-2") || "#131722").trim();
    const txt = (cs.getPropertyValue("--text") || "#d1d4dc").trim();
    const muted = (cs.getPropertyValue("--muted") || "#787b86").trim();
    const grid = (cs.getPropertyValue("--line") || "rgba(128,128,128,0.14)").trim();
    const upCol = (cs.getPropertyValue("--put") || "#089981").trim();
    const dnCol = (cs.getPropertyValue("--call") || "#f23645").trim();
    // Deduplicate & sort strictly ascending by Unix timestamp
    const seen = new Set(), bars = [];
    for (const p of (opts.rawBars || [])) {
      const time = Number(p.ts);
      if (!time || seen.has(time) || p.o == null || p.c == null) continue;
      seen.add(time);
      bars.push({ time, open: +p.o, high: +p.h, low: +p.l, close: +p.c, t: p.t });
    }
    bars.sort((a, b) => a.time - b.time);
    if (bars.length < 2) {
      return _drawTrajectoryFallback(_tmState.t, _tmState.ep, _tmState.ex, opts.rawBars || []);
    }
    _tmTv = LightweightCharts.createChart(tvEl, {
      width: tvEl.clientWidth || 760,
      height: tvEl.clientHeight || 360,
      layout: { background: { color: bg }, textColor: muted, fontFamily: "system-ui, -apple-system, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: grid, style: 1 }, horzLines: { color: grid, style: 1 } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: grid, scaleMargins: { top: 0.14, bottom: 0.14 } },
      timeScale: {
        borderColor: grid, timeVisible: true, secondsVisible: false, rightOffset: 4,
        tickMarkFormatter: (time) => _istFmt.format(new Date(Number(time) * 1000)),
      },
      localization: { timeFormatter: (time) => _istFmt.format(new Date(Number(time) * 1000)) },
    });
    const series = _tmTv.addCandlestickSeries({
      upColor: upCol, downColor: dnCol, borderUpColor: upCol, borderDownColor: dnCol,
      wickUpColor: upCol, wickDownColor: dnCol,
    });
    series.setData(bars);

    const { t, ep, ex } = _tmState;
    const times = bars.map((b) => b.t);
    let eIdx = t.entry_time ? _closestIdx(times, t.entry_time) : 0;
    let xIdx = t.time ? _closestIdx(times, t.time) : bars.length - 1;
    if (eIdx < 0) eIdx = 0;
    if (xIdx < 0) xIdx = bars.length - 1;
    const isLong = t.side === "LONG" || t.side === "BUY";
    const win = (+t.pnl || 0) >= 0;
    const markers = [];
    if (eIdx === xIdx) {
      markers.push({ time: bars[eIdx].time, position: "belowBar", color: "#3b82f6", shape: "arrowUp", text: `ENTRY ₹${fmt(ep, 2)} → EXIT ₹${fmt(ex, 2)}` });
    } else {
      const m1 = { time: bars[eIdx].time, position: isLong ? "belowBar" : "aboveBar", color: "#3b82f6", shape: isLong ? "arrowUp" : "arrowDown", text: opts.priceIsOption ? `ENTRY ₹${fmt(ep, 2)}` : `ENTRY (${t.entry_time ? t.entry_time.slice(0, 5) : bars[eIdx].t})` };
      const m2 = { time: bars[xIdx].time, position: isLong ? "aboveBar" : "belowBar", color: win ? upCol : dnCol, shape: isLong ? "arrowDown" : "arrowUp", text: opts.priceIsOption ? `EXIT ₹${fmt(ex, 2)}` : `EXIT (${t.time ? t.time.slice(0, 5) : bars[xIdx].t})` };
      markers.push(...[m1, m2].sort((a, b) => a.time - b.time));
    }
    series.setMarkers(markers);

    if (opts.priceIsOption) {
      series.createPriceLine({ price: ep, color: "#3b82f6", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Entry" });
      series.createPriceLine({ price: ex, color: win ? upCol : dnCol, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Exit" });
    } else if (_tmState.showPivots && opts.pivots) {
      const pv = opts.pivots;
      const pvSpec = [
        ["r3", "R3", "#2563eb"], ["r2", "R2", "#2563eb"], ["r1", "R1", "#3b82f6"],
        ["p", "P", "#60a5fa"], ["s1", "S1", "#3b82f6"], ["s2", "S2", "#2563eb"],
      ];
      pvSpec.forEach(([k, lbl, col]) => {
        if (pv[k] != null) series.createPriceLine({ price: +pv[k], color: col, lineWidth: k === "p" ? 2 : 1, lineStyle: 2, axisLabelVisible: true, title: lbl });
      });
    }
    _tmTv.timeScale().fitContent();
    const last = bars[bars.length - 1];
    _setOhlcBar(opts.label, last);
    _tmTv.subscribeCrosshairMove((param) => {
      const d = param && param.seriesData ? param.seriesData.get(series) : null;
      _setOhlcBar(opts.label, d || last);
    });
    if (typeof ResizeObserver !== "undefined") {
      _tmRo = new ResizeObserver(() => {
        if (_tmTv && tvEl.clientWidth > 0) _tmTv.applyOptions({ width: tvEl.clientWidth, height: tvEl.clientHeight });
      });
      _tmRo.observe(tvEl);
    }
  }
  function _renderTmView() {
    if (!_tmState) return;
    _clearTradeChart();
    const { t, ep, ex, candles, underlying, mode } = _tmState;
    const seg = $("tmModeSeg"), pivBtn = $("tmPivotBtn");
    const hasOpt = candles && candles.length >= 2;
    const hasUnd = underlying && (underlying.points || []).length >= 2;
    const buttons = [];
    const isOptSym = /\b(CALL|PUT)\b|(?:CE|PE|FUT)$|\s/i.test(t.symbol || "");
    if (hasOpt) buttons.push(["option", isOptSym ? "Option 5m OHLC" : "Stock 5m OHLC"]);
    if (hasUnd) buttons.push(["underlying", `${underlying.symbol} 5m OHLC`]);
    if (!hasOpt) buttons.push(["trajectory", "Option Entry → Exit"]);
    if (seg) {
      seg.innerHTML = buttons.map(([id, lbl]) => `<button type="button" class="${mode === id ? "active" : ""}" data-tm-mode="${id}">${esc(lbl)}</button>`).join("");
      seg.classList.toggle("hidden", buttons.length < 2);
      seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        _tmState.mode = b.dataset.tmMode;
        _renderTmView();
      }));
    }
    if (pivBtn) {
      const showPivBtn = mode === "underlying" && !!(underlying && underlying.pivots);
      pivBtn.classList.toggle("hidden", !showPivBtn);
      pivBtn.classList.toggle("active", !!_tmState.showPivots);
    }
    if (mode === "option" && hasOpt) {
      _drawTvCandles({ rawBars: candles, priceIsOption: true, label: `${t.symbol} · 5m` });
    } else if (mode === "underlying" && hasUnd) {
      _drawTvCandles({ rawBars: underlying.points, pivots: underlying.pivots, priceIsOption: false, label: `${underlying.symbol} · 5m` });
    } else {
      _drawTrajectoryFallback(t, ep, ex, []);
    }
  }
  if ($("tmPivotBtn")) $("tmPivotBtn").addEventListener("click", () => {
    if (!_tmState) return;
    _tmState.showPivots = !_tmState.showPivots;
    _renderTmView();
  });
  async function openTradeModal(t) {
    const modal = $("tradeModal");
    if (!modal) return;
    const ep = _deriveEntry(t), ex = +t.price || 0, q = +t.qty || 0, pl = +t.pnl || 0;
    const isLong = t.side === "LONG" || t.side === "BUY";
    const pts = isLong ? (ex - ep) : (ep - ex);
    const pct = ep > 0 ? (pts / ep * 100) : 0;
    $("tmTitle").textContent = t.symbol || "Trade";
    $("tmSide").textContent = t.side || "";
    $("tmSide").className = "sig " + (isLong ? "bull" : "bear");
    $("tmPnl").textContent = t.pnl == null ? "—" : signed(pl);
    $("tmPnl").className = "tm-pnl " + (pl >= 0 ? "pos" : "neg");
    const eWin = (t.entry_time ? t.entry_time.slice(0, 8) + " → " : "") + (t.time ? t.time.slice(0, 8) : "");
    $("tmSub").textContent = [t.date, eWin, (t.source || "").toUpperCase()].filter(Boolean).join(" · ");
    const tag = t.tag || {};
    const planTxt = tag.on_plan === 1 ? "✓ On plan" : tag.on_plan === 0 ? "✗ Off plan" : "—";
    const cell = (k, v, cls) => `<div class="tm-cell"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`;
    $("tmDetails").innerHTML =
      cell("Entry Price", "₹" + fmt(ep, 2)) +
      cell("Exit Price", "₹" + fmt(ex, 2)) +
      cell("Move", `${pts >= 0 ? "+" : ""}${fmt(pts, 2)} pts (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)`, pts >= 0 ? "pos" : "neg") +
      cell("Quantity", fmt(q)) +
      cell("Buy Value", inr(round2((isLong ? ep : ex) * q))) +
      cell("Sell Value", inr(round2((isLong ? ex : ep) * q))) +
      cell("Setup", esc(tag.setup || "—")) +
      cell("Plan", planTxt, tag.on_plan === 1 ? "pos" : tag.on_plan === 0 ? "neg" : "") +
      cell("Note", esc(tag.note || "—"));
    $("tmChartNote").classList.add("hidden");
    if ($("tmModeSeg")) $("tmModeSeg").classList.add("hidden");
    if ($("tmPivotBtn")) $("tmPivotBtn").classList.add("hidden");
    _tmState = { t, ep, ex, candles: [], underlying: null, mode: "trajectory", showPivots: true };
    modal.classList.remove("hidden");
    _renderTmView();
    try {
      const qs = `?symbol=${encodeURIComponent(t.symbol || "")}&date=${encodeURIComponent(t.date || "")}` +
        `&entry_price=${ep}&exit_price=${ex}&entry_time=${encodeURIComponent(t.entry_time || "")}&exit_time=${encodeURIComponent(t.time || "")}`;
      const d = await (await fetch("/api/journal/trade-chart" + qs)).json();
      if (!_tmState || _tmState.t !== t) return;
      _tmState.candles = d.candles || [];
      _tmState.underlying = d.underlying || null;
      _tmState.mode = _tmState.candles.length >= 2 ? "option" : (_tmState.underlying?.points?.length >= 2 ? "underlying" : "trajectory");
      _renderTmView();
      if (d.note) { $("tmChartNote").textContent = d.note; $("tmChartNote").classList.remove("hidden"); }
    } catch (e) { /* keep trajectory fallback */ }
  }

  // ---------------------------------------------------------- screener
  let scrSort = "volume";
  let scrIndex = "nifty50";
  let scrFilters = new Set();      // active filter ids
  let scrMeta = null;              // { groups, indexes, default_index }
  function scrStatus(msg, err) {
    const s = $("scrStatus");
    if (!s) return;
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jnote" + (err ? " err" : "");
    s.innerHTML = msg;
    s.classList.remove("hidden");
  }
  async function loadScreener(opts) {
    const silent = opts && opts.auto;        // auto-refresh: no spinner / button flicker
    if (!scrMeta) await scrInitControls();   // build chips/index picker on first load
    if (silent && scrBusy) return;           // skip a tick if a load is still in flight
    scrBusy = true;
    if (!silent) showLoading(true);
    const btn = $("scrRefresh"), old = btn ? btn.textContent : "";
    if (!silent && btn) { btn.disabled = true; btn.textContent = "Scanning…"; }
    try {
      const needHist = [...scrFilters].some((f) => SCR_HIST.has(f)) ? 1 : 0;
      const qs = "?sort=" + encodeURIComponent(scrSort) +
        "&index=" + encodeURIComponent(scrIndex) +
        "&hist=" + needHist;
      const r = await fetch("/api/screener" + qs);
      if (r.status === 401) { scrStatus("Log in to Fyers on the Dashboard tab to load live stocks.", true); return; }
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "screener failed");
      renderScreener(d);
    } catch (e) {
      if (!silent) scrStatus("Could not load screener: " + e.message, true);
    } finally {
      scrBusy = false;
      if (!silent) showLoading(false);
      if (!silent && btn) { btn.disabled = false; btn.textContent = old; }
    }
  }
  let scrBusy = false;
  let scrRows = [];                       // rows currently shown (after client filter)
  let scrAllRows = [];                    // full fetched set (filtered locally)
  let scrLive = null, scrScanned = 0, scrCovered = 0, scrStale = 0, scrWarming = 0;
  let scrColSort = { key: null, dir: -1 }; // -1 = high→low, 1 = low→high
  // --- client-side filter predicates (mirror backend/screener.py) ---
  const SCR_NEAR = 0.3, SCR_52W = 2.5, SCR_GAP = 0.5;
  const SCR_PRED = {
    bullish: (m) => m.chg_pct > 0.3,
    bearish: (m) => m.chg_pct < -0.3,
    near_day_high: (m) => m.from_high_pct != null && m.from_high_pct <= SCR_NEAR,
    near_day_low: (m) => m.from_low_pct != null && m.from_low_pct <= SCR_NEAR,
    fall1: (m) => m.from_high_pct != null && m.from_high_pct >= 1,
    fall2_5: (m) => m.from_high_pct != null && m.from_high_pct >= 2.5,
    fall5: (m) => m.from_high_pct != null && m.from_high_pct >= 5,
    rise1: (m) => m.from_low_pct != null && m.from_low_pct >= 1,
    rise2_5: (m) => m.from_low_pct != null && m.from_low_pct >= 2.5,
    rise5: (m) => m.from_low_pct != null && m.from_low_pct >= 5,
    gap_up: (m) => m.gap_pct >= SCR_GAP,
    gap_down: (m) => m.gap_pct <= -SCR_GAP,
    high_volume: (m) => !!m.high_volume,
    near_52w_high: (m) => m.high_52w > 0 && m.ltp >= m.high_52w * (1 - SCR_52W / 100),
    near_52w_low: (m) => m.low_52w > 0 && m.ltp <= m.low_52w * (1 + SCR_52W / 100),
    new_week_high: (m) => m.week_high > 0 && m.ltp >= m.week_high,
    new_month_high: (m) => m.month_high > 0 && m.ltp >= m.month_high,
    new_week_low: (m) => m.week_low > 0 && m.ltp <= m.week_low,
    new_month_low: (m) => m.month_low > 0 && m.ltp <= m.month_low,
    unusual_volume: (m) => m.avg_vol20 > 0 && m.volume >= m.avg_vol20,
    r1: (m) => m.r1 > 0 && m.ltp > m.r1,
    r2: (m) => m.r2 > 0 && m.ltp > m.r2,
    r3: (m) => m.r3 > 0 && m.ltp > m.r3,
  };
  let SCR_LABELS = {};               // id -> label (from meta)
  let SCR_HIST = new Set();          // ids needing daily history (from meta)

  function renderScreener(d) {
    scrAllRows = (d.rows || []).slice();   // full fetched set (filter locally)
    scrLive = d.live;
    scrScanned = d.scanned;
    scrCovered = d.covered != null ? d.covered : (d.rows || []).length;
    scrStale = d.stale_secs || 0;
    scrWarming = d.warming || 0;
    scrMarketOpen = d.market_open !== false;
    scrBudgetLeft = d.budget_left != null ? d.budget_left : 1;
    applyFilters();                        // filter + draw without re-fetching
    if (typeof scrResetCountdown === "function") scrResetCountdown();
    // While the scanner is still filling the universe, poll the (no-Fyers-call)
    // snapshot every few seconds so the table fills fast — but ONLY when the
    // market is open and budget remains, else there's nothing new to load.
    if (scrWarmPoll) { clearTimeout(scrWarmPoll); scrWarmPoll = null; }
    const autoOn = !$("scrAuto") || $("scrAuto").checked;
    const canScan = scrLive !== false && scrMarketOpen && scrBudgetLeft > 0;
    const stillWarming = scrCovered < scrScanned || scrWarming > 0;
    const onScreener = $("viewScreener") && !$("viewScreener").classList.contains("hidden");
    if (autoOn && canScan && stillWarming && onScreener && !document.hidden) {
      scrWarmPoll = setTimeout(() => loadScreener({ auto: true }), 3000);
    }
  }
  let scrWarmPoll = null, scrMarketOpen = true, scrBudgetLeft = 1;
  function applyFilters() {
    const active = [...scrFilters].filter((f) => SCR_PRED[f]);
    let rows;
    if (active.length) {
      rows = [];
      for (const m of scrAllRows) {
        const matched = active.filter((f) => { try { return SCR_PRED[f](m); } catch (e) { return false; } })
          .map((f) => SCR_LABELS[f] || f);
        if (matched.length) { m.matched = matched; rows.push(m); }
      }
    } else {
      rows = scrAllRows.map((m) => { m.matched = []; return m; });
    }
    scrRows = rows;
    syncQuickSignalCounts();
    drawScreenerRows();
    // status line
    const mktOpen = typeof marketStatus !== "function" || marketStatus().open;
    const auto = mktOpen ? " · 🔄 15s" : " · ⏸ paused (market closed)";
    const nF = active.length;
    const head = scrLive === false ? "⚠️ Demo data" : "Live";
    // coverage / state note
    let cov = "";
    if (scrLive !== false && scrBudgetLeft <= 0) {
      cov = ` · 🚫 daily API limit reached — resumes tomorrow (showing last data)`;
    } else if (scrLive !== false && !scrMarketOpen) {
      cov = ` · ⏸ market closed — live scan paused`;
    } else if (scrLive !== false && scrCovered < scrScanned) {
      cov = ` · ⏳ loading ${scrCovered}/${scrScanned}`;
    } else if (scrLive !== false && scrStale > 25) {
      cov = ` · updated ${scrStale}s ago`;
    }
    let msg = `${head} · ${scrScanned} stocks${cov}`;
    if (nF) msg = `${head} · <b>${rows.length} match ${nF} filter${nF > 1 ? "s" : ""}</b> of ${scrScanned}${cov}`;
    const pendingHist = nF && [...scrFilters].some((f) => SCR_HIST.has(f)) && scrWarming;
    if (pendingHist) msg += ` · building daily levels… ${scrWarming} left`;
    scrStatus(msg + auto, false);
  }
  function drawScreenerRows() {
    let rows = scrRows.slice();
    const k = scrColSort.key;
    if (k) {
      rows.sort((a, b) => {
        let va = a[k], vb = b[k];
        if (k === "symbol") return String(va).localeCompare(String(vb)) * scrColSort.dir;
        return ((Number(va) || 0) - (Number(vb) || 0)) * scrColSort.dir;
      });
    }
    // header arrow indicators
    document.querySelectorAll("#screenerTable th.sortable").forEach((th) => {
      const base = th.dataset.label || (th.dataset.label = th.textContent.replace(/[▲▼]\s*$/, "").trim());
      th.textContent = base + (th.dataset.key === k ? (scrColSort.dir === -1 ? " ▼" : " ▲") : "");
      th.classList.toggle("active", th.dataset.key === k);
    });
    const tb = $("screenerTable").querySelector("tbody");
    tb.innerHTML = rows.map((r, i) => {
      const cc = r.chg_pct >= 0 ? "pos" : "neg";
      const gc = r.gap_pct >= 0 ? "pos" : "neg";
      const disp = r.display || r.symbol;
      return `<tr class="scr-row" data-sym="${esc(r.symbol)}" data-disp="${esc(disp)}" title="${esc(r.why || "")}">
        <td>${i + 1}</td>
        <td style="text-align:left"><b>${esc(disp)}</b></td>
        <td>${fmt(r.ltp, 2)}</td>
        <td class="${cc}">${r.chg_pct >= 0 ? "+" : ""}${r.chg_pct}%</td>
        <td class="${gc}">${r.gap_pct >= 0 ? "+" : ""}${r.gap_pct}%</td>
        <td>${r.range_pct}%</td>
        <td>${fmt(r.volume)}</td>
        <td style="text-align:left">${matchCell(r)}</td>
      </tr>`;
    }).join("");
  }
  function matchCell(r) {
    if (r.matched && r.matched.length) {
      return r.matched.map((m) => `<span class="sig ${tagCls(m)}">${esc(m)}</span>`).join(" ");
    }
    return sigBadge(r.signal);
  }
  function tagCls(label) {
    if (/bull|rise|r[123] break|new (week|month)|52w high|gap up|near day high/i.test(label)) return "bull";
    if (/bear|fall|52w low|new (week|month) low|gap down|near day low/i.test(label)) return "bear";
    return "flat";
  }
  function sigBadge(sig) {
    const s = sig || "Neutral";
    const cls = /bull/i.test(s) ? "bull" : /bear/i.test(s) ? "bear" : "flat";
    return `<span class="sig ${cls}">${esc(s)}</span>`;
  }

  // ---- build the filter chips + index picker from /api/screener/meta ----
  async function scrInitControls() {
    if (scrMeta) return;
    try {
      scrMeta = await (await fetch("/api/screener/meta")).json();
    } catch (e) { return; }
    scrIndex = scrMeta.default_index || scrIndex;
    SCR_LABELS = { bullish: "Bullish", bearish: "Bearish" };
    // label + history-flag maps for client-side filtering
    (scrMeta.groups || []).forEach((g) => g.filters.forEach((f) => {
      SCR_LABELS[f.id] = f.label;
      if (f.hist) SCR_HIST.add(f.id);
    }));
    // index picker
    const sel = $("scrIndex");
    if (sel) {
      sel.innerHTML = (scrMeta.indexes || []).map((ix) =>
        `<option value="${esc(ix.id)}">${esc(ix.label)} (${ix.count})</option>`).join("");
      sel.value = scrIndex;
      sel.addEventListener("change", () => { scrIndex = sel.value; loadScreener(); });
    }
    // filter chips
    const bar = $("scrFilterBar");
    if (bar) {
      bar.innerHTML = (scrMeta.groups || []).map((g) => `
        <div class="scr-chip" data-group="${esc(g.id)}">
          <button class="scr-chip-btn" type="button">
            <span>${g.icon || ""} ${esc(g.label)}</span>
            <span class="scr-chip-count" hidden>0</span>
            <span class="scr-chip-caret">▾</span>
          </button>
          <div class="scr-chip-menu" hidden>
            ${g.filters.map((f) => `
              <label class="scr-chk"><input type="checkbox" value="${esc(f.id)}"/>
                <span>${esc(f.label)}${f.hist ? ' <small class="scr-hist">•daily</small>' : ""}</span></label>`).join("")}
          </div>
        </div>`).join("") +
        `<div class="scr-quick">` +
        `<button id="scrQuickBull" class="scr-qbtn bull" type="button" title="Show all Bullish stocks (click again for all)">▲ Bullish <b id="scrBullCnt">0</b></button>` +
        `<button id="scrQuickBear" class="scr-qbtn bear" type="button" title="Show all Bearish stocks (click again for all)">▼ Bearish <b id="scrBearCnt">0</b></button>` +
        `</div>` +
        `<button id="scrClear" class="scr-clear" type="button" hidden>✕ Clear</button>`;
      bar.addEventListener("click", onFilterBarClick);
      bar.addEventListener("change", onFilterChange);
    }
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".scr-chip")) closeAllChipMenus();
    });
  }
  function closeAllChipMenus() {
    document.querySelectorAll(".scr-chip-menu").forEach((m) => (m.hidden = true));
    document.querySelectorAll(".scr-chip").forEach((c) => c.classList.remove("open"));
  }
  function toggleSignalQuick(sigId) {
    const other = sigId === "bullish" ? "bearish" : "bullish";
    const wasOnly = scrFilters.has(sigId) && !scrFilters.has(other);
    scrFilters.delete("bullish");
    scrFilters.delete("bearish");
    if (!wasOnly) scrFilters.add(sigId);
    document.querySelectorAll('#scrFilterBar input[type=checkbox][value="bullish"], #scrFilterBar input[type=checkbox][value="bearish"]').forEach((c) => {
      c.checked = scrFilters.has(c.value);
    });
    syncChipCounts();
    applyFilters();
  }
  function syncQuickSignalCounts() {
    const other = [...scrFilters].filter((f) => f !== "bullish" && f !== "bearish" && SCR_PRED[f]);
    const pool = other.length ? scrAllRows.filter((m) => other.some((f) => { try { return SCR_PRED[f](m); } catch (e) { return false; } })) : scrAllRows;
    let bull = 0, bear = 0;
    for (const m of pool) {
      if (SCR_PRED.bullish(m)) bull++;
      else if (SCR_PRED.bearish(m)) bear++;
    }
    if ($("scrBullCnt")) $("scrBullCnt").textContent = bull;
    if ($("scrBearCnt")) $("scrBearCnt").textContent = bear;
    if ($("scrQuickBull")) $("scrQuickBull").classList.toggle("active", scrFilters.has("bullish"));
    if ($("scrQuickBear")) $("scrQuickBear").classList.toggle("active", scrFilters.has("bearish"));
  }
  function onFilterBarClick(e) {
    const qbtn = e.target.closest("#scrQuickBull, #scrQuickBear");
    if (qbtn) {
      closeAllChipMenus();
      toggleSignalQuick(qbtn.id === "scrQuickBull" ? "bullish" : "bearish");
      return;
    }
    const btn = e.target.closest(".scr-chip-btn");
    if (btn) {
      const chip = btn.closest(".scr-chip");
      const menu = chip.querySelector(".scr-chip-menu");
      const wasOpen = !menu.hidden;
      closeAllChipMenus();
      menu.hidden = wasOpen;
      chip.classList.toggle("open", !wasOpen);
      return;
    }
    if (e.target.closest("#scrClear")) {
      scrFilters.clear();
      document.querySelectorAll("#scrFilterBar input[type=checkbox]").forEach((c) => (c.checked = false));
      syncChipCounts();
      applyFilters();                      // client-side only — no API call
    }
  }
  function onFilterChange(e) {
    const cb = e.target.closest("input[type=checkbox]");
    if (!cb) return;
    if (cb.checked) scrFilters.add(cb.value); else scrFilters.delete(cb.value);
    syncChipCounts();
    applyFilters();                        // filter the data we already have — no fetch
    // one-time fetch only if a history filter is on but its levels aren't loaded yet
    const needHist = [...scrFilters].some((f) => SCR_HIST.has(f));
    const haveLevels = scrAllRows.some((m) => m.high_52w != null);
    if (needHist && !haveLevels && !scrBusy) loadScreener();
  }
  function syncChipCounts() {
    document.querySelectorAll("#scrFilterBar .scr-chip").forEach((chip) => {
      const n = chip.querySelectorAll("input[type=checkbox]:checked").length;
      const badge = chip.querySelector(".scr-chip-count");
      badge.textContent = n;
      badge.hidden = n === 0;
      chip.classList.toggle("active", n > 0);
    });
    const clear = $("scrClear");
    if (clear) clear.hidden = scrFilters.size === 0;
  }

  if ($("scrRefresh")) $("scrRefresh").addEventListener("click", () => loadScreener());
  if ($("scrAuto")) $("scrAuto").addEventListener("change", () => {
    scrResetCountdown();
    if ($("scrAuto").checked) loadScreener();   // resume immediately when turned on
    else scrSetTimer("⏸ off", "paused");
  });

  // ---- auto-refresh the screener every 15s with a live countdown.
  //      Only runs when the tab is open, visible, and the market is live. ----
  const SCR_REFRESH_SEC = 15;
  let scrCountdown = SCR_REFRESH_SEC;
  function scrSetTimer(txt, cls) {
    const el = $("scrCountdown");
    if (!el) return;
    el.textContent = txt;
    el.className = "scr-countdown" + (cls ? " " + cls : "");
  }
  function scrResetCountdown() { scrCountdown = SCR_REFRESH_SEC; }
  function scrAutoOn() { const c = $("scrAuto"); return !c || c.checked; }
  setInterval(() => {
    const v = $("viewScreener");
    if (!v || v.classList.contains("hidden")) return;          // not on the Screener tab
    if (!scrAutoOn()) { scrSetTimer("⏸ off", "paused"); scrResetCountdown(); return; } // Auto toggled off
    const marketOpen = typeof marketStatus !== "function" || marketStatus().open;
    if (!marketOpen) { scrSetTimer("⏸ paused", "paused"); scrResetCountdown(); return; }
    if (document.hidden) { scrSetTimer("⏸ paused", "paused"); return; } // keep counter, resume on focus
    scrCountdown -= 1;
    if (scrCountdown <= 0) {
      scrSetTimer("⟳ …", "due");
      scrResetCountdown();
      loadScreener({ auto: true });
    } else {
      scrSetTimer("⏱ " + scrCountdown + "s");
    }
  }, 1000);

  // ---- click a column header to sort the loaded rows (high→low, toggle low→high) ----
  document.querySelectorAll("#screenerTable th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (scrColSort.key === key) scrColSort.dir *= -1;      // same column → flip direction
      else scrColSort = { key, dir: key === "symbol" ? 1 : -1 }; // new column → numbers high→low, names A→Z
      drawScreenerRows();
    }));

  // ---- per-stock analysis (trend + momentum) on row click ----
  if ($("screenerTable")) $("screenerTable").addEventListener("click", (e) => {
    const row = e.target.closest(".scr-row");
    if (!row) return;
    const sym = row.dataset.sym, disp = row.dataset.disp || sym;
    analyzeStock(sym, { t: disp, f: sym, e: sym.startsWith("BSE:") ? "BSE" : "NSE" });
  });
  function closeAnalyze() {
    $("analyzeModal").classList.add("hidden");
    if (anaChart) { anaChart.destroy(); anaChart = null; }
  }
  if ($("anaCloseBtn")) $("anaCloseBtn").addEventListener("click", closeAnalyze);
  // click outside the card (on the dim backdrop) closes the popup
  if ($("analyzeModal")) $("analyzeModal").addEventListener("click", (e) => {
    if (e.target === $("analyzeModal")) closeAnalyze();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("analyzeModal").classList.contains("hidden")) closeAnalyze();
  });

  // ---- header stock search → opens the analysis popup (ALL NSE + BSE) ----
  let SYMBOLS = [], SYMBOL_ITEMS = [];
  let searchMatches = [], searchActive = -1;
  const _SYM_ALIASES = { MOFSL: "MOTILALOFS", MOSFL: "MOTILALOFS", TATAMOTOR: "TATAMOTORS", HDFC: "HDFCBANK", ZOMATO: "ETERNAL", ETERNAL: "ZOMATO" };
  function _isFuzzyClose(a, b) {
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1 || Math.min(la, lb) < 3) return false;
    if (a === b) return true;
    if (la === lb) {
      const diff = [];
      for (let i = 0; i < la; i++) if (a[i] !== b[i]) diff.push(i);
      if (diff.length === 1) return true;
      return diff.length === 2 && diff[1] === diff[0] + 1 && a[diff[0]] === b[diff[1]] && a[diff[1]] === b[diff[0]];
    }
    const s = la < lb ? a : b, l = la < lb ? b : a;
    let i = 0, j = 0, skip = false;
    while (i < s.length && j < l.length) {
      if (s[i] === l[j]) { i++; j++; }
      else if (skip) return false;
      else { skip = true; j++; }
    }
    return true;
  }
  (async function initStockSearch() {
    const inp = $("stockSearch"), box = $("stockSearchResults");
    if (!inp || !box) return;
    const hide = () => { box.classList.add("hidden"); searchActive = -1; };
    function render() {
      if (!searchMatches.length) { hide(); return; }
      box.innerHTML = searchMatches.map((it, i) => {
        const sym = it.t || it, nm = it.n && it.n !== sym ? it.n : "", ex = it.e || "NSE";
        return `<div class="hs-item${i === searchActive ? " active" : ""}" data-idx="${i}">` +
          `<span class="hs-left"><span class="hs-sym">${esc(sym)}</span>${nm ? `<span class="hs-name">${esc(nm)}</span>` : ""}</span>` +
          `<span class="hs-exch">${esc(ex)}</span></div>`;
      }).join("");
      box.classList.remove("hidden");
    }
    function search(qRaw) {
      const q = qRaw.trim().toUpperCase();
      if (!q) { searchMatches = []; hide(); return; }
      const alias = _SYM_ALIASES[q];
      const toks = q.split(/\s+/).filter(Boolean);
      const exact = [], starts = [], hasT = [], hasN = [], fuzzy = [];
      for (const it of SYMBOL_ITEMS) {
        const t = it.t, n = (it.n || "").toUpperCase(), c = String(it.c || "");
        if ((alias && t === alias) || t === q || c === q) exact.push(it);
        else if (t.startsWith(q) || c.startsWith(q)) starts.push(it);
        else if (t.includes(q)) hasT.push(it);
        else if (n.includes(q) || (toks.length > 1 && toks.every((w) => t.includes(w) || n.includes(w)))) hasN.push(it);
        else if (q.length >= 3 && (_isFuzzyClose(q, t) || (t.length > q.length && _isFuzzyClose(q, t.slice(0, q.length))))) fuzzy.push(it);
      }
      searchMatches = exact.concat(starts, hasT, hasN, fuzzy).slice(0, 12);
      searchActive = searchMatches.length ? 0 : -1;
      render();
    }
    async function pick(itemOrStr) {
      let item = typeof itemOrStr === "object" ? itemOrStr : null;
      const raw = typeof itemOrStr === "string" ? itemOrStr.trim().toUpperCase() : "";
      if (!item && raw) {
        const resolved = SYMBOL_ITEMS.find((x) => x.t === raw) || SYMBOL_ITEMS.find((x) => x.t === (_SYM_ALIASES[raw] || ""));
        item = resolved || { t: raw, n: "", e: "NSE", f: raw };
      }
      if (!item || !item.t) return;
      inp.value = ""; searchMatches = []; hide(); inp.blur();
      analyzeStock(item.f || item.t, item);
    }
    inp.addEventListener("input", () => search(inp.value));
    inp.addEventListener("focus", () => { if (inp.value.trim()) search(inp.value); });
    inp.addEventListener("keydown", async (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); if (searchMatches.length) { searchActive = (searchActive + 1) % searchMatches.length; render(); } }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (searchMatches.length) { searchActive = (searchActive - 1 + searchMatches.length) % searchMatches.length; render(); } }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (searchMatches.length) return pick(searchMatches[searchActive] || searchMatches[0]);
        const q = inp.value.trim();
        if (!q) return;
        try {
          const r = await (await fetch("/api/symbols?q=" + encodeURIComponent(q))).json();
          if (r.items && r.items.length) return pick(r.items[0]);
        } catch (err) { }
        pick(q);
      }
      else if (e.key === "Escape") { hide(); }
    });
    box.addEventListener("mousedown", (e) => {
      const it = e.target.closest(".hs-item");
      if (it) { e.preventDefault(); pick(searchMatches[Number(it.dataset.idx)]); }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".hsearch")) hide(); });
    try {
      const d = await (await fetch("/api/symbols")).json();
      SYMBOLS = d.stocks || [];
      SYMBOL_ITEMS = d.items || SYMBOLS.map((s) => ({ t: s, n: s, e: "NSE", f: `NSE:${s}-EQ`, b: "", c: "" }));
      if (inp.value.trim()) search(inp.value);
    } catch (e) { /* keep backend Enter lookup fallback */ }
  })();

  // ---- formatting helpers for the stock detail view ----
  const _n = (v) => (v == null || v === "" || isNaN(v)) ? null : Number(v);
  const money = (v) => { v = _n(v); return v == null ? "—" : "₹" + fmt(v, 2); };
  function crore(v) {              // big ₹ amounts → Cr / Lakh Cr (Indian)
    v = _n(v); if (v == null) return "—";
    const cr = v / 1e7;
    if (cr >= 1e5) return "₹" + fmt(cr / 1e5, 2) + " L Cr";
    if (cr >= 100) return "₹" + fmt(cr, 0) + " Cr";
    return "₹" + fmt(cr, 2) + " Cr";
  }
  function volh(v) {               // share volume → Cr / L / k
    v = _n(v); if (v == null) return "—";
    if (v >= 1e7) return fmt(v / 1e7, 2) + " Cr";
    if (v >= 1e5) return fmt(v / 1e5, 2) + " L";
    if (v >= 1e3) return fmt(v / 1e3, 1) + "k";
    return fmt(v, 0);
  }
  const pctF = (v) => { v = _n(v); return v == null ? "—" : (v >= 0 ? "" : "") + fmt(v * 100, 2) + "%"; }; // fraction→%
  const pctP = (v, d) => { v = _n(v); return v == null ? "—" : fmt(v, d == null ? 1 : d) + "%"; };          // already %
  const numF = (v, d) => { v = _n(v); return v == null ? "—" : fmt(v, d == null ? 2 : d); };
  const signCls = (v) => { v = _n(v); return v == null ? "" : v >= 0 ? "pos" : "neg"; };

  let anaState = null;            // {sym, tech, fund, fundLoading, tab}

  async function analyzeStock(symIn, meta) {
    const raw = (symIn || "").toUpperCase();
    const sym = (meta && meta.t) || (raw.includes(":") ? raw.split(":")[1].replace(/-(EQ|BE|SM|ST|BZ|A|B|T|X|XT|Z|M|MT|P)$/, "") : raw);
    const reqSym = (meta && meta.f) || raw;
    anaState = { sym, tech: null, fund: null, fundLoading: true, tab: "tech" };
    $("anaTitle").textContent = meta && meta.e ? `${sym} · ${meta.e}` : sym;
    $("analyzeModal").classList.remove("hidden");
    $("anaBody").innerHTML = `<p class="modal-sub">Loading ${esc(sym)}…</p>`;

    // Fundamentals (Screener/Yahoo) and technicals (Fyers/Yahoo) are independent — fetch both in parallel.
    const techP = fetch("/api/screener/analyze?symbol=" + encodeURIComponent(reqSym))
      .then(async (r) => ({ r, d: await r.json().catch(() => ({})) }));
    fetch("/api/stock/fundamentals?symbol=" + encodeURIComponent(reqSym))
      .then((r) => (r.ok ? r.json() : { ok: false }))
      .catch(() => ({ ok: false }))
      .then((f) => { if (anaState && anaState.sym === sym) { anaState.fund = f; anaState.fundLoading = false; renderAnalysisShell(); } });

    try {
      const { r, d } = await techP;
      if (r.status === 401) anaState.tech = { ok: false, note: "Log in to Fyers on the Dashboard tab to see live technicals (fundamentals still load)." };
      else if (!r.ok || d.ok === false) anaState.tech = { ok: false, note: d.note || d.detail || "No technical data available." };
      else anaState.tech = d;
    } catch (err) {
      anaState.tech = { ok: false, note: "Could not load technicals: " + err.message };
    }
    if (anaState && anaState.sym === sym) renderAnalysisShell();
  }

  function renderAnalysisShell() {
    const s = anaState; if (!s) return;
    const t = s.tech || {}, f = s.fund || {};
    const name = (f.ok && f.name) ? f.name : s.sym;
    const sig = t.ok ? t.signal : null;
    const scls = /bull/i.test(sig || "") ? "bull" : /bear/i.test(sig || "") ? "bear" : "flat";
    const price = (t.ok && t.price != null) ? t.price : null;
    const chg = (t.ok && t.chg_pct != null) ? t.chg_pct : null;
    $("anaTitle").textContent = s.sym;

    let html = `<div class="ana-top">` +
      `<div class="ana-id"><div class="ana-name">${esc(name)}</div>` +
      `<div class="ana-ind">${esc(s.sym)} · NSE</div></div>` +
      `<div class="ana-px">` +
      (price != null ? `<div class="ana-price">${money(price)}</div>` : "") +
      (chg != null ? `<div class="ana-trend ${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${fmt(chg, 2)}%</div>` : "") +
      (sig ? `<span class="sig ${scls}">${esc(sig)}</span>` : "") +
      `</div></div>`;

    // Two columns: technicals on the left, fundamentals on the right.
    html += `<div class="ana-cols">`;
    html += `<div class="ana-col"><div class="ana-colh">📊 Technicals</div>` + techTabHtml(t, scls) + `</div>`;
    html += `<div class="ana-col"><div class="ana-colh">💰 Fundamentals</div>` +
      (s.fundLoading ? `<p class="modal-sub">Loading fundamentals…</p>` : fundTabHtml(f, s.sym)) +
      readNotesHtml(t) + `</div>`;
    html += `</div>`;

    $("anaBody").innerHTML = html;
    if (t.ok && t.chart && (t.chart.price || []).length > 1) drawAnaChart(t.chart, scls);
  }

  function techTabHtml(d, scls) {
    if (!d.ok) return `<p class="jnote">${esc(d.note || "No technical data.")}</p>`;
    const row = (k, v, cls) => `<div class="ana-row"><span>${k}</span><b class="${cls || ""}">${v}</b></div>`;
    const sect = (t) => `<div class="ana-sect">${t}</div>`;
    let html = "";
    // Mechanical setup & timing (not advice)
    const su = d.setup;
    if (su && su.bias) {
      const bcls = /bull/i.test(su.bias) ? "bull" : /bear/i.test(su.bias) ? "bear" : "flat";
      html += `<div class="ana-setup ${bcls}">` +
        `<div class="ana-setup-h">🎯 Setup &amp; timing <span class="sig ${bcls}">${esc(su.bias)}</span></div>` +
        (su.timing ? `<p class="ana-setup-t">${esc(su.timing)}</p>` : "") +
        `<div class="ana-setup-g">` +
        (su.entry_zone ? `<div><span>Entry zone</span><b>${esc(su.entry_zone)}</b></div>` : "") +
        (su.trigger ? `<div><span>Trigger</span><b>${esc(su.trigger)}</b></div>` : "") +
        (su.stop ? `<div><span>Stop / invalidation</span><b>${esc(su.stop)}</b></div>` : "") +
        `</div>` +
        `<p class="ana-setup-dis">Mechanical read of levels — <b>not buy/sell advice</b>.</p>` +
        `</div>`;
    }
    if (d.chart && (d.chart.price || []).length > 1)
      html += sect("📊 " + esc(d.chart.title || "Price")) + `<div class="ana-chart"><canvas id="anaChart"></canvas></div>`;
    if (d.intraday) {
      html += sect("📍 Intraday (5-min)") + `<div class="ana-grid">` +
        row("Price", money(d.price)) +
        row("VWAP", money(d.vwap) + (d.vwap != null ? (d.above_vwap ? " ▲" : " ▼") : ""), d.above_vwap ? "pos" : "neg") +
        row("Day open", money(d.day_open)) + row("Prev close", money(d.prev_close)) +
        row("Day high", money(d.day_high)) + row("Day low", money(d.day_low)) +
        row("5-min 9-EMA", money(d.i_ema9)) + row("5-min 20-EMA", money(d.i_ema20)) +
        row("5-min RSI", d.i_rsi != null ? d.i_rsi : "—") +
        row("Opening range", money(d.or_low) + " – " + money(d.or_high)) + `</div>`;
    }
    if (d.daily_trend) {
      html += sect("📈 Daily swing context") + `<div class="ana-grid">` +
        row("Daily trend", esc(d.daily_trend)) +
        row("Daily RSI", d.daily_rsi != null ? d.daily_rsi : "—") +
        row("20-EMA", money(d.ema20)) + row("50-EMA", money(d.ema50)) + `</div>`;
    }
    const ds = d.daily_stats;
    if (ds) {
      html += sect("📅 Positional (daily)") + `<div class="ana-grid">` +
        row("52-wk high", money(ds.week52_high) + (ds.from_52w_high != null ? ` <small>(${ds.from_52w_high >= 0 ? "+" : ""}${ds.from_52w_high}%)</small>` : ""), signCls(ds.from_52w_high)) +
        row("52-wk low", money(ds.week52_low) + (ds.from_52w_low != null ? ` <small>(${ds.from_52w_low >= 0 ? "+" : ""}${ds.from_52w_low}%)</small>` : ""), signCls(ds.from_52w_low)) +
        row("50-DMA", money(ds.sma50)) + row("200-DMA", money(ds.sma200)) +
        row("ATR(14)", ds.atr14 != null ? money(ds.atr14) + (ds.atr_pct != null ? ` <small>(${ds.atr_pct}%)</small>` : "") : "—") +
        row("Rel. volume", ds.rel_vol != null ? ds.rel_vol + "×" : "—", (ds.rel_vol || 0) >= 1.5 ? "pos" : "") +
        `</div>`;
      const rets = [["1W", ds.ret_1w], ["1M", ds.ret_1m], ["3M", ds.ret_3m], ["6M", ds.ret_6m], ["1Y", ds.ret_1y]].filter((x) => x[1] != null);
      if (rets.length) html += sect("↗ Returns") + `<div class="ana-rets">` +
        rets.map(([k, v]) => `<div class="ana-ret"><span>${k}</span><b class="${signCls(v)}">${v >= 0 ? "+" : ""}${v}%</b></div>`).join("") + `</div>`;
    }
    return html;
  }
  function readNotesHtml(d) {
    if (!d || !d.ok || !(d.notes || []).length) return "";
    return `<div class="ana-sect">📝 Read</div><ul class="ana-notes">` +
      d.notes.map((n) => `<li>${esc(n)}</li>`).join("") + `</ul>`;
  }

  function fundTabHtml(f, sym) {
    const scrUrl = "https://www.screener.in/company/" + encodeURIComponent(((f && f.symbol) || sym || "")) + "/";
    if (!f || !f.ok) {
      return `<p class="jnote">${esc((f && f.note) || "Fundamentals are temporarily unavailable for this stock.")}</p>` +
        `<a class="ana-report" href="${scrUrl}" target="_blank" rel="noopener noreferrer">📊 Open full fundamentals on Screener.in ↗</a>`;
    }
    // Key ratios (Market cap, P/E, ROCE, ROE, Book value, Dividend yield, …)
    const ratios = f.ratios || [];
    let html = `<div class="ana-chips">` + ratios.map((r) =>
      `<div class="ana-chip"><div class="k">${esc(r.name)}</div><div class="v">${esc(r.value)}</div></div>`).join("") + `</div>`;
    // Pros & cons (Screener's analysis)
    if ((f.pros && f.pros.length) || (f.cons && f.cons.length)) {
      html += `<div class="ana-pc">`;
      if (f.pros && f.pros.length) html += `<div class="ana-pccol"><div class="ana-pch pos">✔ Pros</div><ul class="ana-plist">` +
        f.pros.map((p) => `<li>${esc(p)}</li>`).join("") + `</ul></div>`;
      if (f.cons && f.cons.length) html += `<div class="ana-pccol"><div class="ana-pch neg">✘ Cons</div><ul class="ana-plist">` +
        f.cons.map((p) => `<li>${esc(p)}</li>`).join("") + `</ul></div>`;
      html += `</div>`;
    }
    if (f.about) html += `<div class="ana-sect">ℹ️ About</div><p class="ana-about">${esc(f.about)}</p>`;
    html += `<a class="ana-report" href="${esc(f.url || scrUrl)}" target="_blank" rel="noopener noreferrer">` +
      `📊 Full report on ${esc(f.source || "Screener.in")} — peers · quarterly · balance sheet · cash flow ↗</a>`;
    html += `<p class="ana-src">Fundamentals: ${esc(f.source || "Screener.in")}. Reported figures — may lag the live price.</p>`;
    return html;
  }
  let anaChart = null;
  function drawAnaChart(c, scls) {
    const el = $("anaChart");
    if (!el || typeof Chart === "undefined") return;
    if (anaChart) { anaChart.destroy(); anaChart = null; }
    const line = scls === "bear" ? "#ef5350" : scls === "bull" ? "#26a69a" : "#6ea8fe";
    const css = getComputedStyle(document.body);
    const grid = (css.getPropertyValue("--border") || "rgba(255,255,255,.08)").trim();
    const tick = (css.getPropertyValue("--muted") || "#9aa4b2").trim();
    const datasets = [{
      label: "Price", data: c.price, borderColor: line, backgroundColor: line + "22",
      borderWidth: 2, pointRadius: 0, tension: 0.25, fill: true,
    }];
    if (c.kind === "intraday" && c.vwap) datasets.push({
      label: "VWAP", data: c.vwap, borderColor: "#f5a623", borderWidth: 1.5,
      borderDash: [5, 4], pointRadius: 0, tension: 0.25, fill: false,
    });
    if (c.kind === "daily" && c.ema20) datasets.push({
      label: "20-EMA", data: c.ema20, borderColor: "#f5a623", borderWidth: 1.5,
      pointRadius: 0, tension: 0.25, fill: false,
    });
    const ann = (c.prev_close != null)
      ? [{ y: c.prev_close }] : [];
    const maxTicks = c.kind === "intraday" ? 7 : 8;
    anaChart = new Chart(el, {
      type: "line",
      data: { labels: c.labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: datasets.length > 1, labels: { color: tick, boxWidth: 12, font: { size: 11 } } },
          tooltip: { callbacks: { label: (x) => x.dataset.label + ": ₹" + Number(x.parsed.y).toFixed(2) } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: tick, maxTicksLimit: maxTicks, font: { size: 10 } } },
          y: { grid: { color: grid }, ticks: { color: tick, font: { size: 10 }, callback: (v) => "₹" + v } },
        },
      },
    });
    // prev-close reference line (drawn via a flat dataset for simplicity)
    if (ann.length && c.prev_close != null) {
      anaChart.data.datasets.push({
        label: "Prev close", data: c.price.map(() => c.prev_close),
        borderColor: tick, borderWidth: 1, borderDash: [2, 3], pointRadius: 0, fill: false,
      });
      anaChart.update();
    }
  }

  // ---------------------------------------------------------------- load
  async function loadJournal() {
    showLoading(true);
    try {
      const d = await (await fetch("/api/journal/overview" + filterQuery())).json();
      lastOverview = d;
      renderBanner(d.today);
      renderUntagged(d.summary);
      renderTargets(d.targets);
      renderTiles(d.summary);
      JDAILY = d.summary.daily || [];
      if (jfMode === "all" || !yearsPopulated) populateYears(JDAILY);
      renderFilterLabel();
      renderChart();
      renderTrades(d.recent_trades);
      const src = d.source || "";
      dhanConnected = d.dhan_connected;
      $("dhanBtn").style.display = src === "dhan" ? "" : "none";
      $("dhanBtn").textContent = dhanConnected ? "⬇ Sync from Dhan ✓" : "🔑 Connect Dhan";
      $("fyersSyncBtn").style.display = (src === "fyers" || src === "upstox") ? "" : "none";
      if (src === "upstox") $("fyersSyncBtn").textContent = "⟳ Sync from Upstox";
      else if (src === "fyers") $("fyersSyncBtn").textContent = "⟳ Sync from Fyers";
      if (src === "dhan" && dhanConnected && !dhanAutoSynced) dhanSyncNow(true);
      if ((src === "fyers" || src === "upstox") && !fyersAutoSynced) fyersSyncNow(true);
    } catch (e) {
      status("Could not load journal: " + e.message, true);
    } finally {
      showLoading(false);
    }
  }

  function renderBanner(today) {
    const b = $("ruleBanner");
    b.classList.remove("hidden");
    if (!today || !today.violations || !today.violations.length) {
      b.className = "rulebanner ok";
      b.innerHTML = `✅ <b>On track today.</b> No rules broken. ` +
        `Day P&amp;L: <b>${signed(today ? today.pnl : 0)}</b> over ${today ? today.trades : 0} trades.`;
      return;
    }
    b.className = "rulebanner bad";
    b.innerHTML = `⚠️ <b>Today you broke ${today.violations.length} rule${today.violations.length > 1 ? "s" : ""}</b> ` +
      `(day P&amp;L ${signed(today.pnl)}):<ul>` +
      today.violations.map((v) => `<li><b>${v.label}:</b> ${v.message}</li>`).join("") + `</ul>`;
  }

  function renderUntagged(s) {
    const el = $("untaggedNote");
    if (s && s.untagged_trades > 0) {
      el.classList.remove("hidden");
      el.innerHTML = `ℹ️ <b>${signed(s.untagged_realized)}</b> from ${s.untagged_trades} trades were imported ` +
        `<b>without dates</b> (a P&amp;L summary). Totals &amp; win-rate include them, but the ` +
        `<b>daily / weekly / monthly</b> views need a <b>Trade Book</b> export (with dates) from Dhan.`;
    } else {
      el.classList.add("hidden");
    }
  }

  // ================= Capital tracker page (monthly) =================
  let capMonth = null;              // 'YYYY-MM' currently viewed (null = current)
  let capData = null;               // last loaded tracker payload
  let capitalChart = null;

  async function loadCapital() {
    showLoading(true);
    try {
      const q = capMonth ? "?month=" + encodeURIComponent(capMonth) : "";
      capData = await (await fetch("/api/journal/capital-tracker" + q)).json();
      capMonth = capData.month;
      renderCapital(capData);
    } catch (e) {
      status("Could not load capital tracker: " + e.message, true);
    } finally {
      showLoading(false);
    }
  }

  function renderCapital(c) {
    capData = c;
    $("capMonthLabel").textContent = c.month_label;
    // month nav: disable prev/next when no data beyond the ends
    const months = c.months || [];
    const idx = months.indexOf(c.month);
    $("capPrev").disabled = idx <= 0;
    $("capNext").disabled = idx < 0 || idx >= months.length - 1;
    // sync buttons: show only the single active broker
    const csrc = c.source || "";
    $("capFyersSync").style.display = (csrc === "fyers" || csrc === "upstox") ? "" : "none";
    if (csrc === "upstox") $("capFyersSync").textContent = "⟳ Sync today (Upstox)";
    else if (csrc === "fyers") $("capFyersSync").textContent = "⟳ Sync today (Fyers)";
    $("capDhanSync").style.display = csrc === "dhan" ? "" : "none";
    $("capDhanSync").textContent = c.dhan_connected
      ? "⬇ Sync this month (Dhan)" : "🔑 Connect Dhan";
    const inp = $("openingCapital");
    if (inp && document.activeElement !== inp) inp.value = c.opening || "";
    const bk = $("capBrokerage"), ch = $("capCharges");
    // When costs are only estimated (not saved), leave inputs blank and show the
    // estimate as the placeholder; when saved, fill the exact values in.
    if (bk && document.activeElement !== bk) {
      bk.value = c.costs_estimated ? "" : (c.brokerage || "");
      bk.placeholder = c.costs_estimated && c.brokerage ? "est. " + Math.round(c.brokerage) : "e.g. 3600";
    }
    if (ch && document.activeElement !== ch) {
      ch.value = c.costs_estimated ? "" : (c.charges || "");
      ch.placeholder = c.costs_estimated && c.charges ? "est. " + Math.round(c.charges) : "e.g. 3342";
    }
    const rt = $("capBrokerageRate");
    if (rt && document.activeElement !== rt) rt.value = c.brokerage_rate != null ? c.brokerage_rate : "";
    drawCapitalTiles(c);
    const td = c.trade_days || [];
    $("capJournalSub").textContent = td.length
      ? `${td.length} trading day${td.length === 1 ? "" : "s"} this month`
      : "no trades this month yet";
    renderCapitalChart(c);
    renderCapitalDays(c);
  }

  function capTile(label, value, cls, sub) {
    return `<div class="cap-tile"><div class="cap-label">${label}</div>` +
      `<div class="cap-value ${cls || ""}">${value}</div>` +
      (sub ? `<div class="cap-subval">${sub}</div>` : "") + `</div>`;
  }
  function drawCapitalTiles(c) {
    const net = c.net_pnl != null ? c.net_pnl : c.total_pnl;
    const est = c.costs_estimated;
    const costNote = c.total_cost
      ? `gross ${signed(c.gross_pnl)} − ${est ? "est. " : ""}costs ${inr(c.total_cost)}` : "";
    $("capitalTiles").innerHTML =
      capTile("Opening capital", inr(c.opening)) +
      capTile("Net P&L this month", signed(net), net >= 0 ? "pos" : "neg", costNote) +
      capTile("Closing capital", inr(c.closing));
    // breakdown line (matches the broker's monthly summary)
    const bd = $("capitalBreakdown");
    if (bd) {
      if (c.total_cost) {
        bd.classList.remove("hidden");
        bd.innerHTML =
          `Gross P&L <b class="${c.gross_pnl >= 0 ? "pos" : "neg"}">${signed(c.gross_pnl)}</b>` +
          ` − Brokerage <b class="neg">${inr(c.brokerage)}</b>` +
          ` − Charges <b class="neg">${inr(c.charges)}</b>` +
          ` = Net <b class="${net >= 0 ? "pos" : "neg"}">${signed(net)}</b>` +
          (est ? ` <span class="cap-est">estimated — enter your broker's figures above for the exact net</span>` : "");
      } else {
        bd.classList.add("hidden");
        bd.innerHTML = "";
      }
    }
    const ret = $("capitalReturn");
    if (!c.opening) {
      ret.innerHTML = `<span class="cap-hint">Enter your Day 1 capital above to see your return.</span>`;
    } else {
      const rc = c.return_pct >= 0 ? "pos" : "neg";
      const sign = c.return_pct >= 0 ? "+" : "";
      ret.innerHTML = `Return this month (net): <b class="${rc}">${sign}${Number(c.return_pct).toFixed(2)}%</b>` +
        ` <span class="cap-hint">· on ${inr(c.opening)} opening capital</span>`;
    }
  }

  function renderCapitalChart(c) {
    const days = c.days || [];
    const labels = days.map((d) => d.label);
    const bars = days.map((d) => (d.traded ? d.pnl : null));
    const barColors = days.map((d) => (d.pnl >= 0 ? "#26a69a" : "#ef5350"));
    // Cumulative capital line (only up to the last traded / today's day)
    const lastIdx = Math.max(0, ...days.map((d, i) => (d.traded ? i : -1)));
    const line = days.map((d, i) => (i <= lastIdx && !d.future ? d.capital : null));
    const data = {
      labels,
      datasets: [
        {
          type: "bar", label: "Daily P&L", data: bars, backgroundColor: barColors,
          borderRadius: 3, maxBarThickness: 18, yAxisID: "y", order: 2
        },
        {
          type: "line", label: "Capital", data: line, borderColor: "#f0b429",
          backgroundColor: "rgba(240,180,41,.12)", borderWidth: 2, tension: .25,
          pointRadius: 0, fill: true, yAxisID: "y1", order: 1, spanGaps: true
        },
      ],
    };
    const opts = {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#8b95a7", usePointStyle: true, boxWidth: 8 } },
        tooltip: { callbacks: { label: (x) => `${x.dataset.label}: ${x.raw == null ? "—" : "₹" + Number(x.raw).toLocaleString("en-IN")}` } },
      },
      scales: {
        x: {
          ticks: { color: "#8b95a7", maxRotation: 0, autoSkip: true, maxTicksLimit: 16 },
          grid: { display: false }
        },
        y: {
          position: "left", ticks: { color: "#8b95a7", callback: (v) => (Math.abs(v) >= 1000 ? "₹" + Math.round(v / 1000) + "k" : "₹" + v) },
          grid: { color: "rgba(128,128,128,.18)" }
        },
        y1: {
          position: "right", ticks: { color: "#b78a1a", callback: (v) => "₹" + Math.round(v / 1000) + "k" },
          grid: { display: false }
        },
      },
    };
    if (!capitalChart) capitalChart = new Chart($("capitalChart"), { data, options: opts });
    else { capitalChart.data = data; capitalChart.options = opts; capitalChart.update(); }
  }

  function renderCapitalDays(c) {
    const el = $("capitalDays");
    const td = c.trade_days || [];
    if (!td.length) {
      el.innerHTML = `<p class="empty">No trades synced for ${c.month_label} yet. ` +
        `Go to the <b>Journal</b> tab and sync from Fyers/Dhan, and your days will appear here.</p>`;
      return;
    }
    el.innerHTML = td.map((d) => {
      const wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][new Date(d.date).getDay() === 0 ? 6 : new Date(d.date).getDay() - 1];
      const cls = d.pnl >= 0 ? "pos" : "neg";
      return `<div class="cap-day">
        <div class="cap-day-date"><b>D${d.n}</b><span>${wd} · ${d.date}</span></div>
        <div class="cap-day-pnl ${cls}">${signed(d.pnl)}</div>
        <input class="cap-day-note" data-date="${d.date}" value="${esc(d.note || "")}" placeholder="note (e.g. Gap up)…" />
        <div class="cap-day-cap">→ ${inr(d.capital)}</div>
      </div>`;
    }).join("");
  }

  // Opening capital / brokerage / charges: live recompute of Net P&L while
  // typing, persist on change (blur) as a safety net alongside the Save button.
  function capLiveRecompute() {
    if (!capData) return;
    const opening = Number($("openingCapital").value) || 0;
    const bkRaw = $("capBrokerage").value, chRaw = $("capCharges").value;
    const typed = bkRaw !== "" || chRaw !== "";
    const gross = capData.gross_pnl != null ? capData.gross_pnl : (capData.total_pnl || 0);
    // typed → exact override; blank → the backend figure (estimate when unsaved)
    const brokerage = typed ? (Number(bkRaw) || 0) : (capData.brokerage || 0);
    const charges = typed ? (Number(chRaw) || 0) : (capData.charges || 0);
    const estimated = typed ? false : capData.costs_estimated;
    const total_cost = brokerage + charges;
    const net = gross - total_cost;
    drawCapitalTiles({
      opening, gross_pnl: gross, brokerage, charges, total_cost,
      net_pnl: net, total_pnl: net, costs_estimated: estimated,
      closing: opening + net, return_pct: opening ? (net / opening * 100) : 0
    });
  }
  function capCostBody() {
    const body = { opening: Number($("openingCapital").value) || 0, month: capMonth };
    const bkRaw = $("capBrokerage").value, chRaw = $("capCharges").value;
    if (bkRaw !== "" || chRaw !== "") {      // only override when actually entered
      body.brokerage = Number(bkRaw) || 0;
      body.charges = Number(chRaw) || 0;
    }
    const rtRaw = $("capBrokerageRate") ? $("capBrokerageRate").value : "";
    if (rtRaw !== "") body.brokerage_rate = Number(rtRaw) || 0;
    return body;
  }
  function capPersist() {
    fetch("/api/journal/capital", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(capCostBody()),
    }).then((r) => r.json()).then(renderCapital).catch(() => { });
  }
  ["openingCapital", "capBrokerage", "capCharges"].forEach((id) => {
    const el = $(id);
    if (el) { el.addEventListener("input", capLiveRecompute); el.addEventListener("change", capPersist); }
  });
  // brokerage rate changes the estimate server-side → persist + reload (no live preview)
  if ($("capBrokerageRate")) $("capBrokerageRate").addEventListener("change", capPersist);
  // Save a per-day note on change.
  if ($("capitalDays")) {
    $("capitalDays").addEventListener("change", (e) => {
      const el = e.target;
      if (!el.classList || !el.classList.contains("cap-day-note")) return;
      fetch("/api/journal/daynote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: el.dataset.date, note: el.value }),
      }).catch(() => { });
    });
  }
  function capShiftMonth(delta) {
    const months = (capData && capData.months) || [];
    const idx = months.indexOf(capMonth);
    const ni = idx + delta;
    if (ni < 0 || ni >= months.length) return;
    capMonth = months[ni];
    loadCapital();
  }
  if ($("capPrev")) $("capPrev").addEventListener("click", () => capShiftMonth(-1));
  if ($("capNext")) $("capNext").addEventListener("click", () => capShiftMonth(1));

  function capSaveMsg(msg, err) {
    const s = $("capSaveMsg");
    if (!s) return;
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jnote" + (err ? " err" : "");
    s.innerHTML = msg;
    s.classList.remove("hidden");
  }
  // Explicit Save: persists opening capital + every visible day note at once,
  // and confirms, so the user knows it's stored in the cloud.
  async function capSaveAll() {
    const btn = $("capSaveBtn"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Saving…";
    capSaveMsg("");
    try {
      const r = await fetch("/api/journal/capital", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(capCostBody()),
      });
      if (!r.ok) throw new Error("capital save failed");
      const fresh = await r.json();
      // Save any day notes currently on screen.
      const noteEls = [...document.querySelectorAll(".cap-day-note")];
      const res = await Promise.allSettled(noteEls.map((el) =>
        fetch("/api/journal/daynote", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ date: el.dataset.date, note: el.value }),
        })));
      renderCapital(fresh);
      const failed = res.filter((x) => x.status === "rejected").length;
      capSaveMsg(failed
        ? `Saved capital, but ${failed} note${failed === 1 ? "" : "s"} failed — try again.`
        : "✓ Saved to the cloud. Your capital and notes are safe.", failed > 0);
    } catch (e) {
      capSaveMsg("Could not save — check your connection and try again.", true);
    } finally { btn.disabled = false; btn.textContent = old; }
  }
  if ($("capSaveBtn")) $("capSaveBtn").addEventListener("click", capSaveAll);

  function capSyncMsg(msg, err) {
    const s = $("capSyncMsg");
    if (!s) return;
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jnote" + (err ? " err" : "");
    s.innerHTML = msg;
    s.classList.remove("hidden");
  }
  // Compute the viewed month's [start, end], capping end at today (no future dates).
  function capMonthRange() {
    const [y, m] = capMonth.split("-").map(Number);
    const last = new Date(y, m, 0).getDate();
    let end = `${capMonth}-${String(last).padStart(2, "0")}`;
    const today = todayISO();
    if (end > today) end = today;
    return { start: `${capMonth}-01`, end };
  }
  // Pull the whole viewed month from Dhan (clears only that month, re-inserts).
  async function capDhanSyncMonth() {
    if (!capData || !capData.dhan_connected) {   // not connected → connect modal
      if ($("dhanFrom")) $("dhanFrom").value = `${capMonth}-01`;
      if ($("dhanTo")) $("dhanTo").value = capMonthRange().end;
      openDhanModal();
      return;
    }
    const { start, end } = capMonthRange();
    const btn = $("capDhanSync"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Syncing…";
    capSyncMsg(`Pulling ${capData.month_label} trades from Dhan…`);
    try {
      const r = await fetch("/api/journal/dhan/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from: start, to: end }),
      });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) { capSyncMsg("Dhan token expired — reconnect from the Journal tab.", true); return; }
        throw new Error(d.detail || "sync failed");
      }
      capSyncMsg(`✅ Synced ${d.round_trips} round-trips from ${d.raw_trades} Dhan trades · ${signed(d.realized)}` +
        (d.note ? ` — ${d.note}` : ""));
      loadCapital();
    } catch (e) {
      capSyncMsg("Dhan sync failed: " + e.message, true);
    } finally { btn.disabled = false; btn.textContent = old; }
  }
  // Fyers tradebook is today-only, so this syncs today's fills (not a month).
  async function capFyersSyncToday() {
    const btn = $("capFyersSync"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Syncing…";
    capSyncMsg("Pulling today's trades from Fyers…");
    try {
      const r = await fetch("/api/journal/sync", { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) { capSyncMsg("Log in to Fyers on the Dashboard tab first.", true); return; }
        throw new Error(d.detail || "sync failed");
      }
      capSyncMsg(`✅ Synced ${d.round_trips} round-trips from Fyers today · ${signed(d.realized_today || 0)}`);
      loadCapital();
    } catch (e) {
      capSyncMsg("Fyers sync failed: " + e.message, true);
    } finally { btn.disabled = false; btn.textContent = old; }
  }
  if ($("capDhanSync")) $("capDhanSync").addEventListener("click", capDhanSyncMonth);
  if ($("capFyersSync")) $("capFyersSync").addEventListener("click", capFyersSyncToday);

  function targetCard(name, t) {
    return `<div class="tlabel">${name} Target</div>
      <div class="tvalue ${t.achieved >= 0 ? "good" : "bad"}">${signed(t.achieved)} <span class="tof">/ ₹${fmt(t.target)}</span></div>
      <div class="tbar"><span class="${name === "Weekly" ? "put" : "call"}" style="width:${t.pct}%"></span></div>
      <div class="tplan">${t.plan}</div>`;
  }
  function renderTargets(tg) {
    $("weeklyTarget").innerHTML = targetCard("Weekly", tg.weekly);
    $("monthlyTarget").innerHTML = targetCard("Monthly", tg.monthly);
  }

  function tile(label, val, sub, cls) {
    return `<div class="tile ${cls || "neutral"}"><div class="label">${label}</div>` +
      `<div class="value">${val}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
  }
  function renderTiles(s) {
    const st = s.current_streak || { type: "none", count: 0 };
    const streakTxt = st.count ? `${st.count} ${st.type} day${st.count > 1 ? "s" : ""}` : "—";
    const net = s.net_realized != null ? s.net_realized : s.total_realized;
    const netAvg = s.net_avg_day != null ? s.net_avg_day : s.avg_day;
    $("journalTiles").innerHTML = [
      tile("Net P&L", signed(net), s.total_costs ? `after ${s.costs_estimated ? "~" : ""}${inr(s.total_costs)} ${s.costs_estimated ? "est. costs" : "brokerage + charges"}` : "no costs yet", net >= 0 ? "good" : "bad"),
      tile("Overall P&L", signed(s.total_realized), `${s.trading_days} trading days · before costs`, s.total_realized >= 0 ? "good" : "bad"),
      tile("Brokerage", inr(s.total_brokerage || 0), s.costs_estimated ? "estimated" : "from broker", "neutral"),
      tile("Charges", inr(s.total_charges || 0), "STT · GST · exchange", "neutral"),
      tile("Total trades", fmt(s.total_trades || 0), "round-trips this period", "neutral"),
      tile("Win rate", (s.win_rate || 0) + "%", `${s.wins}W / ${s.losses}L`, s.win_rate >= 50 ? "good" : "bad"),
      tile("Profit days", s.profit_days, `vs ${s.loss_days} loss days`, "good"),
      tile("Avg / day (net)", signed(netAvg), `gross ${signed(s.avg_day)}/day`, netAvg >= 0 ? "good" : "bad"),
      tile("Best day", s.best_day ? signed(s.best_day.realized) : "—", s.best_day ? s.best_day.date : "", s.best_day && s.best_day.realized >= 0 ? "good" : "bad"),
      tile("Worst day", s.worst_day ? signed(s.worst_day.realized) : "—", s.worst_day ? s.worst_day.date : "", s.worst_day && s.worst_day.realized >= 0 ? "good" : "bad"),
      tile("Current streak", streakTxt, "", st.type === "profit" ? "good" : st.type === "loss" ? "bad" : "neutral"),
      tile("Max drawdown", signed(maxDrawdown(s.daily || [])), "peak-to-trough", "bad"),
    ].join("");
  }

  // Largest peak-to-trough drop of the cumulative equity curve (<= 0).
  function maxDrawdown(daily) {
    const sorted = [...daily].sort((a, b) => (a.date < b.date ? -1 : 1));
    let cum = 0, peak = 0, mdd = 0;
    for (const d of sorted) {
      cum += Number(d.realized || 0);
      peak = Math.max(peak, cum);
      mdd = Math.min(mdd, cum - peak);
    }
    return Math.round(mdd);
  }

  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function weekStart(dateStr) {               // Monday of that ISO week → YYYY-MM-DD
    const d = new Date(dateStr + "T00:00:00");
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d.toISOString().slice(0, 10);
  }
  function aggregate(daily, gran) {
    if (gran === "daily") return daily.map((d) => ({ label: d.date, value: d.realized }));
    const map = new Map();
    daily.forEach((d) => {
      const key = gran === "weekly" ? weekStart(d.date) : d.date.slice(0, 7);
      map.set(key, (map.get(key) || 0) + d.realized);
    });
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([k, v]) => ({
      label: gran === "weekly" ? "wk " + k.slice(5) : MON[+k.slice(5, 7) - 1] + " " + k.slice(0, 4),
      value: Math.round(v),
    }));
  }

  // Heatmap colour: pale for small magnitude → deep/dark for large (per the view's max).
  const _lerp = (a, b, t) => Math.round(a + (b - a) * t);
  function heatColor(value, maxAbs) {
    const t = Math.pow(maxAbs ? Math.min(1, Math.abs(value) / maxAbs) : 0, 0.65);
    if (value >= 0) {       // light green → deep green
      return `rgb(${_lerp(165, 13, t)},${_lerp(226, 115, t)},${_lerp(214, 95, t)})`;
    }                       // light red → deep dark red
    return `rgb(${_lerp(255, 176, t)},${_lerp(176, 20, t)},${_lerp(174, 18, t)})`;
  }

  function renderChart() {
    const agg = aggregate(JDAILY, chartGran);
    const labels = agg.map((a) => a.label);
    $("pnlChartTitle").textContent =
      (chartGran[0].toUpperCase() + chartGran.slice(1)) + (chartType === "line" ? " P&L — Cumulative" : " P&L");

    let dataset;
    if (chartType === "line") {
      let run = 0;
      const cum = agg.map((a) => (run += a.value, Math.round(run)));
      dataset = {
        label: "Cumulative P&L", data: cum, borderColor: "#5b8def",
        backgroundColor: "rgba(91,141,239,.15)", fill: true, tension: 0.25,
        pointRadius: 2, pointBackgroundColor: "#5b8def",
      };
    } else {
      // separate intensity scales: biggest profit = deep green, biggest loss = deep red
      const maxPos = Math.max(1, ...agg.filter((a) => a.value > 0).map((a) => a.value));
      const maxNeg = Math.max(1, ...agg.filter((a) => a.value < 0).map((a) => -a.value));
      dataset = {
        label: "P&L", data: agg.map((a) => a.value),
        backgroundColor: agg.map((a) => heatColor(a.value, a.value >= 0 ? maxPos : maxNeg)),
      };
    }
    const op = +(homeData && homeData.summary && homeData.summary.opening_capital) || 0;
    let runOpen = op;
    const dayOpen = agg.map((a) => { const v = round2(runOpen); runOpen += (+a.value || 0); return v; });
    const capDs = _capRefDs("Opening capital", dayOpen, "y1");
    dataset.yAxisID = "y";
    const opts = {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: _capLegend("#8b95a7", 11),
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.datasetIndex === 0 ? signed(c.raw) : "₹" + fmt(c.raw, 0)}` } }
      },
      scales: {
        x: { ticks: { color: "#8b95a7", maxRotation: 60, minRotation: 0 }, grid: { display: false } },
        y: { position: "left", ticks: { color: "#8b95a7" }, grid: { color: "rgba(128,128,128,.18)" } },
        y1: _y1Scale(),
      },
    };
    if (pnlChart) pnlChart.destroy();
    pnlChart = new Chart($("pnlChart"), { type: chartType === "line" ? "line" : "bar", data: { labels, datasets: [dataset, capDs] }, options: opts });
  }

  // chart control segments
  function wireSeg(segId, apply) {
    document.querySelectorAll("#" + segId + " button").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("#" + segId + " button").forEach((x) => x.classList.toggle("active", x === b));
        apply(b.dataset);
        renderChart();
      }));
  }
  wireSeg("granSeg", (ds) => { chartGran = ds.g; });
  wireSeg("typeSeg", (ds) => { chartType = ds.t; });

  // ---------------------------------------------------------------- period filter
  const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  function populateYears(daily) {
    const years = new Set((daily || []).map((d) => +d.date.slice(0, 4)).filter(Boolean));
    years.add(new Date().getFullYear());
    const sorted = [...years].sort((a, b) => b - a);
    if (!years.has(jfYear)) jfYear = sorted[0];
    const sel = $("filterYear");
    sel.innerHTML = sorted.map((y) => `<option value="${y}">${y}</option>`).join("");
    sel.value = jfYear;
    const msel = $("filterMonth");
    msel.innerHTML = MONTH_NAMES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join("");
    msel.value = jfMonth;
    yearsPopulated = true;
  }

  function syncFilterControls() {
    const isRange = jfMode === "range";
    $("filterYear").classList.toggle("hidden", jfMode === "all" || isRange);
    $("filterMonth").classList.toggle("hidden", jfMode !== "month");
    $("jfFrom").classList.toggle("hidden", !isRange);
    $("jfTo").classList.toggle("hidden", !isRange);
    $("jfRangeSep").classList.toggle("hidden", !isRange);
  }

  function renderFilterLabel() {
    const el = $("filterRangeLabel");
    if (!el) return;
    const days = (lastOverview && lastOverview.summary) ? lastOverview.summary.trading_days : 0;
    let scope = "All time";
    if (jfMode === "year") scope = String(jfYear);
    else if (jfMode === "month") scope = `${MONTH_NAMES[jfMonth - 1]} ${jfYear}`;
    else if (jfMode === "range") {
      scope = (jfFrom || jfTo) ? `${jfFrom || "…"} → ${jfTo || "…"}` : "Pick a date range";
    }
    el.textContent = `${scope} · ${days} trading day${days === 1 ? "" : "s"}`;
  }

  document.querySelectorAll("#periodFilterSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#periodFilterSeg button").forEach((x) => x.classList.toggle("active", x === b));
      jfMode = b.dataset.r;
      if (jfMode === "range" && !jfFrom && !jfTo) {    // seed sensible defaults on first use
        const pad = (n) => String(n).padStart(2, "0");
        const now = new Date();
        jfTo = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
        jfFrom = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`;
        $("jfFrom").value = jfFrom;
        $("jfTo").value = jfTo;
      }
      syncFilterControls();
      loadJournal();
    }));
  $("filterYear").addEventListener("change", (e) => { jfYear = +e.target.value; loadJournal(); });
  $("filterMonth").addEventListener("change", (e) => { jfMonth = +e.target.value; loadJournal(); });
  function onJfRangeChange() {
    if (jfFrom && jfTo && jfFrom > jfTo) {           // guard: from after to → swap
      [jfFrom, jfTo] = [jfTo, jfFrom];
      $("jfFrom").value = jfFrom;
      $("jfTo").value = jfTo;
    }
    loadJournal();
  }
  $("jfFrom").addEventListener("change", (e) => { jfFrom = e.target.value || ""; onJfRangeChange(); });
  $("jfTo").addEventListener("change", (e) => { jfTo = e.target.value || ""; onJfRangeChange(); });
  $("exportBtn").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/journal/export.csv" + filterQuery();
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
  });

  $("saveDetailsBtn").addEventListener("click", saveAllDetails);

  // ---- Clear journal data (by source or all) ----
  if ($("clearBtn")) $("clearBtn").addEventListener("click", () => {
    $("clearStatus").classList.add("hidden");
    $("clearModal").classList.remove("hidden");
  });
  if ($("clearCloseBtn")) $("clearCloseBtn").addEventListener("click", () =>
    $("clearModal").classList.add("hidden"));
  if ($("clearModal")) $("clearModal").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-clear]");
    if (!btn) return;
    const src = btn.dataset.clear;
    const label = src === "all" ? "ALL trades from every source" : `all ${src.toUpperCase()} trades`;
    if (!confirm(`Delete ${label}? This can't be undone.`)) return;
    btn.disabled = true;
    try {
      const r = await fetch("/api/journal/clear", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: src }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "clear failed");
      const s = $("clearStatus");
      s.className = "jnote";
      s.textContent = `✓ Cleared ${label}.`;
      s.classList.remove("hidden");
      await loadJournal();
      setTimeout(() => $("clearModal").classList.add("hidden"), 900);
    } catch (err) {
      const s = $("clearStatus");
      s.className = "jnote err";
      s.textContent = "Could not clear: " + err.message;
      s.classList.remove("hidden");
    } finally { btn.disabled = false; }
  });

  // Pull today's executed trades straight from Fyers (no CSV needed).
  // isAuto = fired automatically on load → fail quietly if not logged in.
  async function fyersSyncNow(isAuto) {
    fyersAutoSynced = true;                   // guard loadJournal auto-sync re-entry
    const btn = $("fyersSyncBtn"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Syncing…";
    if (!isAuto) status("Fetching today's trades from Fyers…");
    try {
      const r = await fetch("/api/journal/sync", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (r.status === 401) {
          status("Log in to Fyers (Dashboard → Login with Fyers) to sync today's trades.", true);
          return;
        }
        throw new Error(d.detail || d.error || "Sync failed");
      }
      status(`✅ Synced ${d.round_trips} trade${d.round_trips === 1 ? "" : "s"} from Fyers` +
        ` · today's P&L ${signed(d.realized_today)}` +
        (d.open_positions ? ` · ${d.open_positions} open` : ""));
      loadJournal();
    } catch (e) {
      if (!isAuto) status("Fyers sync: " + e.message, true);
    } finally {
      btn.disabled = false; btn.textContent = old;
    }
  }
  $("fyersSyncBtn").addEventListener("click", () => fyersSyncNow(false));

  // Predefined setups for the Setup dropdown (edit to taste). Keeping a fixed
  // list means the "Setup Discipline" analytics group cleanly (no typos).
  const SETUPS = ["Breakout", "Breakdown", "Reversal", "Trend",
    "Gap up", "Gap down", "Range", "Scalp", "Momentum", "News/Event", "Other"];
  function setupOptions(current) {
    const opts = [""].concat(SETUPS);
    if (current && !opts.includes(current)) opts.push(current);  // keep an existing custom value
    return opts.map((o) =>
      `<option value="${esc(o)}" ${o === current ? "selected" : ""}>${o || "— setup —"}</option>`).join("");
  }

  function renderTrades(rows) {
    const tb = $("tradesTable").querySelector("tbody");
    $("tradeCount").textContent = rows.length ? `(${rows.length})` : "";
    const empty = $("emptyJournal");
    empty.classList.toggle("hidden", rows.length > 0);
    if (!rows.length) {
      empty.innerHTML = jfMode === "all"
        ? `No trades yet. Click <b>Sync from Dhan</b> after you trade, or <b>Import CSV</b> to load past trades.`
        : `No trades in the selected ${jfMode === "range" ? "date range" : "period"}. Try a wider range or <b>All time</b>.`;
    }
    tb.innerHTML = rows.map((t) => {
      _tradeCache.set(String(t.id), t);
      const pnl = t.pnl == null ? "—" : signed(t.pnl);
      const cls = t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : "";
      const sideCls = t.side === "LONG" || t.side === "BUY" ? "pos" : "neg";
      const dateTxt = t.date === "unknown" ? "—" : t.date;
      const tag = t.tag || {};
      const on = tag.on_plan;
      return `<tr class="tr-click" data-tid="${esc(t.id)}"><td style="text-align:left">${dateTxt}</td><td style="text-align:left">${t.time || ""}</td>
        <td style="text-align:left">${t.symbol || ""}</td><td class="${sideCls}">${t.side || ""}</td>
        <td>${fmt(t.qty)}</td><td>${fmt(t.price, 2)}</td><td class="${cls}"><b>${pnl}</b></td>
        <td><select class="setupin" data-id="${t.id}">${setupOptions(tag.setup || "")}</select></td>
        <td><select class="planin" data-id="${t.id}">
          <option value="">—</option>
          <option value="1" ${on === 1 ? "selected" : ""}>✓ On plan</option>
          <option value="0" ${on === 0 ? "selected" : ""}>✗ Off plan</option>
        </select></td>
        <td><input class="notein" data-id="${t.id}" value="${esc(tag.note || "")}" placeholder="note…" /></td></tr>`;
    }).join("");
  }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); }

  // Collect one row's current details (setup + plan + note) into a tag payload.
  function rowTag(row) {
    const setup = (row.querySelector(".setupin") || {}).value || "";
    const note = (row.querySelector(".notein") || {}).value || "";
    const plan = (row.querySelector(".planin") || {}).value;
    const id = (row.querySelector("[data-id]") || {}).dataset?.id;
    return {
      id, setup, note: note,
      on_plan: plan === "1" ? 1 : plan === "0" ? 0 : null
    };
  }
  function saveTag(payload) {
    return fetch("/api/journal/trades/tag", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // Auto-save a row the moment its Setup / Plan / Note changes (safety net), so
  // nothing is lost even if the user forgets the Save button. (event delegation)
  $("tradesTable").addEventListener("change", (e) => {
    const el = e.target;
    if (!el.dataset || !el.dataset.id) return;
    saveTag(rowTag(el.closest("tr"))).catch(() => { });
  });

  // Explicit "Save details" button: writes every row's setup/plan/note at once
  // and confirms, so the user knows their journal notes are stored and will be
  // there next time they open the app.
  async function saveAllDetails() {
    const rows = [...$("tradesTable").querySelectorAll("tbody tr")];
    const payloads = rows.map(rowTag).filter((p) => p.id);
    if (!payloads.length) { status("No trades to save details for yet.", true); return; }
    const btn = $("saveDetailsBtn");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const res = await Promise.allSettled(payloads.map(saveTag));
      const ok = res.filter((r) => r.status === "fulfilled" && r.value.ok).length;
      if (ok === payloads.length) {
        status(`✓ Saved details for ${ok} trade${ok === 1 ? "" : "s"}. They'll be here next time you open the app.`);
      } else {
        status(`Saved ${ok} of ${payloads.length} — some failed, please try again.`, ok === 0);
      }
    } catch { status("Could not save — check your connection and try again.", true); }
    finally { btn.disabled = false; btn.textContent = label; }
  }

  // ---------------------------------------------------------------- actions
  function status(msg, err) {
    const s = $("jstatus");
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jstatus " + (err ? "err" : "ok");
    s.textContent = msg;
    s.classList.remove("hidden");
  }

  // ---- Dhan connect + sync ----
  function fyStartISO() {
    const d = new Date();
    const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; // Apr = month 3
    return `${y}-04-01`;
  }
  function todayISO() { return new Date().toISOString().slice(0, 10); }
  function dhanMsg(msg, err) {
    const s = $("dhanStatus");
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jnote" + (err ? " err" : "");
    s.innerHTML = msg;
    s.classList.remove("hidden");
  }

  function openDhanModal() {
    // Default to today (daily sync). Change "From" to an earlier date to backfill history.
    if (!$("dhanFrom").value) $("dhanFrom").value = todayISO();
    if (!$("dhanTo").value) $("dhanTo").value = todayISO();
    dhanMsg("");
    $("dhanModal").classList.remove("hidden");
  }

  // One-click sync using the saved token (no popup). Opens the modal to
  // reconnect only when the token is missing/expired (and not an auto-sync).
  async function dhanSyncNow(isAuto) {
    dhanAutoSynced = true;                    // guard loadJournal auto-sync re-entry
    const btn = $("dhanBtn"), old = btn.textContent;
    btn.disabled = true; btn.textContent = "Syncing…";
    status("Syncing your trades from Dhan…");
    try {
      const r = await fetch("/api/journal/dhan/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from: fyStartISO(), to: todayISO() }),
      });
      const d = await r.json();
      if (!r.ok) {
        if (r.status === 401) {
          status("Dhan token expired — reconnect with a fresh token.", true);
          if (!isAuto) openDhanModal();
          return;
        }
        throw new Error(d.detail || "sync failed");
      }
      status(`✅ Synced ${d.round_trips} round-trips from ${d.raw_trades} Dhan trades` +
        (d.date_range ? ` (${d.date_range[0]} → ${d.date_range[1]})` : "") +
        ` · total ${signed(d.realized)}`);
      loadJournal();
    } catch (e) {
      if (!isAuto) status("Dhan sync failed: " + e.message, true);
    } finally {
      btn.disabled = false; btn.textContent = old;
    }
  }

  $("dhanBtn").addEventListener("click", () => {
    if (dhanConnected) dhanSyncNow(false);    // connected → one-click re-sync
    else openDhanModal();                     // first time → enter token
  });
  $("dhanCloseBtn").addEventListener("click", () => $("dhanModal").classList.add("hidden"));

  $("dhanSaveSync").addEventListener("click", async () => {
    const cid = $("dhanClientId").value.trim(), tok = $("dhanToken").value.trim();
    if (!tok) { dhanMsg("Paste your Dhan access token first.", true); return; }
    try {
      await fetch("/api/journal/dhan/connect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: cid, access_token: tok }),
      });
      dhanMsg("Syncing your trade history from Dhan…");
      const r = await fetch("/api/journal/dhan/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from: $("dhanFrom").value, to: $("dhanTo").value }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "sync failed");
      dhanAutoSynced = true;
      $("dhanToken").value = "";
      $("dhanModal").classList.add("hidden");
      status(`✅ Synced ${d.round_trips} round-trips from ${d.raw_trades} Dhan trades` +
        (d.date_range ? ` (${d.date_range[0]} → ${d.date_range[1]})` : "") +
        ` · total ${signed(d.realized)}. Daily/weekly/monthly views are now live.`);
      loadJournal();
    } catch (e) { dhanMsg("Dhan sync failed: " + e.message, true); }
  });

  $("csvInput").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    status("Importing " + file.name + "…");
    try {
      const text = await file.text();
      const r = await fetch("/api/journal/import", { method: "POST", body: text });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "import failed");
      status(`Imported ${d.trades_created} trades (total ${signed(d.total_pnl)})` +
        (d.date_range ? `, ${d.date_range[0]} → ${d.date_range[1]}` : "") + "." +
        (d.note ? " " + d.note : ""), d.no_dates);
      loadJournal();
    } catch (err) { status("Import failed: " + err.message, true); }
    e.target.value = "";
  });

  // ---------------------------------------------------------------- Rules page
  let ruleChart = null;
  let rulesCfg = {};

  // The Journal's "Rules & Targets" button just jumps to the Rules tab.
  $("settingsBtn").addEventListener("click", () => switchView("rules"));
  $("saveSettings").addEventListener("click", saveRules);

  async function loadRules() {
    showLoading(true);
    try {
      const [ov, an, plan] = await Promise.all([
        fetch("/api/journal/overview").then((r) => r.json()),
        fetch("/api/journal/rules/analysis").then((r) => r.json()),
        fetch("/api/journal/plan").then((r) => r.json()),
      ]);
      rulesCfg = ov.rules_config || {};
      renderRulesForm(rulesCfg, ov.targets_config || {});
      renderRuleStats(an);
      renderRuleChart(an);
      renderGamePlan(plan);
    } catch (e) { rulesSaveMsg("Could not load rules: " + e.message, true); }
    finally { showLoading(false); }
  }

  const planCharts = {};
  function planBar(id, labels, values) {
    const el = $(id);
    if (!el) return;
    if (planCharts[id]) planCharts[id].destroy();
    const colors = values.map((v) => (v >= 0 ? "#26a69a" : "#ef5350"));
    planCharts[id] = new Chart(el, {
      type: "bar",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4, maxBarThickness: 34 }] },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => signed(c.raw) } } },
        scales: {
          x: {
            ticks: { color: "#8b95a7", callback: (v) => (Math.abs(v) >= 1000 ? "₹" + Math.round(v / 1000) + "k" : "₹" + v) },
            grid: { color: "rgba(128,128,128,.18)" }
          },
          y: { ticks: { color: "#8b95a7", font: { weight: "600" } }, grid: { display: false } },
        },
      },
    });
  }

  // This week's day-by-day plan with progress bars.
  function renderWeekPlan(wk) {
    const overall = $("weekPlanOverall"), daysEl = $("weekPlanDays");
    if (!overall || !daysEl) return;
    if (!wk || !wk.weekly_target) {
      overall.innerHTML = `<p class="wk-target">Set a weekly target on this page to see your daily plan.</p>`;
      daysEl.innerHTML = "";
      return;
    }
    const achPos = wk.achieved >= 0;
    overall.innerHTML =
      `<div class="wk-oline"><span>Weekly progress · aim ${inr(wk.per_day_target)}/day</span>` +
      `<span><b class="${achPos ? "pos" : "neg"}">${signed(wk.achieved)}</b> / ${inr(wk.weekly_target)} (${wk.pct}%)</span></div>` +
      `<div class="wk-track"><span style="width:${wk.pct}%"></span></div>`;

    daysEl.innerHTML = (wk.days || []).map((d) => {
      const dd = d.date.slice(8) + "/" + d.date.slice(5, 7);
      let badge, cls = "", actualHtml;
      if (d.future) {
        badge = `<span class="wk-badge soon">Upcoming</span>`;
        cls = "future";
        actualHtml = `<div class="wk-actual pending">—</div>`;
      } else if (!d.traded) {
        badge = d.today ? `<span class="wk-badge today">Today</span>` : `<span class="wk-badge soon">No trades</span>`;
        if (d.today) cls = "today";
        actualHtml = `<div class="wk-actual pending">${d.today ? "Not yet" : "—"}</div>`;
      } else {
        const met = d.met;
        badge = met ? `<span class="wk-badge done">✓ Done</span>`
          : d.actual >= 0 ? `<span class="wk-badge done">Done</span>`
            : `<span class="wk-badge miss">Done · loss</span>`;
        cls = met ? "met" : "";
        if (d.today) cls += " today";
        actualHtml = `<div class="wk-actual ${d.actual >= 0 ? "pos" : "neg"}">${signed(d.actual)}</div>`;
      }
      // progress bar: actual vs per-day target
      const t = d.target || 1;
      const w = Math.max(0, Math.min(100, Math.round(Math.abs(d.actual) / t * 100)));
      const barShown = d.traded && d.actual !== 0;
      const bar = `<div class="fbar"><span class="${d.actual >= 0 ? "pos" : "neg"}" style="width:${barShown ? w : 0}%"></span></div>`;
      return `<div class="wk-day ${cls}">
        <div class="wk-day-head"><div><div class="wk-dname">${d.day}</div><div class="wk-date">${dd}</div></div>${badge}</div>
        <div class="wk-target">Target ${inr(d.target)}</div>
        ${actualHtml}
        ${bar}
      </div>`;
    }).join("");
  }

  // This month's week-by-week plan with progress bars.
  function renderMonthPlan(mo) {
    const overall = $("monthPlanOverall"), weeksEl = $("monthPlanWeeks");
    if (!overall || !weeksEl) return;
    if (!mo || !mo.monthly_target) {
      overall.innerHTML = `<p class="wk-target">Set a monthly target on the Rules page to see your weekly plan.</p>`;
      weeksEl.innerHTML = "";
      return;
    }
    const achPos = mo.achieved >= 0;
    overall.innerHTML =
      `<div class="wk-oline"><span>${mo.month_label} · progress</span>` +
      `<span><b class="${achPos ? "pos" : "neg"}">${signed(mo.achieved)}</b> / ${inr(mo.monthly_target)} (${mo.pct}%)</span></div>` +
      `<div class="wk-track"><span style="width:${mo.pct}%"></span></div>`;

    weeksEl.innerHTML = (mo.weeks || []).map((w) => {
      let badge, cls = "", actualHtml;
      if (w.future) {
        badge = `<span class="wk-badge soon">Upcoming</span>`;
        cls = "future";
        actualHtml = `<div class="wk-actual pending">—</div>`;
      } else if (!w.traded) {
        badge = w.current ? `<span class="wk-badge today">This week</span>` : `<span class="wk-badge soon">No trades</span>`;
        if (w.current) cls = "today";
        actualHtml = `<div class="wk-actual pending">${w.current ? "Not yet" : "—"}</div>`;
      } else {
        badge = w.met ? `<span class="wk-badge done">✓ Done</span>`
          : w.actual >= 0 ? `<span class="wk-badge done">Done</span>`
            : `<span class="wk-badge miss">Done · loss</span>`;
        cls = w.met ? "met" : "";
        if (w.current) cls += " today";
        actualHtml = `<div class="wk-actual ${w.actual >= 0 ? "pos" : "neg"}">${signed(w.actual)}</div>`;
      }
      const t = w.target || 1;
      const width = Math.max(0, Math.min(100, Math.round(Math.abs(w.actual) / t * 100)));
      const barShown = w.traded && w.actual !== 0;
      const bar = `<div class="fbar"><span class="${w.actual >= 0 ? "pos" : "neg"}" style="width:${barShown ? width : 0}%"></span></div>`;
      return `<div class="wk-day ${cls}">
        <div class="wk-day-head"><div><div class="wk-dname">${w.label}</div><div class="wk-date">${w.range}</div></div>${badge}</div>
        <div class="wk-target">Target ${inr(w.target)}</div>
        ${actualHtml}
        ${bar}
      </div>`;
    }).join("");
  }

  function renderGamePlan(plan) {
    const hd = $("planHeadline"), list = $("planItems"), charts = $("planCharts");
    if (!hd || !list) return;
    renderWeekPlan(plan && plan.week);
    renderMonthPlan(plan && plan.month);
    if (!plan || !plan.items || !plan.items.length) {
      hd.innerHTML = "Import or sync some trades, then save your rules — your personalised plan appears here.";
      list.innerHTML = "";
      if (charts) charts.style.display = "none";
      return;
    }
    hd.innerHTML = plan.headline || "";
    list.innerHTML = plan.items.map((it) => `
      <div class="plan-item ${it.tone || "info"}">
        <span class="pico">${it.icon || "•"}</span>
        <div class="ptxt">
          <div class="ptitle">${esc(it.title || "")}</div>
          <div class="pdetail">${it.detail || ""}</div>
        </div>
      </div>`).join("");

    const c = plan.charts || {};
    if (charts) charts.style.display = "";
    const wd = c.weekday || [];
    planBar("planWeekdayChart", wd.map((w) => w.day), wd.map((w) => Math.round(w.pnl)));
    const inst = c.instruments || [];
    planBar("planInstrumentChart", inst.map((i) => i.symbol), inst.map((i) => Math.round(i.pnl)));
    const ex = c.expiry || {};
    planBar("planExpiryChart", Object.keys(ex), Object.values(ex).map((v) => Math.round(v)));
  }

  function renderRulesForm(rc, tc) {
    $("rulesForm").innerHTML = Object.entries(rc).map(([key, r]) => `
      <div class="rrow">
        <label class="rtoggle"><input type="checkbox" data-rule="${key}" data-f="enabled" ${r.enabled ? "checked" : ""}/> ${r.label}</label>
        <input type="number" data-rule="${key}" data-f="value" value="${r.value}" />
        <span class="runit">${r.unit || ""}</span>
      </div>`).join("");
    $("tWeekly").value = tc.weekly != null ? tc.weekly : "";
    $("tMonthly").value = tc.monthly != null ? tc.monthly : "";
  }

  function renderRuleStats(an) {
    $("ruleStats").innerHTML = [
      tile("Rule compliance", (an.compliance || 0) + "%", `${an.clean_days}/${an.total_days} clean days`, an.compliance >= 70 ? "good" : "bad"),
      tile("Clean days", an.clean_days || 0, "no rules broken", "good"),
      tile("Days with a break", an.broken_days || 0, `of ${an.total_days} days`, an.broken_days ? "bad" : "neutral"),
      tile("Most broken", an.most_broken || "—", "", "bad"),
    ].join("");
  }

  function renderRuleChart(an) {
    const pr = an.per_rule || {};
    const labels = Object.values(pr).map((r) => r.label);
    const data = Object.values(pr).map((r) => r.broken_days);
    const cfg = {
      type: "bar",
      data: { labels, datasets: [{ label: "Days broken", data, backgroundColor: "#ef5350" }] },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => c.raw + " days" } } },
        scales: {
          x: { ticks: { color: "#8b95a7", precision: 0 }, grid: { color: "rgba(128,128,128,.18)" } },
          y: { ticks: { color: "#8b95a7" }, grid: { display: false } },
        },
      },
    };
    if (ruleChart) ruleChart.destroy();
    ruleChart = new Chart($("ruleChart"), cfg);
  }

  function rulesSaveMsg(msg, err) {
    const s = $("rulesSaveStatus");
    if (!msg) { s.classList.add("hidden"); return; }
    s.className = "jnote" + (err ? " err" : "");
    s.textContent = msg;
    s.classList.remove("hidden");
  }

  // ---------------------------------------------------------------- Insights
  let weekdayChart = null;
  let insightsPeriod = "3m";
  let insightsSymbol = "all";
  let symbolSegBuilt = false;
  let insStart = "", insEnd = "";     // custom date range (overrides period when both set)

  const WD_FULL = {
    Mon: "Mondays", Tue: "Tuesdays", Wed: "Wednesdays",
    Thu: "Thursdays", Fri: "Fridays", Sat: "Saturdays", Sun: "Sundays"
  };

  document.querySelectorAll("#periodSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#periodSeg button").forEach((x) => x.classList.toggle("active", x === b));
      insightsPeriod = b.dataset.p;
      clearRangeInputs();          // picking a preset clears any custom range
      loadInsights();
    }));

  function clearRangeInputs() {
    insStart = ""; insEnd = "";
    // leave pickers empty so it's clear no custom range is active (the preset
    // buttons drive the view until the user actually picks both dates)
    if ($("insFrom")) $("insFrom").value = "";
    if ($("insTo")) $("insTo").value = "";
  }
  clearRangeInputs();
  function onRangeChange() {
    insStart = $("insFrom").value || "";
    insEnd = $("insTo").value || "";
    if (insStart && insEnd) {    // custom range active → deactivate preset buttons
      document.querySelectorAll("#periodSeg button").forEach((x) => x.classList.remove("active"));
      loadInsights();
    }
  }
  if ($("insFrom")) $("insFrom").addEventListener("change", onRangeChange);
  if ($("insTo")) $("insTo").addEventListener("change", onRangeChange);
  if ($("insRangeClear")) $("insRangeClear").addEventListener("click", () => {
    clearRangeInputs();
    const def = [...document.querySelectorAll("#periodSeg button")].find((b) => b.dataset.p === insightsPeriod)
      || document.querySelector("#periodSeg button");
    document.querySelectorAll("#periodSeg button").forEach((x) => x.classList.toggle("active", x === def));
    insightsPeriod = def ? def.dataset.p : "3m";
    loadInsights();
  });

  function buildSymbolSeg(instruments) {
    const seg = $("symbolSeg");
    const btns = ['<button data-s="all" class="active">All</button>']
      .concat((instruments || []).map((s) => `<button data-s="${esc(s)}">${esc(s)}</button>`));
    seg.innerHTML = btns.join("");
    seg.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
        insightsSymbol = b.dataset.s;
        loadInsights();
      }));
    // keep the active button in sync with current selection
    seg.querySelectorAll("button").forEach((x) =>
      x.classList.toggle("active", x.dataset.s === insightsSymbol));
    symbolSegBuilt = true;
  }

  async function loadInsights() {
    showLoading(true);
    try {
      let url = `/api/journal/analytics?period=${insightsPeriod}&symbol=${encodeURIComponent(insightsSymbol)}`;
      const customRange = insStart && insEnd;
      if (customRange) url += `&start=${insStart}&end=${insEnd}`;
      const d = await (await fetch(url)).json();
      if (!symbolSegBuilt) buildSymbolSeg(d.instruments_available);
      const bw = d.best_weekday, ww = d.worst_weekday;
      const scopeTxt = (insightsSymbol === "all" ? "" : ` · <b style="color:var(--accent)">${esc(insightsSymbol)}</b> only`) +
        (customRange ? ` · <b style="color:var(--accent)">${insStart} → ${insEnd}</b>` : "");
      $("insightSummary").innerHTML =
        `<b>${d.days}</b> trading days · total <b>${signed(d.total)}</b>` + scopeTxt +
        (bw ? ` · best weekday <b style="color:var(--put)">${bw.day} (${signed(bw.pnl)})</b>` : "") +
        // only call the low day "worst" when it's an actual loss; else it's just the weakest
        (ww && ww !== bw
          ? (ww.pnl < 0
            ? ` · worst weekday <b style="color:var(--call)">${ww.day} (${signed(ww.pnl)})</b>`
            : ` · weakest weekday <b style="color:var(--muted)">${ww.day} (${signed(ww.pnl)})</b>`)
          : "");
      renderNarrative(d.narrative, d.days);
      renderWeekdayChart(d.weekday);
      renderWeekdayCallout(d.weekday);
      renderWeekdayTable(d.weekday, d.per_day_target);
      renderExpiry(d.expiry);
      renderInstruments(d.instruments);
      renderDayList("topProfit", d.top_profit_days);
      renderDayList("topLoss", d.top_loss_days);
      renderSetup(d.setup);
    } catch (e) { $("insightSummary").textContent = "Could not load insights: " + e.message; }
    finally { showLoading(false); }
  }

  // Plain-English, auto-generated insights from the backend.
  function renderNarrative(lines, days) {
    const el = $("insightNarrative");
    if (!el) return;
    if (!days) {
      el.innerHTML = `<div class="insight-item">📭 No trades in this range yet — pick a wider window.</div>`;
      return;
    }
    el.innerHTML = (lines || []).map((t) => `<div class="insight-item">◆ ${esc(t)}</div>`).join("");
  }

  // Plain-language note on the losing-most weekday.
  function renderWeekdayCallout(wd) {
    const el = $("weekdayCallout");
    if (!el) return;
    const losers = (wd || []).filter((w) => w.pnl < 0).sort((a, b) => a.pnl - b.pnl);
    if (!losers.length) { el.classList.add("hidden"); return; }
    const w = losers[0];
    el.className = "callout bad";
    el.innerHTML = `📉 You lose the most on <b>${WD_FULL[w.day] || w.day}</b> — ` +
      `<b>${signed(w.pnl)}</b> over ${w.days} day${w.days === 1 ? "" : "s"} (${w.win_rate}% win rate). ` +
      `Avg ${signed(w.avg)}/day.`;
    el.classList.remove("hidden");
  }

  // Weekday cards (Mon–Fri), each with a horizontal flowing P&L bar, for the selection.
  const WD_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  // Classify a weekday by its average P&L vs the per-day target (from Rules):
  //   loss (avg < 0) → "bad", on-target (avg >= target) → "good",
  //   profit-but-below-target → "ok". A profit is never flagged red/"worst".
  function weekdayStatus(w, target) {
    if (w.avg < 0) return "bad";
    if (target > 0 && w.avg >= target) return "good";
    return w.avg > 0 ? "ok" : "flat";
  }
  function weekdayBadge(status) {
    return {
      good: `<span class="wd-tag best">✓ On target</span>`,
      ok: `<span class="wd-tag ok">Below target</span>`,
      bad: `<span class="wd-tag worst">Loss</span>`,
      flat: "",
    }[status] || "";
  }

  function renderWeekdayTable(wd, perDayTarget) {
    const target = Number(perDayTarget) || 0;
    const bw = $("weekdayBestWorst"), wrap = $("weekdayCards");
    if (!wrap) return;
    const byDay = {};
    (wd || []).forEach((w) => { byDay[w.day] = w; });
    const full = WD_ORDER.map((day) => byDay[day] || { day, pnl: 0, days: 0, avg: 0, win_rate: 0 });
    const traded = full.filter((w) => w.days > 0);

    if (!traded.length) {
      if (bw) bw.innerHTML = "";
      wrap.innerHTML = `<p class="empty" style="grid-column:1/-1">No trades in this period.</p>`;
      return;
    }
    const best = traded.reduce((a, b) => (b.pnl > a.pnl ? b : a));
    const low = traded.reduce((a, b) => (b.pnl < a.pnl ? b : a));
    const lowIsLoss = low.pnl < 0;           // only a real loss gets the red "Worst" card
    const maxAbs = Math.max(1, ...traded.map((w) => Math.abs(w.pnl)));

    if (bw) {
      const tgtNote = target > 0 ? ` · target ${inr(target)}/day` : "";
      const lowCard = lowIsLoss
        ? `<div class="wd-bw bad"><div class="k">📉 Worst day <small>(losing)</small></div>` +
        `<div class="d">${WD_FULL[low.day] || low.day}</div>` +
        `<div class="p neg"><b>${signed(low.pnl)}</b> over ${low.days} day${low.days === 1 ? "" : "s"} · ${low.win_rate}% win</div></div>`
        : `<div class="wd-bw"><div class="k">📉 Weakest day <small>(still green)</small></div>` +
        `<div class="d">${WD_FULL[low.day] || low.day}</div>` +
        `<div class="p pos"><b>${signed(low.pnl)}</b> over ${low.days} day${low.days === 1 ? "" : "s"} · ${low.win_rate}% win</div></div>`;
      bw.innerHTML = [
        `<div class="wd-bw good"><div class="k">📈 Best day${tgtNote}</div><div class="d">${WD_FULL[best.day] || best.day}</div>` +
        `<div class="p pos"><b>${signed(best.pnl)}</b> over ${best.days} day${best.days === 1 ? "" : "s"} · ${best.win_rate}% win</div></div>`,
        lowCard,
      ].join("");
    }

    wrap.innerHTML = full.map((w) => {
      const label = (WD_FULL[w.day] || w.day).replace(/s$/, "");
      if (!w.days) {
        return `<div class="wd-card">
          <div class="wd-head"><span class="wd-name">${WD_FULL[w.day] || label}</span></div>
          <div class="wd-val" style="color:var(--muted)">—</div>
          <div class="fbar"><span style="width:0"></span></div>
          <div class="wd-sub">No trades</div></div>`;
      }
      const status = weekdayStatus(w, target);
      const pos = w.pnl >= 0;
      const width = Math.max(4, Math.round((Math.abs(w.pnl) / maxAbs) * 100));
      const cardCls = status === "bad" ? "worst" : status === "good" ? "best" : "";
      return `<div class="wd-card ${cardCls}">
        <div class="wd-head"><span class="wd-name">${WD_FULL[w.day] || label}</span>${weekdayBadge(status)}</div>
        <div class="wd-val ${pos ? "pos" : "neg"}">${signed(w.pnl)}</div>
        <div class="fbar"><span class="${pos ? "pos" : "neg"}" style="width:${width}%"></span></div>
        <div class="wd-sub">${w.days} day${w.days === 1 ? "" : "s"} · ${w.win_rate}% win · avg ${signed(w.avg)}</div>
      </div>`;
    }).join("");
  }

  function renderExpiry(ex) {
    if (!ex) return;
    const g = (label, x, cls) =>
      tile(label, signed(x.pnl), `${x.trades} trades · ${x.win_rate}% win · avg ${signed(x.avg)}`,
        x.trades ? cls : "neutral");
    const edPos = ex.expiry_day.pnl >= 0;
    $("expiryTiles").innerHTML = [
      g("Expiry day", ex.expiry_day, edPos ? "good" : "bad"),
      g("Other days", ex.other_days, ex.other_days.pnl >= 0 ? "good" : "bad"),
    ].join("");
    const el = $("expiryCallout");
    const ed = ex.expiry_day, od = ex.other_days;
    if (ed.trades && od.trades) {
      const edAvg = ed.avg, odAvg = od.avg;
      if (edAvg < 0 && edAvg < odAvg) {
        el.className = "callout bad";
        el.innerHTML = `⚠️ Expiry days are your weak spot: avg <b>${signed(edAvg)}</b>/trade vs ` +
          `<b>${signed(odAvg)}</b> on other days. Consider trading smaller (or sitting out) on expiry.`;
      } else if (edAvg > odAvg) {
        el.className = "callout good";
        el.innerHTML = `✅ You actually do better on expiry days: avg <b>${signed(edAvg)}</b>/trade vs ` +
          `<b>${signed(odAvg)}</b> on other days.`;
      } else {
        el.className = "callout";
        el.innerHTML = `Expiry vs other days are similar (avg ${signed(edAvg)} vs ${signed(odAvg)} per trade).`;
      }
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  function renderInstruments(rows) {
    const wrap = $("instrumentCards");
    if (!wrap) return;
    const list = rows || [];
    if (!list.length) {
      wrap.innerHTML = `<p class="empty" style="grid-column:1/-1">No trades in this period.</p>`;
      return;
    }
    const maxAbs = Math.max(1, ...list.map((r) => Math.abs(r.pnl)));
    wrap.innerHTML = list.map((r) => {
      const pos = r.pnl >= 0;
      const width = Math.max(4, Math.round((Math.abs(r.pnl) / maxAbs) * 100));
      const sel = r.symbol === insightsSymbol ? " sel" : "";
      return `<div class="inst-card${sel}" data-s="${esc(r.symbol)}">
        <div class="inst-head"><span class="inst-name">${esc(r.symbol)}</span>
          <span class="inst-sub">${fmt(r.trades)} trades · ${r.win_rate}% win</span></div>
        <div class="inst-val ${pos ? "pos" : "neg"}">${signed(r.pnl)}</div>
        <div class="fbar"><span class="${pos ? "pos" : "neg"}" style="width:${width}%"></span></div>
      </div>`;
    }).join("");
  }
  $("instrumentCards").addEventListener("click", (e) => {
    const card = e.target.closest(".inst-card[data-s]");
    if (!card) return;
    const s = card.dataset.s;
    insightsSymbol = insightsSymbol === s ? "all" : s;   // click again to clear
    document.querySelectorAll("#symbolSeg button").forEach((x) =>
      x.classList.toggle("active", x.dataset.s === insightsSymbol));
    loadInsights();
  });

  function renderWeekdayChart(wd) {
    const data = {
      labels: wd.map((w) => w.day),
      datasets: [{
        label: "P&L", data: wd.map((w) => w.pnl),
        backgroundColor: wd.map((w) => (w.pnl >= 0 ? "#26a69a" : "#ef5350"))
      }],
    };
    const opts = {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => signed(c.raw), afterLabel: (c) => {
              const w = wd[c.dataIndex]; return `${w.days} days · ${w.win_rate}% win · avg ${signed(w.avg)}`;
            }
          }
        }
      },
      scales: {
        x: { ticks: { color: "#8b95a7", font: { weight: "600" } }, grid: { display: false } },
        y: { ticks: { color: "#8b95a7" }, grid: { color: "rgba(128,128,128,.18)" } }
      },
    };
    if (weekdayChart) weekdayChart.destroy();
    weekdayChart = new Chart($("weekdayChart"), { type: "bar", data, options: opts });
  }

  function renderDayList(id, days) {
    $(id).innerHTML = (days && days.length)
      ? days.map((d) => `<div class="dayrow"><span>${d.date}</span><b class="${d.realized >= 0 ? "pos" : "neg"}">${signed(d.realized)}</b></div>`).join("")
      : `<p class="empty">No data in this period.</p>`;
  }

  function renderSetup(s) {
    const card = (label, g, cls) =>
      tile(label, signed(g.pnl), `${g.trades} trades · ${g.win_rate}% win`, cls);
    $("setupTiles").innerHTML = [
      card("On plan / setup", s.on_plan, s.on_plan.pnl >= 0 ? "good" : "bad"),
      card("Off plan", s.off_plan, s.off_plan.pnl >= 0 ? "good" : "bad"),
      card("Untagged", s.untagged, "neutral"),
    ].join("");
    const tb = $("setupTable").querySelector("tbody");
    tb.innerHTML = (s.by_setup && s.by_setup.length)
      ? s.by_setup.map((r) => `<tr><td style="text-align:left">${r.setup}</td><td>${r.trades}</td>
          <td>${r.win_rate}%</td><td class="${r.pnl >= 0 ? "pos" : "neg"}"><b>${signed(r.pnl)}</b></td></tr>`).join("")
      : `<tr><td colspan="4" style="text-align:center;color:var(--muted)">No tagged setups yet.</td></tr>`;
  }

  async function saveRules() {
    const rc = JSON.parse(JSON.stringify(rulesCfg || {}));
    document.querySelectorAll("#rulesForm [data-rule]").forEach((el) => {
      const k = el.dataset.rule, f = el.dataset.f;
      if (!rc[k]) rc[k] = {};
      rc[k][f] = f === "enabled" ? el.checked : Number(el.value);
    });
    const targets = { weekly: Number($("tWeekly").value), monthly: Number($("tMonthly").value) };
    try {
      await fetch("/api/journal/rules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rc) });
      await fetch("/api/journal/targets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(targets) });
      rulesSaveMsg("✅ Saved. Your rules and targets are updated.");
      loadRules();
    } catch (e) { rulesSaveMsg("Save failed: " + e.message, true); }
  }

  // ---------------------------------------------------------------- Trade Mode
  const TM_REFRESH_SEC = 10;
  const TM_SEG_TABS = new Set(["overview", "positions", "orders", "trades", "holdings"]);
  let tmData = null, tmReports = null, tmTab = "overview", tmSeg = "all", tmBound = false, tmBusy = false;
  let tmViewData = { positions: [], orders: [], trades: [], holdings: [] };
  let tmCountdown = TM_REFRESH_SEC, tmRefreshCount = 0;
  function _tmItemSeg(row, isHolding) {
    if (!row) return "equity";
    const sym = String(row.symbol || "").toUpperCase().trim();
    const ex = String(row.exchange_segment || row.exchange || "").toUpperCase().trim();
    const fnoSym = /\b(CE|PE|CALL|PUT|FUT)\b/.test(sym) ||
      /\d+(?:\.\d+)?-?(?:CE|PE|FUT)$/.test(sym) ||
      /(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d*FUT$/.test(sym) ||
      /-FUT$/.test(sym) ||
      /(?:^|[:\-])(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|BANKEX)\b/.test(sym);
    if (isHolding) {
      return (/FNO|NFO|BFO|MCX|CDS/.test(ex) || fnoSym) ? "fno" : "equity";
    }
    if (row.drv_option_type) return "fno";
    if (/FNO|NFO|BFO|MCX|CDS|DERIV/.test(ex) || ex === "11" || ex === "12" || ex === "20") return "fno";
    if (/(?:_EQ$|^EQ$|NSE_EQ|BSE_EQ)/.test(ex) || ex === "10" || /-EQ$|-BE$|-BL$/.test(sym)) return "equity";
    return fnoSym ? "fno" : "equity";
  }
  function _tmApplySeg(rows, isHolding) {
    if (!rows || !rows.length || tmSeg === "all") return rows || [];
    return rows.filter((r) => _tmItemSeg(r, isHolding) === tmSeg);
  }
  function _tmSyncSegUi() {
    const sf = $("tmodeSegFilter");
    if (!sf) return;
    sf.classList.toggle("hidden", !TM_SEG_TABS.has(tmTab));
    sf.querySelectorAll("[data-tmseg]").forEach((b) => b.classList.toggle("active", b.dataset.tmseg === tmSeg));
  }
  function _tmSetTimer(txt, cls) {
    const el = $("tmodeCountdown");
    if (el) {
      el.textContent = txt + ` · #${tmRefreshCount}`;
      el.className = "tm-auto-timer" + (cls ? " " + cls : "");
    }
    const wrap = $("tmodeAutoWrap");
    if (wrap) wrap.classList.toggle("off", cls === "paused");
  }
  function tmodeMsg(msg, err) {
    const el = $("tmodeNote"); if (!el) return;
    if (!msg) { el.classList.add("hidden"); return; }
    el.className = "jnote" + (err ? " err" : "");
    el.textContent = msg;
    el.classList.remove("hidden");
  }
  function setTradeSubTab(tab) {
    tmTab = tab || "overview";
    document.querySelectorAll("#navTrade [data-tmtab]").forEach((x) => x.classList.toggle("active", x.dataset.tmtab === tmTab));
    document.body.classList.remove("sb-open");
    if (tmTab === "reports" && !tmReports) loadTradeReports();
    if (tmData && tmData.supported) renderTradeMode();
    else { _tmSyncSegUi(); renderTradeBody(); }
  }
  function bindTradeModeOnce() {
    if (tmBound) return; tmBound = true;
    if ($("tmodeSegFilter")) $("tmodeSegFilter").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tmseg]"); if (!b) return;
      tmSeg = b.dataset.tmseg || "all";
      _tmSyncSegUi();
      if (tmData && tmData.supported) renderTradeMode();
    });
    if ($("tmodeCloseBtn")) $("tmodeCloseBtn").addEventListener("click", () => switchView(lastNonTradeView || "home"));
    if ($("tmodeSideExit")) $("tmodeSideExit").addEventListener("click", () => switchView(lastNonTradeView || "home"));
    if ($("tmodeRefresh")) $("tmodeRefresh").addEventListener("click", () => loadTradeMode({ forceReports: tmTab === "reports" }));
    if ($("tmodeAuto")) $("tmodeAuto").addEventListener("change", () => {
      tmCountdown = TM_REFRESH_SEC;
      if ($("tmodeAuto").checked) loadTradeMode({ silent: true });
      else _tmSetTimer("⏸ off", "paused");
    });
    if ($("tmodeExitAll")) $("tmodeExitAll").addEventListener("click", async () => {
      const cnt = tmData?.counts || {};
      if ((cnt.open_positions || 0) === 0 && (cnt.pending_orders || 0) === 0) {
        tmodeMsg("No open positions or pending orders right now.");
        return;
      }
      if (!confirm("Exit ALL open positions right now on your live broker account?")) return;
      try {
        const r = await fetch("/api/trademode/exit-position", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || "Exit all failed");
        tmodeMsg("✅ Sent Exit All request to broker.");
        loadTradeMode();
      } catch (e) { tmodeMsg("Exit all failed: " + e.message, true); }
    });
    if ($("navTrade")) $("navTrade").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tmtab]"); if (!b) return;
      setTradeSubTab(b.dataset.tmtab);
    });
    if ($("tmodeBody")) $("tmodeBody").addEventListener("click", async (e) => {
      const jump = e.target.closest("[data-jump-tmtab]");
      if (jump) { setTradeSubTab(jump.dataset.jumpTmtab); return; }
      const exBtn = e.target.closest("[data-exit-pos]");
      if (exBtn) {
        const pid = exBtn.dataset.exitPos, sym = exBtn.dataset.sym || pid;
        if (!confirm(`Exit open position ${sym}?`)) return;
        try {
          const r = await fetch("/api/trademode/exit-position", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: pid, symbol: sym }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Exit failed");
          tmodeMsg(`✅ Exit placed for ${sym}.`);
          loadTradeMode();
        } catch (err) { tmodeMsg("Exit failed: " + err.message, true); }
        return;
      }
      const revBtn = e.target.closest("[data-rev-pos]");
      if (revBtn) {
        const pid = revBtn.dataset.revPos, sym = revBtn.dataset.sym || pid;
        if (!confirm(`Reverse open position ${sym}? (Closes current qty and opens opposite side)`)) return;
        try {
          const r = await fetch("/api/trademode/reverse-position", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: pid, symbol: sym }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Reverse failed");
          tmodeMsg(`✅ Reversed ${sym} (${d.result?.side || ""} ${d.result?.qty || ""}).`);
          loadTradeMode();
        } catch (err) { tmodeMsg("Reverse failed: " + err.message, true); }
        return;
      }
      const cBtn = e.target.closest("[data-cancel-ord]");
      if (cBtn) {
        const oid = cBtn.dataset.cancelOrd, sym = cBtn.dataset.sym || oid;
        if (!confirm(`Cancel pending order ${sym} (#${oid})?`)) return;
        try {
          const r = await fetch("/api/trademode/cancel-order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: oid }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Cancel failed");
          tmodeMsg(`✅ Cancelled order #${oid}.`);
          loadTradeMode();
        } catch (err) { tmodeMsg("Cancel failed: " + err.message, true); }
        return;
      }
      const iPosRow = e.target.closest("[data-inspect-pos]");
      if (iPosRow) {
        const idx = Number(iPosRow.dataset.inspectPos);
        const pos = (tmViewData.positions || [])[idx];
        if (pos) openTmInspect({ kind: "position", item: pos });
        return;
      }
      const iOrdRow = e.target.closest("[data-inspect-ord]");
      if (iOrdRow) {
        const idx = Number(iOrdRow.dataset.inspectOrd);
        const ord = (tmViewData.orders || [])[idx];
        if (ord) openTmInspect({ kind: "order", item: ord });
        return;
      }
      const iTrdRow = e.target.closest("[data-inspect-trd]");
      if (iTrdRow) {
        const idx = Number(iTrdRow.dataset.inspectTrd);
        const trd = (tmViewData.trades || [])[idx];
        if (trd) openTmInspect({ kind: "trade", item: trd });
        return;
      }
      const ksBtn = e.target.closest("[data-ks-set]");
      if (ksBtn) {
        const st = ksBtn.dataset.ksSet;
        if (!confirm(`${st === "ACTIVATE" ? "ACTIVATE" : "DEACTIVATE"} Dhan Kill Switch? (All positions & pending orders must be closed first when activating.)`)) return;
        try {
          const r = await fetch("/api/trademode/killswitch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: st }) });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Kill switch update failed");
          tmodeMsg(`✅ Kill Switch → ${st}.`);
          loadTradeMode();
        } catch (err) { tmodeMsg("Kill Switch error: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmPnlExitSave" || e.target.id === "tmPnlExitClear") {
        const active = e.target.id === "tmPnlExitSave";
        const pv = Number($("tmProfVal")?.value || 0), lv = Number($("tmLossVal")?.value || 0);
        const ks = !!($("tmExitKs")?.checked);
        try {
          const r = await fetch("/api/trademode/pnl-exit", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profit_value: pv, loss_value: lv, enable_kill_switch: ks, active }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "P&L exit update failed");
          tmodeMsg(active ? "✅ P&L Auto-Exit rule updated." : "✅ P&L Auto-Exit disabled.");
          loadTradeMode();
        } catch (err) { tmodeMsg("P&L Exit error: " + err.message, true); }
      }
    });
  }
  if ($("tradeModeBtn")) $("tradeModeBtn").addEventListener("click", () => {
    const inTm = $("viewTradeMode") && !$("viewTradeMode").classList.contains("hidden");
    switchView(inTm ? (lastNonTradeView || "home") : "trademode");
  });
  async function loadTradeReports() {
    try {
      const r = await fetch("/api/trademode/reports");
      if (r.ok) { tmReports = await r.json(); renderTradeBody(); }
    } catch (e) { /* keep silent */ }
  }
  async function loadTradeMode(opts) {
    bindTradeModeOnce();
    const silent = opts && opts.silent;
    if (tmBusy && silent) return;
    tmBusy = true;
    const btn = $("tmodeRefresh"), old = btn ? btn.textContent : "";
    if (!silent && btn) { btn.disabled = true; btn.textContent = "Loading…"; }
    try {
      const r = await fetch("/api/trademode");
      const d = await r.json().catch(() => ({}));
      if (r.status === 401) {
        if ($("tmodeContent")) $("tmodeContent").classList.add("hidden");
        if ($("tmodeEmpty")) {
          const msg = d.detail && d.detail !== "login_required" ? d.detail : "Log in / connect your broker account first to open live Trade Mode.";
          $("tmodeEmpty").innerHTML = `<div class="jnote err" style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap"><span>${esc(msg)}</span><button class="jbtn primary sm" type="button" onclick="document.getElementById('profileReconnect')?.click()">🔄 Reconnect Broker</button></div>`;
          $("tmodeEmpty").classList.remove("hidden");
        }
        return;
      }
      if (!r.ok) throw new Error(d.detail || "Failed to load Trade Mode");
      tmData = d;
      tmRefreshCount += 1;
      tmCountdown = TM_REFRESH_SEC;
      _tmSetTimer("⏱ " + tmCountdown + "s");
      if (opts && opts.forceReports) tmReports = null;
      renderTradeMode();
      if (tmTab === "reports" && d.supported && !tmReports) loadTradeReports();
    } catch (e) {
      if (!silent) tmodeMsg("Could not load Trade Mode: " + e.message, true);
    } finally {
      tmBusy = false;
      if (!silent && btn) { btn.disabled = false; btn.textContent = old; }
    }
  }
  function renderTradeMode() {
    const d = tmData; if (!d) return;
    if ($("tmodeBrokerBadge")) $("tmodeBrokerBadge").textContent = (d.source || "").toUpperCase();
    if ($("tmodeDate")) $("tmodeDate").textContent = d.today ? `${d.today} · ${d.broker_label || ""}` : "";
    if (!d.supported) {
      if ($("tmodeContent")) $("tmodeContent").classList.add("hidden");
      if ($("tmodeExitAll")) $("tmodeExitAll").classList.add("hidden");
      if ($("tmodeEmpty")) {
        $("tmodeEmpty").innerHTML = `<div class="jnote">⚡ <b>${esc(d.note || "Trade Mode is currently supported for Fyers and Dhan.")}</b> Other brokers will be added later. Switch to <b>Fyers</b> or <b>Dhan</b> from the profile menu on the top-right to use live Trade Mode.</div>`;
        $("tmodeEmpty").classList.remove("hidden");
      }
      return;
    }
    if ($("tmodeEmpty")) $("tmodeEmpty").classList.add("hidden");
    if ($("tmodeContent")) $("tmodeContent").classList.remove("hidden");
    const f = d.funds || {}, ctrl = d.controls || {};
    const useFilter = tmSeg !== "all" && TM_SEG_TABS.has(tmTab);
    const segPositions = useFilter ? _tmApplySeg(d.positions || [], false) : (d.positions || []);
    const segOrders = useFilter ? _tmApplySeg(d.orders || [], false) : (d.orders || []);
    const segTrades = useFilter ? _tmApplySeg(d.trades || [], false) : (d.trades || []);
    const segHoldings = useFilter ? _tmApplySeg(d.holdings || [], true) : (d.holdings || []);
    tmViewData = { positions: segPositions, orders: segOrders, trades: segTrades, holdings: segHoldings };

    const p = useFilter ? {
      realized: segPositions.reduce((s, r) => s + Number(r.realized || 0), 0),
      unrealized: segPositions.reduce((s, r) => s + Number(r.unrealized || 0), 0),
      total: segPositions.reduce((s, r) => s + Number(r.pnl || 0), 0),
      holdings_pnl: segHoldings.reduce((s, h) => s + Number(h.pnl || 0), 0),
    } : (d.pnl || {});
    const c = useFilter ? {
      open_positions: segPositions.filter((r) => r.is_open).length,
      total_positions: segPositions.length,
      orders: segOrders.length,
      pending_orders: segOrders.filter((o) => o.is_pending).length,
      trades: segTrades.length,
      holdings: segHoldings.length,
    } : (d.counts || {});
    const segTag = useFilter ? (tmSeg === "equity" ? " · Equity" : " · F&O") : "";
    const hasOpen = (c.open_positions > 0 || c.pending_orders > 0);
    if ($("tmodeExitAll")) {
      $("tmodeExitAll").classList.toggle("hidden", !ctrl.can_exit_all);
      $("tmodeExitAll").classList.toggle("idle", !hasOpen);
    }
    const floatOpen = $("tmFloatWidget") && !$("tmFloatWidget").classList.contains("hidden");
    const floatActBtn = `<button id="tmodeKpiFloatBtn" class="tmode-kpi-float${floatOpen ? " active" : ""}" type="button" title="Pop out movable floating P&amp;L box">⧉ Float</button>`;
    const exitCardBtn = ctrl.can_exit_all
      ? `<button id="tmodeKpiExitAll" class="jbtn danger sm tmode-kpi-exit${hasOpen ? "" : " idle"}" type="button" onclick="document.getElementById('tmodeExitAll')?.click()" title="Close all open positions">⚡ Exit All</button>`
      : "";
    const kpiExtra = `<div class="tmode-kpi-act">${floatActBtn}${exitCardBtn}</div>`;
    const kcard = (l, v, cls, sub, extra = "") =>
      `<div class="kpi"><div class="kpi-main"><div class="kpi-l">${l}</div><div class="kpi-v ${cls || ""}">${v}</div><div class="kpi-s">${sub}</div></div>${extra}</div>`;
    $("tmodeKpis").innerHTML =
      kcard(`Today Total P&amp;L${segTag}`, signed(p.total || 0), (p.total || 0) >= 0 ? "pos" : "neg", `realized ${signed(p.realized || 0)}`, kpiExtra) +
      kcard(`Realized P&amp;L${segTag}`, signed(p.realized || 0), (p.realized || 0) >= 0 ? "pos" : "neg", "booked today") +
      kcard(`Unrealized MTM${segTag}`, signed(p.unrealized || 0), (p.unrealized || 0) >= 0 ? "pos" : "neg", `${c.open_positions || 0} open positions`) +
      kcard("Available Funds", inr(f.available || 0), "pos", `utilized ${inr(f.utilized || 0)}`) +
      kcard("Total Balance", inr(f.total || 0), "", `opening ${inr(f.sod || 0)}`) +
    kcard(`Today Activity${segTag}`, `${c.trades || 0} fills`, "", `${c.orders || 0} orders (${c.pending_orders || 0} pending)`);
    if ($("tmodeKpiFloatBtn")) $("tmodeKpiFloatBtn").addEventListener("click", (e) => {
      e.stopPropagation();
      toggleTmFloatWidget();
    });
    const totC = d.counts || {};
    if ($("tmSidePos")) $("tmSidePos").textContent = totC.total_positions || 0;
    if ($("tmSideOrd")) $("tmSideOrd").textContent = totC.orders || 0;
    if ($("tmSideTrd")) $("tmSideTrd").textContent = totC.trades || 0;
    if ($("tmSideHld")) $("tmSideHld").textContent = totC.holdings || 0;
    document.querySelectorAll("#navTrade [data-tmtab]").forEach((x) => x.classList.toggle("active", x.dataset.tmtab === tmTab));
    _tmSyncSegUi();
    renderTradeBody();
    if (_tmiState && $("tmInspectModal") && !$("tmInspectModal").classList.contains("hidden")) _tmiSyncLive();
    if ($("tmFloatWidget") && !$("tmFloatWidget").classList.contains("hidden")) _renderTmFloatWidget();
  }
  function _tmPositionsTable(rows, ctrl, maxH) {
    return rows.length
      ? `<div class="table-scroll" style="max-height:${maxH || 480}px"><table class="data"><thead><tr>
          <th style="text-align:left">Symbol</th><th>Side</th><th>Product</th><th>Net Qty</th><th>Capital</th><th>Avg</th><th>LTP</th><th>Realized</th><th>Unrealized</th><th>P&amp;L / ROI%</th><th></th>
        </tr></thead><tbody>` + rows.map((r, idx) => {
        const sc = r.side === "LONG" ? "pos" : r.side === "SHORT" ? "neg" : "";
        const pc = r.pnl >= 0 ? "pos" : "neg";
        const cap = r.capital_used != null ? r.capital_used : Math.abs(r.net_qty || 0) * (r.avg_price || 0);
        const pct = r.pnl_pct != null ? r.pnl_pct : (cap > 0 ? (r.pnl / cap * 100) : 0);
        const pctStr = cap > 0 ? ` <small>(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</small>` : "";
        const revBtn = (r.is_open && ctrl.can_reverse_position) ? `<button class="jbtn sm" data-rev-pos="${esc(r.id)}" data-sym="${esc(r.symbol)}" title="Reverse position">⇄ Rev</button>` : "";
        const exBtn = (r.is_open && ctrl.can_exit_single) ? `<button class="jbtn danger sm" data-exit-pos="${esc(r.id)}" data-sym="${esc(r.symbol)}">Exit</button>` : "";
        const act = [revBtn, exBtn].filter(Boolean).join(" ");
        return `<tr class="tm-click-row" data-inspect-pos="${idx}" title="Click for chart, Capital/ROI, Target/SL/TL &amp; Reverse"><td style="text-align:left"><b>${esc(r.symbol)}</b></td><td class="${sc}">${esc(r.side)}</td><td>${esc(r.product)}</td>` +
          `<td>${fmt(r.net_qty)}</td><td>${inr(cap)}</td><td>₹${fmt(r.avg_price, 2)}</td><td>₹${fmt(r.ltp, 2)}</td>` +
          `<td class="${r.realized >= 0 ? "pos" : "neg"}">${signed(r.realized)}</td>` +
          `<td class="${r.unrealized >= 0 ? "pos" : "neg"}">${signed(r.unrealized)}</td>` +
          `<td class="${pc}"><b>${signed(r.pnl)}</b>${pctStr}</td><td style="white-space:nowrap">${act}</td></tr>`;
      }).join("") + `</tbody></table></div>`
      : `<p class="empty">No positions today.</p>`;
  }
  function _tmOrdersTable(rows, ctrl, maxH) {
    return rows.length
      ? `<div class="table-scroll" style="max-height:${maxH || 480}px"><table class="data"><thead><tr>
          <th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Price</th><th>Est. Cap</th><th>Status</th><th style="text-align:left">Note</th><th></th>
        </tr></thead><tbody>` + rows.map((o, idx) => {
        const sc = o.side === "BUY" ? "pos" : "neg";
        const stCls = /TRADED|COMPLETE|FILLED/i.test(o.status) ? "bull" : /REJECT|CANCEL|EXPIRED/i.test(o.status) ? "bear" : "flat";
        const act = (o.is_pending && ctrl.can_cancel_order) ? `<button class="jbtn danger sm" data-cancel-ord="${esc(o.id)}" data-sym="${esc(o.symbol)}">Cancel</button>` : "";
        const cap = o.capital_used || 0;
        return `<tr class="tm-click-row" data-inspect-ord="${idx}" title="Click to inspect order, view chart &amp; capital/margin details"><td style="text-align:left">${esc(o.time)}</td><td style="text-align:left"><b>${esc(o.symbol)}</b></td><td class="${sc}">${esc(o.side)}</td>` +
          `<td>${esc(o.order_type)} · ${esc(o.product)}</td><td>${o.filled_qty}/${o.qty}</td><td>₹${fmt(o.price, 2)}</td>` +
          `<td>${cap > 0 ? inr(cap) : "—"}</td>` +
          `<td><span class="sig ${stCls}">${esc(o.status)}</span></td><td style="text-align:left">${esc(o.message || "—")}</td><td>${act}</td></tr>`;
      }).join("") + `</tbody></table></div>`
      : `<p class="empty">No orders placed today.</p>`;
  }
  function _tmTradesTable(rows, maxH) {
    return rows.length
      ? `<div class="table-scroll" style="max-height:${maxH || 480}px"><table class="data"><thead><tr>
          <th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Side</th><th>Product</th><th>Qty</th><th>Price</th><th>Value</th>
        </tr></thead><tbody>` + rows.map((tr, idx) =>
        `<tr class="tm-click-row" data-inspect-trd="${idx}" title="Click for chart &amp; details"><td style="text-align:left">${esc(tr.time)}</td><td style="text-align:left"><b>${esc(tr.symbol)}</b></td>` +
        `<td class="${tr.side === "BUY" ? "pos" : "neg"}">${esc(tr.side)}</td><td>${esc(tr.product)}</td>` +
        `<td>${fmt(tr.qty)}</td><td>₹${fmt(tr.price, 2)}</td><td>${inr(tr.value)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<p class="empty">No executed trades today yet.</p>`;
  }
  function renderTradeBody() {
    const d = tmData, body = $("tmodeBody"); if (!d || !body) return;
    const ctrl = d.controls || {}, f = d.funds || {};
    const useFilter = tmSeg !== "all" && TM_SEG_TABS.has(tmTab);
    const posList = tmViewData.positions || [];
    const ordList = tmViewData.orders || [];
    const trdList = tmViewData.trades || [];
    const hldList = tmViewData.holdings || [];
    const hldVal = useFilter ? hldList.reduce((s, h) => s + Number(h.value || 0), 0) : (f.holdings_value || 0);
    const hldPnl = useFilter ? hldList.reduce((s, h) => s + Number(h.pnl || 0), 0) : ((d.pnl || {}).holdings_pnl || 0);
    const segLabel = useFilter ? (tmSeg === "equity" ? " · Equity" : " · F&O") : "";
    const cntStr = (shown, total) => (useFilter && shown !== total) ? `${shown} / ${total}` : `${shown}`;
    const totPos = (d.positions || []).length, totOrd = (d.orders || []).length, totTrd = (d.trades || []).length, totHld = (d.holdings || []).length;
    if (tmTab === "overview") {
      body.innerHTML = `<div class="tmode-split">` +
        `<div class="tmode-box"><div class="tmode-sec-h"><span>⚡ Open &amp; Today Positions${segLabel} (${cntStr(posList.length, totPos)})</span><button class="jbtn sm" type="button" data-jump-tmtab="positions">View all</button></div>${_tmPositionsTable(posList, ctrl, 260)}</div>` +
        `<div class="tmode-box"><div class="tmode-sec-h"><span>💳 Funds &amp; Margin Breakdown</span><button class="jbtn sm" type="button" data-jump-tmtab="orders">Orders</button></div>` +
        `<div class="tm-grid" style="margin-top:4px">` +
        `<div class="tm-cell"><div class="k">Available Cash</div><div class="v pos">${inr(f.available || 0)}</div></div>` +
        `<div class="tm-cell"><div class="k">Utilized Margin</div><div class="v">${inr(f.utilized || 0)}</div></div>` +
        `<div class="tm-cell"><div class="k">SOD / Opening</div><div class="v">${inr(f.sod || 0)}</div></div>` +
        `<div class="tm-cell"><div class="k">Total Balance</div><div class="v">${inr(f.total || 0)}</div></div>` +
        `<div class="tm-cell"><div class="k">Holdings Value${segLabel}</div><div class="v">${inr(hldVal)}</div></div>` +
        `<div class="tm-cell"><div class="k">Holdings P&amp;L${segLabel}</div><div class="v ${hldPnl >= 0 ? "pos" : "neg"}">${signed(hldPnl)}</div></div>` +
        `</div></div></div>` +
      `<div class="tmode-box" style="margin-top:12px"><div class="tmode-sec-h"><span>📋 Today Orders${segLabel} (${cntStr(ordList.length, totOrd)})</span><button class="jbtn sm" type="button" data-jump-tmtab="trades">Today's Trades →</button></div>${_tmOrdersTable(ordList, ctrl, 260)}</div>`;
      return;
    }
    if (tmTab === "positions") {
      body.innerHTML = `<div class="tmode-box"><div class="tmode-sec-h"><span>⚡ Positions${segLabel} (${cntStr(posList.length, totPos)})</span></div>${_tmPositionsTable(posList, ctrl, 520)}</div>`;
      return;
    }
    if (tmTab === "orders") {
      body.innerHTML = `<div class="tmode-box"><div class="tmode-sec-h"><span>📋 Orders Today${segLabel} (${cntStr(ordList.length, totOrd)})</span></div>${_tmOrdersTable(ordList, ctrl, 520)}</div>`;
      return;
    }
    if (tmTab === "trades") {
      body.innerHTML = `<div class="tmode-box"><div class="tmode-sec-h"><span>✅ Executed Trades Today${segLabel} (${cntStr(trdList.length, totTrd)})</span></div>${_tmTradesTable(trdList, 520)}</div>`;
      return;
    }
    if (tmTab === "holdings") {
      const rows = hldList;
      body.innerHTML = `<div class="tmode-box"><div class="tmode-sec-h"><span>💼 Portfolio Holdings${segLabel} (${cntStr(rows.length, totHld)})</span></div>` + (rows.length
        ? `<div class="table-scroll" style="max-height:520px"><table class="data"><thead><tr>
            <th style="text-align:left">Symbol</th><th>Qty</th><th>Avg Cost</th><th>LTP</th><th>Current Value</th><th>P&amp;L</th>
          </tr></thead><tbody>` + rows.map((h) =>
          `<tr><td style="text-align:left"><b>${esc(h.symbol)}</b></td><td>${fmt(h.qty)}</td><td>₹${fmt(h.avg_price, 2)}</td>` +
          `<td>₹${fmt(h.ltp, 2)}</td><td>${inr(h.value)}</td><td class="${h.pnl >= 0 ? "pos" : "neg"}"><b>${signed(h.pnl)}</b></td></tr>`).join("") + `</tbody></table></div>`
        : `<p class="empty">No${segLabel ? (tmSeg === "equity" ? " Equity" : " F&amp;O") : ""} portfolio holdings found.</p>`) + `</div>`;
      return;
    }
    if (tmTab === "risk") {
      let html = `<div class="tmode-risk">`;
      if (ctrl.can_killswitch) {
        const ksActive = ctrl.kill_switch === "ACTIVATE";
        html += `<div class="tmode-box"><h3>🔌 Dhan Kill Switch</h3>` +
          `<p class="modal-sub" style="margin:0 0 8px">Status: <b class="${ksActive ? "neg" : "pos"}">${esc(ctrl.kill_switch || "DEACTIVATE")}</b> — locks all order placement for the rest of the day.</p>` +
          `<button class="jbtn ${ksActive ? "primary" : "danger"}" data-ks-set="${ksActive ? "DEACTIVATE" : "ACTIVATE"}">${ksActive ? "Deactivate Kill Switch" : "Activate Kill Switch"}</button></div>`;
      }
      if (ctrl.can_pnl_exit) {
        const pe = ctrl.pnl_exit || {};
        html += `<div class="tmode-box"><h3>🛡 Dhan Auto P&amp;L Exit</h3>` +
          `<p class="modal-sub" style="margin:0 0 6px">Auto-square-off intraday positions at your day target or max loss.</p>` +
          `<div class="tmode-form">` +
          `<label>Max Profit ₹<input id="tmProfVal" type="number" min="0" step="500" value="${esc(pe.profitValue || "")}" placeholder="e.g. 5000"/></label>` +
          `<label>Max Loss ₹<input id="tmLossVal" type="number" min="0" step="500" value="${esc(pe.lossValue || "")}" placeholder="e.g. 3000"/></label>` +
          `<label style="flex-direction:row;align-items:center;gap:5px"><input id="tmExitKs" type="checkbox" ${pe.enableKillSwitch ? "checked" : ""}/> Kill Switch</label>` +
          `<button id="tmPnlExitSave" class="jbtn primary" type="button">Save Rule</button>` +
          `<button id="tmPnlExitClear" class="jbtn" type="button">Disable</button>` +
          `</div></div>`;
      }
      if (!ctrl.can_killswitch && !ctrl.can_pnl_exit) {
        html += `<div class="tmode-box"><h3>⚡ Emergency Position Exit</h3>` +
          `<p class="modal-sub" style="margin:0 0 8px">Square off open positions individually from the <b>Positions</b> tab or exit all open positions at once.</p>` +
          `<button class="jbtn danger" type="button" onclick="document.getElementById('tmodeExitAll')?.click()">⚡ Exit All Open Positions</button></div>`;
      }
      html += `</div>`;
      body.innerHTML = html;
      return;
    }
    // Reports & Ledger tab
    let html = "";
    if (!tmReports) {
      html += `<p class="modal-sub">Loading broker ledger, charges &amp; P&amp;L reports…</p>`;
    } else {
      const led = tmReports.ledger || [], chg = tmReports.charges || [], rls = tmReports.realised || [];
      if (chg.length) {
        html += `<h3 style="margin:8px 0 6px;font-size:13px">Charges History (30D)</h3><pre class="jnote" style="overflow:auto;max-height:180px">${esc(JSON.stringify(chg.slice(0, 12), null, 2))}</pre>`;
      }
      if (rls.length) {
        html += `<h3 style="margin:10px 0 6px;font-size:13px">Realised P&amp;L History (30D)</h3><pre class="jnote" style="overflow:auto;max-height:180px">${esc(JSON.stringify(rls.slice(0, 12), null, 2))}</pre>`;
      }
      if (led.length) {
        const cols = Object.keys(led[0]).slice(0, 6);
        html += `<h3 style="margin:10px 0 6px;font-size:13px">Ledger (Last 30 Days)</h3><div class="table-scroll" style="max-height:360px"><table class="data"><thead><tr>` +
          cols.map((k) => `<th style="text-align:left">${esc(k)}</th>`).join("") + `</tr></thead><tbody>` +
          led.slice(0, 40).map((row) => `<tr>` + cols.map((k) => `<td style="text-align:left">${esc(row[k] == null ? "" : String(row[k]))}</td>`).join("") + `</tr>`).join("") +
          `</tbody></table></div>`;
      } else if (!chg.length && !rls.length) {
        html += `<p class="modal-sub">No recent ledger or charges records in the last 30 days.</p>`;
      }
    }
    body.innerHTML = html;
  }
  setInterval(() => {
    const v = $("viewTradeMode");
    const tmVisible = v && !v.classList.contains("hidden");
    const fwVisible = $("tmFloatWidget") && !$("tmFloatWidget").classList.contains("hidden");
    if (!tmVisible && !fwVisible) return;
    if ($("tmodeAuto") && !$("tmodeAuto").checked) { _tmSetTimer("⏸ off", "paused"); tmCountdown = TM_REFRESH_SEC; return; }
    if (typeof marketStatus === "function" && !marketStatus().open) { _tmSetTimer("⏸ paused", "paused"); tmCountdown = TM_REFRESH_SEC; return; }
    if (document.hidden) { _tmSetTimer("⏸ paused", "paused"); return; }
    tmCountdown -= 1;
    if (tmCountdown <= 0) {
      _tmSetTimer("⟳ …", "due");
      tmCountdown = TM_REFRESH_SEC;
      loadTradeMode({ silent: true });
    } else {
      _tmSetTimer("⏱ " + tmCountdown + "s");
    }
  }, 1000);

  // ------------------------------------------------ Trade Mode Inspector Modal
  let _tmiTv = null, _tmiSeries = null, _tmiRo = null, _tmiState = null;
  let _tmiPriceLines = [];

  function _clearTmiChart() {
    if (_tmiRo) { try { _tmiRo.disconnect(); } catch (e) { } _tmiRo = null; }
    if (_tmiTv) { try { _tmiTv.remove(); } catch (e) { } _tmiTv = null; }
    _tmiSeries = null;
    _tmiPriceLines = [];
  }
  function _tmiMsg(msg, err) {
    const el = $("tmiStatusBanner");
    if (!el) return;
    if (!msg) { el.classList.add("hidden"); return; }
    el.className = "jnote" + (err ? " err" : "");
    el.textContent = msg;
    el.classList.remove("hidden");
  }
  function _parseMarginHint(msg) {
    const m = String(msg || "").match(/add\s+(?:Rs\.?|₹)\s*([\d,]+(?:\.\d+)?)/i);
    return m ? Number(m[1].replace(/,/g, "")) : 0;
  }
  function _tmiResolveCtx() {
    if (!_tmiState) return {};
    const sym = _tmiState.symbol;
    const pos = (tmData?.positions || []).find((p) =>
      (_tmiState.kind === "position" && p.id === _tmiState.id) || (p.symbol && p.symbol === sym)
    ) || (_tmiState.kind === "position" ? _tmiState.item : null);
    const ord = (_tmiState.kind === "order")
      ? ((tmData?.orders || []).find((o) => o.id === _tmiState.id) || _tmiState.item)
      : null;
    const trd = (_tmiState.kind === "trade")
      ? ((tmData?.trades || []).find((t) => t.id === _tmiState.id) || _tmiState.item)
      : null;
    const relOrders = (tmData?.orders || []).filter((o) => o.symbol === sym);
    const relTrades = (tmData?.trades || []).filter((t) => t.symbol === sym);
    return { pos, ord, trd, relOrders, relTrades, quote: _tmiState.quote };
  }
  function _tmiSetOhlc(label, bar) {
    const el = $("tmiOhlcBar");
    if (!el) return;
    if (!bar) { el.innerHTML = esc(label || ""); return; }
    const chg = bar.close - bar.open;
    const pct = bar.open ? (chg / bar.open) * 100 : 0;
    const cls = chg >= 0 ? "pos" : "neg";
    const tStr = typeof bar.time === "number" ? _istFmt.format(new Date(bar.time * 1000)) : (bar.t || "");
    el.innerHTML =
      `<b>${esc(label)}</b>` +
      (tStr ? `<span>${esc(tStr)} IST</span>` : "") +
      `<span>O <b class="${cls}">${fmt(bar.open, 2)}</b></span>` +
      `<span>H <b class="${cls}">${fmt(bar.high, 2)}</b></span>` +
      `<span>L <b class="${cls}">${fmt(bar.low, 2)}</b></span>` +
      `<span>C <b class="${cls}">${fmt(bar.close, 2)}</b></span>` +
      `<span class="${cls}">${chg >= 0 ? "+" : ""}${fmt(chg, 2)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)</span>`;
  }
  function _tmiSyncChartLines() {
    if (!_tmiSeries || !_tmiState || _tmiState.mode !== "contract") return;
    for (const pl of _tmiPriceLines) { try { _tmiSeries.removePriceLine(pl); } catch (e) { } }
    _tmiPriceLines = [];
    const { pos, ord, quote } = _tmiResolveCtx();
    const cs = getComputedStyle(document.documentElement);
    const upCol = (cs.getPropertyValue("--put") || "#089981").trim();
    const dnCol = (cs.getPropertyValue("--call") || "#f23645").trim();
    const entry = Number(pos?.avg_price || ord?.price || _tmiState.item?.price || 0);
    const ltp = Number(quote?.ltp || pos?.ltp || ord?.ltp || 0);
    const tp = Number($("tmiTp")?.value || 0);
    const sl = Number($("tmiSl")?.value || 0);
    if (entry > 0) _tmiPriceLines.push(_tmiSeries.createPriceLine({ price: entry, color: "#3b82f6", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Entry" }));
    if (ltp > 0) _tmiPriceLines.push(_tmiSeries.createPriceLine({ price: ltp, color: "#f59e0b", lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: "LTP" }));
    if (tp > 0) _tmiPriceLines.push(_tmiSeries.createPriceLine({ price: tp, color: upCol, lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: "Target" }));
    if (sl > 0) _tmiPriceLines.push(_tmiSeries.createPriceLine({ price: sl, color: dnCol, lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: "SL" }));
  }
  function _tmiRenderChart() {
    if (!_tmiState) return;
    _clearTmiChart();
    const tvEl = $("tmiTvChart"), emptyEl = $("tmiChartEmpty");
    const seg = $("tmiModeSeg"), pivBtn = $("tmiPivotBtn");
    const hasCon = (_tmiState.candles || []).length >= 2;
    const hasUnd = _tmiState.underlying && (_tmiState.underlying.points || []).length >= 2;
    const undSym = _tmiState.underlying?.symbol || _tmiState.underlying_symbol || "Spot";
    const buttons = [];
    if (hasCon) buttons.push(["contract", `${_tmiState.symbol.split(":").pop().slice(0, 22)} · ${_tmiState.tf}m`]);
    if (hasUnd) buttons.push(["underlying", `${undSym} ${_tmiState.tf}m Spot`]);
    if (!hasCon && !hasUnd) _tmiState.mode = "none";
    else if (_tmiState.mode === "contract" && !hasCon) _tmiState.mode = "underlying";
    else if (_tmiState.mode === "underlying" && !hasUnd) _tmiState.mode = "contract";
    else if (!hasCon && hasUnd) _tmiState.mode = "underlying";
    if (seg) {
      seg.innerHTML = buttons.map(([id, lbl]) => `<button type="button" class="${_tmiState.mode === id ? "active" : ""}" data-tmi-mode="${id}">${esc(lbl)}</button>`).join("");
      seg.classList.toggle("hidden", buttons.length < 2);
      seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        _tmiState.mode = b.dataset.tmiMode;
        _tmiRenderChart();
      }));
    }
    if (pivBtn) {
      const showP = _tmiState.mode === "underlying" && !!(_tmiState.underlying && _tmiState.underlying.pivots);
      pivBtn.classList.toggle("hidden", !showP);
      pivBtn.classList.toggle("active", !!_tmiState.showPivots);
    }
    document.querySelectorAll("#tmiTfSeg button").forEach((b) => b.classList.toggle("active", b.dataset.tmiTf === _tmiState.tf));
    const rawBars = _tmiState.mode === "contract" ? (_tmiState.candles || []) : (_tmiState.underlying?.points || []);
    const label = _tmiState.mode === "contract" ? `${_tmiState.symbol} · ${_tmiState.tf}m` : `${undSym} · ${_tmiState.tf}m Spot`;
    if (!tvEl || typeof LightweightCharts === "undefined" || rawBars.length < 2) {
      if (emptyEl) {
        emptyEl.textContent = _tmiState.loadingChart ? "Loading live intraday chart…" : "No intraday candles available for this symbol right now.";
        emptyEl.classList.remove("hidden");
      }
      _tmiSetOhlc(label, null);
      return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");
    const cs = getComputedStyle(document.documentElement);
    const bg = (cs.getPropertyValue("--panel-2") || "#131722").trim();
    const muted = (cs.getPropertyValue("--muted") || "#787b86").trim();
    const grid = (cs.getPropertyValue("--line") || "rgba(128,128,128,0.14)").trim();
    const upCol = (cs.getPropertyValue("--put") || "#089981").trim();
    const dnCol = (cs.getPropertyValue("--call") || "#f23645").trim();
    const seen = new Set(), bars = [];
    for (const p of rawBars) {
      const time = Number(p.ts);
      if (!time || seen.has(time) || p.o == null || p.c == null) continue;
      seen.add(time);
      bars.push({ time, open: +p.o, high: +p.h, low: +p.l, close: +p.c, t: p.t });
    }
    bars.sort((a, b) => a.time - b.time);
    if (bars.length < 2) { if (emptyEl) emptyEl.classList.remove("hidden"); return; }
    _tmiTv = LightweightCharts.createChart(tvEl, {
      width: tvEl.clientWidth || 760,
      height: tvEl.clientHeight || 310,
      layout: { background: { color: bg }, textColor: muted, fontFamily: "system-ui, -apple-system, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: grid, style: 1 }, horzLines: { color: grid, style: 1 } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: grid, scaleMargins: { top: 0.14, bottom: 0.14 } },
      timeScale: {
        borderColor: grid, timeVisible: true, secondsVisible: false, rightOffset: 4,
        tickMarkFormatter: (time) => _istFmt.format(new Date(Number(time) * 1000)),
      },
      localization: { timeFormatter: (time) => _istFmt.format(new Date(Number(time) * 1000)) },
    });
    _tmiSeries = _tmiTv.addCandlestickSeries({
      upColor: upCol, downColor: dnCol, borderUpColor: upCol, borderDownColor: dnCol,
      wickUpColor: upCol, wickDownColor: dnCol,
    });
    _tmiSeries.setData(bars);
    if (_tmiState.mode === "underlying" && _tmiState.showPivots && _tmiState.underlying?.pivots) {
      const pv = _tmiState.underlying.pivots;
      [["r3", "R3", "#2563eb"], ["r2", "R2", "#2563eb"], ["r1", "R1", "#3b82f6"], ["p", "P", "#60a5fa"], ["s1", "S1", "#3b82f6"], ["s2", "S2", "#2563eb"]]
        .forEach(([k, lbl, col]) => {
          if (pv[k] != null) _tmiSeries.createPriceLine({ price: +pv[k], color: col, lineWidth: k === "p" ? 2 : 1, lineStyle: 2, axisLabelVisible: true, title: lbl });
        });
    } else {
      _tmiSyncChartLines();
    }
    _tmiTv.timeScale().fitContent();
    const last = bars[bars.length - 1];
    _tmiSetOhlc(label, last);
    _tmiTv.subscribeCrosshairMove((param) => {
      const d = param && param.seriesData ? param.seriesData.get(_tmiSeries) : null;
      _tmiSetOhlc(label, d || last);
    });
    if (typeof ResizeObserver !== "undefined") {
      _tmiRo = new ResizeObserver(() => {
        if (_tmiTv && tvEl.clientWidth > 0) _tmiTv.applyOptions({ width: tvEl.clientWidth, height: tvEl.clientHeight });
      });
      _tmiRo.observe(tvEl);
    }
  }
  async function _tmiFetchChart() {
    if (!_tmiState) return;
    const seq = ++_tmiState.fetchSeq;
    _tmiState.loadingChart = true;
    const { pos, ord } = _tmiResolveCtx();
    const secId = pos?.security_id || ord?.security_id || _tmiState.item?.security_id || "";
    const seg = pos?.exchange_segment || ord?.exchange_segment || _tmiState.item?.exchange_segment || "";
    const exp = pos?.drv_expiry || ord?.drv_expiry || _tmiState.item?.drv_expiry || "";
    try {
      const qs = `?symbol=${encodeURIComponent(_tmiState.symbol)}&date=${encodeURIComponent(tmData?.today || "")}` +
        `&timeframe=${encodeURIComponent(_tmiState.tf)}&sec_id=${encodeURIComponent(secId)}` +
        `&exchange_segment=${encodeURIComponent(seg)}&expiry=${encodeURIComponent(exp)}`;
      const d = await (await fetch("/api/trademode/inspect" + qs)).json();
      if (!_tmiState || _tmiState.fetchSeq !== seq) return;
      _tmiState.loadingChart = false;
      _tmiState.candles = d.candles || [];
      _tmiState.underlying = d.underlying || null;
      _tmiState.underlying_symbol = d.underlying_symbol || "";
      if (d.quote && d.quote.ltp) _tmiState.quote = d.quote;
      _tmiState.mode = _tmiState.candles.length >= 2 ? "contract" : (_tmiState.underlying?.points?.length >= 2 ? "underlying" : "none");
      if ($("tmiChartNote")) {
        $("tmiChartNote").textContent = d.note || "";
        $("tmiChartNote").classList.toggle("hidden", !d.note);
      }
      _tmiRenderChart();
      _tmiRenderDetails();
    } catch (e) {
      if (_tmiState && _tmiState.fetchSeq === seq) {
        _tmiState.loadingChart = false;
        _tmiRenderChart();
      }
    }
  }
  function _tmiRenderDetails() {
    if (!_tmiState) return;
    const { pos, ord, trd, quote } = _tmiResolveCtx();
    const item = pos || ord || trd || _tmiState.item || {};
    const side = pos?.side || ord?.side || trd?.side || "";
    const isLong = side === "LONG" || side === "BUY";
    const isShort = side === "SHORT" || side === "SELL";
    const ltp = Number(quote?.ltp || pos?.ltp || ord?.ltp || 0);
    const avg = Number(pos?.avg_price || ord?.price || trd?.price || 0);
    const qty = pos ? Math.abs(pos.net_qty || 0) || Math.max(pos.buy_qty || 0, pos.sell_qty || 0) : Number(ord?.qty || trd?.qty || 0);
    const capUsed = pos
      ? Number(pos.capital_used || (Math.abs(pos.net_qty || 0) * avg) || 0)
      : Number(ord?.capital_used || trd?.value || (qty * (avg || ltp)) || 0);
    const runPnl = pos ? Number(pos.pnl || 0) : null;
    const roiPct = (pos && capUsed > 0) ? (runPnl / capUsed * 100) : null;
    const pts = (pos && pos.is_open && ltp > 0 && avg > 0) ? (pos.net_qty > 0 ? (ltp - avg) : (avg - ltp)) : 0;
    const mvPct = (avg > 0 && pos?.is_open) ? (pts / avg * 100) : 0;
    const shortfall = _parseMarginHint(ord?.message);

    $("tmiTitle").textContent = _tmiState.symbol || "Instrument";
    $("tmiSide").textContent = side || "—";
    $("tmiSide").className = "sig " + (isLong ? "bull" : (isShort ? "bear" : ""));
    const stB = $("tmiStatusBadge");
    if (ord?.status) {
      stB.textContent = ord.status;
      stB.className = "badge";
      stB.classList.remove("hidden");
    } else if (pos) {
      stB.textContent = pos.is_open ? "RUNNING" : "CLOSED";
      stB.className = "badge";
      stB.classList.remove("hidden");
    } else {
      stB.classList.add("hidden");
    }
    const pnlEl = $("tmiPnl");
    if (runPnl != null) {
      const pTxt = signed(runPnl) + (roiPct != null ? ` (${roiPct >= 0 ? "+" : ""}${roiPct.toFixed(2)}%)` : "");
      pnlEl.textContent = pTxt;
      pnlEl.className = "tm-pnl " + (runPnl >= 0 ? "pos" : "neg");
    } else {
      pnlEl.textContent = capUsed > 0 ? `Est. ${inr(capUsed)}` : (ltp > 0 ? `LTP ₹${fmt(ltp, 2)}` : "—");
      pnlEl.className = "tm-pnl";
    }
    $("tmiSub").textContent = [
      item.product, item.exchange_segment || item.exchange, item.time || tmData?.today,
      (tmData?.source || "").toUpperCase(), ord?.id ? `#${ord.id}` : "",
    ].filter(Boolean).join(" · ");

    if (ord?.message && /REJECT|INSUFFICIENT|FAIL|ERROR/i.test(ord.status + " " + ord.message)) {
      _tmiMsg(`Broker Note (${ord.status}): ${ord.message}`, true);
    }

    const cell = (k, v, cls) => `<div class="tm-cell"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div></div>`;
    let html = "";
    html += cell("Capital Used", capUsed > 0 ? inr(capUsed) : "—");
    if (pos) {
      html += cell("Running P&L", `${signed(runPnl)}`, runPnl >= 0 ? "pos" : "neg");
      html += cell("Profit / ROI %", roiPct != null ? `${roiPct >= 0 ? "+" : ""}${roiPct.toFixed(2)}%` : "—", (roiPct || 0) >= 0 ? "pos" : "neg");
      html += cell("Live LTP", ltp > 0 ? `₹${fmt(ltp, 2)}` : "—");
      html += cell("Entry / Avg", avg > 0 ? `₹${fmt(avg, 2)}` : "—");
      html += cell("Move (Pts / %)", pos.is_open && ltp > 0 ? `${pts >= 0 ? "+" : ""}${fmt(pts, 2)} (${mvPct >= 0 ? "+" : ""}${mvPct.toFixed(2)}%)` : "CLOSED", pts >= 0 ? "pos" : "neg");
      html += cell("Realized / Unrealized", `${signed(pos.realized || 0)} / ${signed(pos.unrealized || 0)}`);
      html += cell("Qty (Buy / Sell)", `${fmt(pos.net_qty)} (${pos.buy_qty || 0} / ${pos.sell_qty || 0})`);
    } else if (ord) {
      html += cell("Order Status", esc(ord.status || "—"), /TRADED/i.test(ord.status) ? "pos" : (/REJECT|CANCEL/i.test(ord.status) ? "neg" : ""));
      html += cell("Required Margin / Value", shortfall > 0 ? `${inr(shortfall)} shortfall` : (capUsed > 0 ? inr(capUsed) : "—"));
      html += cell("Live LTP", ltp > 0 ? `₹${fmt(ltp, 2)}` : "—");
      html += cell("Order / Trigger Price", `${ord.price > 0 ? "₹" + fmt(ord.price, 2) : "MARKET"}${ord.stop_price > 0 ? " · SL ₹" + fmt(ord.stop_price, 2) : ""}`);
      html += cell("Quantity (Filled)", `${ord.filled_qty || 0} / ${ord.qty || 0}`);
      html += cell("Order Type · Validity", `${esc(ord.order_type || "—")} · ${esc(ord.validity || "DAY")}`);
      html += cell("Broker Message", esc(ord.message || "—"));
    } else {
      html += cell("Trade Value", inr(trd?.value || capUsed));
      html += cell("Executed Price", `₹${fmt(trd?.price || 0, 2)}`);
      html += cell("Live LTP", ltp > 0 ? `₹${fmt(ltp, 2)}` : "—");
      html += cell("Quantity", fmt(qty));
      html += cell("Order ID", esc(trd?.order_id || trd?.id || "—"));
      html += cell("Product", esc(trd?.product || "—"));
      html += cell("Time", esc(trd?.time || "—"));
    }
    $("tmiDetails").innerHTML = html;
  }
  function _tmiUpdateRR() {
    const rrEl = $("tmiRRPreview");
    if (!rrEl || !_tmiState) return;
    const { pos, ord } = _tmiResolveCtx();
    const ref = Number(pos?.avg_price || pos?.ltp || ord?.ltp || ord?.price || _tmiState.quote?.ltp || 0);
    const q = Number($("tmiCtrlQty")?.value || Math.abs(pos?.net_qty || 0) || ord?.qty || 0);
    const isLong = pos ? (pos.net_qty >= 0) : ((ord?.side || "") === "BUY");
    const tp = Number($("tmiTp")?.value || 0), sl = Number($("tmiSl")?.value || 0);
    if (!ref || !q || (!tp && !sl)) { rrEl.textContent = "Enter Target and/or Stop Loss prices to preview profit/risk and R:R."; return; }
    const parts = [];
    let reward = 0, risk = 0;
    if (tp > 0) {
      reward = (isLong ? (tp - ref) : (ref - tp)) * q;
      const pct = ((isLong ? (tp - ref) : (ref - tp)) / ref * 100);
      parts.push(`Target: <b class="${reward >= 0 ? "pos" : "neg"}">${signed(reward)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</b>`);
    }
    if (sl > 0) {
      risk = (isLong ? (sl - ref) : (ref - sl)) * q;
      const pct = ((isLong ? (sl - ref) : (ref - sl)) / ref * 100);
      parts.push(`SL: <b class="${risk >= 0 ? "pos" : "neg"}">${signed(risk)} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)</b>`);
    }
    if (tp > 0 && sl > 0 && risk < 0 && reward > 0) {
      parts.push(`R:R <b>1 : ${(reward / Math.abs(risk)).toFixed(2)}</b>`);
    }
    rrEl.innerHTML = parts.join(" · ");
    _tmiSyncChartLines();
  }
  function _tmiRenderActions() {
    const el = $("tmiActions");
    if (!el || !_tmiState) return;
    const { pos, ord, quote } = _tmiResolveCtx();
    const ctrl = tmData?.controls || {};
    let html = "";

    if (pos && pos.is_open) {
      const ref = Number(pos.ltp || pos.avg_price || quote?.ltp || 0);
      const q = Math.abs(pos.net_qty || 0);
      const flipSide = pos.net_qty > 0 ? "SELL" : "BUY";
      const flipTo = pos.net_qty > 0 ? "SHORT" : "LONG";
      html += `<div class="tmi-panel">` +
        `<h3><span>🎯 Set Target (TP) · Stop Loss (SL) · Trail (TL)</span><small style="color:var(--muted)">Ref ₹${fmt(ref, 2)}</small></h3>` +
        `<div class="tmi-form">` +
        `<label>Target TP ₹<input id="tmiTp" type="number" step="0.05" placeholder="e.g. ${ref ? fmt(pos.net_qty > 0 ? ref * 1.15 : ref * 0.85, 2) : ""}"/></label>` +
        `<label>Stop Loss SL ₹<input id="tmiSl" type="number" step="0.05" placeholder="e.g. ${ref ? fmt(pos.net_qty > 0 ? ref * 0.90 : ref * 1.10, 2) : ""}"/></label>` +
        `<label>Trail Jump TL ₹<input id="tmiTl" type="number" step="0.5" min="0" placeholder="optional"/></label>` +
        `<label>Qty<input id="tmiCtrlQty" type="number" min="1" value="${q}"/></label>` +
        `</div>` +
        `<div class="tmi-chips">` +
        `<button class="tmi-chip" type="button" data-tmi-preset="sl:5">SL 5%</button>` +
        `<button class="tmi-chip" type="button" data-tmi-preset="sl:10">SL 10%</button>` +
        `<button class="tmi-chip" type="button" data-tmi-preset="sl:20">SL 20%</button>` +
        `<button class="tmi-chip" type="button" data-tmi-preset="tp:10">TP +10%</button>` +
        `<button class="tmi-chip" type="button" data-tmi-preset="tp:20">TP +20%</button>` +
        `<button class="tmi-chip" type="button" data-tmi-preset="tp:40">TP +40%</button>` +
        `</div>` +
        `<div id="tmiRRPreview" class="tmi-rr">Enter Target and/or Stop Loss prices to preview profit/risk and R:R.</div>` +
        `<div class="tmi-btn-row">` +
        `<button id="tmiPlaceTpSlBtn" class="jbtn primary sm" type="button">🎯 Place TP / SL Orders</button>` +
        `</div></div>`;

      html += `<div class="tmi-panel">` +
        `<h3>⚡ Position Controls</h3>` +
        `<p class="modal-sub" style="margin:0 0 8px">Running: <b>${esc(pos.side)} ${q}</b> · Avg ₹${fmt(pos.avg_price, 2)} · LTP ₹${fmt(ref, 2)}<br/>Reversing places <b>${flipSide} ${q * 2} MARKET</b> to flip ${esc(pos.side)} → <b>${flipTo} ${q}</b>.</p>` +
        `<div class="tmi-btn-row">` +
        (ctrl.can_reverse_position !== false ? `<button id="tmiReverseBtn" class="jbtn sm" type="button">⇄ Reverse (${flipSide} ${q * 2})</button>` : "") +
        (ctrl.can_exit_single !== false ? `<button id="tmiExitBtn" class="jbtn danger sm" type="button">✕ Exit Position (${q})</button>` : "") +
        `</div></div>`;
    }

    if (ord && ord.is_pending) {
      html += `<div class="tmi-panel">` +
        `<h3>✏️ Modify / Cancel Pending Order (#${esc(ord.id)})</h3>` +
        `<div class="tmi-form">` +
        `<label>Order Type<select id="tmiModType"><option value="LIMIT" ${/LIMIT/i.test(ord.order_type) && !/SL/i.test(ord.order_type) ? "selected" : ""}>LIMIT</option><option value="MARKET" ${/MARKET/i.test(ord.order_type) && !/SL/i.test(ord.order_type) ? "selected" : ""}>MARKET</option><option value="SL-M" ${/SL-M|STOP_LOSS_MARKET/i.test(ord.order_type) ? "selected" : ""}>SL-M</option><option value="SL-L" ${/SL-L|STOP_LOSS$/i.test(ord.order_type) ? "selected" : ""}>SL-L</option></select></label>` +
        `<label>Qty<input id="tmiModQty" type="number" min="1" value="${ord.qty || 1}"/></label>` +
        `<label>Price ₹<input id="tmiModPrice" type="number" step="0.05" value="${ord.price || 0}"/></label>` +
        `<label>Trigger / SL ₹<input id="tmiModStop" type="number" step="0.05" value="${ord.stop_price || 0}"/></label>` +
        `</div>` +
        `<div class="tmi-btn-row">` +
        `<button id="tmiModSaveBtn" class="jbtn primary sm" type="button">💾 Save Modification</button>` +
        `<button id="tmiCancelOrdBtn" class="jbtn danger sm" type="button">✕ Cancel Order</button>` +
        `</div></div>`;
    }

    if ((!pos || !pos.is_open) && ord && !ord.is_pending) {
      const ref = Number(quote?.ltp || ord.ltp || ord.price || 0);
      html += `<div class="tmi-panel">` +
        `<h3>⚡ Place Fresh Order (with Target · SL · Trail TL)</h3>` +
        `<div class="tmi-form">` +
        `<label>Side<select id="tmiNewSide"><option value="BUY" ${ord.side === "BUY" ? "selected" : ""}>BUY</option><option value="SELL" ${ord.side === "SELL" ? "selected" : ""}>SELL</option></select></label>` +
        `<label>Type<select id="tmiNewType"><option value="MARKET">MARKET</option><option value="LIMIT">LIMIT</option></select></label>` +
        `<label>Qty<input id="tmiNewQty" type="number" min="1" value="${ord.qty || 1}"/></label>` +
        `<label>Price ₹<input id="tmiNewPrice" type="number" step="0.05" value="${ref || ord.price || 0}"/></label>` +
        `<label>Target TP ₹<input id="tmiNewTp" type="number" step="0.05" placeholder="optional"/></label>` +
        `<label>Stop Loss SL ₹<input id="tmiNewSl" type="number" step="0.05" placeholder="optional"/></label>` +
        `<label>Trail Jump TL ₹<input id="tmiNewTl" type="number" step="0.5" min="0" placeholder="optional"/></label>` +
        `</div>` +
        `<div class="tmi-btn-row"><button id="tmiPlaceNewBtn" class="jbtn primary sm" type="button">⚡ Place Order</button></div></div>`;
    }

    el.innerHTML = html;
  }
  function _tmiRenderRelated() {
    const el = $("tmiRelated");
    if (!el || !_tmiState) return;
    const { relOrders, relTrades } = _tmiResolveCtx();
    const ctrl = tmData?.controls || {};
    let html = "";
    if (relOrders.length) {
      html += `<div class="tmi-panel"><h3 style="margin-bottom:6px">📋 Today's Orders on ${esc(_tmiState.symbol)} (${relOrders.length})</h3>` +
        _tmOrdersTable(relOrders, ctrl, 180) + `</div>`;
    }
    if (relTrades.length) {
      html += `<div class="tmi-panel" style="margin-top:8px"><h3 style="margin-bottom:6px">✅ Executed Fills (${relTrades.length})</h3>` +
        _tmTradesTable(relTrades, 160) + `</div>`;
    }
    el.innerHTML = html;
  }
  function _tmiSyncLive() {
    if (!_tmiState) return;
    _tmiRenderDetails();
    _tmiRenderRelated();
    _tmiSyncChartLines();
    if (_tmiState.posOpenState !== !!_tmiResolveCtx().pos?.is_open) {
      _tmiState.posOpenState = !!_tmiResolveCtx().pos?.is_open;
      _tmiRenderActions();
    } else {
      _tmiUpdateRR();
    }
  }
  function openTmInspect({ kind, item }) {
    const modal = $("tmInspectModal");
    if (!modal || !item) return;
    _clearTmiChart();
    _tmiMsg("");
    _tmiState = {
      kind,
      id: String(item.id || ""),
      symbol: String(item.symbol || ""),
      item,
      tf: "5",
      mode: "contract",
      showPivots: true,
      loadingChart: true,
      candles: [],
      underlying: null,
      quote: null,
      fetchSeq: 0,
      posOpenState: !!(kind === "position" ? item.is_open : _tmiResolveCtx?.()?.pos?.is_open),
    };
    _tmiState.posOpenState = !!_tmiResolveCtx().pos?.is_open;
    $("tmiChartNote")?.classList.add("hidden");
    modal.classList.remove("hidden");
    _tmiRenderDetails();
    _tmiRenderActions();
    _tmiRenderRelated();
    _tmiRenderChart();
    _tmiFetchChart();
  }
  function _closeTmInspect() {
    _clearTmiChart();
    _tmiState = null;
    if ($("tmInspectModal")) $("tmInspectModal").classList.add("hidden");
  }
  if ($("tmiClose")) $("tmiClose").addEventListener("click", _closeTmInspect);
  if ($("tmInspectModal")) $("tmInspectModal").addEventListener("click", (e) => {
    if (e.target === $("tmInspectModal")) _closeTmInspect();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("tmInspectModal") && !$("tmInspectModal").classList.contains("hidden")) _closeTmInspect();
  });
  if ($("tmiRefreshBtn")) $("tmiRefreshBtn").addEventListener("click", () => { loadTradeMode({ silent: true }); _tmiFetchChart(); });
  if ($("tmiPivotBtn")) $("tmiPivotBtn").addEventListener("click", () => {
    if (!_tmiState) return;
    _tmiState.showPivots = !_tmiState.showPivots;
    _tmiRenderChart();
  });
  if ($("tmiTfSeg")) $("tmiTfSeg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tmi-tf]");
    if (!b || !_tmiState) return;
    _tmiState.tf = b.dataset.tmiTf;
    _tmiFetchChart();
  });
  if ($("tmiRelated")) $("tmiRelated").addEventListener("click", async (e) => {
    const cBtn = e.target.closest("[data-cancel-ord]");
    if (!cBtn) return;
    const oid = cBtn.dataset.cancelOrd, sym = cBtn.dataset.sym || oid;
    if (!confirm(`Cancel pending order ${sym} (#${oid})?`)) return;
    try {
      const r = await fetch("/api/trademode/cancel-order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: oid }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Cancel failed");
      _tmiMsg(`✅ Cancelled order #${oid}.`);
      loadTradeMode({ silent: true });
    } catch (err) { _tmiMsg("Cancel failed: " + err.message, true); }
  });
  if ($("tmiActions")) {
    $("tmiActions").addEventListener("input", (e) => {
      if (["tmiTp", "tmiSl", "tmiTl", "tmiCtrlQty"].includes(e.target.id)) _tmiUpdateRR();
    });
    $("tmiActions").addEventListener("click", async (e) => {
      if (!_tmiState) return;
      const presetBtn = e.target.closest("[data-tmi-preset]");
      if (presetBtn) {
        const [kindP, pctS] = presetBtn.dataset.tmiPreset.split(":");
        const pct = Number(pctS) / 100;
        const { pos, ord, quote } = _tmiResolveCtx();
        const ref = Number(pos?.ltp || pos?.avg_price || ord?.ltp || ord?.price || quote?.ltp || 0);
        if (!ref) return;
        const isLong = pos ? (pos.net_qty >= 0) : ((ord?.side || "") === "BUY");
        if (kindP === "sl" && $("tmiSl")) $("tmiSl").value = (ref * (isLong ? (1 - pct) : (1 + pct))).toFixed(2);
        if (kindP === "tp" && $("tmiTp")) $("tmiTp").value = (ref * (isLong ? (1 + pct) : (1 - pct))).toFixed(2);
        _tmiUpdateRR();
        return;
      }
      if (e.target.id === "tmiPlaceTpSlBtn") {
        const { pos } = _tmiResolveCtx();
        if (!pos || !pos.is_open) return _tmiMsg("No running position found to attach TP/SL.", true);
        const tp = Number($("tmiTp")?.value || 0), sl = Number($("tmiSl")?.value || 0), tl = Number($("tmiTl")?.value || 0);
        const qty = Number($("tmiCtrlQty")?.value || Math.abs(pos.net_qty || 0));
        if (tp <= 0 && sl <= 0) return _tmiMsg("Enter a Target (TP) and/or Stop Loss (SL) price first.", true);
        if (!confirm(`Place exit protection on ${pos.symbol} (Qty ${qty}):${tp > 0 ? " Target ₹" + tp : ""}${sl > 0 ? " SL ₹" + sl : ""}${tl > 0 ? " TL ₹" + tl : ""}?`)) return;
        try {
          const r = await fetch("/api/trademode/sl-target", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id: pos.id, symbol: pos.symbol, security_id: pos.security_id,
              exchange_segment: pos.exchange_segment, product: pos.product, side: pos.side,
              qty, target_price: tp, sl_price: sl, trailing_jump: tl,
            }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Failed to place TP/SL");
          const msg = `✅ Placed ${(d.result?.placed || []).map((x) => x.leg).join(" + ")} order(s)` + (d.result?.note ? ` — ${d.result.note}` : "");
          _tmiMsg(msg);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("TP/SL failed: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmiReverseBtn") {
        const { pos } = _tmiResolveCtx();
        if (!pos || !pos.is_open) return _tmiMsg("No open position to reverse.", true);
        const qty = Number($("tmiCtrlQty")?.value || Math.abs(pos.net_qty || 0));
        if (!confirm(`REVERSE ${pos.symbol}? Squares off ${Math.abs(pos.net_qty)} and opens opposite side ${qty} via MARKET order.`)) return;
        try {
          const r = await fetch("/api/trademode/reverse-position", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: pos.id, symbol: pos.symbol, security_id: pos.security_id, qty }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Reverse failed");
          _tmiMsg(`✅ Reversed ${pos.symbol} → ${d.result?.side || ""} ${d.result?.qty || ""} placed.`);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("Reverse failed: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmiExitBtn") {
        const { pos } = _tmiResolveCtx();
        if (!pos || !pos.is_open) return _tmiMsg("Position is already closed.", true);
        if (!confirm(`Exit open position ${pos.symbol} (${Math.abs(pos.net_qty)} qty MARKET)?`)) return;
        try {
          const r = await fetch("/api/trademode/exit-position", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: pos.id, symbol: pos.symbol, security_id: pos.security_id }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Exit failed");
          _tmiMsg(`✅ Exit order placed for ${pos.symbol}.`);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("Exit failed: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmiModSaveBtn") {
        const { ord } = _tmiResolveCtx();
        if (!ord) return;
        const order_type = $("tmiModType")?.value || "LIMIT";
        const qty = Number($("tmiModQty")?.value || ord.qty || 1);
        const price = Number($("tmiModPrice")?.value || 0);
        const stop_price = Number($("tmiModStop")?.value || 0);
        try {
          const r = await fetch("/api/trademode/modify-order", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ord.id, order_type, qty, price, stop_price, validity: ord.validity || "DAY" }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Modify failed");
          _tmiMsg(`✅ Modified order #${ord.id}.`);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("Modify failed: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmiCancelOrdBtn") {
        const { ord } = _tmiResolveCtx();
        if (!ord) return;
        if (!confirm(`Cancel pending order ${ord.symbol} (#${ord.id})?`)) return;
        try {
          const r = await fetch("/api/trademode/cancel-order", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ord.id }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Cancel failed");
          _tmiMsg(`✅ Cancelled order #${ord.id}.`);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("Cancel failed: " + err.message, true); }
        return;
      }
      if (e.target.id === "tmiPlaceNewBtn") {
        const { ord } = _tmiResolveCtx();
        const side = $("tmiNewSide")?.value || "BUY";
        const order_type = $("tmiNewType")?.value || "MARKET";
        const qty = Number($("tmiNewQty")?.value || 1);
        const price = Number($("tmiNewPrice")?.value || 0);
        const target_price = Number($("tmiNewTp")?.value || 0);
        const sl_price = Number($("tmiNewSl")?.value || 0);
        const trailing_jump = Number($("tmiNewTl")?.value || 0);
        if (!confirm(`Place ${side} ${qty} ${_tmiState.symbol} (${order_type}${price && order_type === "LIMIT" ? " @ ₹" + price : ""})?`)) return;
        try {
          const r = await fetch("/api/trademode/place-order", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              symbol: _tmiState.symbol,
              security_id: ord?.security_id || _tmiState.item?.security_id || "",
              exchange_segment: ord?.exchange_segment || _tmiState.item?.exchange_segment || "",
              side, qty, product: ord?.product || "INTRADAY", order_type, price,
              target_price, sl_price, trailing_jump,
            }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || "Order placement failed");
          _tmiMsg(`✅ Order placed for ${_tmiState.symbol}.`);
          loadTradeMode({ silent: true });
        } catch (err) { _tmiMsg("Place order failed: " + err.message, true); }
      }
    });
  }

  // ------------------------------------------------ Movable Floating P&L Box
  function _clampFloatPos(left, top, box) {
    const w = box ? box.offsetWidth || 280 : 280;
    const h = box ? box.offsetHeight || 180 : 180;
    return {
      left: Math.max(8, Math.min(window.innerWidth - w - 8, left)),
      top: Math.max(8, Math.min(window.innerHeight - h - 8, top)),
    };
  }
  function _applyStoredFloatPos() {
    const box = $("tmFloatWidget");
    if (!box) return;
    try {
      const raw = JSON.parse(localStorage.getItem("tm-float-pos") || "null");
      if (raw && Number.isFinite(raw.left) && Number.isFinite(raw.top)) {
        const c = _clampFloatPos(raw.left, raw.top, box);
        box.style.left = c.left + "px";
        box.style.top = c.top + "px";
        box.style.right = "auto";
        box.style.bottom = "auto";
      }
    } catch (e) { /* keep default bottom-right anchor */ }
  }
  function _renderTmFloatWidget() {
    const body = $("tmFloatBody"), brk = $("tmFloatBroker");
    if (!body) return;
    const d = tmData;
    if (!d) {
      if (brk) brk.textContent = "—";
      body.innerHTML = `<div class="tm-float-sub">Syncing live P&amp;L…</div>`;
      return;
    }
    if (!d.supported) {
      if (brk) brk.textContent = (d.source || "—").toUpperCase();
      body.innerHTML = `<div class="tm-float-sub">Live P&amp;L float supports <b>Fyers</b> &amp; <b>Dhan</b>.</div>`;
      return;
    }
    const p = d.pnl || {}, f = d.funds || {}, c = d.counts || {}, ctrl = d.controls || {};
    if (brk) brk.textContent = (d.source || "").toUpperCase();
    const tot = Number(p.total || 0), rls = Number(p.realized || 0), unrl = Number(p.unrealized || 0);
    body.innerHTML =
      `<div class="tm-float-pnl ${tot >= 0 ? "pos" : "neg"}">${signed(tot)}</div>` +
      `<div class="tm-float-sub">Realized ${signed(rls)} · MTM ${signed(unrl)}</div>` +
      `<div class="tm-float-meta">` +
      `<div class="tm-float-cell"><div class="k">Open Pos</div><div class="v">${c.open_positions || 0} / ${c.total_positions || 0}</div></div>` +
      `<div class="tm-float-cell"><div class="k">Avail Funds</div><div class="v pos">${inr(f.available || 0)}</div></div>` +
      `<div class="tm-float-cell"><div class="k">Orders</div><div class="v">${c.orders || 0} (${c.pending_orders || 0} pend)</div></div>` +
      `<div class="tm-float-cell"><div class="k">Fills</div><div class="v">${c.trades || 0}</div></div>` +
      `</div>` +
      `<div class="tm-float-foot">` +
      `<button id="tmFloatOpenTm" class="jbtn sm" type="button">⚡ Trade Mode</button>` +
      (ctrl.can_exit_all ? `<button id="tmFloatExitAll" class="jbtn danger sm" type="button" onclick="document.getElementById('tmodeExitAll')?.click()">⚡ Exit All</button>` : "") +
      `</div>`;
    if ($("tmFloatOpenTm")) $("tmFloatOpenTm").addEventListener("click", () => switchView("trademode"));
  }
  function openTmFloatWidget() {
    const box = $("tmFloatWidget");
    if (!box) return;
    box.classList.remove("hidden", "minimized");
    try { localStorage.setItem("tm-float-open", "1"); } catch (e) { }
    _renderTmFloatWidget();
    _applyStoredFloatPos();
    if ($("tmodeKpiFloatBtn")) $("tmodeKpiFloatBtn").classList.add("active");
    if (!tmData) loadTradeMode({ silent: true });
  }
  function closeTmFloatWidget() {
    const box = $("tmFloatWidget");
    if (!box) return;
    box.classList.add("hidden");
    try { localStorage.removeItem("tm-float-open"); } catch (e) { }
    if ($("tmodeKpiFloatBtn")) $("tmodeKpiFloatBtn").classList.remove("active");
  }
  function toggleTmFloatWidget() {
    const box = $("tmFloatWidget");
    if (!box) return;
    if (box.classList.contains("hidden")) openTmFloatWidget();
    else closeTmFloatWidget();
  }
  (function initTmFloatDrag() {
    const box = $("tmFloatWidget"), head = $("tmFloatHead");
    if (!box || !head) return;
    let drag = null;
    head.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      const rect = box.getBoundingClientRect();
      drag = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
      box.style.left = rect.left + "px";
      box.style.top = rect.top + "px";
      box.style.right = "auto";
      box.style.bottom = "auto";
      try { head.setPointerCapture(e.pointerId); } catch (err) { }
      e.preventDefault();
    });
    head.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const c = _clampFloatPos(e.clientX - drag.dx, e.clientY - drag.dy, box);
      box.style.left = c.left + "px";
      box.style.top = c.top + "px";
    });
    const endDrag = () => {
      if (!drag) return;
      drag = null;
      const rect = box.getBoundingClientRect();
      try { localStorage.setItem("tm-float-pos", JSON.stringify({ left: Math.round(rect.left), top: Math.round(rect.top) })); } catch (e) { }
    };
    head.addEventListener("pointerup", endDrag);
    head.addEventListener("pointercancel", endDrag);
    window.addEventListener("resize", () => {
      if (box.classList.contains("hidden") || !box.style.left) return;
      const c = _clampFloatPos(parseFloat(box.style.left), parseFloat(box.style.top), box);
      box.style.left = c.left + "px";
      box.style.top = c.top + "px";
    });
    if ($("tmFloatClose")) $("tmFloatClose").addEventListener("click", closeTmFloatWidget);
    if ($("tmFloatMin")) $("tmFloatMin").addEventListener("click", () => box.classList.toggle("minimized"));
    if ($("tmFloatSync")) $("tmFloatSync").addEventListener("click", () => loadTradeMode({ silent: true }));
  })();

  // Redraw dashboard charts when theme changes so grid/tick/series colors stay synced.
  window.addEventListener("oi-theme-change", () => {
    if (homeData && $("viewHome") && !$("viewHome").classList.contains("hidden")) renderHome(homeData);
  });

  // Land on the analytics Dashboard once auth status allows (runs last, after all declarations exist).
  (window.__authReady || Promise.resolve(true)).then((ok) => {
    if (ok) {
      switchView("home");
      try { if (localStorage.getItem("tm-float-open") === "1") openTmFloatWidget(); } catch (e) { }
    }
  });
})();
