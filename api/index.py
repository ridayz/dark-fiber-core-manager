import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(ROOT, "src", "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import store  # noqa: E402
import sheets  # noqa: E402

_DB_INIT = False

# ---------------------------------------------------------------- auth
AUTH_ENABLED = bool(os.environ.get("APP_PASSWORD"))
_TOKEN_TTL = int(os.environ.get("APP_TOKEN_TTL", str(60 * 60 * 24 * 7)))  # 7 days


def _auth_secret() -> str:
    return os.environ.get("APP_SECRET") or os.environ.get("APP_PASSWORD") or ""


def _make_token() -> str:
    exp = int(time.time()) + _TOKEN_TTL
    payload = f"v1.{exp}"
    sig = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _valid_token(token: str) -> bool:
    try:
        version, exp_s, sig = token.split(".")
        if version != "v1":
            return False
        exp = int(exp_s)
        if exp < int(time.time()):
            return False
        payload = f"v1.{exp}"
        want = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, sig)
    except Exception:
        return False


def _cron_ok(header: str) -> bool:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return False
    return hmac.compare_digest(header or "", f"Bearer {secret}")


# Naive in-memory brute-force guard (per warm instance).
_LOGIN_FAILS: dict[str, list[float]] = {}


def _login_allowed(ip: str) -> bool:
    now = time.time()
    window = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < 60]
    _LOGIN_FAILS[ip] = window
    return len(window) < 6


def _login_failed(ip: str) -> None:
    _LOGIN_FAILS.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------- cache
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = {
    "/api/summary": 15,
    "/api/cables": 15,
    "/api/tenants": 15,
    "/api/leases": 15,
    "/api/plants": 15,
    "/api/sheet/status": 10,
    "/api/sheet/entries": 20,
    "/api/sheet/search": 10,
}


def _cache_get(key: str):
    item = _CACHE.get(key)
    if not item:
        return None
    expires, data = item
    if expires < time.time():
        _CACHE.pop(key, None)
        return None
    return data


def _cache_set(key: str, data, ttl: int) -> None:
    _CACHE[key] = (time.time() + ttl, data)


def _cache_clear() -> None:
    _CACHE.clear()


