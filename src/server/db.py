"""DB adapter: Turso/libSQL when env vars set, else local sqlite3."""
from __future__ import annotations

import os
import sqlite3
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


class TursoConn:
    def __init__(self, url: str, token: str):
        import libsql_client

        self._client = libsql_client.create_client_sync(url, auth_token=token)

    def execute(self, sql: str, params=()):
        rs = self._client.execute(sql, list(params))
        rows = []
        cols = list(rs.columns or [])
        for r in rs.rows:
            row = _Row()
            for i, col in enumerate(cols):
                row[col] = r[i]
            rows.append(row)
        return _CursorResult(rows=rows, lastrowid=getattr(rs, "last_insert_rowid", None))

    def executemany(self, sql: str, seq):
        last = None
        for params in seq:
            last = self.execute(sql, params)
        return last or _CursorResult()

    def executescript(self, sql: str):
        for stmt in sql.split(";"):
            s = stmt.strip()
            if s:
                self.execute(s)
        return _CursorResult()

    def commit(self):
        return None

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


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
