"""HTTP server for the dark fiber core manager.

Serves the JSON API and the static single-page frontend from one process so
there is no build step and no CORS setup.

    python3 src/server/app.py            # http://localhost:4173
    PORT=8080 python3 src/server/app.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import sheets
import store

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
WEB_DIR = os.path.normpath(WEB_DIR)
DEFAULT_PORT = 4173

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

CABLE_RE = re.compile(r"^/api/cables/(\d+)$")
TENANT_RE = re.compile(r"^/api/tenants/(\d+)$")
LEASE_RE = re.compile(r"^/api/leases/([A-Za-z0-9\-]+)$")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "DarkFiber/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ApiError("Payload terlalu besar", 413)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("Body harus JSON yang valid")
        if not isinstance(payload, dict):
            raise ApiError("Body harus berupa objek JSON")
        return payload

    # -- routing ---------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        try:
            conn = store.get_conn()
            try:
                self._route_api(method, path, conn, query)
            finally:
                conn.close()
        except store.StoreError as exc:
            self._error(exc.status, exc.message)
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"error: {exc!r}", file=sys.stderr)
            self._error(500, "Terjadi kesalahan di server")

    def _route_api(self, method: str, path: str, conn, query: dict | None = None) -> None:
        query = query or {}
        if method == "GET" and path == "/api/health":
            self._json(200, {"ok": True, "service": "dark-fiber-core-manager"})
            return

        if method == "GET" and path == "/api/meta":
            self._json(
                200,
                {
                    "purposes": store.PURPOSES,
                    "core_counts": list(store.VALID_CORE_COUNTS),
                    "fiber_colors": [
                        {"name": n, "hex": h} for n, h in store.FIBER_COLORS
                    ],
                    "tenant_colors": store.TENANT_COLORS,
                    "expiring_days": store.EXPIRING_DAYS,
                },
            )
            return

        if path == "/api/summary" and method == "GET":
            self._json(200, store.summary(conn))
            return

        if path == "/api/cables":
            if method == "GET":
                self._json(200, {"cables": store.list_cables(conn)})
            elif method == "POST":
                self._json(201, store.create_cable(conn, self._read_json()))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        m = CABLE_RE.match(path)
        if m:
            cable_id = int(m.group(1))
            if method == "GET":
                cable = store.get_cable(conn, cable_id)
                if not cable:
                    raise ApiError("Kabel tidak ditemukan", 404)
                self._json(200, cable)
            elif method == "PATCH":
                self._json(200, store.update_cable(conn, cable_id, self._read_json()))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        if path == "/api/tenants":
            if method == "GET":
                self._json(200, {"tenants": store.list_tenants(conn)})
            elif method == "POST":
                self._json(201, store.create_tenant(conn, self._read_json()))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        m = TENANT_RE.match(path)
        if m:
            tenant_id = int(m.group(1))
            if method == "GET":
                tenant = store.get_tenant(conn, tenant_id)
                if not tenant:
                    raise ApiError("Tenant tidak ditemukan", 404)
                self._json(200, tenant)
            elif method == "PATCH":
                self._json(200, store.update_tenant(conn, tenant_id, self._read_json()))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        if path == "/api/leases":
            if method == "GET":
                self._json(200, {"leases": store.list_leases(conn)})
            elif method == "POST":
                self._json(201, store.create_lease(conn, self._read_json()))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        m = LEASE_RE.match(path)
        if m:
            batch_id = m.group(1)
            if method == "PATCH":
                self._json(200, store.update_lease(conn, batch_id, self._read_json()))
            elif method == "DELETE":
                self._json(200, store.release_lease(conn, batch_id))
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        # -- OTB / Google Sheets map ------------------------------------
        if method == "GET" and path == "/api/sheet/status":
            self._json(200, sheets.status(conn))
            return

        if method == "POST" and path == "/api/sheet/sync":
            result = sheets.sync(conn, force=True)
            # Rebuild cable plants from updated sheet cache after each sync.
            now = sheets._now()
            try:
                touched = sheets.sync_plants(conn, now)
                conn.commit()
                result["plants_touched"] = touched
            except Exception as exc:  # non-fatal; keep old plants visible
                print(f"plant sync failed: {exc!r}", file=sys.stderr)
            self._json(200, result)
            return

        if method == "GET" and path == "/api/sheet/entries":
            gid = query.get("gid", [""])[0]
            names = dict(sheets.TABS)
            if gid not in names:
                raise ApiError("Tab sheet tidak dikenal", 404)
            self._json(
                200,
                {"gid": gid, "name": names[gid], "entries": sheets.entries(conn, gid)},
            )
            return

        if method == "GET" and path == "/api/sheet/search":
            term = query.get("q", [""])[0].strip()
            if len(term) < 2:
                self._json(200, {"query": term, "results": []})
                return
            self._json(200, {"query": term, "results": sheets.search(conn, term)})
            return

        # -- Cable plants (real plant inventory from sheet) ------------
        PLANT_RE = re.compile(r"^/api/plants/(\d+)$")
        if path == "/api/plants" and method == "GET":
            self._json(200, {"plants": store.list_plants(conn)})
            return
        m = PLANT_RE.match(path)
        if m:
            plant_id = int(m.group(1))
            if method == "GET":
                plant = store.get_plant(conn, plant_id)
                if not plant:
                    raise ApiError("设备台账不存在", 404)
                self._json(200, plant)
            else:
                raise ApiError("Method tidak didukung", 405)
            return

        raise ApiError("Endpoint tidak ditemukan", 404)

    # -- static files ----------------------------------------------------
    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not target.startswith(WEB_DIR + os.sep) or not os.path.isfile(target):
            # SPA fallback for unknown paths
            target = os.path.join(WEB_DIR, "index.html")
            if not os.path.isfile(target):
                self._error(404, "File tidak ditemukan")
                return
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            body = fh.read()
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"))

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("DARKFIBER_LOG") == "1":
            super().log_message(fmt, *args)


def main() -> None:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    store.init_db()
    threading.Thread(target=sheets.background_loop, name="sheet-sync", daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Dark Fiber Core Manager  →  http://localhost:{port}")
    print(f"Database                 →  {store.DB_PATH}")
    print(f"Sheet sync               →  tiap {sheets.POLL_SECONDS}s (read-only)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
