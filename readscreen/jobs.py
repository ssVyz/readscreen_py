"""Job and stage model: creation, parameters, state transitions, reset, cancel, delete, reassign.

Job status: queued, running, paused, stopped, done, error.
Stage state: pending, done, skipped. The current stage is the first pending one.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from . import layout, settings as settings_mod, store
from .layout import PLATFORMS, STAGE_NAMES, is_long, stage_index
from .logic.fastx import looks_like_fasta
from .logic.fingerprint import file_record
from .params import ParamError, validate
from .paths import Env
from .stages import registry

STATUSES = ("queued", "running", "paused", "stopped", "done", "error")


class JobError(RuntimeError):
    """A job operation is not possible. code: not_found, invalid or conflict."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- reading


def job_row(conn: sqlite3.Connection, job_id: int) -> dict:
    row = store.row_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    if row is None:
        raise JobError(f"job {job_id} not found", "not_found")
    return row


def stage_rows(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM stages WHERE job_id = ? ORDER BY idx", (job_id,)):
        d = store.row_dict(r)
        d["params"] = store.loads(d["params"], {})
        d["summary"] = store.loads(d["summary"])
        d["pause_after"] = bool(d["pause_after"])
        out.append(d)
    return out


def current_stage(conn: sqlite3.Connection, job_id: int) -> str | None:
    row = conn.execute(
        "SELECT name FROM stages WHERE job_id = ? AND state = 'pending' ORDER BY idx LIMIT 1", (job_id,)
    ).fetchone()
    return row["name"] if row else None


def get_job(conn: sqlite3.Connection, job_id: int) -> dict:
    job = job_row(conn, job_id)
    job["paired"] = bool(job["paired"])
    job["inputs"] = {
        r["role"]: store.row_dict(r) for r in conn.execute("SELECT * FROM inputs WHERE job_id = ?", (job_id,))
    }
    job["stages"] = stage_rows(conn, job_id)
    job["current_stage"] = current_stage(conn, job_id)
    return job


def list_jobs(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM jobs ORDER BY id"):
        job = store.row_dict(r)
        job["paired"] = bool(job["paired"])
        job["current_stage"] = current_stage(conn, job["id"])
        out.append(job)
    return out


# --------------------------------------------------------------------------- creation and parameters


def build_params(platform: str, settings: dict, given: dict | None) -> dict[str, dict]:
    """Complete, validated parameters for every stage: code defaults < settings < given."""
    given = given or {}
    unknown = set(given) - set(STAGE_NAMES)
    if unknown:
        raise ParamError(f"unknown stage(s): {', '.join(sorted(unknown))}")
    out = {}
    for name, mod in registry().items():
        try:
            p = validate(mod.PARAMS, settings_mod.stage_overrides(settings, name), platform)
            p = validate(mod.PARAMS, given.get(name, {}), platform, base=p)
            if hasattr(mod, "check"):
                mod.check(p, platform)
        except ParamError as exc:
            raise ParamError(f"{name}.{exc}") from None
        out[name] = p
    return out


def create_job(
    conn: sqlite3.Connection,
    env: Env,
    settings: dict,
    *,
    name: str,
    platform: str,
    r1: str,
    r2: str | None = None,
    assembly: str | None = None,
    notes: str = "",
    params: dict | None = None,
    pauses: tuple[str, ...] | list[str] = (),
    queue: bool = True,
) -> int:
    if platform not in PLATFORMS:
        raise JobError(f"unknown platform {platform!r} (choose from {', '.join(PLATFORMS)})")
    if r2 and is_long(platform):
        raise JobError("long reads are single files; --r2 is only for paired short reads")
    for p in pauses:
        stage_index(p)
    params = {k: dict(v) for k, v in (params or {}).items()}
    if assembly:
        params.setdefault("assemble", {})["mode"] = "external"
    stage_params = build_params(platform, settings, params)
    _check_trim_mode(stage_params["trim"], platform, bool(r2))
    if stage_params["assemble"]["mode"] == "external" and not assembly:
        raise JobError("assemble.mode is 'external' but no assembly file was given")
    if not stage_params["blast"]["db"]:
        raise JobError("no BLAST database chosen (blast.db)")

    try:
        records = {"r1": file_record(r1)}
        if r2:
            records["r2"] = file_record(r2)
        if assembly:
            records["assembly"] = file_record(assembly)
    except FileNotFoundError as exc:
        raise JobError(str(exc)) from None
    if r2 and records["r1"]["path"] == records["r2"]["path"]:
        raise JobError("R1 and R2 are the same file")
    if assembly and not looks_like_fasta(records["assembly"]["path"]):
        raise JobError("the assembly is not a FASTA file")

    t = now()
    with store.transaction(conn):
        cur = conn.execute(
            "INSERT INTO jobs (name, notes, platform, paired, status, queue_time, created, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, notes, platform, int(bool(r2)), "queued" if queue else "stopped", t if queue else None, t, t),
        )
        job_id = cur.lastrowid
        for role, rec in records.items():
            conn.execute(
                "INSERT INTO inputs (job_id, role, path, size, fingerprint) VALUES (?, ?, ?, ?, ?)",
                (job_id, role, rec["path"], rec["size"], rec["fingerprint"]),
            )
        for i, stage in enumerate(STAGE_NAMES, 1):
            conn.execute(
                "INSERT INTO stages (job_id, idx, name, state, params, pause_after) VALUES (?, ?, ?, 'pending', ?, ?)",
                (job_id, i, stage, store.dumps(stage_params[stage]), int(stage in pauses)),
            )
    try:
        layout.job_dir(env, job_id).mkdir(parents=True, exist_ok=True)
        if assembly:
            _import_assembly(env, job_id, records["assembly"]["path"])
    except OSError as exc:
        delete_now(conn, env, job_id)
        raise JobError(f"cannot set up the job folder: {exc}") from None
    return job_id


def _check_trim_mode(trim_params: dict, platform: str, paired: bool) -> None:
    from .stages.trim import resolve_mode

    if paired and resolve_mode(trim_params, platform) == "long":
        raise JobError("trim.mode 'long' takes one read file; use 'short' or 'auto' for paired reads")


def _import_assembly(env: Env, job_id: int, path: str) -> None:
    dest = layout.imports_dir(env, job_id)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest / "assembly.fa")


def _require_not_running(job: dict, action: str) -> None:
    if job["status"] == "running":
        raise JobError(f"job {job['id']} is running; cancel it before you {action}", "conflict")


def set_params(
    conn: sqlite3.Connection, env: Env, job_id: int, stage: str, updates: dict, reset: bool = False
) -> dict:
    """Change parameters of a stage. A done stage must be reset first (reset=True does that)."""
    job = job_row(conn, job_id)
    _require_not_running(job, "change parameters")
    mod = registry()[stage] if stage in registry() else None
    if mod is None:
        raise JobError(f"unknown stage: {stage}")
    row = conn.execute("SELECT state, params FROM stages WHERE job_id = ? AND name = ?", (job_id, stage)).fetchone()
    # parameters added in later versions get their defaults, so they do not count as changes
    current = validate(mod.PARAMS, {}, job["platform"], base=store.loads(row["params"], {}))
    try:
        merged = validate(mod.PARAMS, updates, job["platform"], base=current)
        if hasattr(mod, "check"):
            mod.check(merged, job["platform"])
    except ParamError as exc:
        raise JobError(f"{stage}.{exc}") from None
    if stage == "trim":
        _check_trim_mode(merged, job["platform"], bool(job["paired"]))
    if stage == "assemble" and merged["mode"] == "external" and not _has_assembly(conn, job_id):
        raise JobError("assemble.mode 'external' needs an assembly file (reassign one first)")
    if stage == "select" and merged["mode"] != current.get("mode"):
        merged["confirmed"] = False
    if merged == current:
        return merged
    if row["state"] != "pending":
        if not reset:
            later = ", ".join(STAGE_NAMES[stage_index(stage) - 1:])
            raise JobError(
                f"stage '{stage}' is {row['state']}; changing it discards these stages: {later}. "
                "Confirm with reset.",
                "conflict",
            )
        reset_job(conn, env, job_id, stage)
    conn.execute(
        "UPDATE stages SET params = ? WHERE job_id = ? AND name = ?", (store.dumps(merged), job_id, stage)
    )
    _touch(conn, job_id)
    return merged


def _has_assembly(conn: sqlite3.Connection, job_id: int) -> bool:
    return conn.execute("SELECT 1 FROM inputs WHERE job_id = ? AND role = 'assembly'", (job_id,)).fetchone() is not None


def set_pause(conn: sqlite3.Connection, job_id: int, stage: str, on: bool) -> None:
    job_row(conn, job_id)
    stage_index(stage)
    conn.execute("UPDATE stages SET pause_after = ? WHERE job_id = ? AND name = ?", (int(on), job_id, stage))
    _touch(conn, job_id)


def update_meta(conn: sqlite3.Connection, job_id: int, name: str | None = None, notes: str | None = None) -> None:
    job_row(conn, job_id)
    if name is not None:
        if not name.strip():
            raise JobError("the job name cannot be empty")
        conn.execute("UPDATE jobs SET name = ? WHERE id = ?", (name.strip(), job_id))
    if notes is not None:
        conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))
    _touch(conn, job_id)


