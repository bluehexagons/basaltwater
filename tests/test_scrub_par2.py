"""Regression tests for scrub verification and setup failure semantics."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sync.service_tools import scrub_par2


class TestVerifyRepairOutcomes(unittest.TestCase):
    """verify_repair must distinguish "repair failed" from "no problem"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = self.tmp.name
        self.database = os.path.join(self.tmp.name, '.par2db')
        os.makedirs(self.database, exist_ok=True)
        self.file_path = os.path.join(self.directory, 'data.bin')
        with open(self.file_path, 'wb') as fh:
            fh.write(b'hello world')
        # Create a stub par2 file so verify_repair runs the verify branch.
        par2_base = os.path.join(self.database, 'data.bin.par2')
        with open(par2_base, 'wb') as fh:
            fh.write(b'')
        self.log_file = os.path.join(self.tmp.name, 'scrub.log')

    def test_no_par2_returns_ok(self):
        os.remove(os.path.join(self.database, 'data.bin.par2'))
        result = scrub_par2.verify_repair(self.file_path, self.directory, self.database, self.log_file)
        self.assertEqual(result, scrub_par2.VERIFY_OK)

    @patch('sync.service_tools.scrub_par2.subprocess.run')
    def test_volume_only_parity_is_verified(self, mock_run):
        base = os.path.join(self.database, 'data.bin.par2')
        volume = os.path.join(self.database, 'data.bin.vol00+01.par2')
        os.rename(base, volume)
        self.assertEqual(
            scrub_par2.verify_repair(self.file_path, self.directory, self.database, self.log_file),
            scrub_par2.VERIFY_OK,
        )
        self.assertIn(volume, mock_run.call_args.args[0])

    @patch('sync.service_tools.scrub_par2.subprocess.run')
    def test_verification_passes_returns_ok(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
        result = scrub_par2.verify_repair(self.file_path, self.directory, self.database, self.log_file)
        self.assertEqual(result, scrub_par2.VERIFY_OK)

    @patch('sync.service_tools.scrub_par2.subprocess.run')
    def test_repair_succeeds_returns_repaired(self, mock_run):
        # First call (verify) raises, second call (repair) succeeds.
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, 'par2 verify', output='corrupted'),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
        ]
        result = scrub_par2.verify_repair(self.file_path, self.directory, self.database, self.log_file)
        self.assertEqual(result, scrub_par2.VERIFY_REPAIRED)

    @patch('sync.service_tools.scrub_par2.subprocess.run')
    def test_repair_fails_returns_unrepairable(self, mock_run):
        # Both verify and repair raise.
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, 'par2 verify', output='corrupted'),
            subprocess.CalledProcessError(1, 'par2 repair', output='unrecoverable'),
        ]
        result = scrub_par2.verify_repair(self.file_path, self.directory, self.database, self.log_file)
        self.assertEqual(result, scrub_par2.VERIFY_UNREPAIRABLE)


