"""Shared stage machinery: the context a stage runs in, errors, and helpers to find inputs."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import layout, runner, store
from ..paths import Env, resolve_project_path


class StageError(RuntimeError):
    """A stage cannot run or failed; the message is shown to the user."""


class PauseRequest(Exception):
    """The stage needs user input before it can run (e.g. hit selection)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class StageContext:
    env: Env
    conn: sqlite3.Connection
    job: dict
    stage: str
    params: dict
    out_dir: Path
    settings: dict
    cancel_check: Callable[[], bool] = lambda: False
    on_tool: Callable[[int | None], None] | None = None
    _rows: dict = field(default_factory=dict)

    @property
    def job_id(self) -> int:
        return self.job["id"]

    @property
    def platform(self) -> str:
        return self.job["platform"]

    @property
    def threads(self) -> int:
        return int(self.settings["threads"])

    @property
    def log_path(self) -> Path:
        return layout.stage_log(self.env, self.job_id, self.stage)

    def tool(self, name: str) -> Path:
        return self.env.tool(name)

    def check(self) -> None:
        if self.cancel_check():
            raise runner.Cancelled()

    def log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

    def run_tool(self, cmd: list, stdout: Path | None = None, env: dict | None = None) -> None:
        runner.run(
            cmd, log_path=self.log_path, stdout_path=stdout, env=env,
            cancel_check=self.cancel_check, on_start=self.on_tool,
        )

    def stage_dir(self, name: str) -> Path:
        return layout.stage_dir(self.env, self.job_id, name)

    def stage_row(self, name: str) -> dict:
        row = self.conn.execute(
            "SELECT state, params, summary FROM stages WHERE job_id = ? AND name = ?", (self.job_id, name)
        ).fetchone()
        return {
            "state": row["state"],
            "params": store.loads(row["params"], {}),
            "summary": store.loads(row["summary"], {}) or {},
        }

    def inputs(self) -> dict[str, dict]:
        rows = self.conn.execute("SELECT * FROM inputs WHERE job_id = ?", (self.job_id,))
        return {r["role"]: store.row_dict(r) for r in rows}


@dataclass
class ReadSet:
    files: list[Path]
    paired: bool
    source: str  # "input" or "trimmed"


def checked_input(row: dict) -> Path:
    """An input file, verified to exist with its recorded size."""
    p = Path(row["path"])
    if not p.is_file():
        raise StageError(f"input file missing: {p}")
    if p.stat().st_size != row["size"]:
        raise StageError(f"input file changed (size differs from when the job was created): {p}")
    return p


def raw_reads(ctx: StageContext) -> ReadSet:
    ins = ctx.inputs()
    if "r1" not in ins:
        raise StageError("the job has no input reads")
    files = [checked_input(ins["r1"])]
    if "r2" in ins:
        files.append(checked_input(ins["r2"]))
    return ReadSet(files, len(files) == 2, "input")


def current_reads(ctx: StageContext) -> ReadSet:
    """Trimmed reads if trimming ran, else the input reads (never the assembler fragments)."""
    trim = ctx.stage_row("trim")
    if trim["state"] != "done":
        return raw_reads(ctx)
    outputs = trim["summary"].get("outputs", {})
    files = [ctx.stage_dir("trim") / outputs[r] for r in ("r1", "r2") if r in outputs]
    for f in files:
        if not f.is_file():
            raise StageError(f"trimmed reads missing: {f} (reset the job to 'trim')")
    return ReadSet(files, len(files) == 2, "trimmed")


def merge_flags(base: list[str], extra: list[str]) -> tuple[list[str], list[str]]:
    """Append option groups from `extra` whose option is not already in `base`.

    Returns (merged list, the extra tokens that were added).
    """
    present = {t for t in base if t.startswith("-")}
    out, added = list(base), []
    i = 0
    while i < len(extra):
        group = [extra[i]]
        j = i + 1
        while j < len(extra) and not extra[j].startswith("-"):
            group.append(extra[j])
            j += 1
        if group[0] not in present:
            out += group
            added += group
        i = j
    return out, added


def check_reserved(args: list[str], reserved: tuple[str, ...], stage: str) -> None:
    for a in args:
        for r in reserved:
            if a == r or a.startswith(r + "="):
                raise StageError(f"extra argument {a} is set by readscreen and not allowed ({stage}.extra_args)")


def blast_db_dir(settings: dict) -> Path:
    return resolve_project_path(settings["blast_db_dir"])


def resolve_db(settings: dict, value: str | None) -> Path:
    """BLAST database path (without extension) from a name relative to the DB folder."""
    if not value:
        raise StageError("no BLAST database chosen for this job")
    p = Path(value)
    base = p if p.is_absolute() else blast_db_dir(settings) / p
    if not any(base.parent.glob(base.name + ".*")):
        raise StageError(f"BLAST database not found: {base}")
    return base


def read_db_metadata(blastdbcmd: Path, db: Path) -> dict | None:
    """`blastdbcmd -metadata` of a database (None if it cannot be read)."""
    try:
        proc = subprocess.run(
            [str(blastdbcmd), "-db", str(db), "-metadata"], capture_output=True, text=True, timeout=120,
            env={**os.environ, "BLASTDB": str(db.parent)},
            creationflags=runner.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def describe_db(metadata: dict | None) -> dict:
    """The parts of `blastdbcmd -metadata` that readscreen shows and records with the results."""
    metadata = metadata or {}
    return {
        "title": metadata.get("description"),
        "updated": str(metadata.get("last-updated") or "")[:10] or None,
        "sequences": metadata.get("number-of-sequences"),
        "letters": metadata.get("number-of-letters"),
    }


def count_lines(path: Path) -> int:
    n = 0
    with open(path, "rb") as fh:
        for n, _ in enumerate(fh, 1):
            pass
    return n