def _touch(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET updated = ? WHERE id = ?", (now(), job_id))


# --------------------------------------------------------------------------- user actions


def queue_job(conn: sqlite3.Connection, job_id: int) -> None:
    job = job_row(conn, job_id)
    if job["status"] == "paused":
        continue_job(conn, job_id)
        return
    if job["status"] in ("queued", "running"):
        return
    if current_stage(conn, job_id) is None:
        raise JobError("nothing left to run; reset the job to an earlier stage first", "conflict")
    conn.execute(
        "UPDATE jobs SET status = 'queued', queue_time = ?, error = NULL, pause_reason = NULL, updated = ? WHERE id = ?",
        (now(), now(), job_id),
    )


def continue_job(conn: sqlite3.Connection, job_id: int) -> None:
    """Continue a paused job; for the hit-selection pause this confirms the current selection."""
    job = job_row(conn, job_id)
    if job["status"] != "paused":
        raise JobError(f"job {job_id} is not paused (status: {job['status']})", "conflict")
    with store.transaction(conn):
        if job["pause_reason"] == "select":
            row = conn.execute("SELECT params FROM stages WHERE job_id = ? AND name = 'select'", (job_id,)).fetchone()
            p = store.loads(row["params"], {})
            p["confirmed"] = True
            conn.execute(
                "UPDATE stages SET params = ? WHERE job_id = ? AND name = 'select'", (store.dumps(p), job_id)
            )
        conn.execute(
            "UPDATE jobs SET status = 'queued', pause_reason = NULL, queue_time = COALESCE(queue_time, ?),"
            " updated = ? WHERE id = ?",
            (now(), now(), job_id),
        )


def cancel_job(conn: sqlite3.Connection, env: Env, job_id: int, worker_alive: bool) -> str:
    """Returns 'stopped' or 'requested' (the worker stops a running job)."""
    job = job_row(conn, job_id)
    if job["status"] in ("queued", "paused"):
        conn.execute(
            "UPDATE jobs SET status = 'stopped', pause_reason = NULL, updated = ? WHERE id = ?", (now(), job_id)
        )
        return "stopped"
    if job["status"] == "running":
        if worker_alive:
            conn.execute("UPDATE jobs SET cancel_request = 1, updated = ? WHERE id = ?", (now(), job_id))
            return "requested"
        _cleanup_tmp(env, job_id)
        conn.execute(
            "UPDATE jobs SET status = 'stopped', cancel_request = 0, updated = ? WHERE id = ?", (now(), job_id)
        )
        return "stopped"
    raise JobError(f"job {job_id} is neither queued nor running (status: {job['status']})", "conflict")


def reset_job(conn: sqlite3.Connection, env: Env, job_id: int, stage: str, queue: bool = False) -> None:
    """Discard stage `stage` and everything after it; the job becomes stopped (or queued)."""
    job = job_row(conn, job_id)
    _require_not_running(job, "reset it")
    first = stage_index(stage)
    with store.transaction(conn):
        conn.execute(
            "UPDATE stages SET state = 'pending', started = NULL, finished = NULL, summary = NULL, error = NULL"
            " WHERE job_id = ? AND idx >= ?",
            (job_id, first),
        )
        if first <= stage_index("select"):
            row = conn.execute("SELECT params FROM stages WHERE job_id = ? AND name = 'select'", (job_id,)).fetchone()
            p = store.loads(row["params"], {})
            p["confirmed"] = False
            conn.execute("UPDATE stages SET params = ? WHERE job_id = ? AND name = 'select'", (store.dumps(p), job_id))
        if first <= stage_index("rank"):
            conn.execute("DELETE FROM ranking WHERE job_id = ?", (job_id,))
        if first <= stage_index("report"):
            conn.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
        conn.execute(
            "UPDATE jobs SET status = ?, error = NULL, pause_reason = NULL, cancel_request = 0,"
            " queue_time = ?, updated = ? WHERE id = ?",
            ("queued" if queue else "stopped", now() if queue else job["queue_time"], now(), job_id),
        )
    for name in STAGE_NAMES[first - 1:]:
        for path in (
            layout.stage_dir(env, job_id, name),
            layout.stage_tmp(env, job_id, name),
            layout.stage_log(env, job_id, name),
        ):
            layout.safe_rmtree(env, path)


def delete_job(conn: sqlite3.Connection, env: Env, job_id: int, worker_alive: bool) -> str:
    """Returns 'deleted', or 'requested' if the worker has to stop the running job first."""
    job = job_row(conn, job_id)
    if job["status"] == "running" and worker_alive:
        conn.execute("UPDATE jobs SET cancel_request = 1, delete_request = 1 WHERE id = ?", (job_id,))
        return "requested"
    delete_now(conn, env, job_id)
    return "deleted"


def delete_now(conn: sqlite3.Connection, env: Env, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    layout.safe_rmtree(env, layout.job_dir(env, job_id))


def reassign_inputs(
    conn: sqlite3.Connection,
    env: Env,
    job_id: int,
    r1: str | None = None,
    r2: str | None = None,
    assembly: str | None = None,
    force: bool = False,
) -> dict:
    """Point the job to new input files. Same files (fingerprint) keep all results; different
    reads need force=True and reset the job to the start, a different assembly resets to 'assemble'."""
    job = job_row(conn, job_id)
    _require_not_running(job, "reassign its inputs")
    if r2 and not job["paired"]:
        raise JobError("this job has single-end reads; the pairing cannot be changed (create a new job)")
    old = {r["role"]: store.row_dict(r) for r in conn.execute("SELECT * FROM inputs WHERE job_id = ?", (job_id,))}
    new = {}
    try:
        for role, path in (("r1", r1), ("r2", r2), ("assembly", assembly)):
            if path:
                new[role] = file_record(path)
    except FileNotFoundError as exc:
        raise JobError(str(exc)) from None
    if not new:
        raise JobError("no new input file given")
    if "assembly" in new and not looks_like_fasta(new["assembly"]["path"]):
        raise JobError("the assembly is not a FASTA file")

    def same(role: str) -> bool:
        o = old.get(role)
        return o is not None and o["size"] == new[role]["size"] and o["fingerprint"] == new[role]["fingerprint"]

    changed = [role for role in new if not same(role)]
    reset_to = None
    if any(r in ("r1", "r2") for r in changed):
        reset_to = "input"
    elif "assembly" in changed:
        reset_to = "assemble"
    if reset_to and not force:
        raise JobError(
            f"different file(s) for: {', '.join(changed)}. This discards results from stage "
            f"'{reset_to}' on; confirm with force.",
            "conflict",
        )
    with store.transaction(conn):
        for role, rec in new.items():
            conn.execute(
                "INSERT INTO inputs (job_id, role, path, size, fingerprint) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (job_id, role) DO UPDATE SET path = excluded.path, size = excluded.size,"
                " fingerprint = excluded.fingerprint",
                (job_id, role, rec["path"], rec["size"], rec["fingerprint"]),
            )
    if "assembly" in new:
        _import_assembly(env, job_id, new["assembly"]["path"])
    if reset_to:
        reset_job(conn, env, job_id, reset_to)
    if "assembly" in new:
        row = conn.execute("SELECT params FROM stages WHERE job_id = ? AND name = 'assemble'", (job_id,)).fetchone()
        p = store.loads(row["params"], {})
        if p.get("mode") != "external":
            p["mode"] = "external"
            conn.execute("UPDATE stages SET params = ? WHERE job_id = ? AND name = 'assemble'", (store.dumps(p), job_id))
    _touch(conn, job_id)
    return {"changed": changed, "reset_to": reset_to}


# --------------------------------------------------------------------------- selection


def set_selection(conn: sqlite3.Connection, job_id: int, refs: list[str]) -> int:
    job = job_row(conn, job_id)
    _require_not_running(job, "change the selection")
    states = {r["name"]: r["state"] for r in conn.execute("SELECT name, state FROM stages WHERE job_id = ?", (job_id,))}
    if states["rank"] != "done":
        raise JobError("the hits are not ranked yet", "conflict")
    if states["select"] != "pending":
        raise JobError("the selection is already done; reset the job to 'select' to change it", "conflict")
    known = {r["ref"] for r in conn.execute("SELECT ref FROM ranking WHERE job_id = ?", (job_id,))}
    unknown = [r for r in refs if r not in known]
    if unknown:
        raise JobError(f"not in the ranking: {', '.join(unknown[:10])}")
    with store.transaction(conn):
        conn.execute("UPDATE ranking SET selected = 0 WHERE job_id = ?", (job_id,))
        conn.executemany(
            "UPDATE ranking SET selected = 1 WHERE job_id = ? AND ref = ?", [(job_id, r) for r in set(refs)]
        )
    return len(set(refs))


# --------------------------------------------------------------------------- worker side


def claim_next(conn: sqlite3.Connection) -> int | None:
    with store.transaction(conn):
        row = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY queue_time, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE jobs SET status = 'running', updated = ? WHERE id = ?", (now(), row["id"]))
        return row["id"]


def next_pending(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute(
        "SELECT name, params, pause_after FROM stages WHERE job_id = ? AND state = 'pending' ORDER BY idx LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    return {"name": row["name"], "params": store.loads(row["params"], {}), "pause_after": bool(row["pause_after"])}


def stage_started(conn: sqlite3.Connection, job_id: int, stage: str) -> None:
    conn.execute(
        "UPDATE stages SET started = ?, finished = NULL, error = NULL WHERE job_id = ? AND name = ?",
        (now(), job_id, stage),
    )


def stage_finished(conn: sqlite3.Connection, job_id: int, stage: str, state: str, summary: dict | None) -> None:
    conn.execute(
        "UPDATE stages SET state = ?, finished = ?, summary = ? WHERE job_id = ? AND name = ?",
        (state, now(), None if summary is None else store.dumps(summary), job_id, stage),
    )
    _touch(conn, job_id)


def set_status(conn: sqlite3.Connection, job_id: int, status: str, **fields) -> None:
    assert status in STATUSES
    sets = ["status = ?", "updated = ?"]
    values: list = [status, now()]
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        values.append(v)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*values, job_id))


def set_error(conn: sqlite3.Connection, job_id: int, stage: str, message: str) -> None:
    conn.execute("UPDATE stages SET error = ? WHERE job_id = ? AND name = ?", (message, job_id, stage))
    set_status(conn, job_id, "error", error=f"{stage}: {message}", cancel_request=0)


def _cleanup_tmp(env: Env, job_id: int) -> None:
    d = layout.job_dir(env, job_id)
    if d.is_dir():
        for p in d.glob("*.tmp"):
            layout.safe_rmtree(env, p)


def recover(conn: sqlite3.Connection, env: Env) -> list[int]:
    """After a crash (called by a worker holding the lock): jobs left 'running' go back to the
    queue at the start of their interrupted stage; stale .tmp folders are removed."""
    recovered = []
    for row in conn.execute("SELECT id, delete_request, cancel_request FROM jobs WHERE status = 'running'").fetchall():
        job_id = row["id"]
        if row["delete_request"]:
            delete_now(conn, env, job_id)
            continue
        _cleanup_tmp(env, job_id)
        if row["cancel_request"]:
            set_status(conn, job_id, "stopped", cancel_request=0)
        else:
            set_status(conn, job_id, "queued")
        recovered.append(job_id)
    if env.jobs_dir.is_dir():
        for d in env.jobs_dir.iterdir():
            if d.is_dir():
                for p in d.glob("*.tmp"):
                    layout.safe_rmtree(env, p)
    return recovered


def default_name(r1: str | Path) -> str:
    from .logic.pairs import _PATTERNS, _find

    name = Path(r1).name
    for ext in (".fastq.gz", ".fq.gz", ".fastq.bz2", ".fq.bz2", ".fastq", ".fq"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    for m1, _m2, follow in _PATTERNS:
        hits = _find(name + ".fastq", m1, follow)
        if hits:
            m = hits[-1]
            name = name[: m.start()] + name[m.end():]
            break
    return name or "job"
