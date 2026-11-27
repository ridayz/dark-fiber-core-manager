import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(ROOT, "src", "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import store  # noqa: E402
import sheets  # noqa: E402

_DB_INIT = False


def ensure_db():
    global _DB_INIT
    if not _DB_INIT:
        store.init_db()
        _DB_INIT = True


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class handler(BaseHTTPRequestHandler):
    def _send(self, status, data):
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        try:
            ensure_db()
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            path = qs.get("__path", [parsed.path])[0] or "/"
            path = "/" + path.lstrip("/")
            if not path.startswith("/api/"):
                # allow /api/index?action=sheet-sync
                path = "/api/" + path.lstrip("/")
            if path == "/api/index":
                path = "/api/health"
            self._route(method, path, qs)
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _route(self, method, path, qs):
        conn = store.get_conn()
        try:
            # read-only endpoints
            if method == "GET" and path == "/api/health":
                return self._send(200, {"ok": True, "service": "dark-fiber-core-manager", "vercel": True})
            if method == "GET" and path == "/api/meta":
                return self._send(200, {"ok": True, "vercel": True, "db": "turso" if os.environ.get("TURSO_DATABASE_URL") else "sqlite"})
            if method == "GET" and path == "/api/summary":
                return self._send(200, store.summary(conn))
            if method == "GET" and path == "/api/cables":
                return self._send(200, {"cables": store.list_cables(conn)})
            if method == "GET" and path.startswith("/api/cables/"):
                try:
                    cid = int(path.rsplit("/", 1)[-1])
                except Exception:
                    return self._send(400, {"error": "id kabel tidak valid"})
                cable = store.get_cable(conn, cid)
                if not cable:
                    return self._send(404, {"error": "Kabel tidak ditemukan"})
                return self._send(200, cable)
            if method == "GET" and path == "/api/tenants":
                return self._send(200, {"tenants": store.list_tenants(conn)})
            if method == "GET" and path.startswith("/api/tenants/"):
                try:
                    tid = int(path.rsplit("/", 1)[-1])
                except Exception:
                    return self._send(400, {"error": "id tenant tidak valid"})
                tenant = store.get_tenant(conn, tid)
                if not tenant:
                    return self._send(404, {"error": "Tenant tidak ditemukan"})
                return self._send(200, tenant)
            if method == "GET" and path == "/api/leases":
                return self._send(200, {"leases": store.list_leases(conn)})

            # sheet read-only
            if method == "GET" and path == "/api/sheet/status":
                return self._send(200, sheets.status(conn))
            if method == "GET" and path == "/api/sheet/entries":
                gid = qs.get("gid", [""])[0]
                names = dict(sheets.TABS)
                if gid not in names:
                    return self._send(404, {"error": "Tab sheet tidak dikenal"})
                return self._send(200, {"gid": gid, "name": names[gid], "entries": sheets.entries(conn, gid)})
            if method == "GET" and path == "/api/sheet/search":
                term = qs.get("q", [""])[0].strip()
                if len(term) < 2:
                    return self._send(200, {"query": term, "results": []})
                return self._send(200, {"query": term, "results": sheets.search(conn, term)})

            # sync (can be triggered by cron)
            if method == "POST" and path == "/api/sheet/sync":
                result = sheets.sync(conn, force=True)
                try:
                    now = sheets._now()
                    touched = sheets.sync_plants(conn, now)
                    conn.commit()
                    result["plants_touched"] = touched
                except Exception as exc:
                    result["plant_error"] = str(exc)
                return self._send(200, result)
            if method == "GET" and qs.get("action", [""])[0] == "sheet-sync":
                result = sheets.sync(conn, force=True)
                return self._send(200, result)

            # plants
            if method == "GET" and path == "/api/plants":
                return self._send(200, {"plants": store.list_plants(conn)})
            if method == "GET" and path.startswith("/api/plants/"):
                try:
                    pid = int(path.rsplit("/", 1)[-1])
                except Exception:
                    return self._send(400, {"error": "id plant tidak valid"})
                plant = store.get_plant(conn, pid)
                if not plant:
                    return self._send(404, {"error": "Plant tidak ditemukan"})
                return self._send(200, plant)

            # writes - basic pass-through (best effort)
            body = self._read_json() if method in ("POST", "PATCH") else {}
            if method == "POST" and path == "/api/cables":
                return self._send(201, store.create_cable(conn, body))
            if method == "PATCH" and path.startswith("/api/cables/"):
                cid = int(path.rsplit("/", 1)[-1])
                return self._send(200, store.update_cable(conn, cid, body))
            if method == "POST" and path == "/api/tenants":
                return self._send(201, store.create_tenant(conn, body))
            if method == "PATCH" and path.startswith("/api/tenants/"):
                tid = int(path.rsplit("/", 1)[-1])
                return self._send(200, store.update_tenant(conn, tid, body))
            if method == "POST" and path == "/api/leases":
                return self._send(201, store.create_lease(conn, body))
            if method == "PATCH" and path.startswith("/api/leases/"):
                batch = path.rsplit("/", 1)[-1]
                return self._send(200, store.update_lease(conn, batch, body))
            if method == "DELETE" and path.startswith("/api/leases/"):
                batch = path.rsplit("/", 1)[-1]
                return self._send(200, store.release_lease(conn, batch))

            return self._send(404, {"error": "Endpoint tidak ditemukan", "path": path})
        finally:
            try:
                conn.close()
            except Exception:
                pass
