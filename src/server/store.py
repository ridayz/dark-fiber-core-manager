"""Data layer for the dark fiber core manager.

SQLite is used as a single-file store. Cores are derived from the cable
capacity (96 / 288 / 576) instead of being stored one row per core: the
12-fiber color code is deterministic, so only leases need persistence.
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import sheets

DB_PATH = os.environ.get(
    "DARKFIBER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "darkfiber.db"),
)

FIBER_COLORS = [
    ("Biru", "#3b82f6"),
    ("Jingga", "#f97316"),
    ("Hijau", "#22c55e"),
    ("Coklat", "#a16207"),
    ("Abu", "#64748b"),
    ("Putih", "#e2e8f0"),
    ("Merah", "#ef4444"),
    ("Hitam", "#1f2937"),
    ("Kuning", "#eab308"),
    ("Ungu", "#8b5cf6"),
    ("Merah muda", "#f43f5e"),
    ("Aqua", "#06b6d4"),
]

TENANT_COLORS = [
    "#38bdf8",
    "#fbbf24",
    "#a78bfa",
    "#fb7185",
    "#34d399",
    "#fb923c",
    "#f472b6",
    "#93c5fd",
]

# Sizes offered when a cable is created manually; sheet plants can carry any
# multiple of 12 (24 / 48 / 72 / 144 / 288 / 576 ...).
VALID_CORE_COUNTS = (12, 24, 48, 72, 96, 144, 288, 576)


def valid_core_count(value) -> bool:
    return isinstance(value, int) and 0 < value <= 2880 and value % 12 == 0
EXPIRING_DAYS = 30

PURPOSES = [
    "Dark Fiber Lease",
    "Backbone Transmisi",
    "Koneksi Data Center",
    "Lastmile Aggregation",
    "Disaster Recovery Link",
]


def today() -> date:
    return date.today()


def iso(d: date) -> str:
    return d.isoformat()


def core_color(core_no: int) -> dict:
    name, hex_ = FIBER_COLORS[(core_no - 1) % 12]
    return {
        "name": name,
        "hex": hex_,
        "pos": (core_no - 1) % 12 + 1,
        "tube": (core_no - 1) // 12 + 1,
    }


def tenant_color(tenant_id: int) -> str:
    return TENANT_COLORS[(tenant_id - 1) % len(TENANT_COLORS)]


def lease_state(start_date: str, end_date: str, released_at: str | None, ref: date | None = None) -> str:
    """State shown in the UI. Cores stay occupied until a lease is released."""
    if released_at:
        return "released"
    ref = ref or today()
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > ref:
        return "reserved"
    if end < ref:
        return "expired"
    if end <= ref + timedelta(days=EXPIRING_DAYS):
        return "expiring"
    return "active"


def days_left(end_date: str, ref: date | None = None) -> int:
    ref = ref or today()
    return (date.fromisoformat(end_date) - ref).days


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS cables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    route TEXT NOT NULL,
    core_count INTEGER NOT NULL,
    length_km REAL,
    installed_year INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT,
    -- Plant fields populated by sync from Google Sheet OTB panels.
    -- Manual cables have gid = NULL / panel_code = NULL.
    gid TEXT,
    panel_code TEXT,
    section TEXT,
    total_ports INTEGER NOT NULL DEFAULT 0,
    tab_name TEXT,
    synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cable_plant
    ON cables(gid, panel_code)
    WHERE gid IS NOT NULL AND panel_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    pic TEXT,
    contact TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    cable_id INTEGER NOT NULL REFERENCES cables(id),
    core_no INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT,
    released_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_core_active
    ON allocations(cable_id, core_no)
    WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_batch ON allocations(batch_id);
CREATE INDEX IF NOT EXISTS idx_alloc_tenant ON allocations(tenant_id);

CREATE TABLE IF NOT EXISTS sheet_tabs (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    fetched_at TEXT,
    checked_at TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS sheet_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    row_no INTEGER NOT NULL,
    col_no INTEGER NOT NULL,
    section TEXT,
    panel TEXT,
    port TEXT,
    destination TEXT,
    note TEXT,
    fields_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sheet_entries_gid ON sheet_entries(gid, row_no);
CREATE INDEX IF NOT EXISTS idx_sheet_entries_panel ON sheet_entries(gid, panel);

-- Cable plant inventory — derived from Google Sheet OTB panels during sync.
-- One row per OTB / MDF / ODC equipment unit. Core capacity is parsed from
-- the panel name (e.g. "OTB 96.1" → 96, "OTB 144" → 144).
CREATE TABLE IF NOT EXISTS cable_plants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,              -- sheet tab gid
    panel_code TEXT NOT NULL,       -- e.g. "OTB 96.1 RAK 001 JK2"
    core_count INTEGER NOT NULL DEFAULT 0,
    route TEXT,                     -- derived from destination/note of first entry
    notes TEXT,                     -- additional info from first matching row
    section TEXT,                   -- rack/building section
    total_ports INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    synced_at TEXT,                 -- last sheet sync timestamp
    UNIQUE(gid, panel_code)
);
"""


