"""SQLite access: connection setup (WAL), schema, transactions and JSON helpers.

The web server, the CLI and the worker all open their own connections. Each API call uses a
short-lived connection; writes that must be atomic use `transaction()` (BEGIN IMMEDIATE).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# Version 2 added the `download` table. Schema changes so far only add tables, so the
# CREATE TABLE IF NOT EXISTS statements below also upgrade an older database.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    paired INTEGER NOT NULL,
    status TEXT NOT NULL,
    pause_reason TEXT,
    error TEXT,
    queue_time REAL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    cancel_request INTEGER NOT NULL DEFAULT 0,
    delete_request INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, queue_time);
CREATE TABLE IF NOT EXISTS inputs (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (job_id, role)
);
CREATE TABLE IF NOT EXISTS stages (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    params TEXT NOT NULL,
    pause_after INTEGER NOT NULL DEFAULT 0,
    started REAL,
    finished REAL,
    summary TEXT,
    error TEXT,
    PRIMARY KEY (job_id, idx)
);
CREATE TABLE IF NOT EXISTS ranking (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    grp INTEGER NOT NULL,
    leader INTEGER NOT NULL,
    score REAL NOT NULL,
    queries INTEGER NOT NULL,
    ref_cov REAL NOT NULL,
    identity REAL NOT NULL,
    depth REAL,
    length INTEGER NOT NULL,
    taxid TEXT,
    species TEXT,
    title TEXT,
    weak INTEGER NOT NULL,
    auto_selected INTEGER NOT NULL,
    selected INTEGER NOT NULL,
    PRIMARY KEY (job_id, ref)
);
CREATE TABLE IF NOT EXISTS results (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (job_id, ref)
);
CREATE TABLE IF NOT EXISTS worker (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    tool_pid INTEGER,
    started REAL,
    heartbeat REAL,
    job_id INTEGER,
    stage TEXT,
    stop_request TEXT
);
INSERT OR IGNORE INTO worker (id) VALUES (1);
CREATE TABLE IF NOT EXISTS download (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    db TEXT,
    state TEXT,
    phase TEXT,
    message TEXT,
    bytes_done INTEGER,
    bytes_total INTEGER,
    version TEXT,
    error TEXT,
    started REAL,
    finished REAL,
    cancel_request INTEGER NOT NULL DEFAULT 0,
    installing INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO download (id) VALUES (1);
"""


class SchemaError(RuntimeError):
    """The database was created by a newer readscreen version."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode with WAL, foreign keys and a busy timeout."""
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(path: str | Path) -> None:
    """Create the schema if needed and check the schema version."""
    conn = connect(path)
    try:
        with transaction(conn):
            for statement in _statements(SCHEMA):
                conn.execute(statement)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise SchemaError(
                    f"database schema version {row['value']} is newer than this program ({SCHEMA_VERSION})"
                )
            elif int(row["value"]) < SCHEMA_VERSION:
                conn.execute(
                    "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
                )
    finally:
        conn.close()


def _statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    return json.loads(text)


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}
