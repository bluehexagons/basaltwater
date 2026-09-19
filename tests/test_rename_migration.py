"""Recent-installation cutover tests; all service/account calls are mocked."""

from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import rename_migration as migration


class RenameMigrationTests(unittest.TestCase):
    def setUp(self):
        groups = patch('lib.rename_migration.grp.getgrnam', side_effect=KeyError)
        groups.start()
        self.addCleanup(groups.stop)

    def write(self, root: Path, name: str, content: str, mode: int = 0o600) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
        return path

    def test_preview_is_read_only_and_private_data_moves_without_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = self.write(root, '.config/infra_tools/credentials.json', '{"secret":"infra_tools stays secret"}')
            inode, mode = secret.stat().st_ino, secret.stat().st_mode
            plan = migration.build_plan(root, system=False)
            self.assertTrue(secret.exists())
            self.assertFalse((root / '.config/basaltwater').exists())
            migration.apply_plan(plan)
            new = root / '.config/basaltwater/credentials.json'
            self.assertEqual(new.read_text(), '{"secret":"infra_tools stays secret"}')
            self.assertEqual((new.stat().st_ino, new.stat().st_mode), (inode, mode))
            self.assertFalse(os.path.lexists(root / '.config/infra_tools'))
            self.assertEqual(migration.build_plan(root, system=False)['actions'], [])
            self.assertEqual(stat.S_IMODE((Path(plan['recovery']) / 'journal.json').stat().st_mode), 0o600)

    def test_disjoint_historical_spellings_merge_without_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '.config/infra_tools/setups/a.json', '{}')
            self.write(root, '.config/infra-tools/git/identity.json', '{}')
            migration.apply_plan(migration.build_plan(root, system=False))
            self.assertTrue((root / '.config/basaltwater/setups/a.json').exists())
            self.assertTrue((root / '.config/basaltwater/git/identity.json').exists())
            self.assertFalse((root / '.config/infra-tools').exists())

    def test_conflicting_files_fail_before_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('infra_tools', 'basaltwater'):
                self.write(root, f'.config/{name}/credentials.json', name)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                migration.build_plan(root, system=False)
            self.assertEqual((root / '.config/infra_tools/credentials.json').read_text(), 'infra_tools')

    def test_symlinked_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.config').mkdir()
            (root / '.config/infra_tools').symlink_to(root)
            with self.assertRaisesRegex(ValueError, 'Unexpected legacy'):
                migration.build_plan(root, system=False)

    def test_saved_commands_and_paths_are_updated_but_passwords_are_opaque(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '.config/infra_tools/setups/a.json', json.dumps({
                'command': 'infra-tools setup server_lite host',
                'path': '/opt/infra_tools/state', 'password': '/opt/infra_tools/private',
            }))
            migration.apply_plan(migration.build_plan(root, system=False))
            value = json.loads((root / '.config/basaltwater/setups/a.json').read_text())
            self.assertEqual(value['command'], 'basaltw setup server_lite host')
            self.assertEqual(value['path'], '/opt/basaltwater/state')
            self.assertEqual(value['password'], '/opt/infra_tools/private')

    def test_skills_are_replaced_with_current_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '.agents/skills/infra-tools-agent-workspace/SKILL.md', 'managed-by: infra_tools\nold instructions')
            migration.apply_plan(migration.build_plan(root, system=False))
            new = root / '.agents/skills/basaltwater-agent-workspace/SKILL.md'
            self.assertIn('name: basaltwater-agent-workspace', new.read_text())
            self.assertFalse((root / '.agents/skills/infra-tools-agent-workspace').exists())

    def fixture_server(self, root: Path) -> Path:
        self.write(root, 'opt/infra_tools/infra_tools.py', '# old CLI')
        self.write(root, 'opt/infra_tools/lib/installation_info.py', '# recent provenance')
        self.write(root, 'var/lib/infra_tools/setup.json', '{"username":"agent","system_type":"agent_vm"}')
        (root / 'opt/infra_tools/state').symlink_to(root / 'var/lib/infra_tools')
        self.write(root, 'etc/systemd/system/infra-tools-web-panel.service', '[Unit]\nDocumentation=https://github.com/bluehexagons/infra_tools\n[Service]\nExecStart=/usr/bin/python3 /opt/infra_tools/common/service_tools/web_panel_service.py\n', 0o644)
        return Path(migration.__file__).resolve().parents[1]

    def copy_runtime(self, destination):
        self.write(Path(destination), 'basaltwater.py', '# new runtime')

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    @patch('lib.setup_common.copy_project_files')
    def test_server_cutover_stops_old_service_before_starting_new(self, copy, run, _account):
        copy.side_effect = self.copy_runtime
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.fixture_server(root)
            plan = migration.build_plan(root, system=True, runtime_source=source)
            migration.apply_plan(plan)
            calls = [c.args[0] for c in run.call_args_list]
            self.assertLess(calls.index(['systemctl', 'stop', 'infra-tools-web-panel.service']), calls.index(['systemctl', 'start', 'basaltwater-web-panel.service']))
            self.assertFalse((root / 'opt/infra_tools').exists())
            self.assertTrue((root / 'opt/basaltwater/basaltwater.py').is_file())
            self.assertEqual((root / 'opt/basaltwater/state').resolve(), root / 'var/lib/basaltwater')
            self.assertFalse((root / 'etc/systemd/system/infra-tools-web-panel.service').exists())
            self.assertIn('/opt/basaltwater/', (root / 'etc/systemd/system/basaltwater-web-panel.service').read_text())
            self.assertEqual(migration.build_plan(root, system=True, runtime_source=source)['actions'], [])

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    @patch('lib.setup_common.copy_project_files')
    def test_failed_service_start_can_recover_original_state_and_runtime(self, copy, run, _account):
        copy.side_effect = self.copy_runtime
        def fail_start(command, **kwargs):
            if command == ['systemctl', 'start', 'basaltwater-web-panel.service']:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, '', '')
        run.side_effect = fail_start
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.fixture_server(root)
            plan = migration.build_plan(root, system=True, runtime_source=source)
            with self.assertRaises(subprocess.CalledProcessError):
                migration.apply_plan(plan)
            run.side_effect = None
            run.return_value = subprocess.CompletedProcess([], 0, '', '')
            migration.recover(root, system=True)
            self.assertTrue((root / 'opt/infra_tools/infra_tools.py').is_file())
            self.assertTrue((root / 'var/lib/infra_tools/setup.json').is_file())
            self.assertTrue((root / 'etc/systemd/system/infra-tools-web-panel.service').is_file())
            self.assertFalse((root / 'etc/systemd/system/basaltwater-web-panel.service').exists())

    def test_historical_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'opt/infra_tools/infra_tools.py', '# historical release')
            with self.assertRaisesRegex(ValueError, 'Only recent'):
                migration.build_plan(root, system=True)

    def test_interruption_after_rename_recovers_from_pending_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self.write(root, '.config/infra_tools/credentials.json', 'private')
            plan = migration.build_plan(root, system=False)
            save = migration._save
            def fail_after_move(value):
                if value['completed'] == 1:
                    raise OSError('simulated interruption after rename')
                save(value)
            with patch.object(migration, '_save', side_effect=fail_after_move):
                with self.assertRaisesRegex(OSError, 'interruption'):
                    migration.apply_plan(plan)
            self.assertFalse(original.exists())
            migration.recover(root, system=False)
            self.assertEqual(original.read_text(), 'private')
            self.assertFalse((root / '.config/basaltwater').exists())

    def test_agent_jsonc_updates_only_owned_registration_and_preserves_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write(root, '.config/opencode/opencode.jsonc', '''{
                // Existing user settings and a managed MCP server.
                "token": "infra_tools-private-value",
                "mcp": {"infra-tools-playwright": {
                    "command": ["/usr/local/bin/infra-tools-playwright-mcp"],
                    "environment": {"TOKEN": "infra_tools-private-value"},
                }},
            }''')
            migration.apply_plan(migration.build_plan(root, system=False))
            data = json.loads(config.read_text())
            self.assertEqual(data['token'], 'infra_tools-private-value')
            server = data['mcp']['basaltwater-playwright']
            self.assertEqual(server['environment']['TOKEN'], 'infra_tools-private-value')
            self.assertEqual(server['command'], ['/usr/local/bin/basaltwater-playwright-mcp'])
            self.assertNotIn('infra-tools-playwright', data['mcp'])

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    def test_active_nested_operation_lock_refuses_data_changes(self, run, _accounts):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = self.write(root, 'run/lock/infra_tools/operations/host.lock', '')
            saved = self.write(root, 'var/lib/infra_tools/setup.json', '{}')
            plan = migration.build_plan(root, system=True)
            with lock.open('r+') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'Active operation'):
                    migration.apply_plan(plan)
            self.assertTrue(saved.exists())
            self.assertFalse((root / 'var/lib/basaltwater').exists())
            migration.recover(root, system=True)

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    @patch('lib.setup_common.copy_project_files')
    def test_inactive_disabled_unit_is_not_started(self, copy, run, _account):
        copy.side_effect = self.copy_runtime
        def result(command, **kwargs):
            code = 3 if 'is-active' in command or 'is-enabled' in command else 0
            return subprocess.CompletedProcess(command, code, '', '')
        run.side_effect = result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.fixture_server(root)
            migration.apply_plan(migration.build_plan(root, system=True, runtime_source=source))
            commands = [call.args[0] for call in run.call_args_list]
            self.assertFalse(any('start' in command or 'enable' in command for command in commands))

    def test_unmanaged_launcher_is_refused_before_data_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write(root, '.config/infra_tools/credentials.json', 'private')
            self.write(root, '.local/bin/infra-tools', '#!/bin/sh\nexit 17\n', 0o755)
            with self.assertRaisesRegex(ValueError, 'Unmanaged or package-owned'):
                migration.build_plan(root, system=False)
            self.assertEqual(source.read_text(), 'private')

    def test_inline_secret_values_are_not_rebranded(self):
        text = 'Environment=INFRA_TOOLS_TOKEN=infra_tools-secret\nExecStart=/opt/infra_tools/infra_tools.py\n'
        updated = migration._configuration_text(text)
        self.assertIn('BASALTWATER_TOKEN=infra_tools-secret', updated)
        self.assertIn('ExecStart=/opt/basaltwater/basaltwater.py', updated)

    def test_fish_completion_and_environment_secret_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, '.config/fish/completions/infra-tools.fish', 'complete -c infra-tools\n')
            self.write(root, '.config/infra_tools/service.env', 'INFRA_TOOLS_TOKEN=infra_tools-private\n')
            migration.apply_plan(migration.build_plan(root, system=False))
            self.assertEqual((root / '.config/fish/completions/basaltw.fish').read_text(), 'complete -c basaltw\n')
            self.assertFalse((root / '.config/fish/completions/infra-tools.fish').exists())
            self.assertEqual((root / '.config/basaltwater/service.env').read_text(), 'BASALTWATER_TOKEN=infra_tools-private\n')

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    def test_host_dropins_binary_keyrings_and_swap_markers_migrate(self, run, _accounts):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'etc/systemd/system/xrdp.service.d/infra-tools.conf', '[Service]\nExecStart=/usr/local/libexec/infra-tools-xrdp-Xorg\n')
            self.write(root, 'etc/fstab', '# BEGIN infra-tools managed swap\n/swapfile none swap sw 0 0\n# END infra-tools managed swap\n')
            key = self.write(root, 'usr/share/keyrings/infra-tools-microsoft.gpg', '')
            key.write_bytes(b'\xff\x00infra-tools-opaque-key')
            migration.apply_plan(migration.build_plan(root, system=True))
            self.assertIn('/usr/local/libexec/basaltwater-xrdp-Xorg', (root / 'etc/systemd/system/xrdp.service.d/basaltwater.conf').read_text())
            self.assertNotIn('infra-tools', (root / 'etc/fstab').read_text())
            self.assertEqual((root / 'usr/share/keyrings/basaltwater-microsoft.gpg').read_bytes(), b'\xff\x00infra-tools-opaque-key')


if __name__ == '__main__':
    unittest.main()
