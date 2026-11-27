"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 0) =>
  n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d });
const fmtSigned = (n) => (n > 0 ? "+" : "") + fmt(n);

let CFG = { symbols: ["NIFTY"], refresh_seconds: 30, source: "nse" };
let state = {
  symbol: "NIFTY", expiry: "", countdown: 30, timer: null, loading: false,
  marketClosed: false
};
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
  day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
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
function _pad2(n) { return String(n).padStart(2, "0"); }
function _fmtDurationSec(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return d > 0 ? `${d}d ${_pad2(h)}h ${_pad2(m)}m` : `${_pad2(h)}:${_pad2(m)}:${_pad2(s)}`;
}
function _updateTopbarTimer() {
  const wrap = $("topbarTimer"), clEl = $("tbTimerClock"), seEl = $("tbTimerSess");
  if (!wrap || !clEl || !seEl) return;
  const now = new Date();
  const p = Object.fromEntries(_IST_FMT.formatToParts(now).map((x) => [x.type, x.value]));
  const hh = +p.hour, mm = +p.minute, ss = +p.second;
  clEl.textContent = `${p.hour}:${p.minute}:${p.second} IST`;
  const st = marketStatus(now);
  wrap.classList.toggle("open", st.open);
  wrap.classList.toggle("closed", !st.open);
  const nowSecOfDay = hh * 3600 + mm * 60 + ss;
  if (st.open) {
    const rem = MARKET_CLOSE_MIN * 60 - nowSecOfDay;
    seEl.textContent = `Closes ${_fmtDurationSec(rem)}`;
    wrap.title = "Market Open (09:15–15:30 IST) · Closes in " + _fmtDurationSec(rem);
    return;
  }
  // Find next valid trading day open (09:15:00 IST)
  const istNowMs = Date.UTC(+p.year, +p.month - 1, +p.day, hh, mm, ss);
  let targetDay = new Date(Date.UTC(+p.year, +p.month - 1, +p.day));
  if (nowSecOfDay >= MARKET_OPEN_MIN * 60) targetDay.setUTCDate(targetDay.getUTCDate() + 1);
  for (let i = 0; i < 14; i++) {
    const wd = targetDay.getUTCDay();
    const dStr = `${targetDay.getUTCFullYear()}-${_pad2(targetDay.getUTCMonth() + 1)}-${_pad2(targetDay.getUTCDate())}`;
    if (wd !== 0 && wd !== 6 && !NSE_HOLIDAYS.has(dStr)) break;
    targetDay.setUTCDate(targetDay.getUTCDate() + 1);
  }
  const openMs = Date.UTC(targetDay.getUTCFullYear(), targetDay.getUTCMonth(), targetDay.getUTCDate(), 9, 15, 0);
  const remSec = Math.max(0, Math.round((openMs - istNowMs) / 1000));
  seEl.textContent = `Opens ${_fmtDurationSec(remSec)}`;
  wrap.title = `${st.reason} · Next NSE/BSE session opens in ${_fmtDurationSec(remSec)}`;
}
setInterval(_updateTopbarTimer, 1000);
document.addEventListener("DOMContentLoaded", _updateTopbarTimer);
function setCountdown(txt) {
  const el = $("oiCountdown");
  if (el) el.textContent = "⏱ " + txt;
  const wrap = $("oiAutoWrap");
  if (wrap) wrap.classList.toggle("off", txt === "off" || txt === "paused");
}
function countdownLabel() {
  if (!marketStatus().open) return "paused";
  return $("autoRefresh").checked ? Math.max(0, state.countdown) + "s" : "off";
}

// ---------------------------------------------------------------- bootstrap
const _cfgPromise = fetch("/api/config").then((r) => r.json()).catch(() => CFG);
window.__authReady = _cfgPromise.then((c) => !c.needs_login).catch(() => true);