class TestSetupFailurePropagation(unittest.TestCase):
    """Initial sync and scrub failures must reach the setup orchestrator."""

    def test_smb_sync_creates_new_destination_before_probing(self):
        from sync import sync_steps

        created = set()
        def ensure(path, _username):
            created.add(path)

        def probe(path, *, writable=True):
            if path == '/source':
                self.assertFalse(writable)
            else:
                self.assertIn(path, created)
            return True

        with patch.object(sync_steps, 'create_operation_logger'), patch.object(sync_steps, 'validate_filesystem_path'), patch.object(sync_steps, 'validate_mount_for_sync', return_value=True), patch.object(sync_steps, 'ensure_directory', side_effect=ensure), patch.object(sync_steps, 'check_path_on_smb_mount', return_value=True), patch.object(sync_steps, 'validate_smb_connectivity', side_effect=probe), patch.object(sync_steps, 'get_disk_usage_details', return_value={'usage_percent': 0}), patch('sync.service_tools.sync_rsync.run_rsync_with_notifications', return_value=0) as sync:
            sync_steps.create_sync_service(SimpleNamespace(username='agent'), ['/source', '/destination', 'daily'])
            sync.assert_called_once()

    def test_mount_failure_prevents_initial_work(self):
        from sync import scrub_steps, sync_steps

        for module, function, spec in (
            (sync_steps, sync_steps.create_sync_service, ['/mnt/a', '/mnt/b', 'daily']),
            (scrub_steps, scrub_steps.create_scrub_service, ['/mnt/a', '/mnt/b', '10%', 'daily']),
        ):
            for checks in ([False], [True, False]):
                with (
                    self.subTest(module=module.__name__, checks=checks),
                    patch.object(module, 'create_operation_logger'),
                    patch.object(module, 'validate_filesystem_path'),
                    patch.object(module, 'validate_mount_for_sync', side_effect=checks),
                    patch.object(module, 'ensure_directory') as mkdir,
                    patch('sync.service_tools.sync_rsync.run_rsync_with_notifications') as sync,
                    patch.object(scrub_par2, 'scrub_directory') as scrub,
                ):
                    with self.assertRaisesRegex(RuntimeError, 'mount is unavailable'):
                        function(SimpleNamespace(username='agent'), spec)
                    mkdir.assert_not_called()
                    sync.assert_not_called()
                    scrub.assert_not_called()

    def test_initial_sync_failure_is_logged_and_raised(self):
        from sync import sync_steps

        logger = Mock()
        with (
            patch.object(sync_steps, 'create_operation_logger', return_value=logger),
            patch.object(sync_steps, 'validate_filesystem_path') as validate_path,
            patch.object(sync_steps, 'validate_mount_for_sync') as validate_mount,
            patch.object(sync_steps, 'ensure_directory'),
            patch.object(sync_steps, 'check_path_on_smb_mount', return_value=False),
            patch.object(sync_steps, 'get_disk_usage_details') as disk_usage,
            patch(
                'sync.service_tools.sync_rsync.run_rsync_with_notifications',
                return_value=1,
            ) as run_sync,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Initial sync failed'):
                sync_steps.create_sync_service(
                    SimpleNamespace(username='agent'),
                    ['/source', '/destination', 'daily'],
                )

        self.assertEqual(validate_path.call_count, 2)
        self.assertEqual(validate_mount.call_count, 2)
        run_sync.assert_called_once_with(
            '/source',
            '/destination',
            suppress_notifications=True,
        )
        disk_usage.assert_not_called()
        logger.complete.assert_called_once()
        self.assertEqual(logger.complete.call_args.args[0], 'failed')


class TestScrubResultFailures(unittest.TestCase):
    def test_stale_volume_only_parity_is_recreated_and_counted_as_update(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source')
            database = os.path.join(directory, 'parity')
            os.mkdir(source)
            os.mkdir(database)
            data = os.path.join(source, 'data')
            volume = os.path.join(database, 'data.vol00+01.par2')
            for path in (data, volume):
                with open(path, 'w') as stream:
                    stream.write('data')
            os.utime(volume, (10, 10))
            os.utime(data, (20, 20))
            with patch.object(scrub_par2, 'log'), patch.object(scrub_par2, 'create_operation_logger'), patch.object(scrub_par2.subprocess, 'run') as run:
                result = scrub_par2.scrub_directory(source, database, 10, 'unused', verify=False, suppress_notifications=True)
                self.assertTrue(result['ok'])
                self.assertEqual(result['files_updated'], 1)
                self.assertEqual(result['files_created'], 0)
                run.assert_called_once()
                self.assertFalse(os.path.exists(volume))
                # Direct callers must also recreate stale parity without force=True.
                with open(volume, 'w') as stream:
                    stream.write('old')
                os.utime(volume, (10, 10))
                self.assertTrue(scrub_par2.create_par2(data, source, database, 10, 'unused'))
                self.assertEqual(run.call_count, 2)

    def test_cleanup_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source')
            database = os.path.join(directory, 'parity')
            os.mkdir(source)
            os.mkdir(database)
            with open(os.path.join(database, 'orphan.par2'), 'w') as stream:
                stream.write('parity')
            with patch.object(scrub_par2, 'log'), patch.object(scrub_par2, 'create_operation_logger'), patch.object(scrub_par2.os, 'remove', side_effect=PermissionError('cannot remove')):
                with self.assertRaises(PermissionError):
                    scrub_par2.scrub_directory(source, database, 10, 'unused', suppress_notifications=True)

    def test_fifo_in_source_aborts_before_parity_mutation(self):
        with tempfile.TemporaryDirectory() as source:
            os.mkfifo(os.path.join(source, 'pipe'))
            with patch.object(scrub_par2, 'log'), patch.object(scrub_par2, 'create_operation_logger'), patch.object(scrub_par2, 'create_par2') as create:
                with self.assertRaisesRegex(ValueError, 'non-regular'):
                    scrub_par2.scrub_directory(source, os.path.join(source, 'db'), 10, 'unused', suppress_notifications=True)
                create.assert_not_called()

    def test_incomplete_or_symlink_scan_prevents_parity_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'data')
            database = os.path.join(tmp, 'db')
            os.mkdir(source)
            os.mkdir(database)
            link = os.path.join(source, 'escape')
            os.symlink(tmp, link)
            with (
                patch.object(scrub_par2, 'create_operation_logger'),
                patch.object(scrub_par2, 'log'),
                patch.object(scrub_par2, 'create_par2') as create,
                patch.object(scrub_par2, '_cleanup_orphan_par2') as cleanup,
            ):
                with self.assertRaisesRegex(ValueError, 'Symlinks'):
                    scrub_par2.scrub_directory(source, database, 10, 'unused', suppress_notifications=True)
                os.unlink(link)

                def broken_walk(path, *, onerror):
                    yield path, [], []
                    onerror(PermissionError('unreadable subtree'))

                with patch.object(scrub_par2.os, 'walk', side_effect=broken_walk):
                    with self.assertRaises(PermissionError):
                        scrub_par2.scrub_directory(source, database, 10, 'unused', suppress_notifications=True)
                create.assert_not_called()
                cleanup.assert_not_called()

    def test_cleanup_preserves_similar_filenames(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scrub_par2, 'log'):
            names = ['a.par2', 'a.vol00+01.par2', 'a.par2.backup', 'a.par2-other.par2']
            for name in names:
                with open(os.path.join(tmp, name), 'w') as stream:
                    stream.write('keep')
            scrub_par2._remove_par2_files(os.path.join(tmp, 'a.par2'), 'unused')
            self.assertEqual(sorted(os.listdir(tmp)), sorted(names[2:]))

    def test_cli_returns_failure_for_unsuccessful_result(self):
        with patch.object(sys, 'argv', ['scrub', '/data', '/db', '10', '/log']), patch.object(scrub_par2, 'scrub_directory', return_value={'ok': False}):
            self.assertEqual(scrub_par2.main(), 1)

    def test_parity_creation_failure_makes_result_unsuccessful(self):
        operation_logger = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = os.path.join(tmpdir, 'data')
            database = os.path.join(tmpdir, 'parity')
            os.makedirs(directory)
            os.makedirs(database)
            source = os.path.join(directory, 'source.bin')
            with open(source, 'wb') as file_obj:
                file_obj.write(b'data')

            with (
                patch.object(scrub_par2, 'create_operation_logger', return_value=operation_logger),
                patch.object(scrub_par2, 'create_par2', return_value=False),
            ):
                result = scrub_par2.scrub_directory(
                    directory,
                    database,
                    10,
                    os.path.join(tmpdir, 'scrub.log'),
                    verify=False,
                    suppress_notifications=True,
                )

        self.assertFalse(result['ok'])
        self.assertEqual(result['files_failed'], ['source.bin'])
        self.assertEqual(operation_logger.complete.call_args.args[0], 'failed')

    def test_initial_scrub_failure_is_logged_and_raised(self):
        from sync import scrub_steps

        logger = Mock()
        with (
            patch.object(scrub_steps, 'create_operation_logger', return_value=logger),
            patch.object(scrub_steps, 'validate_filesystem_path'),
            patch.object(scrub_steps, 'validate_database_path'),
            patch.object(scrub_steps, 'validate_mount_for_sync'),
            patch.object(scrub_steps, 'validate_redundancy_percentage', return_value=10),
            patch.object(scrub_steps, 'ensure_directory'),
            patch.object(scrub_steps, 'check_path_on_smb_mount', return_value=False),
            patch.object(scrub_steps, 'get_disk_usage_details') as disk_usage,
            patch.object(scrub_steps.os, 'makedirs'),
            patch(
                'sync.service_tools.scrub_par2.scrub_directory',
                return_value={'ok': False},
            ) as run_scrub,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Initial par2 creation'):
                scrub_steps.create_scrub_service(
                    SimpleNamespace(username='agent'),
                    ['/data', '/database', '10%', 'daily'],
                )

        disk_usage.assert_not_called()
        run_scrub.assert_called_once_with(
            '/data',
            '/database',
            10,
            '/var/log/scrub/scrub-22073bd1.log',
            verify=False,
            suppress_notifications=True,
        )
        logger.complete.assert_called_once()
        self.assertEqual(logger.complete.call_args.args[0], 'failed')


if __name__ == '__main__':
    unittest.main()
