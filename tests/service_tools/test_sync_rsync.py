"""Tests for sync.service_tools.sync_rsync."""

from __future__ import annotations

import logging
import os
import sys
import unittest
import tempfile
import subprocess
import signal
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, call, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from sync.service_tools import sync_rsync


class _FakeStream:
    def __init__(self, content: str):
        self._content = content

    def read(self) -> str:
        content = self._content
        self._content = ""
        return content

    def readline(self) -> str:
        if not self._content:
            return ""
        line, _sep, rest = self._content.partition("\n")
        self._content = rest
        return line + ("\n" if _sep else "")


class _FakeProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)

    def poll(self) -> int:
        return self.returncode


class TestSyncRsyncLogging(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.sync_rsync.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.propagate = True
        source_check = patch.object(sync_rsync.os.path, "isdir", return_value=True)
        source_check.start()
        self.addCleanup(source_check.stop)
        def fake_lines(process):
            for stream, is_stdout in ((process.stdout, True), (process.stderr, False)):
                for line in stream.read().splitlines():
                    yield is_stdout, line
        output = patch.object(sync_rsync, "_output_lines", side_effect=fake_lines)
        output.start()
        self.addCleanup(output.stop)

    def test_literal_exclusions_are_private_and_live_until_rsync_finishes(self):
        paths = ("file[1]?.bin", "literal\\name", "wild\\*.bin", "nested/+ file", "#comment")
        expected = b'/file\\[1]\\?.bin\0/literal\\name\0/wild\\\\\\*.bin\0/nested/+ file\0/#comment\0'
        filter_paths = []

        def popen(command, **kwargs):
            self.assertIn('--delete-delay', command)
            self.assertIn('--from0', command)
            path = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--exclude-from='))
            filter_paths.append(Path(path))
            self.assertEqual(Path(path).read_bytes(), expected)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            return _FakeProcess(0)

        def output(process):
            self.assertTrue(filter_paths[0].exists())
            yield True, ''

        with patch.object(sync_rsync.subprocess, 'Popen', side_effect=popen), patch.object(sync_rsync, '_output_lines', side_effect=output), patch.object(sync_rsync, 'get_service_logger'):
            self.assertEqual(sync_rsync.run_rsync_with_notifications('/src', '/dst', True, excluded_paths=paths), 0)
        self.assertFalse(filter_paths[0].exists())

    def test_copy_new_only_disables_deletion_and_existing_file_updates(self):
        with patch.object(sync_rsync.subprocess, 'Popen', return_value=_FakeProcess(0)) as popen, patch.object(sync_rsync, 'get_service_logger'), patch.object(sync_rsync.os, 'walk', return_value=iter(())):
            self.assertEqual(sync_rsync.run_rsync_with_notifications('/src', '/dst', True, copy_new_only=True), 0)
        command = popen.call_args.args[0]
        self.assertIn('--ignore-existing', command)
        self.assertIn('--partial-dir=.rsync-partial', command)
        self.assertNotIn('--delete', command)
        self.assertNotIn('--delete-delay', command)

    def test_conflicting_ancestors_are_excluded_before_rsync_runs(self):
        for kind in ('source-file', 'source-symlink', 'destination-file', 'destination-symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                source, destination = Path(root) / 'source', Path(root) / 'destination'
                source.mkdir()
                destination.mkdir()
                for endpoint, label in ((source, 'source'), (destination, 'destination')):
                    folder = endpoint / 'folder'
                    if kind == label + '-file':
                        folder.write_text('replacement')
                    elif kind == label + '-symlink':
                        folder.symlink_to(Path(root), target_is_directory=True)
                    else:
                        folder.mkdir()
                        (folder / 'flagged').write_text('preserve')
                def popen(command, **kwargs):
                    path = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--exclude-from='))
                    self.assertEqual(Path(path).read_bytes(), b'/folder\0/healthy-folder/flagged\0')
                    self.assertTrue(kwargs['start_new_session'])
                    return _FakeProcess(0)
                with patch.object(sync_rsync.subprocess, 'Popen', side_effect=popen), patch.object(sync_rsync, 'get_service_logger'):
                    result = sync_rsync.run_rsync_with_notifications(str(source), str(destination), True,
                        excluded_paths=('folder/flagged', 'healthy-folder/flagged'))
                self.assertEqual(result, 0)

    def test_timeout_is_an_operational_failure(self):
        with patch.object(sync_rsync.subprocess, 'Popen', return_value=_FakeProcess(0)), patch.object(sync_rsync, '_output_lines', side_effect=subprocess.TimeoutExpired('rsync', 14400)), patch.object(sync_rsync, 'get_service_logger', return_value=self.logger):
            with self.assertLogs(self.logger, level='ERROR') as logs:
                result = sync_rsync.run_rsync_with_notifications('/src', '/dst', True)
        self.assertEqual(result, 124)
        self.assertIn('timed out', '\n'.join(logs.output))

    def test_timed_out_job_is_checkpointed_and_later_backup_runs(self):
        from sync.service_tools import storage_ops

        first, second = _FakeProcess(0), _FakeProcess(0)
        def output(process):
            if process is first:
                raise subprocess.TimeoutExpired('rsync', 14400)
            return iter(())
        config = {'backup_specs': [['/source', '/backup', 'daily'],
                                   ['/other-source', '/other-backup', 'daily']]}
        with tempfile.TemporaryDirectory() as root, patch.object(storage_ops, 'STATE_FILE', str(Path(root) / 'state.json')), patch.object(storage_ops, 'load_setup_config', return_value=config), patch.object(storage_ops, 'parse_notification_args', return_value=[]), patch.object(storage_ops, 'get_service_logger'), patch.object(storage_ops.time, 'time', return_value=1_000_000), patch.object(sync_rsync, 'get_service_logger'), patch.object(sync_rsync.subprocess, 'Popen', side_effect=[first, second]) as popen, patch.object(sync_rsync, '_output_lines', side_effect=output):
            result = storage_ops.execute_storage_operations()
            state = storage_ops.load_last_run()
        self.assertEqual(popen.call_count, 2)
        self.assertFalse(result['syncs'][0]['success'])
        self.assertTrue(result['syncs'][1]['success'])
        self.assertNotIn('sync:/source:/backup', state)
        self.assertEqual(state['sync:/other-source:/other-backup'], 1_000_000)
        self.assertEqual(state['_attempts']['sync:/source:/backup']['retry_after'], 1_003_600)

    def test_copy_new_only_skips_directories_blocked_by_existing_entries(self):
        with tempfile.TemporaryDirectory() as root:
            source, destination = Path(root) / 'source', Path(root) / 'destination'
            source.mkdir()
            destination.mkdir()
            for name in ('file', 'link', 'dangling', 'normal', 'new', 'offline'):
                (source / name).mkdir()
            (destination / 'file').write_text('preserve')
            (destination / 'link').symlink_to(source / 'normal', target_is_directory=True)
            (destination / 'dangling').symlink_to(source / 'absent', target_is_directory=True)
            (destination / 'normal').mkdir()
            (destination / 'offline').mkdir()
            # The offline exclusion must prune traversal before os.walk enters it.
            walk = os.walk
            visited = []
            def inventory(*args, **kwargs):
                for entry in walk(*args, **kwargs):
                    visited.append(entry[0])
                    yield entry
            def popen(command, **kwargs):
                path = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--exclude-from='))
                self.assertEqual(set(Path(path).read_bytes().split(b'\0')[:-1]),
                                 {b'/file', b'/link', b'/dangling', b'/offline'})
                return _FakeProcess(0)
            with patch.object(sync_rsync.os, 'walk', side_effect=inventory), patch.object(sync_rsync.subprocess, 'Popen', side_effect=popen), patch.object(sync_rsync, 'get_service_logger'):
                result = sync_rsync.run_rsync_with_notifications(str(source), str(destination), True,
                    excluded_paths=('offline',), copy_new_only=True)
            self.assertEqual(result, 0)
            self.assertNotIn(str(source / 'offline'), visited)
            self.assertNotIn(str(source / 'file'), visited)
            self.assertIn(str(source / 'normal'), visited)

    def test_invalid_exclusions_never_start_rsync(self):
        for path in ('/absolute', '../outside', 'nested/../outside', '.', 'line\nbreak'):
            with self.subTest(path=path), patch.object(sync_rsync.subprocess, 'Popen') as popen, patch.object(sync_rsync, 'get_service_logger'):
                self.assertEqual(sync_rsync.run_rsync_with_notifications('/src', '/dst', True, excluded_paths=(path,)), 1)
                popen.assert_not_called()

    @patch("sync.service_tools.sync_rsync.datetime")
    @patch("sync.service_tools.sync_rsync.subprocess.Popen")
    @patch("sync.service_tools.sync_rsync.get_service_logger")
    def test_logs_structured_start_and_completion(self, mock_get_logger, mock_popen, mock_datetime):
        mock_get_logger.return_value = self.logger
        mock_popen.return_value = _FakeProcess(
            0,
            stdout="Number of files transferred: 2\nTotal file size: 10485760 bytes\n",
        )
        mock_datetime.now.side_effect = [
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 0, 5),
        ]

        with self.assertLogs(self.logger, level="INFO") as logs:
            result = sync_rsync.run_rsync_with_notifications("/src", "/dst", suppress_notifications=True)

        self.assertEqual(result, 0)
        output = "\n".join(logs.output)
        self.assertIn("Starting sync | destination='/dst' source='/src'", output)
        self.assertIn(
            "Sync completed | destination='/dst' duration_seconds='5.0' files_transferred=2 source='/src' total_mb=10",
            output,
        )

    @patch("sync.service_tools.sync_rsync.datetime")
    @patch("sync.service_tools.sync_rsync.subprocess.Popen")
    @patch("sync.service_tools.sync_rsync.get_service_logger")
    def test_logs_structured_failure_and_error_output(self, mock_get_logger, mock_popen, mock_datetime):
        mock_get_logger.return_value = self.logger
        mock_popen.return_value = _FakeProcess(23, stderr="rsync exploded")
        mock_datetime.now.side_effect = [
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 0, 3),
        ]

        with self.assertLogs(self.logger, level="ERROR") as logs:
            result = sync_rsync.run_rsync_with_notifications("/src", "/dst", suppress_notifications=True)

        self.assertEqual(result, 23)
        output = "\n".join(logs.output)
        self.assertIn(
            "Sync failed | destination='/dst' duration_seconds='3.0' exit_code=23 source='/src'",
            output,
        )
        self.assertIn("Rsync error output | stderr='rsync exploded'", output)

    @patch("lib.notifications.send_notification_safe", side_effect=RuntimeError("notify boom"))
    @patch("lib.notifications.parse_notification_args", return_value=["cfg"])
    @patch("lib.machine_state.load_setup_config", return_value={"notify_specs": [["mailbox", "ops@example.com"]]})
    @patch("sync.service_tools.sync_rsync.datetime")
    @patch("sync.service_tools.sync_rsync.subprocess.Popen")
    @patch("sync.service_tools.sync_rsync.get_service_logger")
    def test_logs_structured_notification_failure(
        self,
        mock_get_logger,
        mock_popen,
        mock_datetime,
        _load_setup,
        _parse_notify,
        _send_notification,
    ):
        mock_get_logger.return_value = self.logger
        mock_popen.return_value = _FakeProcess(
            0,
            stdout="Number of files transferred: 1\nTotal file size: 1024 bytes\n",
        )
        mock_datetime.now.side_effect = [
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 0, 2),
        ]

        with self.assertLogs(self.logger, level="ERROR") as logs:
            result = sync_rsync.run_rsync_with_notifications("/src", "/dst")

        self.assertEqual(result, 0)
        self.assertIn("Failed to send success notification | error='notify boom'", "\n".join(logs.output))