async function init() {
  CFG = await _cfgPromise;

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
  initBrokerTabs();
  state.symbol = CFG.symbols[0] || "NIFTY";
  state.countdown = CFG.refresh_seconds;

  const symSel = $("symbol");
  symSel.innerHTML = CFG.symbols.map((s) => `<option>${s}</option>`).join("");
  symSel.value = state.symbol;
  symSel.addEventListener("change", () => {
    state.symbol = symSel.value; state.expiry = "";
    if ($("chainMeta")) $("chainMeta").textContent = "Loading " + symSel.value + "…";
    load(true);
  });
  $("expiry").addEventListener("change", () => {
    state.expiry = $("expiry").value;
    if ($("chainMeta")) $("chainMeta").textContent = "Loading " + state.symbol + " " + state.expiry + "…";
    load(true);
  });
  $("refreshBtn").addEventListener("click", () => load(true));
  $("autoRefresh").addEventListener("change", resetTick);

  handleLoginRedirect();

  if (CFG.needs_login) {
    showLogin();
    return; // wait for the user to log in before polling
  }
  loadProfile();
  await load(true);
  startTick();
  initTicker();
}

// ------------------------------------------------------------ broker picker
let selectedBroker = "fyers";
function selectBrokerTab(b) {
  selectedBroker = b || "fyers";
  document.querySelectorAll("#brokerTabs .broker-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.broker === selectedBroker));
  const oauth = $("loginOAuthPane"), dhanP = $("loginDhanPane"), demoP = $("loginDemoPane");
  if (oauth) oauth.classList.toggle("hidden", selectedBroker === "dhan" || selectedBroker === "mock");
  if (dhanP) dhanP.classList.toggle("hidden", selectedBroker !== "dhan");
  if (demoP) demoP.classList.toggle("hidden", selectedBroker !== "mock");

  const meta = (CFG.brokers && CFG.brokers[selectedBroker]) || {};
  const names = { fyers: "Fyers", dhan: "Dhan", upstox: "Upstox", mock: "Demo Mode" };
  if ($("loginTitle")) $("loginTitle").textContent = "Connect " + (names[selectedBroker] || "Broker");
  const hint = $("loginHint");
  if (hint) { hint.className = "login-hint"; hint.textContent = ""; }

  if (selectedBroker === "fyers" || selectedBroker === "upstox") {
    const btn = $("loginBtn");
    if (btn) {
      btn.textContent = "🔐 Login with " + names[selectedBroker];
      btn.href = "/auth/login?broker=" + encodeURIComponent(selectedBroker);
      btn.classList.toggle("disabled", meta.configured === false);
    }
    if ($("loginDesc")) {
      $("loginDesc").textContent = selectedBroker === "upstox"
        ? "Log in with Upstox to load live option-chain data and your Upstox trades."
        : "Log in with Fyers to load live option-chain data, stock screener, and your Fyers trades.";
    }
    if (meta.configured === false && hint) {
      hint.className = "login-hint err";
      hint.textContent = selectedBroker === "upstox"
        ? "Set UPSTOX_API_KEY and UPSTOX_API_SECRET on the server to enable Upstox login."
        : "Set FYERS_APP_ID and FYERS_SECRET_ID on the server to enable Fyers login.";
    }
  }
}
function initBrokerTabs() {
  const tabs = $("brokerTabs");
  if (!tabs) return;
  const cur = (CFG.source && CFG.source !== "nse") ? CFG.source : "fyers";
  selectBrokerTab(cur);
  tabs.addEventListener("click", (e) => {
    const b = e.target.closest("[data-broker]");
    if (b) selectBrokerTab(b.dataset.broker);
  });
  if ($("loginDhanBtn")) $("loginDhanBtn").addEventListener("click", async () => {
    const tok = ($("loginDhanToken") ? $("loginDhanToken").value : "").trim();
    const cid = ($("loginDhanCid") ? $("loginDhanCid").value : "").trim();
    const hint = $("loginHint"), btn = $("loginDhanBtn");
    if (!tok) { if (hint) { hint.className = "login-hint err"; hint.textContent = "Paste your Dhan access token first."; } return; }
    btn.disabled = true; btn.textContent = "Connecting…";
    if (hint) { hint.className = "login-hint"; hint.textContent = "Verifying token with Dhan…"; }
    try {
      const r = await fetch("/api/journal/dhan/connect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: tok, client_id: cid }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Dhan connection failed");
      location.href = "/?login=ok";
    } catch (err) {
      if (hint) { hint.className = "login-hint err"; hint.textContent = err.message; }
      btn.disabled = false; btn.textContent = "🔐 Connect Dhan";
    }
  });
  if ($("loginDemoBtn")) $("loginDemoBtn").addEventListener("click", async () => {
    const btn = $("loginDemoBtn");
    btn.disabled = true; btn.textContent = "Starting Demo…";
    try {
      await fetch("/api/auth/broker", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ broker: "mock" }),
      });
      location.href = "/";
    } catch (e) {
      btn.disabled = false; btn.textContent = "▶ Enter Demo Mode";
    }
  });
}

