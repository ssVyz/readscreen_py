"""Global settings, stored in the DB `settings` table.

Two kinds of keys:
- the global settings in SETTINGS (threads, memory, folders, intervals);
- `default.<stage>.<param>`: overrides the code default of a stage parameter for new jobs.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from . import store
from .params import Param, ParamError, Schema, coerce

DEFAULT_PREFIX = "default."


def _default_threads(_platform: str | None) -> int:
    return max(1, min(8, os.cpu_count() or 4))


SETTINGS: Schema = {
    "threads": Param(int, _default_threads, min=1, help="threads used by the tools"),
    "assembler_memory": Param(
        str, "0.5", help="shortread_assembler memory limit: fraction of RAM (0-1) or a size like 16G"
    ),
    "blast_db_dir": Param(str, "blast_db", help="folder with the BLAST databases (relative to the project root)"),
    "download_url": Param(
        str, "https://ftp.ncbi.nlm.nih.gov/blast/db/",
        help="where database downloads come from: the NCBI BLAST database folder or a mirror of it",
    ),
    "poll_interval": Param(float, 3.0, min=0.2, help="seconds between queue checks of an idle worker"),
    "heartbeat_interval": Param(float, 5.0, min=0.5, help="seconds between worker heartbeats"),
}


def _stage_param(key: str) -> tuple[str, str, Param]:
    from .stages import registry  # late import: stages import settings indirectly

    parts = key[len(DEFAULT_PREFIX):].split(".")
    if len(parts) != 2:
        raise ParamError(f"expected default.<stage>.<param>, got {key}")
    stage, name = parts
    mods = registry()
    if stage not in mods:
        raise ParamError(f"unknown stage in {key}")
    schema = mods[stage].PARAMS
    if name not in schema or schema[name].internal:
        raise ParamError(f"unknown parameter in {key}")
    return stage, name, schema[name]


def get_all(conn: sqlite3.Connection) -> dict[str, Any]:
    values = {k: p.default_for(None) for k, p in SETTINGS.items()}
    for row in conn.execute("SELECT key, value FROM settings"):
        values[row["key"]] = store.loads(row["value"])
    return values


def set_value(conn: sqlite3.Connection, key: str, value: Any) -> Any:
    """Validate and store a setting. None removes it (back to the default)."""
    if key.startswith(DEFAULT_PREFIX):
        _, _, p = _stage_param(key)
    elif key in SETTINGS:
        p = SETTINGS[key]
    else:
        raise ParamError(f"unknown setting: {key} (known: {', '.join(SETTINGS)}, default.<stage>.<param>)")
    v = coerce(key, p, value)
    if v is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        return p.default_for(None)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, store.dumps(v)),
    )
    return v


def stage_overrides(values: dict[str, Any], stage: str) -> dict[str, Any]:
    prefix = f"{DEFAULT_PREFIX}{stage}."
    return {k[len(prefix):]: v for k, v in values.items() if k.startswith(prefix)}
