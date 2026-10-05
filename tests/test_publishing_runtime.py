"""Bounded native processes, credential redaction, and scheduler ownership."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from lib.agent_tasks import AgentTasks
from lib.publishing_auth import PublishingAuth, executable
from lib.publishing_store import file_lock
from lib.publishing_worker import execute, work
from tests import test_publishing as fixtures


class PublishingRuntimeTests(unittest.TestCase):
    complete = fixtures.PublishingTests.complete
    approve = fixtures.PublishingTests.approve

    def setUp(self):
        fixtures.PublishingTests.setUp(self)

    def test_steam_launcher_resolves_native_script_root_and_name(self):
        installation = self.home / ".local/share/basaltwater/steamcmd"
        installation.mkdir(parents=True)
        script = installation / "steamcmd.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o700)
        launchers = self.home / ".local/bin"
        launchers.mkdir(parents=True)
        (launchers / "steamcmd").symlink_to(script)
        self.assertEqual(executable("steamcmd", self.home), str(script))

    def test_native_worker_discards_secrets_and_inherits_provider_lease(self):
        artifact = self.publisher.prepare("game")
        run = self.publisher.upload(artifact["id"])
        process = MagicMock()
        process.poll.side_effect = [None, 0]
        process.wait.return_value = 0
        process.pid = 12345
        selector = MagicMock()
        selector.get_map.return_value = {}
        selector.select.return_value = [(SimpleNamespace(fd=42, fileobj=process.stdout), 1)]
        with patch("lib.publishing_worker.subprocess.Popen") as popen, patch("lib.publishing_worker.selectors.DefaultSelector") as select, \
                patch("lib.publishing_worker.os.read", return_value=b'SECRET\n{"type":"result","value":{"buildId":42}}\n'), \
                patch("lib.publishing_worker.os.killpg"), patch.dict(os.environ, {"BUTLER_API_KEY": "SECRET", "LD_PRELOAD": "SECRET"}):
            popen.return_value.__enter__.return_value = process
            select.return_value.__enter__.return_value = selector
            result = execute(self.publisher, run, ["/fake/butler"], lease_fd=99)
        self.assertEqual(result["state"], "uploaded")
        self.assertEqual(result["receipt"], "42")
        self.assertNotIn("SECRET", str(result))
        self.assertEqual(popen.call_args.kwargs["pass_fds"], (99,))
        self.assertNotIn("LD_PRELOAD", popen.call_args.kwargs["env"])
        self.assertNotIn("BUTLER_API_KEY", popen.call_args.kwargs["env"])

    def test_cancelled_process_never_claims_safe_rollback(self):
        artifact = self.publisher.prepare("game")
        run = self.publisher.upload(artifact["id"])
        with self.publisher.store.transaction() as db:
            run["cancel_requested"] = True
            self.publisher.store.put(db, "runs", run)
        process = MagicMock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch("lib.publishing_worker.subprocess.Popen") as popen, patch("lib.publishing_worker.os.killpg") as kill, \
                patch("lib.publishing_worker.selectors.DefaultSelector"):
            popen.return_value.__enter__.return_value = process
            result = execute(self.publisher, run, ["/fake/butler"])
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["receipt"], "")
        self.assertTrue(kill.called)

    def test_auth_failure_pauses_all_provider_jobs(self):
        job = self.publisher.schedule("game", 60)
        artifact = self.publisher.prepare("game")
        self.publisher.upload(artifact["id"], job=job["id"])
        with patch("lib.publishing_worker.os.geteuid", return_value=1000), patch("lib.publishing_worker.upload_command", return_value=["/fake/butler"]), \
                patch("lib.publishing_worker.execute", return_value={"state": "unknown", "needs_login": True}):
            work(self.publisher)
        self.assertEqual(self.publisher.status()["jobs"][0]["state"], "paused")

    def test_failed_login_spawn_closes_pty_and_provider_lock(self):
        master, slave = os.pipe()
        auth = PublishingAuth(self.publisher)
        with patch("lib.publishing_auth.os.geteuid", return_value=1000), patch("lib.publishing_auth.executable", return_value="/fake/butler"), \
                patch("lib.publishing_auth.pty.openpty", return_value=(master, slave)), \
                patch("lib.publishing_auth.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, []]), \
                patch("lib.publishing_auth.termios.tcsetattr"), patch("lib.publishing_auth.subprocess.Popen", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                auth.begin("butler")
        for fd in (master, slave):
            with self.assertRaises(OSError):
                os.fstat(fd)
        with file_lock(self.publisher.store.root / "butler.lock"):
            pass

    def test_background_polling_does_not_release_lease_while_exiting(self):
        tasks = AgentTasks(str(self.home))
        tasks._thread = MagicMock()
        tasks._thread.is_alive.return_value = True
        fd = os.open(self.home / "lease", os.O_CREAT | os.O_RDWR, 0o600)
        tasks._lease = fd
        tasks.close()
        self.assertEqual(tasks._lease, fd)
        tasks._thread.is_alive.return_value = False
        tasks.close()
        self.assertIsNone(tasks._lease)
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_background_errors_never_expose_callback_secrets(self):
        tasks = AgentTasks(str(self.home), background_tick=MagicMock(side_effect=ValueError("SECRET")))
        tasks._stop = MagicMock()
        tasks._stop.is_set.side_effect = [False, True]
        tasks._background_loop()
        self.assertNotIn("SECRET", tasks.background_error)
        tasks._stop.wait.assert_called_once_with(timeout=15)