# Demo tenants only — the cable/plant inventory comes from the Google Sheet
# sync (see sheets.sync_plants) so the dashboard never shows stock data.
TENANTS = [
    ("NF-001", "PT Nusantara Fiber", "Rizky Pratama", "+62 811-2000-341", "noc@nusantarafiber.id", "active"),
    ("SDP-002", "PT Sentra Data Prima", "Dewi Anggraini", "+62 812-8831-090", "noc@sentradata.co.id", "active"),
    ("AKN-003", "CV Akses Kabel Nusantara", "Budi Santoso", "+62 857-2210-774", "support@akseskabel.net", "active"),
    ("TDN-004", "PT Trans Data Nusantara", "Maya Kusuma", "+62 813-9002-512", "noc@tdn.id", "active"),
    ("CII-005", "PT Cloudindo Infrastruktur", "Fajar Nugroho", "+62 878-4411-208", "ops@cloudindo.id", "active"),
    ("MBB-006", "CV Mitra Backbone Bersama", "Sari Wulandari", "+62 821-3344-909", "sari@mitrabackbone.id", "active"),
]


def init_db(db_path: str | None = None, seed: bool = True) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        if seed and conn.execute("SELECT COUNT(*) AS c FROM tenants").fetchone()["c"] == 0:
            seed_demo(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that older databases do not have yet."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cables)")}
    for name, ddl in (
        ("gid", "TEXT"),
        ("panel_code", "TEXT"),
        ("section", "TEXT"),
        ("total_ports", "INTEGER NOT NULL DEFAULT 0"),
        ("tab_name", "TEXT"),
        ("synced_at", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE cables ADD COLUMN {name} {ddl}")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_cable_plant
           ON cables(gid, panel_code)
           WHERE gid IS NOT NULL AND panel_code IS NOT NULL"""
    )


def seed_demo(conn: sqlite3.Connection) -> None:
    for code, name, pic, contact, email, status in TENANTS:
        conn.execute(
            "INSERT INTO tenants (code, name, pic, contact, email, status) VALUES (?, ?, ?, ?, ?, ?)",
            (code, name, pic, contact, email, status),
        )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

_PORT_NUM_RE = re.compile(r"\d+")


def parse_port_numbers(port_text: str) -> list[int]:
    """Expand a sheet ``Port`` cell like ``Port 11-12`` or ``Port 27&30`` into core numbers.

    Rules that match the OTB sheets in use:
    - ``27&30`` / ``16 & 42`` → individual numbers
    - ``11-12`` / ``13-24`` → inclusive range
    - ``49`` / ``Port 1&2 (SC Konektor)`` → numbers found in free text
    - empty / non-numeric → []
    """
    if not port_text:
        return []
    text = str(port_text).strip()
    if not text:
        return []
    # Miss-fix: parser sometimes captured a whole panel title in Port column,
    # e.g. "OTB 288.3 Rak 001 BKT Jarakosta >> to Jakarta ...". Those are not
    # port numbers — reject them so they don't fabricate cores 1/3/288.
    if len(text) > 50 or ">>" in text or (text.upper().startswith("OTB") and text.count(" ") >= 4 and not text.lower().startswith("port")):
        return []
    # Named range: X-Y (allow en dash)
    nums: list[int] = []
    seen: set[int] = set()
    token_re = re.compile(r"(\d+)\s*[-–—]\s*(\d+)|(\d+)")
    for m in token_re.finditer(text):
        if m.group(3) is not None:
            n = int(m.group(3))
            if n not in seen and n > 0:
                seen.add(n)
                nums.append(n)
        else:
            start, end = int(m.group(1)), int(m.group(2))
            if end < start:
                continue
            # Cap absurd ranges to avoid blowing up the core map.
            if end - start + 1 > 1000:
                continue
            for n in range(start, end + 1):
                if n not in seen and n > 0:
                    seen.add(n)
                    nums.append(n)
    return nums


def _sheet_stats(conn: sqlite3.Connection, gid: str | None, panel_code: str | None, core_count: int) -> dict:
    """Mirror sheet entries for one cable panel.

    ``sheet_total`` = rows with a real Port value (so it matches
    ``cable_plants.total_ports`` and the 361 KPI, not 390 raw rows).
    ``sheet_used`` = those with ``destination`` (Tujuan) also filled.
    ``sheet_used_cores`` = distinct core numbers parsed from used rows.
    """
    if not gid or not panel_code:
        return {
            "sheet_total": 0,
            "sheet_used": 0,
            "sheet_used_cores": 0,
            "sheet_map": {},
        }
    rows = conn.execute(
        "SELECT port, destination, note, section, panel FROM sheet_entries WHERE gid = ? AND panel = ?",
        (gid, panel_code),
    ).fetchall()
    # Only rows with a real Port value count toward total (unified with
    # total_ports 361). Empty-port rows are notes, not port inventory.
    counted = [r for r in rows if (r["port"] or "").strip()]
    total = len(counted)
    sheet_map: dict[int, dict] = {}
    used_set: set[int] = set()
    for r in counted:
        if not (r["destination"] or "").strip():
            continue
        for n in parse_port_numbers(r["port"] or ""):
            if core_count and n > core_count:
                continue
            used_set.add(n)
            if n not in sheet_map:
                sheet_map[n] = {
                    "port": r["port"],
                    "destination": r["destination"],
                    "note": r["note"],
                    "section": r["section"],
                }
    return {
        "sheet_total": total,
        "sheet_used": len([r for r in counted if (r["destination"] or "").strip()]),
        "sheet_used_cores": len(used_set),
        "sheet_map": sheet_map,
    }


def _cable_stats(conn: sqlite3.Connection, cable: sqlite3.Row, ref: date) -> dict:
    used = conn.execute(
        "SELECT COUNT(*) AS c FROM allocations WHERE cable_id = ? AND released_at IS NULL",
        (cable["id"],),
    ).fetchone()["c"]
    total = cable["core_count"]
    sheet = _sheet_stats(conn, cable["gid"], cable["panel_code"], total)
    sheet_total = sheet["sheet_total"]
    sheet_used = sheet["sheet_used"]
    sheet_used_cores = sheet["sheet_used_cores"]
    # Overlap between lease allocations and sheet cores (sheet mirror only,
    # never overrides lease; shown so Ringkasan can explain the difference).
    leased_core_nos = {
        r["core_no"]
        for r in conn.execute(
            "SELECT core_no FROM allocations WHERE cable_id = ? AND released_at IS NULL",
            (cable["id"],),
        ).fetchall()
    }
    sheet_only = sum(1 for n in sheet["sheet_map"] if n not in leased_core_nos)
    combined = used + sheet_only
    return {
        "id": cable["id"],
        "code": cable["code"],
        "name": cable["name"],
        "route": cable["route"],
        "core_count": total,
        "length_km": cable["length_km"],
        "installed_year": cable["installed_year"],
        "status": cable["status"],
        "notes": cable["notes"],
        "gid": cable["gid"],
        "panel_code": cable["panel_code"],
        "section": cable["section"],
        "total_ports": cable["total_ports"] or 0,
        "tab_name": cable["tab_name"],
        "synced_at": cable["synced_at"],
        "source": "sheet" if cable["gid"] else "manual",
        "leased": used,
        "available": total - used,
        "utilization": round(used / total * 100, 1) if total else 0,
        # ---- combined physical usage (lease + sheet) ----
        # 0/288 is misleading when the sheet already shows used ports,
        # so the headline number is lease ∪ sheet. `leased` stays lease-only
        # for lease management and tests.
        "used_combined": combined,
        "available_combined": total - combined,
        "utilization_combined": round(combined / total * 100, 1) if total else 0,
        # ---- sheet mirror (Peta OTB) ----
        "sheet_total": sheet_total,
        "sheet_used": sheet_used,
        "sheet_used_cores": sheet_used_cores,
        "sheet_utilization": round(sheet_used_cores / total * 100, 1) if total else 0,
        "sheet_port_utilization": round(sheet_used / sheet_total * 100, 1) if sheet_total else 0,
        "sheet_only_cores": sheet_only,
    }


def list_cables(conn: sqlite3.Connection) -> list[dict]:
    ref = today()
    rows = conn.execute(
        "SELECT * FROM cables ORDER BY (gid IS NULL), core_count DESC, name"
    ).fetchall()
    return [_cable_stats(conn, r, ref) for r in rows]


def get_cable(conn: sqlite3.Connection, cable_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM cables WHERE id = ?", (cable_id,)).fetchone()
    if not row:
        return None
    ref = today()
    cable = _cable_stats(conn, row, ref)
    allocs = conn.execute(
        """SELECT a.*, t.name AS tenant_name
           FROM allocations a JOIN tenants t ON t.id = a.tenant_id
           WHERE a.cable_id = ? AND a.released_at IS NULL
           ORDER BY a.core_no""",
        (cable_id,),
    ).fetchall()
    by_core = {a["core_no"]: a for a in allocs}
    sheet = _sheet_stats(conn, row["gid"], row["panel_code"], cable["core_count"])
    sheet_map = sheet["sheet_map"]

    tubes = []
    tube_count = cable["core_count"] // 12
    for tube_no in range(1, tube_count + 1):
        cores = []
        for pos in range(1, 13):
            core_no = (tube_no - 1) * 12 + pos
            color = core_color(core_no)
            alloc = by_core.get(core_no)
            sheet_hit = sheet_map.get(core_no)
            entry = {
                "no": core_no,
                "pos": pos,
                "color": color["hex"],
                "color_name": color["name"],
                "status": "available",
                "allocation": None,
                "sheet": sheet_hit,
            }
            if alloc:
                state = lease_state(alloc["start_date"], alloc["end_date"], alloc["released_at"], ref)
                entry["status"] = state
                entry["allocation"] = {
                    "batch_id": alloc["batch_id"],
                    "tenant_id": alloc["tenant_id"],
                    "tenant_name": alloc["tenant_name"],
                    "color": tenant_color(alloc["tenant_id"]),
                    "purpose": alloc["purpose"],
                    "start_date": alloc["start_date"],
                    "end_date": alloc["end_date"],
                    "state": state,
                    "days_left": days_left(alloc["end_date"], ref),
                }
            elif sheet_hit:
                entry["status"] = "sheet"
            cores.append(entry)
        tubes.append({"no": tube_no, "cores": cores})

    tenant_totals = {}
    for a in allocs:
        entry = tenant_totals.setdefault(
            a["tenant_id"],
            {
                "id": a["tenant_id"],
                "name": a["tenant_name"],
                "color": tenant_color(a["tenant_id"]),
                "cores": 0,
            },
        )
        entry["cores"] += 1

    cable["tubes"] = tubes
    cable["tenant_totals"] = sorted(tenant_totals.values(), key=lambda x: -x["cores"])
    cable["entries"] = []
    if row["gid"] and row["panel_code"]:
        raw_entries = sheets.entries(conn, row["gid"], row["panel_code"])
        for e in raw_entries:
            dest = (e.get("destination") or "").strip()
            e["used"] = bool(dest)
            e["core_nos"] = parse_port_numbers(e.get("port") or "")
        cable["entries"] = raw_entries
    return cable


def list_tenants(conn: sqlite3.Connection) -> list[dict]:
    ref = today()
    rows = conn.execute("SELECT * FROM tenants ORDER BY name").fetchall()
    out = []
    for r in rows:
        agg = conn.execute(
            """SELECT COUNT(*) AS cores, COUNT(DISTINCT cable_id) AS cables,
                      MIN(CASE WHEN end_date >= ? THEN end_date END) AS next_end
               FROM allocations WHERE tenant_id = ? AND released_at IS NULL""",
            (iso(ref), r["id"]),
        ).fetchone()
        leases = conn.execute(
            "SELECT DISTINCT batch_id FROM allocations WHERE tenant_id = ? AND released_at IS NULL",
            (r["id"],),
        ).fetchall()
        out.append(
            {
                "id": r["id"],
                "code": r["code"],
                "name": r["name"],
                "pic": r["pic"],
                "contact": r["contact"],
                "email": r["email"],
                "status": r["status"],
                "notes": r["notes"],
                "color": tenant_color(r["id"]),
                "created_at": r["created_at"],
                "cores": agg["cores"],
                "cables": agg["cables"],
                "leases": len(leases),
                "next_end": agg["next_end"],
            }
        )
    return out


def get_tenant(conn: sqlite3.Connection, tenant_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not row:
        return None
    tenant = next((t for t in list_tenants(conn) if t["id"] == tenant_id), None)
    rows = conn.execute(
        """SELECT a.batch_id, a.cable_id, c.name AS cable_name, c.code AS cable_code,
                  a.purpose, a.start_date, a.end_date, a.notes,
                  COUNT(*) AS cores, MIN(a.core_no) AS core_from, MAX(a.core_no) AS core_to
           FROM allocations a JOIN cables c ON c.id = a.cable_id
           WHERE a.tenant_id = ? AND a.released_at IS NULL
           GROUP BY a.batch_id
           ORDER BY a.end_date""",
        (tenant_id,),
    ).fetchall()
    leases = []
    for r in rows:
        leases.append(
            {
                "batch_id": r["batch_id"],
                "cable_id": r["cable_id"],
                "cable_name": r["cable_name"],
                "cable_code": r["cable_code"],
                "purpose": r["purpose"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "notes": r["notes"],
                "cores": r["cores"],
                "core_from": r["core_from"],
                "core_to": r["core_to"],
                "state": lease_state(r["start_date"], r["end_date"], None),
                "days_left": days_left(r["end_date"]),
            }
        )
    tenant["leases_detail"] = leases
    return tenant


def _core_label(core_nos: list[int]) -> str:
    """Compact a core number list into ranges: [1,2,3,7] -> '1–3, 7'."""
    if not core_nos:
        return ""
    parts = []
    start = prev = core_nos[0]
    for n in core_nos[1:]:
        if n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    parts.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(parts)


def list_leases(conn: sqlite3.Connection, ref: date | None = None) -> list[dict]:
    ref = ref or today()
    rows = conn.execute(
        """SELECT a.batch_id, a.tenant_id, a.cable_id, a.purpose, a.start_date, a.end_date,
                  a.notes, t.name AS tenant_name, c.name AS cable_name, c.code AS cable_code,
                  a.core_no
           FROM allocations a
           JOIN tenants t ON t.id = a.tenant_id
           JOIN cables c ON c.id = a.cable_id
           WHERE a.released_at IS NULL
           ORDER BY a.end_date, a.batch_id, a.core_no"""
    ).fetchall()
    grouped: dict[str, dict] = {}
    for r in rows:
        item = grouped.get(r["batch_id"])
        if not item:
            item = {
                "batch_id": r["batch_id"],
                "tenant_id": r["tenant_id"],
                "tenant_name": r["tenant_name"],
                "tenant_color": tenant_color(r["tenant_id"]),
                "cable_id": r["cable_id"],
                "cable_name": r["cable_name"],
                "cable_code": r["cable_code"],
                "purpose": r["purpose"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "notes": r["notes"],
                "cores": [],
            }
            grouped[r["batch_id"]] = item
        item["cores"].append(r["core_no"])
    out = []
    for item in grouped.values():
        item["core_count"] = len(item["cores"])
        item["core_label"] = _core_label(item["cores"])
        item["state"] = lease_state(item["start_date"], item["end_date"], None, ref)
        item["days_left"] = days_left(item["end_date"], ref)
        out.append(item)
    out.sort(key=lambda x: (x["end_date"], x["batch_id"]))
    return out


def list_plants(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, gid, panel_code, core_count, route, notes, total_ports, status, synced_at
           FROM cable_plants
           ORDER BY synced_at DESC, panel_code"""
    ).fetchall()
    names = dict(sheets.TABS)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "gid": r["gid"],
            "panel_code": r["panel_code"],
            "core_count": r["core_count"] or 0,
            "route": r["route"],
            "notes": r["notes"],
            "total_ports": r["total_ports"] or 0,
            "status": r["status"],
            "synced_at": r["synced_at"],
            "tab_name": names.get(r["gid"], r["gid"]),
        })
    return out


