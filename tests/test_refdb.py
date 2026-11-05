"""Database download (refdb): against a local fake NCBI server, without network access.

The fake archives hold placeholder files, so blastdbcmd cannot read them; the tests replace the
metadata reader. One test reads the real bundled database, and READSCREEN_NET=1 adds a check
of the real NCBI metadata files (a few hundred bytes).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
import threading
import time
import unittest
import warnings
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from readscreen import refdb, store
from readscreen.cli import main as cli_main
from readscreen.stages.common import describe_db, read_db_metadata
from readscreen.worker import Worker, WorkerLock

from .helpers import BLAST_DB, TempEnvCase, blast_db_available

NAME = "ref_viruses_rep_genomes"
SOURCE = refdb.SOURCES[NAME]


class RangeHandler(SimpleHTTPRequestHandler):
    """Static files with single-range support (enough for resumed downloads)."""

    requests: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.requests.append((self.path, self.headers.get("Range")))
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().do_GET()
        start = int(rng.split("=")[1].split("-")[0])
        data = path.read_bytes()
        if start >= len(data):
            self.send_error(416)
            return
        body = data[start:]
        self.send_response(206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.end_headers()
        self.wfile.write(body)


def make_tar(path: Path, files: dict[str, bytes]) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(gzip.compress(buf.getvalue(), compresslevel=1))


def fake_metadata(blastdbcmd, db):
    """Stands in for blastdbcmd -metadata: reads the version from a marker file in the fake DB."""
    marker = Path(db).parent / (Path(db).name + ".nin")
    if not marker.is_file():
        return None
    return {"description": "fake viral genomes", "last-updated": marker.read_text() + "T00:00:00",
            "number-of-sequences": 3, "number-of-letters": 3000}


class FakeNcbi:
    """A folder with metadata, archives and .md5 files like ftp.ncbi.nlm.nih.gov/blast/db/."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        RangeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, directory=str(root)))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version: str, nsq_size: int = 300_000, db_files: dict | None = None) -> None:
        nsq = hashlib.sha256(version.encode()).digest() * (nsq_size // 32 + 1)
        files = db_files or {f"{NAME}.nin": version.encode(), f"{NAME}.nsq": os.urandom(nsq_size)}
        make_tar(self.root / f"{NAME}.tar.gz", files)
        make_tar(self.root / "taxdb.tar.gz", {"taxdb.btd": b"btd" * 100, "taxdb.bti": b"bti",
                                              "taxonomy4blast.sqlite3": nsq[:1000]})
        for meta, archive, unpacked in ((f"{NAME}-nucl-metadata.json", f"{NAME}.tar.gz", nsq_size),
                                        ("taxdb-metadata.json", "taxdb.tar.gz", 1300)):
            data = (self.root / archive).read_bytes()
            (self.root / meta).write_text(json.dumps({
                "dbname": archive.split(".")[0], "description": "x", "number-of-sequences": 3,
                "number-of-letters": 3000, "files": [f"ftp://ftp.ncbi.nlm.nih.gov/blast/db/{archive}"],
                "last-updated": f"{version}T00:00:00", "bytes-total": unpacked,
                "bytes-total-compressed": len(data)}))
            (self.root / (archive + ".md5")).write_text(f"{hashlib.md5(data).hexdigest()}  {archive}\n")

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DownloadCase(TempEnvCase):
    def setUp(self):
        super().setUp()
        self.ncbi = FakeNcbi(self.tmp / "ncbi")
        self.dbdir = self.tmp / "blast_db"
        self.api.set_setting("blast_db_dir", str(self.dbdir))
        self.api.set_setting("download_url", self.ncbi.url)
        patcher = mock.patch("readscreen.refdb.read_db_metadata", fake_metadata)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.ncbi.close()
        super().tearDown()

    def download(self, progress=None):
        return refdb.Downloader(self.env, SOURCE, progress).run()

    def installed_version(self):
        return (self.dbdir / NAME / f"{NAME}.nin").read_text()


class DownloadTest(DownloadCase):
    def test_remote_info(self):
        self.ncbi.publish("2026-10-25")
        info = refdb.remote_info(self.ncbi.url, SOURCE)
        self.assertEqual(info["updated"], "2026-10-25")
        self.assertEqual([a["file"] for a in info["archives"]], [f"{NAME}.tar.gz", "taxdb.tar.gz"])
        self.assertTrue(info["archives"][0]["url"].startswith(self.ncbi.url))  # ftp:// rewritten to the base URL
        self.assertGreater(info["download_bytes"], 300_000)

    def test_download_install_and_update(self):
        self.ncbi.publish("2026-10-25")
        sources = self.api.db_sources(remote=True)
        self.assertIsNone(sources[0]["installed"])
        self.assertTrue(sources[0]["update_available"])
        result = self.download()
        self.assertEqual((result["updated"], result["db"]), ("2026-10-25", f"{NAME}/{NAME}"))
        self.assertEqual(self.installed_version(), "2026-10-25")
        self.assertTrue((self.dbdir / NAME / "taxdb.btd").is_file())
        self.assertFalse((self.dbdir / refdb.STAGING).exists())
        st = self.api.download_status()
        self.assertEqual((st["running"], st["state"], st["installing"]), (False, "done", 0))
        self.assertFalse(self.api.db_sources(remote=True)[0]["update_available"])

        (self.dbdir / NAME / "extra_file").write_text("old")
        self.ncbi.publish("2026-11-20")
        self.assertTrue(self.api.db_sources(remote=True)[0]["update_available"])
        self.download()
        self.assertEqual(self.installed_version(), "2026-11-20")
        self.assertFalse((self.dbdir / NAME / "extra_file").exists())  # the folder was replaced

    def test_resume_partial_download(self):
        self.ncbi.publish("2026-10-25")
        staging = self.dbdir / refdb.STAGING / NAME
        staging.mkdir(parents=True)
        data = (self.ncbi.root / f"{NAME}.tar.gz").read_bytes()
        (staging / f"{NAME}.tar.gz.part").write_bytes(data[:100_000])
        self.download()
        self.assertIn((f"/{NAME}.tar.gz", "bytes=100000-"), RangeHandler.requests)
        self.assertEqual(self.installed_version(), "2026-10-25")

    def test_stale_partial_download_starts_over(self):
        self.ncbi.publish("2026-10-25")
        staging = self.dbdir / refdb.STAGING / NAME
        staging.mkdir(parents=True)
        (staging / f"{NAME}.tar.gz.part").write_bytes(b"x" * 50_000)  # from another release
        self.download()
        self.assertEqual(self.installed_version(), "2026-10-25")

    def test_checksum_mismatch_keeps_installed_version(self):
        self.ncbi.publish("2026-10-25")
        self.download()
        self.ncbi.publish("2026-11-20")
        (self.ncbi.root / f"{NAME}.tar.gz.md5").write_text("0" * 32 + f"  {NAME}.tar.gz\n")
        with self.assertRaisesRegex(refdb.DownloadError, "checksum mismatch"):
            self.download()
        self.assertEqual(self.installed_version(), "2026-10-25")
        st = self.api.download_status()
        self.assertEqual(st["state"], "error")
        self.assertIn("checksum", st["error"])

    def test_unsafe_archive_entry_refused(self):
        self.ncbi.publish("2026-10-25", db_files={"../evil.nsq": b"x", f"{NAME}.nin": b"2026-10-25"})
        with self.assertRaisesRegex(refdb.DownloadError, "unexpected entry"):
            self.download()
        self.assertFalse((self.tmp / "evil.nsq").exists())
        self.assertFalse((self.dbdir / NAME).exists())

    def test_unreadable_database_refused(self):
        self.ncbi.publish("2026-10-25", db_files={f"{NAME}.nsq": b"no index file"})
        with self.assertRaisesRegex(refdb.DownloadError, "cannot read"):
            self.download()
        self.assertFalse((self.dbdir / NAME).exists())

    def test_unreachable_server(self):
        self.api.set_setting("download_url", "http://127.0.0.1:9/")
        with self.assertRaisesRegex(refdb.DownloadError, "cannot reach"):
            self.download()
        src = self.api.db_sources(remote=True)[0]
        self.assertIsNone(src["remote"])
        self.assertIn("cannot reach", src["remote_error"])

    def test_cancel_removes_staging(self):
        self.ncbi.publish("2026-10-25", nsq_size=3_000_000)

        def cancel_when_downloading(row):
            if row["phase"] == "download" and (row["bytes_done"] or 0) > 0:
                conn = store.connect(self.env.db_path)
                conn.execute("UPDATE download SET cancel_request = 1 WHERE id = 1")
                conn.close()

        with mock.patch.object(refdb, "CHUNK", 64 * 1024), self.assertRaises(refdb.Cancelled):
            refdb.Downloader(self.env, SOURCE, cancel_when_downloading).run()
        self.assertEqual(self.api.download_status()["state"], "cancelled")
        self.assertFalse((self.dbdir / refdb.STAGING).exists())
        self.assertFalse((self.dbdir / NAME).exists())

    def test_second_download_refused(self):
        lock = WorkerLock(self.env.download_lock_path)
        self.assertTrue(lock.acquire())
        try:
            self.assertTrue(refdb.is_running(self.env))
            with self.assertRaises(refdb.AlreadyRunning):
                self.download()
        finally:
            lock.release()


class WorkerCoordinationTest(DownloadCase):
    def _set_worker(self, job_id, stage):
        conn = store.connect(self.env.db_path)
        conn.execute("UPDATE worker SET job_id = ?, stage = ? WHERE id = 1", (job_id, stage))
        conn.close()

    def test_install_waits_for_a_running_blast_stage(self):
        self.ncbi.publish("2026-10-25")
        self.download()
        self.ncbi.publish("2026-11-20")
        worker_lock = WorkerLock(self.env.lock_path)  # pretend a worker is running job 7's blast
        self.assertTrue(worker_lock.acquire())
        self._set_worker(7, "blast")
        errors = []
        t = threading.Thread(target=lambda: self._run_catching(errors))
        t.start()
        try:
            deadline = time.monotonic() + 20
            while self.api.download_status()["phase"] != "wait" and time.monotonic() < deadline:
                time.sleep(0.1)
            time.sleep(1.5)
            st = self.api.download_status()
            self.assertEqual((st["phase"], st["installing"]), ("wait", 1))
            self.assertIn("job 7", st["message"])
            self.assertEqual(self.installed_version(), "2026-10-25")
            self._set_worker(7, "rank")
        finally:
            t.join(timeout=30)
            worker_lock.release()
        self.assertEqual(errors, [])
        self.assertEqual(self.installed_version(), "2026-11-20")

    def _run_catching(self, errors):
        try:
            self.download()
        except Exception as exc:  # reported to the main thread
            errors.append(exc)

    def test_worker_waits_while_installing(self):
        w = Worker(self.env)
        w.conn = store.connect(self.env.db_path)
        self.addCleanup(w.conn.close)
        conn = store.connect(self.env.db_path)
        self.addCleanup(conn.close)
        conn.execute("UPDATE download SET installing = 1 WHERE id = 1")
        # flag set but no download process: a leftover flag is ignored
        self.assertFalse(w._install_pending())
        lock = WorkerLock(self.env.download_lock_path)
        self.assertTrue(lock.acquire())
        try:
            self.assertTrue(w._install_pending())
            job_id = self._job(conn)
            conn.execute("UPDATE jobs SET cancel_request = 1 WHERE id = ?", (job_id,))
            self.assertFalse(w._enter_stage(job_id, "blast"))  # waits, then gives up on the cancel
            self.assertTrue(w._enter_stage(job_id, "rank"))  # stages without the database do not wait
        finally:
            lock.release()
        self.assertTrue(w._enter_stage(job_id, "blast"))
        row = conn.execute("SELECT job_id, stage FROM worker WHERE id = 1").fetchone()
        self.assertEqual((row["job_id"], row["stage"]), (job_id, "blast"))

    def _job(self, conn):
        t = time.time()
        cur = conn.execute(
            "INSERT INTO jobs (name, platform, paired, status, created, updated) VALUES ('j', 'illumina', 0, 'running', ?, ?)",
            (t, t))
        return cur.lastrowid


class CliTest(DownloadCase):
    def test_check_download_status(self):
        self.ncbi.publish("2026-10-25")
        argv = ["--data-dir", str(self.env.data_dir), "dbs"]
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(cli_main(argv + ["check"]), 0)
            self.assertEqual(cli_main(argv + ["download"]), 0)
            self.assertEqual(cli_main(argv + ["download"]), 0)  # second time: up to date
            self.assertEqual(cli_main(argv + ["status"]), 0)
        text = out.getvalue()
        self.assertIn("installed: no", text)
        self.assertIn("update available", text)
        self.assertIn(f"jobs use it as: {NAME}/{NAME}", text)
        self.assertIn("is up to date (2026-10-25)", text)
        self.assertIn("done", text.splitlines()[-1])

    def test_background_download_reports_errors(self):
        self.api.set_setting("download_url", "http://127.0.0.1:9/")
        with warnings.catch_warnings():  # the detached child outlives its Popen object on purpose
            warnings.simplefilter("ignore", ResourceWarning)
            pid = self.api.start_download()
        self.assertGreater(pid, 0)
        deadline = time.monotonic() + 30
        while refdb.is_running(self.env) and time.monotonic() < deadline:
            time.sleep(0.2)
        st = self.api.download_status()
        self.assertEqual(st["state"], "error")
        self.assertIn("cannot reach", st["error"])


class StoreUpgradeTest(TempEnvCase):
    def test_version_1_database_gets_the_download_table(self):
        conn = store.connect(self.env.db_path)
        conn.execute("DROP TABLE download")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        conn.close()
        store.init_db(self.env.db_path)
        conn = store.connect(self.env.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM download").fetchone()[0], 1)
            version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(int(version), store.SCHEMA_VERSION)
        finally:
            conn.close()


@unittest.skipUnless(blast_db_available(), "bundled BLAST database or binaries missing")
class InstalledDatabaseTest(TempEnvCase):
    def test_metadata_of_the_bundled_database(self):
        info = describe_db(read_db_metadata(self.env.tool("blastdbcmd"), BLAST_DB))
        self.assertGreater(info["sequences"], 1000)
        self.assertRegex(info["updated"], r"^\d{4}-\d{2}-\d{2}$")


@unittest.skipUnless(os.environ.get("READSCREEN_NET") == "1", "set READSCREEN_NET=1 to read NCBI's metadata")
class NcbiMetadataTest(unittest.TestCase):
    def test_ncbi_metadata(self):
        info = refdb.remote_info("https://ftp.ncbi.nlm.nih.gov/blast/db/", SOURCE)
        self.assertRegex(info["updated"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual([a["file"] for a in info["archives"]], [f"{NAME}.tar.gz", "taxdb.tar.gz"])
        self.assertGreater(info["download_bytes"], 100_000_000)
