"""Tests for lib/machine_state.py: machine state checks with mocked state files."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import lib.machine_state as ms
from lib.config import DEFAULT_MACHINE_TYPE


class TestInvalidStateRecovery(unittest.TestCase):
    def test_invalid_existing_state_cannot_be_overwritten_by_save(self):
        for content in ('not json', '[]', '{"version":2}', '{"version":true}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "machine.json")
                with open(path, "w") as file:
                    file.write(content)
                with patch.object(ms, "STATE_FILE", path), patch.object(ms, "STATE_DIR", directory):
                    for operation in (ms.can_modify_kernel, lambda: ms.save_machine_state("vm", "server_lite", "agent")):
                        with self.assertRaisesRegex(ms.StateReadError, "File retained"):
                            operation()
                with open(path) as file:
                    self.assertEqual(file.read(), content)

    def test_unsafe_state_files_fail_without_blocking(self):
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "machine.json")
                if kind == "symlink":
                    os.symlink(os.path.join(directory, "missing"), path)
                else:
                    os.mkfifo(path)
                with patch.object(ms, "STATE_FILE", path):
                    with self.assertRaises(ms.StateReadError):
                        ms.load_machine_state()


class TestMachineStateHelpers(unittest.TestCase):
    """Test machine type helper functions by mocking load_machine_state."""

    def _patch_machine_type(self, machine_type):
        return patch.object(ms, 'load_machine_state', return_value={
            'machine_type': machine_type,
            'system_type': 'server_lite',
            'username': 'test',
        })

    def test_is_unprivileged(self):
        with self._patch_machine_type('unprivileged'):
            self.assertTrue(ms.is_unprivileged())
            self.assertTrue(ms.is_container())
            self.assertFalse(ms.can_modify_kernel())
            self.assertFalse(ms.can_manage_swap())

    def test_is_oci(self):
        with self._patch_machine_type('oci'):
            self.assertTrue(ms.is_oci())
            self.assertTrue(ms.is_container())
            self.assertFalse(ms.can_restart_system())

    def test_is_vm(self):
        with self._patch_machine_type('vm'):
            self.assertTrue(ms.is_vm())
            self.assertFalse(ms.is_container())
            self.assertTrue(ms.can_modify_kernel())
            self.assertTrue(ms.can_manage_swap())
            self.assertTrue(ms.can_manage_firewall())
            self.assertTrue(ms.can_manage_time_sync())

    def test_is_privileged(self):
        with self._patch_machine_type('privileged'):
            self.assertTrue(ms.is_privileged_container())
            self.assertFalse(ms.is_container())
            self.assertTrue(ms.can_modify_kernel())

    def test_is_hardware(self):
        with self._patch_machine_type('hardware'):
            self.assertTrue(ms.is_hardware())
            self.assertFalse(ms.is_container())
            self.assertTrue(ms.can_modify_kernel())
            self.assertTrue(ms.can_restart_system())

    def test_can_restart_system_not_oci(self):
        for mt in ('unprivileged', 'vm', 'privileged', 'hardware'):
            with self._patch_machine_type(mt):
                self.assertTrue(ms.can_restart_system(), f"Expected can_restart_system=True for {mt}")

    def test_system_services_are_not_managed_in_oci(self):
        with self._patch_machine_type('oci'):
            self.assertFalse(ms.can_manage_system_services())
        with self._patch_machine_type('unprivileged'):
            self.assertTrue(ms.can_manage_system_services())

    def test_auto_machine_type_resolves_from_runtime(self):
        with self._patch_machine_type('auto'), \
             patch.object(ms, 'detect_machine_type', return_value='vm'):
            self.assertTrue(ms.is_vm())

    def test_time_sync_can_use_current_type_instead_of_saved_state(self):
        with self._patch_machine_type('hardware'):
            self.assertFalse(ms.can_manage_time_sync('unprivileged'))

    def test_firewall_can_use_current_type_instead_of_saved_state(self):
        with self._patch_machine_type('hardware'):
            self.assertFalse(ms.can_manage_firewall('unprivileged'))

    def test_machine_type_context_overrides_and_restores_saved_state(self):
        with self._patch_machine_type('hardware'):
            self.assertTrue(ms.is_hardware())
            with ms.machine_type_context('unprivileged'):
                self.assertTrue(ms.is_unprivileged())
                with ms.machine_type_context('vm'):
                    self.assertTrue(ms.is_vm())
                self.assertTrue(ms.is_unprivileged())
            self.assertTrue(ms.is_hardware())

    def test_machine_type_context_restores_state_after_failure(self):
        with self._patch_machine_type('hardware'):
            with self.assertRaisesRegex(RuntimeError, 'setup failed'):
                with ms.machine_type_context('unprivileged'):
                    raise RuntimeError('setup failed')
            self.assertTrue(ms.is_hardware())


class TestMachineTypeDetection(unittest.TestCase):
    def test_detects_lxc_as_unprivileged(self):
        with patch.object(ms, '_systemd_detect_virt', return_value='lxc'):
            self.assertEqual(ms.detect_machine_type(), 'unprivileged')

    def test_detects_oci_containers(self):
        with patch.object(ms, '_systemd_detect_virt', return_value='docker'):
            self.assertEqual(ms.detect_machine_type(), 'oci')

    def test_detects_virtual_machines(self):
        with patch.object(ms, '_systemd_detect_virt', return_value='kvm'):
            self.assertEqual(ms.detect_machine_type(), 'vm')

    def test_falls_back_to_bare_metal(self):
        with patch.object(ms, '_systemd_detect_virt', return_value='none'), \
             patch.object(ms, '_read_text', return_value=None), \
             patch.object(ms.os.path, 'exists', return_value=False):
            self.assertEqual(ms.detect_machine_type(), 'hardware')

    def test_fallback_detects_lxc_from_cgroup(self):
        with patch.object(ms, '_systemd_detect_virt', return_value=None), \
             patch.object(ms, '_read_text', side_effect=[None, '0::/lxc/100']):
            self.assertEqual(ms.detect_machine_type(), 'unprivileged')

    def test_fallback_detects_oci_from_container_marker(self):
        with patch.object(ms, '_systemd_detect_virt', return_value=None), \
             patch.object(ms, '_read_text', side_effect=[None, None]), \
             patch.object(ms.os.path, 'exists', side_effect=[True, False]):
            self.assertEqual(ms.detect_machine_type(), 'oci')


class TestSaveLoadMachineState(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, 'machine.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'STATE_FILE', state_file):
                ms.save_machine_state('vm', 'server_dev', 'testuser')
                state = ms.load_machine_state()
                self.assertEqual(state['machine_type'], 'vm')
                self.assertEqual(state['system_type'], 'server_dev')
                self.assertEqual(state['username'], 'testuser')

    def test_load_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, 'nonexistent.json')
            with patch.object(ms, 'STATE_FILE', state_file):
                state = ms.load_machine_state()
                self.assertEqual(state['machine_type'], DEFAULT_MACHINE_TYPE)
                self.assertIsNone(state['system_type'])

    def test_load_corrupt_file_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, 'corrupt.json')
            with open(state_file, 'w') as f:
                f.write('not valid json')
            with patch.object(ms, 'STATE_FILE', state_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_machine_state()

    def test_save_with_extra_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, 'machine.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'STATE_FILE', state_file):
                ms.save_machine_state('hardware', 'server_web', 'admin', extra_data={'gpu': True})
                state = ms.load_machine_state()
                self.assertTrue(state.get('gpu'))


class TestSaveLoadSetupConfig(unittest.TestCase):
    def test_save_and_load_setup_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'setup.json')
            notification_file = os.path.join(tmpdir, 'notifications.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'SETUP_CONFIG_FILE', config_file), \
                 patch.object(ms, 'NOTIFICATION_CONFIG_FILE', notification_file):
                ms.save_setup_config({'timezone': 'UTC', 'username': 'test',
                                      'host': '10.0.0.1', 'system_type': 'server_lite'})
                loaded = ms.load_setup_config()
                assert loaded is not None
                self.assertEqual(loaded['timezone'], 'UTC')
                notification_state = ms.load_notification_state()
                assert notification_state is not None
                self.assertEqual(notification_state['notify_specs'], [])
                self.assertFalse(notification_state['notification_strict_https'])

    def test_save_setup_config_excludes_password(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'setup.json')
            notification_file = os.path.join(tmpdir, 'notifications.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'SETUP_CONFIG_FILE', config_file), \
                 patch.object(ms, 'NOTIFICATION_CONFIG_FILE', notification_file):
                ms.save_setup_config({
                    'username': 'test',
                    'system_type': 'workstation_dev',
                    'password': 'supersecret',
                })

                loaded = ms.load_setup_config()

            assert loaded is not None
            self.assertNotIn('password', loaded)

    def test_save_setup_config_writes_only_notification_subset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'setup.json')
            notification_file = os.path.join(tmpdir, 'notifications.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'SETUP_CONFIG_FILE', config_file), \
                 patch.object(ms, 'NOTIFICATION_CONFIG_FILE', notification_file):
                ms.save_setup_config({
                    'username': 'test',
                    'system_type': 'server_web',
                    'notify_specs': [['webhook', 'https://example.com/hook#token']],
                    'notification_level': 'warning',
                    'notification_strict_https': True,
                    'git_auth_token': 'must-not-be-copied',
                })

                notification_state = ms.load_notification_state()

            assert notification_state is not None
            self.assertEqual(
                notification_state['notify_specs'],
                [['webhook', 'https://example.com/hook#token']],
            )
            self.assertEqual(notification_state['notification_level'], 'warning')
            self.assertTrue(notification_state['notification_strict_https'])
            self.assertNotIn('git_auth_token', notification_state)

    @patch.object(ms.pwd, 'getpwnam', return_value=SimpleNamespace(pw_gid=1234))
    @patch.object(ms, 'write_json_atomic')
    def test_notification_state_uses_target_primary_group(self, mock_write, _getpwnam):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'setup.json')
            notification_file = os.path.join(tmpdir, 'notifications.json')
            with patch.object(ms, 'STATE_DIR', tmpdir), \
                 patch.object(ms, 'SETUP_CONFIG_FILE', config_file), \
                 patch.object(ms, 'NOTIFICATION_CONFIG_FILE', notification_file):
                ms.save_setup_config({
                    'username': 'agent',
                    'system_type': 'server_web',
                    'notify_specs': [],
                })

        notification_call = mock_write.call_args_list[-1]
        self.assertEqual(notification_call.args[0], notification_file)
        self.assertEqual(notification_call.kwargs['mode'], 0o640)
        self.assertEqual(notification_call.kwargs['gid'], 1234)

    def test_load_missing_notification_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            notification_file = os.path.join(tmpdir, 'missing.json')
            with patch.object(ms, 'NOTIFICATION_CONFIG_FILE', notification_file):
                self.assertIsNone(ms.load_notification_state())

    def test_load_setup_config_removes_legacy_password_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'setup.json')
            ms.write_json_atomic(config_file, {
                'username': 'test',
                'system_type': 'workstation_dev',
                'password': 'supersecret',
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                loaded = ms.load_setup_config()

            assert loaded is not None
            self.assertNotIn('password', loaded)
            with open(config_file, encoding='utf-8') as file_obj:
                self.assertNotIn('supersecret', file_obj.read())

    def test_load_missing_setup_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'no_such.json')
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                self.assertIsNone(ms.load_setup_config())

    def test_load_corrupt_setup_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, 'bad.json')
            with open(config_file, 'w') as f:
                f.write('{broken')
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_setup_config()


class TestMachineStateValidation(unittest.TestCase):
    """Test structural validation of loaded machine state."""

    def _write_state(self, tmpdir, data):
        state_file = os.path.join(tmpdir, 'machine.json')
        import json
        with open(state_file, 'w') as f:
            json.dump(data, f)
        return state_file

    def test_valid_state_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self._write_state(tmpdir, {
                'machine_type': 'vm', 'system_type': 'server_dev', 'username': 'admin'
            })
            with patch.object(ms, 'STATE_FILE', state_file):
                state = ms.load_machine_state()
                self.assertEqual(state['machine_type'], 'vm')

    def test_missing_required_key_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Missing 'username'
            state_file = self._write_state(tmpdir, {
                'machine_type': 'vm', 'system_type': 'server_dev'
            })
            with patch.object(ms, 'STATE_FILE', state_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_machine_state()

    def test_unknown_machine_type_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self._write_state(tmpdir, {
                'machine_type': 'quantum_computer', 'system_type': 'server_dev', 'username': 'test'
            })
            with patch.object(ms, 'STATE_FILE', state_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_machine_state()

    def test_json_list_instead_of_dict_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self._write_state(tmpdir, ["not", "a", "dict"])
            with patch.object(ms, 'STATE_FILE', state_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_machine_state()

    def test_null_machine_type_accepted(self):
        """machine_type=None is accepted (edge case for partial state)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self._write_state(tmpdir, {
                'machine_type': None, 'system_type': None, 'username': None
            })
            with patch.object(ms, 'STATE_FILE', state_file):
                state = ms.load_machine_state()
                self.assertIsNone(state['machine_type'])

    def test_extra_keys_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = self._write_state(tmpdir, {
                'machine_type': 'hardware', 'system_type': 'server_web',
                'username': 'root', 'gpu': True, 'custom_flag': 42
            })
            with patch.object(ms, 'STATE_FILE', state_file):
                state = ms.load_machine_state()
                self.assertTrue(state['gpu'])
                self.assertEqual(state['custom_flag'], 42)


