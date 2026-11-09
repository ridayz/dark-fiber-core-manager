"""Google Sheets sync for the OTB / rack map.

The source sheet is a human layout, not a database: every tab is one building
or rack (``JK-2``, ``JK-5``, ``MDF A JK1`` ...) and the OTB panels are placed
side by side. Each panel has a header row like ``No. Port | Tujuan | Ket`` and
data rows below it.

This module flattens those blocks into rows that the app can list and search,
then keeps a cached copy in SQLite. The sheet stays read-only: it is the source
of truth for the OTB map, while tenants/leases remain managed by the app.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SPREADSHEET_ID = os.environ.get(
    "SHEET_ID", "19n4TKBRU85SikjXjnT_cYsY559am51TgbOV2wOp6570"
)
POLL_SECONDS = max(15, int(os.environ.get("SHEET_POLL_SECONDS", "60")))

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
SHEET_URL = "https://docs.google.com/spreadsheets/d/{sid}/edit"

# gid, display name — taken from the published tab list.
TABS = [
    ("0", "RAK JK-2"),
    ("846179042", "RAK JK-5"),
    ("851182142", "RAK 18 JK-1"),
    ("334133509", "RAK XL JK-1"),
    ("714418334", "MDF A JK1 DCI"),
    ("850927627", "INR"),
    ("249004981", "DELTAMAS"),
    ("1595097109", "NTT JKT3 CIBITUNG"),
    ("1041365960", "User non GPON"),
    ("1780086120", "GRAND ZURI"),
    ("1665504232", "MDF B - H2-01"),
    ("162485036", "MDF C - H2-01"),
    ("495629564", "MANOR SURYA CIPTA"),
    ("1902613452", "PDG"),
    ("110235621", "ODC KLBD"),
]

HEADER_LABELS = {
    "no. port",
    "no port",
    "no.port",
    "port",
    "tujuan",
    "ket",
    "keterangan",
    "no",
    "no.",
    "nama user",
    "user",
    "cust id",
    "gateway dci",
    "perangkat dilokasi",
    "ip",
    "core",
    "bestray",
    "tipe",
    "status",
}

START_LABELS = {"no. port", "no port", "no.port", "port", "no", "no.", "nama user"}

TITLE_RE = re.compile(r"\b(OTB|ODC|PATCH\s*PANEL|PATCHPANEL|POE|MDF|MMR|HH|USER)\b", re.I)
SECTION_RE = re.compile(r"\b(RAK|BAGIAN|LANTAI|GEDUNG|LT\.?\s*\d)\b", re.I)

_LOCK = threading.Lock()
_SYNCING = False


# --------------------------------------------------------------------- parse
def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _is_header_row(row: list[str]) -> bool:
    labels = [_norm(c) for c in row]
    hits = sum(1 for label in labels if label in HEADER_LABELS)
    if hits < 2:
        return False
    return any(label in {"tujuan", "ket", "keterangan", "user", "core"} for label in labels)


def _is_title_row(row: list[str]) -> bool:
    nonempty = [c.strip() for c in row if c.strip()]
    if len(nonempty) > 2:
        return False
    text = " ".join(nonempty)
    if not text:
        return False
    if TITLE_RE.search(text):
        return True
    # A lone rack/section marker also ends the previous block.
    return len(nonempty) == 1 and bool(SECTION_RE.search(text))


def _block_starts(row: list[str]) -> list[int]:
    starts = [i for i, cell in enumerate(row) if _norm(cell) in START_LABELS]
    if starts:
        return starts
    first = next((i for i, cell in enumerate(row) if cell.strip()), 0)
    return [first]


def _find_title(rows: list[list[str]], header_row: int, start: int) -> str | None:
    for row_no in range(header_row - 1, max(-1, header_row - 7), -1):
        row = rows[row_no]
        for col in range(min(start + 1, len(row)) - 1, max(-1, start - 3), -1):
            if col < 0 or col >= len(row):
                continue
            text = row[col].strip()
            if text and TITLE_RE.search(text):
                return text
    return None


def _find_section(rows: list[list[str]], header_row: int, start: int) -> str | None:
    for row_no in range(header_row - 1, max(-1, header_row - 25), -1):
        row = rows[row_no]
        for col in range(min(start, len(row) - 1), -1, -1):
            text = row[col].strip()
            if not text or TITLE_RE.search(text):
                continue
            if not SECTION_RE.search(text):
                continue
            # Miss-fix: notes like "alibaba | 549855... (IA1-... rak JK1)" contain
            # "rak" but are not section headers. Only accept short headers that
            # name a rack part (BAGIAN DEPAN/BELAKANG, LANTAI) or start with RAK.
            if len(text) > 60 or "|" in text or ">" in text:
                continue
            if "BAGIAN" in text.upper() or "LANTAI" in text.upper() or re.match(r"(?i)RAK\s+\d+", text):
                return text
            # Generic fallback: very short RAK marker alone (<=16 chars)
            if len(text) <= 16 and re.search(r"(?i)\bRAK\b", text):
                return text
    return None


def parse_tab(rows: list[list[str]]) -> list[dict]:
    """Flatten one sheet tab into a list of OTB port entries."""
    if not rows:
        return []
    width = max((len(r) for r in rows), default=0)
    entries: list[dict] = []

    for header_row, row in enumerate(rows):
        if not _is_header_row(row):
            continue

        starts = _block_starts(row)
        last_header_col = max(
            (i for i, cell in enumerate(row) if cell.strip()), default=len(starts) - 1
        )

        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else max(width, last_header_col + 1)
            if end <= start:
                continue

            labels = [
                _norm(row[c]) if c < len(row) and _norm(row[c]) in HEADER_LABELS else f"kolom{c - start + 1}"
                for c in range(start, end)
            ]
            panel = _find_title(rows, header_row, start)
            section = _find_section(rows, header_row, start)
            blank_run = 0

            for data_row in range(header_row + 1, len(rows)):
                next_row = rows[data_row]
                if _is_header_row(next_row):
                    break
                if _is_title_row(next_row):
                    break

                values = [
                    (next_row[c].strip() if c < len(next_row) else "")
                    for c in range(start, end)
                ]
                if not any(values):
                    blank_run += 1
                    if blank_run >= 3:
                        break
                    continue
                blank_run = 0

                fields = {}
                for label, value in zip(labels, values):
                    if value:
                        fields[label] = value

                port = values[0] if values else ""
                destination = values[1] if len(values) > 1 else ""
                note_parts = [v for v in values[2:] if v]
                note = " · ".join(note_parts)

                if not port and not destination and not note:
                    continue

                entries.append(
                    {
                        "row_no": data_row + 1,
                        "col_no": start + 1,
                        "section": section,
                        "panel": panel,
                        "port": port,
                        "destination": destination,
                        "note": note,
                        "fields": fields,
                    }
                )

    return entries


def parse_csv(text: str) -> list[dict]:
    return parse_tab(list(csv.reader(io.StringIO(text))))


# --------------------------------------------------------------------- fetch
def fetch_tab(gid: str, timeout: int = 20) -> str:
    url = EXPORT_URL.format(sid=SPREADSHEET_ID, gid=gid)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "dark-fiber-core-manager/1.0 (+local NOC dashboard)",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("utf-8", "replace")
    if text.lstrip().startswith("<"):
        raise RuntimeError("Sheet tidak bisa diakses (butuh akses publik)")
    return text


def sync(conn, force: bool = False) -> dict:
    """Fetch every tab, store a parsed copy, and report what changed."""
    global _SYNCING
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "busy": True, "tabs": [], "error": "Sync sedang berjalan"}

    _SYNCING = True
    results = []
    changed = 0
    errors = 0

    # Fetch in parallel: one tab normally takes ~1s, fifteen in series is slow.
    fetched: dict[str, str] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_tab, gid): gid for gid, _ in TABS}
        for future, gid in futures.items():
            try:
                fetched[gid] = future.result()
            except Exception as exc:  # network failure keeps the old cache
                failures[gid] = str(exc)

    try:
        for position, (gid, name) in enumerate(TABS):
            result = {"gid": gid, "name": name, "changed": False, "error": None, "entries": 0}
            try:
                if gid in failures:
                    raise RuntimeError(failures[gid])
                text = fetched[gid]
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                previous = conn.execute(
                    "SELECT content_hash, entry_count FROM sheet_tabs WHERE gid = ?", (gid,)
                ).fetchone()

                if previous and previous["content_hash"] == digest:
                    result["entries"] = previous["entry_count"] or 0
                    conn.execute(
                        "UPDATE sheet_tabs SET checked_at = ?, error = NULL WHERE gid = ?",
                        (_now(), gid),
                    )
                    results.append(result)
                    continue

                entries = parse_csv(text)
                conn.execute("DELETE FROM sheet_entries WHERE gid = ?", (gid,))
                conn.executemany(
                    """INSERT INTO sheet_entries
                       (gid, row_no, col_no, section, panel, port, destination, note, fields_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            gid,
                            entry["row_no"],
                            entry["col_no"],
                            entry["section"],
                            entry["panel"],
                            entry["port"],
                            entry["destination"],
                            entry["note"],
                            json.dumps(entry["fields"], ensure_ascii=False),
                        )
                        for entry in entries
                    ],
                )
                conn.execute(
                    """INSERT INTO sheet_tabs
                       (gid, name, position, content_hash, fetched_at, checked_at, entry_count, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                       ON CONFLICT(gid) DO UPDATE SET
                         name = excluded.name,
                         position = excluded.position,
                         content_hash = excluded.content_hash,
                         fetched_at = excluded.fetched_at,
                         checked_at = excluded.checked_at,
                         entry_count = excluded.entry_count,
                         error = NULL""",
                    (gid, name, position, digest, _now(), _now(), len(entries)),
                )
                result.update(changed=True, entries=len(entries))
                changed += 1
            except Exception as exc:  # network or parse failure keeps the cache
                errors += 1
                result["error"] = str(exc)
                conn.execute(
                    """INSERT INTO sheet_tabs
                       (gid, name, position, entry_count, error, checked_at)
                       VALUES (?, ?, ?, 0, ?, ?)
                       ON CONFLICT(gid) DO UPDATE SET
                         name = excluded.name,
                         position = excluded.position,
                         error = excluded.error,
                         checked_at = excluded.checked_at""",
                    (gid, name, position, str(exc), _now()),
                )
            results.append(result)

        conn.commit()
        # Rebuild cable plants from updated sheet cache after every full sync.
        try:
            _touched = sync_plants(conn, _now())
            conn.commit()
            print(f"plant sync  →  {_touched} plants updated")
        except Exception as _exc:  # non-fatal; old plants stay visible
            print(f"plant sync: {_exc!r}")
        return {
            "ok": errors == 0,
            "busy": False,
            "changed": changed,
            "errors": errors,
            "tabs": results,
            "synced_at": _now(),
        }
    finally:
        _SYNCING = False
        _LOCK.release()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# -------------------------------------------------------------------- status
