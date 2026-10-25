"""Browser launch and recovery contracts without a real graphical session."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop import kiosk_runtime as runtime


class KioskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(runtime, "HOME", self.home))
        self.enterContext(patch.object(runtime, "RUNTIME_ROOT", self.home / "run"))
        self.enterContext(patch.object(runtime.pwd, "getpwnam", return_value=SimpleNamespace(
            pw_uid=12345, pw_dir=str(self.home), pw_gecos=runtime.ACCOUNT_COMMENT, pw_shell="/usr/sbin/nologin")))

    def test_browser_uses_normal_navigation_and_literal_url(self):
        url = "https://pve.example:8006/a'b?x=$(id)&y=`id`#v1"
        command = runtime.browser_command(url)
        self.assertEqual(command[-1], url)
        self.assertEqual(command[:6], ["/usr/bin/cage", "-s", "-m", "last", "--", "/usr/bin/firefox-esr"])
        self.assertNotIn("--kiosk", command)
        self.assertEqual(command[command.index("--profile") + 1], str(self.home / "profile"))
        self.assertEqual(runtime.browser_command(url, start_page=True)[-1], (self.home / "start.html").as_uri())
        with self.assertRaises(ValueError):
            runtime.browser_command("file:///etc/passwd")

    def test_environment_keeps_pam_runtime_and_dbus_but_selects_local_seat(self):
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/123", "DBUS_SESSION_BUS_ADDRESS": "unix:path=test",
                                     "DISPLAY": ":10", "WAYLAND_DISPLAY": "wayland-1", "GREETD_SOCK": "socket"}, clear=True):
            environment = runtime.session_environment()
        self.assertEqual(environment["XDG_RUNTIME_DIR"], "/run/user/123")
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=test")
        self.assertEqual(environment["MOZ_ENABLE_WAYLAND"], "1")
        self.assertEqual(environment["LIBSEAT_BACKEND"], "logind")
        self.assertNotIn("DISPLAY", environment)
        self.assertNotIn("WAYLAND_DISPLAY", environment)
        self.assertNotIn("GREETD_SOCK", environment)

    def supervisor(self, results):
        processes = [Mock(pid=42000 + i) for i in range(len(results))]
        for process, result in zip(processes, results):
            process.poll.return_value = result
        popen = self.enterContext(patch.object(runtime.subprocess, "Popen", side_effect=processes))
        cleanup = self.enterContext(patch.object(runtime, "_stop_group"))
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = False
        self.enterContext(patch.object(runtime.threading, "Event", return_value=event))
        handlers = self.enterContext(patch.object(runtime.signal, "signal", return_value=signal.SIG_DFL))
        self.enterContext(patch.object(runtime.time, "monotonic", return_value=0))
        self.enterContext(redirect_stdout(io.StringIO()))
        return popen, cleanup, event, handlers

    def test_successful_close_does_not_relaunch_browser(self):
        popen, cleanup, _, handlers = self.supervisor([0])
        self.assertEqual(runtime.supervise("http://localhost/"), 0)
        self.assertEqual(popen.call_count, 1)
        cleanup.assert_called_once()
        self.assertEqual(handlers.call_count, 6)

    def test_supervisor_opens_the_generated_local_page_when_requested(self):
        popen, cleanup, _, _ = self.supervisor([0])
        self.assertEqual(runtime.supervise("http://localhost/", start_page=True), 0)
        self.assertEqual(popen.call_args.args[0][-1], (self.home / "start.html").as_uri())
        cleanup.assert_called_once()

    def test_crashes_exhaust_budget_and_clean_every_attempt(self):
        popen, cleanup, event, handlers = self.supervisor([1, -11, 1])
        self.assertEqual(runtime.supervise("http://localhost/"), 1)
        self.assertEqual(popen.call_count, runtime.MAX_ATTEMPTS)
        self.assertEqual(cleanup.call_count, runtime.MAX_ATTEMPTS)
        self.assertEqual(event.wait.call_count, runtime.MAX_ATTEMPTS - 1)
        self.assertEqual(handlers.call_count, 6)

    def test_stable_run_resets_crash_budget(self):
        popen, cleanup, _, _ = self.supervisor([1, 1, 1, 0])
        with patch.object(runtime.time, "monotonic", side_effect=[0, 1, 2, 70, 71, 72, 73]):
            self.assertEqual(runtime.supervise("http://localhost/"), 0)
        self.assertEqual(popen.call_count, 4)
        self.assertEqual(cleanup.call_count, 4)

    def test_termination_stops_child_without_retry(self):
        popen, cleanup, event, handlers = self.supervisor([None])

        def interrupt(_seconds):
            handlers.call_args_list[0].args[1](signal.SIGTERM, None)
            event.is_set.return_value = True
            return True

        event.wait.side_effect = interrupt
        self.assertEqual(runtime.supervise("http://localhost/"), 0)
        self.assertEqual(popen.call_count, 1)
        cleanup.assert_called_once()

    def test_cleanup_escalates_and_reaps_after_timeout(self):
        process = Mock(pid=424242)
        process.wait.side_effect = [subprocess.TimeoutExpired("cage", 5), 0]
        with patch.object(runtime.os, "killpg") as kill:
            runtime._stop_group(process)
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(424242, signal.SIGTERM), (424242, signal.SIGKILL)])
        self.assertEqual(process.wait.call_count, 2)

    def test_root_and_wrong_account_cannot_launch_browser(self):
        with patch.object(runtime.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=12345)), \
             patch.object(runtime, "supervise") as supervise:
            for uid in (0, 54321):
                with self.subTest(uid=uid), patch.object(runtime.os, "getuid", return_value=uid), \
                     self.assertRaisesRegex(RuntimeError, "unprivileged"):
                    runtime.run_session(self.home / "config.json")
        supervise.assert_not_called()

    def test_private_session_can_launch_but_rejects_shared_or_symlinked_profile(self):
        run_dir = self.home / "runtime"
        run_dir.mkdir(mode=0o700)
        self.home.chmod(0o700)
        with patch.object(runtime.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid())), \
             patch.object(runtime.os, "getuid", return_value=os.getuid() or 12345), \
             patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(run_dir)}), \
             patch.object(runtime, "configuration", return_value={"url": "http://localhost/"}), \
             patch.object(runtime, "supervise", return_value=0) as supervise:
            # Fixtures use their actual UID; root CI needs a matching simulated
            # desktop identity while retaining real file types and permissions.
            actual_lstat = Path.lstat

            def user_stat(path):
                values = list(actual_lstat(path))
                values[4] = os.getuid() or 12345
                return os.stat_result(values)

            with patch.object(Path, "lstat", autospec=True, side_effect=user_stat), \
                 patch.object(runtime.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid() or 12345)):
                self.assertEqual(runtime.run_session(self.home / "config.json"), 0)
                supervise.assert_called_once_with("http://localhost/")
                supervise.reset_mock()
                destination = {"name": "Coding VM", "url": "http://vm.example/"}
                with patch.object(runtime, "configuration", return_value={"url": "http://localhost/",
                                                                         "destinations": [destination]}):
                    self.assertEqual(runtime.run_session(self.home / "config.json"), 0)
                supervise.assert_called_once_with("http://localhost/", start_page=True)
                page = self.home / "start.html"
                self.assertEqual(page.stat().st_mode & 0o777, 0o600)
                self.assertIn("Coding VM", page.read_text())
                self.assertIn('href="http://vm.example/"', page.read_text())
                (self.home / "profile").chmod(0o755)
                with self.assertRaisesRegex(RuntimeError, "profile"):
                    runtime.run_session(self.home / "config.json")
                (self.home / "profile").rmdir()
                (self.home / "profile").symlink_to(run_dir, target_is_directory=True)
                with self.assertRaisesRegex(RuntimeError, "profile"):
                    runtime.run_session(self.home / "config.json")

    def test_configuration_requires_owned_regular_file_and_supported_schema(self):
        path = self.home / "config.json"
        payload = {"manager": runtime.MANAGER, "version": 1, "url": "http://localhost/"}
        path.write_text(json.dumps(payload))
        path.chmod(0o644)
        original = Path.lstat

        def root_stat(candidate):
            values = list(original(candidate))
            values[4] = 0
            return os.stat_result(values)

        with patch.object(Path, "lstat", autospec=True, side_effect=root_stat):
            self.assertEqual(runtime.configuration(path), payload)
            with_destinations = payload | {"destinations": [{"name": "VM", "url": "https://vm.example/"}]}
            path.write_text(json.dumps(with_destinations))
            self.assertEqual(runtime.configuration(path), with_destinations)
            for change in ({"version": True}, {"version": 2}, {"manager": "other"}, {"url": "file:///etc/passwd"},
                           {"destinations": None}, {"destinations": [{"name": "Bad", "url": "file:///etc/passwd"}]},
                           {"unexpected": []}):
                path.write_text(json.dumps(payload | change))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    runtime.configuration(path)
            path.chmod(0o666)
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                runtime.configuration(path)
            path.unlink()
            path.symlink_to(self.home / "other")
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                runtime.configuration(path)

    def test_maximum_destination_urls_fit_the_bounded_session_reader(self):
        path = self.home / "config.json"
        url = "https://vm.example/" + "é" * 4000
        payload = {"manager": runtime.MANAGER, "version": 1, "url": "http://localhost/",
                   "destinations": [{"name": f"VM {number}", "url": url} for number in range(16)]}
        path.write_text(json.dumps(payload))
        path.chmod(0o644)
        original = Path.lstat

        def root_stat(candidate):
            values = list(original(candidate))
            values[4] = 0
            return os.stat_result(values)

        with patch.object(Path, "lstat", autospec=True, side_effect=root_stat):
            self.assertEqual(runtime.configuration(path), payload)

    def test_stop_cleans_only_dedicated_logind_user(self):
        with patch.object(runtime.os, "getuid", return_value=0), \
             patch.object(runtime.subprocess, "run", side_effect=[
                 Mock(stdout="1000 agent\n12345 basaltwater-kiosk\n"), Mock(), Mock(stdout="1000 agent\n")]) as run:
            self.assertEqual(runtime.cleanup_sessions(), 0)
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/loginctl", "terminate-user", runtime.USER])

    def test_stop_handles_absent_user_but_propagates_inspection_or_termination_failure(self):
        with patch.object(runtime.os, "getuid", return_value=0), \
             patch.object(runtime.subprocess, "run", return_value=Mock(stdout="1000 agent\n")) as run:
            self.assertEqual(runtime.cleanup_sessions(), 0)
            self.assertEqual(run.call_count, 1)
            run.return_value.stdout = "unexpected output"
            with self.assertRaisesRegex(RuntimeError, "inspect"):
                runtime.cleanup_sessions()
            run.side_effect = [Mock(stdout="12345 basaltwater-kiosk\n"), subprocess.CalledProcessError(1, "loginctl")]
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.cleanup_sessions()

    def test_stop_rejects_changed_account_and_logind_identity(self):
        with patch.object(runtime.os, "getuid", return_value=0), \
             patch.object(runtime.subprocess, "run", return_value=Mock(stdout="999 basaltwater-kiosk\n")) as run:
            with self.assertRaisesRegex(RuntimeError, "identity"):
                runtime.cleanup_sessions()
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            runtime.pwd.getpwnam.return_value.pw_uid = 0
            with self.assertRaisesRegex(RuntimeError, "changed kiosk account"):
                runtime.cleanup_sessions()
            run.assert_not_called()

    def test_cleanup_requires_root_before_inspecting_users(self):
        with patch.object(runtime.os, "getuid", return_value=12345), \
             patch.object(runtime.subprocess, "run") as run, self.assertRaisesRegex(RuntimeError, "requires root"):
            runtime.cleanup_sessions()
        run.assert_not_called()

    def test_stop_waits_for_session_and_runtime_teardown_after_queued_termination(self):
        directory = runtime.RUNTIME_ROOT / "12345"
        directory.mkdir(parents=True)
        users = "12345 basaltwater-kiosk\n"

        def teardown(_seconds):
            if directory.exists():
                directory.rmdir()

        with patch.object(runtime.os, "getuid", return_value=0), \
             patch.object(runtime.time, "sleep", side_effect=teardown) as sleep, \
             patch.object(runtime.subprocess, "run", side_effect=[
                 Mock(stdout=users), Mock(), Mock(stdout=users), Mock(stdout="")]) as run:
            self.assertEqual(runtime.cleanup_sessions(), 0)
        self.assertEqual(run.call_count, 4)
        sleep.assert_called_once()
        self.assertFalse(directory.exists())

    def test_stop_times_out_when_runtime_teardown_never_finishes(self):
        directory = runtime.RUNTIME_ROOT / "12345"
        directory.mkdir(parents=True)
        clock = [0.0]

        def advance(seconds):
            clock[0] += seconds

        with patch.object(runtime.os, "getuid", return_value=0), \
             patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(runtime.time, "sleep", side_effect=advance), \
             patch.object(runtime.subprocess, "run", return_value=Mock(stdout="")), \
             self.assertRaisesRegex(RuntimeError, "Timed out"):
            runtime.cleanup_sessions()
        self.assertEqual(clock[0], runtime.CLEANUP_SECONDS)


if __name__ == "__main__":
    unittest.main()
