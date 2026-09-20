"""Recent-installation cutover tests; all service/account calls are mocked."""

from __future__ import annotations

import json
import errno
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib import rename_migration as migration


class RenameMigrationTests(unittest.TestCase):
    def setUp(self):
        groups = patch('lib.rename_migration.grp.getgrnam', side_effect=KeyError)
        groups.start()
        self.addCleanup(groups.stop)
        accounts = patch('lib.rename_migration.pwd.getpwall', return_value=[])
        accounts.start()
        self.addCleanup(accounts.stop)

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

    def worktree_fixture(self, root: Path) -> tuple[Path, Path]:
        tree = root / '.local/share/infra_tools/worktrees/project/task'
        metadata = root / 'repos/project/.git/worktrees/task'
        self.write(tree, '.git', f'gitdir: {metadata}\n')
        self.write(metadata, 'commondir', '../..\n')
        self.write(metadata, 'gitdir', f'{tree}/.git\n')
        self.write(metadata, 'HEAD', 'ref: refs/heads/agent/task\n')
        self.write(metadata, 'index', 'opaque staged changes')
        self.write(tree, 'local.env', 'INFRA_TOOLS_VALUE=user-owned\n')
        self.write(tree, 'state.json', json.dumps({'path': str(tree)}))
        (tree / 'link').symlink_to('infra_tools-user-file')
        return tree, metadata

    def test_linked_worktrees_move_with_dirty_files_and_registration_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree, metadata = self.worktree_fixture(root)
            before = {name: (tree / name).read_bytes() for name in ('local.env', 'state.json')}
            # Relative Git pointers must remain valid after the path changes.
            (tree / '.git').write_text(f'gitdir: {os.path.relpath(metadata, tree)}\n')
            plan = migration.build_plan(root, system=False)
            self.assertEqual((metadata / 'gitdir').read_text(), f'{tree}/.git\n')
            migration.apply_plan(plan)
            new = root / '.local/share/basaltwater/worktrees/project/task'
            self.assertFalse(tree.exists())
            self.assertEqual((new / '.git').read_text(), f'gitdir: {metadata}\n')
            self.assertEqual((metadata / 'gitdir').read_text(), f'{new}/.git\n')
            self.assertEqual((metadata / 'index').read_text(), 'opaque staged changes')
            self.assertEqual((metadata / 'HEAD').read_text(), 'ref: refs/heads/agent/task\n')
            for name, content in before.items():
                self.assertEqual((new / name).read_bytes(), content)
            self.assertEqual(os.readlink(new / 'link'), 'infra_tools-user-file')
            self.assertEqual(migration.build_plan(root, system=False)['actions'], [])

    def test_worktree_registration_recovers_after_interrupted_cutover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree, metadata = self.worktree_fixture(root)
            plan = migration.build_plan(root, system=False)
            with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    migration.apply_plan(plan)
            migration.recover(root, system=False)
            self.assertEqual((tree / '.git').read_text(), f'gitdir: {metadata}\n')
            self.assertEqual((metadata / 'gitdir').read_text(), f'{tree}/.git\n')

    def test_worktree_metadata_conflicts_and_links_fail_before_moves(self):
        for unsafe in ('mismatch', 'symlink', 'moving-common'):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                tree, metadata = self.worktree_fixture(root)
                if unsafe == 'mismatch':
                    (metadata / 'gitdir').write_text('/unrelated/.git\n')
                elif unsafe == 'symlink':
                    (metadata / 'gitdir').unlink()
                    (metadata / 'gitdir').symlink_to(metadata / 'HEAD')
                else:
                    destination = root / '.local/share/infra_tools/common'
                    (root / 'repos/project/.git').rename(destination)
                    (tree / '.git').write_text(f'gitdir: {destination}/worktrees/task\n')
                with self.assertRaises(ValueError):
                    migration.build_plan(root, system=False)
                self.assertTrue(tree.exists())
                self.assertFalse((root / '.local/state/basaltwater-migration').exists())

    def test_conflicting_files_fail_before_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('infra_tools', 'basaltwater'):
                self.write(root, f'.config/{name}/credentials.json', name)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                migration.build_plan(root, system=False)
            self.assertEqual((root / '.config/infra_tools/credentials.json').read_text(), 'infra_tools')

    def provision_lock_pair(self, root: Path) -> tuple[Path, Path]:
        name = "provision-" + "a" * 64 + ".lock"
        return tuple(self.write(root, f"run/lock/{brand}/{name}", "")
                     for brand in ("infra-tools", "basaltwater"))

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_idle_duplicate_provision_locks_preserve_canonical_inode(self, _account):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = self.provision_lock_pair(root)
            old_inode, new_inode = old.stat().st_ino, new.stat().st_ino
            self.write(root, 'run/lock/infra-tools/unique.lock', '')
            plan = migration.build_plan(root, system=True)
            self.assertTrue(old.exists())  # Preview remains read-only.
            migration.apply_plan(plan)
            self.assertEqual(new.stat().st_ino, new_inode)
            self.assertFalse(old.parent.exists())
            self.assertTrue((new.parent / 'unique.lock').exists())
            retired = list(Path(plan['recovery']).glob('retired-*'))
            self.assertEqual([path.stat().st_ino for path in retired], [old_inode])
            self.assertEqual(migration.build_plan(root, system=True)['actions'], [])

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_active_duplicate_provision_lock_in_either_namespace_blocks(self, _account):
        for index in (0, 1):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                locks = self.provision_lock_pair(root)
                saved = self.write(root, 'var/lib/infra_tools/setup.json', '{}')
                plan = migration.build_plan(root, system=True)
                with locks[index].open('r+') as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(ValueError, 'Active operation'):
                        migration.apply_plan(plan)
                self.assertTrue(saved.exists())
                self.assertTrue(all(path.exists() for path in locks))
                self.assertFalse(Path(plan['recovery']).exists())

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_two_legacy_lock_spellings_merge_when_canonical_directory_is_absent(self, _account):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, canonical = self.provision_lock_pair(root)
            canonical.parent.rename(canonical.parent.with_name('infra_tools'))
            inode = old.stat().st_ino
            migration.apply_plan(migration.build_plan(root, system=True))
            self.assertEqual(canonical.stat().st_ino, inode)
            self.assertFalse(old.exists())

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_cross_filesystem_lock_retirement_and_recovery(self, _account):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, canonical = self.provision_lock_pair(root)
            canonical_inode = canonical.stat().st_ino
            rename = Path.rename

            def across_devices(source, destination):
                if source.name.startswith('provision-') or source.name.startswith('retired-'):
                    raise OSError(errno.EXDEV, 'Invalid cross-device link')
                return rename(source, destination)

            plan = migration.build_plan(root, system=True)
            with patch.object(Path, 'rename', across_devices):
                with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        migration.apply_plan(plan)
                self.assertFalse(old.exists())
                retired = list(Path(plan['recovery']).glob('retired-*'))
                self.assertEqual(len(retired), 1)
                self.assertEqual(retired[0].read_bytes(), b'')
                migration.recover(root, system=True)
            self.assertEqual(old.read_bytes(), b'')
            self.assertEqual(stat.S_IMODE(old.stat().st_mode), 0o600)
            self.assertEqual(canonical.stat().st_ino, canonical_inode)

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_setup_recovers_previous_cross_device_failure_then_migrates(self, _account):
        from lib.setup_upgrade import _check_journal

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, canonical = self.provision_lock_pair(root)
            original = self.write(root, 'var/lib/infra_tools/setup.json', '{}')
            plan = migration.build_plan(root, system=True)
            with patch.object(migration, '_move_empty_lock', side_effect=OSError(errno.EXDEV, 'Invalid cross-device link')):
                with self.assertRaises(OSError):
                    migration.apply_plan(plan)
            self.assertFalse(original.exists())
            with canonical.open('r+') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'Active operation'):
                    _check_journal(root, system=True)
            self.assertFalse(original.exists())
            self.assertFalse(_check_journal(root, system=True))
            self.assertTrue(original.exists())
            self.assertTrue(old.exists())
            archives = list((root / 'var/lib').glob('basaltwater-migration-recovered-*'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(json.loads((archives[0] / 'journal.json').read_text())['status'], 'recovered')
            migration.apply_plan(migration.build_plan(root, system=True))
            self.assertTrue(_check_journal(root, system=True))
            self.assertFalse(original.exists())
            self.assertTrue((root / 'var/lib/basaltwater/setup.json').exists())

    def test_lock_archive_does_not_hide_other_rename_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.write(Path(directory), 'lock', '')
            destination = Path(directory) / 'retired'
            with patch.object(Path, 'rename', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    migration._move_empty_lock(source, destination)
            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_duplicate_lock_retirement_recovers_without_replacing_canonical(self, _account):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = self.provision_lock_pair(root)
            old_inode, new_inode = old.stat().st_ino, new.stat().st_ino
            plan = migration.build_plan(root, system=True)
            with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    migration.apply_plan(plan)
            migration.recover(root, system=True)
            self.assertEqual(old.stat().st_ino, old_inode)
            self.assertEqual(new.stat().st_ino, new_inode)

    def test_duplicate_lock_exception_rejects_data_links_and_unknown_names(self):
        for unsafe in ('contents', 'symlink', 'hardlink', 'unknown'):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old, new = self.provision_lock_pair(root)
                if unsafe == 'contents':
                    old.write_text('not disposable data')
                elif unsafe == 'symlink':
                    old.unlink()
                    old.symlink_to(new)
                elif unsafe == 'hardlink':
                    os.link(old, root / 'another-link')
                else:
                    old.rename(old.with_name('unknown.lock'))
                    new.rename(new.with_name('unknown.lock'))
                with self.assertRaises(ValueError):
                    migration.build_plan(root, system=True)

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_later_cluster_node_keeps_already_migrated_shared_configuration(self, _account):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'node1', Path(directory) / 'node2'
            shared_path = 'etc/pve/firewall/cluster.fw'
            self.write(first, shared_path, '[IPSET management]\n192.0.2.0/24 # infra-tools access source\n')
            self.write(first, 'var/lib/infra_tools/setup.json', '{}')
            migration.apply_plan(migration.build_plan(first, system=True))
            # Model pmxcfs replication: the later node already sees renamed
            # cluster configuration, but still has its own legacy data/locks.
            shared = (first / shared_path).read_bytes()
            second_shared = self.write(second, shared_path, shared.decode())
            self.write(second, 'var/lib/infra_tools/setup.json', '{}')
            self.provision_lock_pair(second)
            plan = migration.build_plan(second, system=True)
            self.assertFalse(any('/etc/pve/' in action['old'] for action in plan['actions']))
            migration.apply_plan(plan)
            self.assertEqual(second_shared.read_bytes(), shared)
            self.assertTrue((second / 'var/lib/basaltwater/setup.json').exists())


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

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    @patch('lib.setup_common.copy_project_files')
    def test_runtime_and_launcher_archives_stay_on_the_source_filesystems(self, copy, run, _account):
        copy.side_effect = self.copy_runtime
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.fixture_server(root)
            for name in ('infra-tools', 'infra_tools'):
                self.write(root, f'usr/local/bin/{name}', f'#!/bin/sh\nexec python3 {root}/opt/infra_tools/infra_tools.py "$@"\n', 0o755)
            rename = Path.rename

            def within_filesystem(path, destination):
                if path.relative_to(root).parts[0] != Path(destination).relative_to(root).parts[0]:
                    raise OSError(errno.EXDEV, 'simulated separate /opt, /usr and /var')
                return rename(path, destination)

            plan = migration.build_plan(root, system=True, runtime_source=source)
            with patch.object(Path, 'rename', within_filesystem):
                with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        migration.apply_plan(plan)
                self.assertTrue((root / 'opt/basaltwater/basaltwater.py').exists())
                self.assertTrue((root / 'usr/local/bin/basaltw').exists())
                for action in plan['actions']:
                    if 'archive' in action:
                        self.assertEqual(Path(action['archive']).stat().st_mode & 0o777, 0o700)
                migration.recover(root, system=True)
            self.assertTrue((root / 'opt/infra_tools/infra_tools.py').exists())
            for name in ('infra-tools', 'infra_tools'):
                self.assertTrue((root / 'usr/local/bin' / name).exists())
            self.assertFalse((root / 'usr/local/bin/basaltw').exists())

    @patch('lib.rename_migration.subprocess.run')
    def test_user_paths_named_after_services_never_reload_host_integrations(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.worktree_fixture(root / 'nginx')
            plan = migration.build_plan(root / 'nginx', system=False)
            migration.apply_plan(plan)
            run.assert_not_called()

    @patch('lib.rename_migration.subprocess.run')
    def test_inactive_nginx_is_validated_without_attempting_reload(self, run):
        def result(command, **kwargs):
            return subprocess.CompletedProcess(command, 3 if 'is-active' in command else 0, '', '')

        run.side_effect = result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'etc/nginx/sites-available/infra-tools-web', '# Managed by infra-tools\n')
            plan = migration.build_plan(root, system=True)
            migration._reload_integrations(plan)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(['nginx', '-t'], commands)
            self.assertFalse(any('reload' in command for command in commands))

    @patch('lib.rename_migration.subprocess.run')
    def test_system_data_paths_named_nginx_do_not_reload_nginx(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'nginx'
            self.write(root, 'var/lib/infra_tools/setup.json', '{}')
            migration._reload_integrations(migration.build_plan(root, system=True))
            run.assert_not_called()

    def test_historical_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'opt/infra_tools/infra_tools.py', '# historical release')
            with self.assertRaisesRegex(ValueError, 'Only recent'):
                migration.build_plan(root, system=True)

    @patch('lib.rename_migration.subprocess.run')
    def test_systemd_settings_move_and_recover_without_changing_values(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs = {
                'etc/systemd/journald.conf.d/infra-tools.conf': '[Journal]\nSystemMaxUse=100M\n',
                'etc/systemd/zram-generator.conf.d/90-infra-tools.conf': '[zram0]\nzram-size = 4096\n',
            }
            for relative, content in configs.items():
                self.write(root, relative, content)
            with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    migration.apply_plan(migration.build_plan(root, system=True))
            for relative, content in configs.items():
                old = root / relative
                self.assertFalse(old.exists())
                self.assertEqual(old.with_name(migration.rename_text(old.name)).read_text(), content)
            migration.recover(root, system=True)
            for relative, content in configs.items():
                self.assertEqual((root / relative).read_text(), content)
            run.assert_not_called()

    def test_unfinished_unit_operations_block_migration_without_touching_backups(self):
        for brand in ('infra-tools', 'basaltwater'):
            with self.subTest(brand=brand), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write(root, 'var/lib/infra_tools/setup.json', '{}')
                marker = self.write(root, f'etc/systemd/system/.{brand}-unit-operation.json', '{}')
                backup = self.write(root, f'etc/systemd/system/.{brand}-units-fixture/previous.json', 'opaque infra-tools recovery data')
                with self.assertRaisesRegex(ValueError, 'Unfinished systemd unit replacement'):
                    migration.build_plan(root, system=True)
                self.assertEqual(marker.read_text(), '{}')
                self.assertEqual(backup.read_text(), 'opaque infra-tools recovery data')
                self.assertFalse((root / 'var/lib/basaltwater-migration').exists())

    def test_hidden_unit_backups_and_idle_locks_are_opaque(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'etc/systemd/system/.infra-tools-units-fixture/infra-tools-test.service', '[Service]\nExecStart=/opt/infra_tools/service\n')
            self.write(root, 'etc/systemd/system/.infra-tools-unit-operation.json.lock', '')
            self.assertEqual(migration.build_plan(root, system=True)['actions'], [])

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

    def test_codex_external_project_trust_survives_user_setup_retry(self):
        from lib import setup_upgrade

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = ('[projects."/home/agent/repos/infra-tools"]\ntrust_level = "untrusted"\n'
                       '[projects."/home/agent/repos/basaltwater"]\ntrust_level = "trusted"\n')
            config = self.write(root, '.codex/config.toml', content)
            before = config.stat().st_ino, config.stat().st_mode
            with patch.object(Path, 'home', return_value=root), \
                 patch('sys.argv', ['setup_upgrade', '--user-migration']):
                self.assertEqual(setup_upgrade.main(), 0)
                self.assertEqual(setup_upgrade.main(), 0)
            self.assertEqual(config.read_text(), content)
            self.assertEqual((config.stat().st_ino, config.stat().st_mode), before)
            self.assertFalse((root / '.local/state/basaltwater-migration').exists())

    def test_codex_managed_registration_migrates_without_renaming_external_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = ('[projects."/repos/infra-tools"]\ntrust_level = "untrusted"\n'
                        "['projects'.'/repos/basaltwater'] # separate repository\ntrust_level = \"trusted\"\n")
            config = self.write(root, '.codex/config.toml', projects +
                                '[mcp_servers.infra-tools-playwright]\ncommand = "/usr/local/bin/infra-tools-playwright-mcp"\n')
            migration.apply_plan(migration.build_plan(root, system=False))
            self.assertTrue(config.read_text().startswith(projects))
            self.assertIn('[mcp_servers.basaltwater-playwright]', config.read_text())
            self.assertIn('/usr/local/bin/basaltwater-playwright-mcp', config.read_text())
            self.assertEqual(migration.build_plan(root, system=False)['actions'], [])

    def test_codex_project_trust_tracks_only_moved_directory_prefix_and_recovers(self):
        for quote in ('"', "'"):
            with self.subTest(quote=quote), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old = root / '.local/share/infra_tools'
                new = old.with_name('basaltwater')
                self.write(old, 'worktrees/infra-tools/task/file.txt', 'keep')
                # Existing destination exercises directory merging too.
                self.write(new, 'other.txt', 'keep')
                content = (f'[projects.{quote}{old}/worktrees/infra-tools/task{quote}] # keep comment\n'
                           'trust_level = "trusted"\n'
                           f'[projects.{quote}{old}-other{quote}]\ntrust_level = "untrusted"\n')
                config = self.write(root, '.codex/config.toml', content)
                with patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                    with self.assertRaises(OSError):
                        migration.apply_plan(migration.build_plan(root, system=False))
                self.assertEqual(config.read_text(), content.replace(f'{old}/worktrees/', f'{new}/worktrees/'))
                migration.recover(root, system=False)
                self.assertEqual(config.read_text(), content)

    def test_codex_real_project_or_mcp_rename_collision_still_stops_before_changes(self):
        for kind in ('project', 'mcp'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                old = root / '.local/share/infra_tools'
                self.write(old, 'data.txt', 'keep')
                if kind == 'project':
                    content = (f'[projects."{old}"]\ntrust_level = "trusted"\n'
                               f'[projects."{old.with_name("basaltwater")}"]\ntrust_level = "untrusted"\n')
                else:
                    content = '[mcp_servers.infra-tools-playwright]\n[mcp_servers.basaltwater-playwright]\n'
                config = self.write(root, '.codex/config.toml', content)
                with self.assertRaisesRegex(ValueError, 'Conflicting agent configuration tables'):
                    migration.build_plan(root, system=False)
                self.assertEqual(config.read_text(), content)
                self.assertTrue(old.exists())
                self.assertFalse((root / '.local/state/basaltwater-migration').exists())

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.subprocess.run')
    def test_active_nested_operation_lock_refuses_data_changes(self, run, _accounts):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = self.write(root, 'run/lock/infra_tools/operations/host.lock', '')
            saved = self.write(root, 'var/lib/infra_tools/setup.json', '{}')
            self.write(root, 'etc/systemd/system/infra-tools-test.service', '[Service]\nExecStart=/bin/true\n')
            plan = migration.build_plan(root, system=True)
            with lock.open('r+') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'Active operation'):
                    migration.apply_plan(plan)
            self.assertTrue(saved.exists())
            self.assertFalse((root / 'var/lib/basaltwater').exists())
            self.assertFalse(Path(plan['recovery']).exists())
            self.assertFalse(any(call.args[0][1] in ('stop', 'disable', 'start') for call in run.call_args_list))

    @patch('lib.rename_migration.subprocess.run')
    def test_user_dropins_reload_and_restart_owning_unit_once(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('infra-tools', 'infra-tools-extra'):
                self.write(root, f'.config/systemd/user/t3code.service.d/{name}.conf',
                           '[Service]\nEnvironment=PATH=/home/agent/.local/share/infra_tools/t3-npm/bin\n')
            plan = migration.build_plan(root, system=False)
            self.assertEqual(plan['units'], [{'old': 't3code.service', 'new': 't3code.service'}])
            migration.apply_plan(plan)
            commands = [call.args[0] for call in run.call_args_list]
            stop = ['systemctl', '--user', 'stop', 't3code.service']
            reload = ['systemctl', '--user', 'daemon-reload']
            start = ['systemctl', '--user', 'start', 't3code.service']
            self.assertLess(commands.index(stop), commands.index(reload))
            self.assertLess(commands.index(reload), commands.index(start))
            self.assertFalse(any('disable' in command or 'enable' in command for command in commands))
            self.assertIn('/basaltwater/', (root / '.config/systemd/user/t3code.service.d/basaltwater.conf').read_text())

    @patch('lib.rename_migration.subprocess.run')
    def test_renamed_unit_keeps_all_overrides_and_recovers_them(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        for system in (False, True):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                resources = 'etc/systemd/system' if system else '.config/systemd/user'
                old = root / resources / 'infra-tools-test.service.d'
                new = old.with_name('basaltwater-test.service.d')
                self.write(root, f'{resources}/infra-tools-test.service', '[Service]\nExecStart=/bin/true\n')
                override = self.write(old, 'override.conf', '[Service]\nMemoryMax=512M\n')
                self.write(old, 'infra-tools.conf', '[Service]\nWorkingDirectory=/opt/infra_tools\n')
                before = override.stat().st_ino, override.stat().st_mode
                plan = migration.build_plan(root, system=system)
                self.assertEqual(plan['units'], [{'old': 'infra-tools-test.service', 'new': 'basaltwater-test.service'}])
                with patch.object(migration.pwd, 'getpwnam', side_effect=KeyError), patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        migration.apply_plan(plan)
                self.assertFalse(old.exists())
                self.assertEqual((new / 'override.conf').read_text(), '[Service]\nMemoryMax=512M\n')
                self.assertEqual(((new / 'override.conf').stat().st_ino, (new / 'override.conf').stat().st_mode), before)
                self.assertIn('/opt/basaltwater', (new / 'basaltwater.conf').read_text())
                migration.recover(root, system=system)
                self.assertTrue(override.is_file())
                self.assertIn('/opt/infra_tools', (old / 'infra-tools.conf').read_text())
                self.assertFalse(new.exists())

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    def test_resume_configuration_moves_and_can_be_removed_by_swap_setup(self, _accounts):
        from common import swap_steps

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = self.write(root, 'etc/initramfs-tools/conf.d/99-infra-tools-resume', 'RESUME=UUID=12345678\n')
            new = old.with_name('99-basaltwater-resume')
            migration.apply_plan(migration.build_plan(root, system=True))
            self.assertFalse(old.exists())
            self.assertEqual(new.read_text(), 'RESUME=UUID=12345678\n')
            with patch.object(swap_steps, 'RESUME_PATH', str(new)), patch.object(swap_steps, 'run') as run:
                swap_steps._configure_resume(None, [])
            self.assertFalse(new.exists())
            run.assert_called_once_with('update-initramfs -u')

    @patch('lib.rename_migration.pwd.getpwnam', side_effect=KeyError)
    @patch('lib.rename_migration.shutil.which', return_value='/usr/bin/tool')
    @patch('lib.rename_migration.subprocess.run')
    def test_syncthing_can_traverse_merged_state_before_restart_and_acl_recovers(self, run, _which, _accounts):
        acl = 'user::rwx\ngroup::---\nother::---\n'
        run.return_value = subprocess.CompletedProcess([], 0, acl, '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = self.write(root, 'var/lib/infra_tools/setup.json', '{}').parent
            private.chmod(0o700)
            home = self.write(root, 'var/lib/infra-tools/syncthing/config.xml', '<configuration/>').parent
            self.write(root, 'etc/systemd/system/infra-syncthing.service', '[Service]\nUser=agent\nExecStart=/usr/bin/syncthing serve --home=/var/lib/infra-tools/syncthing\n')
            original_stat = Path.stat

            def stat_with_service_owner(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == home:
                    fields = list(result)
                    fields[4] = os.geteuid() + 1
                    return os.stat_result(fields)
                return result

            with patch.object(Path, 'stat', stat_with_service_owner):
                plan = migration.build_plan(root, system=True)
            migration.apply_plan(plan)
            commands = [call.args[0] for call in run.call_args_list]
            parent = root / 'var/lib/basaltwater'
            grant = ['setfacl', '--set-file=-', '--', str(parent)]
            self.assertLess(commands.index(grant), commands.index(['systemctl', 'start', 'basaltwater-syncthing.service']))
            granted = next(call for call in run.call_args_list if call.args[0] == grant)
            self.assertIn(f'user:{os.geteuid() + 1}:--x', granted.kwargs['input'])
            self.assertEqual(parent.stat().st_mode & 0o777, 0o700)  # Mocked ACL calls never widen access.
            # Simulate failure after all actions but before committing completion.
            plan['status'] = 'planned'
            migration._save(plan)
            migration.recover(root, system=True)
            restore = next(call for call in reversed(run.call_args_list) if call.args[0][:2] == ['setfacl', '--set-file=-'])
            self.assertEqual(restore.kwargs['input'], acl)
            self.assertTrue((home / 'config.xml').exists())
            self.assertTrue((private / 'setup.json').exists())

    def test_traversal_acl_does_not_unmask_existing_users_or_groups(self):
        before = ('user::rwx\nuser:1001:rwx\nuser:1002:r-x\ngroup::rwx\n'
                  'group:1003:rwx\nmask::r--\nother::---\ndefault:user::rwx\n')
        after = migration._traversal_acl(before, 1001)
        self.assertIn('user:1001:r-x\n', after)
        self.assertIn('user:1002:r--\n', after)
        self.assertIn('group::r--\n', after)
        self.assertIn('group:1003:r--\n', after)
        self.assertIn('mask::r-x\n', after)
        self.assertIn('default:user::rwx\n', after)

    @patch('lib.rename_migration.shutil.which', return_value=None)
    @patch('lib.remote_utils.install_package', autospec=True, return_value=False)
    def test_missing_acl_tools_fail_before_creating_a_journal(self, install, _which):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = self.write(root, '.config/infra_tools/setup.json', '{}').parent
            plan = migration.build_plan(root, system=False)
            plan['actions'].append({'kind': 'traverse', 'old': str(parent), 'uid': os.geteuid() + 1})
            with self.assertRaisesRegex(ValueError, 'ACL tools'):
                migration.apply_plan(plan)
            install.assert_called_once()
            self.assertTrue(parent.exists())
            self.assertFalse(Path(plan['recovery']).exists())

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

    @patch('lib.rename_migration.subprocess.run')
    def test_deployment_account_home_is_updated_and_recovered(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'var/lib/infra_tools/build-users/app'
            new = root / 'var/lib/basaltwater/build-users/app'
            self.write(old, '.ssh/id_ed25519', 'private key')
            account = SimpleNamespace(pw_name='build-app', pw_dir=str(old))
            plan = migration.build_plan(root, system=True)
            with patch.object(migration.pwd, 'getpwall', return_value=[account]), patch.object(migration.pwd, 'getpwnam', side_effect=KeyError), patch.object(migration, '_reload_integrations', side_effect=OSError('interrupted')):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    migration.apply_plan(plan)
            self.assertIn(['usermod', '--home', str(new), 'build-app'], [call.args[0] for call in run.call_args_list])
            account.pw_dir = str(new)
            with patch.object(migration.pwd, 'getpwnam', return_value=account):
                migration.recover(root, system=True)
            self.assertIn(['usermod', '--home', str(old), 'build-app'], [call.args[0] for call in run.call_args_list])
            self.assertEqual((old / '.ssh/id_ed25519').read_text(), 'private key')


if __name__ == '__main__':
    unittest.main()
