"""Real local Linux INT/TERM injection; no SSH, guests, toolchain or builds."""
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import donor_wamrc as donor


JOB = r'''
import json, pathlib, signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
mask = [int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, set())]
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(nested_pid=child.pid, signal_mask=mask)))
try:
    child.wait()
finally:
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=5)
'''


def proc_state(pid):
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return None


class RunnerSpawnTests(unittest.TestCase):
    def exercise(self, signum):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            marker = root / "job-started.json"
            runner = donor.Runner(root)
            unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            real_popen = subprocess.Popen
            jobs = []
            nested = None
            old_term = signal.getsignal(signal.SIGTERM)
            old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            def interrupted(signum, frame):
                raise KeyboardInterrupt("injected external SIGTERM")
            signal.signal(signal.SIGTERM, interrupted)
            def spawn_and_interrupt(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                jobs.append(child)
                # Barrier makes a real nested job exist before the signal
                # arrives immediately after Popen, before Runner assignment.
                deadline = time.monotonic() + 5
                while not marker.exists():
                    if time.monotonic() > deadline:
                        raise AssertionError("job did not publish test barrier")
                    time.sleep(0.01)
                blocked = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                self.assertIn(signal.SIGINT, blocked)
                self.assertIn(signal.SIGTERM, blocked)
                os.kill(os.getpid(), signum)
                return child
            try:
                with patch.object(runner, "sample"), patch.object(donor.subprocess, "Popen", side_effect=spawn_and_interrupt), self.assertRaises(KeyboardInterrupt):
                    runner.run("real-local-job", [sys.executable, "-c", JOB, str(marker)])
                self.assertEqual(len(jobs), 1)
                self.assertIsNotNone(jobs[0].poll(), "Runner leaked its job leader")
                receipt = json.loads(marker.read_text())
                nested = receipt["nested_pid"]
                deadline = time.monotonic() + 5
                while proc_state(nested) not in (None, "Z") and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIn(proc_state(nested), (None, "Z"), "Runner leaked its nested job")
                self.assertIsNone(unrelated.poll(), "Runner signalled an unrelated process")
                self.assertNotIn(int(signal.SIGINT), receipt["signal_mask"])
                self.assertNotIn(int(signal.SIGTERM), receipt["signal_mask"])
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), old_mask)
            finally:
                signal.signal(signal.SIGTERM, old_term)
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
                for child in jobs + [unrelated]:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)

    def test_sigint_after_spawn_before_assignment_cleans_owned_job_and_nested_child(self):
        self.exercise(signal.SIGINT)

    def test_sigterm_after_spawn_before_assignment_cleans_owned_job_and_nested_child(self):
        self.exercise(signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
