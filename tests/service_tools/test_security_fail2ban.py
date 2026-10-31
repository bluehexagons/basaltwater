"""Rotation, source coverage and bounded collection tests for Fail2ban."""

from __future__ import annotations

import gzip
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from lib.xrdp_certificate import XrdpCertificateHealth
from security.service_tools import security_fail2ban as reader
from security.service_tools import security_monitor as monitor


def record(time: str = '02:01:00', message: str = 'NOTICE [sshd] Ban 192.0.2.4') -> str:
    return f'2026-10-31 {time},123 fail2ban.actions [1]: {message}\n'


class TestFail2banRotations(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / 'fail2ban.log'
        self.since = datetime(2026, 10, 31, 1, 55)
        self.until = datetime(2026, 10, 31, 2, 5)
        self.enterContext(patch.object(reader.shutil, 'which', return_value=None))

    def write(self, text: str, suffix: str = ''):
        path = Path(str(self.path) + suffix)
        path.write_bytes(gzip.compress(text.encode()) if suffix.endswith('.gz') else text.encode())
        return path

    def scan(self):
        return reader.scan_fail2ban(str(self.path), self.since, self.until)

    def assert_failure(self, message: str):
        result = self.scan()
        self.assertIn(message, result.error)
        self.assertEqual((result.ban_count, result.unban_count, result.bans, result.unbans), (0, 0, [], []))
        self.assertIsNone(result.oldest)

    def test_original_rotation_loss_and_compressed_history(self):
        self.write(record(message='INFO Log rotation performed'))
        self.write(record('01:59:00'), '.1')
        self.write(record('01:50:00') + record('01:55:00', 'NOTICE [sshd] Unban 192.0.2.4'), '.2.gz')
        result = self.scan()
        self.assertIsNone(result.error)
        self.assertEqual((result.ban_count, result.unban_count), (1, 1))
        self.assertEqual(result.oldest, datetime(2026, 10, 31, 1, 50))

    def test_inclusive_window_and_identical_legitimate_records(self):
        self.write(record('01:54:59') + record('01:55:00') * 2 + record('02:05:00') + record('02:05:01'))
        self.write(record('01:55:00'), '.1')
        result = self.scan()
        self.assertIsNone(result.error)
        self.assertEqual(result.ban_count, 4)

    def test_hardlink_alias_is_read_once(self):
        self.write(record())
        os.link(self.path, str(self.path) + '.1')
        self.assertEqual(self.scan().ban_count, 1)

    def test_empty_active_with_timestamped_archive(self):
        self.write('')
        self.write(record('01:50:00', 'INFO Quiet server'), '.1.gz')
        result = self.scan()
        self.assertIsNone(result.error)
        self.assertEqual(result.ban_count, 0)
        self.assertEqual(result.oldest, datetime(2026, 10, 31, 1, 50))

    def test_missing_optional_source_and_installed_missing_source(self):
        self.assertIsNone(self.scan().error)
        with patch.object(reader.shutil, 'which', return_value='/usr/bin/fail2ban-client'):
            self.assert_failure('active log file unavailable')
        self.write(record(), '.1')
        self.assert_failure('active log file unavailable')

    def test_unknown_retention_or_incomplete_record(self):
        for content, error in [('', 'retention evidence unavailable'), ('traceback\n', 'retention evidence unavailable'),
                               (record().rstrip(), 'incomplete final log record')]:
            with self.subTest(content=content):
                self.write(content)
                self.assert_failure(error)

    def test_invalid_timestamps_and_utf8_fail_closed(self):
        self.write(record().replace('2026-10-31', '2026-99-31'))
        self.assert_failure('does not match format')
        self.path.write_bytes(record().encode() + b'\xff\n')
        self.assert_failure('decode')

    def test_non_action_prefix_does_not_create_ban(self):
        self.write(record().replace('fail2ban.actions', 'fail2ban.server') + record(message='INFO [sshd] Ban 192.0.2.4'))
        self.assertEqual(self.scan().ban_count, 0)

    def test_ambiguous_or_unsupported_rotations(self):
        for suffix in ('.0', '.01', '.9999999999', '.1.xz', '-20261031', '.1.gz'):
            with self.subTest(suffix=suffix):
                self.write(record())
                self.write(record(), '.1')
                extra = self.write(record(), suffix)
                self.assert_failure('ambiguous' if suffix == '.1.gz' else 'unsupported')
                extra.unlink()
        Path(str(self.path) + '.1').unlink()
        self.write(record(), '.2')
        self.assert_failure('missing generation')

    def test_symlink_directory_and_fifo_are_rejected(self):
        self.write(record(), '.1')
        self.path.symlink_to(str(self.path) + '.1')
        self.assert_failure('regular file')
        self.path.unlink()
        self.path.mkdir()
        self.assert_failure('regular file')
        self.path.rmdir()
        os.mkfifo(self.path)
        self.assert_failure('regular file')

    def test_replaced_with_fifo_after_inventory_never_blocks(self):
        self.write(record())
        original = reader._lines

        def replaced(log, budget):
            self.path.unlink()
            os.mkfifo(self.path)
            yield from original(log, budget)

        with patch.object(reader, '_lines', side_effect=replaced):
            self.assert_failure('regular file')

    def test_unreadable_file_discards_partial_counts(self):
        self.write(record())
        self.write(record(), '.1')
        original = reader.os.open

        def denied(path, flags):
            if path == str(self.path):
                raise PermissionError('test denied')
            return original(path, flags)

        with patch.object(reader.os, 'open', side_effect=denied):
            self.assert_failure('test denied')

    def test_corrupt_archives_discard_partial_counts(self):
        self.write(record())
        archive = self.write(record(), '.1.gz')
        valid = archive.read_bytes()
        damaged_crc = bytearray(valid)
        damaged_crc[-8] ^= 1
        for content in (b'', b'broken', valid[:9], valid[:-5], bytes(damaged_crc), valid + b'garbage'):
            with self.subTest(content=content[:12]):
                archive.write_bytes(content)
                result = self.scan()
                self.assertIsNotNone(result.error)
                self.assertEqual(result.ban_count, 0)
                self.assertEqual(result.bans, [])
        archive.write_bytes(valid + valid)
        self.assertEqual(self.scan().ban_count, 3)

    def test_rotation_during_read_retries_complete_snapshot(self):
        self.write(record('01:59:00'))
        original = reader._lines
        changed = False

        def rotate(log, budget):
            nonlocal changed
            for line in original(log, budget):
                yield line
                if not changed:
                    changed = True
                    self.path.rename(str(self.path) + '.1')
                    self.write(record())

        with patch.object(reader, '_lines', side_effect=rotate):
            result = self.scan()
        self.assertIsNone(result.error)
        self.assertEqual(result.ban_count, 2)

    def test_append_truncate_and_compression_during_read_retry(self):
        for mutation in ('append', 'truncate', 'compress', 'remove'):
            with self.subTest(mutation=mutation):
                self.write(record())
                archive = self.write(record('01:59:00'), '.1')
                original = reader._lines
                changed = False

                def mutate(log, budget):
                    nonlocal changed
                    yield from original(log, budget)
                    if not changed:
                        changed = True
                        if mutation == 'append':
                            with self.path.open('a') as stream:
                                stream.write(record())
                        elif mutation == 'truncate':
                            self.write(record('02:03:00'))
                        elif mutation == 'compress':
                            self.write(archive.read_text(), '.1.gz')
                            archive.unlink()
                        else:
                            archive.unlink()

                with patch.object(reader, '_lines', side_effect=mutate):
                    result = self.scan()
                self.assertIsNone(result.error)
                self.assertEqual(result.ban_count, 3 if mutation == 'append' else 1 if mutation == 'remove' else 2)
                for entry in Path(self.directory).iterdir():
                    entry.unlink()

    def test_persistent_change_returns_error_without_counts(self):
        self.write(record())
        original = reader._lines

        def append(log, budget):
            yield from original(log, budget)
            with self.path.open('a') as stream:
                stream.write(record())

        with patch.object(reader, '_lines', side_effect=append):
            self.assert_failure('changed during collection')

    def test_retry_shares_decompressed_byte_budget(self):
        self.write(record())
        original = reader._lines

        def changed(log, budget):
            yield from original(log, budget)
            raise reader._Changed()

        with patch.object(reader, '_MAX_BYTES', len(record().encode())), patch.object(reader, '_lines', side_effect=changed):
            self.assert_failure('size limit')

    def test_bounds_apply_to_files_decompressed_bytes_lines_and_deadline(self):
        self.write(record())
        self.write(record(), '.1.gz')
        for name, value, message in [('_MAX_FILES', 1, 'too many'), ('_MAX_BYTES', 10, 'size limit'),
                                     ('_MAX_LINE_BYTES', 10, 'size limit'), ('_TIMEOUT', 0, 'deadline')]:
            with self.subTest(bound=name), patch.object(reader, name, value):
                self.assert_failure(message)

    def test_real_reader_cursor_gaps_recovery_and_other_source_events(self):
        since = datetime.now().replace(microsecond=0) - timedelta(hours=1)
        floor = since + timedelta(minutes=5)
        self.write(record(floor.strftime('%H:%M:%S')).replace('2026-10-31', floor.strftime('%Y-%m-%d')))
        state = {'last_run': since.isoformat()}
        ssh = {'failure_count': 7}
        self.enterContext(patch.object(monitor, '_FAIL2BAN_LOG', str(self.path)))
        self.enterContext(patch.object(monitor, '_STATE_FILE', str(Path(self.directory) / 'state.json')))
        self.enterContext(patch.object(monitor, 'load_notification_configs_from_state', return_value=['cfg']))
        self.enterContext(patch.object(monitor, '_check_retention', return_value=({}, [])))
        self.enterContext(patch.object(monitor, '_check_auditd', return_value=([], False, [])))
        self.enterContext(patch.object(monitor, 'managed_setup_audit_windows', return_value=[]))
        self.enterContext(patch.object(monitor, '_check_ssh_failures', return_value=(ssh, None)))
        self.enterContext(patch.object(monitor, 'inspect_xrdp_certificate', return_value=XrdpCertificateHealth('not_configured', '', '')))
        notify = self.enterContext(patch.object(monitor, 'send_notification_safe'))
        monitor._save_state(state)
        self.assertEqual(monitor.main(), 1)  # Known gap advances just this window.
        state = monitor._load_state()
        self.assertEqual(state['last_run'], (since + timedelta(minutes=15)).isoformat())
        self.assertEqual(state['retention_gaps'], [{'source': 'fail2ban', 'since': since.isoformat(), 'until': floor.isoformat()}])
        self.assertEqual(notify.call_args.kwargs['data']['counts']['fail2ban_bans'], 1)
        self.assertEqual(notify.call_args.kwargs['data']['counts']['ssh_authentication_failures'], 7)
        self.assertEqual(os.stat(monitor._STATE_FILE).st_mode & 0o777, 0o600)
        self.write('')
        self.assertEqual(monitor.main(), 1)  # Unknown evidence freezes cursor.
        self.assertEqual(monitor._load_state()['last_run'], state['last_run'])
        calls = notify.call_count
        self.assertEqual(monitor.main(), 1)
        self.assertEqual(notify.call_count, calls)
        self.write(record(floor.strftime('%H:%M:%S')).replace('2026-10-31', floor.strftime('%Y-%m-%d')))
        self.assertEqual(monitor.main(), 0)
        self.assertEqual(monitor._load_state()['last_run'], (since + timedelta(minutes=30)).isoformat())
        self.assertEqual(monitor._load_state()['retention_gaps'], state['retention_gaps'])
        self.assertEqual(notify.call_args.kwargs['data']['counts']['fail2ban_bans'], 0)


if __name__ == '__main__':
    unittest.main()