// ------------------------------------------------------------ user profile
const _USER_SVG = '<svg viewBox="0 0 24 24"><path d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12Zm0 2.25c-3.9 0-7.5 1.95-7.5 4.5V20h15v-1.25c0-2.55-3.6-4.5-7.5-4.5Z"/></svg>';
function _initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
function _fillAvatar(el, avatarUrl, name) {
  if (!el) return;
  el.textContent = "";
  if (avatarUrl) {
    const img = document.createElement("img");
    img.src = avatarUrl;
    img.alt = "";
    img.onerror = () => {
      el.textContent = _initials(name) || "";
      if (!el.textContent) el.innerHTML = _USER_SVG;
    };
    el.appendChild(img);
    return;
  }
  const ini = _initials(name);
  if (ini) el.textContent = ini;
  else el.innerHTML = _USER_SVG;
}
async function loadProfile() {
  const wrap = $("profileWrap");
  if (!wrap) return;
  try {
    const d = await (await fetch("/api/profile")).json();
    if (!d.logged_in) { wrap.classList.add("hidden"); return; }
    const name = d.name || (d.source ? d.source.toUpperCase() + " Account" : "Trader");
    if ($("profileName")) $("profileName").textContent = name;
    if ($("profileFullName")) $("profileFullName").textContent = name;
    const subBits = [d.broker || (d.source || "").toUpperCase(), d.user_id, d.email].filter(Boolean);
    if ($("profileSub")) $("profileSub").textContent = subBits.join(" · ");
    const st = $("profileStatusBadge");
    if (st) {
      st.textContent = d.token_expired ? "Token Expired — Reconnect" : "Connected";
      st.className = "prof-badge " + (d.token_expired ? "err" : "ok");
    }
    const det = $("profileDetails");
    if (det) {
      const escP = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[m]));
      const rows = Array.isArray(d.details) ? d.details : [];
      det.innerHTML = rows.map(([k, v]) => `<div class="prof-det"><div class="k">${escP(k)}</div><div class="v" title="${escP(v)}">${escP(v || "—")}</div></div>`).join("");
      det.classList.toggle("hidden", rows.length === 0);
    }
    if ($("profCustomName")) $("profCustomName").value = d.needs_custom_name ? "" : name;
    if ($("profCustomAvatar")) $("profCustomAvatar").value = d.avatar || "";
    if ($("profSetNameBtn")) $("profSetNameBtn").classList.toggle("hidden", !d.needs_custom_name);
    _fillAvatar($("profileAvatar"), d.avatar, name);
    _fillAvatar($("profileAvatarLg"), d.avatar, name);
    const canOAuth = d.source === "fyers" || d.source === "upstox";
    const canReconnect = canOAuth || d.source === "dhan";
    const canLogout = canReconnect;
    if ($("profileReconnect")) {
      $("profileReconnect").classList.toggle("hidden", !canReconnect);
      $("profileReconnect").dataset.src = d.source || "";
      $("profileReconnect").textContent = d.token_expired ? "🔄 Reconnect Now" : "🔄 Reconnect";
    }
    if ($("logoutBtn")) $("logoutBtn").classList.toggle("hidden", !canLogout);
    if ($("switchBrokerBtn")) $("switchBrokerBtn").classList.remove("hidden");
    wrap.classList.remove("hidden");
  } catch (e) { /* keep hidden on error */ }
}
(function initProfileMenu() {
  const btn = $("profileBtn"), menu = $("profileMenu"), logout = $("logoutBtn"), sw = $("switchBrokerBtn"), rc = $("profileReconnect");
  if (btn && menu) {
    btn.addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); });
    document.addEventListener("click", (e) => {
      if (!menu.classList.contains("hidden") && !e.target.closest("#profileWrap")) menu.classList.add("hidden");
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.classList.add("hidden"); });
  }
  if ($("profEditToggle") && $("profileCustomForm")) {
    $("profEditToggle").addEventListener("click", (e) => {
      e.stopPropagation();
      $("profileCustomForm").classList.toggle("hidden");
    });
  }
  if ($("profSetNameBtn") && $("profileCustomForm")) {
    $("profSetNameBtn").addEventListener("click", (e) => {
      e.stopPropagation();
      $("profileCustomForm").classList.remove("hidden");
      $("profCustomName")?.focus();
    });
  }
  if ($("profCustomFile")) {
    $("profCustomFile").addEventListener("change", (e) => {
      const f = e.target.files?.[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          const sz = 96, c = document.createElement("canvas");
          c.width = sz; c.height = sz;
          const ctx = c.getContext("2d");
          const s = Math.min(img.width, img.height);
          const sx = (img.width - s) / 2, sy = (img.height - s) / 2;
          ctx.drawImage(img, sx, sy, s, s, 0, 0, sz, sz);
          if ($("profCustomAvatar")) $("profCustomAvatar").value = c.toDataURL("image/jpeg", 0.82);
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(f);
    });
  }
  if ($("profCustomSave")) {
    $("profCustomSave").addEventListener("click", async () => {
      const name = ($("profCustomName")?.value || "").trim();
      const avatar = ($("profCustomAvatar")?.value || "").trim();
      try {
        await fetch("/api/profile/custom", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, avatar }),
        });
        if ($("profileCustomForm")) $("profileCustomForm").classList.add("hidden");
        loadProfile();
      } catch (e) {}
    });
  }
  if (rc) {
    rc.addEventListener("click", () => {
      const s = rc.dataset.src || CFG.source;
      if (s === "fyers" || s === "upstox") { window.location.href = "/auth/login"; return; }
      if (menu) menu.classList.add("hidden");
      selectBrokerTab("dhan");
      showLogin();
    });
  }
  async function doSignOut() {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch (e) {}
    if (menu) menu.classList.add("hidden");
    if ($("profileWrap")) $("profileWrap").classList.add("hidden");
    const cur = (CFG.source && CFG.source !== "nse" && CFG.source !== "mock") ? CFG.source : "fyers";
    selectBrokerTab(cur);
    showLogin();
  }
  if (sw) sw.addEventListener("click", doSignOut);
  if (logout) {
    logout.addEventListener("click", async () => {
      logout.disabled = true;
      await doSignOut();
      logout.disabled = false;
    });
  }
})();

