"""Real disposable workers prove the shared slot covers complete jobs."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.lock = self.root / "queue.lock"
        self.lock.touch(mode=0o600)
        repository = str(Path(__file__).resolve().parents[1])
        self.guardian = self.root / "guardian.py"
        self.guardian.write_text(
            "import os,sys,pathlib\n"
            f"sys.path.insert(0, {repository!r})\n"
            "from lib.github_runner_queue import hold_job_slot\n"
            f"pathlib.Path({str(self.root / 'guardian')!r}).write_text(str(os.getpid()))\n"
            "assert 'RUNNER_TRACKING_ID' not in os.environ\n"
            "raise SystemExit(hold_job_slot(*map(int, sys.argv[2:])))\n"
        )
        self.gate = self.root / "gate.py"
        self.gate.write_text(
            "import sys\n"
            f"sys.path.insert(0, {repository!r})\n"
            "from lib.github_runner_queue import acquire_job_slot\n"
            f"acquire_job_slot(int(sys.argv[1]), lock_path={str(self.lock)!r}, entry={str(self.guardian)!r})\n"
        )
        self.hook = self.root / "hook.sh"
        self.hook.write_text(f'#!/bin/bash\nset -e\n{sys.executable} -I {self.gate} "$PPID"\ntrue\n')
        self.worker = self.root / "worker.py"
        self.worker.write_text(
            "import json,os,pathlib,subprocess,sys,time\n"
            "root=pathlib.Path(sys.argv[1]); name=sys.argv[2]\n"
            "subprocess.run(['/bin/bash', str(root/'hook.sh')], check=True)\n"
            "(root/(name+'-start')).write_text(str(time.monotonic()))\n"
            "while not (root/(name+'-finish')).exists(): time.sleep(.02)\n"
            "(root/(name+'-end')).write_text(str(time.monotonic()))\n"
        )
        self.workers: list[subprocess.Popen] = []
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self) -> None:
        for worker in self.workers:
            if worker.poll() is None:
                worker.kill()
            worker.communicate(timeout=10)

    def start(self, name: str) -> subprocess.Popen:
        process = subprocess.Popen([sys.executable, str(self.worker), str(self.root), name],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "RUNNER_TRACKING_ID": "must-not-reach-guardian"})
        self.workers.append(process)
        return process

    def wait_for(self, name: str) -> None:
        deadline = time.monotonic() + 10
        while not (self.root / name).exists():
            if time.monotonic() > deadline:
                self.fail(f"Timed out waiting for {name}")
            time.sleep(.02)

    def finish(self, name: str, worker: subprocess.Popen) -> None:
        (self.root / (name + "-finish")).touch()
        output, errors = worker.communicate(timeout=10)
        self.assertEqual(worker.returncode, 0, (output, errors))

    def test_two_repositories_never_execute_steps_together(self) -> None:
        first = self.start("first")
        self.wait_for("first-start")
        second = self.start("second")
        time.sleep(.3)
        self.assertFalse((self.root / "second-start").exists())
        self.finish("first", first)
        self.wait_for("second-start")
        self.finish("second", second)
        self.assertLessEqual(float((self.root / "first-end").read_text()),
                             float((self.root / "second-start").read_text()))

    def test_worker_crash_releases_slot_and_cancelled_waiter_cannot_run(self) -> None:
        first = self.start("first")
        self.wait_for("first-start")
        cancelled = self.start("cancelled")
        time.sleep(.3)
        cancelled.kill()
        cancelled.communicate(timeout=10)
        second = self.start("second")
        first.kill()
        first.communicate(timeout=10)
        self.wait_for("second-start")
        self.finish("second", second)
        self.assertFalse((self.root / "cancelled-start").exists())

    def test_guardian_failure_kills_its_worker_before_unlocking(self) -> None:
        first = self.start("first")
        self.wait_for("first-start")
        os.kill(int((self.root / "guardian").read_text()), signal.SIGTERM)
        first.communicate(timeout=10)
        self.assertEqual(first.returncode, -signal.SIGKILL)
        second = self.start("second")
        self.wait_for("second-start")
        self.finish("second", second)

    def test_unsafe_or_missing_lock_prevents_workflow_steps(self) -> None:
        for kind in ("missing", "public", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                self.lock.unlink(missing_ok=True)
                if kind == "public":
                    self.lock.touch(mode=0o644)
                elif kind == "symlink":
                    self.lock.symlink_to(self.gate)
                elif kind == "hardlink":
                    os.link(self.gate, self.lock)
                process = self.start(kind)
                process.communicate(timeout=10)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse((self.root / (kind + "-start")).exists())
