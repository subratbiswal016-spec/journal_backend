"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 0) =>
  n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d });
const fmtSigned = (n) => (n > 0 ? "+" : "") + fmt(n);

let CFG = { symbols: ["NIFTY"], refresh_seconds: 30, source: "nse" };
let state = { symbol: "NIFTY", expiry: "", countdown: 30, timer: null, loading: false,
              marketClosed: false };
let chgChart = null;
let oiChart = null;

// ---------------------------------------------------------- market hours
// NSE/BSE equity hours: Mon–Fri 09:15–15:30 IST, minus exchange holidays.
// Outside these, prices don't change, so we pause auto-refresh to save calls.
const MARKET_OPEN_MIN = 9 * 60 + 15;    // 09:15 IST
const MARKET_CLOSE_MIN = 15 * 60 + 30;  // 15:30 IST
// NSE weekday trading holidays (full-day closures). Update yearly from
// https://www.nseindia.com/resources/exchange-communication-holidays .
// Weekends are handled automatically, so only weekday holidays are listed.
const NSE_HOLIDAYS = new Set([
  // 2026
  "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
  "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14",
  "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
]);
// Read the wall clock in IST regardless of the viewer's own timezone.
const _IST_FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata", weekday: "short", year: "numeric", month: "2-digit",
  day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
function marketStatus(now = new Date()) {
  const p = Object.fromEntries(_IST_FMT.formatToParts(now).map((x) => [x.type, x.value]));
  const date = `${p.year}-${p.month}-${p.day}`;
  const mins = (+p.hour) * 60 + (+p.minute);
  if (p.weekday === "Sat" || p.weekday === "Sun") return { open: false, reason: "Weekend" };
  if (NSE_HOLIDAYS.has(date)) return { open: false, reason: "Exchange holiday" };
  if (mins < MARKET_OPEN_MIN) return { open: false, reason: "Pre-market" };
  if (mins >= MARKET_CLOSE_MIN) return { open: false, reason: "After hours" };
  return { open: true, reason: "Market open" };
}
function countdownLabel() {
  if (!marketStatus().open) return "paused";
  return $("autoRefresh").checked ? Math.max(0, state.countdown) + "s" : "off";
}

// ---------------------------------------------------------------- bootstrap
async function init() {
  try {
    CFG = await (await fetch("/api/config")).json();
  } catch (e) { /* use defaults */ }

  $("sourceBadge").textContent = CFG.source.toUpperCase();
  const SRC = {
    nse: ["NSE public option chain", "Unofficial."],
    fyers: ["Fyers API (official)", "Official broker feed."],
    upstox: ["Upstox API (official)", "Official broker feed."],
    mock: ["Demo data", "Synthetic — not real."],
  };
  const [srcLabel, srcNote] = SRC[CFG.source] || [CFG.source, ""];
  $("footSource").textContent = srcLabel;
  $("footNote").textContent = srcNote;
  if (CFG.login_label) {
    $("loginBtn").textContent = "🔐 " + CFG.login_label;
    $("loginTitle").textContent = "Connect " + (CFG.source || "broker").replace(/^\w/, c => c.toUpperCase());
  }
  state.symbol = CFG.symbols[0] || "NIFTY";
  state.countdown = CFG.refresh_seconds;

  const symSel = $("symbol");
  symSel.innerHTML = CFG.symbols.map((s) => `<option>${s}</option>`).join("");
  symSel.value = state.symbol;
  symSel.addEventListener("change", () => { state.symbol = symSel.value; state.expiry = ""; load(true); });
  $("expiry").addEventListener("change", () => { state.expiry = $("expiry").value; load(true); });
  $("refreshBtn").addEventListener("click", () => load(true));
  $("autoRefresh").addEventListener("change", resetTick);

  handleLoginRedirect();

  if (CFG.needs_login) {
    showLogin();
    return; // wait for the user to log in before polling
  }
  await load(true);
  startTick();
}