def ensure_db():
    global _DB_INIT
    if not _DB_INIT:
        store.init_db()
        _DB_INIT = True


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class handler(BaseHTTPRequestHandler):
    def _send(self, status, data, cache_ttl: int = 0):
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
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
        self.send_response(204)
        self.send_header("Allow", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

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
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            raw_path = qs.get("__path", [""])[0]
            path = "/" + raw_path.lstrip("/") if raw_path else parsed.path
            if not path.startswith("/api/"):
                path = "/api/" + path.lstrip("/")
            action = qs.get("action", [""])[0]

            # Public endpoints only. `/api/meta` exposes just the auth flag so the
            # frontend can decide whether to show the login gate.
            public = path in ("/api/health", "/api/meta", "/api/login")
            cron_path = path == "/api/cron/sheet-sync"
            cron = (cron_path or action == "sheet-sync") and _cron_ok(
                self.headers.get("Authorization", "")
            )

            if AUTH_ENABLED and not public and not cron:
                auth = self.headers.get("Authorization", "")
                token = auth[7:].strip() if auth.startswith("Bearer ") else ""
                if not token or not _valid_token(token):
                    return self._send(401, {"error": "Tidak terautentikasi"})

            if path == "/api/login":
                return self._login()

            if path == "/api/health":
                return self._send(200, {"ok": True, "service": "dark-fiber-core-manager", "auth": AUTH_ENABLED})

            if path == "/api/meta":
                return self._send(200, {"ok": True, "service": "dark-fiber-core-manager", "auth": AUTH_ENABLED})

            ensure_db()

            # Cron-triggered sync (GET) — handled before normal routing.
            if cron:
                conn = store.get_conn()
                try:
                    result = sheets.sync(conn, force=True)
                    try:
                        touched = sheets.sync_plants(conn, sheets._now())
                        conn.commit()
                        result["plants_touched"] = touched
                    except Exception:
                        pass
                    _cache_clear()
                    return self._send(200, result)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            self._route(method, path, qs)
        except store.StoreError as e:
            self._send(e.status, {"error": e.message})
        except Exception as exc:  # never leak internals / connection strings
            print(f"[api] error: {exc}", file=sys.stderr)
            self._send(500, {"error": "Terjadi kesalahan server"})

    def _login(self):
        ip = self.client_address[0] if self.client_address else "unknown"
        if not _login_allowed(ip):
            return self._send(429, {"error": "Terlalu banyak percobaan. Coba lagi sebentar."})
        body = self._read_json()
        password = str(body.get("password") or "")
        expected = os.environ.get("APP_PASSWORD") or ""
        if not AUTH_ENABLED:
            return self._send(200, {"token": "", "auth": False})
        if not hmac.compare_digest(password, expected):
            _login_failed(ip)
            return self._send(401, {"error": "Password salah"})
        return self._send(200, {"token": _make_token(), "auth": True})

    def _route(self, method, path, qs):
        # Cached read endpoints.
        cache_key = f"{path}?{qs.get('gid', [''])[0]}|{qs.get('q', [''])[0]}"
        ttl = _CACHE_TTL.get(path)
        if method == "GET" and ttl:
            hit = _cache_get(cache_key)
            if hit is not None:
                return self._send(200, hit)

        conn = store.get_conn()
        try:
            result = self._dispatch(method, path, qs, conn)
            if result is None:
                return
            status, data = result
            if method == "GET" and ttl and status == 200:
                _cache_set(cache_key, data, ttl)
            return self._send(status, data)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, method, path, qs, conn):
        # ---- read-only ----
        if method == "GET" and path == "/api/summary":
            return 200, store.summary(conn)
        if method == "GET" and path == "/api/cables":
            return 200, {"cables": store.list_cables(conn)}
        if method == "GET" and path.startswith("/api/cables/"):
            try:
                cid = int(path.rsplit("/", 1)[-1])
            except Exception:
                return 400, {"error": "id kabel tidak valid"}
            cable = store.get_cable(conn, cid)
            if not cable:
                return 404, {"error": "Kabel tidak ditemukan"}
            return 200, cable
        if method == "GET" and path == "/api/tenants":
            return 200, {"tenants": store.list_tenants(conn)}
        if method == "GET" and path.startswith("/api/tenants/"):
            try:
                tid = int(path.rsplit("/", 1)[-1])
            except Exception:
                return 400, {"error": "id tenant tidak valid"}
            tenant = store.get_tenant(conn, tid)
            if not tenant:
                return 404, {"error": "Tenant tidak ditemukan"}
            return 200, tenant
        if method == "GET" and path == "/api/leases":
            return 200, {"leases": store.list_leases(conn)}

        # ---- sheet read-only ----
        if method == "GET" and path == "/api/sheet/status":
            return 200, sheets.status(conn)
        if method == "GET" and path == "/api/sheet/entries":
            gid = qs.get("gid", [""])[0]
            names = dict(sheets.TABS)
            if gid not in names:
                return 404, {"error": "Tab sheet tidak dikenal"}
            return 200, {"gid": gid, "name": names[gid], "entries": sheets.entries(conn, gid)}
        if method == "GET" and path == "/api/sheet/search":
            term = qs.get("q", [""])[0].strip()
            if len(term) < 2:
                return 200, {"query": term, "results": []}
            return 200, {"query": term, "results": sheets.search(conn, term)}

        # ---- sync ----
        if method == "POST" and path == "/api/sheet/sync":
            result = sheets.sync(conn, force=True)
            try:
                touched = sheets.sync_plants(conn, sheets._now())
                conn.commit()
                result["plants_touched"] = touched
            except Exception:
                pass
            _cache_clear()
            return 200, result

        # ---- plants ----
        if method == "GET" and path == "/api/plants":
            return 200, {"plants": store.list_plants(conn)}
        if method == "GET" and path.startswith("/api/plants/"):
            try:
                pid = int(path.rsplit("/", 1)[-1])
            except Exception:
                return 400, {"error": "id plant tidak valid"}
            plant = store.get_plant(conn, pid)
            if not plant:
                return 404, {"error": "Plant tidak ditemukan"}
            return 200, plant

        # ---- writes ----
        body = self._read_json() if method in ("POST", "PATCH") else {}
        if method == "POST" and path == "/api/cables":
            data = store.create_cable(conn, body)
            _cache_clear()
            return 201, data
        if method == "PATCH" and path.startswith("/api/cables/"):
            cid = int(path.rsplit("/", 1)[-1])
            data = store.update_cable(conn, cid, body)
            _cache_clear()
            return 200, data
        if method == "POST" and path == "/api/tenants":
            data = store.create_tenant(conn, body)
            _cache_clear()
            return 201, data
        if method == "PATCH" and path.startswith("/api/tenants/"):
            tid = int(path.rsplit("/", 1)[-1])
            data = store.update_tenant(conn, tid, body)
            _cache_clear()
            return 200, data
        if method == "POST" and path == "/api/leases":
            data = store.create_lease(conn, body)
            _cache_clear()
            return 201, data
        if method == "PATCH" and path.startswith("/api/leases/"):
            batch = path.rsplit("/", 1)[-1]
            data = store.update_lease(conn, batch, body)
            _cache_clear()
            return 200, data
        if method == "DELETE" and path.startswith("/api/leases/"):
            batch = path.rsplit("/", 1)[-1]
            data = store.release_lease(conn, batch)
            _cache_clear()
            return 200, data

        return 404, {"error": "Endpoint tidak ditemukan"}
