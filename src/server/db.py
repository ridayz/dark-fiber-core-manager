"""DB adapter: Turso/libSQL over HTTP when env vars set, else local sqlite3."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DB = Path(os.environ.get("DARKFIBER_DB", ROOT / "src" / "server" / "darkfiber.db"))


class _Row(dict):
    __getattr__ = dict.get

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _CursorResult:
    def __init__(self, rows=None, lastrowid=None):
        self._rows = rows or []
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _turso_arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _turso_val(v):
    if v is None:
        return None
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        try:
            return int(v.get("value"))
        except Exception:
            return v.get("value")
    if t == "float":
        try:
            return float(v.get("value"))
        except Exception:
            return v.get("value")
    if t == "text":
        return v.get("value")
    if t == "blob":
        return base64.b64decode(v.get("base64", ""))
    return v.get("value")


class TursoConn:
    def __init__(self, url: str, token: str):
        base = url.strip()
        if base.startswith("libsql://"):
            base = "https://" + base[len("libsql://"):]
        if base.startswith("ws://"):
            base = "http://" + base[len("ws://"):]
        if base.startswith("wss://"):
            base = "https://" + base[len("wss://"):]
        self._endpoint = base.rstrip("/") + "/v2/pipeline"
        self._token = token

    def _post(self, requests):
        body = json.dumps({"requests": list(requests) + [{"type": "close"}]}).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def execute(self, sql: str, params=()):
        stmt = {"sql": sql}
        p = list(params)
        if p:
            stmt["args"] = [_turso_arg(v) for v in p]
        data = self._post([{"type": "execute", "stmt": stmt}])
        results = data.get("results", [])
        if not results:
            return _CursorResult()
        res = results[0]
        if res.get("type") == "error":
            err = res.get("error", {})
            raise RuntimeError(err.get("message") or str(err))
        r = res.get("response", {}).get("result", {})
        cols = [c.get("name") for c in r.get("cols", [])]
        rows = []
        for row in r.get("rows", []):
            obj = _Row()
            for i, col in enumerate(cols):
                obj[col] = _turso_val(row[i])
            rows.append(obj)
        return _CursorResult(rows=rows, lastrowid=r.get("last_insert_rowid"))

    def executemany(self, sql: str, seq):
        last = None
        for params in seq:
            last = self.execute(sql, params)
        return last or _CursorResult()

    def executescript(self, sql: str):
        stmts = [s.strip() for s in sql.split(";") if s.strip()]
        if not stmts:
            return _CursorResult()
        reqs = [{"type": "execute", "stmt": {"sql": s}} for s in stmts]
        data = self._post(reqs)
        for res in data.get("results", []):
            if res.get("type") == "error":
                err = res.get("error", {})
                raise RuntimeError(err.get("message") or str(err))
        return _CursorResult()

    def commit(self):
        return None

    def close(self):
        return None


def using_turso() -> bool:
    url = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN")
    return bool(url and token)


def get_conn(db_path: str | None = None):
    url = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN")
    if url and token:
        return TursoConn(url, token)
    conn = sqlite3.connect(db_path or str(LOCAL_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
