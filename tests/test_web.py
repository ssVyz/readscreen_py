"""Milestone 2.1: HTTP server and JSON API (urllib against a server on a random port)."""

import json
import threading
import urllib.error
import urllib.request

from readscreen import store
from readscreen.web.server import make_server

from .helpers import TempEnvCase, write_fasta, write_fastq_gz


class WebCase(TempEnvCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.api, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.r1 = write_fastq_gz(self.tmp / "s_R1.fastq.gz", ["ACGT" * 30] * 5)
        self.r2 = write_fastq_gz(self.tmp / "s_R2.fastq.gz", ["TGCA" * 30] * 5, mate=2)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                content = resp.read()
                return resp.status, (content if raw else json.loads(content)), resp.headers
        except urllib.error.HTTPError as exc:
            with exc:
                content = exc.read()
            return exc.code, (content if raw else json.loads(content)), exc.headers

    def create(self, **kw):
        body = {"platform": "illumina", "r1": str(self.r1), "r2": str(self.r2),
                "params": {"blast": {"db": "dummy"}}, **kw}
        status, job, _ = self.call("POST", "/api/jobs", body)
        self.assertEqual(status, 200, job)
        return job


class ApiRoutesTest(WebCase):
    def test_info_stages_settings(self):
        status, info, _ = self.call("GET", "/api/info")
        self.assertEqual(status, 200)
        self.assertIn("illumina", info["platforms"])
        _, stages, _ = self.call("GET", "/api/stages?platform=ont")
        assemble = next(s for s in stages if s["name"] == "assemble")
        self.assertEqual(next(p for p in assemble["params"] if p["name"] == "mode")["default"], "skip")
        status, values, _ = self.call("PUT", "/api/settings", {"threads": 3, "default.map.sensitivity": "normal"})
        self.assertEqual((status, values["threads"], values["default.map.sensitivity"]), (200, 3, "normal"))
        status, err, _ = self.call("PUT", "/api/settings", {"threads": 0})
        self.assertEqual((status, err["code"]), (400, "invalid"))

    def test_job_lifecycle(self):
        job = self.create(name="web job", pauses=["blast"])
        jid = job["id"]
        _, jobs, _ = self.call("GET", "/api/jobs")
        self.assertEqual([j["name"] for j in jobs], ["web job"])
        status, job, _ = self.call("PATCH", f"/api/jobs/{jid}", {"notes": "hello"})
        self.assertEqual((status, job["notes"]), (200, "hello"))
        status, params, _ = self.call("PUT", f"/api/jobs/{jid}/params/map", {"updates": {"sensitivity": "normal"}})
        self.assertEqual((status, params["sensitivity"]), (200, "normal"))
        status, job, _ = self.call("PUT", f"/api/jobs/{jid}/pause/rank", {"on": True})
        self.assertTrue(next(s for s in job["stages"] if s["name"] == "rank")["pause_after"])
        status, res, _ = self.call("POST", f"/api/jobs/{jid}/cancel")
        self.assertEqual((status, res["result"]), (200, "stopped"))
        status, job, _ = self.call("POST", f"/api/jobs/{jid}/queue")
        self.assertEqual(job["status"], "queued")
        status, err, _ = self.call("POST", f"/api/jobs/{jid}/continue")
        self.assertEqual((status, err["code"]), (409, "conflict"))
        status, job, _ = self.call("POST", f"/api/jobs/{jid}/reset", {"stage": "trim"})
        self.assertEqual(job["status"], "stopped")
        status, log, _ = self.call("GET", f"/api/jobs/{jid}/log/trim")
        self.assertEqual((status, log["text"]), (200, ""))
        status, res, _ = self.call("DELETE", f"/api/jobs/{jid}")
        self.assertEqual(res["result"], "deleted")
        status, err, _ = self.call("GET", f"/api/jobs/{jid}")
        self.assertEqual(status, 404)

    def test_create_errors(self):
        status, err, _ = self.call("POST", "/api/jobs", {"platform": "illumina", "r1": str(self.tmp / "nope.fq")})
        self.assertEqual(status, 400)
        self.assertIn("not found", err["error"])
        status, err, _ = self.call("POST", "/api/jobs", [1, 2])
        self.assertEqual(status, 400)

    def test_ranking_selection_results_export(self):
        jid = self.create()["id"]
        conn = store.connect(self.env.db_path)
        conn.execute("UPDATE stages SET state = 'done' WHERE job_id = ? AND idx <= 6", (jid,))
        conn.execute("UPDATE jobs SET status = 'paused', pause_reason = 'select' WHERE id = ?", (jid,))
        conn.execute("INSERT INTO ranking (job_id, ref, grp, leader, score, queries, ref_cov, identity, length,"
                     " weak, auto_selected, selected) VALUES (?, 'NC_1.1', 1, 1, 500, 3, 0.5, 90, 1000, 0, 1, 1)", (jid,))
        conn.execute("INSERT INTO results (job_id, ref, data) VALUES (?, 'NC_1.1', ?)",
                     (jid, json.dumps({"ref": "NC_1.1", "reads": 5, "breadth": 0.5, "bins": {"count": 1}})))
        conn.close()
        _, ranking, _ = self.call("GET", f"/api/jobs/{jid}/ranking")
        self.assertEqual((ranking[0]["ref"], ranking[0]["group"], ranking[0]["selected"]), ("NC_1.1", 1, True))
        status, res, _ = self.call("PUT", f"/api/jobs/{jid}/selection", {"refs": [], "continue": True})
        self.assertEqual((status, res["selected"]), (200, 0))
        _, job, _ = self.call("GET", f"/api/jobs/{jid}")
        self.assertEqual(job["status"], "queued")
        _, res, _ = self.call("GET", f"/api/jobs/{jid}/results")
        self.assertNotIn("bins", res["references"][0])
        _, one, _ = self.call("GET", f"/api/jobs/{jid}/results/NC_1.1")
        self.assertEqual(one["bins"], {"count": 1})
        status, err, _ = self.call("GET", f"/api/jobs/{jid}/results/NC_9.9")
        self.assertEqual(status, 404)
        status, body, headers = self.call("GET", f"/api/jobs/{jid}/export?format=tsv", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(body.decode().startswith("ref\t"))

    def test_fs(self):
        (self.tmp / "sub").mkdir()
        write_fasta(self.tmp / "asm.fa", [("c", "ACGT")])
        _, listing, _ = self.call("GET", "/api/fs/list?path=" + urllib.request.quote(str(self.tmp)))
        names = [e["name"] for e in listing["entries"]]
        self.assertEqual(names[0], "data")  # folders first
        kinds = {e["name"]: e["kind"] for e in listing["entries"]}
        self.assertEqual((kinds["s_R1.fastq.gz"], kinds["asm.fa"]), ("fastq", "fasta"))
        self.assertEqual(listing["parent"], str(self.tmp.parent))
        _, roots, _ = self.call("GET", "/api/fs/list")
        self.assertTrue(roots["entries"])
        status, _, _ = self.call("GET", "/api/fs/list?path=relative/path")
        self.assertEqual(status, 400)
        _, sug, _ = self.call("GET", "/api/fs/suggest?path=" + urllib.request.quote(str(self.r2)))
        self.assertEqual((sug["role"], sug["mate"], sug["name"]), ("r2", str(self.r1), "s"))

    def test_worker_status(self):
        status, st, _ = self.call("GET", "/api/worker")
        self.assertEqual((status, st["running"]), (200, False))
        _, res, _ = self.call("POST", "/api/worker/stop")
        self.assertEqual(res["result"], "not_running")

    def test_download_routes(self):
        self.call("GET", "/api/dbs")  # fills the server's database list cache
        self.call("PUT", "/api/settings", {"blast_db_dir": str(self.tmp / "blast_db")})
        self.assertEqual(self.call("GET", "/api/dbs")[1], [])  # saving settings clears the cache
        status, sources, _ = self.call("GET", "/api/dbs/sources")
        self.assertEqual(status, 200)
        self.assertEqual((sources[0]["name"], sources[0]["installed"]), ("ref_viruses_rep_genomes", None))
        self.assertNotIn("remote", sources[0])  # NCBI is only asked with ?remote=1
        status, st, _ = self.call("GET", "/api/dbs/download")
        self.assertEqual((status, st["running"], st["state"]), (200, False, None))
        _, res, _ = self.call("POST", "/api/dbs/download/cancel")
        self.assertEqual(res, {"cancelled": False})
        status, err, _ = self.call("POST", "/api/dbs/download", {"name": "nt"})
        self.assertEqual((status, err["code"]), (400, "invalid"))

    def test_unknown_and_wrong_method(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/jobs")[0], 405)


class StaticTest(WebCase):
    def test_index_and_types(self):
        status, body, headers = self.call("GET", "/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html>", body.lower())
        status, _, headers = self.call("GET", "/js/app.js", raw=True)
        self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))

    def test_no_traversal(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/..%5cserver.py", "/js/../../server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path, raw=True)[0], 404)


if __name__ == "__main__":
    import unittest

    unittest.main()