class TestRsyncDeadline(unittest.TestCase):
    def test_deadline_stops_stalled_and_noisy_process_groups(self):
        for noisy in (False, True):
            with self.subTest(noisy=noisy):
                process = Mock(pid=12345)
                # A dead leader must not let descendants hold the pipes forever.
                process.poll.return_value = 0
                readable = [process.stdout] if noisy else []
                with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 2), patch.object(sync_rsync.time, 'monotonic', side_effect=[0, 0, 1, 2]), patch.object(sync_rsync.select, 'select', return_value=(readable, [], [])) as select, patch.object(sync_rsync.os, 'read', return_value=b'progress\n'), patch.object(sync_rsync.os, 'killpg') as killpg:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        list(sync_rsync._output_lines(process))
                self.assertEqual(select.call_count, 2)
                self.assertEqual(killpg.call_args_list, [call(12345, signal.SIGTERM), call(12345, signal.SIGKILL)])
                process.stdout.close.assert_called_once()
                process.stderr.close.assert_called_once()

    def test_closed_pipes_do_not_allow_an_unbounded_process_wait(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired('rsync', 8),
                                   subprocess.TimeoutExpired('rsync', 5), 0]
        with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 10), patch.object(sync_rsync.time, 'monotonic', side_effect=[100, 101, 102]), patch.object(sync_rsync.select, 'select', return_value=([process.stdout, process.stderr], [], [])), patch.object(sync_rsync.os, 'read', return_value=b''), patch.object(sync_rsync.os, 'killpg') as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                list(sync_rsync._output_lines(process))
        self.assertEqual(process.wait.call_args_list, [call(timeout=8), call(timeout=5), call(timeout=5)])
        self.assertEqual(killpg.call_args_list[-1], call(12345, signal.SIGKILL))
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()


