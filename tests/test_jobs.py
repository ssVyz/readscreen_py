"""Milestone 1.4: job/stage model (no binaries involved)."""

import shutil
import unittest

from readscreen import jobs, layout, store
from readscreen.api import ApiError

from .helpers import TempEnvCase, write_fasta, write_fastq_gz


class JobCase(TempEnvCase):
    def setUp(self):
        super().setUp()
        self.r1 = write_fastq_gz(self.tmp / "s_R1.fastq.gz", ["ACGT" * 30] * 5)
        self.r2 = write_fastq_gz(self.tmp / "s_R2.fastq.gz", ["TGCA" * 30] * 5, mate=2)

    def create(self, **kw):
        kw.setdefault("platform", "illumina")
        kw.setdefault("r1", str(self.r1))
        kw.setdefault("r2", str(self.r2))
        params = kw.pop("params", {})
        params.setdefault("blast", {}).setdefault("db", "dummy")
        return self.api.create_job(params=params, **kw)

    def db(self):
        return store.connect(self.env.db_path)

    def fake_done(self, job_id, upto):
        """Mark stages 1..upto as done with a folder each, plus ranking and result rows."""
        conn = self.db()
        try:
            for name in layout.STAGE_NAMES[: layout.stage_index(upto)]:
                d = layout.stage_dir(self.env, job_id, name)
                d.mkdir(parents=True, exist_ok=True)
                (d / "out.txt").write_text(name)
                conn.execute("UPDATE stages SET state = 'done', summary = '{}' WHERE job_id = ? AND name = ?",
                             (job_id, name))
            conn.execute("INSERT INTO ranking (job_id, ref, grp, leader, score, queries, ref_cov, identity, length,"
                         " weak, auto_selected, selected) VALUES (?, 'NC_1.1', 1, 1, 500, 3, 0.5, 90, 1000, 0, 1, 1)",
                         (job_id,))
            conn.execute("INSERT INTO results (job_id, ref, data) VALUES (?, 'NC_1.1', '{}')", (job_id,))
            conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))
        finally:
            conn.close()


class CreateTest(JobCase):
    def test_create_defaults(self):
        job = self.create()
        self.assertEqual(job["name"], "s")
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["paired"])
        self.assertEqual([s["name"] for s in job["stages"]], list(layout.STAGE_NAMES))
        self.assertEqual(job["current_stage"], "input")
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["assemble"]["params"]["mode"], "assemble")
        self.assertEqual(st["map"]["params"]["sensitivity"], "sensitive")
        self.assertTrue(layout.job_dir(self.env, job["id"]).is_dir())

    def test_long_read_defaults_and_settings(self):
        self.api.set_setting("default.map.sensitivity", "normal")
        job = self.create(platform="ont", r2=None)
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["assemble"]["params"]["mode"], "skip")
        self.assertEqual(st["map"]["params"]["sensitivity"], "normal")

    def test_create_errors(self):
        cases = [
            dict(platform="pacbio"),
            dict(r1=str(self.tmp / "missing.fastq.gz")),
            dict(platform="ont"),  # r2 with long reads
            dict(params={"map": {"nope": 1}}),
            dict(params={"assemble": {"k": 30}}),
            dict(params={"assemble": {"mode": "external"}}),
            dict(params={"nostage": {}}),
            dict(r2=str(self.r1)),
            dict(pauses=["nostage"]),
        ]
        for kw in cases:
            with self.subTest(kw=kw), self.assertRaises(ApiError):
                self.create(**kw)
        self.assertEqual(self.api.list_jobs(), [])

    def test_external_assembly(self):
        asm = write_fasta(self.tmp / "asm.fa", [("c1", "ACGT" * 100)])
        job = self.create(assembly=str(asm))
        st = {s["name"]: s for s in job["stages"]}
        self.assertEqual(st["assemble"]["params"]["mode"], "external")
        self.assertTrue((layout.imports_dir(self.env, job["id"]) / "assembly.fa").is_file())
        with self.assertRaises(ApiError):
            self.create(assembly=str(self.r1))  # not FASTA


class ParamsAndResetTest(JobCase):
    def test_set_params_pending(self):
        job = self.create()
        p = self.api.set_params(job["id"], "map", {"sensitivity": "normal"})
        self.assertEqual(p["sensitivity"], "normal")
        with self.assertRaises(ApiError):
            self.api.set_params(job["id"], "map", {"sensitivity": "extreme"})
        with self.assertRaises(ApiError):
            self.api.set_params(job["id"], "select", {"confirmed": True})

    def test_done_stage_needs_reset(self):
        job = self.create()
        self.fake_done(job["id"], "report")
        with self.assertRaises(ApiError) as cm:
            self.api.set_params(job["id"], "blast", {"evalue": 1e-5})
        self.assertEqual(cm.exception.code, "conflict")
        self.api.set_params(job["id"], "blast", {"evalue": 1e-5}, reset=True)
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["current_stage"], "blast")
        self.assertTrue(layout.stage_dir(self.env, job["id"], "assemble").is_dir())
        self.assertFalse(layout.stage_dir(self.env, job["id"], "blast").exists())
        self.assertFalse(layout.stage_dir(self.env, job["id"], "report").exists())
        self.assertEqual(self.api.get_ranking(job["id"]), [])
        # unchanged value: no reset needed
        self.fake_done(job["id"], "report")
        self.api.set_params(job["id"], "blast", {"evalue": 1e-5})

    def test_reset_keeps_earlier(self):
        job = self.create()
        self.fake_done(job["id"], "report")
        self.api.reset_job(job["id"], "map", queue=True)
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["current_stage"], "map")
        self.assertEqual(len(self.api.get_ranking(job["id"])), 1)  # rank stays
        self.assertFalse(self.api.get_results(job["id"])["references"])
        self.assertTrue(layout.stage_dir(self.env, job["id"], "extract").is_dir())
        self.assertFalse(layout.stage_dir(self.env, job["id"], "map").exists())


