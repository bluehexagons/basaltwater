"""T3 login-startup configuration in temporary homes, without launching apps."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import cachyos_t3_desktop as desktop
from common import cachyos_t3 as t3
from common.service_tools import cachyos_t3_refresh as refresh


class DesktopAutostartTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}))

    def test_entry_uses_native_executable_and_kde_login_only(self):
        path = desktop.autostart_path(self.home)
        self.assertFalse(path.parent.exists())
        desktop.enable_autostart(self.home)
        self.assertEqual(path.read_text(), desktop.AUTOSTART_ENTRY)
        self.assertIn("Exec=/usr/bin/t3code\n", path.read_text())
        self.assertIn("TryExec=/usr/bin/t3code\n", path.read_text())
        self.assertIn("OnlyShowIn=KDE;\n", path.read_text())
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "available")
        desktop.disable_autostart(self.home)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")

    def test_custom_xdg_config_home_is_honored_and_relative_values_ignored(self):
        configured = self.home / "custom config"
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(configured)}):
            desktop.enable_autostart(self.home)
            path = desktop.autostart_path(self.home)
            self.assertEqual(path.parent, configured / "autostart")
            desktop.disable_autostart(self.home)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}):
            self.assertEqual(desktop.autostart_path(self.home).parent, self.home / ".config/autostart")

    def test_custom_files_and_symlinks_are_preserved(self):
        path = desktop.autostart_path(self.home)
        path.parent.mkdir(parents=True)
        for kind in ("file", "symlink", "directory"):
            with self.subTest(kind=kind):
                if kind == "file":
                    path.write_text("personal fixture")
                elif kind == "symlink":
                    path.symlink_to(self.home / "missing")
                else:
                    path.mkdir()
                for operation in (desktop.enable_autostart, desktop.disable_autostart):
                    with self.assertRaises(ValueError):
                        operation(self.home)
                self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")
                if kind == "directory":
                    path.rmdir()
                else:
                    path.unlink()

    def test_symlinked_config_directory_is_not_traversed(self):
        outside = self.home / "personal"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "autostart directory"):
            desktop.enable_autostart(self.home)
        self.assertEqual(list(outside.iterdir()), [])

    def test_disabled_entry_is_advisory_and_setup_reenables_it(self):
        desktop.enable_autostart(self.home)
        path = desktop.autostart_path(self.home)
        path.write_text(desktop.AUTOSTART_ENTRY + "Hidden=true\n")
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "deferred")
        desktop.enable_autostart(self.home)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "available")

    def test_changed_or_unsafe_entries_do_not_qualify_startup(self):
        desktop.enable_autostart(self.home)
        path = desktop.autostart_path(self.home)
        path.write_text(desktop.AUTOSTART_ENTRY.replace("Exec=/usr/bin/t3code", "Exec=personal-secret"))
        state, reason = desktop.collect_autostart_health(self.home)
        self.assertEqual(state, "failed")
        self.assertNotIn("personal-secret", reason)
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            desktop.disable_autostart(self.home)
        path.chmod(0o644)
        with patch.object(desktop.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")
        path.write_bytes(b"x" * 16385)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")


class DesktopRefreshTests(unittest.TestCase):
    QUEUED_BEFORE_NS = 10_000_000_000

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.stack = stack
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.proc = self.home / "proc"
        self.proc.mkdir()
        stack.enter_context(patch.object(refresh, "PROC", self.proc))
        self.pidfd = stack.enter_context(patch.object(refresh.os, "pidfd_open", side_effect=lambda pid: pid + 1000))
        self.close = stack.enter_context(patch.object(refresh.os, "close"))

    def process(self, pid, arguments=(), environment=(), *, executable=None, started_seconds=1):
        path = self.proc / str(pid)
        path.mkdir()
        (path / "exe").symlink_to(executable or refresh.APP_EXECUTABLE)
        (path / "cwd").symlink_to(self.home)
        (path / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in
                                               (refresh.APP_EXECUTABLE, *arguments)) + b"\0")
        (path / "environ").write_bytes(b"\0".join(os.fsencode(item) for item in environment) + b"\0")
        start_ticks = started_seconds * os.sysconf("SC_CLK_TCK")
        (path / "stat").write_text(f"{pid} (T3 (desktop)) S " + "0 " * 18 + str(start_ticks) + "\n")
        return path

    def test_discovery_excludes_renderers_cli_hosts_and_other_executables(self):
        self.process(100, ("--user-data-dir=/private/profile",), ("DISPLAY=:1", "TOKEN=private=value"),
                     executable=refresh.APP_EXECUTABLE + " (deleted)")
        self.process(101, ("--type=renderer",))
        self.process(102, environment=("ELECTRON_RUN_AS_NODE=1",))
        self.process(103, executable="/usr/bin/unrelated")
        app = refresh._running_desktop(self.QUEUED_BEFORE_NS)
        self.assertEqual(app.descriptor, 1100)
        self.assertEqual(app.arguments[1:], ["--user-data-dir=/private/profile"])
        self.assertEqual(app.environment, {"DISPLAY": ":1", "TOKEN": "private=value"})
        self.assertEqual(app.directory, str(self.home))
        self.assertEqual({call.args[0] for call in self.close.call_args_list}, {1101, 1102})

    def test_foreign_users_and_vanished_processes_are_not_signaled(self):
        self.process(100)
        with patch.object(refresh.os, "getuid", return_value=os.getuid() + 1):
            self.assertIsNone(refresh._running_desktop(self.QUEUED_BEFORE_NS))
        self.pidfd.assert_not_called()
        self.pidfd.side_effect = ProcessLookupError()
        self.assertIsNone(refresh._running_desktop(self.QUEUED_BEFORE_NS))
        self.close.assert_not_called()

    def test_multiple_desktop_profiles_fail_before_shutdown_and_close_handles(self):
        self.process(100)
        self.process(101)
        with self.assertRaisesRegex(RuntimeError, "Multiple T3 desktops"):
            refresh._running_desktop(self.QUEUED_BEFORE_NS)
        self.assertEqual({call.args[0] for call in self.close.call_args_list}, {1100, 1101})

    def test_unreadable_unrelated_process_does_not_block_discovery(self):
        self.process(100)
        with patch.object(refresh, "_is_app", side_effect=PermissionError()):
            self.assertIsNone(refresh._running_desktop(self.QUEUED_BEFORE_NS))
        self.pidfd.assert_not_called()

    def test_overlapping_requests_do_not_restart_the_reopened_app(self):
        self.process(100)
        app = refresh._running_desktop(self.QUEUED_BEFORE_NS)
        self.assertEqual(app.descriptor, 1100)
        # The first worker exits the old process and execs T3 after both setups
        # queued their requests. The next serialized worker sees the new app.
        for path in (self.proc / "100").iterdir():
            path.unlink()
        (self.proc / "100").rmdir()
        self.process(200, started_seconds=12)
        self.assertFalse(refresh._running_desktop(self.QUEUED_BEFORE_NS + 1_000_000_000).refresh_needed)
        # A later setup can still refresh the newly running app.
        self.assertEqual(refresh._running_desktop(15_000_000_000).descriptor, 1200)

    def test_launch_state_is_bounded(self):
        path = self.home / "state"
        path.write_bytes(b"private" * 10)
        with patch.object(refresh, "MAX_PROCESS_DATA", 16), \
                self.assertRaisesRegex(RuntimeError, "exceeds the refresh limit"):
            refresh._process_data(path)

    def test_request_queued_during_slow_shutdown_does_not_restart_reopened_app(self):
        # exec retains the worker's earlier start time, while the launch stamp
        # records that this app loaded settings after the second request.
        self.process(100, environment=(f"{refresh.REOPENED_AT_ENV}=12000000000",), started_seconds=9)
        self.assertFalse(refresh._running_desktop(11_000_000_000).refresh_needed)
        self.assertEqual(refresh._running_desktop(15_000_000_000).descriptor, 1100)

    def runtime_mocks(self, *, running=True, owner="t3code-bin", polls=None):
        app = refresh.RunningDesktop(1100, ["old-path", "--user-data-dir=/private/profile"],
                                     {"DISPLAY": ":1", "TOKEN": "private"}, str(self.home))
        self.stack.enter_context(patch.object(refresh, "_running_desktop", return_value=app if running else None))
        self.stack.enter_context(patch.object(refresh, "validate_filesystem_path"))
        self.stack.enter_context(patch.object(refresh.os, "access", return_value=True))
        self.chdir = self.stack.enter_context(patch.object(refresh.os, "chdir"))
        self.package = self.stack.enter_context(patch.object(
            refresh.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, owner + "\n", "")))
        self.terminate = self.stack.enter_context(patch.object(refresh.signal, "pidfd_send_signal"))
        self.execute = self.stack.enter_context(patch.object(refresh.os, "execve"))
        self.stack.enter_context(patch.object(refresh.time, "clock_gettime_ns", return_value=20_000_000_000))
        self.poller = self.stack.enter_context(patch.object(refresh.select, "poll")).return_value
        self.poller.poll.side_effect = polls if polls is not None else [[], [(1100, 1)]]
        return app

    def test_restart_waits_for_graceful_shutdown_and_preserves_private_launch_state(self):
        app = self.runtime_mocks()
        events = []
        self.terminate.side_effect = lambda *args: events.append("terminate")
        self.poller.poll.side_effect = lambda timeout: [] if timeout == 0 else events.append("exited") or [(1100, 1)]
        self.execute.side_effect = lambda *args: events.append("launch")
        self.assertTrue(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS))
        self.assertEqual(events, ["terminate", "exited", "launch"])
        self.terminate.assert_called_once_with(app.descriptor, signal.SIGTERM)
        self.execute.assert_called_once_with(refresh.APP_EXECUTABLE,
                                             [refresh.APP_EXECUTABLE, *app.arguments[1:]],
                                             {**app.environment, refresh.REOPENED_AT_ENV: "20000000000"})
        self.chdir.assert_called_once_with(app.directory)
        self.close.assert_called_once_with(app.descriptor)

    def test_stopped_app_starts_in_active_graphical_session(self):
        self.runtime_mocks(running=False)
        with patch.dict(os.environ, {"DISPLAY": ":1", "ELECTRON_RUN_AS_NODE": "1"}):
            self.assertTrue(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS, self.home))
        self.assertEqual(self.package.call_args_list[0].args[0][-1], "graphical-session.target")
        self.assertEqual(self.package.call_args_list[1].args[0],
                         ["/usr/bin/pacman", "-Qqo", "--", refresh.APP_EXECUTABLE])
        self.assertEqual(self.execute.call_args.args[:2], (refresh.APP_EXECUTABLE, [refresh.APP_EXECUTABLE]))
        self.assertNotIn("ELECTRON_RUN_AS_NODE", self.execute.call_args.args[2])
        self.terminate.assert_not_called()
        self.close.assert_not_called()

    def test_stopped_app_defers_without_active_graphical_session(self):
        self.runtime_mocks(running=False)
        for environment, status in (({}, 0), ({"DISPLAY": ":1"}, 3)):
            with self.subTest(environment=environment, status=status), \
                    patch.dict(os.environ, environment, clear=True):
                self.package.return_value = subprocess.CompletedProcess([], status, "", "")
                self.assertFalse(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS, self.home))
        self.execute.assert_not_called()
        self.terminate.assert_not_called()

    def test_fresh_running_app_is_not_duplicated_or_restarted(self):
        app = self.runtime_mocks()
        app.refresh_needed = False
        self.assertFalse(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS, self.home))
        self.package.assert_not_called()
        self.execute.assert_not_called()
        self.terminate.assert_not_called()
        self.close.assert_called_once_with(app.descriptor)

    def test_exited_process_handle_does_not_reopen_app(self):
        self.runtime_mocks(polls=[[(1100, 1)]])
        self.assertFalse(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS))
        self.package.assert_not_called()
        self.execute.assert_not_called()
        self.terminate.assert_not_called()
        self.close.assert_called_once_with(1100)

    def test_app_closed_during_package_probe_is_not_reopened(self):
        self.runtime_mocks()
        self.terminate.side_effect = ProcessLookupError()
        self.assertFalse(refresh.restart_running_desktop(self.QUEUED_BEFORE_NS))
        self.poller.poll.assert_called_once_with(0)
        self.execute.assert_not_called()
        self.close.assert_called_once_with(1100)

    def test_failed_shutdown_never_force_kills_or_launches_duplicate(self):
        self.runtime_mocks(polls=[[], []])
        with self.assertRaisesRegex(RuntimeError, "no forced kill or duplicate launch"):
            refresh.restart_running_desktop(self.QUEUED_BEFORE_NS)
        self.terminate.assert_called_once_with(1100, signal.SIGTERM)
        self.execute.assert_not_called()
        self.close.assert_called_once_with(1100)

    def test_wrong_package_owner_is_rejected_before_shutdown(self):
        self.runtime_mocks(owner="foreign-package")
        with self.assertRaisesRegex(RuntimeError, "belong to t3code-bin"):
            refresh.restart_running_desktop(self.QUEUED_BEFORE_NS)
        self.terminate.assert_not_called()
        self.execute.assert_not_called()
        self.close.assert_called_once_with(1100)

    def test_package_probe_failure_is_propagated_without_shutdown(self):
        self.runtime_mocks()
        self.package.side_effect = subprocess.TimeoutExpired("pacman", 15)
        with self.assertRaises(subprocess.TimeoutExpired):
            refresh.restart_running_desktop(self.QUEUED_BEFORE_NS)
        self.terminate.assert_not_called()
        self.execute.assert_not_called()
        self.close.assert_called_once_with(1100)

    def test_queue_runs_independently_and_dry_run_does_not_schedule(self):
        config = SimpleNamespace(dry_run=False, username="fixture", machine_type="vm",
                                 t3code_desktop=True, _cachyos_t3_completion_request="a" * 32)
        with patch("lib.remote_utils.is_dry_run", return_value=False), \
                patch("lib.machine_state.can_manage_system_services", return_value=True), \
                patch.object(desktop.os, "getuid", return_value=1000), \
                patch.object(desktop.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)), \
                patch("common.cachyos_steps._home", return_value=self.home), \
                patch.object(desktop.time, "clock_gettime_ns", return_value=self.QUEUED_BEFORE_NS), \
                patch("lib.remote_utils.run") as run:
            desktop.queue_refresh(config)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:3], ["/usr/bin/systemd-run", "--user", "--collect"])
            self.assertIn("--on-active=5s", argv)
            self.assertEqual(argv[-8:-6], ["/usr/bin/python3", "-I"])
            self.assertTrue(argv[-6].endswith("/common/service_tools/cachyos_t3_refresh.py"))
            self.assertEqual(argv[-5:], [str(self.QUEUED_BEFORE_NS), "desktop", str(self.home),
                                        str(self.home / ".config"), "a" * 32])
            config.dry_run = True
            run.reset_mock()
            desktop.queue_refresh(config)
            run.assert_not_called()

    def test_worker_serializes_before_discovery_and_rejects_unsafe_locks(self):
        with patch.object(refresh.os, "getuid", return_value=1000), \
                patch.object(refresh, "Path") as path, \
                patch.object(refresh.os, "open", return_value=2000) as open_lock, \
                patch.object(refresh.os, "fstat") as file_info, \
                patch.object(refresh.fcntl, "flock") as lock, \
                patch.object(refresh, "restart_running_desktop") as restart, \
                patch("common.cachyos_t3.complete_setup", return_value=True) as complete, \
                patch.object(refresh, "validate_filesystem_path"), \
                patch.dict(os.environ):
            runtime_info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
            path.return_value.lstat.return_value = runtime_info
            path.return_value.is_absolute.return_value = True
            lock_info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1)
            file_info.return_value = lock_info
            events = []
            lock.side_effect = lambda *args: events.append("lock")
            restart.side_effect = lambda *args: events.append("discover")
            self.assertEqual(refresh.main(self.QUEUED_BEFORE_NS, "desktop", self.home,
                                          self.home / ".config", "a" * 32), 0)
            self.assertEqual(events, ["lock", "discover"])
            complete.assert_called_once_with(self.home, "desktop", "a" * 32)
            restart.assert_called_once_with(self.QUEUED_BEFORE_NS, self.home)
            self.assertTrue(open_lock.call_args.args[1] & os.O_CLOEXEC)
            self.close.assert_called_once_with(2000)
            for field, value in (("st_mode", stat.S_IFREG | 0o666), ("st_uid", 2000), ("st_nlink", 2)):
                with self.subTest(field=field):
                    previous = getattr(lock_info, field)
                    setattr(lock_info, field, value)
                    restart.reset_mock()
                    lock.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, "Unsafe T3 desktop refresh lock"):
                        refresh.main(self.QUEUED_BEFORE_NS, "desktop", self.home,
                                     self.home / ".config", "a" * 32)
                    restart.assert_not_called()
                    lock.assert_not_called()
                    setattr(lock_info, field, previous)
            runtime_info.st_mode = stat.S_IFDIR | 0o777
            open_lock.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "private user runtime directory"):
                refresh.main(self.QUEUED_BEFORE_NS, "desktop", self.home,
                             self.home / ".config", "a" * 32)
            open_lock.assert_not_called()

    def test_plugin_saves_last_and_leaves_disruptive_completion_to_runner(self):
        import basaltwater
        from common import cachyos_steps
        from lib.cachyos import cachyos_config_from_args
        from plugins.cachyos import build_cachyos_steps

        for option in (["--t3code-desktop"], ["--web-interface", "t3code"]):
            with self.subTest(option=option):
                parser, _, _ = basaltwater.create_basaltwater_parser()
                config = cachyos_config_from_args(parser.parse_args(
                    ["setup", "agent_cachyos", "localhost", "human", *option]))
                functions = [function for _, function in build_cachyos_steps(config)]
                install = (cachyos_steps.install_cachyos_t3_desktop if config.t3code_desktop
                           else cachyos_steps.install_cachyos_t3)
                self.assertLess(functions.index(cachyos_steps.install_cachyos_skills), functions.index(install))
                self.assertEqual(functions[-1], cachyos_steps.save_cachyos_setup)
                self.assertNotIn(desktop.queue_refresh, functions)


class CompletionLockTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(t3.os, "getuid", return_value=1000))
        path = self.stack.enter_context(patch.object(t3, "Path"))
        path.return_value.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
        self.stack.enter_context(patch.object(t3.os, "open", return_value=2000))
        self.info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1,
                                    st_dev=1, st_ino=2)
        self.file_info = self.stack.enter_context(patch.object(t3.os, "fstat", return_value=self.info))
        self.close = self.stack.enter_context(patch.object(t3.os, "close"))
        self.inheritable = self.stack.enter_context(patch.object(t3.os, "set_inheritable"))
        self.lock = self.stack.enter_context(patch.object(t3.fcntl, "flock"))
        self.stack.enter_context(patch.object(t3, "is_dry_run", return_value=False))
        self.config = SimpleNamespace(dry_run=False)

    def test_setup_refuses_a_concurrent_completion_before_running_steps(self):
        self.lock.side_effect = BlockingIOError()
        with self.assertRaisesRegex(RuntimeError, "Another CachyOS setup or T3 completion"):
            with t3.setup_completion_lock(self.config):
                self.fail("Concurrent setup entered its step body")
        self.close.assert_called_once_with(2000)

    def test_refresh_handoff_reuses_only_the_same_lock_file_and_clears_inheritance(self):
        os.environ[t3.COMPLETION_LOCK_FD_ENV] = "3000"
        with t3.setup_completion_lock(self.config) as descriptor:
            self.assertEqual(descriptor, 3000)
            self.assertNotIn(t3.COMPLETION_LOCK_FD_ENV, os.environ)
            self.inheritable.assert_called_once_with(3000, False)
            self.lock.assert_called_once_with(3000, t3.fcntl.LOCK_EX | t3.fcntl.LOCK_NB)
        self.assertEqual([call.args[0] for call in self.close.call_args_list], [2000, 3000])

    def test_foreign_or_standard_stream_descriptor_cannot_bypass_lock(self):
        for value in ("0", "2", "invalid", "3000"):
            with self.subTest(value=value):
                os.environ[t3.COMPLETION_LOCK_FD_ENV] = value
                self.file_info.side_effect = [self.info, SimpleNamespace(st_dev=2, st_ino=3)]
                self.lock.reset_mock()
                with self.assertRaises(ValueError):
                    with t3.setup_completion_lock(self.config):
                        self.fail("Invalid inherited descriptor entered its step body")
                self.lock.assert_not_called()

    def test_dry_run_does_not_open_lock(self):
        self.config.dry_run = True
        with t3.setup_completion_lock(self.config) as descriptor:
            self.assertIsNone(descriptor)
        t3.os.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