// Read ?login=ok / ?login_error=... from the Upstox callback redirect.
function handleLoginRedirect() {
  const p = new URLSearchParams(location.search);
  if (p.has("login_error")) {
    const hint = $("loginHint");
    hint.className = "login-hint err";
    hint.textContent = "Login failed — " + p.get("login_error");
  }
  if (p.get("login") === "ok" || p.has("login_error")) {
    history.replaceState({}, "", location.pathname); // clean the URL
  }
}

function showLogin() {
  $("loginBox").classList.remove("hidden");
  document.querySelector("main").style.display = "none";
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
}
function hideLogin() {
  $("loginBox").classList.add("hidden");
  document.querySelector("main").style.display = "";
}

// ---------------------------------------------------------------- data load
async function load(force = false) {
  if (state.loading) return;
  state.loading = true;
  $("refreshBtn").classList.add("busy");
  try {
    const url = `/api/snapshot?symbol=${encodeURIComponent(state.symbol)}` +
      (state.expiry ? `&expiry=${encodeURIComponent(state.expiry)}` : "") +
      (force ? "&force=true" : "");
    const res = await fetch(url);
    if (res.status === 401) {
      const err = await res.json().catch(() => ({}));
      if ((err.detail || "").includes("login")) { showLogin(); return; }
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    hideLogin();
    render(data);
    // Keep the "market closed" note up while closed; only show stale/error here.
    if (marketStatus().open || data.stale_error) {
      showStatus(data.stale_error
        ? `Showing cached data — last refresh failed: ${data.stale_error}` : "", "error");
    }
  } catch (e) {
    showStatus(`Could not load data: ${e.message}. NSE may be throttling — retrying on next cycle.`, "error");
  } finally {
    state.loading = false;
    state.countdown = CFG.refresh_seconds;
    $("refreshBtn").classList.remove("busy");
    $("refreshBtn").innerHTML = `⟳ <span id="countdown">${countdownLabel()}</span>`;
  }
}

// ---------------------------------------------------------------- render
function render(d) {
  const s = d.summary || {};
  // expiry dropdown
  const exSel = $("expiry");
  if (d.expiries && d.expiries.length) {
    const cur = state.expiry || d.expiry;
    exSel.innerHTML = d.expiries.map((e) => `<option${e === cur ? " selected" : ""}>${e}</option>`).join("");
    state.expiry = exSel.value;
  }

  renderTiles(d, s);
  renderChainTable(d, s);
  renderFutures(d);
  renderCharts(d, s);

  $("chainMeta").textContent = `${d.symbol} · ${d.expiry} · ${d.rows.length} strikes`;
  $("lastUpdated").textContent = "Updated " + (d.timestamp || new Date().toLocaleTimeString());
}

function tile(label, value, sub = "", cls = "neutral") {
  return `<div class="tile ${cls}"><div class="label">${label}</div>` +
    `<div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

// Tile with an animated "flowing" fill bar showing its share of total OI.
function tileFill(label, value, sub, pct, kind) {
  const w = Math.max(3, Math.min(100, pct));
  return `<div class="tile ${kind === "call" ? "bad" : "good"}">
    <div class="label">${label}</div>
    <div class="value">${value}</div>
    <div class="sub">${sub}</div>
    <div class="fillbar"><span class="${kind}" style="width:${w}%"></span><em class="pct">${pct}%</em></div>
  </div>`;
}

// Colored day-change for the Spot tile, broker-style: "▲ +271.32  +0.33%".
function spotChangeHtml(d) {
  const pct = d.spot_change_pct, abs = d.spot_change;
  if (pct == null && abs == null) return "";
  const up = (pct != null ? pct : abs) >= 0;
  const col = up ? "var(--put)" : "var(--call)";
  const arrow = up ? "▲" : "▼";
  const absTxt = abs == null ? "" : (abs >= 0 ? "+" : "") + fmt(abs, 2);
  const pctTxt = pct == null ? "" : `  ${up ? "+" : ""}${fmt(pct, 2)}%`;
  return `<br><span style="color:${col};font-weight:700">${arrow} ${absTxt}${pctTxt}</span>`;
}

function renderTiles(d, s) {
  const biasPill = `<span class="pill ${s.bias}">${s.bias || "—"}</span>`;
  const pcrCls = s.pcr >= 1.3 ? "good" : s.pcr <= 0.7 ? "bad" : "neutral";
  const totOi = (s.total_ce_oi + s.total_pe_oi) || 1;
  const callPct = Math.round((s.total_ce_oi / totOi) * 100);
  const putPct = 100 - callPct;
  $("tiles").innerHTML = [
    tile("Spot", fmt(d.spot, 2), `ATM ${fmt(s.atm)}${spotChangeHtml(d)}`),
    tile("PCR", fmt(s.pcr, 2), biasPill, pcrCls),
    tile("Max Pain", fmt(s.max_pain), `vs spot ${fmtSigned(Math.round(d.spot - s.max_pain))}`),
    tile("Support (max Put OI)", fmt(s.support), "", "good"),
    tile("Resistance (max Call OI)", fmt(s.resistance), "", "bad"),
    tileFill("Total Call OI", fmt(s.total_ce_oi), `Chg ${fmtSigned(s.total_ce_chg_oi)}`, callPct, "call"),
    tileFill("Total Put OI", fmt(s.total_pe_oi), `Chg ${fmtSigned(s.total_pe_chg_oi)}`, putPct, "put"),
  ].join("");
}

function renderChainTable(d, s) {
  const tbody = $("chainTable").querySelector("tbody");
  const maxOi = Math.max(1, ...d.rows.flatMap((r) => [r.ce_oi, r.pe_oi]));
  const maxChg = Math.max(1, ...d.rows.flatMap((r) => [Math.abs(r.ce_chg_oi), Math.abs(r.pe_chg_oi)]));
  const bar = (v, max) => `<span class="bar" style="width:${Math.min(100, (Math.abs(v) / max) * 100)}%"></span>`;
  const cell = (v, cls, max, dec = 0, signed = false) =>
    `<td class="${cls}">${bar(v, max)}<span class="num">${signed ? fmtSigned(v) : fmt(v, dec)}</span></td>`;

  tbody.innerHTML = d.rows.map((r) => {
    const isAtm = r.strike === s.atm;
    const ceChgCls = "ce-chg oi " + (r.ce_chg_oi < 0 ? "pos" : r.ce_chg_oi > 0 ? "neg" : "");
    const peChgCls = "pe-chg oi " + (r.pe_chg_oi < 0 ? "neg" : r.pe_chg_oi > 0 ? "pos" : "");
    return `<tr class="${isAtm ? "atm" : ""}">
      ${cell(r.ce_oi, "ce-oi oi call-cell", maxOi)}
      ${cell(r.ce_chg_oi, ceChgCls + " call-cell", maxChg, 0, true)}
      <td class="call-cell">${fmt(r.ce_iv, 1)}</td>
      <td class="call-cell">${fmt(r.ce_ltp, 2)}</td>
      <td class="strike">${fmt(r.strike)}</td>
      <td class="put-cell">${fmt(r.pe_ltp, 2)}</td>
      <td class="put-cell">${fmt(r.pe_iv, 1)}</td>
      ${cell(r.pe_chg_oi, peChgCls + " put-cell", maxChg, 0, true)}
      ${cell(r.pe_oi, "pe-oi oi put-cell", maxOi)}
    </tr>`;
  }).join("");
}

function renderFutures(d) {
  const tbody = $("futTable").querySelector("tbody");
  if (!d.futures || !d.futures.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--muted)">No futures data available</td></tr>`;
    return;
  }
  tbody.innerHTML = d.futures.map((f) => {
    const cls = f.chg_oi < 0 ? "pos" : f.chg_oi > 0 ? "neg" : "";
    return `<tr><td style="text-align:left">${f.expiry}</td><td>${fmt(f.last_price, 2)}</td>` +
      `<td>${fmt(f.oi)}</td><td class="oi ${cls}"><span class="num">${fmtSigned(f.chg_oi)}</span></td></tr>`;
  }).join("");
}

