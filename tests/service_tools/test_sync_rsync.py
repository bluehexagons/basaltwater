"""Tests for sync.service_tools.sync_rsync."""

from __future__ import annotations

import logging
import os
import sys
import unittest
import tempfile
import subprocess
import signal
import io
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, call, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from sync.service_tools import sync_rsync
from lib.runtime_config import RuntimeConfig
from lib.state_read import StateReadError


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
        saved = patch('lib.machine_state.load_setup_config', return_value={})
        saved.start()
        self.addCleanup(saved.stop)
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
        report = patch.object(sync_rsync, '_read_worker_report', return_value=sync_rsync.SyncPrecautions())
        report.start()
        self.addCleanup(report.stop)

    def test_literal_exclusions_use_anonymous_stdin_filters(self):
        paths = ("offline [x]", "file[1]?.bin", "literal\\name", "wild\\*.bin", "nested/+ file", "#comment")
        expected = b'/offline \\[x]\0/file\\[1]\\?.bin\0/literal\\name\0/wild\\\\\\*.bin\0/nested/+ file\0/#comment\0'

        with sync_rsync._rsync_command('/src', '/dst', paths, False) as (command, filters):
            self.assertIn('--delete-delay', command)
            self.assertIn('--from0', command)
            self.assertEqual([arg for arg in command if arg.startswith('--exclude-from=')], ['--exclude-from=-'])
            self.assertEqual(filters.read(), expected)
            self.assertEqual(os.fstat(filters.fileno()).st_mode & 0o777, 0o600)
            self.assertEqual(os.fstat(filters.fileno()).st_nlink, 0)
            self.assertFalse(os.get_inheritable(filters.fileno()))
        self.assertTrue(filters.closed)

    def test_filter_stream_closes_on_failure_without_leaving_a_path(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(sync_rsync.tempfile, 'tempdir', root):
                with self.assertRaisesRegex(RuntimeError, 'exec failed'):
                    with sync_rsync._rsync_command('/src', '/dst', ('offline [x]',), False) as (_command, filters):
                        self.assertEqual(os.listdir(root), [])
                        raise RuntimeError('exec failed')
            self.assertTrue(filters.closed)
            self.assertEqual(os.listdir(root), [])

    def test_copy_new_only_disables_deletion_and_existing_file_updates(self):
        with patch.object(sync_rsync.os, 'walk', return_value=iter(())):
            with sync_rsync._rsync_command('/src', '/dst', (), True) as (command, filters):
                self.assertIn('--ignore-existing', command)
                self.assertIn('--partial-dir=.rsync-partial', command)
                self.assertIn('--exclude=.rsync-partial', command)
                self.assertIn('--partial', command)
                self.assertNotIn('--inplace', command)
                self.assertNotIn('--delete', command)
                self.assertNotIn('--delete-delay', command)
                self.assertIsNone(filters)
                self.assertNotIn('--from0', command)
                self.assertNotIn('--exclude-from=-', command)

    def test_ordinary_sync_uses_separate_partial_storage_and_excludes_it(self):
        with sync_rsync._rsync_command('/src', '/dst', (), False) as (command, filters):
            self.assertIn('--delete', command)
            self.assertIn('--delete-delay', command)
            self.assertIn('--partial', command)
            self.assertIn('--partial-dir=.rsync-partial', command)
            self.assertIn('--exclude=.rsync-partial', command)
            self.assertNotIn('--inplace', command)
            self.assertIsNone(filters)

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
                with sync_rsync._rsync_command(str(source), str(destination),
                        ('folder/flagged', 'healthy-folder/flagged'), False) as (_command, filters):
                    self.assertEqual(filters.read(), b'/folder\0/healthy-folder/flagged\0')

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
            with patch.object(sync_rsync.os, 'walk', side_effect=inventory):
                with sync_rsync._rsync_command(str(source), str(destination), ('offline',), True) as (_command, filters):
                    self.assertEqual(set(filters.read().split(b'\0')[:-1]),
                                     {b'/file', b'/link', b'/dangling', b'/offline'})
            self.assertNotIn(str(source / 'offline'), visited)
            self.assertNotIn(str(source / 'file'), visited)
            self.assertIn(str(source / 'normal'), visited)

    def test_invalid_exclusions_never_start_rsync(self):
        for path in ('/absolute', '../outside', 'nested/../outside', '.', 'line\nbreak'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                with sync_rsync._rsync_command('/src', '/dst', (path,), False):
                    self.fail('Invalid filter accepted')

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


class TestMountNamespace(unittest.TestCase):
    """Model host unmounts without creating namespaces, mounts, or real rsyncs."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source, self.destination = Path(temporary.name) / 'source', Path(temporary.name) / 'backup'
        self.source.mkdir()
        self.destination.mkdir()
        self.host_mounts = set()
        self.in_worker = False
        self.before_namespace = lambda: None
        self.after_validation = lambda _command: None
        self.mountinfo = '36 25 0:32 / / rw - ext4 /dev/root rw\n'
        self.child_namespace = 'mnt:[child]'
        self.config = {}
        readlink = os.readlink
        for target, kwargs in (
            ('lib.machine_state.load_setup_config', {'side_effect': lambda: self.config}),
            ('sync.service_tools.sync_rsync.get_service_logger', {}),
            ('sync.service_tools.sync_rsync.os.readlink', {'side_effect': lambda path: (
                self.child_namespace if self.in_worker else 'mnt:[parent]'
            ) if path == '/proc/self/ns/mnt' else (
                'mnt:[parent]' if path == f'/proc/{os.getppid()}/ns/mnt' else readlink(path)
            )}),
            ('sync.service_tools.sync_rsync.subprocess.Popen', {'side_effect': self.spawn}),
            ('sync.service_tools.sync_rsync._output_lines', {'side_effect': lambda process: [(True, process.stdout.read()), (False, process.stderr.read())]}),
        ):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        execute = patch.object(sync_rsync.os, 'execv', side_effect=self.execute)
        self.exec = execute.start()
        self.addCleanup(execute.stop)
        redirect = patch.object(sync_rsync.os, 'dup2', side_effect=self.redirect_stdin)
        self.dup2 = redirect.start()
        self.addCleanup(redirect.stop)

    def spawn(self, command, **kwargs):
        self.assertEqual(command[:6], ['/usr/bin/unshare', '--mount', '--propagation', 'private', '--', sys.executable])
        self.assertEqual(command[-2], '--mount-worker')
        self.assertEqual(kwargs['pass_fds'], (int(command[-1]),))
        self.assertEqual(os.fstat(kwargs['pass_fds'][0]).st_nlink, 0)
        self.assertEqual(os.fstat(kwargs['pass_fds'][0]).st_mode & 0o777, 0o600)
        self.assertFalse(os.get_inheritable(kwargs['pass_fds'][0]))
        # Popen passes a duplicate into the child; closing it cannot close the
        # parent's copy in this in-process worker fixture.
        report_fd = os.dup(kwargs['pass_fds'][0])
        self.assertTrue(kwargs['start_new_session'])
        request = json.load(kwargs['stdin'])
        self.request = request
        self.before_namespace()
        # unshare snapshots the tree and recursively detaches propagation
        # before Python can perform any mount validation or traversal.
        self.view = self.host_mounts.copy()
        mountinfo = self.mountinfo
        for root in sorted(self.view):
            escaped = root.replace('\\', '\\134').replace(' ', '\\040')
            mountinfo += f'37 36 0:33 / {escaped} rw - ext4 /dev/data rw\n'
        self.in_worker = True
        self.filter_input = None
        errors = io.StringIO()
        try:
            with patch.object(sync_rsync.sys, 'stdin', io.StringIO(json.dumps(request))), patch.object(sync_rsync.sys, 'stderr', errors), patch.object(sync_rsync, 'open', mock_open(read_data=mountinfo), create=True), patch.object(sys, 'argv', ['sync_rsync.py', '--mount-worker', str(report_fd)]):
                result = sync_rsync.main()
        finally:
            self.in_worker = False
            try:
                os.close(report_fd)
            except OSError:
                pass  # The worker closes the descriptor before exec.
        return _FakeProcess(result, stderr=errors.getvalue())

    def redirect_stdin(self, descriptor, target):
        self.assertTrue(self.in_worker)
        self.assertEqual(target, 0)
        self.assertEqual(sync_rsync.sys.stdin.read(), '')
        self.assertFalse(os.get_inheritable(descriptor))
        self.filter_input = os.read(descriptor, 65536)

    def execute(self, executable, command):
        self.assertTrue(self.in_worker)
        self.assertEqual(executable, '/usr/bin/rsync')
        self.after_validation(command)

    def run_sync(self, **kwargs):
        return sync_rsync.run_rsync_with_notifications(str(self.source), str(self.destination), True, **kwargs)

    def test_standalone_guards_endpoints_and_nested_smb_and_storage_mounts(self):
        for smb in (False, True):
            for endpoint in (self.source, self.destination):
                for nested in (False, True):
                    root = endpoint / 'disk' if nested else endpoint
                    self.config = ({'smb_mounts': [[str(root), 'host', 'secret-reference', 'share']]} if smb else
                                   {'storage_mounts': [['data', str(root)]]})
                    with self.subTest(smb=smb, endpoint=endpoint, nested=nested):
                        self.exec.reset_mock()
                        with patch.object(sys, 'argv', ['sync_rsync.py', str(self.source), str(self.destination)]):
                            result = sync_rsync.main()
                        self.assertEqual(result, 0 if nested else 1)
                        if nested:
                            self.exec.assert_called_once()
                        else:
                            self.exec.assert_not_called()
                        self.assertNotIn('secret-reference', json.dumps(self.request))

    def test_unmount_after_preflight_before_namespace_is_revalidated(self):
        for endpoint in (self.source, self.destination):
            for nested in (False, True):
                root = str(endpoint / 'disk' if nested else endpoint)
                self.config = {'storage_mounts': [['data', root]]}
                self.host_mounts = {root}
                self.before_namespace = lambda: self.host_mounts.clear()
                filters = []
                def inspect(command):
                    self.assertIn('--exclude-from=-', command)
                    filters.append(self.filter_input)
                self.after_validation = inspect
                with self.subTest(endpoint=endpoint, nested=nested):
                    self.exec.reset_mock()
                    precautions = sync_rsync.SyncPrecautions()
                    self.assertEqual(self.run_sync(precautions=precautions), 0 if nested else 1)
                    if nested:
                        self.assertEqual(filters, [b'/disk\0'])
                        self.assertTrue(precautions.retry_required)
                        self.assertIn('unavailable configured mounts: disk', precautions.warning)
                    else:
                        self.exec.assert_not_called()

    def test_absent_nested_mount_filters_reach_stdin_before_exec(self):
        for endpoint in (self.source, self.destination):
            with self.subTest(endpoint=endpoint):
                self.config = {'smb_mounts': [[str(endpoint / 'offline [x]'), 'host', 'secret-reference', 'share']]}
                def inspect(command):
                    self.assertIn('--from0', command)
                    self.assertEqual([arg for arg in command if arg.startswith('--exclude-from=')], ['--exclude-from=-'])
                    self.assertEqual(self.filter_input, b'/finding \\[1]\\?.bin\0/wild\\*.bin\0/offline \\[x]\0')
                self.after_validation = inspect
                self.dup2.reset_mock()
                self.exec.reset_mock()
                self.assertEqual(self.run_sync(excluded_paths=('finding [1]?.bin', 'wild*.bin')), 0)
                self.dup2.assert_called_once()
                self.exec.assert_called_once()
                self.assertNotIn('secret-reference', json.dumps(self.request))

    def test_filter_handoff_or_exec_failure_closes_streams_and_refuses_sync(self):
        self.config = {'storage_mounts': [['data', str(self.source / 'offline [x]')]]}
        temporary_file = sync_rsync.tempfile.TemporaryFile
        for operation in (self.dup2, self.exec):
            streams = []
            def capture_stream(*args, **kwargs):
                stream = temporary_file(*args, **kwargs)
                streams.append(stream)
                return stream
            original = operation.side_effect
            operation.side_effect = OSError('handoff failed')
            try:
                with self.subTest(operation=operation), patch.object(sync_rsync.tempfile, 'TemporaryFile', side_effect=capture_stream):
                    self.exec.reset_mock()
                    self.assertEqual(self.run_sync(), 1)
                    self.assertEqual(len(streams), 3)  # Report, request and generated filters.
                    self.assertTrue(all(stream.closed for stream in streams))
                    if operation is self.dup2:
                        self.exec.assert_not_called()
                    else:
                        self.exec.assert_called_once()
            finally:
                operation.side_effect = original

    def test_mount_view_survives_host_unmount_after_child_validation(self):
        for endpoint in (self.source, self.destination):
            for nested in (False, True):
                root = str(endpoint / 'disk' if nested else endpoint)
                self.config = {'storage_mounts': [['data', root]]}
                self.host_mounts = {root}
                def traverse(command):
                    self.host_mounts.clear()
                    self.assertIn(root, self.view)
                    self.assertIn('--delete', command)
                    self.assertFalse(any(arg.startswith('--exclude-from=') for arg in command))
                    self.assertIsNone(self.filter_input)
                    self.dup2.assert_not_called()
                self.after_validation = traverse
                with self.subTest(endpoint=endpoint, nested=nested):
                    self.assertEqual(self.run_sync(), 0)

    def test_invalid_or_missing_saved_state_cannot_start_worker(self):
        invalid = [None, StateReadError('/state/setup.json', 'malformed'),
                   {'storage_mounts': [['incomplete']]}, {'smb_mounts': 'invalid'}]
        invalid.extend({field: value} for field in ('storage_mounts', 'smb_mounts')
                       for value in (False, 0, 0.0, '', {}))
        for saved in invalid:
            with self.subTest(saved=saved), patch('lib.machine_state.load_setup_config', side_effect=saved if isinstance(saved, Exception) else None, return_value=saved), patch.object(sync_rsync.subprocess, 'Popen') as launch:
                self.assertEqual(self.run_sync(), 1)
                launch.assert_not_called()

    def test_optional_saved_mount_fields_preserve_empty_semantics(self):
        for saved in ({}, {'storage_mounts': None, 'smb_mounts': None},
                      {'storage_mounts': [], 'smb_mounts': []}):
            self.config = saved
            with self.subTest(saved=saved):
                self.exec.reset_mock()
                self.assertEqual(self.run_sync(), 0)
                self.exec.assert_called_once()

    def test_standalone_worker_precautions_reach_warning_notification(self):
        self.config = {'storage_mounts': [['data', str(self.source / 'offline')]],
                       'notify_specs': [['mailbox', 'ops@example.com']]}
        with patch('lib.notifications.parse_notification_args', return_value=['cfg']), patch('lib.notifications.send_notification_safe') as notify:
            self.assertEqual(sync_rsync.run_rsync_with_notifications(str(self.source), str(self.destination)), 0)
        self.assertEqual(notify.call_args.kwargs['status'], 'warning')
        self.assertEqual(notify.call_args.kwargs['state'], 'firing')
        self.assertIn('offline', notify.call_args.kwargs['details'])

    def test_report_loss_or_corruption_cannot_claim_success_or_rerun(self):
        spawn = self.spawn
        for payload in (b'', b'{broken', b'x' * (sync_rsync.WORKER_REPORT_LIMIT + 1)):
            def corrupt(command, **kwargs):
                process = spawn(command, **kwargs)
                descriptor = kwargs['pass_fds'][0]
                os.ftruncate(descriptor, 0)
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, payload)
                # Report-shaped filenames/output must not replace the missing
                # dedicated report or make an invalid one look successful.
                process.stdout = _FakeStream(json.dumps({'version': 1, 'unavailable_count': 0, 'sample': []}))
                return process
            with self.subTest(payload=payload[:20]), patch.object(sync_rsync.subprocess, 'Popen', side_effect=corrupt) as launch:
                self.exec.reset_mock()
                self.assertEqual(self.run_sync(), 1)
                self.exec.assert_called_once()
                launch.assert_called_once()

    def test_worker_report_is_bounded_without_truncating_filter_paths(self):
        paths = ['/'.join(['\U0001f4be' * 50] * 5) + str(index) for index in range(9)]
        self.config = {'storage_mounts': [[str(index), str(self.source / path)]
                                          for index, path in enumerate(paths)]}
        precautions = sync_rsync.SyncPrecautions()
        self.assertEqual(self.run_sync(precautions=precautions), 0)
        self.assertEqual(len(self.filter_input.split(b'\0')[:-1]), 9)
        self.assertIn(os.fsencode(paths[-1]), self.filter_input)
        self.assertIn('Preserved 9 subtree(s)', precautions.warning)
        self.assertLess(len(precautions.warning), 1500)

    def test_worker_mount_loss_updates_scheduled_results_notifications_and_retry(self):
        from sync.service_tools import storage_ops

        for endpoint in (self.source, self.destination):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as state_dir:
                root = str(endpoint / 'disk')
                self.config = {'backup_specs': [[str(self.source), str(self.destination), 'weekly']],
                               'storage_mounts': [['data', root]]}
                self.host_mounts = {root}
                self.before_namespace = lambda: self.host_mounts.clear()
                op_id = storage_ops.get_sync_op_id(str(self.source), str(self.destination))
                with (
                    patch.object(storage_ops, 'load_setup_config', side_effect=lambda: self.config),
                    patch.object(storage_ops, 'STATE_FILE', str(Path(state_dir) / 'state.json')),
                    patch.object(storage_ops, 'get_service_logger'),
                    patch.object(storage_ops, 'parse_notification_args', return_value=['cfg']),
                    patch.object(storage_ops, 'send_notification_safe') as notify,
                    patch.object(storage_ops.time, 'time', return_value=1_000_000) as clock,
                    patch('lib.task_utils.os.path.ismount', side_effect=lambda path: path in self.host_mounts),
                ):
                    storage_ops.save_last_run({op_id: 1})
                    first = storage_ops.execute_storage_operations()
                    self.assertFalse(first['success'])
                    self.assertTrue(first['syncs'][0]['success'])
                    self.assertFalse(first['syncs'][0]['completed'])
                    self.assertIn('disk', first['syncs'][0]['warning'])
                    self.assertEqual(notify.call_args.kwargs['status'], 'error')
                    self.assertIn('incomplete and requires retry', notify.call_args.kwargs['details'])
                    state = storage_ops.load_last_run()
                    self.assertEqual(state[op_id], 1)
                    self.assertEqual(state['_attempts'][op_id]['retry_after'], 1_003_600)
                    self.assertEqual(self.filter_input, b'/disk\0')

                    clock.return_value += 3599
                    self.exec.reset_mock()
                    self.assertEqual(storage_ops.execute_storage_operations()['syncs'], [])
                    self.exec.assert_not_called()
                    clock.return_value += 1
                    repeated = storage_ops.execute_storage_operations()
                    self.assertFalse(repeated['syncs'][0]['completed'])
                    self.assertEqual(self.filter_input, b'/disk\0')
                    self.assertEqual(storage_ops.load_last_run()['_attempts'][op_id]['retry_after'], 1_010_800)

                    self.before_namespace = lambda: None
                    self.host_mounts = {root}
                    clock.return_value = 1_010_800
                    recovered = storage_ops.execute_storage_operations()
                    self.assertTrue(recovered['syncs'][0]['completed'])
                    self.assertEqual(recovered['syncs'][0]['warning'], '')
                    self.assertEqual(notify.call_args.kwargs['status'], 'good')
                    self.assertEqual(storage_ops.load_last_run(), {op_id: 1_010_800})
                    clock.return_value += 3600
                    self.assertEqual(storage_ops.execute_storage_operations()['syncs'], [])

    def test_invalid_report_schema_is_rejected(self):
        base = {'version': 2, 'unavailable_count': 1, 'sample': ['disk'],
                'warning': '', 'retry_required': True}
        invalid = [None, [], {}, {**base, 'version': True}, {**base, 'version': 1},
                   {**base, 'unavailable_count': True}, {**base, 'unavailable_count': -1},
                   {**base, 'sample': 'disk'}, {**base, 'sample': []},
                   {**base, 'sample': [None]}, {**base, 'sample': ['x' * (sync_rsync.WORKER_SAMPLE_PATH_LIMIT + 1)]},
                   {**base, 'retry_required': False}, {**base, 'warning': 'x' * (sync_rsync.WORKER_REPORT_LIMIT + 1)},
                   {**base, 'extra': 'unsupported'}]
        for report in invalid:
            with self.subTest(report=report), self.assertRaises(ValueError):
                sync_rsync._read_worker_report(io.BytesIO(json.dumps(report).encode()))

    def test_namespace_failure_has_no_fallback_and_reports_requirement(self):
        for failure in (FileNotFoundError('unshare missing'), PermissionError('not permitted'), _FakeProcess(1, stderr='unshare: unshare failed: Operation not permitted')):
            logger = Mock()
            with self.subTest(failure=failure), patch.object(sync_rsync, 'get_service_logger', return_value=logger), patch.object(sync_rsync.subprocess, 'Popen', side_effect=failure if isinstance(failure, Exception) else None, return_value=failure) as launch:
                self.assertEqual(self.run_sync(), 1)
                launch.assert_called_once()
                self.exec.assert_not_called()
                self.assertIn('CAP_SYS_ADMIN', str(logger.log.call_args_list))

    def test_worker_rejects_same_namespace_and_nonprivate_propagation(self):
        for namespace, optional in (('mnt:[parent]', ''), ('mnt:[child]', 'shared:1 '), ('mnt:[child]', 'master:1 '), ('mnt:[child]', 'propagate_from:1 ')):
            self.child_namespace = namespace
            self.mountinfo = f'36 25 0:32 / / rw {optional}- ext4 /dev/root rw\n'
            with self.subTest(namespace=namespace, optional=optional):
                self.assertEqual(self.run_sync(), 1)
                self.exec.assert_not_called()

    def test_autofs_placeholder_is_unavailable_until_real_filesystem_is_in_snapshot(self):
        for endpoint in (self.source, self.destination):
            for nested in (False, True):
                root = str(endpoint / 'disk name' if nested else endpoint)
                self.config = {'smb_mounts': [[root]]}
                escaped = root.replace(' ', '\\040')
                self.mountinfo = f'36 25 0:32 / / rw - ext4 /dev/root rw\n38 36 0:34 / {escaped} rw - autofs systemd-1 rw\n'
                self.host_mounts.clear()
                with self.subTest(endpoint=endpoint, nested=nested), patch.object(sync_rsync.os.path, 'ismount', return_value=True):
                    self.exec.reset_mock()
                    self.assertEqual(self.run_sync(), 0 if nested else 1)
                    if nested:
                        self.exec.assert_called_once()
                    else:
                        self.exec.assert_not_called()
                    # A real filesystem stacked on autofs is an active mount.
                    self.host_mounts.add(root)
                    self.assertEqual(self.run_sync(), 0)

    def test_undeclared_mnt_endpoint_requires_real_mounted_ancestor(self):
        self.destination = Path('/mnt/offline/new-backup')
        self.mountinfo += '38 36 0:34 / /mnt/offline rw - autofs systemd-1 rw\n'
        with patch.object(sync_rsync.os.path, 'realpath', side_effect=lambda path: os.fspath(path)):
            self.assertEqual(self.run_sync(), 1)
            self.exec.assert_not_called()
            self.host_mounts.add('/mnt/offline')
            self.assertEqual(self.run_sync(), 0)

    def test_setup_uses_current_config_and_probes_only_inside_namespace(self):
        self.config = None  # A new setup has not saved configuration yet.
        config = RuntimeConfig.from_dict({'username': 'agent', 'smb_mounts': [[str(root)] for root in (self.source, self.destination)]})
        self.host_mounts = {str(self.source), str(self.destination)}
        created = set()
        def ensure(path, username):
            self.assertTrue(self.in_worker)
            self.assertEqual(username, 'agent')
            created.add(path)
        def probe(path, *, writable=True):
            self.assertTrue(self.in_worker)
            self.assertIn(path, created)
            self.assertEqual(writable, path == str(self.destination))
            return True
        with patch('lib.machine_state.load_setup_config', side_effect=AssertionError('stale config lookup')), patch.object(sync_rsync, 'ensure_directory', side_effect=ensure), patch.object(sync_rsync, 'validate_smb_connectivity', side_effect=probe) as probes:
            self.assertEqual(self.run_sync(config=config, initial_setup=True), 0)
        self.assertEqual(probes.call_count, 2)

    def test_scheduled_integrity_metadata_is_checked_inside_namespace_worker(self):
        from sync.service_tools import storage_ops

        config = RuntimeConfig.from_dict({'scrub_specs': [
            [str(self.source), '.pardatabase', '10%', 'weekly'],
        ]})
        integrity = storage_ops.SyncIntegrity(
            excluded_paths=('suspect.bin',), warning='Preserved finding',
            copy_new_only=True,
        )

        def inspect(source, destination, current_config, *, mounted_roots=None):
            self.assertTrue(self.in_worker)
            self.assertEqual(source, str(self.source))
            self.assertEqual(destination, str(self.destination))
            self.assertEqual(current_config.scrub_specs[0][1], '.pardatabase')
            self.assertIsNotNone(mounted_roots)
            return integrity

        self.after_validation = lambda command: self.assertIn('--ignore-existing', command)
        precautions = sync_rsync.SyncPrecautions()
        with patch.object(storage_ops, 'validate_sync_integrity', side_effect=inspect) as check:
            self.assertEqual(self.run_sync(config=config, check_integrity=True,
                                           precautions=precautions), 0)
        check.assert_called_once()
        self.assertEqual(self.filter_input, b'/suspect.bin\0')
        self.assertEqual(precautions.warning, 'Preserved finding')
        self.assertFalse(precautions.retry_required)

    def test_findings_preserve_backup_paths_and_normal_completion_cadence(self):
        from sync.service_tools import scrub_findings, storage_ops

        database = self.source.parent / 'parity'
        database.mkdir()
        finding = scrub_findings.Findings(str(self.source), str(database))
        finding.record('data.bin', {'category': 'unrepairable'})
        self.config = {'backup_specs': [[str(self.source), str(self.destination), 'daily']],
                       'scrub_specs': [[str(self.source), str(database), '10%', 'weekly']]}
        op_id = storage_ops.get_sync_op_id(str(self.source), str(self.destination))
        now = 1_000_000.0
        state = {storage_ops.get_scrub_op_id(str(self.source), str(database)): now,
                 storage_ops.get_parity_op_id(str(self.source), str(database)): now,
                 '_attempts': {op_id: {'failures': 3, 'failed_at': 1, 'retry_after': 2}}}
        with (
            patch.object(storage_ops, 'load_setup_config', return_value=self.config),
            patch.object(storage_ops, 'load_last_run', return_value=state),
            patch.object(storage_ops, 'save_last_run') as save,
            patch.object(storage_ops.time, 'time', return_value=now),
            patch.object(storage_ops, 'parse_notification_args', return_value=['cfg']),
            patch.object(storage_ops, 'get_service_logger'),
            patch.object(storage_ops, 'send_notification_safe') as notify,
        ):
            result = storage_ops.execute_storage_operations()
        self.exec.assert_called_once()
        self.assertEqual(self.filter_input, b'/data.bin\0')
        self.assertTrue(result['success'])
        self.assertTrue(result['syncs'][0]['completed'])
        self.assertEqual(save.call_args.args[0][op_id], now)
        self.assertNotIn('_attempts', save.call_args.args[0])
        self.assertEqual(notify.call_args.kwargs['status'], 'warning')
        self.assertIn('data.bin', notify.call_args.kwargs['details'])
        self.assertTrue(scrub_findings.Findings(str(self.source), str(database)).active('data.bin'))

    def test_scheduled_parity_destination_guard_prevents_rsync_inside_worker(self):
        from sync.service_tools import storage_ops

        self.config = {'scrub_specs': [[str(self.source), str(self.destination), '10%', 'weekly']]}
        self.assertEqual(self.run_sync(check_integrity=True), 1)
        self.exec.assert_not_called()

    def test_offline_endpoints_fail_but_independent_backup_uses_conservative_copy(self):
        from sync.service_tools import storage_ops

        offline, database = self.source.parent / 'offline', self.source.parent / 'parity'
        self.config = {
            'backup_specs': [[str(self.source), str(offline), 'daily'],
                             [str(self.source), str(self.destination), 'daily']],
            'scrub_specs': [[str(self.source), str(database), '10%', 'weekly']],
            'storage_mounts': [['offline', str(offline)], ['available', str(self.destination)],
                               ['parity', str(database)]],
        }
        self.host_mounts = {str(self.destination)}
        self.after_validation = lambda command: self.assertIn('--ignore-existing', command)
        with (
            patch.object(storage_ops, 'STATE_FILE', str(self.source.parent / 'state.json')),
            patch.object(storage_ops, 'load_setup_config', return_value=self.config),
            patch.object(storage_ops, 'parse_notification_args', return_value=[]),
            patch.object(storage_ops, 'get_service_logger'),
            patch('lib.task_utils.os.path.ismount', side_effect=lambda path: path in self.host_mounts),
            patch.object(storage_ops, 'run_scrub') as scrub,
        ):
            result = storage_ops.execute_storage_operations()
            state = storage_ops.load_last_run()
        self.assertFalse(result['success'])
        self.assertFalse(result['syncs'][0]['success'])
        self.assertTrue(result['syncs'][1]['completed'])
        self.assertIn('Copying new files only', result['syncs'][1]['warning'])
        self.exec.assert_called_once()
        self.assertIn(storage_ops.get_sync_op_id(str(self.source), str(self.destination)), state)
        failed_id = storage_ops.get_sync_op_id(str(self.source), str(offline))
        self.assertNotIn(failed_id, state)
        self.assertIn(failed_id, state['_attempts'])
        self.assertTrue(result['parity_updates'][0]['skipped'])
        scrub.assert_not_called()

    def test_service_exit_is_nonzero_for_incomplete_mount_coverage(self):
        from sync.service_tools import storage_ops

        self.config = {'backup_specs': [[str(self.source), str(self.destination), 'daily']],
                       'storage_mounts': [['data', str(self.source / 'offline')]]}
        with (
            patch.object(storage_ops, 'LOCK_FILE', str(self.source.parent / 'operations.lock')),
            patch.object(storage_ops, 'STATE_FILE', str(self.source.parent / 'state.json')),
            patch.object(storage_ops, 'load_setup_config', return_value=self.config),
            patch.object(storage_ops, 'parse_notification_args', return_value=[]),
            patch.object(storage_ops, 'get_service_logger'),
        ):
            self.assertEqual(storage_ops.main(), 1)
            state = storage_ops.load_last_run()
        self.exec.assert_called_once()
        self.assertEqual(self.filter_input, b'/offline\0')
        op_id = storage_ops.get_sync_op_id(str(self.source), str(self.destination))
        self.assertNotIn(op_id, state)
        self.assertIn(op_id, state['_attempts'])

    def test_inline_setup_computes_coverage_only_in_supervised_worker(self):
        from sync import sync_steps

        mount = str(self.source / 'disk')
        config = RuntimeConfig.from_dict({'username': 'agent', 'storage_mounts': [['data', mount]]})
        realpath = os.path.realpath

        def resolve(path):
            self.assertTrue(self.in_worker, 'Filesystem access outside supervised worker')
            return realpath(path)

        with (
            patch.object(sync_steps, 'create_operation_logger') as logger,
            patch.object(sync_rsync.os.path, 'realpath', side_effect=resolve),
            patch.object(sync_rsync, 'ensure_directory'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'Initial sync incomplete'):
                sync_steps.create_sync_service(config, [str(self.source), str(self.destination), 'daily'])
            self.assertEqual(self.filter_input, b'/disk\0')
            self.assertEqual(logger.return_value.complete.call_args.args[0], 'failed')
            # A mount restored before the namespace is captured is included;
            # no stale controller exclusion can hide it or mark partial work complete.
            self.before_namespace = lambda: self.host_mounts.add(mount)
            sync_steps.create_sync_service(config, [str(self.source), str(self.destination), 'daily'])
            self.assertIsNone(self.filter_input)
            self.assertEqual(logger.return_value.complete.call_args.args[0], 'completed')

    def test_blocked_integrity_metadata_fails_inside_worker_before_rsync(self):
        from sync.service_tools import storage_ops

        config = RuntimeConfig.from_dict({'scrub_specs': [
            [str(self.source), '.pardatabase', '10%', 'weekly'],
        ]})

        def blocked(*_args, **_kwargs):
            self.assertTrue(self.in_worker)
            raise TimeoutError('simulated blocked findings metadata')

        with patch.object(storage_ops, 'validate_sync_integrity', side_effect=blocked):
            self.assertEqual(self.run_sync(config=config, check_integrity=True), 1)
        self.exec.assert_not_called()

    def test_setup_mount_loss_prevents_preparation_and_failed_probe_prevents_rsync(self):
        config = RuntimeConfig.from_dict({'username': 'agent', 'smb_mounts': [[str(self.destination)]]})
        with patch.object(sync_rsync, 'ensure_directory') as mkdir, patch.object(sync_rsync, 'validate_smb_connectivity', return_value=False) as probe:
            self.assertEqual(self.run_sync(config=config, initial_setup=True), 1)
            mkdir.assert_not_called()
            probe.assert_not_called()
            self.host_mounts.add(str(self.destination))
            self.assertEqual(self.run_sync(config=config, initial_setup=True), 1)
            probe.assert_called_once_with(str(self.destination))
            self.exec.assert_not_called()

    def test_alias_overlap_and_file_source_are_rejected_inside_worker(self):
        alias = self.destination.parent / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        self.destination = alias
        self.assertEqual(self.run_sync(), 1)
        self.exec.assert_not_called()
        self.destination = alias.parent / 'backup'
        self.source = alias.parent / 'file'
        self.source.write_text('source is not a directory')
        self.assertEqual(self.run_sync(), 1)
        self.exec.assert_not_called()

    def test_interrupted_ordinary_sync_keeps_good_destination_and_separates_partial(self):
        destination_file = self.destination / 'important.bin'
        destination_file.write_bytes(b'previous complete contents')

        def interrupt(command):
            self.assertIn('--partial', command)
            self.assertIn('--partial-dir=.rsync-partial', command)
            self.assertIn('--exclude=.rsync-partial', command)
            self.assertNotIn('--inplace', command)
            partial = self.destination / '.rsync-partial' / destination_file.name
            partial.parent.mkdir()
            partial.write_bytes(b'incomplete replacement')
            raise OSError('simulated rsync interruption')

        self.after_validation = interrupt
        self.assertEqual(self.run_sync(), 1)
        self.assertEqual(destination_file.read_bytes(), b'previous complete contents')
        self.assertEqual(
            (self.destination / '.rsync-partial' / destination_file.name).read_bytes(),
            b'incomplete replacement',
        )


class TestRsyncDeadline(unittest.TestCase):
    def test_deadline_stops_stalled_and_noisy_process_groups(self):
        for noisy in (False, True):
            with self.subTest(noisy=noisy):
                process = Mock(pid=12345)
                # A dead leader must not let descendants hold the pipes forever.
                process.poll.return_value = 0
                readable = [process.stdout] if noisy else []
                with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 2), patch.object(sync_rsync, 'SYNC_PREFLIGHT_TIMEOUT_SECONDS', 2), patch.object(sync_rsync, '_wait_for_process_group_exit'), patch.object(sync_rsync.time, 'monotonic', side_effect=[0, 0, 1, 2]), patch.object(sync_rsync.select, 'select', return_value=(readable, [], [])) as select, patch.object(sync_rsync.os, 'read', return_value=b'progress\n'), patch.object(sync_rsync.os, 'killpg') as killpg:
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
        with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 10), patch.object(sync_rsync, 'SYNC_PREFLIGHT_TIMEOUT_SECONDS', 10), patch.object(sync_rsync, '_wait_for_process_group_exit'), patch.object(sync_rsync.time, 'monotonic', side_effect=[100, 101, 102]), patch.object(sync_rsync.select, 'select', return_value=([process.stdout, process.stderr], [], [])), patch.object(sync_rsync.os, 'read', return_value=b''), patch.object(sync_rsync.os, 'killpg') as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                list(sync_rsync._output_lines(process))
        self.assertEqual(process.wait.call_args_list, [call(timeout=8), call(timeout=5), call(timeout=None)])
        self.assertEqual(killpg.call_args_list[-1], call(12345, signal.SIGKILL))
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_preflight_deadline_is_separate_and_group_teardown_is_confirmed(self):
        process = Mock(pid=12345)
        wait_for_group = Mock()
        with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 3), \
             patch.object(sync_rsync, 'SYNC_PREFLIGHT_TIMEOUT_SECONDS', 120), \
             patch.object(sync_rsync.time, 'monotonic', side_effect=[0, 0, 3]), \
             patch.object(sync_rsync.select, 'select', return_value=([], [], [])) as select, \
             patch.object(sync_rsync.os, 'killpg') as killpg, \
             patch.object(sync_rsync, '_wait_for_process_group_exit', wait_for_group):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                list(sync_rsync._output_lines(process))
        self.assertEqual(caught.exception.cmd, 'rsync preflight')
        self.assertLessEqual(select.call_args.args[3], 0.5)
        self.assertEqual(killpg.call_args_list,
                         [call(12345, signal.SIGTERM), call(12345, signal.SIGKILL)])
        process.wait.assert_has_calls([call(timeout=5), call(timeout=None)])
        wait_for_group.assert_called_once_with(12345)

    def test_group_cleanup_keeps_supervision_through_interrupt_and_signal_errors(self):
        process = Mock(pid=12345)
        process.wait.side_effect = [
            subprocess.TimeoutExpired('rsync', 5),
            KeyboardInterrupt(),
            0,
        ]
        wait_for_group = Mock()
        with patch.object(sync_rsync.os, 'killpg', side_effect=PermissionError('signal denied')) as killpg, \
             patch.object(sync_rsync, '_wait_for_process_group_exit', wait_for_group):
            sync_rsync._terminate_process_group(process)
        self.assertEqual(killpg.call_args_list, [
            call(12345, signal.SIGTERM), call(12345, signal.SIGKILL),
        ])
        process.wait.assert_has_calls([call(timeout=5), call(timeout=None), call(timeout=None)])
        wait_for_group.assert_called_once_with(12345)

    def test_process_group_wait_releases_only_after_group_disappears(self):
        with patch.object(sync_rsync.os, 'killpg', side_effect=[None, ProcessLookupError]), \
             patch.object(sync_rsync.time, 'sleep') as sleep:
            sync_rsync._wait_for_process_group_exit(12345)
        sleep.assert_called_once_with(0.1)

    def test_clean_leader_exit_stops_residual_descendants_before_failure(self):
        process = Mock(pid=12345)
        with patch.object(sync_rsync, 'RSYNC_TIMEOUT_SECONDS', 5), \
             patch.object(sync_rsync, 'SYNC_PREFLIGHT_TIMEOUT_SECONDS', 5), \
             patch.object(sync_rsync.select, 'select', return_value=([process.stdout, process.stderr], [], [])), \
             patch.object(sync_rsync.os, 'read', side_effect=[b'', b'']), \
             patch.object(sync_rsync.os, 'killpg', side_effect=[None, None, None, ProcessLookupError]) as killpg:
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                list(sync_rsync._output_lines(process))
        self.assertIn('worker descendants remained', caught.exception.stderr)
        self.assertEqual(killpg.call_args_list, [
            call(12345, 0), call(12345, signal.SIGTERM),
            call(12345, signal.SIGKILL), call(12345, 0),
        ])
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()


class TestSyncPathSafety(unittest.TestCase):
    def test_controller_sync_validation_stays_lexical(self):
        process = _FakeProcess(0)
        with patch.object(sync_rsync.os.path, 'realpath', side_effect=AssertionError('realpath outside worker')), \
             patch.object(sync_rsync.os.path, 'isdir', side_effect=AssertionError('isdir outside worker')), \
             patch.object(sync_rsync.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(sync_rsync, '_output_lines', return_value=iter(())), \
             patch.object(sync_rsync, '_read_worker_report', return_value=sync_rsync.SyncPrecautions()), \
             patch.object(sync_rsync, 'get_service_logger'):
            result = sync_rsync.run_rsync_with_notifications(
                '/srv/source', '/mnt/destination', True,
                config=RuntimeConfig.from_dict({}),
            )
        self.assertEqual(result, 0)
        launch.assert_called_once()

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

    def test_lexical_overlap_never_launches_worker(self):
        from sync.sync_steps import parse_sync_spec

        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.mkdir(source)
            for destination in (source, root, os.path.join(source, "mirror")):
                with self.subTest(destination=destination), patch.object(sync_rsync.subprocess, "Popen") as popen, patch.object(sync_rsync, "get_service_logger"):
                    self.assertEqual(sync_rsync.run_rsync_with_notifications(source, destination, True,
                                     config=RuntimeConfig.from_dict({})), 1)
                    popen.assert_not_called()
            with self.assertRaisesRegex(ValueError, "equal or nested"):
                parse_sync_spec([source, source + "/../source/mirror", "daily"])

if __name__ == "__main__":
    unittest.main()
