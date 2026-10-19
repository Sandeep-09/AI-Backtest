"""SQLite persistence. JSON columns hold structured payloads."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    fingerprint TEXT PRIMARY KEY, path TEXT, size INTEGER, mtime REAL, format TEXT, schema TEXT,
    dataset TEXT, meta_json TEXT, status TEXT, ingest_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, ref TEXT, severity TEXT, code TEXT,
    message TEXT, details_json TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS issues_scope ON issues(scope, ref);
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id INTEGER, version INTEGER, spec_json TEXT,
    spec_hash TEXT, parent_version INTEGER, source TEXT, change_json TEXT, notes_json TEXT, created_at TEXT,
    UNIQUE(strategy_id, version)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id INTEGER, version INTEGER, role TEXT,
    content TEXT, meta_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS previews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id INTEGER, version INTEGER, spec_hash TEXT,
    result_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id INTEGER, version INTEGER, spec_hash TEXT,
    assumptions_json TEXT, approved_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id INTEGER, version INTEGER, spec_hash TEXT,
    approval_id INTEGER, job_id TEXT, status TEXT, result_json TEXT, created_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, n INTEGER, direction TEXT, symbol TEXT,
    entry_ts INTEGER, exit_ts INTEGER, net_pnl REAL, exit_reason TEXT, search_text TEXT, detail_json TEXT
);
CREATE INDEX IF NOT EXISTS trades_run ON trades(run_id, n);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT, status TEXT, progress REAL, message TEXT, log_json TEXT,
    result_json TEXT, created_at TEXT, updated_at TEXT
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(get_settings().db_path)
    if _conn is None or _conn_path != path:
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _conn_path = path
    return _conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, args).fetchall()


def q1(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


def j(v: Any) -> str:
    return json.dumps(v, default=str)


def lj(s: str | None, default: Any = None) -> Any:
    if s is None:
        return default
    return json.loads(s)


def add_issue(scope: str, ref: str, severity: str, code: str, message: str, details: Any = None) -> None:
    ex("INSERT INTO issues(scope, ref, severity, code, message, details_json, created_at) VALUES (?,?,?,?,?,?,?)",
       (scope, ref, severity, code, message, j(details), now()))


def clear_issues(scope: str, ref: str | None = None) -> None:
    if ref is None:
        ex("DELETE FROM issues WHERE scope=?", (scope,))
    else:
        ex("DELETE FROM issues WHERE scope=? AND ref=?", (scope, ref))


def issues(scope: str | None = None) -> list[dict]:
    rows = q("SELECT * FROM issues" + (" WHERE scope=?" if scope else "") + " ORDER BY id", (scope,) if scope else ())
    return [{"id": r["id"], "scope": r["scope"], "ref": r["ref"], "severity": r["severity"], "code": r["code"],
             "message": r["message"], "details": lj(r["details_json"]), "created_at": r["created_at"]} for r in rows]
