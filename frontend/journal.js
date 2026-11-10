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
  document.querySelectorAll(".tab").forEach((btn) =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)));

  function switchView(view) {
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view));
    $("viewOI").classList.toggle("hidden", view !== "oi");
    $("viewJournal").classList.toggle("hidden", view !== "journal");
    $("viewCapital").classList.toggle("hidden", view !== "capital");
    $("viewRules").classList.toggle("hidden", view !== "rules");
    $("viewInsights").classList.toggle("hidden", view !== "insights");
    const oiCtl = $("oiControls");
    if (oiCtl) oiCtl.style.display = view === "oi" ? "" : "none";
    if (view === "journal") loadJournal();
    if (view === "capital") loadCapital();
    if (view === "rules") loadRules();
    if (view === "insights") loadInsights();
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
      dhanConnected = d.dhan_connected;
      $("dhanBtn").textContent = dhanConnected ? "⬇ Sync from Dhan ✓" : "🔑 Connect Dhan";
      $("fyersSyncBtn").style.display = d.can_sync ? "" : "none";   // only on DATA_SOURCE=fyers
      if (dhanConnected && !dhanAutoSynced) dhanSyncNow(true);      // Dhan auto-sync once per load
      if (d.can_sync && !fyersAutoSynced) fyersSyncNow(true);       // Fyers auto-sync once per load
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
    // sync buttons: Fyers only when on the Fyers source; Dhan label by connection
    $("capFyersSync").style.display = c.can_sync ? "" : "none";
    $("capDhanSync").textContent = c.dhan_connected
      ? "⬇ Sync this month (Dhan)" : "🔑 Connect Dhan";
    const inp = $("openingCapital");
    if (inp && document.activeElement !== inp) inp.value = c.opening || "";
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
    $("capitalTiles").innerHTML =
      capTile("Opening capital", inr(c.opening)) +
      capTile("Profit / loss this month", signed(c.total_pnl), c.total_pnl >= 0 ? "pos" : "neg") +
      capTile("Closing capital", inr(c.closing));
    const ret = $("capitalReturn");
    if (!c.opening) {
      ret.innerHTML = `<span class="cap-hint">Enter your Day 1 capital above to see your return.</span>`;
    } else {
      const rc = c.return_pct >= 0 ? "pos" : "neg";
      const sign = c.return_pct >= 0 ? "+" : "";
      ret.innerHTML = `Return this month: <b class="${rc}">${sign}${Number(c.return_pct).toFixed(2)}%</b>` +
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
        { type: "bar", label: "Daily P&L", data: bars, backgroundColor: barColors,
          borderRadius: 3, maxBarThickness: 18, yAxisID: "y", order: 2 },
        { type: "line", label: "Capital", data: line, borderColor: "#f0b429",
          backgroundColor: "rgba(240,180,41,.12)", borderWidth: 2, tension: .25,
          pointRadius: 0, fill: true, yAxisID: "y1", order: 1, spanGaps: true },
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
        x: { ticks: { color: "#8b95a7", maxRotation: 0, autoSkip: true, maxTicksLimit: 16 },
             grid: { display: false } },
        y: { position: "left", ticks: { color: "#8b95a7", callback: (v) => (Math.abs(v) >= 1000 ? "₹" + Math.round(v / 1000) + "k" : "₹" + v) },
             grid: { color: "rgba(128,128,128,.18)" } },
        y1: { position: "right", ticks: { color: "#b78a1a", callback: (v) => "₹" + Math.round(v / 1000) + "k" },
              grid: { display: false } },
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

  // Opening capital: live recompute while typing; persist on change.
  if ($("openingCapital")) {
    $("openingCapital").addEventListener("input", () => {
      if (!capData) return;
      const v = Number($("openingCapital").value) || 0;
      const pnl = capData.total_pnl || 0;
      drawCapitalTiles({ opening: v, total_pnl: pnl, closing: v + pnl,
        return_pct: v ? (pnl / v * 100) : 0 });
    });
    $("openingCapital").addEventListener("change", () => {
      const v = Number($("openingCapital").value) || 0;
      fetch("/api/journal/capital", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ opening: v, month: capMonth }),
      }).then((r) => r.json()).then(renderCapital).catch(() => {});
    });
  }
  // Save a per-day note on change.
  if ($("capitalDays")) {
    $("capitalDays").addEventListener("change", (e) => {
      const el = e.target;
      if (!el.classList || !el.classList.contains("cap-day-note")) return;
      fetch("/api/journal/daynote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: el.dataset.date, note: el.value }),
      }).catch(() => {});
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
      const v = Number($("openingCapital").value) || 0;
      const r = await fetch("/api/journal/capital", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ opening: v, month: capMonth }),
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
    $("journalTiles").innerHTML = [
      tile("Total P&L", signed(s.total_realized), `${s.trading_days} trading days`, s.total_realized >= 0 ? "good" : "bad"),
      tile("Win rate", (s.win_rate || 0) + "%", `${s.wins}W / ${s.losses}L`, s.win_rate >= 50 ? "good" : "bad"),
      tile("Profit days", s.profit_days, `vs ${s.loss_days} loss days`, "good"),
      tile("Avg / day", signed(s.avg_day), "", s.avg_day >= 0 ? "good" : "bad"),
      tile("Best day", s.best_day ? signed(s.best_day.realized) : "—", s.best_day ? s.best_day.date : "", "good"),
      tile("Worst day", s.worst_day ? signed(s.worst_day.realized) : "—", s.worst_day ? s.worst_day.date : "", "bad"),
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
    const opts = {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => signed(c.raw) } } },
      scales: {
        x: { ticks: { color: "#8b95a7", maxRotation: 60, minRotation: 0 }, grid: { display: false } },
        y: { ticks: { color: "#8b95a7" }, grid: { color: "rgba(128,128,128,.18)" } },
      },
    };
    if (pnlChart) pnlChart.destroy();
    pnlChart = new Chart($("pnlChart"), { type: chartType === "line" ? "line" : "bar", data: { labels, datasets: [dataset] }, options: opts });
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
      const pnl = t.pnl == null ? "—" : signed(t.pnl);
      const cls = t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : "";
      const sideCls = t.side === "LONG" || t.side === "BUY" ? "pos" : "neg";
      const dateTxt = t.date === "unknown" ? "—" : t.date;
      const tag = t.tag || {};
      const on = tag.on_plan;
      return `<tr><td style="text-align:left">${dateTxt}</td><td style="text-align:left">${t.time || ""}</td>
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
    return { id, setup, note: note,
      on_plan: plan === "1" ? 1 : plan === "0" ? 0 : null };
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
    saveTag(rowTag(el.closest("tr"))).catch(() => {});
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
    if (!$("dhanFrom").value) $("dhanFrom").value = fyStartISO();
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
          x: { ticks: { color: "#8b95a7", callback: (v) => (Math.abs(v) >= 1000 ? "₹" + Math.round(v / 1000) + "k" : "₹" + v) },
               grid: { color: "rgba(128,128,128,.18)" } },
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

  const WD_FULL = { Mon: "Mondays", Tue: "Tuesdays", Wed: "Wednesdays",
    Thu: "Thursdays", Fri: "Fridays", Sat: "Saturdays", Sun: "Sundays" };

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
      datasets: [{ label: "P&L", data: wd.map((w) => w.pnl),
        backgroundColor: wd.map((w) => (w.pnl >= 0 ? "#26a69a" : "#ef5350")) }],
    };
    const opts = {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => signed(c.raw), afterLabel: (c) => {
          const w = wd[c.dataIndex]; return `${w.days} days · ${w.win_rate}% win · avg ${signed(w.avg)}`; } } } },
      scales: { x: { ticks: { color: "#8b95a7", font: { weight: "600" } }, grid: { display: false } },
        y: { ticks: { color: "#8b95a7" }, grid: { color: "rgba(128,128,128,.18)" } } },
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
})();
