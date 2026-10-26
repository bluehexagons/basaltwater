"""Tests for CI/CD webhook system."""

from __future__ import annotations

import unittest
import errno
import json
from pathlib import Path
import pwd
import stat
import struct
import subprocess
import tempfile
import time
from unittest.mock import call, patch, MagicMock
import sys
import os

# Add the parent directory to the path so we can import the modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from web.service_tools import cicd_executor
from web.service_tools import webhook_manager
from web.service_tools import webhook_receiver

from web.cicd_steps import (
    install_cicd_dependencies,
    create_cicd_user,
    create_cicd_directories,
    generate_webhook_secret,
    create_default_webhook_config,
    create_webhook_receiver_service,
    create_cicd_executor_service,
    configure_nginx_for_webhook,
)


class TestCICDSteps(unittest.TestCase):
    """Test CI/CD setup steps."""
    
    @patch('web.cicd_steps.is_package_installed')
    @patch('web.cicd_steps.run')
    def test_install_cicd_dependencies_already_installed(self, mock_run, mock_is_installed):
        """Test that we skip installation if dependencies are already installed."""
        mock_is_installed.return_value = True
        mock_config = MagicMock()
        
        install_cicd_dependencies(mock_config)
        
        # Should not call apt-get install
        mock_run.assert_not_called()
    
    @patch('web.cicd_steps.is_package_installed')
    @patch('web.cicd_steps.run')
    def test_install_cicd_dependencies_missing(self, mock_run, mock_is_installed):
        """Test that we install missing dependencies."""
        mock_is_installed.side_effect = [False, True, True]
        mock_config = MagicMock()
        
        install_cicd_dependencies(mock_config)
        
        mock_run.assert_called_once_with(["apt-get", "install", "-y", "-qq", "git", "acl"])
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[:2], ['apt-get', 'install'])
        self.assertIn('git', call_args)
    
    @patch('web.cicd_steps.run')
    def test_create_cicd_user_already_exists(self, mock_run):
        """Test that we skip user creation if user exists."""
        # Simulate user exists (id command returns 0)
        mock_run.return_value = MagicMock(returncode=0)
        mock_config = MagicMock()
        
        create_cicd_user(mock_config)
        
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(mock_run.call_args_list[0][0][0], ['id', 'cicd-build'])
        self.assertEqual(mock_run.call_args_list[2][0][0], ['id', 'webhook'])
        self.assertEqual(
            mock_run.call_args_list[3][0][0],
            ['usermod', '--home', '/var/lib/basaltwater/cicd', 'webhook'],
        )
    
    @patch('web.cicd_steps.run')
    def test_create_cicd_user_new(self, mock_run):
        """Test that we create user if it doesn't exist."""
        # First call (id): user doesn't exist, second call (useradd): create user
        mock_run.side_effect = [
            MagicMock(returncode=1),  # id fails
            MagicMock(returncode=0),  # useradd succeeds
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        mock_config = MagicMock()
        
        create_cicd_user(mock_config)
        
        # Should call both id and useradd
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(mock_run.call_args_list[1][0][0][0], 'useradd')
        self.assertIn('--home-dir', mock_run.call_args_list[1][0][0])
        self.assertIn('/var/lib/basaltwater/cicd/build', mock_run.call_args_list[1][0][0])
        self.assertIn('cicd-build', mock_run.call_args_list[1][0][0])
    
    @patch('web.cicd_steps.reconcile_cicd_state_access')
    @patch('web.cicd_steps.create_isolated_build_directories')
    @patch('web.cicd_steps.secure_cicd_directories')
    @patch('web.cicd_steps.directory_fd')
    @patch('web.cicd_steps.check_directory_path')
    def test_create_cicd_directories(self, check_path, directory, secure, isolated, access):
        """Test that we create required directories."""
        order = MagicMock()
        for name, step in (('secure', secure), ('isolated', isolated), ('access', access)):
            order.attach_mock(step, name)
        create_cicd_directories(MagicMock())
        self.assertEqual(check_path.call_count, 3)
        directory.assert_called_once_with('/etc/basaltwater/cicd', create=True)
        secure.assert_called_once_with([
            '/var/lib/basaltwater/cicd', '/var/lib/basaltwater/cicd/jobs',
            '/var/lib/basaltwater/cicd/logs',
        ])
        isolated.assert_called_once()
        access.assert_called_once()
        self.assertEqual([c[0] for c in order.mock_calls], ['secure', 'isolated', 'access'])
    
    @patch('web.cicd_steps.os.path.exists')
    @patch('web.cicd_steps.secrets.token_urlsafe')
    @patch('web.cicd_steps.write_text_atomic')
    @patch('web.cicd_steps.run')
    def test_generate_webhook_secret_new(self, mock_run, mock_write_text, mock_token, mock_exists):
        """Test that we generate a new webhook secret."""
        mock_exists.return_value = False
        mock_token.return_value = "test-secret-token"
        mock_config = MagicMock()
        
        secret = generate_webhook_secret(mock_config)
        
        self.assertEqual(secret, "test-secret-token")
        mock_token.assert_called_once_with(32)
        self.assertEqual(mock_write_text.call_count, 2)
    
    @patch('web.cicd_steps.os.path.exists')
    @patch('web.cicd_steps.save_config_file')
    def test_create_default_webhook_config(self, mock_write_json, mock_exists):
        """Test that we create default webhook configuration."""
        mock_exists.return_value = False
        mock_config = MagicMock()
        
        create_default_webhook_config(mock_config)
        
        mock_write_json.assert_called_once()
        
        # Check that config has repositories key
        config_data = mock_write_json.call_args[0][1]
        self.assertIn('repositories', config_data)
        self.assertIsInstance(config_data['repositories'], list)
        self.assertEqual(config_data['repositories'][0]['branches'], ['main'])

    @patch('web.service_tools.webhook_manager.save_config')
    @patch(
        'web.service_tools.webhook_manager.load_config',
        return_value={'repositories': []},
    )
    def test_webhook_manager_defaults_new_repositories_to_main(
        self,
        _mock_load,
        mock_save,
    ):
        args = MagicMock(
            url='https://github.com/example/repo.git',
            branches=None,
            install=None,
            build=None,
            test=None,
            deploy=None,
        )

        self.assertEqual(webhook_manager.add_repository(args), 0)

        saved_config = mock_save.call_args.args[0]
        self.assertEqual(saved_config['repositories'][0]['branches'], ['main'])
    
    @patch('web.cicd_steps._check_cicd_state_access')
    @patch('web.cicd_steps.replace_units')
    @patch('web.cicd_steps.run')
    @patch('web.cicd_steps.generate_webhook_secret')
    def test_create_webhook_receiver_service(self, _secret, _run, replace, access):
        create_webhook_receiver_service(MagicMock())
        replace.assert_called_once()
        self.assertEqual(replace.call_args.kwargs['activate'], ('webhook-receiver.service',))
        unit = replace.call_args.args[0]['webhook-receiver.service']
        self.assertIn('Environment=HOME=/var/lib/basaltwater/cicd', unit)
        self.assertIn('ReadWritePaths=/var/lib/basaltwater/cicd\n', unit)
        access.assert_called_once()

    @patch('web.cicd_steps._check_cicd_state_access')
    @patch('web.cicd_steps.replace_units')
    @patch('web.cicd_steps.run')
    def test_create_cicd_executor_service(self, _run, replace, access):
        create_cicd_executor_service(MagicMock())
        replace.assert_called_once()
        self.assertEqual(replace.call_args.kwargs['activate'], ('cicd-executor.path',))
        units = replace.call_args.args[0]
        service_lines = units['cicd-executor.service'].splitlines()
        # The system manager supplies UID 0 without the explicit-User seccomp
        # setup that drops CAP_SETUID before exec under NNP in systemd 257.
        self.assertFalse(any(line.startswith(('User=', 'DynamicUser=')) for line in service_lines))
        for directive in (
            'Type=oneshot', 'Group=root', 'TimeoutStartSec=infinity',
            'ExecStart=/usr/bin/python3 -I /opt/basaltwater/web/service_tools/cicd_executor.py',
            'NoNewPrivileges=true', 'PrivateTmp=true', 'PrivateDevices=true',
            'ProtectSystem=strict', 'ProtectHome=true', 'ProtectKernelTunables=true',
            'ProtectKernelModules=true', 'ProtectKernelLogs=true', 'ProtectControlGroups=true',
            'ProtectClock=true', 'ProtectHostname=true', 'ProtectProc=invisible',
            'RestrictNamespaces=true', 'RestrictRealtime=true', 'RestrictSUIDSGID=true',
            'LockPersonality=true', 'UMask=0027',
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, service_lines)
        for name, value in (
            ('CapabilityBoundingSet', 'CAP_SETUID CAP_SETGID CAP_SETPCAP CAP_DAC_OVERRIDE CAP_KILL'),
            ('AmbientCapabilities', ''),
        ):
            self.assertEqual(
                [line for line in service_lines if line.startswith(f'{name}=')],
                [f'{name}={value}'],
            )
        self.assertIn('PathChanged=/var/lib/basaltwater/cicd/jobs', units['cicd-executor.path'])
        self.assertIn('Unit=cicd-executor.service', units['cicd-executor.path'])
        access.assert_called_once()

    @patch('web.cicd_steps.os.path.exists')
    @patch('web.cicd_steps.os.makedirs')
    @patch('web.cicd_steps.run')
    def test_configure_nginx_for_webhook(self, mock_run, mock_makedirs, mock_exists):
        """Test nginx configuration for webhook endpoint."""
        mock_exists.return_value = False
        mock_config = MagicMock()
        
        # Mock nginx -t to succeed
        mock_run.side_effect = [
            MagicMock(returncode=0),  # nginx -t
            MagicMock(returncode=0),  # systemctl reload nginx
        ]
        
        with patch('web.cicd_steps.write_text_atomic') as write:
            configure_nginx_for_webhook(mock_config)
        
        # Should write nginx config
        write.assert_called_once()
        written_config = write.call_args.args[1]
        self.assertIn('client_max_body_size 1m;', written_config)
        
        # Should test nginx config
        test_calls = [call for call in mock_run.call_args_list if call.args[0] == 'nginx -t']
        self.assertGreater(len(test_calls), 0)
        
        # Should reload nginx
        reload_calls = [call for call in mock_run.call_args_list if call.args[0] == ['systemctl', 'reload', 'nginx']]
        self.assertGreater(len(reload_calls), 0)


class TestCicdStateAccess(unittest.TestCase):
    def setUp(self):
        from web import cicd_steps as steps

        self.steps = steps
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / 'state'
        self.home = self.state / 'cicd'
        self.build = self.home / 'build'
        self.directories = (
            self.home, self.home / 'jobs', self.home / 'logs',
            self.build, self.build / 'workspaces',
        )
        for directory in self.directories:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        self.state.chmod(0o700)
        self.enterContext(patch.object(steps, 'CICD_HOME', str(self.home)))
        self.enterContext(patch.object(steps, 'BUILD_HOME', str(self.build)))
        self.accounts = {
            'webhook': MagicMock(pw_uid=12345, pw_gid=12346),
            'cicd-build': MagicMock(pw_uid=23456, pw_gid=23457),
        }
        self.enterContext(patch.object(steps.pwd, 'getpwnam', side_effect=self.accounts.__getitem__))
        self.native_accounts = self.enterContext(patch.object(steps.pwd, 'getpwall', return_value=[]))
        self.run = self.enterContext(patch.object(steps, 'run', side_effect=self._command))

    @staticmethod
    def _acl_entries(path):
        try:
            current = os.getxattr(path, 'system.posix_acl_access')
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise
            mode = os.stat(path).st_mode
            return {'user:': (mode >> 6) & 7, 'group:': (mode >> 3) & 7, 'other:': mode & 7}
        entries = {}
        for index in range(4, len(current), 8):
            tag, permissions, identity = struct.unpack('<HHI', current[index:index + 8])
            kind = {1: 'user', 2: 'user', 4: 'group', 8: 'group', 16: 'mask', 32: 'other'}[tag]
            qualifier = str(identity) if tag in (2, 8) else ''
            entries[f'{kind}:{qualifier}'] = permissions
        return entries

    def _write_acl(self, path, entries, *, default=False):
        packed = []
        for key, permissions in entries.items():
            kind, qualifier = key.split(':')
            tag = {'user': 2 if qualifier else 1, 'group': 8 if qualifier else 4,
                   'mask': 16, 'other': 32}[kind]
            identity = int(qualifier) if qualifier else 0xffffffff
            packed.append((tag, permissions, identity))
        packed.sort(key=lambda entry: (entry[0], entry[2]))
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in packed)
        try:
            os.setxattr(path, 'system.posix_acl_default' if default else 'system.posix_acl_access', acl)
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest('temporary filesystem does not support POSIX ACLs')
            raise
        return acl

    def _command(self, command, **kwargs):
        # Model ACL utilities only on fixture inodes; never execute host commands.
        if command[0] == 'getfacl':
            self.assertEqual(command[:-1], [
                'getfacl', '--access', '--omit-header', '--numeric', '--no-effective', '--',
            ])
            entries = self._acl_entries(command[-1])
            output = '\n'.join(
                f'{key}:' + ''.join(letter if permissions & bit else '-'
                                   for letter, bit in (('r', 4), ('w', 2), ('x', 1)))
                for key, permissions in entries.items()
            )
            return subprocess.CompletedProcess(command, 0, output, '')
        if command[0] == 'setfacl':
            self.assertEqual(command[:3], ['setfacl', '--no-mask', '-m'])
            self.assertEqual(command[4], '--')
            entries = self._acl_entries(command[-1])
            for change in command[3].split(','):
                key, permissions = change.rsplit(':', 1)
                entries[key] = sum(bit for letter, bit in (('r', 4), ('w', 2), ('x', 1))
                                   if letter in permissions)
            self._write_acl(command[-1], entries)
        return subprocess.CompletedProcess(command, 0, '', '')

    def _manifest_account(self, name='build-example', uid=34567, **overrides):
        fields = dict(pw_name=name, pw_passwd='x', pw_uid=uid, pw_gid=uid,
                      pw_gecos='', pw_dir=str(self.state / 'build-users' / name),
                      pw_shell='/usr/sbin/nologin')
        fields.update(overrides)
        return pwd.struct_passwd(tuple(fields.values()))

    def test_rerun_restores_only_traversal_without_recalculating_other_acl_rights(self):
        key = self.home / 'deploy_key'
        key.write_bytes(b'private credential')
        key.chmod(0o600)
        runtime_secret = self.state / 'runtime_secret'
        runtime_secret.write_bytes(b'private runtime state')
        runtime_secret.chmod(0o600)

        def snapshot():
            return {path: (path.stat().st_ino, path.stat().st_mode,
                           path.stat().st_uid, path.stat().st_gid, path.stat().st_mtime_ns)
                    for path in (*self.directories, key, runtime_secret)}

        before = snapshot()
        owner = (self.state.stat().st_uid, self.state.stat().st_gid)
        entries = {'user:': 7, 'user:34567': 1, 'user:45678': 7,
                   'group:': 7, 'group:56789': 7, 'mask:': 0, 'other:': 0}
        self._write_acl(self.state, entries)
        acl = self._write_acl(self.state, entries, default=True)
        self.native_accounts.return_value = [
            self._manifest_account(), self._manifest_account('build-second', 34568),
        ]
        for _ in range(2):
            # Match _ensure_local_runtime_state(): chmod masks every named ACL.
            self.state.chmod(0o700)
            self.steps.reconcile_cicd_state_access()
            entries = self._acl_entries(self.state)
            for uid in (12345, 23456, 34567, 34568):
                self.assertEqual(entries[f'user:{uid}'] & entries['mask:'], 1)
            for acl_key in ('user:45678', 'group:', 'group:56789'):
                self.assertEqual(entries[acl_key] & entries['mask:'], 0)
            self.assertEqual(entries['mask:'], 1)
            self.assertEqual(entries['other:'], 0)
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o710)
            self.assertEqual((self.state.stat().st_uid, self.state.stat().st_gid), owner)
            self.assertEqual(os.getxattr(self.state, 'system.posix_acl_default'), acl)
            self.assertEqual(snapshot(), before)
            self.assertEqual(key.read_bytes(), b'private credential')
            self.assertEqual(runtime_secret.read_bytes(), b'private runtime state')

    def test_grant_preserves_existing_mask_read_write_bits(self):
        for mask in range(8):
            with self.subTest(mask=mask):
                before = {'user:': 7, 'user:12345': 6, 'user:34567': 5,
                          'user:45678': 7, 'user:45679': 4,
                          'group:': 3, 'group:56789': 5, 'group:56790': 2,
                          'mask:': mask, 'other:': 0}
                self._write_acl(self.state, before)
                self.native_accounts.return_value = [self._manifest_account()]
                self.steps.reconcile_cicd_state_access()
                after = self._acl_entries(self.state)
                for key in ('user:45678', 'user:45679', 'group:', 'group:56789', 'group:56790'):
                    self.assertEqual(after[key] & after['mask:'], before[key] & mask)
                for key in ('user:12345', 'user:34567'):
                    self.assertEqual(after[key] & after['mask:'], (before[key] & mask) | 1)
                self.assertEqual(after['mask:'], mask | 1)
                self.assertEqual(after['user:'], before['user:'])
                self.assertEqual(after['other:'], before['other:'])

    def test_account_convention_is_required_for_manifest_authorization(self):
        invalid = [
            self._manifest_account('other'),
            self._manifest_account('build-'),
            self._manifest_account('build-' + 'a' * 26),
            self._manifest_account('build-bad\n'),
            self._manifest_account(pw_dir='/home/build-example'),
            self._manifest_account(pw_dir=str(self.state / 'build-users' / '..' / 'build-example')),
            self._manifest_account(pw_shell='/bin/bash'),
            self._manifest_account(uid=0),
            self._manifest_account(pw_gid=0),
        ]
        for account in invalid:
            with self.subTest(account=account):
                self.native_accounts.return_value = [account]
                self.assertEqual(self.steps._cicd_state_principals(str(self.state)), {12345, 23456})

    def test_unrecognized_latent_named_user_does_not_gain_traversal(self):
        self.native_accounts.return_value = [self._manifest_account(pw_dir='/home/build-example')]
        self._write_acl(self.state, {'user:': 7, 'user:34567': 1, 'group:': 0,
                                     'mask:': 0, 'other:': 0})
        self.steps.reconcile_cicd_state_access()
        entries = self._acl_entries(self.state)
        self.assertEqual(entries['user:34567'] & entries['mask:'], 0)

    def test_native_mode_without_extended_acl_keeps_group_and_other_permissions(self):
        self.state.chmod(0o740)
        self.steps.reconcile_cicd_state_access()
        entries = self._acl_entries(self.state)
        self.assertEqual(entries['group:'] & entries['mask:'], 4)
        self.assertEqual(entries['other:'], 0)
        self.assertEqual(entries['user:12345'] & entries['mask:'], 1)
        self.assertEqual(entries['user:23456'] & entries['mask:'], 1)

    def test_unreadable_or_invalid_acl_stops_before_mutation(self):
        for output in ('', 'user::rwx\ngroup::---\nother::---\nuser:bad:--x',
                       'user::rwx\ngroup::---\nother::---\nmask::---\nmask::rwx'):
            with self.subTest(output=output):
                self.run.reset_mock()
                self.run.side_effect = None
                self.run.return_value = subprocess.CompletedProcess([], 0, output, '')
                with self.assertRaises(ValueError):
                    self.steps.reconcile_cicd_state_access()
                self.run.assert_called_once()

    def test_checks_use_each_runtime_uid_with_groups_and_capabilities_cleared(self):
        self.steps._check_cicd_state_access()
        expected = []
        for user, directories in (
            ('webhook', self.directories[:3]), ('cicd-build', self.directories[3:]),
        ):
            account = self.accounts[user]
            for directory in directories:
                for permission in ('-x', '-w', '-r'):
                    expected.append(call([
                        '/usr/bin/setpriv', f'--reuid={account.pw_uid}',
                        f'--regid={account.pw_gid}', '--clear-groups',
                        '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
                        '--no-new-privs', '--', '/usr/bin/test', permission, str(directory),
                    ], timeout=30))
        self.assertEqual(self.run.call_args_list, expected)

    def test_acl_failure_stops_before_access_probes(self):
        for utility in ('getfacl', 'setfacl'):
            with self.subTest(utility=utility):
                def command(command, **kwargs):
                    if command[0] == utility:
                        raise RuntimeError('ACL unavailable')
                    return self._command(command, **kwargs)

                self.run.reset_mock()
                self.run.side_effect = command
                with self.assertRaisesRegex(RuntimeError, 'ACL unavailable'):
                    self.steps.reconcile_cicd_state_access()
                self.assertEqual(self.run.call_count, 1 if utility == 'getfacl' else 2)

    def test_parent_symlink_is_rejected_without_granting_access(self):
        moved = self.root / 'moved'
        self.state.rename(moved)
        self.state.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.steps.reconcile_cicd_state_access()
        self.run.assert_not_called()

    def test_grant_stays_on_opened_parent_if_path_is_swapped(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        moved = self.root / 'moved'
        original = self.state.stat().st_ino

        def command(command, **kwargs):
            if command[0] == 'setfacl':
                self.state.rename(moved)
                self.state.symlink_to(outside, target_is_directory=True)
            self.assertEqual(Path(command[-1]).stat().st_ino, original)
            self.assertNotEqual(Path(command[-1]).stat().st_ino, outside.stat().st_ino)
            return self._command(command, **kwargs)

        self.run.side_effect = command
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.steps.reconcile_cicd_state_access()
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self._acl_entries(moved)['user:12345'], 1)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(outside.stat().st_mode & 0o777, 0o700)

    def test_both_plans_repair_access_before_toolchains_and_unit_activation(self):
        from plugins.web import extend_build_server_steps, extend_cicd_steps

        for build in (False, True):
            with self.subTest(build=build):
                config = MagicMock(enable_cicd=True, is_build_server=build,
                                   install_node=True, install_python=True,
                                   disable_build_host=False,
                                   build_host_enrollment=None)
                plan = []
                extend_cicd_steps(config, plan)
                extend_build_server_steps(config, plan)
                names = [step.__name__ for _, step in plan]
                directories = 'create_build_workspace_dirs' if build else 'create_cicd_directories'
                self.assertLess(names.index('install_cicd_dependencies'), names.index(directories))
                self.assertLess(names.index('create_cicd_user'), names.index(directories))
                for later in ('create_webhook_receiver_service', 'create_cicd_executor_service',
                              *(('install_build_node', 'install_build_python_tools') if build else ())):
                    self.assertLess(names.index(directories), names.index(later))

    def test_access_failure_prevents_both_unit_replacements(self):
        for service in (create_webhook_receiver_service, create_cicd_executor_service):
            for failure in (0, 9):  # Receiver home and isolated build home.
                with self.subTest(service=service.__name__, failure=failure):
                    self.run.side_effect = [None] * failure + [RuntimeError('access denied')]
                    with patch.object(self.steps, 'generate_webhook_secret'), \
                         patch.object(self.steps, 'replace_units') as replace:
                        with self.assertRaisesRegex(RuntimeError, 'access denied'):
                            service(MagicMock())
                    replace.assert_not_called()


