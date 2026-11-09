/* Dark Fiber Core Manager — single-page frontend, no build step. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const viewEl = $("#view");
  const searchEl = $("#search");
  const modalRoot = $("#modal-root");

  const FALLBACK_PURPOSES = [
    "Dark Fiber Lease",
    "Backbone Transmisi",
    "Koneksi Data Center",
    "Lastmile Aggregation",
    "Disaster Recovery Link",
  ];
  const FALLBACK_FIBER = ["#3b82f6", "#f97316", "#22c55e", "#a16207", "#64748b", "#e2e8f0", "#ef4444", "#1f2937", "#eab308", "#8b5cf6", "#f43f5e", "#06b6d4"];

  const state = {
    meta: null,
    summary: null,
    cables: [],
    tenants: [],
    leases: [],
    plants: [],
    cable: null,
    tenant: null,
    coreIndex: new Map(),
    selection: new Map(),
    lastPicked: null,
    routeKey: "",
    search: "",
    cableFilter: "all",
    coreFilter: "all",
    leaseFilter: "all",
    sheetStatus: null,
    sheetEntries: [],
    sheetTab: "",
    sheetQuery: "",
    sheetSyncing: false,
    sheetLoadedGid: "",
    loading: true,
    searchResults: [],
    searchQuery: "",
    searchBusy: false,
    searchJump: null,
  };

  // ---------------------------------------------------------------- helpers
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const num = (n) => Number(n || 0).toLocaleString("id-ID");
  const pct = (n) => `${Number(n || 0).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;

  function todayISO() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function addDaysISO(isoDate, days) {
    const [y, m, d] = isoDate.split("-").map(Number);
    const dt = new Date(y, m - 1, d + days);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
  }

  function fmtDate(isoDate) {
    if (!isoDate) return "–";
    const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
    if (!y || !m || !d) return isoDate;
    return new Date(y, m - 1, d).toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" });
  }

  function fmtDateTime(value) {
    if (!value) return "–";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" });
  }

  function compactRanges(numbers) {
    const nums = [...numbers].sort((a, b) => a - b);
    if (!nums.length) return "";
    const parts = [];
    let start = nums[0];
    let prev = nums[0];
    for (let i = 1; i < nums.length; i += 1) {
      const n = nums[i];
      if (n === prev + 1) {
        prev = n;
      } else {
        parts.push(start === prev ? `${start}` : `${start}–${prev}`);
        start = prev = n;
      }
    }
    parts.push(start === prev ? `${start}` : `${start}–${prev}`);
    return parts.join(", ");
  }

  const STATE_LABEL = {
    active: ["Aktif", "ok"],
    expiring: ["Segera berakhir", "warn"],
    expired: ["Kedaluwarsa", "bad"],
    reserved: ["Reservasi", "info"],
    released: ["Dilepas", "muted"],
  };

  function stateTag(value) {
    const [label, tone] = STATE_LABEL[value] || [value, "muted"];
    return `<span class="state state-${tone}"><span class="dot"></span>${label}</span>`;
  }

  function cableStatusTag(status) {
    const map = { active: ["Aktif", "ok"], maintenance: ["Maintenance", "warn"], planned: ["Rencana", "muted"] };
    const [label, tone] = map[status] || [status, "muted"];
    return `<span class="state state-${tone}"><span class="dot"></span>${label}</span>`;
  }

  function tenantStatusTag(status) {
    const map = { active: ["Aktif", "ok"], suspended: ["Suspend", "bad"], inactive: ["Nonaktif", "muted"] };
    const [label, tone] = map[status] || [status, "muted"];
    return `<span class="state state-${tone}"><span class="dot"></span>${label}</span>`;
  }

  function remainingLabel(lease) {
    if (lease.state === "expired") return `Lewat ${Math.abs(lease.days_left)} hari`;
    if (lease.state === "reserved") return `Mulai ${fmtDate(lease.start_date)}`;
    return `${lease.days_left} hari lagi`;
  }

  function remainingTone(lease) {
    if (lease.state === "expired") return "bad";
    if (lease.state === "expiring") return "warn";
    if (lease.state === "reserved") return "info";
    return "";
  }

  function initials(name) {
    return String(name || "?")
      .replace(/^(PT|CV|UD|Tbk)\s+/i, "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join("")
      .toUpperCase();
  }

  function shortOtb(cable) {
    const panel = String((cable && (cable.panel_code || cable.name)) || "").trim();
    const tab = String((cable && cable.tab_name) || "").trim();
    if (!panel) return cable ? cable.code || "—" : "—";
    const baseMatch = panel.match(/^(OTB|ODC|MDF|PATCHPANEL|MMR|POE|HH)\s*(\d+(?:\.\d+)?)?/i);
    let base;
    if (baseMatch) {
      const dev = baseMatch[1].toUpperCase();
      const num = baseMatch[2] || "";
      // OBC tanpa nomor, e.g. ODC JARAKOSTA → keep second word
      const second = panel.split(/\s+/)[1] || "";
      if (!num && /^[A-Z][A-Z-]+$/i.test(second) && second.length > 2) base = `${dev} ${second.toUpperCase()}`;
      else base = num ? `${dev} ${num}` : dev;
    } else {
      base = panel.split(/\s+/).slice(0, 2).join(" ");
    }
    // Lokasi: JK5 / JK2 / JK1 dari panel dulu, baru tab
    let loc = null;
    const fromPanel = panel.match(/\bJK[- ]?(\d+)\b/i);
    if (fromPanel) loc = `JK${fromPanel[1]}`;
    else {
      const fromTab = tab.match(/\bJK[- ]?(\d+)\b/i);
      if (fromTab) loc = `JK${fromTab[1]}`;
    }
    if (!loc && tab) loc = tab.replace(/^(RAK|OTB)\s+/i, "").slice(0, 14).trim();
    return loc ? `${base} ${loc}` : base;
  }

  function fiberColors() {
    return (state.meta && state.meta.fiber_colors ? state.meta.fiber_colors : FALLBACK_FIBER.map((hex) => ({ hex }))).map((c) => c.hex || c);
  }

  function purposes() {
    return state.meta && state.meta.purposes ? state.meta.purposes : FALLBACK_PURPOSES;
  }

  function searchMatch(...values) {
    if (!state.search) return true;
    const hay = values.filter(Boolean).join(" ").toLowerCase();
    return hay.includes(state.search);
  }

  // ---------------------------------------------------------- global search
  function cableByPanel(gid, panel) {
    if (!gid || !panel) return null;
    return state.cables.find((c) => c.gid === gid && c.panel_code === panel) || null;
  }

  function hl(text, q) {
    const t = String(text ?? "");
    if (!q) return esc(t);
    const i = t.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(t);
    return esc(t.slice(0, i)) + "<mark>" + esc(t.slice(i, i + q.length)) + "</mark>" + esc(t.slice(i + q.length));
  }

  function renderSearchPop() {
    const pop = $("#search-pop");
    if (!pop) return;
    const q = state.searchQuery.trim();
    if (q.length < 2) {
      pop.hidden = true;
      pop.innerHTML = "";
      searchEl.setAttribute("aria-expanded", "false");
      return;
    }
    const cableHits = (state.cables || [])
      .filter((c) => {
        const hay = [c.name, c.code, c.route, c.tab_name, c.panel_code, c.section].filter(Boolean).join(" ").toLowerCase();
        return hay.includes(q.toLowerCase());
      })
      .slice(0, 4);
    const sheetHits = (state.searchResults || []).slice(0, 10);
    pop.innerHTML =
      (state.searchBusy ? `<div class="sp-head">Mencari…</div>` : "") +
      (cableHits.length
        ? `<div class="sp-head">Kabel backbone</div>` +
          cableHits
            .map(
              (c) => `<a class="sp-row" href="#/cables/${c.id}">
                <span class="sp-main"><span class="sp-title">${hl(c.panel_code || c.name, q)}</span><span class="sp-sub">${esc(c.tab_name || "")} · ${num(c.core_count)} core · ${num(c.sheet_used || 0)}/${num(c.sheet_total || 0)} port sheet</span></span>
                <span class="sp-arrow">›</span>
              </a>`,
            )
            .join("")
        : "") +
      (sheetHits.length
        ? `<div class="sp-head">Port OTB (Google Sheet) · ${num(state.searchResults.length)} hasil</div>` +
          sheetHits
            .map((r) => {
              // Klik → mendarat di Peta OTB tab gedung itu, tabel ke-filter
              // ke kata kunci, jadi langsung kelihatan Port berapa + Tujuan + Ket.
              return `<a class="sp-row" href="#/otb/${esc(r.gid)}" data-goto-gid="${esc(r.gid)}" data-goto-q="${esc(q)}">
                <span class="sp-main">
                  <span class="sp-title">${esc(r.port || "—")}</span>
                  <span class="sp-sub">${esc(r.tab_name || r.gid)} · ${esc(r.panel || "")}</span>
                  <span class="sp-note">→ ${hl(r.destination || "—", q)}${r.note ? ` · ${hl(r.note, q)}` : ""}</span>
                </span>
                <span class="sp-arrow">›</span>
              </a>`;
            })
            .join("") +
          `<a class="sp-more" href="#/otb">Lihat semua di Peta OTB ›</a>`
        : "") +
      (cableHits.length === 0 && sheetHits.length === 0 && !state.searchBusy
        ? `<div class="sp-empty">Tidak ditemukan untuk “${esc(q)}” · coba OTB 96.1, alibaba, Port 11-12</div>`
        : "");
    pop.hidden = false;
    searchEl.setAttribute("aria-expanded", "true");
  }

  let _searchTimer = null;
  async function runGlobalSearch() {
    const q = searchEl.value.trim();
    state.searchQuery = q;
    state.search = q.toLowerCase();
    clearTimeout(_searchTimer);
    if (q.length < 2) {
      state.searchResults = [];
      renderSearchPop();
      renderView();
      syncSelection();
      return;
    }
    state.searchBusy = true;
    renderSearchPop();
    _searchTimer = setTimeout(async () => {
      try {
        const data = await api(`/sheet/search?q=${encodeURIComponent(q)}`);
        if (state.searchQuery.trim() !== q) return; // stale
        state.searchResults = data.results || [];
      } catch {
        state.searchResults = [];
      } finally {
        state.searchBusy = false;
        renderSearchPop();
      }
    }, 160);
    renderView();
    syncSelection();
  }

  function hideSearchPop() {
    const pop = $("#search-pop");
    if (pop) {
      pop.hidden = true;
      pop.innerHTML = "";
    }
    searchEl.setAttribute("aria-expanded", "false");
  }

  async function api(path, options = {}) {
    const res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let data = {};
    try {
      data = await res.json();
    } catch {
      data = {};
    }
    if (!res.ok) throw new Error(data.error || `Permintaan gagal (${res.status})`);
    return data;
  }

  function toast(message, kind = "ok") {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast show${kind === "bad" ? " bad" : kind === "warn" ? " warn" : ""}`;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      el.className = "toast";
    }, 3600);
  }

  function emptyState(title, sub, icon = "i-layers") {
    return `<div class="empty">
      <svg class="ico"><use href="#${icon}"/></svg>
      <p class="empty-title">${esc(title)}</p>
      ${sub ? `<p class="empty-sub">${esc(sub)}</p>` : ""}
    </div>`;
  }

  function loadingState() {
    return `<div class="loading">Memuat data…</div>`;
  }

  // ---------------------------------------------------------------- data
  async function refreshBasics() {
    const [summary, tenants, leases] = await Promise.all([api("/summary"), api("/tenants"), api("/leases")]);
    state.summary = summary;
    state.cables = summary.cables;
    state.plants = summary.plants || [];
    state.tenants = tenants.tenants;
    state.leases = leases.leases;
    updateSidebar();
  }

  function updateSidebar() {
    const totals = state.summary ? state.summary.totals : null;
    if (!totals) return;
    $("#side-count").textContent = num(totals.used_combined ?? totals.leased);
    $("#side-total").textContent = num(totals.capacity);
    $("#side-pct").textContent = pct(totals.utilization_combined ?? totals.utilization);
    $("#side-fill").style.width = `${Math.min(100, totals.utilization_combined ?? totals.utilization)}%`;
  }

  // ---------------------------------------------------------------- routing
  function getRoute() {
    const raw = location.hash.replace(/^#\/?/, "") || "overview";
    const [name, id] = raw.split("/");
    return { name: name || "overview", id: id || null };
  }

  function updateChrome(route) {
    const titles = {
      overview: ["Ringkasan jaringan", "Kapasitas dan utilisasi core backbone"],
      cables: ["Kabel backbone", "Perangkat OTB dari Google Sheets dan kabel manual"],
      otb: ["Peta OTB & rak", "Sinkron otomatis dari Google Sheets"],
      tenants: ["Tenant", "Pemilik lease dark fiber"],
      leases: ["Lease", "Kontrak pemakaian core per tenant"],
    };
    let title;
    let sub;
    let primary = "";
    let search = true;

    if (route.name === "cables" && route.id && state.cable) {
      title = "Peta core";
      sub = `${state.cable.code} · ${num(state.cable.core_count)} core`;
      primary = `<button class="btn btn-ghost" data-action="cable-edit"><svg class="ico"><use href="#i-edit"/></svg>Edit kabel</button>`;
    } else if (route.name === "tenants" && route.id && state.tenant) {
      title = "Detail tenant";
      sub = `${state.tenant.code} · ${state.tenant.pic || "Tanpa PIC"}`;
      primary = `<button class="btn btn-ghost" data-action="tenant-edit"><svg class="ico"><use href="#i-edit"/></svg>Edit tenant</button>`;
    } else if (route.name === "cables") {
      const [t] = titles.cables;
      title = t;
      const plants = state.plants || [];
      const buildings = new Set(plants.map((p) => p.gid)).size;
      sub = `${state.summary ? state.summary.totals.cables : 0} kabel · ${state.summary ? num(state.summary.totals.capacity) : "–"} core · ${buildings} stasiun dari sheet`;
      primary = `<button class="btn btn-primary" data-action="sheet-sync"><svg class="ico"><use href="#i-clock"/></svg>Sync sheet</button>`;
    } else if (route.name === "tenants") {
      const [t] = titles.tenants;
      title = t;
      const active = state.tenants.filter((x) => x.status === "active").length;
      sub = `${active} tenant aktif · ${num(state.tenants.reduce((a, x) => a + x.cores, 0))} core dikelola`;
      primary = `<button class="btn btn-primary" data-action="tenant-new"><svg class="ico"><use href="#i-plus"/></svg>Tambah tenant</button>`;
    } else if (route.name === "leases") {
      const [t] = titles.leases;
      title = t;
      const attention = state.leases.filter((l) => l.state === "expiring" || l.state === "expired").length;
      sub = `${state.leases.length} lease berjalan${attention ? ` · ${attention} perlu perhatian` : ""}`;
      primary = `<button class="btn btn-primary" data-route="#/cables"><svg class="ico"><use href="#i-plus"/></svg>Buat lease</button>`;
    } else if (route.name === "otb") {
      const [t] = titles.otb;
      title = t;
      const status = state.sheetStatus;
      const active = status && route.id ? status.tabs.find((x) => x.gid === route.id) : null;
      sub = active
        ? `${active.name} · ${num(active.entries)} entri port`
        : status
          ? `${status.tabs.length} gedung/rak · ${num(status.total_entries)} entri port`
          : "Memuat peta OTB…";
      primary = `<button class="btn btn-primary" data-action="sheet-sync" ${state.sheetSyncing ? "disabled" : ""}><svg class="ico"><use href="#i-clock"/></svg>${state.sheetSyncing ? "Menyinkronkan…" : "Sync sekarang"}</button>`;
    } else {
      [title, sub] = titles[route.name] || titles.overview;
      primary = `<button class="btn btn-primary" data-route="#/cables">Buka peta kabel</button>`;
    }

    $("#page-title").textContent = title;
    $("#page-sub").textContent = sub;
    $("#primary-slot").innerHTML = primary;
    $("#search-wrap").classList.toggle("hidden", !search);
    if (search) {
      searchEl.placeholder =
        route.name === "tenants"
          ? "Cari tenant, PIC, kode, port OTB"
          : route.name === "leases"
            ? "Cari lease, tenant, kabel, port OTB"
            : route.name === "otb"
              ? "Cari port, tujuan, OTB lintas gedung"
              : "Cari kabel, tenant, lease, port OTB";
    }

    const navName = route.name === "cables" ? "cables" : route.name === "tenants" ? "tenants" : route.name;
    $$(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.nav === navName));
  }

  async function renderApp() {
    const route = getRoute();
    const key = `${route.name}/${route.id || ""}`;
    const changed = key !== state.routeKey;

    if (changed) {
      state.routeKey = key;
      state.search = "";
      state.searchQuery = "";
      state.searchResults = [];
      searchEl.value = "";
      hideSearchPop();
      state.selection.clear();
      state.lastPicked = null;
      state.cable = null;
      state.tenant = null;
      state.cableFilter = "all";
      state.coreFilter = "all";
      state.sheetQuery = "";
      // Search jump: langsung ke-filter ke Port yang dicari (mis. WSA).
      if (state.searchJump && route.name === "otb") {
        state.sheetQuery = state.searchJump.q || "";
        state.searchJump = null;
      }
      closeModal();
      window.scrollTo({ top: 0 });
    }

    try {
      if (route.name === "cables" && route.id) {
        state.cable = await api(`/cables/${route.id}`);
        buildCoreIndex();
      } else if (route.name === "tenants" && route.id) {
        state.tenant = await api(`/tenants/${route.id}`);
      } else if (route.name === "otb") {
        await loadSheet(route);
      }
      state.loading = false;
      updateChrome(route);
      renderView();
      syncSelection();
    } catch (err) {
      state.loading = false;
      updateChrome(route);
      viewEl.innerHTML = emptyState("Data tidak bisa dimuat", err.message, "i-alert");
    }
  }

  function buildCoreIndex() {
    state.coreIndex = new Map();
    if (!state.cable) return;
    for (const tube of state.cable.tubes) {
      for (const core of tube.cores) {
        core.tube = tube.no;
        state.coreIndex.set(core.no, core);
      }
    }
  }

  async function loadSheet(route, { force = false } = {}) {
    state.sheetStatus = await api("/sheet/status");
    const tabs = state.sheetStatus.tabs || [];
    let gid = route.id;
    if (!tabs.some((tab) => tab.gid === gid)) gid = state.sheetTab || (tabs[0] && tabs[0].gid) || "";
    if (!gid) {
      state.sheetEntries = [];
      state.sheetLoadedGid = "";
      return;
    }
    if (force || gid !== state.sheetLoadedGid) {
      const data = await api(`/sheet/entries?gid=${encodeURIComponent(gid)}`);
      state.sheetEntries = data.entries || [];
      state.sheetLoadedGid = gid;
    }
    state.sheetTab = gid;
  }

  // ---------------------------------------------------------------- views
  function renderView() {
    const route = getRoute();
    if (route.name === "cables" && route.id) return renderCableView();
    if (route.name === "tenants" && route.id) return renderTenantView();
    if (route.name === "cables") return renderCablesView();
    if (route.name === "otb") return renderOtbView();
    if (route.name === "tenants") return renderTenantsView();
    if (route.name === "leases") return renderLeasesView();
    return renderOverview();
  }

  function renderOverview() {
    const s = state.summary;
    if (!s) {
      viewEl.innerHTML = loadingState();
      return;
    }
    const t = s.totals;
    const cables = s.cables.filter((c) => searchMatch(c.name, c.code, c.route));
    const expiring = s.expiring.filter((l) => searchMatch(l.tenant_name, l.cable_name, l.batch_id));
    const maxTenant = Math.max(...s.top_tenants.map((x) => x.cores), 1);
    const leasesAttention = state.leases.filter((l) => l.state === "expiring" || l.state === "expired").length;

    viewEl.innerHTML = `
      <div class="kpis">
        ${state.plants.length ? `
        <div class="kpi">
          <div class="kpi-label">Perangkat plant</div>
          <div class="kpi-value">${num(state.plants.length)}<small>panel</small></div>
          <div class="kpi-note">Panel OTB / MDF / ODC dari Google Sheet</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Port terdaftar</div>
          <div class="kpi-value">${num(state.plants.reduce((a, p) => a + (p.total_ports || 0), 0))}</div>
          <div class="kpi-note">Total baris port di semua panel</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Stasiun / gedung</div>
          <div class="kpi-value">${num(new Set(state.plants.map((p) => p.gid)).size)}</div>
          <div class="kpi-note">Tab sheet yang tersinkron</div>
        </div>` : ""}
        <div class="kpi">
          <div class="kpi-label">Kapasitas terpasang</div>
          <div class="kpi-value">${num(t.capacity)}<small>core</small></div>
          <div class="kpi-note">${t.cables} kabel · ${[...new Set(cables.map((c) => c.core_count).filter(Boolean))].sort((a, b) => a - b).join(" / ")} core</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Core terpakai</div>
          <div class="kpi-value">${num(t.used_combined ?? t.leased)}<small>core</small></div>
          <div class="kpi-note">Utilisasi ${pct(t.utilization_combined ?? t.utilization)} · ${num(t.leased)} lease + ${num(t.sheet_only_cores ?? 0)} sheet</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Port sheet kepake</div>
          <div class="kpi-value">${num(t.sheet_used)}<small>/${num(t.sheet_total)} port</small></div>
          <div class="kpi-note">Tujuan terisi · ${num(t.sheet_used_cores)} core teridentifikasi · ${pct(t.sheet_port_utilization)}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">Core tersedia</div>
          <div class="kpi-value">${num(t.available_combined ?? t.available)}<small>core</small></div>
          <div class="kpi-note">Siap dialokasikan ke tenant</div>
        </div>
        <div class="kpi ${leasesAttention ? "alert" : ""}">
          <div class="kpi-label">Lease berjalan</div>
          <div class="kpi-value">${num(t.leases)}<small>kontrak</small></div>
          <div class="kpi-note">${leasesAttention ? `<strong>${leasesAttention}</strong> perlu perhatian` : "Tidak ada yang mendesak"}</div>
        </div>
      </div>

      <div class="overview-grid">
        <section class="panel">
          <div class="panel-head">
            <div>
              <h3 class="panel-title">Distribusi core per kabel</h3>
              <p class="panel-meta">Warna segmen menandai tenant pemakai core</p>
            </div>
            <a class="panel-link" href="#/cables">Semua kabel <svg class="ico"><use href="#i-chevron"/></svg></a>
          </div>
          <div class="panel-body">
            <div class="cable-bars">
              ${cables.length ? cables.map(cableBarsRow).join("") : emptyState("Tidak ada kabel yang cocok", "Ubah kata kunci pencarian")}
            </div>
            <div class="legend">
              <span class="legend-item"><span class="dot dot-neutral"></span>Tersedia</span>
              ${s.top_tenants.map((x) => `<span class="legend-item"><span class="dot" style="color:${x.color}"></span>${esc(x.name)} <span class="count">${num(x.cores)}</span></span>`).join("")}
            </div>
            <div class="size-grid">
              ${s.by_size.map((row) => `
                <div class="size-item">
                  <div class="size-top"><span class="size-label">${row.size} core</span><span class="size-pct">${pct(row.capacity ? ((row.used_combined ?? row.leased) / row.capacity) * 100 : 0)}</span></div>
                  <div class="meter"><span style="width:${row.capacity ? Math.min(100, ((row.used_combined ?? row.leased) / row.capacity) * 100) : 0}%"></span></div>
                  <div class="size-fig">${num(row.used_combined ?? row.leased)} / ${num(row.capacity)} core terpakai</div>
                  ${row.sheet_total ? `<div class="size-sheet">${num(row.sheet_used)}/${num(row.sheet_total)} port sheet · ${num(row.sheet_used_cores)} core sheet · ${num(row.leased)} lease</div>` : ""}
                </div>`).join("")}
            </div>
          </div>
        </section>

        <div class="overview-side">
          <section class="panel">
            <div class="panel-head">
              <div>
                <h3 class="panel-title">Sewa berakhir</h3>
                <p class="panel-meta">≤ 30 hari atau sudah lewat</p>
              </div>
              <a class="panel-link" href="#/leases">Lease <svg class="ico"><use href="#i-chevron"/></svg></a>
            </div>
            <div class="panel-body">
              ${expiring.length ? `<div class="exp-list">
                ${expiring.map((l) => `
                  <button type="button" class="exp-row" data-lease-open="${esc(l.batch_id)}">
                    <span class="dot" style="color:${l.tenant_color}"></span>
                    <span class="exp-main">
                      <span class="exp-name">${esc(l.tenant_name)}</span>
                      <span class="exp-meta">${esc(l.cable_name)} · ${num(l.core_count)} core</span>
                    </span>
                    <span class="exp-side">
                      <span class="exp-date">${fmtDate(l.end_date)}</span>
                      <span class="exp-days ${remainingTone(l)}">${remainingLabel(l)}</span>
                    </span>
                  </button>`).join("")}
              </div>` : emptyState("Tidak ada sewa mendesak", "Semua lease masih jauh dari jatuh tempo", "i-check")}
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h3 class="panel-title">Tenant teratas</h3>
              <span class="panel-meta">Berdasarkan core aktif</span>
            </div>
            <div class="panel-body">
              ${s.top_tenants.length ? `<div class="rank-list">
                ${s.top_tenants.map((x) => `
                  <div class="rank-row">
                    <span class="dot" style="color:${x.color}"></span>
                    <a class="rank-name" href="#/tenants/${x.id}">${esc(x.name)}</a>
                    <span class="rank-val">${num(x.cores)}</span>
                    <span class="rank-bar"><span style="width:${(x.cores / maxTenant) * 100}%;background:${x.color}"></span></span>
                  </div>`).join("")}
              </div>` : emptyState("Belum ada alokasi", "Core akan muncul setelah lease pertama", "i-users")}
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <div>
                <h3 class="panel-title">Port sheet kepake</h3>
                <p class="panel-meta">Data asli Peta OTB · Tujuan terisi · sync Google Sheet</p>
              </div>
              <a class="panel-link" href="#/otb">Peta OTB <svg class="ico"><use href="#i-chevron"/></svg></a>
            </div>
            <div class="panel-body">
              ${s.sheet_by_cable && s.sheet_by_cable.length ? `<div class="sheet-list">
                ${s.sheet_by_cable.slice(0, 10).map((row) => `
                  <a class="sheet-row" href="#/cables/${row.cable_id}">
                    <span class="sheet-main">
                      <span class="sheet-name">${esc(row.panel_code || row.name)}</span>
                      <span class="sheet-sub">${esc(row.tab_name || "")} · ${num(row.sheet_used)}/${num(row.sheet_total)} port · ${num(row.sheet_used_cores)} core</span>
                    </span>
                    <span class="sheet-pct">${pct(row.sheet_total ? (row.sheet_used / row.sheet_total) * 100 : 0)}</span>
                    <span class="sheet-bar"><span style="width:${row.sheet_total ? Math.min(100, (row.sheet_used / row.sheet_total) * 100) : 0}%"></span></span>
                  </a>`).join("")}
              </div>` : emptyState("Belum ada data sheet", "Jalankan sync sheet dari menu Kabel backbone atau Peta OTB", "i-layers")}
            </div>
          </section>
        </div>
      </div>`;
  }

  function cableBarsRow(cable) {
    const total = cable.core_count;
    const segments = cable.mix
      .map((m) => `<span class="seg" style="width:${(m.cores / total) * 100}%;background:${m.color}" title="${esc(m.name)} · ${num(m.cores)} core"></span>`)
      .join("");
    const sheetBadge = cable.sheet_total
      ? `<span class="sheet-badge" title="Port sheet terpakai (Tujuan terisi) · ${esc(cable.tab_name || "")}">${esc(cable.tab_name || "Sheet")} ${num(cable.sheet_used)}/${num(cable.sheet_total)} port · ${num(cable.sheet_used_cores)} core</span>`
      : "";
    const usedCombined = cable.used_combined ?? cable.leased;
    const utilCombined = cable.utilization_combined ?? cable.utilization;
    const figures = total
      ? `<span class="size">${cable.core_count}</span> ${num(usedCombined)}/${num(total)}<span class="pct">${pct(utilCombined)}</span>`
      : `<span class="size">?</span> ${num(cable.sheet_used)}/${num(cable.sheet_total)} port`;
    return `<a class="cable-bar-row" href="#/cables/${cable.id}">
      <div class="bar-top">
        <div><span class="bar-name">${esc(cable.name)}</span><span class="bar-code">${esc(cable.code)}</span></div>
        <div class="bar-figures">${figures}</div>
      </div>
      <div class="stack">${segments}</div>
      ${sheetBadge ? `<div class="bar-sheet">${sheetBadge}</div>` : ""}
      ${cable.sheet_total && usedCombined !== cable.leased ? `<div class="bar-breakdown">${num(cable.leased)} lease + ${num(cable.sheet_only_cores ?? 0)} sheet</div>` : ""}
    </a>`;
  }

  // ---------------------------------------------------------- cable cards
  function cableCard(cable, colors) {
    const isPlant = cable.source === "sheet";
    const percent = cable.core_count ? ((cable.used_combined ?? cable.leased) / cable.core_count) * 100 : 0;
    const segments = cable.mix
      ? cable.mix
          .map((m) => `<span class="seg" style="width:${(m.cores / cable.core_count) * 100}%;background:${m.color}" title="${esc(m.name)} · ${num(m.cores)} core"></span>`)
          .join("")
      : "";
    const route = cable.route ? esc(String(cable.route).slice(0, 90)) : "<span class='muted'>Tanpa keterangan</span>";
    const title = cable.panel_code || cable.name;
    const sheetLine = cable.sheet_total
      ? `<div class="sheet-line"><span class="sheet-dot"></span><span><strong>${num(cable.sheet_used)}/${num(cable.sheet_total)} port</strong> sheet kepake (Tujuan terisi) · ${num(cable.sheet_used_cores)} core teridentifikasi</span></div>`
      : "";
    return `<a class="cable-card" href="#/cables/${cable.id}">
      <div class="card-top">
        <span class="code">${esc(cable.code)}</span>
        <span class="state state-info"><span class="dot"></span>${esc(cable.tab_name || (isPlant ? "Sheet" : "Manual"))}</span>
      </div>
      <h3 class="cable-name">${esc(title)}</h3>
      <p class="route">${route}</p>
      <div class="fiber-strip" title="Kode warna 12 fiber TIA-598">${colors.map((hex) => `<span style="background:${hex}"></span>`).join("")}</div>
      <div class="cable-metrics">
        ${cable.core_count ? `<div><span class="metric-big">${num(cable.used_combined ?? cable.leased)}</span> <span class="metric-sub">/ ${num(cable.core_count)} core</span></div>` : `<div><span class="metric-big">–</span> <span class="metric-sub">ukuran belum terdeteksi</span></div>`}
        ${cable.core_count ? `<span class="metric-pct">${pct(cable.utilization_combined ?? cable.utilization)}</span>` : ""}
      </div>
      ${cable.core_count && cable.sheet_total ? `<div class="metric-breakdown">${num(cable.leased)} lease + ${num(cable.sheet_only_cores ?? 0)} sheet core</div>` : ""}
      <div class="stack">${segments}</div>
      ${sheetLine}
      <div class="card-foot">
        <span>${isPlant ? `${num(cable.total_ports)} port${cable.section ? ` · ${esc(cable.section)}` : ""}` : `${cable.length_km ? `${num(cable.length_km)} km` : "–"} · ${cable.installed_year || "–"}`}</span>
        <span class="link">Peta core <svg class="ico"><use href="#i-chevron"/></svg></span>
      </div>
    </a>`;
  }

  function renderCablesView() {
    const sizes = [...new Set(state.cables.map((c) => c.core_count).filter(Boolean))].sort((a, b) => a - b);
    const filtered = state.cables.filter(
      (c) => (state.cableFilter === "all" || c.core_count === Number(state.cableFilter)) && searchMatch(c.name, c.code, c.route, c.tab_name, c.section, c.panel_code)
    );
    const colors = fiberColors();

    const totalSheetUsed = state.cables.reduce((a, c) => a + (c.sheet_used || 0), 0);
    const totalSheet = state.cables.reduce((a, c) => a + (c.sheet_total || 0), 0);

    viewEl.innerHTML = `
      <div class="list-toolbar">
        <span class="panel-meta">${filtered.length} dari ${state.cables.length} kabel · ${new Set(filtered.map((c) => c.gid).filter(Boolean)).size} stasiun sheet · ${num(totalSheetUsed)}/${num(totalSheet)} port sheet kepake</span>
        <div class="segmented" role="group" aria-label="Filter ukuran kabel">
          <button class="seg-btn ${state.cableFilter === "all" ? "active" : ""}" data-action="filter-cable" data-size="all">Semua</button>
          ${sizes.map((s) => `<button class="seg-btn ${state.cableFilter === String(s) ? "active" : ""}" data-action="filter-cable" data-size="${s}">${s}</button>`).join("")}
        </div>
      </div>
      ${filtered.length ? `<div class="cable-grid">${filtered.map((c) => cableCard(c, colors)).join("")}</div>` : emptyState("Tidak ada kabel yang cocok", "Ubah filter ukuran atau kata kunci pencarian")}
    `;
  }

  function renderCableView() {
    if (!state.cable) {
      viewEl.innerHTML = loadingState();
      return;
    }
    const c = state.cable;
    const used = c.used_combined ?? c.leased;
    const free = c.available_combined ?? c.available;
    const tenantChips = c.tenant_totals
      .map((t) => `<span class="legend-item"><span class="dot" style="color:${t.color}"></span>${esc(t.name)} <span class="count">${num(t.cores)}</span></span>`)
      .join("");

    viewEl.innerHTML = `
      <a class="back" href="#/cables"><svg class="ico"><use href="#i-back"/></svg>Kabel backbone</a>
      <div class="detail-head">
        <div>
          <div class="detail-title-row">
            <h2 class="detail-title">${esc(c.name)}</h2>
            <span class="code-chip">${esc(c.code)}</span>
            ${cableStatusTag(c.status)}
          </div>
          <p class="detail-sub">${esc(c.route)}</p>
        </div>
      </div>

      <div class="stats-strip">
        ${c.core_count ? `
        <div class="stat-cell"><div class="stat-label">Kapasitas</div><div class="stat-value">${num(c.core_count)} <small>core</small></div></div>
        <div class="stat-cell"><div class="stat-label">Terpakai</div><div class="stat-value">${num(used)}</div></div>
        <div class="stat-cell"><div class="stat-label">Lease</div><div class="stat-value">${num(c.leased || 0)}</div></div>
        <div class="stat-cell"><div class="stat-label">Port sheet kepake</div><div class="stat-value">${num(c.sheet_used || 0)}<small>/${num(c.sheet_total || 0)}</small></div></div>
        <div class="stat-cell"><div class="stat-label">Core sheet</div><div class="stat-value">${num(c.sheet_used_cores || 0)}</div></div>
        <div class="stat-cell"><div class="stat-label">Tersedia</div><div class="stat-value">${num(free)}</div></div>
        <div class="stat-cell"><div class="stat-label">Utilisasi</div><div class="stat-value accent">${pct(c.utilization_combined ?? c.utilization)}</div></div>` : `
        <div class="stat-cell"><div class="stat-label">Ukuran core</div><div class="stat-value">–</div></div>
        <div class="stat-cell"><div class="stat-label">Port terdaftar</div><div class="stat-value">${num(c.total_ports)}</div></div>
        <div class="stat-cell"><div class="stat-label">Port sheet kepake</div><div class="stat-value">${num(c.sheet_used || 0)}<small>/${num(c.sheet_total || 0)}</small></div></div>`}
        <div class="stat-cell"><div class="stat-label">Panjang</div><div class="stat-value">${c.length_km ? num(c.length_km) : "–"} <small>km</small></div></div>
        <div class="stat-cell"><div class="stat-label">Terpasang</div><div class="stat-value">${c.installed_year || "–"}</div></div>
      </div>

      ${c.sheet_total ? `<div class="sheet-banner">Sheet <strong>${esc(c.tab_name || "")}</strong> · ${num(c.sheet_used)} dari ${num(c.sheet_total)} port Tujuan terisi · <strong>${num(c.sheet_used_cores)} core</strong> teridentifikasi dari nomor port · ${num(c.sheet_only_cores || 0)} core sheet belum ada lease</div>` : ""}

      ${c.core_count ? `
      <section class="panel map-panel">
        <div class="map-toolbar">
          <div>
            <h3 class="panel-title">Peta core</h3>
            <p class="panel-meta">${c.tubes.length} tube · ${num(c.core_count)} core · klik core untuk memilih · ${num(c.sheet_used_cores || 0)} core dari sheet</p>
          </div>
          <div class="map-tools">
            <div class="segmented" role="group" aria-label="Filter status core">
              <button class="seg-btn ${state.coreFilter === "all" ? "active" : ""}" data-action="filter-core" data-filter="all">Semua</button>
              <button class="seg-btn ${state.coreFilter === "available" ? "active" : ""}" data-action="filter-core" data-filter="available">Tersedia</button>
              <button class="seg-btn ${state.coreFilter === "used" ? "active" : ""}" data-action="filter-core" data-filter="used">Terpakai</button>
            </div>
            <button class="btn btn-ghost btn-sm" data-action="select-available">Pilih tersedia</button>
            <button class="btn btn-ghost btn-sm" data-action="select-clear">Bersihkan</button>
          </div>
        </div>
        <div class="map-legend">
          <span class="legend-item"><span class="dot dot-neutral"></span>Tersedia <span class="count">${num(free)}</span></span>
          <span class="legend-item"><span class="dot sheet-dot-legend"></span>Sheet (Tujuan terisi) <span class="count">${num(c.sheet_used_cores || 0)}</span></span>
          ${tenantChips}
        </div>
        <div class="tube-grid">
          ${c.tubes.map((tube) => tubeCard(tube, c)).join("")}
        </div>
        <div class="map-foot">
          <span>Garis bawah tiap core mengikuti kode warna 12 fiber TIA-598</span>
          <span>${num(used)} terpakai · ${num(free)} tersedia</span>
        </div>
      </section>

      <div class="tray" id="tray" hidden>` : `
      <section class="panel map-panel">
        ${emptyState("Ukuran core belum terdeteksi", "Panel ini hanya menampilkan daftar port dari sheet. Edit kabel untuk mengisi jumlah core agar peta tube tersedia.", "i-layers")}
      </section>
      <div class="tray" id="tray" hidden>`}

      ${c.entries && c.entries.length ? `
      <section class="panel" style="margin-top:14px">
        <div class="panel-head">
          <div>
            <h3 class="panel-title">Port OTB dari Google Sheet</h3>
            <p class="panel-meta">${esc(c.tab_name || "")} · ${esc(c.section || "")} · ${c.entries.length} port · ${num(c.sheet_used || 0)} kepake (Tujuan terisi)</p>
          </div>
        </div>
        <div class="table-wrap" style="border:0;border-radius:0">
          <table>
            <thead><tr><th>Status</th><th>Port</th><th>Tujuan</th><th>Keterangan</th></tr></thead>
            <tbody>
              ${c.entries.map((e) => `<tr class="${e.used ? "row-used" : ""}">
                <td>${e.used ? '<span class="state state-ok"><span class="dot"></span>Kepake</span>' : '<span class="state state-muted"><span class="dot"></span>Kosong</span>'}</td>
                <td class="mono-sm">${esc(e.port) || "–"}${e.core_nos && e.core_nos.length ? `<div class="cell-sub mono">core ${e.core_nos.length} · ${esc(e.core_nos.slice(0, 8).join(", "))}${e.core_nos.length > 8 ? "…" : ""}</div>` : ""}</td>
                <td>${esc(e.destination) || "–"}</td>
                <td>${esc(e.note) || "–"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </section>` : ""}

      <div class="tray" id="tray" hidden>

        <div class="tray-info">
          <span class="tray-count" id="tray-count">0</span>
          <span class="tray-label">core dipilih</span>
          <span class="tray-ranges" id="tray-ranges"></span>
        </div>
        <div class="tray-actions">
          <button class="btn btn-ghost" data-action="select-clear">Bersihkan</button>
          <button class="btn btn-primary" data-action="allocate-open" id="tray-allocate">Alokasikan</button>
        </div>
      </div>`;
  }

  function tubeCard(tube, cable) {
    const used = tube.cores.filter((c) => c.allocation || c.sheet).length;
    const leased = tube.cores.filter((c) => c.allocation).length;
    return `<div class="tube">
      <div class="tube-head">
        <span class="tube-name">Tube ${String(tube.no).padStart(2, "0")}</span>
        <span class="tube-count" title="${leased} lease + ${used - leased} sheet">${used}/12</span>
      </div>
      <div class="cores">
        ${tube.cores.map((core) => coreCell(core, cable)).join("")}
      </div>
    </div>`;
  }

  function coreCell(core, cable) {
    const selected = state.selection.has(core.no);
    const occupied = Boolean(core.allocation || core.sheet);
    const dim =
      (state.coreFilter === "available" && occupied) || (state.coreFilter === "used" && !occupied);
    const classes = ["core"];
    if (core.allocation) classes.push("leased");
    else if (core.sheet) classes.push("sheet");
    if (selected) classes.push("selected");
    if (dim) classes.push("dim");

    let title;
    if (core.allocation) {
      title = `Core ${core.no} · Tube ${core.tube} pos ${core.pos} · ${core.color_name} · ${core.allocation.tenant_name} · ${core.allocation.purpose} · s/d ${fmtDate(core.allocation.end_date)}`;
      if (core.sheet) title += ` · Sheet: ${core.sheet.port} → ${core.sheet.destination}`;
    } else if (core.sheet) {
      title = `Core ${core.no} · Tube ${core.tube} pos ${core.pos} · ${core.color_name} · Sheet ${core.sheet.port} → ${core.sheet.destination}${core.sheet.note ? ` · ${core.sheet.note}` : ""}`;
    } else {
      title = `Core ${core.no} · Tube ${core.tube} pos ${core.pos} · ${core.color_name} · Tersedia`;
    }

    let style;
    if (core.allocation) style = `background:${core.allocation.color}`;
    else if (core.sheet) style = `background:repeating-linear-gradient(45deg, #2a3431 0 5px, #1f2a27 5px 10px);box-shadow:inset 0 0 0 1px #3fb68b`;
    else style = `box-shadow:inset 0 -2px 0 ${core.color}`;

    return `<button type="button" class="${classes.join(" ")}" data-core="${core.no}" data-tube="${core.tube}"
      style="${style}"
      title="${esc(title)}" aria-label="${esc(title)}">${core.no}</button>`;
  }

  function renderTenantsView() {
    const tenants = state.tenants.filter((t) => searchMatch(t.name, t.code, t.pic, t.email, t.contact));

    viewEl.innerHTML = `
      ${tenants.length ? `<div class="table-wrap">
        <table>
          <thead><tr>
            <th>Tenant</th><th>PIC</th><th class="num">Core aktif</th><th class="num">Lease</th>
            <th>Berakhir terdekat</th><th>Status</th><th></th>
          </tr></thead>
          <tbody>
            ${tenants.map((t) => `
              <tr class="row-link" data-route="#/tenants/${t.id}">
                <td>
                  <div class="cell-tenant">
                    <span class="dot" style="color:${t.color}"></span>
                    <div><div class="cell-main">${esc(t.name)}</div><div class="cell-sub mono">${esc(t.code)}</div></div>
                  </div>
                </td>
                <td><div class="cell-main">${esc(t.pic || "–")}</div><div class="cell-sub">${esc(t.contact || t.email || "–")}</div></td>
                <td class="num mono-sm">${num(t.cores)}</td>
                <td class="num mono-sm">${num(t.leases)}</td>
                <td><span class="mono-sm">${t.next_end ? fmtDate(t.next_end) : "–"}</span></td>
                <td>${tenantStatusTag(t.status)}</td>
                <td><div class="cell-actions"><svg class="ico" style="width:15px;height:15px;color:var(--mut)"><use href="#i-chevron"/></svg></div></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>` : emptyState("Tidak ada tenant yang cocok", "Ubah kata kunci pencarian", "i-users")}
    `;
  }

  function renderTenantView() {
    if (!state.tenant) {
      viewEl.innerHTML = loadingState();
      return;
    }
    const t = state.tenant;
    const leases = t.leases_detail;
    viewEl.innerHTML = `
      <a class="back" href="#/tenants"><svg class="ico"><use href="#i-back"/></svg>Tenant</a>
      <div class="detail-head">
        <div class="tenant-head" style="margin-bottom:0">
          <div class="tenant-avatar" style="background:${t.color}">${esc(initials(t.name))}</div>
          <div>
            <div class="detail-title-row">
              <h2 class="detail-title">${esc(t.name)}</h2>
              <span class="code-chip">${esc(t.code)}</span>
              ${tenantStatusTag(t.status)}
            </div>
            <p class="detail-sub">${esc(t.pic || "Tanpa PIC")} · ${esc(t.contact || t.email || "Kontak belum diisi")}</p>
          </div>
        </div>
      </div>

      <div class="detail-grid">
        <section class="panel">
          <div class="panel-head">
            <div>
              <h3 class="panel-title">Lease aktif</h3>
              <p class="panel-meta">${leases.length} kontrak · ${num(t.cores)} core</p>
            </div>
          </div>
          <div class="panel-body" style="padding-left:0;padding-right:0;padding-bottom:0">
            ${leases.length ? `<div class="table-wrap" style="border:0;border-radius:0;border-top:1px solid var(--line)">
              <table style="min-width:640px">
                <thead><tr><th>Lease</th><th>Kabel</th><th>Core</th><th>Periode</th><th>Status</th><th>Sisa</th></tr></thead>
                <tbody>
                  ${leases.map((l) => `
                    <tr class="row-link" data-lease-open="${esc(l.batch_id)}">
                      <td><span class="mono-sm">${esc(l.batch_id)}</span><div class="cell-sub">${esc(l.purpose)}</div></td>
                      <td><div class="cell-main">${esc(l.cable_name)}</div><div class="cell-sub mono">${esc(l.cable_code)}</div></td>
                      <td><span class="mono-sm">${num(l.cores)} core</span><div class="cell-sub">${l.core_from}–${l.core_to}</div></td>
                      <td><span class="mono-sm">${fmtDate(l.start_date)}</span><div class="cell-sub">s/d ${fmtDate(l.end_date)}</div></td>
                      <td>${stateTag(l.state)}</td>
                      <td><span class="state state-${remainingTone(l) || "muted"}">${remainingLabel(l)}</span></td>
                    </tr>`).join("")}
                </tbody>
              </table>
            </div>` : emptyState("Belum ada lease", "Tenant ini belum memakai core", "i-file")}
          </div>
        </section>

        <div class="detail-side">
          <section class="panel">
            <div class="panel-head"><h3 class="panel-title">Kontak</h3></div>
            <div class="panel-body">
              <div class="def-grid" style="grid-template-columns:1fr">
                <div class="contact-item"><div class="def-label">PIC</div><div class="def-value">${esc(t.pic || "–")}</div></div>
                <div class="contact-item"><div class="def-label">Telepon</div><div class="def-value mono">${esc(t.contact || "–")}</div></div>
                <div class="contact-item"><div class="def-label">Email</div><div class="def-value">${esc(t.email || "–")}</div></div>
                <div class="contact-item"><div class="def-label">Catatan</div><div class="def-value" style="color:var(--tx2)">${esc(t.notes || "–")}</div></div>
              </div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h3 class="panel-title">Ringkasan</h3></div>
            <div class="panel-body">
              <div class="def-grid" style="grid-template-columns:1fr 1fr">
                <div><div class="def-label">Core aktif</div><div class="stat-value" style="font-size:20px">${num(t.cores)}</div></div>
                <div><div class="def-label">Lease</div><div class="stat-value" style="font-size:20px">${num(t.leases)}</div></div>
                <div><div class="def-label">Kabel dipakai</div><div class="stat-value" style="font-size:20px">${num(t.cables)}</div></div>
                <div><div class="def-label">Bergabung</div><div class="def-value mono" style="margin-top:6px">${fmtDate(t.created_at)}</div></div>
              </div>
            </div>
          </section>
        </div>
      </div>`;
  }

  function renderLeasesView() {
    const counts = {
      all: state.leases.length,
      active: state.leases.filter((l) => l.state === "active").length,
      expiring: state.leases.filter((l) => l.state === "expiring").length,
      expired: state.leases.filter((l) => l.state === "expired").length,
      reserved: state.leases.filter((l) => l.state === "reserved").length,
    };
    const tabs = [
      ["all", "Semua"],
      ["active", "Aktif"],
      ["expiring", "Segera berakhir"],
      ["expired", "Kedaluwarsa"],
      ["reserved", "Reservasi"],
    ];
    const leases = state.leases.filter(
      (l) =>
        (state.leaseFilter === "all" || l.state === state.leaseFilter) &&
        searchMatch(l.batch_id, l.tenant_name, l.cable_name, l.cable_code, l.purpose, l.core_label)
    );

    viewEl.innerHTML = `
      <div class="tabs" role="group" aria-label="Filter status lease">
        ${tabs.map(([key, label]) => `<button class="tab ${state.leaseFilter === key ? "active" : ""}" data-action="filter-lease" data-filter="${key}">${label}<span class="tab-count">${num(counts[key])}</span></button>`).join("")}
      </div>
      ${leases.length ? `<div class="table-wrap">
        <table>
          <thead><tr>
            <th>Lease</th><th>Tenant</th><th>Kabel</th><th>Core</th><th>Periode</th><th>Status</th><th>Sisa</th><th></th>
          </tr></thead>
          <tbody>
            ${leases.map((l) => `
              <tr class="row-link" data-lease-open="${esc(l.batch_id)}">
                <td><span class="mono-sm">${esc(l.batch_id)}</span><div class="cell-sub">${esc(l.purpose)}</div></td>
                <td><div class="cell-tenant"><span class="dot" style="color:${l.tenant_color}"></span><span class="cell-main">${esc(l.tenant_name)}</span></div></td>
                <td><div class="cell-main">${esc(l.cable_name)}</div><div class="cell-sub mono">${esc(l.cable_code)}</div></td>
                <td><span class="mono-sm">${num(l.core_count)} core</span><div class="cell-sub" title="${esc(l.core_label)}">${esc(l.core_label)}</div></td>
                <td><span class="mono-sm">${fmtDate(l.start_date)}</span><div class="cell-sub">s/d ${fmtDate(l.end_date)}</div></td>
                <td>${stateTag(l.state)}</td>
                <td><span class="state state-${remainingTone(l) || "muted"}">${remainingLabel(l)}</span></td>
                <td><div class="cell-actions">
                  <button class="btn btn-icon" title="Perpanjang" data-action="lease-extend" data-batch="${esc(l.batch_id)}"><svg class="ico"><use href="#i-calendar"/></svg></button>
                  <button class="btn btn-icon" title="Lepas core" data-action="lease-release" data-batch="${esc(l.batch_id)}"><svg class="ico"><use href="#i-unplug"/></svg></button>
                </div></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>` : emptyState("Tidak ada lease pada filter ini", "Pilih filter lain atau buat lease baru", "i-file")}
    `;
  }

  // ---------------------------------------------------------------- selection
  function handleCoreClick(btn, event) {
    if (!state.cable) return;
    const coreNo = Number(btn.dataset.core);
    const core = state.coreIndex.get(coreNo);
    if (!core) return;
    if (core.allocation) {
      openLeaseModal(core.allocation.batch_id);
      return;
    }

    if (event.shiftKey && state.lastPicked && state.lastPicked.tube === core.tube) {
      const [from, to] = [Math.min(state.lastPicked.pos, core.pos), Math.max(state.lastPicked.pos, core.pos)];
      for (let pos = from; pos <= to; pos += 1) {
        const target = state.coreIndex.get((core.tube - 1) * 12 + pos);
        if (target && !target.allocation) state.selection.set(target.no, target);
      }
    } else if (state.selection.has(coreNo)) {
      state.selection.delete(coreNo);
    } else {
      state.selection.set(coreNo, core);
    }
    state.lastPicked = { tube: core.tube, pos: core.pos };
    syncSelection();
  }

  function syncSelection() {
    $$("#view .core").forEach((el) => {
      const selected = state.selection.has(Number(el.dataset.core));
      el.classList.toggle("selected", selected);
    });
    const tray = $("#tray");
    if (!tray) return;
    const count = state.selection.size;
    tray.hidden = count === 0;
    if (!count) return;
    $("#tray-count").textContent = num(count);
    $("#tray-ranges").textContent = compactRanges([...state.selection.keys()]);
    $("#tray-allocate").textContent = `Alokasikan ${num(count)} core`;
  }

  // ---------------------------------------------------------------- modals
  function openModal(html, { wide = false } = {}) {
    modalRoot.innerHTML = `<div class="overlay" data-overlay>
      <div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="modal-title">${html}</div>
    </div>`;
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => {
      const first = modalRoot.querySelector("input:not([type=hidden]), select, textarea, button");
      if (first) first.focus();
    });
  }

  function closeModal() {
    if (!modalRoot.innerHTML) return;
    modalRoot.innerHTML = "";
    document.body.style.overflow = "";
  }

  function modalHead(title, sub) {
    return `<div class="modal-head">
      <div><h3 class="modal-title" id="modal-title">${esc(title)}</h3>${sub ? `<p class="modal-sub">${esc(sub)}</p>` : ""}</div>
      <button class="btn btn-icon" data-action="modal-close" aria-label="Tutup"><svg class="ico"><use href="#i-x"/></svg></button>
    </div>`;
  }

  function showFormError(form, message) {
    let el = form.querySelector(".form-error");
    if (!el) {
      el = document.createElement("p");
      el.className = "form-error";
      form.appendChild(el);
    }
    el.textContent = message;
  }

  function clearFormError(form) {
    const el = form.querySelector(".form-error");
    if (el) el.remove();
  }

  function openAllocateModal() {
    if (!state.cable || state.selection.size === 0) {
      toast("Pilih minimal satu core dulu", "warn");
      return;
    }
    const c = state.cable;
    const cores = [...state.selection.keys()].sort((a, b) => a - b);
    const tenantOptions = state.tenants
      .filter((t) => t.status === "active")
      .map((t) => `<option value="${t.id}">${esc(t.name)} · ${esc(t.code)}</option>`)
      .join("");
    const start = todayISO();
    const end = addDaysISO(start, 365);

    openModal(`
      ${modalHead("Alokasikan core", `${c.name} · ${c.code}`)}
      <form id="form-allocate">
        <div class="modal-body">
          <div class="selected-box">
            <div class="sel-top"><span class="sel-cable">${esc(c.name)}</span><span class="sel-count">${num(cores.length)} core</span></div>
            <div class="sel-ranges">Core ${esc(compactRanges(cores))}</div>
          </div>
          <div class="field">
            <label for="al-tenant">Tenant</label>
            <select id="al-tenant" name="tenant_id" required>${tenantOptions}</select>
          </div>
          <div class="field">
            <label for="al-purpose">Keperluan</label>
            <select id="al-purpose" name="purpose">${purposes().map((p) => `<option>${esc(p)}</option>`).join("")}</select>
          </div>
          <div class="grid2">
            <div class="field"><label for="al-start">Mulai</label><input id="al-start" type="date" name="start_date" value="${start}" required></div>
            <div class="field"><label for="al-end">Berakhir</label><input id="al-end" type="date" name="end_date" value="${end}" required></div>
          </div>
          <div class="field field-row">
            <label for="al-notes">Catatan</label>
            <textarea id="al-notes" name="notes" rows="2" placeholder="Nomor kontrak, jalur, atau detail teknis"></textarea>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn btn-ghost" data-action="modal-close">Batal</button>
          <button type="submit" class="btn btn-primary">Alokasikan ${num(cores.length)} core</button>
        </div>
      </form>`, { wide: true });
  }

  function openLeaseModal(batchId) {
    const lease = state.leases.find((l) => l.batch_id === batchId);
    if (!lease) {
      toast("Lease tidak ditemukan, memuat ulang data", "warn");
      refreshBasics().then(() => renderApp());
      return;
    }
    openModal(`
      ${modalHead(lease.batch_id, `${lease.tenant_name} · ${lease.cable_name}`)}
      <div class="modal-body">
        <div class="def-grid">
          <div><div class="def-label">Tenant</div><div class="def-value">${esc(lease.tenant_name)}</div></div>
          <div><div class="def-label">Kabel</div><div class="def-value">${esc(lease.cable_name)} <span class="mono muted">${esc(lease.cable_code)}</span></div></div>
          <div><div class="def-label">Keperluan</div><div class="def-value">${esc(lease.purpose)}</div></div>
          <div><div class="def-label">Status</div><div class="def-value">${stateTag(lease.state)}</div></div>
          <div><div class="def-label">Mulai</div><div class="def-value mono">${fmtDate(lease.start_date)}</div></div>
          <div><div class="def-label">Berakhir</div><div class="def-value mono">${fmtDate(lease.end_date)} <span class="state state-${remainingTone(lease) || "muted"}" style="margin-left:6px">${remainingLabel(lease)}</span></div></div>
          <div><div class="def-label">Jumlah core</div><div class="def-value mono">${num(lease.core_count)} core</div></div>
          <div><div class="def-label">Nomor core</div><div class="def-value mono" style="max-height:64px;overflow-y:auto">${esc(lease.core_label)}</div></div>
        </div>
        ${lease.notes ? `<div style="margin-top:16px"><div class="def-label">Catatan</div><p class="modal-note">${esc(lease.notes)}</p></div>` : ""}
        <p class="modal-note" style="margin-top:16px">Core tetap tercatat terpakai sampai lease dilepas, termasuk setelah tanggal berakhir.</p>
      </div>
      <div class="modal-foot">
        <button class="btn btn-danger" data-action="lease-release" data-batch="${esc(lease.batch_id)}"><svg class="ico"><use href="#i-unplug"/></svg>Lepas core</button>
        <span class="spacer"></span>
        <button class="btn btn-ghost" data-action="modal-close">Tutup</button>
        <button class="btn btn-primary" data-action="lease-extend" data-batch="${esc(lease.batch_id)}">Perpanjang</button>
      </div>`);
  }

  function openExtendModal(batchId) {
    const lease = state.leases.find((l) => l.batch_id === batchId);
    if (!lease) return;
    const nextEnd = addDaysISO(lease.end_date, 365);
    openModal(`
      ${modalHead("Perpanjang lease", `${lease.batch_id} · ${lease.tenant_name}`)}
      <form id="form-extend">
        <div class="modal-body">
          <input type="hidden" name="batch_id" value="${esc(lease.batch_id)}">
          <div class="def-grid" style="margin-bottom:16px">
            <div><div class="def-label">Kabel</div><div class="def-value">${esc(lease.cable_name)}</div></div>
            <div><div class="def-label">Core</div><div class="def-value mono">${num(lease.core_count)} core</div></div>
            <div><div class="def-label">Berakhir sekarang</div><div class="def-value mono">${fmtDate(lease.end_date)}</div></div>
            <div><div class="def-label">Status</div><div class="def-value">${stateTag(lease.state)}</div></div>
          </div>
          <div class="field">
            <label for="ex-end">Berakhir baru</label>
            <input id="ex-end" type="date" name="end_date" value="${nextEnd}" required>
            <span class="help">Tanggal harus setelah ${fmtDate(lease.start_date)}</span>
          </div>
          <div class="field">
            <label for="ex-purpose">Keperluan</label>
            <input id="ex-purpose" name="purpose" value="${esc(lease.purpose)}">
          </div>
          <div class="field">
            <label for="ex-notes">Catatan</label>
            <textarea id="ex-notes" name="notes" rows="2">${esc(lease.notes || "")}</textarea>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn btn-ghost" data-action="modal-close">Batal</button>
          <button type="submit" class="btn btn-primary">Simpan perubahan</button>
        </div>
      </form>`, { wide: true });
  }

  function openReleaseModal(batchId) {
    const lease = state.leases.find((l) => l.batch_id === batchId);
    if (!lease) return;
    openModal(`
      ${modalHead("Lepas core", `${lease.batch_id} · ${lease.tenant_name}`)}
      <form id="form-release">
        <div class="modal-body">
          <input type="hidden" name="batch_id" value="${esc(lease.batch_id)}">
          <p class="modal-note">${num(lease.core_count)} core pada <strong style="color:var(--tx)">${esc(lease.cable_name)}</strong> akan dikembalikan ke status tersedia dan bisa dialokasikan ke tenant lain.</p>
          <div class="selected-box" style="margin-top:14px;margin-bottom:0">
            <div class="sel-top"><span class="sel-cable">${esc(lease.tenant_name)}</span><span class="sel-count">${num(lease.core_count)} core</span></div>
            <div class="sel-ranges">${esc(lease.core_label)}</div>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn btn-ghost" data-action="modal-close">Batal</button>
          <button type="submit" class="btn btn-danger">Lepas ${num(lease.core_count)} core</button>
        </div>
      </form>`);
  }

  function openTenantModal(tenant) {
    const isEdit = Boolean(tenant);
    openModal(`
      ${modalHead(isEdit ? "Edit tenant" : "Tambah tenant", isEdit ? tenant.code : "Tenant baru untuk pengelolaan dark fiber")}
      <form id="form-tenant">
        <div class="modal-body">
          <input type="hidden" name="id" value="${isEdit ? tenant.id : ""}">
          <div class="grid2">
            <div class="field">
              <label for="tn-code">Kode tenant</label>
              <input id="tn-code" name="code" value="${isEdit ? esc(tenant.code) : ""}" placeholder="otomatis bila kosong" ${isEdit ? "disabled" : ""}>
            </div>
            <div class="field">
              <label for="tn-status">Status</label>
              <select id="tn-status" name="status">
                <option value="active" ${isEdit && tenant.status === "active" ? "selected" : ""}>Aktif</option>
                <option value="suspended" ${isEdit && tenant.status === "suspended" ? "selected" : ""}>Suspend</option>
                <option value="inactive" ${isEdit && tenant.status === "inactive" ? "selected" : ""}>Nonaktif</option>
              </select>
            </div>
          </div>
          <div class="field field-row">
            <label for="tn-name">Nama tenant</label>
            <input id="tn-name" name="name" value="${isEdit ? esc(tenant.name) : ""}" placeholder="PT Contoh Nusantara" required>
          </div>
          <div class="grid2">
            <div class="field">
              <label for="tn-pic">PIC</label>
              <input id="tn-pic" name="pic" value="${isEdit ? esc(tenant.pic || "") : ""}" placeholder="Nama penanggung jawab">
            </div>
            <div class="field">
              <label for="tn-contact">Telepon</label>
              <input id="tn-contact" name="contact" value="${isEdit ? esc(tenant.contact || "") : ""}" placeholder="+62 8xx-xxxx-xxxx">
            </div>
          </div>
          <div class="field field-row">
            <label for="tn-email">Email</label>
            <input id="tn-email" type="email" name="email" value="${isEdit ? esc(tenant.email || "") : ""}" placeholder="noc@contoh.id">
          </div>
          <div class="field">
            <label for="tn-notes">Catatan</label>
            <textarea id="tn-notes" name="notes" rows="2">${isEdit ? esc(tenant.notes || "") : ""}</textarea>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn btn-ghost" data-action="modal-close">Batal</button>
          <button type="submit" class="btn btn-primary">${isEdit ? "Simpan perubahan" : "Tambah tenant"}</button>
        </div>
      </form>`, { wide: true });
  }

  function openCableModal(cable) {
    const isEdit = Boolean(cable);
    openModal(`
      ${modalHead(isEdit ? "Edit kabel" : "Tambah kabel", isEdit ? cable.code : "Kabel backbone 96, 288, atau 576 core")}
      <form id="form-cable">
        <div class="modal-body">
          <input type="hidden" name="id" value="${isEdit ? cable.id : ""}">
          <div class="grid2">
            <div class="field">
              <label for="cb-code">Kode kabel</label>
              <input id="cb-code" name="code" value="${isEdit ? esc(cable.code) : ""}" placeholder="BB-JKT-BDG-02" required>
            </div>
            <div class="field">
              <label for="cb-cores">Jumlah core</label>
              <select id="cb-cores" name="core_count" ${isEdit ? "disabled" : ""}>
                ${[96, 288, 576].map((n) => `<option value="${n}" ${isEdit && cable.core_count === n ? "selected" : ""}>${n} core</option>`).join("")}
              </select>
              ${isEdit ? `<span class="help">Kapasitas core tidak bisa diubah</span>` : ""}
            </div>
          </div>
          <div class="field field-row">
            <label for="cb-name">Nama kabel</label>
            <input id="cb-name" name="name" value="${isEdit ? esc(cable.name) : ""}" placeholder="Jakarta – Bandung" required>
          </div>
          <div class="field">
            <label for="cb-route">Rute</label>
            <input id="cb-route" name="route" value="${isEdit ? esc(cable.route) : ""}" placeholder="Jakarta – Purwakarta – Bandung" required>
          </div>
          <div class="grid2">
            <div class="field">
              <label for="cb-length">Panjang (km)</label>
              <input id="cb-length" type="number" step="0.1" min="0" name="length_km" value="${isEdit && cable.length_km ? cable.length_km : ""}" placeholder="148.5">
            </div>
            <div class="field">
              <label for="cb-year">Tahun terpasang</label>
              <input id="cb-year" type="number" min="1980" max="2100" name="installed_year" value="${isEdit && cable.installed_year ? cable.installed_year : ""}" placeholder="2024">
            </div>
          </div>
          <div class="field field-row">
            <label for="cb-status">Status</label>
            <select id="cb-status" name="status">
              <option value="active" ${isEdit && cable.status === "active" ? "selected" : ""}>Aktif</option>
              <option value="maintenance" ${isEdit && cable.status === "maintenance" ? "selected" : ""}>Maintenance</option>
              <option value="planned" ${isEdit && cable.status === "planned" ? "selected" : ""}>Rencana</option>
            </select>
          </div>
          <div class="field">
            <label for="cb-notes">Catatan</label>
            <textarea id="cb-notes" name="notes" rows="2">${isEdit ? esc(cable.notes || "") : ""}</textarea>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn btn-ghost" data-action="modal-close">Batal</button>
          <button type="submit" class="btn btn-primary">${isEdit ? "Simpan perubahan" : "Tambah kabel"}</button>
        </div>
      </form>`, { wide: true });
  }

  // ---------------------------------------------------------------- actions
  async function handleAction(action, dataset) {
    switch (action) {
      case "modal-close":
        closeModal();
        break;
      case "filter-cable":
        state.cableFilter = dataset.size;
        renderView();
        break;
      case "filter-core":
        state.coreFilter = dataset.filter;
        renderView();
        syncSelection();
        break;
      case "filter-lease":
        state.leaseFilter = dataset.filter;
        renderView();
        break;
      case "sheet-sync": {
        if (state.sheetSyncing) break;
        state.sheetSyncing = true;
        updateChrome(getRoute());
        renderView();
        try {
          const result = await api("/sheet/sync", { method: "POST" });
          state.sheetLoadedGid = "";
          state.sheetSyncing = false;
          // Sync also rebuilds the cable/plant inventory, so refresh everything.
          await refreshBasics();
          await renderApp();
          if (result.busy) {
            toast("Sync sedang berjalan, coba lagi sebentar", "warn");
          } else if (result.errors) {
            toast(`Sync selesai, ${result.errors} tab gagal diakses`, "warn");
          } else if (result.changed) {
            toast(`Sync selesai · ${result.changed} tab diperbarui`);
          } else {
            toast("Semua tab sudah yang terbaru");
          }
        } catch (err) {
          state.sheetSyncing = false;
          updateChrome(getRoute());
          renderView();
          toast(err.message, "bad");
        }
        break;
      }
      case "select-available": {
        if (!state.cable) break;
        for (const core of state.coreIndex.values()) {
          if (!core.allocation) state.selection.set(core.no, core);
        }
        syncSelection();
        if (state.selection.size === 0) toast("Semua core pada kabel ini sudah terpakai", "warn");
        break;
      }
      case "select-clear":
        state.selection.clear();
        state.lastPicked = null;
        syncSelection();
        break;
      case "allocate-open":
        openAllocateModal();
        break;
      case "lease-open":
        openLeaseModal(dataset.batch);
        break;
      case "lease-extend":
        openExtendModal(dataset.batch);
        break;
      case "lease-release":
        openReleaseModal(dataset.batch);
        break;
      case "tenant-new":
        openTenantModal(null);
        break;
      case "tenant-edit":
        if (state.tenant) openTenantModal(state.tenant);
        break;
      case "cable-new":
        openCableModal(null);
        break;
      case "cable-edit":
        if (state.cable) openCableModal(state.cable);
        break;
      default:
        break;
    }
  }

  async function refreshAfterChange(message, { clearSelection = false } = {}) {
    closeModal();
    if (clearSelection) {
      state.selection.clear();
      state.lastPicked = null;
    }
    await refreshBasics();
    await renderApp();
    if (message) toast(message);
  }

  // ---------------------------------------------------------------- forms
  async function submitAllocate(form, button) {
    clearFormError(form);
    const data = new FormData(form);
    const cores = [...state.selection.keys()].sort((a, b) => a - b);
    const payload = {
      tenant_id: Number(data.get("tenant_id")),
      cable_id: state.cable.id,
      cores,
      purpose: data.get("purpose"),
      start_date: data.get("start_date"),
      end_date: data.get("end_date"),
      notes: data.get("notes") || null,
    };
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "Menyimpan…";
    try {
      const lease = await api("/leases", { method: "POST", body: JSON.stringify(payload) });
      await refreshAfterChange(`${num(lease.core_count)} core dialokasikan · ${lease.batch_id}`, { clearSelection: true });
    } catch (err) {
      showFormError(form, err.message);
      toast(err.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  async function submitTenant(form, button) {
    clearFormError(form);
    const data = new FormData(form);
    const id = data.get("id");
    const payload = {
      name: data.get("name"),
      pic: data.get("pic"),
      contact: data.get("contact"),
      email: data.get("email"),
      status: data.get("status"),
      notes: data.get("notes"),
    };
    if (!id) payload.code = data.get("code");
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "Menyimpan…";
    try {
      if (id) {
        await api(`/tenants/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
        await refreshAfterChange("Data tenant diperbarui");
      } else {
        const tenant = await api("/tenants", { method: "POST", body: JSON.stringify(payload) });
        await refreshAfterChange(`Tenant ${tenant.name} ditambahkan`);
      }
    } catch (err) {
      showFormError(form, err.message);
      toast(err.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  async function submitCable(form, button) {
    clearFormError(form);
    const data = new FormData(form);
    const id = data.get("id");
    const payload = {
      code: data.get("code"),
      name: data.get("name"),
      route: data.get("route"),
      length_km: data.get("length_km") ? Number(data.get("length_km")) : null,
      installed_year: data.get("installed_year") ? Number(data.get("installed_year")) : null,
      status: data.get("status"),
      notes: data.get("notes"),
    };
    if (!id) payload.core_count = Number(data.get("core_count"));
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "Menyimpan…";
    try {
      if (id) {
        await api(`/cables/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
        await refreshAfterChange("Data kabel diperbarui");
      } else {
        const cable = await api("/cables", { method: "POST", body: JSON.stringify(payload) });
        closeModal();
        await refreshBasics();
        toast(`Kabel ${cable.code} ditambahkan`);
        location.hash = `#/cables/${cable.id}`;
      }
    } catch (err) {
      showFormError(form, err.message);
      toast(err.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  async function submitExtend(form, button) {
    clearFormError(form);
    const data = new FormData(form);
    const batch = data.get("batch_id");
    const payload = { end_date: data.get("end_date"), purpose: data.get("purpose"), notes: data.get("notes") || null };
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "Menyimpan…";
    try {
      await api(`/leases/${batch}`, { method: "PATCH", body: JSON.stringify(payload) });
      await refreshAfterChange(`Lease ${batch} diperpanjang`);
    } catch (err) {
      showFormError(form, err.message);
      toast(err.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  async function submitRelease(form, button) {
    clearFormError(form);
    const data = new FormData(form);
    const batch = data.get("batch_id");
    const old = button.textContent;
    button.disabled = true;
    button.textContent = "Melepas…";
    try {
      const result = await api(`/leases/${batch}`, { method: "DELETE" });
      await refreshAfterChange(`${num(result.released_cores)} core dilepas dari ${batch}`);
    } catch (err) {
      showFormError(form, err.message);
      toast(err.message, "bad");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  // ------------------------------------------------------------- OTB view
  function fmtWhen(value) {
    if (!value) return "–";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString("id-ID", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function renderOtbView() {
    const status = state.sheetStatus;
    if (!status) {
      viewEl.innerHTML = loadingState();
      return;
    }
    const tabs = status.tabs || [];
    const activeGid = state.sheetTab || (tabs[0] && tabs[0].gid);
    const tab = tabs.find((t) => t.gid === activeGid) || tabs[0] || null;
    const all = state.sheetEntries || [];
    const q = state.sheetQuery.trim().toLowerCase();
    const entries = q
      ? all.filter((e) =>
          [e.section, e.panel, e.port, e.destination, e.note, Object.values(e.fields || {}).join(" ")]
            .filter(Boolean)
            .join(" ")
            .toLowerCase()
            .includes(q),
        )
      : all;

    const groups = [];
    const byKey = new Map();
    for (const entry of entries) {
      const key = `${entry.section || ""}\u0000${entry.panel || ""}`;
      if (!byKey.has(key)) {
        const group = { section: entry.section, panel: entry.panel, rows: [] };
        byKey.set(key, group);
        groups.push(group);
      }
      byKey.get(key).rows.push(entry);
    }

    const failed = tabs.filter((t) => t.error);

    viewEl.innerHTML = `
      <div class="otb-layout">
        <aside class="card otb-index">
          <div class="otb-index-head">
            <span class="eyebrow">Gedung / rak</span>
            <span class="mono muted">${tabs.length}</span>
          </div>
          <nav class="otb-tabs" aria-label="Pilih gedung atau rak">
            ${tabs
              .map(
                (t) => `<a class="otb-tab ${t.gid === activeGid ? "active" : ""}" href="#/otb/${t.gid}">
                  <span class="otb-tab-name">${esc(t.name)}</span>
                  <span class="otb-tab-meta">${t.error ? `<span class="state state-bad"><span class="dot"></span></span>` : ""}<span class="mono">${num(t.entries)}</span></span>
                </a>`,
              )
              .join("")}
          </nav>
          <div class="otb-sync">
            <p>Sync terakhir <span class="mono">${fmtWhen(status.last_sync)}</span></p>
            <p class="muted">Otomatis tiap ${num(status.poll_seconds)} detik</p>
            ${failed.length ? `<p class="state state-warn"><span class="dot"></span>${failed.length} tab gagal diakses</p>` : ""}
            <a class="link" href="${esc(status.spreadsheet_url)}" target="_blank" rel="noopener">Buka Google Sheet ↗</a>
          </div>
        </aside>

        <section class="otb-main">
          <div class="card otb-head">
            <div>
              <h2 class="otb-title">${esc(tab ? tab.name : "—")}</h2>
              <p class="otb-sub" id="otb-count">${num(entries.length)}${q ? ` dari ${num(all.length)}` : ""} entri port · ${groups.length} panel</p>
            </div>
            <label class="search otb-search">
              <svg class="ico"><use href="#i-search"/></svg>
              <input id="sheet-q" type="search" value="${esc(state.sheetQuery)}" placeholder="Cari port, tujuan, OTB" autocomplete="off" aria-label="Cari di peta OTB">
            </label>
          </div>

          ${tab && tab.error ? `<div class="card otb-error"><svg class="ico"><use href="#i-alert"/></svg><div><strong>Gagal sync tab ini</strong><p>${esc(tab.error)}</p></div></div>` : ""}

          ${
            entries.length
              ? `<div class="table-wrap otb-table"><table>
                  <thead><tr><th>Port / user</th><th>Tujuan</th><th>Keterangan</th></tr></thead>
                  <tbody>
                    ${groups
                      .map(
                        (group) => `
                        <tr class="otb-group"><td colspan="3">
                          ${group.section ? `<span class="otb-section">${esc(group.section)}</span>` : ""}
                          <span class="otb-panel">${esc(group.panel || "Tanpa panel")}</span>
                          <span class="otb-count">${num(group.rows.length)} port</span>
                        </td></tr>
                        ${group.rows
                          .map(
                            (row) => `<tr data-search="${esc(
                              [row.section, row.panel, row.port, row.destination, row.note]
                                .filter(Boolean)
                                .join(" ")
                                .toLowerCase(),
                            )}">
                              <td class="otb-port">${esc(row.port) || "–"}</td>
                              <td>${esc(row.destination) || "<span class='muted'>–</span>"}</td>
                              <td class="otb-note">${esc(row.note) || "<span class='muted'>–</span>"}</td>
                            </tr>`,
                          )
                          .join("")}`,
                      )
                      .join("")}
                  </tbody>
                </table></div>`
              : emptyState(
                  q ? "Tidak ada hasil" : "Belum ada data OTB",
                  q ? `Tidak ada entri yang cocok dengan “${state.sheetQuery}”.` : "Jalankan sync untuk menarik data dari Google Sheets.",
                  "i-layers",
                )
          }
        </section>
      </div>`;
  }

  // ---------------------------------------------------------------- events
  document.addEventListener("click", (event) => {
    const coreBtn = event.target.closest("[data-core]");
    if (coreBtn) {
      event.preventDefault();
      handleCoreClick(coreBtn, event);
      return;
    }
    const actionEl = event.target.closest("[data-action]");
    if (actionEl) {
      event.preventDefault();
      handleAction(actionEl.dataset.action, actionEl.dataset);
      return;
    }
    const leaseEl = event.target.closest("[data-lease-open]");
    if (leaseEl) {
      event.preventDefault();
      openLeaseModal(leaseEl.dataset.leaseOpen);
      return;
    }
    const routeEl = event.target.closest("[data-route]");
    if (routeEl) {
      event.preventDefault();
      location.hash = routeEl.dataset.route;
      return;
    }
    if (event.target.matches("[data-overlay]")) closeModal();
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    const formId = form.getAttribute("id");
    if (!formId) return;
    event.preventDefault();
    const button = form.querySelector("[type=submit]");
    if (!button || button.disabled) return;
    if (formId === "form-allocate") return submitAllocate(form, button);
    if (formId === "form-tenant") return submitTenant(form, button);
    if (formId === "form-cable") return submitCable(form, button);
    if (formId === "form-extend") return submitExtend(form, button);
    if (formId === "form-release") return submitRelease(form, button);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeModal();
      hideSearchPop();
    }
    if (event.key === "/" && !event.target.matches("input, textarea, select") && !$("#search-wrap").classList.contains("hidden")) {
      event.preventDefault();
      searchEl.focus();
      searchEl.select();
    }
  });

  searchEl.addEventListener("focus", () => {
    if (state.searchQuery.trim().length >= 2) renderSearchPop();
  });

  searchEl.addEventListener("input", runGlobalSearch);

  searchEl.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = $("#search-pop .sp-row");
    if (first) {
      first.click(); // ikut handler click di bawah (set searchJump)
    } else if (state.searchQuery.trim().length >= 2) {
      // Ada kata kunci tapi dropdown belum ada hasil: langsung ke Peta OTB
      // dengan filter kata kunci itu.
      state.searchJump = { q: state.searchQuery.trim() };
      location.hash = "#/otb";
    }
  });

  document.addEventListener("click", (event) => {
    const jump = event.target.closest("#search-pop a[data-goto-gid]");
    if (jump) {
      state.searchJump = { gid: jump.dataset.gotoGid, q: jump.dataset.gotoQ || state.searchQuery };
      // Udah di halaman OTB tab yang sama: langsung terapkan filter-nya.
      const route = getRoute();
      if (route.name === "otb" && route.id === jump.dataset.gotoGid) {
        state.sheetQuery = state.searchJump.q || "";
        state.searchJump = null;
        renderView();
      }
    }
    const box = $("#search-box");
    if (box && !box.contains(event.target)) hideSearchPop();
    const link = event.target.closest("#search-pop a");
    if (link) {
      hideSearchPop();
      searchEl.blur();
    }
  });

  viewEl.addEventListener("input", (event) => {
    if (event.target.id !== "sheet-q") return;
    state.sheetQuery = event.target.value;
    const term = state.sheetQuery.trim().toLowerCase();
    const rows = $$("tr[data-search]", viewEl);
    for (const row of rows) {
      row.classList.toggle("is-hidden", Boolean(term) && !row.dataset.search.includes(term));
    }
    let visibleRows = 0;
    let visibleGroups = 0;
    for (const group of $$("tr.otb-group", viewEl)) {
      let count = 0;
      let node = group.nextElementSibling;
      while (node && !node.classList.contains("otb-group")) {
        if (node.dataset.search !== undefined && !node.classList.contains("is-hidden")) count += 1;
        node = node.nextElementSibling;
      }
      group.classList.toggle("is-hidden", count === 0);
      if (count) visibleGroups += 1;
      visibleRows += count;
    }
    const counter = $("#otb-count");
    if (counter) {
      counter.textContent = `${num(visibleRows)}${term ? ` dari ${num(rows.length)}` : ""} entri port · ${visibleGroups} panel`;
    }
  });

  window.addEventListener("hashchange", () => {
    renderApp();
  });

  // Keep the OTB view in step with the server-side sheet sync.
  setInterval(async () => {
    if (getRoute().name !== "otb" || document.hidden) return;
    if (document.activeElement && document.activeElement.id === "sheet-q") return;
    try {
      state.sheetStatus = await api("/sheet/status");
      if (!state.sheetQuery && state.sheetTab) {
        const data = await api(`/sheet/entries?gid=${encodeURIComponent(state.sheetTab)}`);
        state.sheetEntries = data.entries || [];
        state.sheetLoadedGid = state.sheetTab;
      }
      updateChrome(getRoute());
      renderView();
    } catch {
      /* keep the last snapshot visible */
    }
  }, 60000);

  // ---------------------------------------------------------------- boot
  async function boot() {
    viewEl.innerHTML = loadingState();
    try {
      state.meta = await api("/meta");
      await refreshBasics();
      if (!location.hash) history.replaceState(null, "", "#/overview");
      await renderApp();
    } catch (err) {
      viewEl.innerHTML = emptyState("Gagal terhubung ke server", err.message, "i-alert");
    }
  }

  boot();
})();