class TestSetupConfigValidation(unittest.TestCase):
    """Test structural validation of loaded setup config."""

    def _write_config(self, tmpdir, data):
        config_file = os.path.join(tmpdir, 'setup.json')
        import json
        with open(config_file, 'w') as f:
            json.dump(data, f)
        return config_file

    def test_valid_config_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, {
                'host': '10.0.0.1', 'username': 'admin', 'system_type': 'server_lite'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                config = ms.load_setup_config()
                assert config is not None
                self.assertEqual(config['host'], '10.0.0.1')

    def test_missing_required_key_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Missing 'username' (required for runtime operations)
            config_file = self._write_config(tmpdir, {
                'system_type': 'server_lite'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_setup_config()

    def test_unknown_system_type_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, {
                'host': '10.0.0.1', 'username': 'admin', 'system_type': 'moon_base'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_setup_config()

    def test_unknown_machine_type_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, {
                'host': '10.0.0.1', 'username': 'admin',
                'system_type': 'server_lite', 'machine_type': 'invalid'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_setup_config()

    def test_json_list_instead_of_dict_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, [1, 2, 3])
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                with self.assertRaises(ms.StateReadError):
                    ms.load_setup_config()

    def test_valid_machine_type_in_config_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, {
                'host': '10.0.0.1', 'username': 'admin',
                'system_type': 'server_lite', 'machine_type': 'vm'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                config = ms.load_setup_config()
                assert config is not None
                self.assertEqual(config['machine_type'], 'vm')

    def test_no_machine_type_key_accepted(self):
        """Config without machine_type is valid (it's optional)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = self._write_config(tmpdir, {
                'host': '10.0.0.1', 'username': 'admin', 'system_type': 'server_dev'
            })
            with patch.object(ms, 'SETUP_CONFIG_FILE', config_file):
                config = ms.load_setup_config()
                assert config is not None
                self.assertNotIn('machine_type', config)


if __name__ == '__main__':
    unittest.main()
