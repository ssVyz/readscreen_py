"""Milestone 1.5: the worker with fake stages (no pipeline binaries) and a detached start via the CLI."""

import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace

from readscreen import layout, store, worker
from readscreen.paths import ROOT
from readscreen.stages import registry
from readscreen.stages.common import StageError
from readscreen.worker import AlreadyRunning, Worker

from .helpers import TempEnvCase, write_fastq_gz

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def fake_stages(**overrides):
    """Real parameter schemas and skip rules, fake work (the real 'select' stage is kept)."""
    out = {}
    for name, mod in registry().items():
        def run(ctx, _name=name):
            (ctx.out_dir / "out.txt").write_text(_name)
            return {"ok": _name}

        out[name] = SimpleNamespace(PARAMS=mod.PARAMS, is_skipped=getattr(mod, "is_skipped", lambda p: False),
                                    run=mod.run if name == "select" else run)
    for name, fn in overrides.items():
        out[name] = SimpleNamespace(PARAMS=out[name].PARAMS, is_skipped=out[name].is_skipped, run=fn)
    return out


def slow_tool(ctx):
    ctx.run_tool(SLEEP)
    return {}


class WorkerCase(TempEnvCase):
    def setUp(self):
        super().setUp()
        self.r1 = write_fastq_gz(self.tmp / "s_R1.fastq.gz", ["ACGT" * 30] * 5)
        self.threads = []

    def tearDown(self):
        worker.request_stop(self.env, now=True)
        for t in self.threads:
            t.join(timeout=20)
        super().tearDown()

    def create(self, **kw):
        params = kw.pop("params", {})
        params.setdefault("blast", {}).setdefault("db", "dummy")
        return self.api.create_job(platform="illumina", r1=str(self.r1), params=params, **kw)["id"]

    def run_worker(self, stages, **kw):
        kw.setdefault("exit_when_empty", True)
        Worker(self.env, poll=0.05, stages=stages, **kw).run()

    def start_worker(self, stages, **kw):
        kw.setdefault("exit_when_empty", True)
        w = Worker(self.env, poll=0.05, stages=stages, **kw)
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        self.threads.append(t)
        return t

    def wait_for(self, cond, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.05)
        self.fail("condition not reached in time")

    def running_stage(self):
        return self.api.worker_status().get("stage")

    def states(self, job_id):
        return {s["name"]: s["state"] for s in self.api.get_job(job_id)["stages"]}


class FlowTest(WorkerCase):
    def test_all_stages(self):
        jid = self.create(params={"analyze": {"skip": True}})
        self.run_worker(fake_stages())
        job = self.api.get_job(jid)
        self.assertEqual(job["status"], "done")
        st = self.states(jid)
        self.assertEqual(st["analyze"], "skipped")
        self.assertTrue(all(v == "done" for k, v in st.items() if k != "analyze"))
        self.assertFalse(layout.stage_dir(self.env, jid, "analyze").exists())
        self.assertEqual((layout.stage_dir(self.env, jid, "blast") / "out.txt").read_text(), "blast")
        self.assertFalse(list(layout.job_dir(self.env, jid).glob("*.tmp")))
        self.assertFalse(self.api.worker_status()["running"])

    def test_queue_order(self):
        a, b = self.create(), self.create()
        order = []

        def record(ctx):
            order.append(ctx.job_id)
            return {}

        self.run_worker(fake_stages(input=record))
        self.assertEqual(order, [a, b])

    def test_pause_after_and_continue(self):
        jid = self.create(pauses=["blast"])
        self.run_worker(fake_stages())
        job = self.api.get_job(jid)
        self.assertEqual((job["status"], job["pause_reason"], job["current_stage"]), ("paused", "after:blast", "rank"))
        self.api.continue_job(jid)
        self.run_worker(fake_stages())
        self.assertEqual(self.api.get_job(jid)["status"], "done")

    def test_manual_selection_pause(self):
        jid = self.create(params={"select": {"mode": "manual"}})
        other = self.create()
        self.run_worker(fake_stages())
        job = self.api.get_job(jid)
        self.assertEqual((job["status"], job["pause_reason"], job["current_stage"]), ("paused", "select", "select"))
        self.assertEqual(self.api.get_job(other)["status"], "done")  # the paused job did not block the queue
        self.api.continue_job(jid)
        self.run_worker(fake_stages())
        self.assertEqual(self.api.get_job(jid)["status"], "done")

    def test_error_then_requeue(self):
        def broken(ctx):
            (ctx.out_dir / "partial").write_text("x")
            raise StageError("input file missing: /nowhere")

        jid = self.create()
        self.run_worker(fake_stages(blast=broken))
        job = self.api.get_job(jid)
        self.assertEqual(job["status"], "error")
        self.assertIn("blast: input file missing", job["error"])
        self.assertFalse(layout.stage_tmp(self.env, jid, "blast").exists())
        self.assertIn("input file missing", self.api.stage_log(jid, "blast"))
        self.api.queue_job(jid)
        self.run_worker(fake_stages())
        self.assertEqual(self.api.get_job(jid)["status"], "done")

    def test_internal_error_keeps_worker_alive(self):
        def bug(ctx):
            raise KeyError("oops")

        a = self.create()
        b = self.create()
        self.run_worker(fake_stages(map=lambda ctx: bug(ctx) if ctx.job_id == a else {}))
        self.assertEqual(self.api.get_job(a)["status"], "error")
        self.assertIn("internal error", self.api.get_job(a)["error"])
        self.assertEqual(self.api.get_job(b)["status"], "done")


