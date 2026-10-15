"""Tests for lib/cache.py: setup command caching, loading, and merging."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.atomic_io import write_json_atomic
from lib.cache import (
    get_cache_path_for_host,
    save_setup_command,
    load_setup_command,
    load_all_setup_cache_records,
    load_all_setup_commands,
    merge_setup_configs,
    rename_setup_command,
)
from lib.config import SetupConfig
from lib.state_read import StateReadError


class TestGetCachePathForHost(unittest.TestCase):
    def test_returns_json_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir):
                path = get_cache_path_for_host('myhost')
                self.assertTrue(path.endswith('.json'))
                self.assertIn('myhost', path)

    def test_normalizes_host(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir):
                path1 = get_cache_path_for_host('MyHost.')
                path2 = get_cache_path_for_host('myhost')
                self.assertEqual(path1, path2)

    def test_safe_chars_in_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir):
                path = get_cache_path_for_host('host with spaces!')
                basename = os.path.basename(path)
                # Should not contain spaces or special chars
                self.assertNotIn(' ', basename)
                self.assertNotIn('!', basename)


class TestSaveAndLoadSetupCommand(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(timezone='America/New_York')
                save_setup_command(config)
                loaded = load_setup_command('testhost')
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.host, 'testhost')
                self.assertEqual(loaded.system_type, 'server_lite')
                self.assertEqual(loaded.timezone, 'America/New_York')

                cache_path = get_cache_path_for_host('testhost')
                with open(cache_path, encoding='utf-8') as f:
                    cache_data = json.load(f)
                self.assertEqual(cache_data['script'], 'basaltw')
                self.assertEqual(cache_data['command'], 'basaltw setup')

    def test_load_retains_legacy_privilege_broker_origin_and_reports_upgrade_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir):
                cache_path = get_cache_path_for_host('vm.example')
                legacy_args = {
                    'username': 'agent',
                    'privilege_broker': 'https://vm.example:9444',
                }
                with open(cache_path, 'w', encoding='utf-8') as file_obj:
                    json.dump({
                        'host': 'vm.example',
                        'system_type': 'agent_vm',
                        'args': legacy_args,
                    }, file_obj)

                with self.assertRaisesRegex(StateReadError, 'docs/migration-guide.md'):
                    load_setup_command('vm.example')
                with open(cache_path, encoding='utf-8') as file_obj:
                    self.assertEqual(json.load(file_obj)['args'], legacy_args)

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                loaded = load_setup_command('nonexistent')
                self.assertIsNone(loaded)

    def test_save_with_name_and_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(friendly_name='My Server', tags=['web', 'prod'])
                save_setup_command(config)
                loaded = load_setup_command('testhost')
                self.assertEqual(loaded.friendly_name, 'My Server')
                self.assertEqual(loaded.tags, ['web', 'prod'])

    def test_load_by_friendly_name_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(host='10.0.0.5', friendly_name='My Server')
                save_setup_command(config)

                loaded = load_setup_command('My Server')

                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.host, '10.0.0.5')
                self.assertEqual(loaded.friendly_name, 'My Server')

    def test_load_by_tag_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(host='10.0.0.5', tags=['web', 'prod'])
                save_setup_command(config)

                loaded = load_setup_command('prod')

                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.host, '10.0.0.5')
                self.assertEqual(loaded.tags, ['web', 'prod'])

    def test_load_by_name_returns_none_when_no_match_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(host='10.0.0.5', friendly_name='My Server', tags=['prod'])
                save_setup_command(config)

                loaded = load_setup_command('unknown-name')

                self.assertIsNone(loaded)

    def test_load_by_name_rejects_corrupted_cache_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                with open(os.path.join(tmpdir, 'broken.json'), 'w') as f:
                    f.write('{not valid json')

                config = self._make_config(host='10.0.0.5', friendly_name='My Server', tags=['prod'])
                save_setup_command(config)

                with self.assertRaises(StateReadError):
                    load_setup_command("My Server")

    def test_save_with_timing_and_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config()
                start = 1700000000.0
                end = 1700000060.0
                save_setup_command(config, start_time=start, end_time=end, success=True)
                cache_path = get_cache_path_for_host('testhost')
                with open(cache_path) as f:
                    data = json.load(f)
                self.assertEqual(data['last_start_time'], start)
                self.assertEqual(data['last_end_time'], end)
                self.assertIs(data['last_success'], True)

    def test_save_with_failure_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config()
                start = 1700000000.0
                end = 1700000030.0
                save_setup_command(config, start_time=start, end_time=end, success=False)
                cache_path = get_cache_path_for_host('testhost')
                with open(cache_path) as f:
                    data = json.load(f)
                self.assertIs(data['last_success'], False)

    def test_second_save_updates_timing(self):
        """Verify that a second save (with timing) overwrites the first (without timing)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('lib.cache.get_setup_cache_dir', return_value=tmpdir), patch('lib.cache.get_history_dir', return_value=tmpdir):
                config = self._make_config(timezone='UTC')
                # First save: no timing (pre-run)
                save_setup_command(config)
                cache_path = get_cache_path_for_host('testhost')
                with open(cache_path) as f:
                    data = json.load(f)
                self.assertNotIn('last_start_time', data)
                self.assertNotIn('last_success', data)
                # Second save: with timing (post-run)
                start = 1700000000.0
                end = 1700000045.0
                save_setup_command(config, start_time=start, end_time=end, success=True)
                with open(cache_path) as f:
                    data = json.load(f)
                self.assertEqual(data['last_start_time'], start)
                self.assertEqual(data['last_end_time'], end)
                self.assertIs(data['last_success'], True)
                # Config data still present
                self.assertEqual(data['host'], 'testhost')
                self.assertEqual(data['system_type'], 'server_lite')

    def test_completed_run_writes_history_entry(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as history_dir:
            with patch('lib.cache.get_setup_cache_dir', return_value=cache_dir), patch('lib.cache.get_history_dir', return_value=history_dir):
                config = self._make_config(friendly_name='My Server', tags=['prod'])
                save_setup_command(
                    config,
                    start_time=1700000000.0,
                    end_time=1700000045.0,
                    success=True,
                    operation='patch',
                )

                history_files = os.listdir(history_dir)
                self.assertEqual(len(history_files), 1)
                history_path = os.path.join(history_dir, history_files[0])
                with open(history_path, encoding='utf-8') as f:
                    history_data = json.load(f)

                self.assertEqual(history_data['host'], 'testhost')
                self.assertEqual(history_data['operation'], 'patch')
                self.assertEqual(history_data['system_type'], 'server_lite')
                self.assertEqual(history_data['name'], 'My Server')
                self.assertEqual(history_data['tags'], ['prod'])
                self.assertIs(history_data['success'], True)
                self.assertEqual(history_data['duration_seconds'], 45.0)
                self.assertEqual(history_data['script'], 'basaltw')
                self.assertEqual(history_data['command'], 'basaltw patch')
                self.assertNotIn('share_credentials', history_data['args'])

    def test_initial_cache_write_does_not_create_history_entry(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as history_dir:
            with patch('lib.cache.get_setup_cache_dir', return_value=cache_dir), patch('lib.cache.get_history_dir', return_value=history_dir):
                config = self._make_config()
                save_setup_command(config, operation='setup')

                self.assertEqual(os.listdir(history_dir), [])

    def test_completed_runs_in_same_second_get_distinct_history_files(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as history_dir:
            with patch('lib.cache.get_setup_cache_dir', return_value=cache_dir), patch('lib.cache.get_history_dir', return_value=history_dir):
                config = self._make_config()
                for _index in range(2):
                    save_setup_command(
                        config,
                        start_time=1700000000.0,
                        end_time=1700000045.0,
                        success=True,
                    )

                self.assertEqual(len(os.listdir(history_dir)), 2)

    def test_rename_setup_command_preserves_metadata_and_history(self):
        with tempfile.TemporaryDirectory() as cache_dir, tempfile.TemporaryDirectory() as history_dir:
            with patch('lib.cache.get_setup_cache_dir', return_value=cache_dir), patch('lib.cache.get_history_dir', return_value=history_dir):
                config = self._make_config(
                    username='olduser',
                    enable_syncthing=True,
                    syncthing_admin='syncthing-admin',
                    agent_workspace='/home/olduser/repos',
                    sync_specs=[['/home/olduser/data', '/srv/backup', 'daily']],
                    notify_specs=[['webhook', '/home/olduser/secret-looking-value']],
                    gogs=['git.example.com:3000', '/home/olduser/gogs'],
                    friendly_name='My Server',
                    tags=['prod'],
                )
                save_setup_command(config, start_time=1.0, end_time=2.0, success=True)
                rename_setup_command(
                    'testhost',
                    old_username='olduser',
                    new_username='newuser',
                    old_home='/home/olduser',
                    new_home='/home/newuser',
                )

                loaded = load_setup_command('testhost')
                assert loaded is not None
                self.assertEqual(loaded.username, 'newuser')
                self.assertEqual(loaded.agent_workspace, '/home/newuser/repos')
                self.assertEqual(loaded.sync_specs[0][0], '/home/newuser/data')
                self.assertEqual(loaded.syncthing_admin, 'syncthing-admin')
                self.assertEqual(loaded.gogs[1], '/home/newuser/gogs')
                self.assertEqual(
                    loaded.notify_specs[0][1],
                    '/home/olduser/secret-looking-value',
                )
                self.assertEqual(loaded.friendly_name, 'My Server')
                self.assertEqual(loaded.tags, ['prod'])
                self.assertEqual(len(os.listdir(history_dir)), 1)


class TestScopedSetupLookup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = patch.dict(os.environ, {"BASALTWATER_WORKSPACE": temporary.name})
        workspace.start()
        self.addCleanup(workspace.stop)

    def _save_legacy(self):
        path = get_cache_path_for_host("192.168.0.25")
        write_json_atomic(path, {
            "host": "192.168.0.25", "system_type": "server_web",
            "name": "Older server", "tags": ["legacy"], "args": {
                "username": "admin", "api_subdomain": None,
                "install_ruby": False, "reset_migrations": False,
            },
        })
        return path

    def _save_current(self, **overrides):
        values = {"host": "192.168.0.50", "username": "agent", "system_type": "server_lite",
                  "friendly_name": "New agent", "tags": ["agents"]}
        values.update(overrides)
        config = SetupConfig(**values)
        save_setup_command(config)
        return get_cache_path_for_host(config.host)

    def test_new_ip_does_not_scan_unrelated_legacy_state(self):
        self._save_legacy()
        with patch("lib.cache.iter_setup_cache_records", side_effect=AssertionError("unexpected inventory scan")):
            for address in ("192.168.0.50", "2001:db8::50"):
                with self.subTest(address=address):
                    self.assertIsNone(load_setup_command(address))

    def test_new_ip_cannot_resolve_to_another_hosts_name_or_tag(self):
        self._save_current(host="192.168.0.25", friendly_name="192.168.0.50", tags=["192.168.0.51"])
        self.assertIsNone(load_setup_command("192.168.0.50"))
        self.assertIsNone(load_setup_command("192.168.0.51"))

    def test_new_vm_provisioning_metadata_ignores_unrelated_retired_setup(self):
        import basaltwater
        from argparse import Namespace

        path = self._save_legacy()
        with open(path, "rb") as file_obj:
            original = file_obj.read()
        for host in ("192.168.0.50", "192.168.0.50/24"):
            with self.subTest(host=host):
                config = SetupConfig(host=host, username="agent", system_type="server_lite",
                                     machine_type="vm", hosted_node="ts1")
                self.assertIsNone(basaltwater._load_cached_provisioning_metadata(config))
                self.assertFalse(basaltwater._reuse_cached_provisioning_metadata(config, Namespace()))
        with open(path, "rb") as file_obj:
            self.assertEqual(file_obj.read(), original)

    def test_name_and_tag_decode_only_the_selected_current_setup(self):
        path = self._save_legacy()
        self._save_current()
        with open(path, "rb") as file_obj:
            original = file_obj.read()
        for alias in ("NEW AGENT", "AGENTS"):
            with self.subTest(alias=alias):
                self.assertEqual(load_setup_command(alias).host, "192.168.0.50")
        with open(path, "rb") as file_obj:
            self.assertEqual(file_obj.read(), original)

    def test_unknown_name_does_not_decode_unrelated_retired_setup(self):
        self._save_legacy()
        self.assertIsNone(load_setup_command("unknown-vm.example"))

    def test_explicit_legacy_host_name_and_tag_still_require_upgrade(self):
        path = self._save_legacy()
        with open(path, "rb") as file_obj:
            original = file_obj.read()
        for alias in ("192.168.0.25", "Older server", "legacy"):
            with self.subTest(alias=alias), self.assertRaisesRegex(StateReadError, "Retired setup fields"):
                load_setup_command(alias)
        with open(path, "rb") as file_obj:
            self.assertEqual(file_obj.read(), original)

    def test_full_inventories_still_validate_all_saved_setups(self):
        self._save_legacy()
        self._save_current()
        for inventory in (load_all_setup_commands, load_all_setup_cache_records):
            with self.subTest(inventory=inventory.__name__), self.assertRaisesRegex(StateReadError, "Retired setup fields"):
                inventory()

    def test_argument_name_and_csv_tags_take_precedence_over_display_metadata(self):
        path = self._save_current()
        with open(path, encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        data.update({"name": "Stale name", "tags": ["stale-tag"]})
        data["args"]["tags"] = " agents , , production "
        write_json_atomic(path, data)
        self.assertIsNone(load_setup_command("Stale name"))
        self.assertIsNone(load_setup_command("stale-tag"))
        self.assertEqual(load_setup_command("New agent").host, "192.168.0.50")
        self.assertEqual(load_setup_command("production").tags, ["agents", "production"])

    def test_shared_tag_retains_friendly_name_ordering(self):
        self._save_current(host="192.168.0.25", friendly_name="Zebra", tags=["shared"])
        self._save_current(host="192.168.0.50", friendly_name="Alpha", tags=["shared"])
        self.assertEqual(load_setup_command("shared").host, "192.168.0.50")

    def test_invalid_lookup_metadata_cannot_be_treated_as_unrelated(self):
        path = self._save_current()
        with open(path, encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        data["args"]["tags"] = [123]
        write_json_atomic(path, data)
        with self.assertRaisesRegex(StateReadError, "lookup name or tags"):
            load_setup_command("unmatched-name")

    def test_disappeared_selected_setup_is_not_a_cache_miss(self):
        self._save_current()
        with patch("lib.cache._load_cache_file", return_value=None):
            with self.assertRaisesRegex(StateReadError, "disappeared during lookup"):
                load_setup_command("New agent")


class TestMergeSetupConfigs(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_basic_merge(self):
        cached = self._make_config(timezone='UTC')
        new = self._make_config(timezone='America/New_York')
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.timezone, 'America/New_York')

    def test_none_values_not_overwritten(self):
        cached = self._make_config(timezone='America/New_York')
        new = self._make_config(timezone=None)
        merged = merge_setup_configs(cached, new)
        # None should not overwrite existing value
        self.assertEqual(merged.timezone, 'America/New_York')

    def test_preserve_keys_keep_cached_values(self):
        cached = self._make_config(system_type='server_web', machine_type='unprivileged')
        new = self._make_config(system_type='server_web', machine_type='vm')
        merged = merge_setup_configs(cached, new, preserve_keys={'machine_type'})
        self.assertEqual(merged.machine_type, 'unprivileged')

    def test_deploy_specs_merge_no_duplicates(self):
        cached = self._make_config(deploy_specs=[['example.com/', 'https://git.com/repo1']])
        new = self._make_config(deploy_specs=[['example.com/', 'https://git.com/repo1'], ['other.com/', 'https://git.com/repo2']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(len(merged.deploy_specs), 2)

    def test_samba_shares_merge(self):
        cached = self._make_config(samba_shares=[['read', 'share1', '/mnt/data', 'u:p']])
        new = self._make_config(samba_shares=[['write', 'share2', '/mnt/docs', 'u:p']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(len(merged.samba_shares), 2)

    def test_share_credentials_merge(self):
        cached = self._make_config(share_credentials=[['user1', 'pass1']])
        new = self._make_config(share_credentials=[['user2', 'pass2']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.share_credentials, [['user1', 'pass1'], ['user2', 'pass2']])

    def test_share_credentials_update_existing(self):
        cached = self._make_config(share_credentials=[['user1', 'oldpass']])
        new = self._make_config(share_credentials=[['user1', 'newpass']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.share_credentials, [['user1', 'newpass']])

    def test_share_credentials_mixed_update_and_add(self):
        cached = self._make_config(share_credentials=[['user1', 'pass1']])
        new = self._make_config(share_credentials=[['user1', 'updated'], ['user2', 'pass2']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.share_credentials, [['user1', 'updated'], ['user2', 'pass2']])

    def test_share_credentials_none_cached(self):
        cached = self._make_config(share_credentials=None)
        new = self._make_config(share_credentials=[['user1', 'pass1']])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.share_credentials, [['user1', 'pass1']])

    def test_tags_overwritten(self):
        cached = self._make_config(tags=['old'])
        new = self._make_config(tags=['new1', 'new2'])
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.tags, ['new1', 'new2'])

    def test_host_system_type_preserved(self):
        cached = self._make_config(host='host1', system_type='server_lite')
        new = self._make_config(host='host2', system_type='server_web')
        merged = merge_setup_configs(cached, new)
        self.assertEqual(merged.host, 'host1')
        self.assertEqual(merged.system_type, 'server_lite')


if __name__ == '__main__':
    unittest.main()