def get_plant(conn: sqlite3.Connection, plant_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM cable_plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        return None
    plant = dict(row)
    # Miss-fix: filter by panel too, not the whole tab (was returning 103
    # entries for a 7-row panel).
    plant["entries"] = sheets.entries(conn, plant["gid"], plant["panel_code"])
    plant["tab_name"] = dict(sheets.TABS).get(plant["gid"], plant["gid"])
    return plant


def summary(conn: sqlite3.Connection) -> dict:
    ref = today()
    cables = list_cables(conn)
    tenants = list_tenants(conn)
    leases = list_leases(conn, ref)

    mix_rows = conn.execute(
        """SELECT a.cable_id, a.tenant_id, t.name AS tenant_name, COUNT(*) AS cores
           FROM allocations a JOIN tenants t ON t.id = a.tenant_id
           WHERE a.released_at IS NULL
           GROUP BY a.cable_id, a.tenant_id
           ORDER BY cores DESC"""
    ).fetchall()
    mix: dict[int, list] = {}
    for r in mix_rows:
        mix.setdefault(r["cable_id"], []).append(
            {
                "tenant_id": r["tenant_id"],
                "name": r["tenant_name"],
                "color": tenant_color(r["tenant_id"]),
                "cores": r["cores"],
            }
        )
    for cable in cables:
        cable["mix"] = mix.get(cable["id"], [])

    capacity = sum(c["core_count"] for c in cables)
    leased = sum(c["leased"] for c in cables)
    expiring = [l for l in leases if l["state"] in ("expiring", "expired")]
    # Sheet mirror totals — data asli dari Peta OTB (Tujuan terisi = kepake)
    sheet_total = sum(c.get("sheet_total", 0) for c in cables)
    sheet_used = sum(c.get("sheet_used", 0) for c in cables)
    sheet_used_cores = sum(c.get("sheet_used_cores", 0) for c in cables)
    sheet_only_cores = sum(c.get("sheet_only_cores", 0) for c in cables)
    combined = sum(c.get("used_combined", 0) for c in cables)
    # Per-cable sheet detail for Ringkasan: only panels that carry data
    sheet_by_cable = [
        {
            "cable_id": c["id"],
            "code": c["code"],
            "name": c["name"],
            "tab_name": c.get("tab_name") or "",
            "panel_code": c.get("panel_code") or c["name"],
            "core_count": c["core_count"],
            "sheet_total": c.get("sheet_total", 0),
            "sheet_used": c.get("sheet_used", 0),
            "sheet_used_cores": c.get("sheet_used_cores", 0),
            "used_combined": c.get("used_combined", 0),
        }
        for c in sorted(cables, key=lambda c: -c.get("sheet_used", 0))
        if c.get("sheet_total", 0) > 0
    ]
    return {
        "totals": {
            "capacity": capacity,
            "leased": leased,
            "available": capacity - leased,
            "utilization": round(leased / capacity * 100, 1) if capacity else 0,
            "used_combined": combined,
            "available_combined": capacity - combined,
            "utilization_combined": round(combined / capacity * 100, 1) if capacity else 0,
            "cables": len(cables),
            "tenants": len([t for t in tenants if t["status"] == "active"]),
            "leases": len(leases),
            "expiring": len(expiring),
            "sheet_total": sheet_total,
            "sheet_used": sheet_used,
            "sheet_used_cores": sheet_used_cores,
            "sheet_only_cores": sheet_only_cores,
            "sheet_utilization": round(sheet_used_cores / capacity * 100, 1) if capacity else 0,
            "sheet_port_utilization": round(sheet_used / sheet_total * 100, 1) if sheet_total else 0,
        },
        "sheet_by_cable": sheet_by_cable,
        "cables": cables,
        "top_tenants": sorted(
            [t for t in tenants if t["cores"] > 0], key=lambda t: -t["cores"]
        )[:6],
        "expiring": sorted(expiring, key=lambda x: x["end_date"])[:8],
        "by_size": [
            {
                "size": size,
                "capacity": sum(c["core_count"] for c in cables if c["core_count"] == size),
                "leased": sum(c["leased"] for c in cables if c["core_count"] == size),
                "used_combined": sum(c.get("used_combined", 0) for c in cables if c["core_count"] == size),
                "sheet_used_cores": sum(c.get("sheet_used_cores", 0) for c in cables if c["core_count"] == size),
                "sheet_total": sum(c.get("sheet_total", 0) for c in cables if c["core_count"] == size),
                "sheet_used": sum(c.get("sheet_used", 0) for c in cables if c["core_count"] == size),
            }
            for size in sorted({c["core_count"] for c in cables if c["core_count"]})
        ],
        "plants": list_plants(conn),
    }


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class StoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def create_tenant(conn: sqlite3.Connection, data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise StoreError("Nama tenant wajib diisi")
    code = (data.get("code") or "").strip().upper()
    if not code:
        base = "".join(w[0] for w in name.split()[:3]).upper() or "TNT"
        code = f"{base}-{uuid.uuid4().hex[:3].upper()}"
    try:
        cur = conn.execute(
            """INSERT INTO tenants (code, name, pic, contact, email, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                code,
                name,
                data.get("pic"),
                data.get("contact"),
                data.get("email"),
                data.get("status") or "active",
                data.get("notes"),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise StoreError(f"Kode tenant '{code}' sudah dipakai", 409)
    return next(t for t in list_tenants(conn) if t["id"] == cur.lastrowid)


def update_tenant(conn: sqlite3.Connection, tenant_id: int, data: dict) -> dict:
    row = conn.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not row:
        raise StoreError("Tenant tidak ditemukan", 404)
    fields = ["name", "pic", "contact", "email", "status", "notes"]
    updates = {k: data[k] for k in fields if k in data}
    if "name" in updates and not str(updates["name"]).strip():
        raise StoreError("Nama tenant wajib diisi")
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE tenants SET {sets} WHERE id = ?", (*updates.values(), tenant_id))
        conn.commit()
    return next(t for t in list_tenants(conn) if t["id"] == tenant_id)


def create_cable(conn: sqlite3.Connection, data: dict) -> dict:
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    route = (data.get("route") or "").strip()
    try:
        cores = int(data.get("core_count"))
    except (TypeError, ValueError):
        raise StoreError("Jumlah core harus kelipatan 12")
    if not valid_core_count(cores):
        raise StoreError("Jumlah core harus kelipatan 12 (mis. 24, 48, 72, 96, 144, 288, 576)")
    if not name or not code or not route:
        raise StoreError("Kode, nama, dan rute wajib diisi")
    try:
        cur = conn.execute(
            """INSERT INTO cables (code, name, route, core_count, length_km, installed_year, status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                code,
                name,
                route,
                cores,
                data.get("length_km"),
                data.get("installed_year"),
                data.get("status") or "active",
                data.get("notes"),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise StoreError(f"Kode kabel '{code}' sudah dipakai", 409)
    return next(c for c in list_cables(conn) if c["id"] == cur.lastrowid)


def update_cable(conn: sqlite3.Connection, cable_id: int, data: dict) -> dict:
    if not conn.execute("SELECT id FROM cables WHERE id = ?", (cable_id,)).fetchone():
        raise StoreError("Kabel tidak ditemukan", 404)
    fields = ["code", "name", "route", "length_km", "installed_year", "status", "notes"]
    updates = {k: data[k] for k in fields if k in data}
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE cables SET {sets} WHERE id = ?", (*updates.values(), cable_id))
        conn.commit()
    return next(c for c in list_cables(conn) if c["id"] == cable_id)


def _next_batch_id(conn: sqlite3.Connection, ref: date) -> str:
    prefix = f"DF-{ref.year}-"
    row = conn.execute(
        "SELECT batch_id FROM allocations WHERE batch_id LIKE ? ORDER BY batch_id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["batch_id"].split("-")[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def create_lease(conn: sqlite3.Connection, data: dict) -> dict:
    try:
        tenant_id = int(data.get("tenant_id"))
        cable_id = int(data.get("cable_id"))
    except (TypeError, ValueError):
        raise StoreError("Tenant dan kabel wajib dipilih")
    cores = data.get("cores") or []
    if not isinstance(cores, list) or not cores:
        raise StoreError("Pilih minimal satu core")
    try:
        cores = sorted({int(c) for c in cores})
    except (TypeError, ValueError):
        raise StoreError("Nomor core tidak valid")

    tenant = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    if not tenant:
        raise StoreError("Tenant tidak ditemukan", 404)
    if tenant["status"] != "active":
        raise StoreError("Tenant sedang tidak aktif", 409)

    cable = conn.execute("SELECT * FROM cables WHERE id = ?", (cable_id,)).fetchone()
    if not cable:
        raise StoreError("Kabel tidak ditemukan", 404)
    if cable["status"] == "maintenance":
        raise StoreError("Kabel sedang dalam maintenance, alokasi baru ditutup", 409)
    if cores[0] < 1 or cores[-1] > cable["core_count"]:
        raise StoreError(f"Nomor core harus antara 1 dan {cable['core_count']}")

    placeholders = ",".join("?" for _ in cores)
    conflicts = conn.execute(
        f"""SELECT core_no FROM allocations
            WHERE cable_id = ? AND released_at IS NULL AND core_no IN ({placeholders})""",
        (cable_id, *cores),
    ).fetchall()
    if conflicts:
        labels = _core_label(sorted(r["core_no"] for r in conflicts))
        raise StoreError(f"Core sudah dipakai: {labels}", 409)

    start_date = data.get("start_date") or iso(today())
    end_date = data.get("end_date")
    if not end_date:
        raise StoreError("Tanggal berakhir wajib diisi")
    try:
        if date.fromisoformat(end_date) <= date.fromisoformat(start_date):
            raise StoreError("Tanggal berakhir harus setelah tanggal mulai")
    except ValueError:
        raise StoreError("Format tanggal tidak valid (YYYY-MM-DD)")

    purpose = (data.get("purpose") or "Dark Fiber Lease").strip()
    notes = data.get("notes")
    batch_id = _next_batch_id(conn, today())
    for core_no in cores:
        conn.execute(
            """INSERT INTO allocations
               (batch_id, tenant_id, cable_id, core_no, purpose, start_date, end_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, tenant_id, cable_id, core_no, purpose, start_date, end_date, notes),
        )
    conn.commit()
    return next(l for l in list_leases(conn) if l["batch_id"] == batch_id)


def update_lease(conn: sqlite3.Connection, batch_id: str, data: dict) -> dict:
    rows = conn.execute(
        "SELECT * FROM allocations WHERE batch_id = ? AND released_at IS NULL",
        (batch_id,),
    ).fetchall()
    if not rows:
        raise StoreError("Lease tidak ditemukan", 404)
    fields = []
    values = []
    if "purpose" in data and str(data["purpose"]).strip():
        fields.append("purpose = ?")
        values.append(str(data["purpose"]).strip())
    if "notes" in data:
        fields.append("notes = ?")
        values.append(data["notes"])
    if "end_date" in data:
        end = data["end_date"]
        try:
            if date.fromisoformat(end) <= date.fromisoformat(rows[0]["start_date"]):
                raise StoreError("Tanggal berakhir harus setelah tanggal mulai")
        except ValueError:
            raise StoreError("Format tanggal tidak valid (YYYY-MM-DD)")
        fields.append("end_date = ?")
        values.append(end)
    if not fields:
        raise StoreError("Tidak ada perubahan")
    values.append(batch_id)
    conn.execute(
        f"UPDATE allocations SET {', '.join(fields)} WHERE batch_id = ? AND released_at IS NULL",
        values,
    )
    conn.commit()
    return next(l for l in list_leases(conn) if l["batch_id"] == batch_id)


def release_lease(conn: sqlite3.Connection, batch_id: str) -> dict:
    rows = conn.execute(
        "SELECT id FROM allocations WHERE batch_id = ? AND released_at IS NULL",
        (batch_id,),
    ).fetchall()
    if not rows:
        raise StoreError("Lease tidak ditemukan", 404)
    stamp = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE allocations SET released_at = ? WHERE batch_id = ? AND released_at IS NULL",
        (stamp, batch_id),
    )
    conn.commit()
    return {"batch_id": batch_id, "released_cores": len(rows)}
