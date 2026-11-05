"""The worker process: the only part of readscreen that runs the pipeline binaries.

Only one worker can run at a time (OS file lock on db/worker.lock, released automatically when
the process dies). Stop requests and job cancellation reach it through the DB.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import traceback
from types import ModuleType

from . import jobs, layout, runner, settings as settings_mod, store
from .params import ParamError
from .paths import ROOT, Env, ToolMissing
from .stages import registry
from .stages.common import PauseRequest, StageContext, StageError

log = logging.getLogger("readscreen.worker")


class AlreadyRunning(RuntimeError):
    """Another worker holds the lock."""


class WorkerLock:
    def __init__(self, path):
        self.path = path
        self._fh = None

    def acquire(self, timeout: float = 0.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            fh = open(self.path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return True
            except OSError:
                fh.close()
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.1)

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def is_running(env: Env) -> bool:
    lock = WorkerLock(env.lock_path)
    if lock.acquire():
        lock.release()
        return False
    return True


def status(env: Env) -> dict:
    conn = store.connect(env.db_path)
    try:
        row = store.row_dict(conn.execute("SELECT * FROM worker WHERE id = 1").fetchone())
    finally:
        conn.close()
    running = is_running(env)
    row.pop("id", None)
    if not running:
        row = {k: None for k in row}
    return {"running": running, **row}


def request_stop(env: Env, now: bool = False) -> bool:
    """Ask a running worker to stop. Returns False if no worker is running."""
    if not is_running(env):
        return False
    conn = store.connect(env.db_path)
    try:
        conn.execute("UPDATE worker SET stop_request = ? WHERE id = 1", ("now" if now else "graceful",))
    finally:
        conn.close()
    return True


def force_kill(env: Env) -> None:
    """Kill an unresponsive worker and its tool, then clean up as a crash."""
    conn = store.connect(env.db_path)
    try:
        row = conn.execute("SELECT pid, tool_pid FROM worker WHERE id = 1").fetchone()
        for pid in (row["tool_pid"], row["pid"]):
            if pid and runner.pid_alive(pid):
                runner.kill_tree(pid)
        lock = WorkerLock(env.lock_path)
        if lock.acquire(timeout=10):
            try:
                jobs.recover(conn, env)
                _clear_row(conn)
            finally:
                lock.release()
    finally:
        conn.close()


def stop(env: Env, now: bool = False, wait: float = 0.0) -> str:
    """Stop the worker: 'not_running', 'requested', 'stopped' or 'killed'."""
    if not request_stop(env, now):
        return "not_running"
    if wait <= 0:
        return "requested"
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not is_running(env):
            return "stopped"
        time.sleep(0.2)
    if now:
        force_kill(env)
        return "killed"
    return "requested"


def start_detached(env: Env, timeout: float = 15.0) -> int:
    """Start `worker run` as a background process that outlives the caller. Returns its PID."""
    if is_running(env):
        raise AlreadyRunning("a worker is already running")
    env.ensure_dirs()
    cmd = [sys.executable, str(ROOT / "main.py"), "--data-dir", str(env.data_dir), "worker", "run"]
    out_path = env.logs_dir / "worker.out"
    with open(out_path, "ab") as out:
        kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "close_fds": True, "cwd": str(ROOT)}
        if os.name == "nt":
            base = runner.DETACHED_PROCESS | runner.CREATE_NEW_PROCESS_GROUP
            try:
                proc = subprocess.Popen(cmd, creationflags=base | runner.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
            except OSError:
                proc = subprocess.Popen(cmd, creationflags=base, **kwargs)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the worker exited right away:\n{runner.tail(out_path)}")
        st = status(env)
        if st["running"] and st["pid"] == proc.pid:
            return proc.pid
        time.sleep(0.2)
    raise RuntimeError("the worker did not report as running in time")


def _clear_row(conn) -> None:
    conn.execute(
        "UPDATE worker SET pid = NULL, tool_pid = NULL, started = NULL, heartbeat = NULL, job_id = NULL,"
        " stage = NULL, stop_request = NULL WHERE id = 1"
    )


def setup_logging(env: Env, console: bool) -> None:
    root = logging.getLogger("readscreen")
    if any(getattr(h, "_readscreen", False) for h in root.handlers):
        return
    env.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(env.logs_dir / "worker.log", maxBytes=5_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    fh._readscreen = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh._readscreen = True  # type: ignore[attr-defined]
        root.addHandler(sh)
    root.setLevel(logging.INFO)


class Worker:
    """Processes queued jobs one at a time, in queue order."""

    def __init__(
        self,
        env: Env,
        exit_when_empty: bool = False,
        poll: float | None = None,
        stages: dict[str, ModuleType] | None = None,
    ):
        self.env = env
        self.exit_when_empty = exit_when_empty
        self.poll = poll
        self.stages = stages or registry()
        self.conn = None
        self._stop_hb = threading.Event()

    # ------------------------------------------------------------------ helpers
    def _stop_request(self) -> str | None:
        return self.conn.execute("SELECT stop_request FROM worker WHERE id = 1").fetchone()["stop_request"]

    def _cancelled(self, job_id: int) -> bool:
        row = self.conn.execute("SELECT cancel_request FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row is None or bool(row["cancel_request"]) or self._stop_request() == "now"

    def _set_tool(self, pid: int | None) -> None:
        self.conn.execute("UPDATE worker SET tool_pid = ? WHERE id = 1", (pid,))

    def _heartbeat(self, interval: float) -> None:
        conn = store.connect(self.env.db_path)
        try:
            while not self._stop_hb.wait(interval):
                conn.execute("UPDATE worker SET heartbeat = ? WHERE id = 1", (time.time(),))
        finally:
            conn.close()

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_request():
                return
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        self.env.ensure_dirs()
        store.init_db(self.env.db_path)
        lock = WorkerLock(self.env.lock_path)
        if not lock.acquire(timeout=2):
            raise AlreadyRunning("a worker is already running")
        self.conn = store.connect(self.env.db_path)
        hb = None
        try:
            t = time.time()
            self.conn.execute(
                "UPDATE worker SET pid = ?, tool_pid = NULL, started = ?, heartbeat = ?, job_id = NULL,"
                " stage = NULL, stop_request = NULL WHERE id = 1",
                (os.getpid(), t, t),
            )
            recovered = jobs.recover(self.conn, self.env)
            if recovered:
                log.info("recovered interrupted jobs: %s", recovered)
            values = settings_mod.get_all(self.conn)
            hb = threading.Thread(target=self._heartbeat, args=(float(values["heartbeat_interval"]),), daemon=True)
            hb.start()
            log.info("worker started (pid %s)", os.getpid())
            while True:
                if self._stop_request():
                    break
                job_id = jobs.claim_next(self.conn)
                if job_id is None:
                    if self.exit_when_empty:
                        break
                    self._sleep(self.poll if self.poll is not None else float(settings_mod.get_all(self.conn)["poll_interval"]))
                    continue
                outcome = self._process(job_id)
                self.conn.execute("UPDATE worker SET job_id = NULL, stage = NULL WHERE id = 1")
                if outcome == "stop":
                    break
            log.info("worker stopped")
            return 0
        finally:
            self._stop_hb.set()
            if hb is not None:
                hb.join(timeout=2)
            _clear_row(self.conn)
            self.conn.close()
            lock.release()

    def _process(self, job_id: int) -> str:
        """Run the stages of one job until it pauses, finishes, fails or is stopped."""
        conn, env = self.conn, self.env
        log.info("job %s: started", job_id)
        while True:
            if self._stop_request() == "graceful":
                jobs.set_status(conn, job_id, "queued")
                log.info("job %s: back in the queue (worker stop)", job_id)
                return "stop"
            job = jobs.job_row(conn, job_id)
            if job["delete_request"]:
                jobs.delete_now(conn, env, job_id)
                log.info("job %s: deleted", job_id)
                return "next"
            if job["cancel_request"]:
                jobs.set_status(conn, job_id, "stopped", cancel_request=0)
                log.info("job %s: cancelled", job_id)
                return "next"
            nxt = jobs.next_pending(conn, job_id)
            if nxt is None:
                jobs.set_status(conn, job_id, "done")
                log.info("job %s: done", job_id)
                return "next"
            name, params = nxt["name"], nxt["params"]
            mod = self.stages[name]
            if getattr(mod, "is_skipped", None) and mod.is_skipped(params):
                jobs.stage_finished(conn, job_id, name, "skipped", None)
                continue
            tmp = layout.stage_tmp(env, job_id, name)
            final = layout.stage_dir(env, job_id, name)
            layout.safe_rmtree(env, tmp)
            layout.safe_rmtree(env, final)
            tmp.mkdir(parents=True)
            jobs.stage_started(conn, job_id, name)
            conn.execute("UPDATE worker SET job_id = ?, stage = ? WHERE id = 1", (job_id, name))
            values = settings_mod.get_all(conn)
            ctx = StageContext(env, conn, job, name, params, tmp, values,
                               cancel_check=lambda: self._cancelled(job_id), on_tool=self._set_tool)
            ctx.log(f"stage '{name}' started")
            log.info("job %s: stage %s", job_id, name)
            try:
                summary = mod.run(ctx)
            except PauseRequest as pr:
                layout.safe_rmtree(env, tmp)
                jobs.set_status(conn, job_id, "paused", pause_reason=pr.reason)
                ctx.log(f"paused: {pr.reason}")
                return "next"
            except runner.Cancelled:
                layout.safe_rmtree(env, tmp)
                ctx.log("stopped")
                job = jobs.job_row(conn, job_id)
                if job["delete_request"]:
                    jobs.delete_now(conn, env, job_id)
                    return "next"
                if job["cancel_request"]:
                    jobs.set_status(conn, job_id, "stopped", cancel_request=0)
                    return "next"
                jobs.set_status(conn, job_id, "queued")
                return "stop"
            except KeyboardInterrupt:
                layout.safe_rmtree(env, tmp)
                jobs.set_status(conn, job_id, "queued")
                raise
            except (StageError, runner.ToolError, ToolMissing, ParamError, OSError) as exc:
                layout.safe_rmtree(env, tmp)
                ctx.log(f"error: {exc}")
                jobs.set_error(conn, job_id, name, str(exc))
                log.warning("job %s: stage %s failed: %s", job_id, name, exc)
                return "next"
            except Exception as exc:  # a bug: keep the worker alive, report it on the job
                layout.safe_rmtree(env, tmp)
                ctx.log("internal error:\n" + traceback.format_exc())
                jobs.set_error(conn, job_id, name, f"internal error: {exc!r}")
                log.exception("job %s: stage %s crashed", job_id, name)
                return "next"
            finally:
                conn.execute("UPDATE worker SET stage = NULL WHERE id = 1")
            layout.safe_rename(tmp, final)
            jobs.stage_finished(conn, job_id, name, "done", summary)
            ctx.log(f"stage '{name}' done")
            if nxt["pause_after"]:
                jobs.set_status(conn, job_id, "paused", pause_reason=f"after:{name}")
                return "next"