class StopCancelTest(WorkerCase):
    def test_cancel_running_job(self):
        jid = self.create()
        other = self.create()
        t = self.start_worker(fake_stages(trim=lambda ctx: slow_tool(ctx) if ctx.job_id == jid else {}))
        self.wait_for(lambda: self.running_stage() == "trim" and self.api.worker_status()["tool_pid"])
        self.assertEqual(self.api.cancel_job(jid), "requested")
        t.join(timeout=20)
        self.assertFalse(t.is_alive())
        job = self.api.get_job(jid)
        self.assertEqual((job["status"], job["current_stage"]), ("stopped", "trim"))
        self.assertFalse(layout.stage_tmp(self.env, jid, "trim").exists())
        self.assertEqual(self.api.get_job(other)["status"], "done")

    def test_stop_now(self):
        jid = self.create()
        t = self.start_worker(fake_stages(trim=slow_tool), exit_when_empty=False)
        self.wait_for(lambda: self.running_stage() == "trim" and self.api.worker_status()["tool_pid"])
        tool_pid = self.api.worker_status()["tool_pid"]
        self.assertEqual(self.api.stop_worker(now=True, wait=15), "stopped")
        t.join(timeout=5)
        job = self.api.get_job(jid)
        self.assertEqual((job["status"], job["current_stage"]), ("queued", "trim"))
        from readscreen.runner import pid_alive

        self.wait_for(lambda: not pid_alive(tool_pid), timeout=5)

    def test_graceful_stop_finishes_stage(self):
        def slowish(ctx):
            time.sleep(1.0)
            return {"slow": True}

        jid = self.create()
        t = self.start_worker(fake_stages(trim=slowish), exit_when_empty=False)
        self.wait_for(lambda: self.running_stage() == "trim")
        self.assertEqual(self.api.stop_worker(), "requested")
        t.join(timeout=20)
        job = self.api.get_job(jid)
        self.assertEqual((job["status"], job["current_stage"]), ("queued", "assemble"))
        self.assertEqual(self.states(jid)["trim"], "done")

    def test_delete_running_job(self):
        jid = self.create()
        t = self.start_worker(fake_stages(trim=slow_tool))
        self.wait_for(lambda: self.running_stage() == "trim" and self.api.worker_status()["tool_pid"])
        self.assertEqual(self.api.delete_job(jid), "requested")
        t.join(timeout=20)
        self.assertEqual(self.api.list_jobs(), [])
        self.assertFalse(layout.job_dir(self.env, jid).exists())

    def test_single_worker(self):
        self.create()
        self.start_worker(fake_stages(trim=slow_tool), exit_when_empty=False)
        self.wait_for(lambda: self.api.worker_status()["running"])
        with self.assertRaises(AlreadyRunning):
            Worker(self.env, stages=fake_stages()).run()

    def test_crash_recovery(self):
        jid = self.create()
        conn = store.connect(self.env.db_path)
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (jid,))
        conn.execute("UPDATE stages SET state = 'done' WHERE job_id = ? AND idx <= 2", (jid,))
        conn.close()
        tmp = layout.stage_tmp(self.env, jid, "trim")
        tmp.mkdir(parents=True)
        (tmp / "half.fastq.gz").write_bytes(b"partial")
        self.run_worker(fake_stages())
        self.assertEqual(self.api.get_job(jid)["status"], "done")
        self.assertFalse(tmp.exists())


class DetachedTest(TempEnvCase):
    def cli(self, *args, timeout=60):
        return subprocess.run([sys.executable, str(ROOT / "main.py"), "--data-dir", str(self.env.data_dir), *args],
                              capture_output=True, text=True, timeout=timeout)

    def test_start_status_stop(self):
        out = self.cli("worker", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("worker started", out.stdout)
        try:
            self.assertIn("running", self.cli("worker", "status").stdout)
            again = self.cli("worker", "start")
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already running", again.stderr)
        finally:
            out = self.cli("worker", "stop", "--wait", "20")
        self.assertEqual(out.stdout.strip(), "stopped")
        self.assertIn("not running", self.cli("worker", "status").stdout)


if __name__ == "__main__":
    unittest.main()
