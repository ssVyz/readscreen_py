"""Download and installation of prebuilt BLAST databases from NCBI.

Only the RefSeq viral reference genomes (`ref_viruses_rep_genomes`) are offered for now, together
with NCBI's taxonomy files (`taxdb`), which BLAST needs for the species names.

A download runs in its own process (one at a time: OS lock on db/download.lock) and reports its
progress in the `download` table, so the CLI and the web page show the same state. Steps:

1. check: read NCBI's metadata files (version date, archive names, sizes).
2. download: into <blast_db_dir>/.download/<name>/, resumable, each archive checked against its .md5.
3. unpack: into a staging folder next to the installed database.
4. test: blastdbcmd must be able to read the new database.
5. wait: until the worker is not running a stage that reads the database (worker.DB_STAGES); the
   worker in turn does not start such a stage while the `installing` flag is set.
6. install: swap the folders by renaming; the previous version is restored if that fails.

Only the standard library is used (urllib, hashlib, tarfile); proxies come from the usual
HTTPS_PROXY / HTTP_PROXY environment variables (and the system settings on Windows).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__, runner, settings as settings_mod, store
from .paths import ROOT, Env, ToolMissing
from .stages.common import blast_db_dir, describe_db, read_db_metadata
from .worker import DB_STAGES, WorkerLock, is_running as worker_running

NET_ERRORS = (OSError, http.client.HTTPException)

USER_AGENT = f"readscreen/{__version__}"
TIMEOUT = 60.0  # seconds without data before a request fails
CHUNK = 1 << 20
REMOTE_CACHE_SECONDS = 600
SPACE_MARGIN = 100 << 20  # free space kept on top of what the download needs
STAGING = ".download"  # inside the database folder, so the final rename stays on one file system


class DownloadError(RuntimeError):
    """A download, check or installation step failed."""


class HttpError(DownloadError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AlreadyRunning(RuntimeError):
    """Another download holds the lock."""


class Cancelled(Exception):
    """The download was cancelled."""


@dataclass(frozen=True)
class Source:
    name: str  # NCBI database name; also the folder and the file prefix in the database folder
    title: str
    metadata: tuple[str, ...]  # NCBI metadata files: the database first, then the taxonomy

    @property
    def db(self) -> str:
        """The value of the blast stage's `db` parameter for this database."""
        return f"{self.name}/{self.name}"


SOURCES: dict[str, Source] = {
    "ref_viruses_rep_genomes": Source(
        "ref_viruses_rep_genomes",
        "RefSeq viral reference genomes",
        ("ref_viruses_rep_genomes-nucl-metadata.json", "taxdb-metadata.json"),
    ),
}
DEFAULT_SOURCE = "ref_viruses_rep_genomes"


def get_source(name: str | None) -> Source:
    name = name or DEFAULT_SOURCE
    if name not in SOURCES:
        raise ValueError(f"unknown database: {name} (available: {', '.join(SOURCES)})")
    return SOURCES[name]


# ---------------------------------------------------------------------------- HTTP


