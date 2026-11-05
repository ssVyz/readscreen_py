"""Core facade: the only module the CLI and the web server call.

Every method opens a short-lived DB connection and returns plain, JSON-serialisable data.
Errors are raised as ApiError with a code: invalid, not_found or conflict.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import __version__, jobs, layout, params as params_mod, settings as settings_mod, store, worker
from .logic import pairs
from .params import ParamError
from .paths import Env, ToolMissing
from .runner import CREATE_NO_WINDOW, tail
from .stages import registry
from .stages.common import blast_db_dir


class ApiError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ApiError:
        raise
    except jobs.JobError as exc:
        raise ApiError(str(exc), exc.code) from None
    except (ParamError, ValueError, FileNotFoundError) as exc:
        raise ApiError(str(exc)) from None
    except worker.AlreadyRunning as exc:
        raise ApiError(str(exc), "conflict") from None
    except ToolMissing as exc:
        raise ApiError(str(exc)) from None


class Api:
    def __init__(self, env: Env | None = None):
        self.env = env or Env.default()
        self.env.ensure_dirs()
        store.init_db(self.env.db_path)

    @contextmanager
    def _db(self) -> Iterator[Any]:
        conn = store.connect(self.env.db_path)
        try:
            with _errors():
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ info
    def version(self) -> str:
        return __version__

    def stage_info(self, platform: str | None = None) -> list[dict]:
        return [
            {"index": i, "name": name, "params": params_mod.describe(mod.PARAMS, platform)}
            for i, (name, mod) in enumerate(registry().items(), 1)
        ]

    def list_dbs(self) -> list[dict]:
        """Nucleotide BLAST databases in the database folder (via blastdbcmd -list)."""
        with self._db() as conn:
            values = settings_mod.get_all(conn)
        folder = blast_db_dir(values)
        if not folder.is_dir():
            return []
        with _errors():
            exe = self.env.tool("blastdbcmd")
        proc = subprocess.run(
            [str(exe), "-list", str(folder), "-recursive", "-list_outfmt", "%f\t%p\t%t\t%n\t%l"],
            capture_output=True, text=True, timeout=60,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = []
        for line in proc.stdout.splitlines():
            f = line.split("\t")
            if len(f) < 5 or f[1].strip().lower() != "nucleotide":
                continue
            path = Path(f[0])
            try:
                name = path.resolve().relative_to(folder.resolve()).as_posix()
            except ValueError:
                name = str(path)
            out.append({"name": name, "path": str(path), "title": f[2], "sequences": int(f[3]), "bases": int(f[4])})
        return sorted(out, key=lambda d: d["name"])

    # ------------------------------------------------------------------ settings
    def get_settings(self) -> dict:
        with self._db() as conn:
            return settings_mod.get_all(conn)

    def set_setting(self, key: str, value: Any) -> Any:
        with self._db() as conn:
            return settings_mod.set_value(conn, key, value)

    # ------------------------------------------------------------------ jobs
    def create_job(
        self,
        *,
        platform: str,
        r1: str,
        r2: str | None = None,
        assembly: str | None = None,
        name: str | None = None,
        notes: str = "",
        params: dict | None = None,
        pauses: list[str] | tuple[str, ...] = (),
        queue: bool = True,
    ) -> dict:
        params = {k: dict(v) for k, v in (params or {}).items()}
        with self._db() as conn:
            values = settings_mod.get_all(conn)
            blast_params = params.setdefault("blast", {})
            if not blast_params.get("db") and not values.get("default.blast.db"):
                dbs = self.list_dbs()
                if len(dbs) == 1:
                    blast_params["db"] = dbs[0]["name"]
                elif not dbs:
                    raise ApiError(f"no BLAST database found in {blast_db_dir(values)}")
                else:
                    raise ApiError("choose a BLAST database: " + ", ".join(d["name"] for d in dbs))
            job_id = jobs.create_job(
                conn, self.env, values, name=name or jobs.default_name(r1), platform=platform,
                r1=r1, r2=r2, assembly=assembly, notes=notes, params=params, pauses=pauses, queue=queue,
            )
            return jobs.get_job(conn, job_id)

    def list_jobs(self) -> list[dict]:
        with self._db() as conn:
            return jobs.list_jobs(conn)

    def get_job(self, job_id: int) -> dict:
        with self._db() as conn:
            return jobs.get_job(conn, job_id)

    def update_job(self, job_id: int, name: str | None = None, notes: str | None = None) -> dict:
        with self._db() as conn:
            jobs.update_meta(conn, job_id, name, notes)
            return jobs.get_job(conn, job_id)

    def set_params(self, job_id: int, stage: str, updates: dict, reset: bool = False) -> dict:
        with self._db() as conn:
            return jobs.set_params(conn, self.env, job_id, stage, updates, reset)

    def set_pause(self, job_id: int, stage: str, on: bool) -> None:
        with self._db() as conn:
            jobs.set_pause(conn, job_id, stage, on)

    def queue_job(self, job_id: int) -> None:
        with self._db() as conn:
            jobs.queue_job(conn, job_id)

    def continue_job(self, job_id: int) -> None:
        with self._db() as conn:
            jobs.continue_job(conn, job_id)

    def cancel_job(self, job_id: int) -> str:
        with self._db() as conn:
            return jobs.cancel_job(conn, self.env, job_id, worker.is_running(self.env))

    def reset_job(self, job_id: int, stage: str, queue: bool = False) -> None:
        with self._db() as conn:
            jobs.reset_job(conn, self.env, job_id, stage, queue)

    def delete_job(self, job_id: int) -> str:
        with self._db() as conn:
            return jobs.delete_job(conn, self.env, job_id, worker.is_running(self.env))

    def reassign_inputs(self, job_id: int, r1: str | None = None, r2: str | None = None,
                        assembly: str | None = None, force: bool = False) -> dict:
        with self._db() as conn:
            return jobs.reassign_inputs(conn, self.env, job_id, r1, r2, assembly, force)

    def stage_log(self, job_id: int, stage: str, max_lines: int = 2000) -> str:
        with self._db() as conn:
            jobs.job_row(conn, job_id)
        with _errors():
            path = layout.stage_log(self.env, job_id, stage)
        return tail(path, max_lines) if path.is_file() else ""

    # ------------------------------------------------------------------ ranking, selection, results
    def get_ranking(self, job_id: int) -> list[dict]:
        with self._db() as conn:
            jobs.job_row(conn, job_id)
            rows = conn.execute(
                "SELECT * FROM ranking WHERE job_id = ? ORDER BY grp, leader DESC, score DESC", (job_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = store.row_dict(r)
            d.pop("job_id")
            d["group"] = d.pop("grp")
            for k in ("leader", "weak", "auto_selected", "selected"):
                d[k] = bool(d[k])
            out.append(d)
        return out

    def set_selection(self, job_id: int, refs: list[str]) -> int:
        with self._db() as conn:
            return jobs.set_selection(conn, job_id, refs)

    def get_results(self, job_id: int, with_bins: bool = False) -> dict:
        with self._db() as conn:
            job = jobs.get_job(conn, job_id)
            rows = conn.execute("SELECT data FROM results WHERE job_id = ?", (job_id,)).fetchall()
        refs = [json.loads(r["data"]) for r in rows]
        if not with_bins:
            for r in refs:
                r.pop("bins", None)
        refs.sort(key=lambda r: (-r["reads"], -r["breadth"], r["ref"]))
        report = next(s for s in job["stages"] if s["name"] == "report")
        return {
            "job": {"id": job_id, "name": job["name"], "status": job["status"]},
            "available": report["state"] == "done",
            "taxa": (report["summary"] or {}).get("taxa", []),
            "references": refs,
        }

    def get_result(self, job_id: int, ref: str) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT data FROM results WHERE job_id = ? AND ref = ?", (job_id, ref)).fetchone()
        if row is None:
            raise ApiError(f"no result for {ref} in job {job_id}", "not_found")
        return json.loads(row["data"])

    def export_results(self, job_id: int, fmt: str = "json") -> str:
        res = self.get_results(job_id, with_bins=(fmt == "json"))
        if fmt == "json":
            return json.dumps(res, indent=1)
        if fmt != "tsv":
            raise ApiError(f"unknown format {fmt!r} (json or tsv)")
        cols = ["ref", "species", "taxid", "title", "length", "reads", "ambiguous", "breadth", "breadth_1x",
                "mean_depth", "mean_depth_covered", "max_depth", "identity", "expected_breadth", "breadth_ratio", "flags"]
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for r in res["references"]:
            w.writerow([",".join(r[c]) if c == "flags" else ("" if r.get(c) is None else r.get(c)) for c in cols])
        return buf.getvalue()

    # ------------------------------------------------------------------ worker
    def worker_status(self) -> dict:
        return worker.status(self.env)

    def start_worker(self) -> int:
        with _errors():
            return worker.start_detached(self.env)

    def stop_worker(self, now: bool = False, wait: float = 0.0) -> str:
        return worker.stop(self.env, now, wait)

    # ------------------------------------------------------------------ files (used by the file picker)
    def find_mate(self, path: str) -> str | None:
        mate = pairs.find_mate(path)
        return str(mate) if mate else None
