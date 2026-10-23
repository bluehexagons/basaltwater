"""Tests for storage system core logic: parsing, validation, and utilities."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import subprocess

import lib.task_utils as tu
from smb.samba_steps import (
    parse_share_spec,
    parse_share_credentials,
    validate_samba_share_credentials,
    _get_veto_dirs_for_share,
)
from smb.smb_mount_steps import parse_smb_mount_spec
from sync.sync_steps import parse_sync_spec
from sync.scrub_steps import parse_scrub_spec
from lib.task_utils import (
    validate_frequency,
    get_timer_calendar,
    escape_systemd_description,
    check_path_on_smb_mount,
    ensure_directory,
    VALID_FREQUENCIES,
)
from lib.validation import (
    validate_filesystem_path,
    validate_database_path,
    validate_service_name_uniqueness,
    validate_redundancy_percentage,
)
from lib.config import SetupConfig


# ---------------------------------------------------------------------------
# parse_share_spec
# ---------------------------------------------------------------------------

class TestParseShareSpec(unittest.TestCase):
    def test_valid_read_share(self):
        result = parse_share_spec(['read', 'myshare', '/mnt/data', 'user1:pass1'])
        self.assertEqual(result['access_type'], 'read')
        self.assertEqual(result['share_name'], 'myshare')
        self.assertEqual(result['paths'], ['/mnt/data'])
        self.assertEqual(result['users'], [{'username': 'user1', 'password': 'pass1'}])

    def test_valid_write_share(self):
        result = parse_share_spec(['write', 'docs', '/mnt/docs', 'admin:secret'])
        self.assertEqual(result['access_type'], 'write')

    def test_multiple_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one path'):
            parse_share_spec(['read', 's', '/a,/b', 'u1:p1,u2:p2'])

    def test_invalid_access_type(self):
        with self.assertRaises(ValueError):
            parse_share_spec(['execute', 'x', '/x', 'u:p'])

    def test_too_few_args(self):
        with self.assertRaises(ValueError):
            parse_share_spec(['read', 'x'])

    def test_none_spec(self):
        with self.assertRaises(ValueError):
            parse_share_spec(None)

    def test_user_without_password(self):
        with self.assertRaises(ValueError):
            parse_share_spec(['read', 's', '/a', 'nopassword'])

    def test_user_without_password_uses_named_credential(self):
        credentials = parse_share_credentials([['user1', 'pass1']])
        result = parse_share_spec(['read', 's', '/a', 'user1'], credentials)
        self.assertEqual(result['users'], [{'username': 'user1', 'password': 'pass1'}])

    def test_missing_named_credential(self):
        with self.assertRaisesRegex(ValueError, 'Missing credential for share user: user1'):
            parse_share_spec(['read', 's', '/a', 'user1'], {})


class TestValidateSambaShareCredentials(unittest.TestCase):
    def test_accepts_username_only_share_users_with_credentials(self):
        config = SetupConfig(
            host='testhost',
            username='testuser',
            system_type='server_lite',
            samba_shares=[['read', 'share', '/mnt/data', 'user1']],
            share_credentials=[['user1', 'pass1']],
        )

        validate_samba_share_credentials(config)

    def test_rejects_missing_username_only_share_credentials(self):
        config = SetupConfig(
            host='testhost',
            username='testuser',
            system_type='server_lite',
            samba_shares=[['read', 'share', '/mnt/data', 'user1']],
        )

        with self.assertRaisesRegex(ValueError, 'Missing credential for share user: user1'):
            validate_samba_share_credentials(config)


# ---------------------------------------------------------------------------
# parse_smb_mount_spec
# ---------------------------------------------------------------------------

class TestParseSmbMountSpec(unittest.TestCase):
    def test_valid_mount_spec(self):
        result = parse_smb_mount_spec(['/mnt/share', '192.168.1.10', 'user:pass', 'docs', '/sub'])
        self.assertEqual(result['mountpoint'], '/mnt/share')
        self.assertEqual(result['ip'], '192.168.1.10')
        self.assertEqual(result['username'], 'user')
        self.assertEqual(result['password'], 'pass')
        self.assertEqual(result['share'], 'docs')
        self.assertEqual(result['subdir'], '/sub')

    def test_wrong_arg_count(self):
        with self.assertRaises(ValueError):
            parse_smb_mount_spec(['/mnt/share', '192.168.1.10'])

    def test_relative_mountpoint(self):
        with self.assertRaises(ValueError):
            parse_smb_mount_spec(['relative/path', '1.2.3.4', 'u:p', 'share', '/'])

    def test_credentials_no_colon(self):
        with self.assertRaises(ValueError):
            parse_smb_mount_spec(['/mnt/x', '1.2.3.4', 'nopassword', 'share', '/'])

    def test_none_spec(self):
        with self.assertRaises(ValueError):
            parse_smb_mount_spec(None)

    def test_empty_spec(self):
        with self.assertRaises(ValueError):
            parse_smb_mount_spec([])


# ---------------------------------------------------------------------------
# parse_sync_spec
# ---------------------------------------------------------------------------

class TestParseSyncSpec(unittest.TestCase):
    def test_valid_sync(self):
        result = parse_sync_spec(['/src', '/dst', 'daily'])
        self.assertEqual(result['source'], '/src')
        self.assertEqual(result['destination'], '/dst')
        self.assertEqual(result['interval'], 'daily')

    def test_all_frequencies(self):
        for freq in VALID_FREQUENCIES:
            result = parse_sync_spec(['/a', '/b', freq])
            self.assertEqual(result['interval'], freq)

    def test_invalid_frequency(self):
        with self.assertRaises(ValueError):
            parse_sync_spec(['/a', '/b', 'yearly'])

    def test_relative_source(self):
        with self.assertRaises(ValueError):
            parse_sync_spec(['relative', '/dst', 'daily'])

    def test_relative_dest(self):
        with self.assertRaises(ValueError):
            parse_sync_spec(['/src', 'relative', 'daily'])

    def test_wrong_arg_count(self):
        with self.assertRaises(ValueError):
            parse_sync_spec(['/src', '/dst'])


# ---------------------------------------------------------------------------
# parse_scrub_spec
# ---------------------------------------------------------------------------

class TestParseScrubSpec(unittest.TestCase):
    def test_valid_scrub_absolute_db(self):
        result = parse_scrub_spec(['/mnt/data', '/mnt/db', '5%', 'weekly'])
        self.assertEqual(result['directory'], '/mnt/data')
        self.assertEqual(result['database_path'], '/mnt/db')
        self.assertEqual(result['redundancy'], '5%')
        self.assertEqual(result['frequency'], 'weekly')

    def test_relative_db_path_becomes_absolute(self):
        result = parse_scrub_spec(['/mnt/data', '.pardatabase', '10%', 'daily'])
        self.assertTrue(result['database_path'].startswith('/'))
        self.assertIn('.pardatabase', result['database_path'])

    def test_invalid_redundancy_no_percent(self):
        with self.assertRaises(ValueError):
            parse_scrub_spec(['/mnt/data', '/mnt/db', '5', 'daily'])

    def test_redundancy_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_scrub_spec(['/mnt/data', '/mnt/db', '0%', 'daily'])
        with self.assertRaises(ValueError):
            parse_scrub_spec(['/mnt/data', '/mnt/db', '101%', 'daily'])

    def test_wrong_arg_count(self):
        with self.assertRaises(ValueError):
            parse_scrub_spec(['/mnt/data', '/mnt/db'])


# ---------------------------------------------------------------------------
# validate_frequency
# ---------------------------------------------------------------------------

class TestValidateFrequency(unittest.TestCase):
    def test_all_valid(self):
        for freq in VALID_FREQUENCIES:
            validate_frequency(freq)  # should not raise

    def test_invalid(self):
        with self.assertRaises(ValueError):
            validate_frequency('never')

    def test_custom_label_in_error(self):
        with self.assertRaises(ValueError) as ctx:
            validate_frequency('bad', label='interval')
        self.assertIn('interval', str(ctx.exception))


# ---------------------------------------------------------------------------
# get_timer_calendar
# ---------------------------------------------------------------------------

class TestGetTimerCalendar(unittest.TestCase):
    def test_hourly(self):
        self.assertEqual(get_timer_calendar('hourly'), '*-*-* *:00:00')

    def test_daily_default(self):
        self.assertIn('02:00:00', get_timer_calendar('daily'))

    def test_daily_custom_hour(self):
        self.assertIn('05:00:00', get_timer_calendar('daily', hour_offset=5))

    def test_weekly(self):
        result = get_timer_calendar('weekly')
        self.assertIn('Mon', result)

    def test_monthly(self):
        result = get_timer_calendar('monthly')
        self.assertIn('-01', result)

    def test_biweekly(self):
        result = get_timer_calendar('biweekly')
        self.assertIn('Mon', result)
        # Biweekly runs weekly; orchestrator handles 14-day interval
        self.assertNotIn('1,15', result)

    def test_bimonthly(self):
        result = get_timer_calendar('bimonthly')
        # Bimonthly runs monthly; orchestrator handles 60-day interval
        self.assertIn('-01', result)
        self.assertNotIn('01/2', result)  # Step syntax not valid in systemd

    def test_unknown_frequency_fallback(self):
        result = get_timer_calendar('unknown')
        self.assertIn('02:00:00', result)


# ---------------------------------------------------------------------------
# escape_systemd_description
# ---------------------------------------------------------------------------

class TestEscapeSystemdDescription(unittest.TestCase):
    def test_backslash(self):
        self.assertEqual(escape_systemd_description('a\\b'), 'a\\\\b')

    def test_newline(self):
        self.assertEqual(escape_systemd_description('a\nb'), 'a b')

    def test_quotes(self):
        self.assertEqual(escape_systemd_description('a"b'), "a'b")

    def test_plain(self):
        self.assertEqual(escape_systemd_description('/mnt/data'), '/mnt/data')


# ---------------------------------------------------------------------------
# check_path_on_smb_mount
# ---------------------------------------------------------------------------

class TestCheckPathOnSmbMount(unittest.TestCase):
    def _make_config(self, smb_mounts):
        return SetupConfig(
            host='test', username='test', system_type='server_lite',
            smb_mounts=smb_mounts,
        )

    def test_path_on_mount(self):
        config = self._make_config([['/mnt/share', '1.2.3.4', 'u:p', 'share', '/']])
        self.assertTrue(check_path_on_smb_mount('/mnt/share/dir', config))

    def test_exact_mount(self):
        config = self._make_config([['/mnt/share', '1.2.3.4', 'u:p', 'share', '/']])
        self.assertTrue(check_path_on_smb_mount('/mnt/share', config))

    def test_path_not_on_mount(self):
        config = self._make_config([['/mnt/share', '1.2.3.4', 'u:p', 'share', '/']])
        self.assertFalse(check_path_on_smb_mount('/mnt/other', config))

    def test_no_mounts(self):
        config = self._make_config(None)
        self.assertFalse(check_path_on_smb_mount('/mnt/share', config))


# ---------------------------------------------------------------------------
# validate_filesystem_path
# ---------------------------------------------------------------------------

class TestValidateFilesystemPath(unittest.TestCase):
    def test_empty_path(self):
        with self.assertRaises(ValueError):
            validate_filesystem_path('')

    def test_valid_path(self):
        validate_filesystem_path('/tmp')  # should not raise

    def test_must_exist_missing(self):
        with self.assertRaises(ValueError):
            validate_filesystem_path('/does/not/exist/xyz', must_exist=True)

    def test_check_writable_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            validate_filesystem_path(tmpdir, check_writable=True)  # should not raise

    def test_check_writable_nonexistent_parent_writable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'newdir')
            validate_filesystem_path(path, check_writable=True)  # should not raise

    def test_check_writable_no_parent(self):
        with self.assertRaises(ValueError):
            validate_filesystem_path('/no/such/parent/child', check_writable=True)


# ---------------------------------------------------------------------------
# validate_database_path
# ---------------------------------------------------------------------------

class TestValidateDatabasePath(unittest.TestCase):
    def test_valid_path(self):
        validate_database_path('/tmp/somedb')  # should not raise

    def test_empty_path(self):
        with self.assertRaises(ValueError):
            validate_database_path('')


# ---------------------------------------------------------------------------
# validate_service_name_uniqueness
# ---------------------------------------------------------------------------

class TestValidateServiceNameUniqueness(unittest.TestCase):
    def test_valid_short_name(self):
        self.assertTrue(validate_service_name_uniqueness('my-service', []))

    def test_valid_long_name(self):
        """Generated names like sync-_mnt_data-to-_mnt_backup-a1b2c3d4 should pass."""
        name = 'sync-_mnt_data_docs-to-_mnt_backup_docs-a1b2c3d4'
        self.assertTrue(validate_service_name_uniqueness(name, []))

    def test_empty_name(self):
        with self.assertRaises(ValueError):
            validate_service_name_uniqueness('', [])

    def test_duplicate_name(self):
        with self.assertRaises(ValueError):
            validate_service_name_uniqueness('my-service', ['my-service'])

    def test_reserved_name(self):
        with self.assertRaises(ValueError):
            validate_service_name_uniqueness('service', [])

    def test_invalid_chars(self):
        with self.assertRaises(ValueError):
            validate_service_name_uniqueness('My Service!', [])


# ---------------------------------------------------------------------------
# validate_redundancy_percentage
# ---------------------------------------------------------------------------

class TestValidateRedundancyPercentage(unittest.TestCase):
    def test_with_percent(self):
        self.assertEqual(validate_redundancy_percentage('5%'), 5)

    def test_without_percent(self):
        self.assertEqual(validate_redundancy_percentage('10'), 10)

    def test_boundary_zero(self):
        self.assertEqual(validate_redundancy_percentage('0'), 0)

    def test_boundary_hundred(self):
        self.assertEqual(validate_redundancy_percentage('100%'), 100)

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            validate_redundancy_percentage('101%')
        with self.assertRaises(ValueError):
            validate_redundancy_percentage('-1')

    def test_empty(self):
        with self.assertRaises(ValueError):
            validate_redundancy_percentage('')

    def test_non_numeric(self):
        with self.assertRaises(ValueError):
            validate_redundancy_percentage('abc%')


# ---------------------------------------------------------------------------
# ensure_directory
# ---------------------------------------------------------------------------

class TestEnsureDirectory(unittest.TestCase):
    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = os.path.join(tmpdir, 'new_sub')
            # Mock run to avoid actual chown
            original_run = tu.run
            calls: list[str] = []
            def mock_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(args=[cmd], returncode=0, stdout='', stderr='')
            tu.run = mock_run
            try:
                ensure_directory(new_dir, 'testuser')
                self.assertTrue(os.path.isdir(new_dir))
                self.assertTrue(any('chown' in c for c in calls))
            finally:
                tu.run = original_run

    def test_existing_directory_no_op(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_run = tu.run
            calls: list[str] = []
            def mock_run(cmd, **kw):
                calls.append(cmd)
            tu.run = mock_run
            try:
                ensure_directory(tmpdir, 'testuser')
                self.assertEqual(len(calls), 0)  # no chown on existing dir
            finally:
                tu.run = original_run

    def test_existing_file_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, 'a_file')
            with open(file_path, 'w') as f:
                f.write('content')
            with self.assertRaises(NotADirectoryError):
                ensure_directory(file_path, 'testuser')


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------

class TestRuntimeConfig(unittest.TestCase):
    def test_from_dict_basic(self):
        from lib.runtime_config import RuntimeConfig
        data = {
            "username": "testuser",
            "sync_specs": [["/src", "/dst", "daily"]],
            "scrub_specs": [["/data", ".par", "5%", "weekly"]],
            "notify_specs": [["webhook", "http://example.com"]],
        }
        config = RuntimeConfig.from_dict(data)
        self.assertEqual(config.username, "testuser")
        self.assertEqual(len(config.sync_specs), 1)
        self.assertEqual(len(config.scrub_specs), 1)
        self.assertEqual(len(config.notify_specs), 1)

    def test_from_dict_defaults(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig.from_dict({})
        self.assertEqual(config.username, "root")
        self.assertEqual(config.sync_specs, [])
        self.assertEqual(config.scrub_specs, [])
        self.assertEqual(config.notify_specs, [])
        self.assertIsNone(config.smb_mounts)

    def test_has_storage_ops_true(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig(
            username="test",
            sync_specs=[["/src", "/dst", "daily"]],
            scrub_specs=[],
            notify_specs=[],
        )
        self.assertTrue(config.has_storage_ops())

    def test_has_storage_ops_false(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig(
            username="test",
            sync_specs=[],
            scrub_specs=[],
            notify_specs=[],
        )
        self.assertFalse(config.has_storage_ops())

    def test_get_all_paths(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig(
            username="test",
            sync_specs=[["/src1", "/dst1", "daily"], ["/src2", "/dst2", "hourly"]],
            scrub_specs=[["/data1", ".par1", "5%", "weekly"]],
            notify_specs=[],
        )
        paths = config.get_all_paths()
        self.assertEqual(len(paths), 6)  # 2 sync sources + 2 sync dests + 1 scrub dir + 1 scrub db
        self.assertIn("/src1", paths)
        self.assertIn("/dst1", paths)
        self.assertIn("/src2", paths)
        self.assertIn("/dst2", paths)
        self.assertIn("/data1", paths)
        self.assertIn(".par1", paths)

    def test_to_dict(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig(
            username="testuser",
            sync_specs=[["/src", "/dst", "daily"]],
            scrub_specs=[],
            notify_specs=[],
            smb_mounts=None,
        )
        result = config.to_dict()
        self.assertEqual(result["username"], "testuser")
        self.assertEqual(result["sync_specs"], [["/src", "/dst", "daily"]])
        self.assertEqual(result["smb_mounts"], None)

    def test_friendly_name_from_dict(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig.from_dict({
            "username": "test",
            "friendly_name": "scrapbox",
        })
        self.assertEqual(config.friendly_name, "scrapbox")

    def test_friendly_name_from_dict_missing(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig.from_dict({"username": "test"})
        self.assertIsNone(config.friendly_name)

    def test_friendly_name_in_to_dict(self):
        from lib.runtime_config import RuntimeConfig
        config = RuntimeConfig(
            username="test",
            sync_specs=[],
            scrub_specs=[],
            notify_specs=[],
            friendly_name="mybox",
        )
        result = config.to_dict()
        self.assertEqual(result["friendly_name"], "mybox")


# ---------------------------------------------------------------------------
# _get_veto_dirs_for_share
# ---------------------------------------------------------------------------

class TestGetVetoDirsForShare(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_no_scrub_specs(self):
        config = self._make_config(scrub_specs=None)
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, [])

    def test_scrub_db_under_share_path(self):
        """When scrub directory matches share path and db is relative, veto the db dir."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store', '.pardatabase', '5%', 'monthly']
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, ['.pardatabase'])

    def test_scrub_db_absolute_under_share(self):
        """When scrub db is an absolute path under the share."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store', '/mnt/data/store/.pardb', '5%', 'monthly']
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, ['.pardb'])

    def test_scrub_db_outside_share(self):
        """When scrub db is outside the share path, no veto."""
        config = self._make_config(scrub_specs=[
            ['/mnt/other', '.pardatabase', '5%', 'monthly']
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, [])

    def test_multiple_scrub_specs_same_share(self):
        """Multiple scrub specs matching the same share."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store', '.pardatabase', '5%', 'monthly'],
            ['/mnt/data/store', '.checksums', '3%', 'weekly'],
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, ['.pardatabase', '.checksums'])

    def test_no_duplicates(self):
        """Same db name from different specs should only appear once."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store', '.pardatabase', '5%', 'monthly'],
            ['/mnt/data/store', '.pardatabase', '3%', 'weekly'],
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, ['.pardatabase'])

    def test_scrub_spec_too_short(self):
        """Scrub spec with fewer than 2 elements is skipped."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store'],
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, [])

    def test_scrub_dir_is_subdir_of_share(self):
        """When scrub directory is a subdirectory of the share."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store/subdir', '.pardatabase', '5%', 'monthly']
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        # The db resolves to /mnt/data/store/subdir/.pardatabase
        # Top-level relative to share is 'subdir'
        self.assertEqual(result, ['subdir'])

    def test_absolute_db_not_under_share(self):
        """Absolute db path not under the share should not be vetoed."""
        config = self._make_config(scrub_specs=[
            ['/mnt/data/store', '/var/lib/pardb', '5%', 'monthly']
        ])
        result = _get_veto_dirs_for_share('/mnt/data/store', config)
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