// ---------------------------------------------------------------- charts
function renderCharts(d, s) {
  const rows = d.rows;
  // focus the charts around ATM (±15 strikes) so bars stay readable
  let rngRows = rows;
  const atmIdx = rows.findIndex((r) => r.strike === s.atm);
  if (atmIdx >= 0) rngRows = rows.slice(Math.max(0, atmIdx - 15), atmIdx + 16);

  const labels = rngRows.map((r) => r.strike);
  const common = {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { labels: { color: "#7a8498" } } },
    scales: {
      x: { ticks: { color: "#8b95a7", maxRotation: 90, minRotation: 45 }, grid: { color: "rgba(128,128,128,.15)" } },
      y: { ticks: { color: "#8b95a7" }, grid: { color: "rgba(128,128,128,.18)" } },
    },
  };

  const chgData = {
    labels,
    datasets: [
      { label: "Call Chg OI", data: rngRows.map((r) => r.ce_chg_oi), backgroundColor: "#ef5350" },
      { label: "Put Chg OI", data: rngRows.map((r) => r.pe_chg_oi), backgroundColor: "#26a69a" },
    ],
  };
  const oiData = {
    labels,
    datasets: [
      { label: "Call OI", data: rngRows.map((r) => r.ce_oi), backgroundColor: "#ef5350" },
      { label: "Put OI", data: rngRows.map((r) => r.pe_oi), backgroundColor: "#26a69a" },
    ],
  };

  if (!chgChart) chgChart = new Chart($("chgOiChart"), { type: "bar", data: chgData, options: common });
  else { chgChart.data = chgData; chgChart.update(); }

  if (!oiChart) oiChart = new Chart($("oiChart"), { type: "bar", data: oiData, options: common });
  else { oiChart.data = oiData; oiChart.update(); }
}

