"""DB adapter for Vercel (Turso/libSQL) with local sqlite3 fallback.

Local dev keeps using src/server/darkfiber.db via sqlite3.
Vercel uses Turso when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    """Minimal sqlite3-like wrapper over libsql-client sync."""

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
        # libsql may not support executescript the same way; split naively on semicolons.
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


class SqliteConnWrapper:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        return cur

    def executemany(self, sql, seq):
        return self._conn.executemany(sql, seq)

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory


def get_conn():
    url = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN")
    if url and token:
        return TursoConn(url, token)
    conn = sqlite3.connect(str(LOCAL_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return SqliteConnWrapper(conn)


def using_turso() -> bool:
    url = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN")
    return bool(url and token)