class TestSyncPathSafety(unittest.TestCase):
    def test_output_reader_drains_stderr_while_stdout_has_no_newline(self):
        # A real local child is the pipe fixture; no rsync or host mutations.
        process = subprocess.Popen(
            [sys.executable, "-c", "import os; os.write(1, b'partial'); os.write(2, b'x' * 262144); os.write(1, b' done\\n')"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            lines = list(sync_rsync._output_lines(process))
            self.assertEqual(process.returncode, 0)
            self.assertIn((True, "partial done"), lines)
            self.assertEqual(sum(len(line) for stdout, line in lines if not stdout), 262144)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_overlap_and_symlink_aliases_never_launch_rsync(self):
        from sync.sync_steps import parse_sync_spec

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.mkdir(source)
            alias = os.path.join(root, "alias")
            os.symlink(source, alias)
            for destination in (source, root, os.path.join(source, "mirror"), alias):
                with self.subTest(destination=destination), patch.object(sync_rsync.subprocess, "Popen") as popen, patch.object(sync_rsync, "get_service_logger"):
                    self.assertEqual(sync_rsync.run_rsync_with_notifications(source, destination, True), 1)
                    popen.assert_not_called()
            with self.assertRaisesRegex(ValueError, "equal or nested"):
                parse_sync_spec([source, source + "/../source/mirror", "daily"])

    def test_file_source_is_rejected_before_rsync(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "file")
            with open(source, "w") as stream:
                stream.write("data")
            with patch.object(sync_rsync.subprocess, "Popen") as popen, patch.object(sync_rsync, "get_service_logger"):
                self.assertEqual(sync_rsync.run_rsync_with_notifications(source, root + "-dest", True), 1)
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