// ---------------------------------------------------------------- ticking
function startTick() {
  resetTick();
  state.timer = setInterval(() => {
    const mkt = marketStatus();
    if (!mkt.open) {                       // market closed → pause polling
      if (!state.marketClosed) { state.marketClosed = true; showClosed(mkt); }
      return;
    }
    if (state.marketClosed) {              // market just reopened → refresh now
      state.marketClosed = false;
      showStatus("", "");
      resetTick();
      load(false);
      return;
    }
    if (!$("autoRefresh").checked) { $("countdown").textContent = "off"; return; }
    state.countdown -= 1;
    if (state.countdown <= 0) { load(false); }
    $("countdown").textContent = Math.max(0, state.countdown) + "s";
  }, 1000);
}
function resetTick() {
  state.countdown = CFG.refresh_seconds;
  $("countdown").textContent = countdownLabel();
}
function showClosed(mkt) {
  $("countdown").textContent = "paused";
  const detail = {
    "Weekend": "Market closed (weekend)",
    "Exchange holiday": "Market closed (exchange holiday)",
    "Pre-market": "Pre-market — NSE opens 9:15 AM IST",
    "After hours": "Market closed for the day (after 3:30 PM IST)",
  }[mkt.reason] || "Market closed";
  showStatus(detail + " — auto-refresh paused. Press ⟳ to refresh manually.", "info");
}

function showStatus(msg, cls) {
  const bar = $("statusBar");
  if (!msg) { bar.classList.add("hidden"); return; }
  bar.className = "statusbar " + (cls || "");
  bar.textContent = msg;
}

// ---------- theme toggle (light / dark) ----------
(function () {
  const btn = document.getElementById("themeBtn");
  if (!btn) return;
  const setIcon = () => { btn.textContent = document.documentElement.classList.contains("light") ? "☀️" : "🌙"; };
  setIcon();
  btn.addEventListener("click", () => {
    const light = document.documentElement.classList.toggle("light");
    try { localStorage.setItem("oi-theme", light ? "light" : "dark"); } catch (e) {}
    setIcon();
  });
})();

init();