class TestSetupDirectorySafety(unittest.TestCase):
    def test_symlink_components_are_rejected_before_creation_or_ownership(self):
        from web import cicd_steps as steps
        for isolated in (False, True):
            for leaf in (False, True):
                with self.subTest(isolated=isolated, leaf=leaf), tempfile.TemporaryDirectory() as root:
                    outside = os.path.join(root, 'outside')
                    os.mkdir(outside)
                    linked = os.path.join(root, 'linked')
                    os.symlink(outside, linked)
                    target = linked if leaf else os.path.join(linked, 'new')
                    with patch.object(steps, 'run', return_value=MagicMock(returncode=0)), \
                         patch.object(steps, 'BUILD_HOME', target), \
                         patch.object(steps.os, 'fchown') as chown:
                        with self.assertRaisesRegex(ValueError, 'symlink'):
                            if isolated:
                                steps.create_isolated_build_directories()
                            else:
                                steps.secure_cicd_directories([target])
                    chown.assert_not_called()
                    self.assertEqual(os.listdir(outside), [])

    def test_opened_directory_survives_path_swap_without_touching_target(self):
        from lib.setup_directories import directory_fd
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, 'target')
            outside = os.path.join(root, 'outside')
            moved = os.path.join(root, 'moved')
            os.mkdir(target)
            os.mkdir(outside, 0o755)
            with directory_fd(target) as descriptor, patch('lib.setup_directories.os.fchown') as chown:
                os.rename(target, moved)
                os.symlink(outside, target)
                os.fchown(descriptor, 123, 456)
                os.fchmod(descriptor, 0o700)
                chown.assert_called_once_with(descriptor, 123, 456)
                self.assertEqual(os.fstat(chown.call_args.args[0]).st_ino, os.stat(moved).st_ino)
            self.assertEqual(os.stat(moved).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(outside).st_mode & 0o777, 0o755)

    def test_symlink_inserted_between_mkdir_and_open_is_rejected(self):
        from lib.setup_directories import directory_fd
        with tempfile.TemporaryDirectory() as root:
            outside = os.path.join(root, 'outside')
            os.mkdir(outside)
            real_mkdir = os.mkdir

            def race(name, mode=0o777, *, dir_fd=None):
                if name == 'new':
                    os.symlink(outside, name, dir_fd=dir_fd)
                else:
                    real_mkdir(name, mode=mode, dir_fd=dir_fd)

            with patch('lib.setup_directories.os.mkdir', side_effect=race):
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    with directory_fd(os.path.join(root, 'new'), create=True):
                        self.fail('must reject raced symlink')
            self.assertEqual(os.listdir(outside), [])

    def test_cicd_modes_preserve_private_files(self):
        from web import cicd_steps as steps
        with tempfile.TemporaryDirectory() as root:
            home = os.path.join(root, 'cicd')
            build = os.path.join(home, 'build')
            os.makedirs(build)
            key = os.path.join(build, 'key')
            with open(key, 'w') as stream:
                stream.write('private')
            os.chmod(key, 0o600)
            with patch.object(steps, 'CICD_HOME', home), patch.object(steps, 'BUILD_HOME', build), \
                 patch.object(steps, 'run', return_value=MagicMock(returncode=0)), \
                 patch.object(steps.pwd, 'getpwnam', return_value=MagicMock(pw_uid=123)), \
                 patch.object(steps.grp, 'getgrnam', return_value=MagicMock(gr_gid=456)), \
                 patch.object(steps.os, 'fchown') as chown:
                steps.secure_cicd_directories([home, os.path.join(home, 'jobs')])
                steps.create_isolated_build_directories()
            self.assertEqual(chown.call_count, 4)
            self.assertEqual(os.stat(home).st_mode & 0o777, 0o711)
            self.assertEqual(os.stat(os.path.join(home, 'jobs')).st_mode & 0o777, 0o750)
            self.assertEqual(os.stat(build).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)

    def test_webhook_nginx_failures_raise_and_remove_candidate(self):
        from web import cicd_steps as steps
        for failure in ('test', 'reload', 'exception'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                target = os.path.join(root, 'webhook.conf')
                calls = []

                def activate(command, **kwargs):
                    calls.append(command)
                    if len(calls) == 1 and failure == 'test':
                        return MagicMock(returncode=1)
                    if len(calls) == 2:
                        if failure == 'exception':
                            raise OSError('reload interrupted')
                        if failure == 'reload':
                            return MagicMock(returncode=1)
                    return MagicMock(returncode=0)

                with patch.object(steps, 'WEBHOOK_NGINX_CONF', target), \
                     patch.object(steps, 'run', side_effect=activate):
                    with self.assertRaises((RuntimeError, OSError)):
                        steps.configure_nginx_for_webhook(MagicMock())
                self.assertFalse(os.path.exists(target))


class TestWebhookReceiverStructuredLogging(unittest.TestCase):
    @patch('web.service_tools.webhook_receiver.load_config_file', side_effect=FileNotFoundError)
    def test_load_config_rejects_missing_config(self, _mock_load):
        with self.assertRaises(FileNotFoundError):
            webhook_receiver.load_config()

    @patch.dict(os.environ, {"WEBHOOK_SECRET": "secret", "WEBHOOK_PORT": "9123"}, clear=True)
    @patch("web.service_tools.webhook_receiver.os.makedirs")
    @patch("web.service_tools.webhook_receiver.HTTPServer")
    def test_main_logs_start_listen_and_shutdown(self, mock_http_server, _mock_makedirs):
        httpd = MagicMock()
        httpd.serve_forever.side_effect = KeyboardInterrupt()
        mock_http_server.return_value = httpd

        with self.assertLogs(webhook_receiver.logger, level="INFO") as logs:
            result = webhook_receiver.main()

        self.assertEqual(result, 0)
        output = "\n".join(logs.output)
        self.assertIn("Starting webhook receiver", output)
        self.assertIn("Webhook receiver listening | bind='127.0.0.1' port=9123", output)
        self.assertIn("Server is ready to accept webhooks", output)
        self.assertIn("Shutting down webhook receiver", output)


class TestAppServerSteps(unittest.TestCase):
    """Test app server setup steps."""
    
    @patch('web.app_server_steps.is_package_installed')
    @patch('web.app_server_steps.run')
    def test_install_app_server_dependencies_already_installed(self, mock_run, mock_is_installed):
        """Test that we skip installation if dependencies are already installed."""
        mock_is_installed.return_value = True
        mock_config = MagicMock()
        
        from web.app_server_steps import install_app_server_dependencies
        install_app_server_dependencies(mock_config)
        
        mock_run.assert_not_called()
    
    @patch('web.app_server_steps.is_package_installed')
    @patch('web.app_server_steps.run')
    def test_install_app_server_dependencies_missing(self, mock_run, mock_is_installed):
        """Test that we install missing dependencies."""
        mock_is_installed.return_value = False
        mock_config = MagicMock()
        
        from web.app_server_steps import install_app_server_dependencies
        install_app_server_dependencies(mock_config)
        
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[:2], ['apt-get', 'install'])
    
    @patch('web.app_server_steps.run')
    def test_create_deploy_user_already_exists(self, mock_run):
        """Existing deploy users are reconciled with the managed SSH group."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]
        mock_config = MagicMock()
        
        from web.app_server_steps import create_deploy_user
        create_deploy_user(mock_config)
        
        self.assertEqual(
            [entry.args[0] for entry in mock_run.call_args_list],
            [
                ['id', 'deploy'],
                ['getent', 'group', 'remoteusers'],
                ['usermod', '-aG', 'remoteusers', 'deploy'],
            ],
        )
        mock_run.assert_called_with(['usermod', '-aG', 'remoteusers', 'deploy'])
    
    @patch('web.app_server_steps.run')
    def test_create_deploy_user_new(self, mock_run):
        """Test that we create user if it doesn't exist."""
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]
        mock_config = MagicMock()
        
        from web.app_server_steps import create_deploy_user
        create_deploy_user(mock_config)
        
        self.assertEqual(
            [entry.args[0] for entry in mock_run.call_args_list],
            [
                ['id', 'deploy'],
                ['useradd', '--system', '--create-home', '--shell', '/bin/bash', 'deploy'],
                ['getent', 'group', 'remoteusers'],
                ['usermod', '-aG', 'remoteusers', 'deploy'],
                ['mkdir', '-p', '/home/deploy/.ssh'],
                ['chmod', '700', '/home/deploy/.ssh'],
                ['chown', '-R', 'deploy:deploy', '/home/deploy/.ssh'],
            ],
        )

    @patch('web.app_server_steps.run')
    def test_create_deploy_user_without_managed_ssh_group(self, mock_run):
        """A host without remoteusers keeps the existing Gogs-compatible behavior."""
        mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=2)]

        from web.app_server_steps import create_deploy_user
        create_deploy_user(MagicMock())

        self.assertEqual(
            [entry.args[0] for entry in mock_run.call_args_list],
            [['id', 'deploy'], ['getent', 'group', 'remoteusers']],
        )

    @patch('web.app_server_steps.run')
    def test_create_deploy_user_rejects_unexpected_group_probe_status(self, mock_run):
        """Unexpected getent failures stop both existing and fresh user paths."""
        from web.app_server_steps import create_deploy_user

        for user_exists in (True, False):
            with self.subTest(user_exists=user_exists):
                mock_run.reset_mock()
                mock_run.side_effect = [
                    MagicMock(returncode=0 if user_exists else 1),
                    *([] if user_exists else [MagicMock(returncode=0)]),
                    MagicMock(returncode=1),
                ]

                with self.assertRaisesRegex(RuntimeError, 'getent exited 1'):
                    create_deploy_user(MagicMock())

                commands = [entry.args[0] for entry in mock_run.call_args_list]
                self.assertIn(['getent', 'group', 'remoteusers'], commands)
                self.assertFalse(any(command[0] in {'mkdir', 'chmod', 'chown'} for command in commands))
                self.assertNotIn(['usermod', '-aG', 'remoteusers', 'deploy'], commands)

    @patch('web.app_server_steps.run')
    def test_create_deploy_user_fails_when_group_membership_cannot_be_updated(self, mock_run):
        """A failed required group mutation stops setup and propagates."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            OSError('usermod failed'),
        ]

        from web.app_server_steps import create_deploy_user
        with self.assertRaisesRegex(OSError, 'usermod failed'):
            create_deploy_user(MagicMock())

    def test_deploy_sudoers_validation_and_rollback(self):
        from web import app_server_steps as steps

        for existing in (False, True):
            for failure in (None, 0, 1, 2):
                with self.subTest(existing=existing, failure=failure), tempfile.TemporaryDirectory() as root:
                    source = os.path.join(root, 'source')
                    helper = os.path.join(root, 'helper')
                    target = os.path.join(root, 'deploy-nginx')
                    with open(source, 'w') as stream:
                        stream.write('helper')
                    if existing:
                        with open(target, 'w') as stream:
                            stream.write('old rule')
                        os.chmod(target, 0o440)
                    calls = []

                    def validate(command, **kwargs):
                        index = len(calls)
                        calls.append(command)
                        if index == 0:
                            self.assertEqual(command[:3], ['visudo', '-c', '-f'])
                            with open(command[3]) as stream:
                                body = stream.read()
                            self.assertIn(helper + ' *', body)
                            self.assertNotIn('/usr/bin/rm', body)
                            self.assertEqual(os.stat(command[3]).st_mode & 0o777, 0o440)
                            if existing:
                                with open(target) as stream:
                                    self.assertEqual(stream.read(), 'old rule')
                            else:
                                self.assertFalse(os.path.exists(target))
                        return MagicMock(returncode=int(index == failure))

                    with patch.object(steps, 'DEPLOY_ADMIN_SOURCE', source), \
                         patch.object(steps, 'DEPLOY_ADMIN_HELPER', helper), \
                         patch.object(steps, 'DEPLOY_SUDOERS_FILE', target), \
                         patch.object(steps.os, 'chown'), patch.object(steps.os, 'fchown'), \
                         patch.object(steps, 'run', side_effect=validate):
                        if failure is None:
                            steps.configure_deploy_sudoers(MagicMock())
                            with open(target) as stream:
                                self.assertIn(helper + ' *', stream.read())
                        else:
                            with self.assertRaisesRegex(RuntimeError, 'validation failed'):
                                steps.configure_deploy_sudoers(MagicMock())
                            if existing:
                                with open(target) as stream:
                                    self.assertEqual(stream.read(), 'old rule')
                                self.assertEqual(os.stat(target).st_mode & 0o777, 0o440)
                            else:
                                self.assertFalse(os.path.exists(target))
                    self.assertFalse(any(name.startswith('.deploy-nginx-') for name in os.listdir(root)))


class TestAppDirectorySafety(unittest.TestCase):
    def setUp(self):
        from web import app_server_steps as steps

        self.steps = steps
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.www = self.root / 'www'
        self.support = (
            self.root / 'logs' / 'web',
            self.root / 'nginx' / 'sites-available',
            self.root / 'nginx' / 'sites-enabled',
        )
        self.enterContext(patch.object(steps, 'APP_DEPLOY_ROOT', str(self.www)))
        self.enterContext(patch.object(steps, 'APP_SUPPORT_DIRECTORIES', tuple(map(str, self.support))))
        self.enterContext(patch.object(steps.grp, 'getgrnam', return_value=MagicMock(gr_gid=456)))
        self.run = self.enterContext(patch.object(steps, 'run'))
        self.chown = self.enterContext(patch.object(steps.os, 'fchown', side_effect=self._chown_root))

    def _chown_root(self, descriptor, uid, gid):
        # Model privileged ownership changes without changing host identities.
        self.assertEqual((uid, gid), (0, 456))
        self.assertEqual(os.fstat(descriptor).st_ino, self.www.stat().st_ino)

    @staticmethod
    def _snapshot(paths):
        result = {}
        for path in paths:
            info = path.stat()
            result[path] = (
                info.st_ino, info.st_uid, info.st_gid, info.st_mode,
                info.st_mtime_ns, info.st_ctime_ns,
                path.read_bytes() if path.is_file() else None,
                {key: os.getxattr(path, key) for key in os.listxattr(path)},
            )
        return result

    def test_new_directory_roots_allow_deploy_without_changing_support_owners(self):
        self.steps.create_app_directories(MagicMock())

        self.assertEqual(stat.S_IMODE(self.www.stat().st_mode), 0o775)
        for directory in self.support:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
            self.assertEqual(directory.stat().st_uid, os.getuid())
        self.chown.assert_called_once()
        self.run.assert_not_called()

    def test_populated_rerun_preserves_service_data_backups_and_release_state(self):
        shared = self.www / '.basaltwater_shared' / 'app'
        component = shared / 'api'
        data = component / 'data'
        backups = component / 'backups'
        release = self.www / 'app'
        for directory, mode in (
            (data, 0o750), (backups, 0o700), (release, 0o755),
            *[(directory, 0o750) for directory in self.support],
        ):
            directory.mkdir(parents=True)
            directory.chmod(mode)
        files = {
            data / 'database.sqlite3': (0o600, b'service database'),
            backups / 'database.sqlite3': (0o600, b'private recovery database'),
            shared / 'manifest-operation.json': (0o600, b'{"phase":"recovery_required"}'),
            shared / 'manifest-operation.lock': (0o600, b'lock'),
            shared / 'manifest-units.previous.json': (0o600, b'{"units":{}}'),
            shared / 'manifest-operation.last-result.json': (0o600, b'{"phase":"complete"}'),
            release / 'server': (0o755, b'executable release'),
            release / '.deploy-version': (0o644, b'previous release'),
            self.support[0] / 'access.log': (0o640, b'private log'),
            self.support[1] / 'app.conf': (0o640, b'operator nginx config'),
        }
        for path, (mode, content) in files.items():
            path.write_bytes(content)
            path.chmod(mode)
        paths = [*self.www.rglob('*'), *self.support, *self.root.joinpath('logs').rglob('*'),
                 *self.root.joinpath('nginx').rglob('*')]
        before = self._snapshot(paths)

        for _ in range(2):
            self.steps.create_app_directories(MagicMock())
            self.assertEqual(self._snapshot(paths), before)
        self.assertEqual(stat.S_IMODE(self.www.stat().st_mode), 0o775)
        self.assertEqual(self.chown.call_count, 2)
        self.run.assert_not_called()

    def test_existing_descendant_acl_is_preserved(self):
        data = self.www / 'app' / 'data'
        data.mkdir(parents=True)
        # Linux POSIX ACL: a named reader plus a private group/other policy.
        acl = struct.pack('<I', 2) + b''.join(
            struct.pack('<HHI', tag, permission, identity)
            for tag, permission, identity in (
                (1, 7, 0xffffffff), (2, 4, 12345), (4, 0, 0xffffffff),
                (16, 5, 0xffffffff), (32, 0, 0xffffffff),
            )
        )
        try:
            os.setxattr(data, 'system.posix_acl_access', acl)
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest('temporary filesystem does not support POSIX ACLs')
            raise
        before = self._snapshot([data])

        self.steps.create_app_directories(MagicMock())
        self.steps.create_app_directories(MagicMock())

        self.assertEqual(self._snapshot([data]), before)

    def test_unsafe_directory_paths_refuse_all_mutations(self):
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'private'
        sentinel.write_bytes(b'private')
        sentinel.chmod(0o600)
        before = self._snapshot([outside, sentinel])
        unsafe = self.root / 'unsafe'
        for index in range(4):
            for leaf in (False, True):
                for linked in (False, True):
                    with self.subTest(index=index, leaf=leaf, linked=linked):
                        if linked:
                            unsafe.symlink_to(outside, target_is_directory=True)
                        else:
                            unsafe.write_bytes(b'not a directory')
                        paths = [str(self.www), *map(str, self.support)]
                        paths[index] = str(unsafe if leaf else unsafe / 'child')
                        try:
                            with patch.object(self.steps, 'APP_DEPLOY_ROOT', paths[0]), \
                                 patch.object(self.steps, 'APP_SUPPORT_DIRECTORIES', tuple(paths[1:])), \
                                 patch.object(self.steps.os, 'mkdir') as mkdir, \
                                 patch.object(self.steps.os, 'fchmod') as chmod:
                                with self.assertRaisesRegex(ValueError, 'symlink or non-directory'):
                                    self.steps.create_app_directories(MagicMock())
                            mkdir.assert_not_called()
                            chmod.assert_not_called()
                            self.chown.assert_not_called()
                            self.run.assert_not_called()
                            self.assertEqual(self._snapshot([outside, sentinel]), before)
                        finally:
                            unsafe.unlink()

    def test_symlink_inserted_after_preflight_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        check = self.steps.check_directory_path

        def replace_after_check(path):
            check(path)
            if path == str(self.support[-1]):
                self.www.symlink_to(outside, target_is_directory=True)

        with patch.object(self.steps, 'check_directory_path', side_effect=replace_after_check), \
             patch.object(self.steps.os, 'mkdir') as mkdir, \
             patch.object(self.steps.os, 'fchmod') as chmod:
            with self.assertRaisesRegex(ValueError, 'symlink'):
                self.steps.create_app_directories(MagicMock())
        mkdir.assert_not_called()
        chmod.assert_not_called()
        self.chown.assert_not_called()
        self.run.assert_not_called()
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o700)
        self.assertEqual(list(outside.iterdir()), [])


class TestBuildServerSteps(unittest.TestCase):
    """Test build server setup steps."""
    
    @patch('web.build_server_steps.run')
    def test_generate_deploy_ssh_key_existing(self, mock_run):
        """Existing keys have their required modes repaired on every run."""
        mock_config = MagicMock()

        from web.build_server_steps import generate_deploy_ssh_key
        with patch('web.build_server_steps.os.path.lexists', return_value=True), \
             patch('web.build_server_steps.os.path.islink', return_value=False), \
             patch('web.build_server_steps.os.path.isfile', return_value=True), \
             patch('web.build_server_steps.os.makedirs'):
            generate_deploy_ssh_key(mock_config)

        self.assertNotIn(
            "ssh-keygen",
            " ".join(str(call.args[0]) for call in mock_run.call_args_list),
        )
        self.assertIn(
            call(["chmod", "600", "/var/lib/basaltwater/cicd/.ssh/deploy_key"]),
            mock_run.call_args_list,
        )
        self.assertFalse(any("-R" in c.args[0] for c in mock_run.call_args_list))
    
    @patch('web.build_server_steps.os.makedirs')
    @patch('web.build_server_steps.run')
    def test_generate_deploy_ssh_key_new(self, mock_run, mock_makedirs):
        """Test that we generate a new SSH key."""
        mock_config = MagicMock()

        from web.build_server_steps import generate_deploy_ssh_key
        with patch('web.build_server_steps.os.path.lexists', return_value=False):
            generate_deploy_ssh_key(mock_config)
        
        ssh_keygen_calls = [call for call in mock_run.call_args_list if 'ssh-keygen' in str(call)]
        self.assertEqual(len(ssh_keygen_calls), 1)

    @patch('web.build_server_steps.reconcile_cicd_state_access')
    @patch('web.build_server_steps.secure_cicd_directories')
    @patch('web.build_server_steps.create_isolated_build_directories')
    @patch('web.build_server_steps.os.makedirs')
    def test_build_workspace_setup_preserves_file_modes(
        self,
        mock_makedirs,
        mock_isolated,
        mock_secure_directories,
        mock_access,
    ):
        from web.build_server_steps import create_build_workspace_dirs

        order = MagicMock()
        for name, step in (('secure', mock_secure_directories),
                           ('isolated', mock_isolated), ('access', mock_access)):
            order.attach_mock(step, name)
        create_build_workspace_dirs(MagicMock())

        mock_makedirs.assert_not_called()
        mock_isolated.assert_called_once()
        mock_secure_directories.assert_called_once()
        mock_access.assert_called_once()
        self.assertEqual([c[0] for c in order.mock_calls], ['secure', 'isolated', 'access'])

    def test_both_dependency_paths_install_acl_when_only_acl_is_missing(self):
        from web import build_server_steps, cicd_steps

        for steps, install in (
            (cicd_steps, cicd_steps.install_cicd_dependencies),
            (build_server_steps, build_server_steps.install_build_dependencies),
        ):
            with self.subTest(install=install.__name__):
                installed = set()

                def is_installed(package):
                    return package != 'acl' or package in installed

                def run(command):
                    self.assertEqual(command[:4], ['apt-get', 'install', '-y', '-qq'])
                    self.assertIn('acl', command[4:])
                    installed.update(command[4:])

                with patch.object(steps, 'is_package_installed', side_effect=is_installed), \
                     patch.object(steps, 'run', side_effect=run) as mocked:
                    install(MagicMock())
                    install(MagicMock())
                mocked.assert_called_once()

    @patch('web.build_server_steps.install_node_for_user')
    def test_install_build_node_targets_cicd_home(self, mock_install_node):
        mock_config = MagicMock()

        from web.build_server_steps import install_build_node
        install_build_node(mock_config)

        mock_install_node.assert_called_once_with('cicd-build', '/var/lib/basaltwater/cicd/build')

    @patch('web.build_server_steps.install_or_update_uv', return_value=True)
    @patch('web.build_server_steps.run')
    def test_install_build_python_tools_targets_cicd_home(self, mock_run, mock_install_uv):
        mock_config = MagicMock()

        from web.build_server_steps import install_build_python_tools
        install_build_python_tools(mock_config)

        self.assertTrue(
            any(
                call.args[0][:2] == ['apt-get', 'install']
                for call in mock_run.call_args_list
            )
        )
        mock_install_uv.assert_called_once_with(user_home='/var/lib/basaltwater/cicd/build', username='cicd-build')
    
    @patch('web.build_server_steps.os.path.exists')
    @patch('web.build_server_steps.os.makedirs')
    @patch('web.build_server_steps.write_json_atomic')
    def test_configure_deploy_targets(self, mock_write_json, mock_makedirs, mock_exists):
        """Test that we configure deploy targets."""
        mock_exists.return_value = False
        mock_config = MagicMock()
        mock_config.deploy_targets = ['app1.example.com', 'app2.example.com']
        
        from web.build_server_steps import configure_deploy_targets
        configure_deploy_targets(mock_config)
        
        mock_write_json.assert_called_once()
        config_data = mock_write_json.call_args[0][1]
        self.assertIn('app1.example.com', config_data)
        self.assertIn('app2.example.com', config_data)

    @patch('web.build_server_steps.is_host_key_enrolled', return_value=True)
    @patch('web.build_server_steps.get_known_hosts_path', return_value='/var/lib/basaltwater/cicd/known_hosts')
    @patch('web.build_server_steps.os.path.exists')
    @patch('web.build_server_steps.os.makedirs')
    @patch('web.build_server_steps.run')
    def test_configure_deploy_known_hosts_uses_workspace_file(
        self,
        mock_run,
        mock_makedirs,
        mock_exists,
        _mock_enrolled,
        _mock_known_hosts_path,
    ):
        mock_exists.side_effect = lambda path: path == '/var/lib/basaltwater/cicd/known_hosts'
        mock_config = MagicMock()
        mock_config.deploy_targets = ['app1.example.com', 'app2.example.com']

        from web.build_server_steps import configure_deploy_known_hosts
        configure_deploy_known_hosts(mock_config)

        mock_makedirs.assert_called_once_with('/var/lib/basaltwater/cicd', mode=0o700, exist_ok=True)
        self.assertEqual(
            mock_run.call_args_list,
            [
                call(['chown', 'webhook:webhook', '/var/lib/basaltwater/cicd/known_hosts']),
                call(['chmod', '644', '/var/lib/basaltwater/cicd/known_hosts']),
            ],
        )

    @patch('web.build_server_steps.is_host_key_enrolled', return_value=False)
    @patch('web.build_server_steps.get_known_hosts_path', return_value='/var/lib/basaltwater/cicd/known_hosts')
    @patch('web.build_server_steps.os.makedirs')
    def test_configure_deploy_known_hosts_requires_explicit_enrollment(
        self, mock_makedirs, _mock_known_hosts_path, _mock_enrolled
    ):
        mock_config = MagicMock()
        mock_config.deploy_targets = ['app1.example.com']

        from web.build_server_steps import configure_deploy_known_hosts
        with self.assertRaisesRegex(RuntimeError, 'not enrolled'):
            configure_deploy_known_hosts(mock_config)
        mock_makedirs.assert_called_once_with(
            '/var/lib/basaltwater/cicd', mode=0o700, exist_ok=True
        )


class TestRemoteDeploy(unittest.TestCase):
    """Test remote deployment utilities."""
    
    def test_load_deploy_targets(self):
        """Test loading deploy targets configuration."""
        from lib.remote_deploy import load_deploy_targets
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'targets.json')
            with open(path, 'w') as file_obj:
                json.dump({'app1.example.com': {'host': 'app1.example.com'}}, file_obj)
            with patch('lib.remote_deploy.DEPLOY_TARGETS_FILE', path):
                targets = load_deploy_targets()
        
        self.assertIn('app1.example.com', targets)
    
    @patch('lib.remote_deploy.load_deploy_targets')
    def test_get_deploy_target(self, mock_load):
        """Test getting a specific deploy target."""
        mock_load.return_value = {'app1.example.com': {'host': 'app1.example.com'}}
        
        from lib.remote_deploy import get_deploy_target
        target = get_deploy_target('app1.example.com')
        
        self.assertIsNotNone(target)
        if target:
            self.assertEqual(target['host'], 'app1.example.com')
    
    @patch('lib.remote_deploy.load_deploy_targets')
    def test_get_deploy_target_not_found(self, mock_load):
        """Test getting a non-existent deploy target."""
        mock_load.return_value = {}
        
        from lib.remote_deploy import get_deploy_target
        target = get_deploy_target('unknown.example.com')
        
        self.assertIsNone(target)

    def test_build_ssh_stdin_script_cmd_uses_bash_stdin(self):
        """Deploy scripts should stream over stdin instead of being embedded in the command."""
        from lib.remote_deploy import _build_ssh_stdin_script_cmd

        target = {
            'host': 'app1.example.com',
            'user': 'deploy',
            'ssh_key': '/tmp/key',
            'ssh_port': 2222,
        }
        command = _build_ssh_stdin_script_cmd(target, '/var/www/app one')

        self.assertEqual(command[:6], ['ssh', '-i', '/tmp/key', '-p', '2222', '-o'])
        self.assertEqual(command[-2], 'deploy@app1.example.com')
        self.assertEqual(command[-1], "cd '/var/www/app one' && bash -s --")


class TestRemoteDeployScriptExecution(unittest.TestCase):
    @patch('web.service_tools.cicd_executor.logger')
    @patch('web.service_tools.cicd_executor.run_command')
    @patch('lib.remote_deploy._build_ssh_stdin_script_cmd', return_value=['ssh', 'deploy@app1', 'bash -s --'])
    @patch('lib.remote_deploy.reload_nginx', return_value=True)
    @patch('lib.remote_deploy.push_nginx_config', return_value=True)
    @patch('lib.remote_deploy.push_artifact', return_value=True)
    @patch('lib.remote_deploy.get_deploy_target')
    def test_execute_remote_deployment_streams_script_over_stdin(
        self,
        mock_get_target,
        _mock_push_artifact,
        _mock_push_nginx,
        _mock_reload_nginx,
        _mock_build_ssh_stdin,
        mock_run,
        _mock_logger,
    ):
        from web.service_tools.cicd_executor import perform_remote_deployment

        mock_get_target.return_value = {'host': 'app1.example.com', 'base_dir': '/var/www'}
        mock_run.return_value = subprocess.CompletedProcess(args=['ssh'], returncode=0, stdout='ok', stderr='')

        with tempfile.TemporaryDirectory() as workspace:
            script_path = os.path.join(workspace, 'deploy.sh')
            with open(script_path, 'w') as script_file:
                script_file.write('echo hello\n')
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as log_file:
                log_path = log_file.name

            try:
                result = perform_remote_deployment(
                    workspace=workspace,
                    deploy_target='app1.example.com',
                    deploy_spec=None,
                    repo_url='https://example.com/repo.git',
                    commit_sha='abc123',
                    log_file=log_path,
                    repo_config={'scripts': {'deploy': 'deploy.sh'}},
                )
            finally:
                os.unlink(log_path)

        self.assertTrue(result)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs['input'], 'echo hello\n')
        self.assertEqual(mock_run.call_args.args[0], ['ssh', 'deploy@app1', 'bash -s --'])

    @patch('lib.remote_deploy.get_deploy_target', return_value=None)
    def test_remote_deployment_logs_unknown_target(self, _mock_get_target):
        from web.service_tools.cicd_executor import perform_remote_deployment

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as log_file:
            log_path = log_file.name

        try:
            with self.assertLogs(cicd_executor.logger, level='ERROR') as logs:
                result = perform_remote_deployment(
                    workspace='/tmp/workspace',
                    deploy_target='missing.example.com',
                    deploy_spec=None,
                    repo_url='https://example.com/repo.git',
                    commit_sha='abc123',
                    log_file=log_path,
                    repo_config={},
                )
        finally:
            os.unlink(log_path)

        self.assertFalse(result)
        self.assertIn("Unknown deploy target | deploy_target='missing.example.com'", "\n".join(logs.output))

    @patch('web.service_tools.cicd_executor.run_command', return_value=subprocess.CompletedProcess(args=['ssh'], returncode=0, stdout='ok', stderr=''))
    @patch('lib.remote_deploy._build_ssh_stdin_script_cmd', return_value=['ssh', 'deploy@app1', 'bash -s --'])
    @patch('lib.remote_deploy.reload_nginx', return_value=True)
    @patch('lib.remote_deploy.push_nginx_config', return_value=True)
    @patch('lib.remote_deploy.push_artifact', return_value=True)
    @patch('lib.remote_deploy.get_deploy_target')
    def test_remote_deployment_logs_success(
        self,
        mock_get_target,
        _mock_push_artifact,
        _mock_push_nginx,
        _mock_reload_nginx,
        _mock_build_ssh_stdin,
        _mock_run,
    ):
        from web.service_tools.cicd_executor import perform_remote_deployment

        mock_get_target.return_value = {'host': 'app1.example.com', 'base_dir': '/var/www'}

        with tempfile.TemporaryDirectory() as workspace:
            script_path = os.path.join(workspace, 'deploy.sh')
            with open(script_path, 'w') as script_file:
                script_file.write('echo hello\n')
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as log_file:
                log_path = log_file.name

            try:
                with self.assertLogs(cicd_executor.logger, level='INFO') as logs:
                    result = perform_remote_deployment(
                        workspace=workspace,
                        deploy_target='app1.example.com',
                        deploy_spec=None,
                        repo_url='https://example.com/repo.git',
                        commit_sha='abc123',
                        log_file=log_path,
                        repo_config={'scripts': {'deploy': 'deploy.sh'}},
                    )
            finally:
                os.unlink(log_path)

        self.assertTrue(result)
        output = "\n".join(logs.output)
        self.assertIn("Detected project type | deploy_target='app1.example.com' project_type='unknown'", output)
        self.assertIn("Remote deployment completed | deploy_target='app1.example.com' remote_path='/var/www/root'", output)


class TestExecutorStructuredLogging(unittest.TestCase):
    @patch('web.service_tools.cicd_executor.load_config_file', side_effect=FileNotFoundError)
    def test_load_config_rejects_missing_config(self, _mock_load):
        with self.assertRaises(FileNotFoundError):
            cicd_executor.load_config()

    @patch("web.service_tools.cicd_executor.os.path.lexists", return_value=False)
    @patch("web.service_tools.cicd_executor.run_build_command")
    def test_clone_or_update_repo_logs_clone_and_success(self, mock_run, _mock_exists):
        commit_sha = "a" * 40
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=["git", "clone"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "fetch"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "cat-file"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "merge-base"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "checkout"], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=["git", "clean"], returncode=0, stdout="", stderr=""),
        ]

        with self.assertLogs(cicd_executor.logger, level="INFO") as logs:
            result = cicd_executor.clone_or_update_repo(
                "https://github.com/org/repo.git",
                "/tmp/repo",
                "refs/heads/main",
                commit_sha,
            )

        self.assertTrue(result)
        clone_command = mock_run.call_args_list[0].args[0]
        self.assertIn('core.hooksPath=/dev/null', clone_command)
        self.assertIn('--no-checkout', clone_command)
        output = "\n".join(logs.output)
        self.assertIn("Creating fresh repository clone | repo_url='https://github.com/org/repo.git'", output)
        self.assertIn("Checking out authenticated commit | branch='main' commit_sha='aaaaaaaa' repo_url='https://github.com/org/repo.git'", output)
        self.assertIn("Repository checkout prepared | branch='main' commit_sha='aaaaaaaa' repo_url='https://github.com/org/repo.git'", output)

    @patch("web.service_tools.cicd_executor.run_build_command", return_value=subprocess.CompletedProcess(args=["/bin/bash"], returncode=0, stdout="", stderr=""))
    def test_run_script_logs_start_and_success(self, mock_run):
        with tempfile.TemporaryDirectory() as workspace:
            script_path = os.path.join(workspace, "build.sh")
            log_path = os.path.join(workspace, "build.log")
            with open(script_path, "w") as f:
                f.write("echo ok\n")

            with self.assertLogs(cicd_executor.logger, level="INFO") as logs:
                result = cicd_executor.run_script("build.sh", workspace, log_path)

        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0][:2], ['/usr/bin/python3', '-I'])
        self.assertTrue(args[0][2].endswith('/lib/cicd_project.py'))
        self.assertEqual(args[0][3:], [workspace, '--script', script_path])
        self.assertNotIn('env', kwargs)
        output = "\n".join(logs.output)
        self.assertIn(f"Running script | script_path='{script_path}'", output)
        self.assertIn(f"Script completed successfully | script_path='{script_path}'", output)

    @patch("web.service_tools.cicd_executor.send_notification_safe", side_effect=RuntimeError("notify boom"))
    def test_notify_success_logs_structured_failure(self, _mock_notify):
        with self.assertLogs(cicd_executor.logger, level="WARNING") as logs:
            cicd_executor.notify_success("https://github.com/org/repo.git", "abcdef123456", "/tmp/build.log", ["cfg"])

        self.assertIn(
            "Failed to send success notification | commit_sha='abcdef12' error='notify boom' repo_url='https://github.com/org/repo.git'",
            "\n".join(logs.output),
        )

    @patch("web.service_tools.cicd_executor.send_notification_safe", side_effect=RuntimeError("notify boom"))
    def test_notify_failure_logs_structured_failure(self, _mock_notify):
        with self.assertLogs(cicd_executor.logger, level="WARNING") as logs:
            cicd_executor.notify_failure("https://github.com/org/repo.git", "abcdef123456", "Build failed", ["cfg"])

        self.assertIn(
            "Failed to send failure notification | commit_sha='abcdef12' error='notify boom' repo_url='https://github.com/org/repo.git'",
            "\n".join(logs.output),
        )

    @patch("web.service_tools.cicd_executor.os.remove")
    @patch("web.service_tools.cicd_executor.load_notification_configs_from_state", return_value=[])
    @patch("web.service_tools.cicd_executor.run_script", return_value=True)
    @patch("web.service_tools.cicd_executor.clone_or_update_repo", return_value=True)
    @patch("web.service_tools.cicd_executor.load_config")
    @patch("web.service_tools.cicd_executor.os.makedirs")
    def test_process_job_logs_start_and_completion(
        self,
        _mock_makedirs,
        mock_load_config,
        _mock_clone,
        _mock_run_script,
        _mock_notifications,
        _mock_remove,
    ):
        mock_load_config.return_value = {
            "repositories": [
                {
                    "url": "https://github.com/org/repo.git",
                    "scripts": {"build": "build.sh"},
                }
            ]
        }
        job_data = {
            "repo_url": "https://github.com/org/repo.git",
            "ref": "refs/heads/main",
            "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
            "pusher": "alice",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            job_file = os.path.join(tmpdir, "job.json")
            with open(job_file, "w") as f:
                json.dump(job_data, f)

            with patch("web.service_tools.cicd_executor.run_manifest_workflow", return_value=True), \
                 patch("web.service_tools.cicd_executor.WORKSPACES_DIR", tmpdir), \
                 patch("web.service_tools.cicd_executor.LOGS_DIR", tmpdir):
                with self.assertLogs(cicd_executor.logger, level="INFO") as logs:
                    result = cicd_executor.process_job(job_file)

        self.assertTrue(result)
        output = "\n".join(logs.output)
        self.assertIn(f"Processing job | job_file='{job_file}'", output)
        self.assertIn(
            f"Job completed | commit_sha='abcdef12' job_file='{job_file}' repo_url='https://github.com/org/repo.git' result='success'",
            output,
        )


class TestCleanupOldBuildLogs(unittest.TestCase):
    """Test build log cleanup."""

    def test_cleanup_keeps_recent_logs(self):
        """Recent log files are not removed."""
        from web.service_tools.cicd_executor import cleanup_old_build_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            recent_log = os.path.join(tmpdir, 'def67890.log')
            with open(recent_log, 'w') as f:
                f.write('recent build log')

            with patch('web.service_tools.cicd_executor.LOGS_DIR', tmpdir):
                removed = cleanup_old_build_logs(days_to_keep=30)

            self.assertEqual(removed, 0)
            self.assertTrue(os.path.exists(recent_log))

    def test_cleanup_ignores_non_log_files(self):
        """Non-.log files in the logs directory are not removed."""
        from web.service_tools.cicd_executor import cleanup_old_build_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            other_file = os.path.join(tmpdir, 'somefile.txt')
            with open(other_file, 'w') as f:
                f.write('not a log')
            old_time = time.time() - (60 * 24 * 60 * 60)
            os.utime(other_file, (old_time, old_time))

            with patch('web.service_tools.cicd_executor.LOGS_DIR', tmpdir):
                removed = cleanup_old_build_logs(days_to_keep=30)

            self.assertEqual(removed, 0)
            self.assertTrue(os.path.exists(other_file))

    def test_cleanup_missing_logs_dir(self):
        """Returns 0 when the logs directory does not exist."""
        from web.service_tools.cicd_executor import cleanup_old_build_logs

        with patch('web.service_tools.cicd_executor.LOGS_DIR', '/nonexistent/logs/dir'):
            removed = cleanup_old_build_logs(days_to_keep=30)

        self.assertEqual(removed, 0)

    def test_cleanup_logs_structured_summary(self):
        """Cleanup summary uses structured logging context."""
        from web.service_tools.cicd_executor import cleanup_old_build_logs, logger

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log = os.path.join(tmpdir, 'abc12345.log')
            with open(old_log, 'w') as f:
                f.write('old build log')
            old_time = time.time() - (40 * 24 * 60 * 60)
            os.utime(old_log, (old_time, old_time))

            with patch('web.service_tools.cicd_executor.LOGS_DIR', tmpdir):
                with self.assertLogs(logger, level='INFO') as logs:
                    removed = cleanup_old_build_logs(days_to_keep=30)

            self.assertFalse(os.path.exists(old_log))
        self.assertEqual(removed, 1)
        self.assertIn("Cleaned up old build logs | days_to_keep=30 removed_count=1", "\n".join(logs.output))

    def test_cleanup_logs_structured_warning_on_remove_failure(self):
        from web.service_tools.cicd_executor import cleanup_old_build_logs, logger

        with tempfile.TemporaryDirectory() as tmpdir:
            old_log = os.path.join(tmpdir, "abc12345.log")
            with open(old_log, "w") as f:
                f.write("old build log")
            old_time = time.time() - (40 * 24 * 60 * 60)
            os.utime(old_log, (old_time, old_time))

            with patch("web.service_tools.cicd_executor.LOGS_DIR", tmpdir), \
                 patch("web.service_tools.cicd_executor.os.remove", side_effect=OSError("permission denied")):
                with self.assertLogs(logger, level="WARNING") as logs:
                    removed = cleanup_old_build_logs(days_to_keep=30)

        self.assertEqual(removed, 0)
        self.assertIn(
            "Failed to remove old build log | error='permission denied' log_file='abc12345.log'",
            "\n".join(logs.output),
        )


class TestCleanupStaleWorkspaces(unittest.TestCase):
    """Test stale workspace cleanup."""

    def test_empty_config_skips_cleanup(self):
        """Empty or invalid config does not delete any workspaces."""
        from web.service_tools.cicd_executor import cleanup_stale_workspaces

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = os.path.join(tmpdir, 'some-repo')
            os.makedirs(ws)

            for config in [{}, {'repositories': []}]:
                with patch('web.service_tools.cicd_executor.WORKSPACES_DIR', tmpdir):
                    removed = cleanup_stale_workspaces(config)
                self.assertEqual(removed, 0)
                self.assertTrue(os.path.exists(ws))

    def test_removes_stale_keeps_active(self):
        """Only stale workspaces are removed when both are present."""
        from web.service_tools.cicd_executor import cleanup_stale_workspaces

        with tempfile.TemporaryDirectory() as tmpdir:
            active_name = cicd_executor.get_workspace_name('https://github.com/org/active-repo.git')
            active_ws = os.path.join(tmpdir, active_name)
            stale_ws = os.path.join(tmpdir, 'stale-repo')
            os.makedirs(active_ws)
            os.makedirs(stale_ws)

            config = {'repositories': [{'url': 'https://github.com/org/active-repo.git'}]}

            with patch('web.service_tools.cicd_executor.WORKSPACES_DIR', tmpdir):
                removed = cleanup_stale_workspaces(config)

            self.assertEqual(removed, 1)
            self.assertTrue(os.path.exists(active_ws))
            self.assertFalse(os.path.exists(stale_ws))

    def test_missing_workspaces_dir(self):
        """Returns 0 when workspaces directory does not exist."""
        from web.service_tools.cicd_executor import cleanup_stale_workspaces

        config = {'repositories': [{'url': 'https://github.com/org/my-repo.git'}]}
        with patch('web.service_tools.cicd_executor.WORKSPACES_DIR', '/nonexistent/workspaces'):
            removed = cleanup_stale_workspaces(config)

        self.assertEqual(removed, 0)

    def test_ignores_files_in_workspaces_dir(self):
        """Regular files in the workspaces dir are not deleted."""
        from web.service_tools.cicd_executor import cleanup_stale_workspaces

        with tempfile.TemporaryDirectory() as tmpdir:
            stray_file = os.path.join(tmpdir, 'somefile.txt')
            with open(stray_file, 'w') as f:
                f.write('stray file')

            # Config has repos so the guard doesn't skip; the stray file is
            # not a directory so it should be left alone.
            config = {'repositories': [{'url': 'https://github.com/org/my-repo.git'}]}

            with patch('web.service_tools.cicd_executor.WORKSPACES_DIR', tmpdir):
                removed = cleanup_stale_workspaces(config)

            self.assertEqual(removed, 0)
            self.assertTrue(os.path.exists(stray_file))

    def test_logs_structured_stale_workspace_removal(self):
        """Stale workspace removals include structured workspace context."""
        from web.service_tools.cicd_executor import cleanup_stale_workspaces, logger

        with tempfile.TemporaryDirectory() as tmpdir:
            stale_ws = os.path.join(tmpdir, 'old-repo')
            os.makedirs(stale_ws)
            config = {'repositories': [{'url': 'https://github.com/org/other-repo.git'}]}

            with patch('web.service_tools.cicd_executor.WORKSPACES_DIR', tmpdir):
                with self.assertLogs(logger, level='INFO') as logs:
                    removed = cleanup_stale_workspaces(config)

        self.assertEqual(removed, 1)
        self.assertIn("Removed stale workspace | workspace='old-repo'", "\n".join(logs.output))

    def test_logs_structured_stale_workspace_removal_failure(self):
        from web.service_tools.cicd_executor import cleanup_stale_workspaces, logger

        with tempfile.TemporaryDirectory() as tmpdir:
            stale_ws = os.path.join(tmpdir, "old-repo")
            os.makedirs(stale_ws)
            config = {"repositories": [{"url": "https://github.com/org/other-repo.git"}]}

            with patch("web.service_tools.cicd_executor.WORKSPACES_DIR", tmpdir), \
                 patch("web.service_tools.cicd_executor.shutil.rmtree", side_effect=OSError("permission denied")):
                with self.assertLogs(logger, level="WARNING") as logs:
                    removed = cleanup_stale_workspaces(config)

        self.assertEqual(removed, 0)
        self.assertIn(
            "Failed to remove stale workspace | error='permission denied' workspace='old-repo'",
            "\n".join(logs.output),
        )


if __name__ == '__main__':
    unittest.main()