def status(conn) -> dict:
    rows = {
        row["gid"]: row
        for row in conn.execute("SELECT * FROM sheet_tabs").fetchall()
    }
    tabs = []
    total = 0
    last = None
    for gid, name in TABS:
        row = rows.get(gid)
        entries = row["entry_count"] if row and row["entry_count"] else 0
        total += entries
        fetched_at = row["fetched_at"] if row else None
        checked_at = row["checked_at"] if row else None
        if checked_at and (last is None or checked_at > last):
            last = checked_at
        tabs.append(
            {
                "gid": gid,
                "name": name,
                "entries": entries,
                "fetched_at": fetched_at,
                "checked_at": checked_at,
                "error": row["error"] if row else None,
            }
        )
    return {
        "spreadsheet_id": SPREADSHEET_ID,
        "spreadsheet_url": SHEET_URL.format(sid=SPREADSHEET_ID),
        "poll_seconds": POLL_SECONDS,
        "syncing": _SYNCING,
        "last_sync": last,
        "total_entries": total,
        "tabs": tabs,
    }


def entries(conn, gid: str, panel: str | None = None) -> list[dict]:
    if panel:
        rows = conn.execute(
            """SELECT row_no, col_no, section, panel, port, destination, note, fields_json
               FROM sheet_entries WHERE gid = ? AND panel = ?
               ORDER BY row_no, col_no""",
            (gid, panel),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT row_no, col_no, section, panel, port, destination, note, fields_json
               FROM sheet_entries WHERE gid = ?
               ORDER BY row_no, col_no""",
            (gid,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["fields"] = json.loads(item.pop("fields_json") or "{}")
        except json.JSONDecodeError:
            item["fields"] = {}
            item.pop("fields_json", None)
        result.append(item)
    return result


def search(conn, query: str, limit: int = 200) -> list[dict]:
    like = f"%{query.strip()}%"
    rows = conn.execute(
        """SELECT gid, row_no, col_no, section, panel, port, destination, note
           FROM sheet_entries
           WHERE port LIKE ? OR destination LIKE ? OR note LIKE ?
              OR panel LIKE ? OR section LIKE ?
           ORDER BY gid, row_no LIMIT ?""",
        (like, like, like, like, like, limit),
    ).fetchall()
    names = dict(TABS)
    return [{**dict(row), "tab_name": names.get(row["gid"], row["gid"])} for row in rows]


# ------------------------------------------------------------------ cable plants
# Regex that recognises OTB/ODC/MDF panel names carrying a core-count hint.
# Must match OTB144 (no space), 48LC / 96LC (LC suffix), OTB.48, 288.1 etc.
# Digit-boundaries only (not letter boundaries) so 48LC still counts.
_OTB_SIZE_RE = re.compile(r"(?<![0-9])(576|288|144|96|72|48|24)(?![0-9])", re.I)


def _extract_size(text: str) -> int | None:
    if not text:
        return None
    m = _OTB_SIZE_RE.search(str(text))
    return int(m.group(1)) if m else None


def _short(text: str, limit: int = 64) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _plant_code(conn, gid: str, panel: str) -> str:
    """Stable, human-readable cable code for a sheet panel."""
    base = _short(panel) or "OTB"
    existing = conn.execute("SELECT gid FROM cables WHERE code = ?", (base,)).fetchone()
    if not existing or existing["gid"] == gid:
        return base
    # Same panel name in another tab: suffix the station name.
    name = dict(TABS).get(gid, gid)
    candidate = f"{base} [{name}]"
    suffix = 2
    while conn.execute("SELECT gid FROM cables WHERE code = ?", (candidate,)).fetchone():
        candidate = f"{base} [{name} {suffix}]"
        suffix += 1
    return candidate


def sync_plants(conn, synced_at: str) -> int:
    """Rebuild ``cable_plants`` and the plant rows in ``cables`` from the
    sheet entries cache. The ``cables`` rows are the upload-ready inventory
    used by the cable list and the core map; existing allocations and manual
    cables are left untouched.

    Returns how many plant rows were touched (inserted or updated).
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT gid, section, panel, port, destination, note FROM sheet_entries"
    ).fetchall():
        if not row["panel"]:
            continue  # skip rows without a panel name
        key = (row["gid"], row["panel"])
        groups[key].append(dict(row))

    names = dict(TABS)
    touched = 0
    now = synced_at
    for (gid, panel), entries in groups.items():
        panel = panel.strip()
        size = _extract_size(panel) or _extract_size(" ".join(e["note"] or "" for e in entries))
        first = entries[0]
        # Prefer destination as route; fall back to note.
        route = _short(first["destination"] or first["note"] or "", 120)
        notes_parts = [e["note"] for e in entries if e["note"]]
        notes = " · ".join(notes_parts[:3]) if notes_parts else None
        # Total port = rows with a real port value (strip). Keep this in sync
        # with store._sheet_stats so Ringkasan 361 == sheet_total 361.
        total_ports = len([e for e in entries if (e["port"] or "").strip()])
        tab_name = names.get(gid, gid)
        conn.execute(
            """INSERT INTO cable_plants
               (gid, panel_code, core_count, route, notes, section, total_ports, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(gid, panel_code) DO UPDATE SET
                 core_count       = excluded.core_count,
                 route            = COALESCE(excluded.route, cable_plants.route),
                 notes            = COALESCE(excluded.notes, cable_plants.notes),
                 section          = excluded.section,
                 total_ports      = excluded.total_ports,
                 synced_at        = excluded.synced_at""",
            (gid, panel, size or 0, route, notes, first["section"], total_ports, now),
        )

        existing = conn.execute(
            "SELECT id, core_count FROM cables WHERE gid = ? AND panel_code = ?",
            (gid, panel),
        ).fetchone()
        if existing:
            core_count = existing["core_count"] or size or 0
            if size and not existing["core_count"]:
                core_count = size
            max_alloc = conn.execute(
                "SELECT MAX(core_no) AS m FROM allocations WHERE cable_id = ? AND released_at IS NULL",
                (existing["id"],),
            ).fetchone()["m"]
            if max_alloc and core_count < max_alloc:
                core_count = max_alloc  # never shrink below an active allocation
            conn.execute(
                """UPDATE cables SET
                     name = ?, route = ?, core_count = ?, notes = ?,
                     section = ?, total_ports = ?, tab_name = ?, synced_at = ?, status = 'active'
                   WHERE id = ?""",
                (panel, route, core_count, notes, first["section"], total_ports, tab_name, now, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO cables
                   (code, name, route, core_count, status, notes, gid, panel_code,
                    section, total_ports, tab_name, synced_at)
                   VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _plant_code(conn, gid, panel),
                    panel,
                    route,
                    size or 0,
                    notes,
                    gid,
                    panel,
                    first["section"],
                    total_ports,
                    tab_name,
                    now,
                ),
            )
        touched += 1
    conn.commit()
    return touched


def background_loop() -> None:
    """Keep the local cache fresh; failures are non-fatal."""
    while True:
        try:
            import store

            conn = store.get_conn()
            try:
                sync(conn)
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"sheet sync: {exc!r}")
        time.sleep(POLL_SECONDS)
