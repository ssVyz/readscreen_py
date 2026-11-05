"""Milestone 1.3: the subprocess runner (logs, errors, cancellation, process-tree kill)."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

from readscreen import runner

# a parent that starts a sleeping child and prints the child's PID
PARENT = (
    "import subprocess, sys, time;"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "print(c.pid, flush=True); time.sleep(60)"
)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.log = self.tmp / "x.log"

    def tearDown(self):
        self._tmp.cleanup()

    def test_success_and_stdout_capture(self):
        out = self.tmp / "out.txt"
        runner.run([sys.executable, "-c", "import sys; print('hello'); print('err', file=sys.stderr)"],
                   log_path=self.log, stdout_path=out)
        self.assertEqual(out.read_text().strip(), "hello")
        log = self.log.read_text()
        self.assertIn("err", log)
        self.assertIn("$ ", log)

    def test_failure(self):
        with self.assertRaises(runner.ToolError) as cm:
            runner.run([sys.executable, "-c", "import sys; print('bad input', file=sys.stderr); sys.exit(3)"],
                       log_path=self.log)
        self.assertIn("exit code 3", str(cm.exception))
        self.assertIn("bad input", str(cm.exception))

    def test_cancel_kills_tree(self):
        out = self.tmp / "pid.txt"
        started = []
        t0 = time.monotonic()

        def cancel():
            return out.exists() and out.read_text().strip() != "" and time.monotonic() - t0 > 1

        with self.assertRaises(runner.Cancelled):
            runner.run([sys.executable, "-c", PARENT], log_path=self.log, stdout_path=out,
                       cancel_check=cancel, on_start=started.append, poll=0.1)
        self.assertLess(time.monotonic() - t0, 30)
        parent_pid = started[0]
        child_pid = int(out.read_text().strip())
        self.assertEqual(started[-1], None)
        for _ in range(50):
            if not runner.pid_alive(child_pid) and not runner.pid_alive(parent_pid):
                break
            time.sleep(0.1)
        self.assertFalse(runner.pid_alive(parent_pid))
        self.assertFalse(runner.pid_alive(child_pid))

    def test_pid_alive(self):
        import os

        self.assertTrue(runner.pid_alive(os.getpid()))
        self.assertFalse(runner.pid_alive(None))
        self.assertFalse(runner.pid_alive(999999))


if __name__ == "__main__":
    unittest.main()