// ------------------------------------------------------------ ticker tape
let tickerPaused = false;
let tickerKeys = "";
function initTicker() {
  const t = $("tickerToggle");
  if (t) t.addEventListener("click", () => { tickerPaused = !tickerPaused; applyTickerPaused(); });
  loadTicker();
  setInterval(() => {
    if (document.hidden) return;
    // during market hours refresh often; when closed just keep last values
    if (typeof marketStatus === "function" && !marketStatus().open && tickerKeys) return;
    loadTicker();
  }, 20000);
}
function applyTickerPaused() {
  const tr = $("tickerTrack"), btn = $("tickerToggle");
  if (tr) tr.classList.toggle("paused", tickerPaused);
  if (btn) { btn.textContent = tickerPaused ? "▶" : "⏸"; btn.title = tickerPaused ? "Resume" : "Pause"; }
}
async function loadTicker() {
  try {
    const d = await (await fetch("/api/ticker")).json();
    const items = [];
    (d.indices || []).forEach((i) => items.push({ key: "idx:" + i.name, label: i.name, ltp: i.ltp, chg: i.chg_pct, isIdx: true }));
    (d.stocks || []).forEach((s) => items.push({ key: "stk:" + s.symbol, label: s.symbol, ltp: s.ltp, chg: s.chg_pct }));
    if (items.length) { $("tickerBar").classList.remove("hidden"); renderTicker(items); }
  } catch (e) { /* keep last */ }
}
function tkItemHTML(it) {
  const up = it.chg >= 0;
  return `<span class="tk-item${it.isIdx ? " tk-idx" : ""}" data-key="${it.key}">` +
    `<b>${it.label}</b> <span class="tk-val">${fmt(it.ltp, 2)}</span>` +
    `<span class="tk-chg ${up ? "up" : "down"}">${up ? "▲ +" : "▼ "}${it.chg}%</span></span>`;
}
function renderTicker(items) {
  const track = $("tickerTrack");
  if (!track) return;
  const keys = items.map((i) => i.key).join(",");
  if (keys === tickerKeys && track.children.length) {
    // same symbols → update values in place so the scroll never jumps
    items.forEach((it) => {
      track.querySelectorAll(`[data-key="${CSS.escape(it.key)}"]`).forEach((el) => {
        const up = it.chg >= 0;
        el.querySelector(".tk-val").textContent = fmt(it.ltp, 2);
        const c = el.querySelector(".tk-chg");
        c.textContent = (up ? "▲ +" : "▼ ") + it.chg + "%";
        c.className = "tk-chg " + (up ? "up" : "down");
      });
    });
    return;
  }
  tickerKeys = keys;
  const one = items.map(tkItemHTML).join("");
  // Repeat the set enough times that ONE half always spans the screen, so the
  // -50% loop wraps seamlessly even when only a few items have loaded.
  const perHalf = Math.max(1, Math.ceil(14 / items.length));
  const half = one.repeat(perHalf);
  track.innerHTML = half + half;        // two halves → seamless loop at -50%
  const unit = items.length * perHalf;  // items per half
  // deterministic speed (~2.6s per item) — robust even before layout settles
  track.style.animationDuration = Math.max(24, Math.round(unit * 2.6)) + "s";
  applyTickerPaused();
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
  document.body.classList.add("needs-login");
  $("loginBox").classList.remove("hidden");
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
}
function hideLogin() {
  document.body.classList.remove("needs-login");
  $("loginBox").classList.add("hidden");
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
    setCountdown(countdownLabel());
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
    if (!$("autoRefresh").checked) { setCountdown("off"); return; }
    state.countdown -= 1;
    if (state.countdown <= 0) { load(false); }
    setCountdown(Math.max(0, state.countdown) + "s");
  }, 1000);
}
function resetTick() {
  state.countdown = CFG.refresh_seconds;
  setCountdown(countdownLabel());
}
function showClosed(mkt) {
  setCountdown("paused");
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

// ---------- theme switcher (dark / light / emerald / sand) ----------
(function () {
  const picker = document.getElementById("themePicker");
  if (!picker) return;
  const THEMES = ["dark", "light", "emerald", "sand"];
  const EXTRA = ["light", "emerald", "sand"];
  const cur = () => {
    for (const t of EXTRA) if (document.documentElement.classList.contains(t)) return t;
    return "dark";
  };
  const sync = () => {
    const active = cur();
    picker.querySelectorAll("[data-theme]").forEach((b) =>
      b.classList.toggle("active", b.dataset.theme === active));
  };
  const apply = (t) => {
    if (!THEMES.includes(t)) t = "sand";
    EXTRA.forEach((c) => document.documentElement.classList.remove(c));
    if (t !== "dark") document.documentElement.classList.add(t);
    try { localStorage.setItem("oi-theme", t); } catch (e) { }
    sync();
    try { window.dispatchEvent(new CustomEvent("oi-theme-change", { detail: t })); } catch (e) { }
  };
  try { if (!localStorage.getItem("oi-theme") && cur() === "dark") document.documentElement.classList.add("sand"); } catch (e) { }
  sync();
  picker.addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme]");
    if (b) apply(b.dataset.theme);
  });
})();

init();
