"""Privileged observation and permanent worker demotion; host calls are mocked."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import user_cache_launcher as launcher, user_cache_maintenance
from lib import cleanup_process_observer as client


class LauncherTests(unittest.TestCase):
    def test_worker_is_demoted_before_python_and_receives_no_root_environment(self):
        account = SimpleNamespace(pw_uid=1000, pw_gid=1001, pw_name="agent", pw_dir="/home/agent")
        worker = Mock()
        worker.wait.return_value = 7
        worker.poll.return_value = 7
        with patch.object(launcher.subprocess, "Popen", return_value=worker) as spawn, \
                patch.object(launcher, "_serve") as serve:
            code = launcher.launch(account, Path("/opt/basaltwater"), dry_run=True, agent_storage_only=True)
        command = spawn.call_args.args[0]
        self.assertEqual(command[:9], [
            "/usr/bin/setpriv", "--reuid=1000", "--regid=1001", "--clear-groups",
            "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all", "--no-new-privs", "--",
        ])
        self.assertEqual(command[9:12], ["/usr/bin/python3", "-I", "/opt/basaltwater/common/service_tools/user_cache_maintenance.py"])
        self.assertIn("--agent-storage-only", command)
        self.assertIn("--dry-run", command)
        self.assertEqual(spawn.call_args.kwargs["cwd"], account.pw_dir)
        self.assertEqual(set(spawn.call_args.kwargs["env"]), {"HOME", "USER", "LOGNAME", "PATH", "LANG"})
        self.assertEqual(len(spawn.call_args.kwargs["pass_fds"]), 1)
        self.assertTrue(spawn.call_args.kwargs["close_fds"])
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(serve.call_args.args[2], 1000)
        self.assertEqual(code, 7)

    def test_drop_failure_preserves_exit_and_observer_failure_reaps_worker(self):
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="agent", pw_dir="/home/agent")
        worker = Mock()
        worker.poll.return_value = 1
        worker.wait.return_value = 1
        with patch.object(launcher.subprocess, "Popen", return_value=worker), patch.object(launcher, "_serve"):
            self.assertEqual(launcher.launch(account, Path("/opt/basaltwater"), dry_run=False, agent_storage_only=False), 1)
        worker.poll.return_value = None
        worker.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        with patch.object(launcher.subprocess, "Popen", return_value=worker), \
                patch.object(launcher, "_serve", side_effect=OSError("fixture channel lost")):
            with self.assertRaises(OSError):
                launcher.launch(account, Path("/opt/basaltwater"), dry_run=False, agent_storage_only=False)
        worker.terminate.assert_called_once()
        worker.kill.assert_called_once()

    def test_unprivileged_or_nonisolated_start_never_launches(self):
        for uid, isolated in ((1000, True), (0, False)):
            with self.subTest(uid=uid, isolated=isolated), \
                    patch.object(launcher.os, "geteuid", return_value=uid), \
                    patch.object(launcher, "sys", SimpleNamespace(
                        flags=SimpleNamespace(isolated=isolated), stderr=launcher.sys.stderr)), \
                    patch.object(launcher, "launch") as launch:
                self.assertEqual(launcher.main(["--user", "agent"]), 1)
                launch.assert_not_called()

    def test_root_validates_code_and_account_before_starting_worker(self):
        events = []
        account = SimpleNamespace(pw_uid=1000)
        with patch.object(launcher.os, "geteuid", return_value=0), \
                patch.object(launcher, "sys", SimpleNamespace(
                    flags=SimpleNamespace(isolated=True), stderr=launcher.sys.stderr)), \
                patch.object(launcher, "_trusted_path", side_effect=lambda _: events.append("trust")), \
                patch.object(launcher, "_account", side_effect=lambda *args: events.append("account") or account), \
                patch.object(launcher, "launch", side_effect=lambda *args, **kwargs: events.append("worker") or 7):
            self.assertEqual(launcher.main(["--user", "agent", "--dry-run"]), 7)
        self.assertEqual(events, ["trust", "account", "worker"])

    def test_account_uses_shared_validator_and_requires_nonroot_owned_home(self):
        project = Path(__file__).resolve().parents[2]
        account = SimpleNamespace(pw_uid=1000, pw_dir="/home/agent")
        with patch.object(launcher, "_trusted_path"), \
                patch.object(launcher.pwd, "getpwnam", return_value=account) as lookup, \
                patch.object(launcher.os, "lstat", return_value=SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o700)):
            self.assertIs(launcher._account("agent", project), account)
            for username in ("--root", "agent\nroot", "../root"):
                with self.subTest(username=username), self.assertRaises(ValueError):
                    launcher._account(username, project)
            self.assertEqual(lookup.call_count, 1)
            account.pw_uid = 0
            with self.assertRaises(ValueError):
                launcher._account("root", project)
            account.pw_uid = 1001
            with self.assertRaises(ValueError):
                launcher._account("agent", project)

    def test_trusted_code_rejects_writable_file_and_nonroot_ancestors(self):
        root_file = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
        root_dir = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        for bad in (SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o755),
                    SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o777)):
            with self.subTest(bad=bad), patch.object(Path, "lstat", side_effect=[root_dir, bad]):
                with self.assertRaises(ValueError):
                    launcher._trusted_path(Path("/opt/launcher.py"))
        with patch.object(Path, "lstat", side_effect=[root_dir, root_dir, SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o666)]):
            with self.assertRaises(ValueError):
                launcher._trusted_path(Path("/opt/launcher.py"))
        with patch.object(Path, "lstat", side_effect=[root_dir, SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777), root_file]):
            launcher._trusted_path(Path("/tmp/launcher.py"))

    def test_protocol_has_only_fixed_inspection_and_bounded_results(self):
        for request, valid in ((b"inspect\n", True), (b"inspect /etc/shadow\n", False), (b"uid=0\n", False)):
            with self.subTest(request=request), self._channels() as (parent, child):
                child.sendall(request)
                worker = Mock()
                worker.poll.side_effect = [None, 0]
                with patch.object(launcher, "_executables", return_value=["/usr/lib/systemd/systemd"]) as scan:
                    if valid:
                        launcher._serve(parent, worker, 1000)
                        self.assertEqual(json.loads(child.recv(4096)), {"executables": ["/usr/lib/systemd/systemd"]})
                        scan.assert_called_once_with(1000)
                    else:
                        with self.assertRaises(ValueError):
                            launcher._serve(parent, worker, 1000)
                        scan.assert_not_called()

    @staticmethod
    @contextmanager
    def _channels():
        parent, child = socket.socketpair()
        try:
            yield parent, child
        finally:
            parent.close()
            child.close()

    def test_observation_failure_is_an_incomplete_reply_without_private_details(self):
        for failure in (PermissionError("private fixture"), ValueError("private fixture")):
            with self.subTest(failure=failure), self._channels() as (parent, child):
                child.sendall(b"inspect\n")
                worker = Mock()
                worker.poll.side_effect = [None, 0]
                with patch.object(launcher, "_executables", side_effect=failure):
                    launcher._serve(parent, worker, 1000)
                self.assertEqual(json.loads(child.recv(4096)), {"error": "process inventory unavailable"})


class ProcessInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.proc = Path(temporary.name)
        original = os.open

        def open_fixture(path, *args, **kwargs):
            return original(self.proc if path == "/proc" else path, *args, **kwargs)

        patcher = patch.object(launcher.os, "open", side_effect=open_fixture)
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, pid, uid=1000, *, state="S", target="/usr/lib/systemd/systemd"):
        root = self.proc / str(pid)
        root.mkdir()
        (root / "status").write_bytes(b"Name:\tfixture-\xff\n" + f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nState:\t{state} (fixture)\n".encode())
        if target is not None:
            (root / "exe").symlink_to(target)
        return root

    def test_reads_protected_target_identity_and_never_resolves_privileged_paths(self):
        self.process(800)
        self.process(801, uid=0, target="/root/private-tool")
        self.process(802, state="Z", target=None)
        with patch.object(launcher.os.path, "realpath", side_effect=AssertionError("no privileged path traversal")):
            self.assertEqual(launcher._executables(1000), ["/usr/lib/systemd/systemd"])

    def test_live_missing_executable_and_invalid_identity_defer(self):
        process = self.process(800, target=None)
        with self.assertRaises(OSError):
            launcher._executables(1000)
        (process / "status").write_text("Uid:\t1000\nState:\tS\n")
        with self.assertRaises(ValueError):
            launcher._executables(1000)

    def test_process_exit_during_read_is_ignored_only_when_directory_is_gone(self):
        process = self.process(800)

        def exit_during_read(*args, **kwargs):
            (process / "exe").unlink()
            (process / "status").unlink()
            process.rmdir()
            raise ProcessLookupError("fixture exited")

        with patch.object(launcher.os, "readlink", side_effect=exit_during_read):
            self.assertEqual(launcher._executables(1000), [])

    def test_account_change_or_pid_replacement_defers(self):
        self.process(800)
        with patch.object(launcher, "_status", side_effect=[((1000,) * 4, "S"), ((0,) * 4, "S")]):
            with self.assertRaises(OSError):
                launcher._executables(1000)
        with patch.object(launcher.os, "fstat", return_value=SimpleNamespace(st_dev=-1, st_ino=-1)):
            with self.assertRaises(OSError):
                launcher._executables(1000)

    def test_unreadable_enumeration_and_scan_budget_fail_closed(self):
        self.process(800)
        with patch.object(launcher.os, "scandir", side_effect=PermissionError("fixture")):
            with self.assertRaises(OSError):
                launcher._executables(1000)
        with patch.object(launcher, "MAX_PROCESSES", 0):
            with self.assertRaises(OSError):
                launcher._executables(1000)


class ObserverClientTests(unittest.TestCase):
    def transport(self, *, uid=0, pid=123):
        channel = Mock(family=socket.AF_UNIX, type=socket.SOCK_STREAM)
        channel.getsockopt.return_value = struct.pack("3i", pid, uid, 0)
        self.enterContext(patch.object(client.socket, "socket", return_value=channel))
        self.enterContext(patch.object(client.os, "getuid", return_value=1000))
        self.enterContext(patch.object(client.os, "geteuid", return_value=1000))
        self.enterContext(patch.object(client.os, "getppid", return_value=123))
        return channel

    def test_authentication_rejects_wrong_peer_and_closes_descriptor(self):
        for uid, pid in ((1000, 123), (0, 456)):
            with self.subTest(uid=uid, pid=pid):
                channel = self.transport(uid=uid, pid=pid)
                with self.assertRaises(OSError):
                    client.ProcessObserver(12)
                channel.close.assert_called_once()

    def test_context_uses_fresh_requests_closes_and_never_inherits_channel(self):
        channel = self.transport()
        channel.recv.side_effect = [b'{"executables":["/one"]}\n', b'{"executables":["/two"]}\n']
        with client.use_process_observer(12):
            self.assertEqual(client.observed_executables(1000), ("/one",))
            self.assertEqual(client.observed_executables(1000), ("/two",))
            with self.assertRaises(OSError):
                client.observed_executables(2000)
        self.assertIsNone(client.observed_executables(1000))
        self.assertEqual(channel.sendall.call_count, 2)
        channel.set_inheritable.assert_called_once_with(False)
        channel.close.assert_called_once()

    def test_bad_incomplete_oversized_and_disconnected_replies_fail_closed(self):
        channel = self.transport()
        for reply in (b'{"error":"unavailable"}\n', b'{}\n', b'{"executables":["relative"]}\n',
                      b'not json\n', b'x' * (client.MAX_RESPONSE_BYTES + 1), b''):
            with self.subTest(reply=reply[:30]):
                channel.recv.side_effect = None
                channel.recv.return_value = reply
                with client.use_process_observer(12):
                    with self.assertRaisesRegex(OSError, "observation unavailable"):
                        client.observed_executables(1000)

    def test_timeout_permanently_invalidates_channel_before_late_reply(self):
        channel = self.transport()
        channel.recv.side_effect = [TimeoutError("fixture timeout"), b'{"executables":[]}\n']
        with client.use_process_observer(12):
            for _ in range(2):
                with self.assertRaisesRegex(OSError, "observation unavailable"):
                    client.observed_executables(1000)
            self.assertEqual(channel.recv.call_count, 1)
            self.assertEqual(channel.sendall.call_count, 1)
            channel.close.assert_called_once()

    def test_invalid_worker_channel_aborts_before_any_policy(self):
        with patch.object(user_cache_maintenance, "use_process_observer", side_effect=OSError("fixture denied")), \
                patch.object(user_cache_maintenance, "cleanup_agent_storage") as cleanup:
            self.assertEqual(user_cache_maintenance.main(["--agent-storage-only", "--process-observer-fd", "12"]), 1)
            cleanup.assert_not_called()