class TransitionsTest(JobCase):
    def test_cancel_queue_continue(self):
        job = self.create()
        jid = job["id"]
        self.assertEqual(self.api.cancel_job(jid), "stopped")
        self.assertEqual(self.api.get_job(jid)["status"], "stopped")
        self.api.queue_job(jid)
        self.assertEqual(self.api.get_job(jid)["status"], "queued")
        with self.assertRaises(ApiError):
            self.api.continue_job(jid)  # not paused
        conn = self.db()
        conn.execute("UPDATE jobs SET status = 'paused', pause_reason = 'select' WHERE id = ?", (jid,))
        conn.close()
        self.api.continue_job(jid)
        job = self.api.get_job(jid)
        self.assertEqual(job["status"], "queued")
        st = {s["name"]: s for s in job["stages"]}
        self.assertTrue(st["select"]["params"]["confirmed"])

    def test_queue_done_job_refused(self):
        job = self.create()
        self.fake_done(job["id"], "report")
        with self.assertRaises(ApiError):
            self.api.queue_job(job["id"])

    def test_cancel_running_without_worker(self):
        job = self.create()
        conn = self.db()
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))
        conn.close()
        tmp = layout.stage_tmp(self.env, job["id"], "trim")
        tmp.mkdir(parents=True)
        with self.assertRaises(ApiError):
            self.api.reset_job(job["id"], "trim")
        self.assertEqual(self.api.cancel_job(job["id"]), "stopped")
        self.assertFalse(tmp.exists())

    def test_delete(self):
        job = self.create()
        d = layout.job_dir(self.env, job["id"])
        (d / "big.bin").write_bytes(b"x" * 1000)
        self.assertEqual(self.api.delete_job(job["id"]), "deleted")
        self.assertFalse(d.exists())
        self.assertTrue(self.r1.exists())
        with self.assertRaises(ApiError) as cm:
            self.api.get_job(job["id"])
        self.assertEqual(cm.exception.code, "not_found")

    def test_rmtree_guard(self):
        with self.assertRaises(RuntimeError):
            layout.safe_rmtree(self.env, self.tmp)
        with self.assertRaises(RuntimeError):
            layout.safe_rmtree(self.env, self.env.jobs_dir)
        with self.assertRaises(RuntimeError):
            layout.safe_rmtree(self.env, self.env.jobs_dir / ".." / "db")

    def test_recover(self):
        job = self.create()
        conn = self.db()
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job["id"],))
        tmp = layout.stage_tmp(self.env, job["id"], "trim")
        tmp.mkdir(parents=True)
        self.assertEqual(jobs.recover(conn, self.env), [job["id"]])
        conn.close()
        self.assertEqual(self.api.get_job(job["id"])["status"], "queued")
        self.assertFalse(tmp.exists())


class ReassignTest(JobCase):
    def test_moved_file_keeps_results(self):
        job = self.create()
        self.fake_done(job["id"], "report")
        moved = self.tmp / "moved"
        moved.mkdir()
        shutil.copyfile(self.r1, moved / "s_R1.fastq.gz")
        res = self.api.reassign_inputs(job["id"], r1=str(moved / "s_R1.fastq.gz"))
        self.assertIsNone(res["reset_to"])
        job = self.api.get_job(job["id"])
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["inputs"]["r1"]["path"].endswith("moved\\s_R1.fastq.gz") or
                        job["inputs"]["r1"]["path"].endswith("moved/s_R1.fastq.gz"))

    def test_different_file_needs_force(self):
        job = self.create()
        self.fake_done(job["id"], "report")
        other = write_fastq_gz(self.tmp / "other_R1.fastq.gz", ["GGGG" * 30] * 7)
        with self.assertRaises(ApiError):
            self.api.reassign_inputs(job["id"], r1=str(other))
        res = self.api.reassign_inputs(job["id"], r1=str(other), force=True)
        self.assertEqual(res["reset_to"], "input")
        self.assertEqual(self.api.get_job(job["id"])["current_stage"], "input")

    def test_single_end_cannot_gain_r2(self):
        job = self.create(r2=None)
        with self.assertRaises(ApiError):
            self.api.reassign_inputs(job["id"], r2=str(self.r2))


class SelectionTest(JobCase):
    def test_selection_rules(self):
        job = self.create()
        with self.assertRaises(ApiError):
            self.api.set_selection(job["id"], [])  # not ranked yet
        self.fake_done(job["id"], "rank")
        with self.assertRaises(ApiError):
            self.api.set_selection(job["id"], ["NC_9.9"])
        self.assertEqual(self.api.set_selection(job["id"], []), 0)
        self.assertFalse(self.api.get_ranking(job["id"])[0]["selected"])
        self.assertEqual(self.api.set_selection(job["id"], ["NC_1.1"]), 1)


if __name__ == "__main__":
    unittest.main()