def _open(url: str, headers: dict | None = None, timeout: float = TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise HttpError(f"{url}: HTTP {exc.code} {exc.reason}", exc.code) from None
    except NET_ERRORS as exc:
        raise HttpError(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from None


def _fetch(url: str, timeout: float = 30.0) -> bytes:
    """A small file (metadata, checksum)."""
    with _open(url, timeout=timeout) as resp:
        try:
            return resp.read(1 << 20)
        except NET_ERRORS as exc:
            raise HttpError(f"reading {url} failed: {exc}") from None


def _base(url: str) -> str:
    return url if url.endswith("/") else url + "/"


# ---------------------------------------------------------------------------- versions


_REMOTE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_REMOTE_LOCK = threading.Lock()


def remote_info(base_url: str, source: Source, max_age: float = 0.0) -> dict:
    """What NCBI offers: version date, size, archives. Cached for `max_age` seconds."""
    base = _base(base_url)
    key = (base, source.name)
    with _REMOTE_LOCK:
        hit = _REMOTE_CACHE.get(key)
        if max_age > 0 and hit and time.monotonic() - hit[0] < max_age:
            return hit[1]
    parts = []
    for meta_name in source.metadata:
        url = base + meta_name
        try:
            meta = json.loads(_fetch(url))
        except ValueError:
            raise DownloadError(f"{url}: not a valid metadata file") from None
        files = [str(f).rstrip("/").rsplit("/", 1)[-1] for f in meta.get("files") or []]
        if not files or not all(f.endswith(".tar.gz") for f in files):
            raise DownloadError(f"{url}: expected .tar.gz archives, got {meta.get('files')}")
        parts.append({
            "dbname": meta.get("dbname"),
            "updated": str(meta.get("last-updated") or "")[:10] or None,
            "archives": [{"file": f, "url": base + f} for f in files],
            "download_bytes": int(meta.get("bytes-total-compressed") or 0),
            "disk_bytes": int(meta.get("bytes-total") or 0),
            "sequences": meta.get("number-of-sequences"),
            "letters": meta.get("number-of-letters"),
        })
    main = parts[0]
    info = {
        "name": source.name,
        "updated": main["updated"],
        "sequences": main["sequences"],
        "letters": main["letters"],
        "taxdb_updated": parts[1]["updated"] if len(parts) > 1 else None,
        "archives": [a for p in parts for a in p["archives"]],
        "download_bytes": sum(p["download_bytes"] for p in parts),
        "disk_bytes": sum(p["disk_bytes"] for p in parts),
    }
    with _REMOTE_LOCK:
        _REMOTE_CACHE[key] = (time.monotonic(), info)
    return info


def installed(env: Env, values: dict, source: Source) -> dict | None:
    """The installed copy of a source (None if not installed)."""
    folder = blast_db_dir(values) / source.name
    db = folder / source.name
    if not any(folder.glob(source.name + ".n*")):
        return None
    try:
        info = describe_db(read_db_metadata(env.tool("blastdbcmd"), db))
    except ToolMissing:
        info = describe_db(None)
    return {**info, "path": str(folder), "taxdb": (folder / "taxdb.btd").is_file()}


def update_available(local: dict | None, remote: dict | None) -> bool | None:
    if remote is None:
        return None
    if local is None or not local.get("updated") or not remote.get("updated"):
        return True
    return remote["updated"] > local["updated"]


# ---------------------------------------------------------------------------- process control


def is_running(env: Env) -> bool:
    lock = WorkerLock(env.download_lock_path)
    if lock.acquire():
        lock.release()
        return False
    return True


def status(env: Env) -> dict:
    conn = store.connect(env.db_path)
    try:
        row = store.row_dict(conn.execute("SELECT * FROM download WHERE id = 1").fetchone())
    finally:
        conn.close()
    row.pop("id", None)
    running = is_running(env)
    if row["state"] == "running" and not running:
        row["state"] = "error"
        row["error"] = row["error"] or "the download process ended unexpectedly"
    return {"running": running, **row}


def request_cancel(env: Env) -> bool:
    if not is_running(env):
        return False
    conn = store.connect(env.db_path)
    try:
        conn.execute("UPDATE download SET cancel_request = 1 WHERE id = 1")
    finally:
        conn.close()
    return True


def start_detached(env: Env, name: str, timeout: float = 15.0) -> int:
    """Start `dbs download` as a background process that outlives the caller. Returns its PID."""
    source = get_source(name)
    if is_running(env):
        raise AlreadyRunning("a database download is already running")
    env.ensure_dirs()
    cmd = [sys.executable, str(ROOT / "main.py"), "--data-dir", str(env.data_dir),
           "dbs", "download", "--source", source.name, "--force"]
    out_path = env.logs_dir / "download.out"
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
        if status(env)["pid"] == proc.pid:
            return proc.pid  # running, or already finished (an early error is in the status)
        if proc.poll() is not None:
            raise RuntimeError(f"the download exited right away:\n{runner.tail(out_path)}")
        time.sleep(0.2)
    raise RuntimeError("the download did not report as running in time")


# ---------------------------------------------------------------------------- the download


def _md5_file(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _rmtree(path: Path) -> None:
    for attempt in range(5):
        if not path.exists():
            return
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.5)


def _retry(fn: Callable[[], Any], seconds: float = 30.0) -> None:
    """Renames on Windows fail while another process has a file open; try for a while."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            fn()
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _mb(n: int | None) -> str:
    return "?" if n is None else f"{n / 1e6:.0f} MB"


class Downloader:
    """Downloads, checks and installs one source. Run it with `run()` in its own process."""

    def __init__(self, env: Env, source: Source, progress: Callable[[dict], None] | None = None):
        self.env = env
        self.source = source
        self.progress = progress
        self.conn = None
        self._state: dict = {}
        self._last_write = 0.0

    # ------------------------------------------------------------------ status row
    def _update(self, force: bool = True, **fields) -> None:
        self._state.update(fields)
        now = time.monotonic()
        if force or now - self._last_write >= 0.5:
            self._last_write = now
            cols = ", ".join(f"{k} = ?" for k in self._state)
            self.conn.execute(f"UPDATE download SET {cols} WHERE id = 1", tuple(self._state.values()))
            self._state = {}
            if self.progress:
                row = store.row_dict(self.conn.execute("SELECT * FROM download WHERE id = 1").fetchone())
                self.progress(row)

    def _phase(self, phase: str, message: str, total: int | None = None) -> None:
        self._update(phase=phase, message=message, bytes_done=0 if total else None, bytes_total=total)

    def _check_cancel(self) -> None:
        if self.conn.execute("SELECT cancel_request FROM download WHERE id = 1").fetchone()["cancel_request"]:
            raise Cancelled()

    # ------------------------------------------------------------------ main
    def run(self) -> dict:
        self.env.ensure_dirs()
        lock = WorkerLock(self.env.download_lock_path)
        if not lock.acquire(timeout=1):
            raise AlreadyRunning("a database download is already running")
        self.conn = store.connect(self.env.db_path)
        staging = None
        try:
            values = settings_mod.get_all(self.conn)
            folder = blast_db_dir(values)
            staging = folder / STAGING / self.source.name
            self._update(pid=os.getpid(), db=self.source.name, state="running", phase="check", message="starting",
                         bytes_done=None, bytes_total=None, version=None, error=None, started=time.time(),
                         finished=None, cancel_request=0, installing=0)
            try:
                result = self._steps(values, folder, staging)
            except (Cancelled, KeyboardInterrupt):
                self._cleanup(staging, everything=True)
                self._update(state="cancelled", phase=None, message="cancelled", finished=time.time(), installing=0)
                raise Cancelled() from None
            except Exception as exc:
                self._cleanup(staging, everything=False)
                if isinstance(exc, (DownloadError, OSError, tarfile.TarError, ToolMissing)):
                    message = str(exc)
                else:
                    message = f"internal error: {exc!r}"
                self._update(state="error", phase=None, message="failed", error=message,
                             finished=time.time(), installing=0)
                raise DownloadError(message) from exc
            self._update(state="done", phase=None, message=f"installed version {result['updated']}",
                         finished=time.time(), installing=0)
            return result
        finally:
            self.conn.close()
            lock.release()

    def _cleanup(self, staging: Path | None, everything: bool) -> None:
        """After a failure keep the downloaded archives (a retry resumes); after a cancel remove all."""
        if staging is None:
            return
        try:
            if everything:
                _rmtree(staging)
                if staging.parent.is_dir() and not any(staging.parent.iterdir()):
                    staging.parent.rmdir()
            else:
                _rmtree(staging / "new")
        except OSError:
            pass

    def _steps(self, values: dict, folder: Path, staging: Path) -> dict:
        src = self.source
        self._phase("check", "reading the NCBI metadata")
        remote = remote_info(values["download_url"], src)
        self._update(version=remote["updated"])
        staging.mkdir(parents=True, exist_ok=True)
        self._check_space(staging, remote)

        archives = []
        done_before = 0
        for i, archive in enumerate(remote["archives"], 1):
            message = f"downloading {archive['file']} ({i}/{len(remote['archives'])})"
            if i == 1:
                self._phase("download", message, total=remote["download_bytes"] or None)
            else:
                self._update(message=message)
            path = self._download(archive, staging, done_before)
            archives.append(path)
            done_before += path.stat().st_size

        new = staging / "new"
        _rmtree(new)
        new.mkdir()
        self._phase("unpack", "unpacking", total=remote["disk_bytes"] or None)
        unpacked = 0
        for path in archives:
            unpacked = self._unpack(path, new, unpacked)

        self._phase("test", "testing the new database with blastdbcmd")
        info = describe_db(read_db_metadata(self.env.tool("blastdbcmd"), new / src.name))
        if not info["sequences"]:
            raise DownloadError("blastdbcmd cannot read the downloaded database")
        if not (new / "taxdb.btd").is_file():
            raise DownloadError("the download does not contain the taxonomy files (taxdb.btd)")

        self._wait_for_worker()
        self._phase("install", "installing")
        self._install(new, folder / src.name, staging / "old")
        self._cleanup(staging, everything=True)
        return {**info, "name": src.name, "db": src.db, "path": str(folder / src.name)}

    def _check_space(self, staging: Path, remote: dict) -> None:
        have = sum(p.stat().st_size for p in staging.iterdir() if p.is_file())
        need = max(0, remote["download_bytes"] - have) + remote["disk_bytes"] + SPACE_MARGIN
        free = shutil.disk_usage(staging).free
        if free < need:
            raise DownloadError(
                f"not enough disk space in {staging.parent.parent}: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free"
            )

    # ------------------------------------------------------------------ download
    def _download(self, archive: dict, staging: Path, offset: int) -> Path:
        target = staging / archive["file"]
        part = staging / (archive["file"] + ".part")
        line = _fetch(archive["url"] + ".md5").decode("ascii", "replace").split()
        expected = line[0].lower() if line else ""
        if len(expected) != 32:
            raise DownloadError(f"{archive['url']}.md5: no checksum found")
        if target.is_file():
            if _md5_file(target) == expected:  # complete from an earlier attempt
                self._update(bytes_done=offset + target.stat().st_size)
                return target
            target.unlink()
        for _ in range(2):
            digest, resumed = self._fetch_to(archive["url"], part, offset)
            if digest == expected:
                part.replace(target)
                return target
            part.unlink()
            if not resumed:
                break
            # a resumed file can be from an older release: once more from the start
        raise DownloadError(f"{archive['file']}: checksum mismatch (the download is damaged; try again)")

    def _fetch_to(self, url: str, part: Path, offset: int) -> tuple[str, bool]:
        have = part.stat().st_size if part.is_file() else 0
        try:
            resp = _open(url, {"Range": f"bytes={have}-"} if have else None)
        except HttpError as exc:
            if exc.status != 416:  # 416: the part is not shorter than the file; start over
                raise
            part.unlink()
            have = 0
            resp = _open(url)
        digest = hashlib.md5(usedforsecurity=False)
        with resp:
            resumed = bool(have) and resp.status == 206
            if resumed:
                with open(part, "rb") as fh:
                    while chunk := fh.read(CHUNK):
                        digest.update(chunk)
            else:
                have = 0
            length = resp.headers.get("Content-Length")
            expected_end = have + int(length) if length and length.isdigit() else None
            with open(part, "ab" if resumed else "wb") as fh:
                while True:
                    self._check_cancel()
                    try:
                        chunk = resp.read(CHUNK)
                    except NET_ERRORS as exc:
                        raise DownloadError(
                            f"the download of {url} was interrupted ({exc}); try again to resume"
                        ) from None
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    have += len(chunk)
                    self._update(force=False, bytes_done=offset + have)
        self._update(bytes_done=offset + have)
        if expected_end is not None and have < expected_end:
            raise DownloadError(f"the connection closed early ({_mb(have)} of {_mb(expected_end)}); try again to resume")
        return digest.hexdigest(), resumed

    # ------------------------------------------------------------------ unpack and install
    def _unpack(self, archive: Path, dest: Path, done: int) -> int:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                self._check_cancel()
                name = member.name[2:] if member.name.startswith("./") else member.name
                if member.isdir() and name in ("", "."):
                    continue
                if not member.isfile() or not name or "/" in name or "\\" in name or name.startswith("."):
                    raise DownloadError(f"{archive.name}: unexpected entry {member.name!r}")
                src = tar.extractfile(member)
                with src, open(dest / name, "wb") as out:
                    while chunk := src.read(CHUNK):
                        out.write(chunk)
                        done += len(chunk)
                        self._update(force=False, bytes_done=done)
        self._update(bytes_done=done)
        return done

    def _wait_for_worker(self) -> None:
        """Set the installing flag, then wait until no running stage reads the database."""
        self._phase("wait", "waiting until no job is using the database")
        with store.transaction(self.conn):
            self.conn.execute("UPDATE download SET installing = 1 WHERE id = 1")
        shown = None
        while True:
            row = self.conn.execute("SELECT job_id, stage FROM worker WHERE id = 1").fetchone()
            if row["stage"] not in DB_STAGES or not worker_running(self.env):
                return
            if shown != (row["job_id"], row["stage"]):
                shown = (row["job_id"], row["stage"])
                self._update(message=f"waiting for job {row['job_id']} to finish its {row['stage']} stage")
            self._check_cancel()
            time.sleep(1.0)

    def _install(self, new: Path, final: Path, old: Path) -> None:
        _rmtree(old)
        had_old = final.exists()
        if had_old:
            _retry(lambda: final.rename(old))
        try:
            _retry(lambda: new.rename(final))
        except OSError:
            if had_old:
                old.rename(final)  # put the previous version back
            raise
        with store.transaction(self.conn):
            self.conn.execute("UPDATE download SET installing = 0 WHERE id = 1")
        try:
            _rmtree(old)
        except OSError:
            pass  # removed with the staging folder next time
