"""Automatic target migration uses temporary files and mocked host operations."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import rename_migration, setup_common, setup_upgrade


class AutomaticSetupMigrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.runtime = self.root / 'opt/basaltwater'
        self.source = self.root / 'incoming'
        self.source.mkdir()
        (self.source / 'basaltwater.py').write_text('# new runtime\n')
        self.stack.enter_context(patch.object(setup_common, 'REMOTE_INSTALL_DIR', str(self.runtime)))
        self.stack.enter_context(patch.object(setup_common, 'PERSISTENT_STATE_DIR', str(self.root / 'var/lib/basaltwater')))
        self.stack.enter_context(patch.object(rename_migration.pwd, 'getpwnam', side_effect=KeyError))
        self.stack.enter_context(patch.object(rename_migration.pwd, 'getpwall', return_value=[]))
        self.stack.enter_context(patch.object(rename_migration.grp, 'getgrnam', side_effect=KeyError))
        self.stack.enter_context(patch.object(rename_migration.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')))
        self.users = self.stack.enter_context(patch.object(setup_upgrade, '_migrate_users'))
        def copy_runtime(destination):
            (Path(destination) / 'basaltwater.py').write_text('# new runtime\n')
        self.stack.enter_context(patch.object(setup_common, 'copy_project_files', side_effect=copy_runtime))

    def legacy(self):
        runtime = self.root / 'opt/infra_tools'
        (runtime / 'lib').mkdir(parents=True)
        (runtime / 'infra_tools.py').write_text('# old runtime\n')
        (runtime / 'lib/installation_info.py').write_text('# recent provenance\n')
        state = self.root / 'var/lib/infra_tools'
        state.mkdir(parents=True)
        state.chmod(0o700)
        secret = state / 'credentials.json'
        secret.write_text('{"password":"unchanged infra_tools secret"}')
        secret.chmod(0o600)
        (runtime / 'state').symlink_to(state)
        return runtime, secret

    def test_setup_automatically_migrates_recent_runtime_and_private_data(self):
        old, secret = self.legacy()
        original = secret.read_bytes(), secret.stat().st_ino, secret.stat().st_mode
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        new = self.root / 'var/lib/basaltwater/credentials.json'
        self.assertEqual((new.read_bytes(), new.stat().st_ino, new.stat().st_mode), original)
        self.assertFalse(old.exists())
        self.assertTrue((self.runtime / 'basaltwater.py').exists())
        self.assertEqual((self.runtime / 'state').resolve(), new.parent)
        self.users.assert_called_once_with(self.runtime, 'agent')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.assertEqual(new.read_bytes(), original[0])
        self.assertEqual(self.users.call_count, 2)

    def test_conflict_stops_before_runtime_replacement(self):
        old, secret = self.legacy()
        destination = self.root / 'var/lib/basaltwater'
        destination.mkdir()
        (destination / secret.name).write_text('conflict')
        with patch.object(setup_common, '_activate_local_runtime') as activate:
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        activate.assert_not_called()
        self.users.assert_not_called()
        self.assertTrue((old / 'infra_tools.py').exists())
        self.assertTrue(secret.exists())

    def test_restrictive_root_umask_does_not_make_migrated_runtime_private(self):
        self.legacy()
        previous = os.umask(0o077)
        try:
            setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        finally:
            os.umask(previous)
        self.assertEqual(self.runtime.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / 'var/lib/basaltwater-migration').stat().st_mode & 0o777, 0o700)

    def test_deployment_sources_survive_migration_and_setup_retry(self):
        old, _secret = self.legacy()
        (old / 'deployments/project').mkdir(parents=True)
        (old / 'deployments/project/app.py').write_text('# deployed source\n')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.assertEqual((self.runtime / 'deployments/project/app.py').read_text(), '# deployed source\n')

    def test_interrupted_journal_blocks_setup_even_after_old_source_was_moved(self):
        directory = self.root / 'var/lib/basaltwater-migration'
        directory.mkdir(parents=True, mode=0o700)
        journal = directory / 'journal.json'
        journal.write_text(json.dumps({'status': 'planned'}))
        journal.chmod(0o600)
        with patch.object(setup_common, '_activate_local_runtime') as activate:
            with self.assertRaisesRegex(ValueError, 'requires recovery'):
                setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        activate.assert_not_called()

    def test_fresh_setup_activates_without_migration(self):
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.assertTrue((self.runtime / 'basaltwater.py').exists())
        self.users.assert_not_called()

    def test_completed_cutover_repairs_encoded_firewall_and_codex_markers(self):
        self.legacy()
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        comment = 'infra-tools T3 Code 3773/tcp source 192.168.0.0/24'
        packet_rule = '-A ufw-user-input -p tcp --dport 3773 -j ACCEPT\n'
        for filename in ('user.rules', 'user6.rules'):
            path = self.root / 'etc/ufw' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('### tuple ### allow tcp 3773 0.0.0.0/0 any 192.168.0.0/24 in comment=' + comment.encode().hex() + '\n' + packet_rule)
            path.chmod(0o640)
        policy = self.root / 'etc/codex/config.toml'
        policy.parent.mkdir(parents=True)
        policy.write_text('# Managed by infra-tools coding-agent security policy.\napproval_policy = "on-request"\n')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        for filename in ('user.rules', 'user6.rules'):
            path = self.root / 'etc/ufw' / filename
            self.assertIn(comment.replace('infra-tools', 'basaltwater').encode().hex(), path.read_text())
            self.assertTrue(path.read_text().endswith(packet_rule))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertIn('# Managed by basaltwater coding-agent security policy.', policy.read_text())

    def test_initial_migration_preserves_unowned_firewall_rules_and_codex_policy(self):
        self.legacy()
        rules = self.root / 'etc/ufw/user.rules'
        rules.parent.mkdir(parents=True)
        owned = 'infra-tools T3 Code pairing 3774/tcp source 192.168.0.0/24'
        unowned = 'operator-owned rule'
        prefix = '### tuple ### allow tcp 3774 0.0.0.0/0 any 192.168.0.0/24 in comment='
        rules.write_text(prefix + owned.encode().hex() + '\n' + prefix + unowned.encode().hex() + '\n')
        policy = self.root / 'etc/codex/config.toml'
        policy.parent.mkdir(parents=True)
        policy.write_text('# Operator configuration\napproval_policy = "on-request"\n')
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.assertIn(owned.replace('infra-tools', 'basaltwater').encode().hex(), rules.read_text())
        self.assertIn(prefix + unowned.encode().hex(), rules.read_text())
        self.assertEqual(policy.read_text(), '# Operator configuration\napproval_policy = "on-request"\n')

    def test_user_migration_failure_prevents_success_and_is_retried_on_next_setup(self):
        self.legacy()
        self.users.side_effect = subprocess.CalledProcessError(1, ['runuser'])
        with self.assertRaises(subprocess.CalledProcessError):
            setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.users.side_effect = None
        setup_upgrade.prepare_target_runtime(str(self.source), 'agent')
        self.assertEqual(self.users.call_count, 2)


class UserMigrationContextTests(unittest.TestCase):
    def test_user_pass_uses_owner_environment_and_cannot_consume_payload_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            account = SimpleNamespace(pw_uid=1001, pw_name='agent', pw_dir=directory)
            with patch.object(setup_upgrade.pwd, 'getpwall', return_value=[account]), \
                 patch.object(setup_upgrade.subprocess, 'run') as run:
                setup_upgrade._migrate_users(Path('/opt/basaltwater'), 'agent')
            command = run.call_args.args[0]
            self.assertEqual(command[:5], ['runuser', '--user', 'agent', '--', 'env'])
            self.assertIn('-i', command)
            self.assertIn(f'HOME={directory}', command)
            self.assertIn('XDG_RUNTIME_DIR=/run/user/1001', command)
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertTrue(run.call_args.kwargs['check'])


if __name__ == '__main__':
    unittest.main()
