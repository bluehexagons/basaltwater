"""Tests for lib/config.py: SetupConfig serialization, to_remote_args, to_dict, from_dict."""

from __future__ import annotations

import os
import sys
import unittest
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig, DEFAULT_MACHINE_TYPE


class TestSetupConfigDefaults(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_default_values(self):
        config = self._make_config()
        self.assertEqual(config.machine_type, DEFAULT_MACHINE_TYPE)
        self.assertEqual(config.timezone, 'UTC')
        self.assertFalse(config.enable_rdp)
        self.assertFalse(config.rdp_existing_password)
        self.assertEqual(config.rdp_bind_address, '0.0.0.0')
        self.assertIsNone(config.rdp_allowed_sources)
        self.assertTrue(config.rdp_clipboard)
        self.assertFalse(config.rdp_drive_redirection)
        self.assertFalse(config.rdp_audio)
        self.assertEqual(config.rdp_idle_timeout, 0)
        self.assertFalse(config.dry_run)
        self.assertFalse(config.refresh_packages)
        self.assertEqual(config.desktop, 'xfce')
        self.assertIsNone(config.editor)

    def test_custom_values(self):
        config = self._make_config(timezone='America/New_York', enable_rdp=True, dry_run=True)
        self.assertEqual(config.timezone, 'America/New_York')
        self.assertTrue(config.enable_rdp)
        self.assertTrue(config.dry_run)


class TestSetupConfigToDict(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_to_dict_excludes_host_and_system_type(self):
        config = self._make_config()
        d = config.to_dict()
        self.assertNotIn('host', d)
        self.assertNotIn('system_type', d)

    def test_refresh_packages_is_transient(self):
        config = self._make_config(refresh_packages=True)

        self.assertIn('--refresh-packages', config.to_remote_args())
        self.assertNotIn('--refresh-packages', config.to_setup_command())
        self.assertNotIn('refresh_packages', config.to_dict())

    def test_to_dict_includes_username(self):
        config = self._make_config()
        d = config.to_dict()
        self.assertEqual(d['username'], 'testuser')

    def test_to_dict_tags_joined(self):
        config = self._make_config(tags=['web', 'prod'])
        d = config.to_dict()
        self.assertEqual(d['tags'], 'web,prod')

    def test_to_dict_excludes_share_credentials(self):
        config = self._make_config(share_credentials=[['user1', 'secret1']])
        d = config.to_dict()
        self.assertNotIn('share_credentials', d)

    def test_to_dict_excludes_login_password(self):
        config = self._make_config(password='supersecret')

        self.assertNotIn('password', config.to_dict())

    def test_to_dict_excludes_transient_agent_sources(self):
        config = self._make_config(
            agent_tools=['gh', 'codex'],
            git_auth_source='active',
            git_auth_file='/tmp/hosts.yml',
            agent_auth_source='active',
            agent_auth_files=[['codex', '/tmp/auth.json']],
            agent_config_source='active',
            copy_agent_keys=True,
            copy_agent_config=True,
        )
        saved = config.to_dict()
        for name in (
            'git_auth_source', 'git_auth_file', 'agent_auth_source',
            'agent_auth_files', 'agent_config_source', 'copy_agent_keys',
            'copy_agent_config', 'agent_payload',
        ):
            self.assertNotIn(name, saved)
        self.assertEqual(saved['agent_tools'], ['gh', 'codex'])

    def test_to_dict_keeps_antistatic_admin_username_without_password(self):
        config = self._make_config(
            antistatic_admin='operator',
            share_credentials=[['operator', 'secret1']],
        )
        d = config.to_dict()
        self.assertEqual(d['antistatic_admin'], 'operator')
        self.assertNotIn('share_credentials', d)

    def test_to_dict_redacts_inline_share_passwords(self):
        config = self._make_config(samba_shares=[['read', 'share', '/mnt/data', 'user1:secret1,user2']])
        d = config.to_dict()
        self.assertEqual(d['samba_shares'], [['read', 'share', '/mnt/data', 'user1,user2']])

    def test_to_dict_redacts_inline_smb_mount_passwords(self):
        config = self._make_config(smb_mounts=[['/mnt/share', '1.2.3.4', 'user1:secret1', 'docs', '/']])
        d = config.to_dict()
        self.assertEqual(d['smb_mounts'], [['/mnt/share', '1.2.3.4', 'user1', 'docs', '/']])


class TestSetupConfigFromDict(unittest.TestCase):
    def test_legacy_desktop_policy_migrates_without_mutating_saved_input(self):
        data = {'username': 'agent', 'rdp_max_sessions': 3,
                'rdp_kill_disconnected': True, 'rdp_disconnected_timeout': 60}
        with self.assertWarns(UserWarning):
            config = SetupConfig.from_dict('vm', 'agent_workstation', data)
        self.assertEqual(data['rdp_max_sessions'], 3)
        for name in ('rdp_max_sessions', 'rdp_kill_disconnected', 'rdp_disconnected_timeout'):
            self.assertNotIn(name, config.to_dict())

    def test_from_dict_basic(self):
        data = {'username': 'testuser', 'timezone': 'UTC'}
        config = SetupConfig.from_dict('host1', 'server_lite', data)
        self.assertEqual(config.host, 'host1')
        self.assertEqual(config.system_type, 'server_lite')
        self.assertEqual(config.username, 'testuser')

    def test_from_dict_tags_string(self):
        data = {'username': 'u', 'tags': 'web,prod'}
        config = SetupConfig.from_dict('h', 'server_lite', data)
        self.assertEqual(config.tags, ['web', 'prod'])

    def test_from_dict_tags_empty(self):
        data = {'username': 'u', 'tags': ''}
        config = SetupConfig.from_dict('h', 'server_lite', data)
        self.assertIsNone(config.tags)

    def test_from_dict_friendly_name_missing(self):
        data = {'username': 'u'}
        config = SetupConfig.from_dict('h', 'server_lite', data)
        self.assertIsNone(config.friendly_name)

    def test_from_dict_ignores_removed_feature_fields(self):
        data = {
            'username': 'u',
            'install_ruby': True,
            'reset_migrations': True,
            'api_subdomain': True,
            'desktop_interfaces': ['t3code'],
        }

        config = SetupConfig.from_dict('h', 'server_web', data)

        self.assertNotIn('install_ruby', config.to_dict())
        self.assertNotIn('reset_migrations', config.to_dict())
        self.assertNotIn('api_subdomain', config.to_dict())
        self.assertNotIn('desktop_interfaces', config.to_dict())

    def test_from_dict_rejects_invalid_legacy_privilege_broker_origin(self):
        with self.assertRaisesRegex(ValueError, 'Legacy privilege broker origin'):
            SetupConfig.from_dict(
                'vm.example', 'agent_vm',
                {'username': 'agent', 'privilege_broker': 'http://vm.example:9444'},
            )


class TestSetupConfigToRemoteArgs(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_basic_args(self):
        config = self._make_config()
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--system-type', args_str)
        self.assertIn('--username', args_str)
        self.assertIn('--machine', args_str)

    def test_rdp_flag(self):
        config = self._make_config(enable_rdp=True)
        args = config.to_remote_args()
        self.assertIn('--rdp', args)
        self.assertIn('--rdp-bind-address 0.0.0.0', args)

    def test_rdp_existing_password_flag(self):
        config = self._make_config(enable_rdp=True, rdp_existing_password=True)
        args = config.to_remote_args()
        self.assertIn('--rdp-existing-password', args)

    def test_rdp_policy_args(self):
        config = self._make_config(
            enable_rdp=True,
            rdp_bind_address='10.0.0.25',
            rdp_allowed_sources=['10.0.0.0/24', '2001:db8::/64'],
            rdp_clipboard=False,
            rdp_drive_redirection=True,
            rdp_audio=True,
            rdp_idle_timeout=14400,
        )
        args = config.to_remote_args()
        self.assertIn('--rdp-bind-address 10.0.0.25', args)
        self.assertIn('--rdp-source 10.0.0.0/24', args)
        self.assertIn('--rdp-source 2001:db8::/64', args)
        self.assertIn('--no-rdp-clipboard', args)
        self.assertIn('--rdp-drive-redirection', args)
        self.assertIn('--rdp-audio', args)
        self.assertIn('--rdp-idle-timeout 14400', args)

    def test_browser_single(self):
        config = self._make_config(browser='firefox', browsers=None)
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--browser firefox', args_str)

    def test_explicit_editor_is_sent_to_remote(self):
        config = self._make_config(editor='geany', include_desktop=True)

        self.assertIn('--editor geany', config.to_remote_args())

    def test_browsers_list(self):
        config = self._make_config(browsers=['firefox', 'brave'])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertEqual(args_str.count('--browser'), 2)

    def test_dry_run_flag(self):
        config = self._make_config(dry_run=True)
        args = config.to_remote_args()
        self.assertIn('--dry-run', args)

    def test_python_flag(self):
        config = self._make_config(install_python=True)
        args = config.to_remote_args()
        self.assertIn('--python', args)

    def test_agent_tool_flags(self):
        config = self._make_config(
            install_gh=True,
            install_codex=True,
            install_claude=True,
            install_opencode=True,
            copy_agent_keys=True,
            copy_agent_config=True,
            agent_repos=['https://github.com/user/my_codebase.git'],
        )
        args_str = ' '.join(config.to_remote_args())
        self.assertEqual(args_str.count('--agent-tool'), 4)
        self.assertIn('--agent-tool gh', args_str)
        self.assertIn('--agent-tool codex', args_str)
        self.assertIn('--agent-tool claude', args_str)
        self.assertIn('--agent-tool opencode', args_str)
        self.assertNotIn('--agent-tool t3code', args_str)
        self.assertIn('--agent-payload', args_str)
        self.assertIn('--repo https://github.com/user/my_codebase.git', args_str)

    def test_deploy_specs(self):
        config = self._make_config(deploy_specs=[['example.com/', 'https://github.com/user/repo.git']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        # Default deployment mode doesn't add a flag; lite and full modes use --deployment-lite/--deployment-full
        self.assertIn('--deploy', args_str)

    def test_explicit_agent_tools_select_only_requested_tools(self):
        config = self._make_config(agent_tools=['gh', 'codex'])
        self.assertEqual(config.selected_agent_tools(), ['gh', 'codex'])
        self.assertTrue(config.install_gh)
        self.assertTrue(config.install_codex)

    def test_invalid_agent_tool_fails(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported agent tool'):
            self._make_config(agent_tools=['everything'])

    def test_editor_requires_supported_desktop_setup(self):
        with self.assertRaisesRegex(ValueError, 'desktop-capable setup'):
            self._make_config(editor='geany')
        with self.assertRaisesRegex(ValueError, 'editor must be one of'):
            self._make_config(editor='emacs', include_desktop=True)

    def test_deployment_mode_flags(self):
        deploy_specs = [['example.com/', 'https://github.com/user/repo.git']]

        lite = self._make_config(deploy_specs=deploy_specs, deployment_mode='lite')
        self.assertIn('--deployment-lite', lite.to_remote_args())
        self.assertIn('--deployment-lite', lite.to_setup_command())

        full = self._make_config(deploy_specs=deploy_specs, deployment_mode='full')
        self.assertTrue(full.full_deploy)
        self.assertIn('--deployment-full', full.to_remote_args())
        self.assertIn('--deployment-full', full.to_setup_command())

    def test_deploy_latest(self):
        config = self._make_config(deploy_latest=True)
        args = config.to_remote_args()
        self.assertIn('--deploy-latest', args)
        args = config.to_setup_command()
        self.assertIn('--deploy-latest', args)
        self.assertNotIn('deploy_latest', config.to_dict())

    def test_sync_specs(self):
        config = self._make_config(sync_specs=[['/src', '/dst', 'daily']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--sync', args_str)

    def test_scrub_specs(self):
        config = self._make_config(scrub_specs=[['/data', '/db', '5%', 'weekly']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--scrub', args_str)

    def test_samba_shares(self):
        config = self._make_config(enable_samba=True, samba_shares=[['read', 'share', '/mnt/data', 'u:p']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--samba', args_str)
        self.assertIn('--share', args_str)

    def test_share_credentials(self):
        config = self._make_config(share_credentials=[['user1', 'pass1']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--credential user1 pass1', args_str)

    def test_smb_mounts(self):
        config = self._make_config(enable_smbclient=True, smb_mounts=[['/mnt/share', '1.2.3.4', 'u:p', 'share', '/']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--smbclient', args_str)
        self.assertIn('--mount-smb', args_str)

    def test_notify_specs(self):
        config = self._make_config(notify_specs=[['webhook', 'https://example.com/hook']])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--notify', args_str)

    def test_antistatic_db(self):
        config = self._make_config(antistatic_db='db.example.com:8081')
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--antistatic-db db.example.com:8081', args_str)

    def test_antistatic_admin(self):
        config = self._make_config(
            antistatic_server='lobby.example.com:8080',
            antistatic_admin='operator',
            share_credentials=[['operator', 'secret1']],
        )
        args_str = ' '.join(config.to_remote_args())
        self.assertIn('--antistatic-admin operator', args_str)
        self.assertIn('--credential operator secret1', args_str)

    def test_gogs(self):
        config = self._make_config(gogs=['git.example.com:3000', '/srv/gogs'])
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--gogs git.example.com:3000 /srv/gogs', args_str)

    def test_friendly_name_included(self):
        config = self._make_config(friendly_name='scrapbox')
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertIn('--name scrapbox', args_str)

    def test_friendly_name_none_omitted(self):
        config = self._make_config(friendly_name=None)
        args = config.to_remote_args()
        args_str = ' '.join(args)
        self.assertNotIn('--name', args_str)


class TestSetupConfigToSetupCommand(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_basic_command(self):
        config = self._make_config()
        parts = config.to_setup_command()
        self.assertIn('basaltw setup server_lite', parts[0])
        self.assertIn('testhost', parts)

    def test_includes_username(self):
        config = self._make_config()
        parts = config.to_setup_command(include_username=True)
        self.assertIn('testuser', parts)

    def test_excludes_username(self):
        config = self._make_config()
        parts = config.to_setup_command(include_username=False)
        self.assertNotIn('testuser', parts)

    def test_ssh_key(self):
        config = self._make_config(ssh_key='/path/to/key')
        parts = config.to_setup_command()
        self.assertTrue(any('-k' in p for p in parts))

    def test_non_default_timezone(self):
        config = self._make_config(timezone='America/New_York')
        parts = config.to_setup_command()
        self.assertTrue(any(p.startswith('-t ') for p in parts))

    def test_default_timezone_omitted(self):
        config = self._make_config(timezone='UTC')
        parts = config.to_setup_command()
        self.assertFalse(any(p.startswith('-t ') for p in parts))

    def test_non_default_machine_type(self):
        config = self._make_config(machine_type='hardware')
        parts = config.to_setup_command()
        self.assertTrue(any('--machine' in p for p in parts))

    def test_lxc_machine_type_is_explicit(self):
        config = self._make_config(system_type='server_web', machine_type='unprivileged')
        parts = config.to_setup_command()
        self.assertIn('--machine unprivileged', parts)

    def test_auto_machine_type_is_omitted_from_setup_command(self):
        config = self._make_config(system_type='server_web', machine_type='auto')
        parts = config.to_setup_command()
        self.assertFalse(any('--machine' in p for p in parts))

    def test_vm_machine_type_is_explicit_when_auto_is_the_default(self):
        config = self._make_config(system_type='server_web', machine_type='vm')
        parts = config.to_setup_command()
        self.assertIn('--machine vm', parts)

    def test_password_not_included(self):
        config = self._make_config(password='secret')
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertNotIn('secret', cmd)

    def test_rdp_policy_included(self):
        config = self._make_config(
            enable_rdp=True,
            rdp_bind_address='10.0.0.25',
            rdp_allowed_sources=['10.0.0.0/24'],
            rdp_clipboard=False,
            rdp_idle_timeout=14400,
        )
        cmd = ' '.join(config.to_setup_command())
        self.assertIn('--rdp-bind-address 10.0.0.25', cmd)
        self.assertIn('--rdp-source 10.0.0.0/24', cmd)
        self.assertIn('--no-rdp-clipboard', cmd)
        self.assertIn('--rdp-idle-timeout 14400', cmd)

    def test_rdp_existing_password_is_included_without_a_secret(self):
        config = self._make_config(
            enable_rdp=True,
            rdp_existing_password=True,
            password=None,
        )
        cmd = ' '.join(config.to_setup_command())
        self.assertIn('--rdp-existing-password', cmd)
        self.assertNotIn('--password', cmd)

    def test_share_credentials_redacted_for_username_only_shares(self):
        config = self._make_config(
            share_credentials=[['user1', 'secret1']],
            samba_shares=[['read', 'share', '/mnt/data', 'user1']],
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('--credential user1 [REDACTED]', cmd)
        self.assertNotIn('secret1', cmd)

    def test_antistatic_admin_password_is_redacted(self):
        config = self._make_config(
            antistatic_server='lobby.example.com',
            antistatic_admin='operator',
            share_credentials=[['operator', 'secret1']],
        )
        cmd = ' '.join(config.to_setup_command())
        self.assertIn('--antistatic-admin operator', cmd)
        self.assertIn('--credential operator [REDACTED]', cmd)
        self.assertNotIn('secret1', cmd)

    def test_share_credentials_omitted_for_inline_share_passwords(self):
        config = self._make_config(
            share_credentials=[['user1', 'secret1']],
            samba_shares=[['read', 'share', '/mnt/data', 'user1:secret1']],
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertNotIn('--credential user1 [REDACTED]', cmd)
        self.assertIn('user1:[REDACTED]', cmd)
        self.assertNotIn('secret1', cmd)

    def test_share_command_handles_incomplete_share_specs(self):
        config = self._make_config(
            samba_shares=[['read', 'share', '/mnt/data']],
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('--share read share /mnt/data', cmd)
        self.assertNotIn('[REDACTED]', cmd)

    def test_share_command_redacts_mixed_share_users(self):
        config = self._make_config(
            share_credentials=[['user1', 'secret1'], ['user2', 'secret2']],
            # Intentional whitespace, duplicates, and empty entries to exercise user list cleanup.
            samba_shares=[['read', 'share', '/mnt/data', ' user1 , , user2:secret2 ,user1,user3:secret3 ']],
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('--credential user1 [REDACTED]', cmd)
        self.assertEqual(cmd.count('--credential user1 [REDACTED]'), 1)
        self.assertIn('user1,user2:[REDACTED],user1,user3:[REDACTED]', cmd)
        self.assertNotIn('secret2', cmd)
        self.assertNotIn('secret3', cmd)

    def test_python_flag_included(self):
        config = self._make_config(install_python=True)
        parts = config.to_setup_command()
        self.assertIn('--python', parts)

    def test_editor_flag_is_reconstructed(self):
        config = self._make_config(editor='vscode', include_desktop=True)

        self.assertIn('--editor vscode', config.to_setup_command())

    def test_agent_tool_flags_included(self):
        config = self._make_config(
            install_gh=True,
            install_codex=True,
            install_claude=True,
            install_opencode=True,
            copy_agent_keys=True,
            copy_agent_config=True,
            agent_repos=['https://github.com/user/my_codebase.git'],
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('--agent-tool gh', parts)
        self.assertIn('--agent-tool codex', parts)
        self.assertIn('--agent-tool claude', parts)
        self.assertIn('--agent-tool opencode', parts)
        self.assertIn('--repo https://github.com/user/my_codebase.git', cmd)

    def test_smb_mount_password_redacted(self):
        config = self._make_config(
            smb_mounts=[['/mnt/share', '1.2.3.4', 'user1:secret1', 'docs', '/']]
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('user1:[REDACTED]', cmd)
        self.assertNotIn('secret1', cmd)


class TestSetupConfigFromArgs(unittest.TestCase):
    def _make_args(self, **overrides):
        defaults = dict(
            host='testhost',
            username='testuser',
            timezone='UTC',
            tags=None,
            desktop=None,
            browsers=None,
            browser=None,
            editor=None,
            install_office=None,
            enable_rdp=None,
            rdp_bind_address='0.0.0.0',
            rdp_allowed_sources=None,
            rdp_clipboard=True,
            rdp_drive_redirection=False,
            rdp_audio=False,
            rdp_idle_timeout=0,
            smb_mounts=None,
            enable_smbclient=None,
            auto_restart=None,
            auto_restart_force_days=None,
            auto_restart_grace=None,
            machine_type=None,
            control_plane=False,
            password=None,
            ssh_key=None,
            friendly_name=None,
            use_flatpak=False,
            apt_packages=None,
            flatpak_packages=None,
            dark_theme=False,
            dry_run=False,
            install_go=False,
            install_node=False,
            install_python=False,
            custom_steps=None,
            deploy_specs=None,
            full_deploy=False,
            enable_ssl=False,
            ssl_email=None,
            enable_cloudflare=False,
            enable_cicd=False,
            is_build_server=False,
            is_app_server=False,
            deploy_targets=None,
            enable_samba=False,
            samba_shares=None,
            share_credentials=None,
            sync_specs=None,
            scrub_specs=None,
            notify_specs=None,
            antistatic_server=None,
            antistatic_admin=None,
            antistatic_db=None,
            gogs=None,
            hosted_node=None,
            hosted_user='root',
            hosted_key=None,
            container_memory=None,
            container_storage=None,
            container_cores=1,
            container_base='debian',
        )
        defaults.update(overrides)
        return Namespace(**defaults)

    def test_workstation_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(self._make_args(), 'workstation_desktop')
        self.assertEqual(config.machine_type, 'auto')
        self.assertFalse(config.enable_rdp)
        self.assertTrue(config.include_desktop)
        self.assertTrue(config.include_cli_tools)
        self.assertTrue(config.include_desktop_apps)
        self.assertEqual(config.browser, 'firefox')

    def test_headless_desktop_opt_in_survives_remote_forwarding(self):
        config = SetupConfig.from_args(self._make_args(desktop='xfce'), 'agent_vm')
        self.assertTrue(config.include_desktop)
        self.assertIn('--desktop xfce', config.to_remote_args())
        self.assertIn('--desktop xfce', config.to_setup_command())
        headless = SetupConfig.from_args(self._make_args(), 'agent_vm')
        self.assertFalse(headless.include_desktop)
        self.assertFalse(any(arg.startswith('--desktop') for arg in headless.to_remote_args()))

    def test_workstation_rdp_can_be_enabled_explicitly(self):
        config = SetupConfig.from_args(self._make_args(enable_rdp=True), 'workstation_desktop')
        self.assertTrue(config.enable_rdp)
        self.assertTrue(config.include_desktop)

    def test_workstation_dev_defaults_to_firefox_without_an_editor(self):
        config = SetupConfig.from_args(self._make_args(), 'workstation_dev')

        self.assertEqual(config.browser, 'firefox')
        self.assertIsNone(config.editor)
        self.assertNotIn('--browser firefox', config.to_setup_command())

    def test_workstation_dev_accepts_an_explicit_editor(self):
        config = SetupConfig.from_args(
            self._make_args(editor='geany'),
            'workstation_dev',
        )

        self.assertEqual(config.editor, 'geany')
        self.assertIn('--editor geany', config.to_remote_args())

    def test_workstation_rdp_can_reuse_existing_local_password(self):
        config = SetupConfig.from_args(
            self._make_args(enable_rdp=True, rdp_existing_password=True),
            'workstation_desktop',
        )
        self.assertTrue(config.rdp_existing_password)

    def test_workstation_rdp_policy_from_args(self):
        config = SetupConfig.from_args(
            self._make_args(
                enable_rdp=True,
                rdp_bind_address='10.0.0.25',
                rdp_allowed_sources=['10.0.0.0/24'],
                rdp_clipboard=False,
                rdp_drive_redirection=True,
                rdp_audio=True,
                rdp_idle_timeout=14400,
            ),
            'workstation_dev',
        )
        self.assertEqual(config.rdp_bind_address, '10.0.0.25')
        self.assertEqual(config.rdp_allowed_sources, ['10.0.0.0/24'])
        self.assertFalse(config.rdp_clipboard)
        self.assertTrue(config.rdp_drive_redirection)
        self.assertTrue(config.rdp_audio)
        self.assertEqual(config.rdp_idle_timeout, 14400)

    def test_pc_dev_defaults_include_office_and_smbclient(self):
        config = SetupConfig.from_args(self._make_args(), 'pc_dev')
        self.assertEqual(config.machine_type, 'auto')
        self.assertTrue(config.install_office)
        self.assertTrue(config.enable_smbclient)
        self.assertTrue(config.include_pc_dev_apps)
        self.assertEqual(config.browser, 'firefox')

    def test_server_web_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(self._make_args(), 'server_web')
        self.assertEqual(config.machine_type, 'auto')

    def test_server_dev_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(self._make_args(), 'server_dev')
        self.assertEqual(config.machine_type, 'auto')

    def test_server_lite_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(self._make_args(), 'server_lite')
        self.assertEqual(config.machine_type, 'auto')

    def test_control_plane_flag_enables_administrator_tools(self):
        config = SetupConfig.from_args(
            self._make_args(control_plane=True),
            'workstation_dev',
        )
        self.assertTrue(config.include_control_plane_tools)
        self.assertTrue(config.include_desktop)

    def test_custom_steps_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(
            self._make_args(custom_steps='configure_swap'),
            'custom_steps',
        )
        self.assertEqual(config.machine_type, 'auto')

    def test_build_server_defaults_to_auto_detection(self):
        config = SetupConfig.from_args(
            self._make_args(is_build_server=True),
            'server_lite',
        )
        self.assertTrue(config.is_build_server)
        self.assertEqual(config.machine_type, 'auto')

    def test_hosted_setup_defaults_to_vm_for_guest_provisioning(self):
        config = SetupConfig.from_args(
            self._make_args(hosted_node='pve1'),
            'server_web',
        )
        self.assertEqual(config.machine_type, 'vm')

    def test_explicit_machine_type_overrides_vm_default(self):
        config = SetupConfig.from_args(
            self._make_args(machine_type='unprivileged'),
            'workstation_dev',
        )
        self.assertEqual(config.machine_type, 'unprivileged')

    def test_antistatic_db_from_args(self):
        config = SetupConfig.from_args(
            self._make_args(antistatic_db='db.example.com'),
            'server_web',
        )
        self.assertEqual(config.antistatic_db, 'db.example.com')

    def test_antistatic_admin_from_args(self):
        config = SetupConfig.from_args(
            self._make_args(antistatic_server='lobby.example.com', antistatic_admin='operator'),
            'server_web',
        )
        self.assertEqual(config.antistatic_admin, 'operator')

    def test_gogs_from_args(self):
        config = SetupConfig.from_args(
            self._make_args(gogs=['git.example.com:3000', '/srv/gogs']),
            'server_web',
        )
        self.assertEqual(config.gogs, ['git.example.com:3000', '/srv/gogs'])

    def test_server_proxmox_defaults_defer_restarts_without_a_force_deadline(self):
        config = SetupConfig.from_args(self._make_args(), 'server_proxmox')
        self.assertEqual(config.machine_type, 'auto')
        self.assertFalse(config.auto_restart)
        self.assertEqual(config.auto_restart_force_days, 0)
        self.assertEqual(config.auto_restart_grace, 5)
        self.assertFalse(config.include_cli_tools)
        self.assertFalse(config.include_desktop)

    def test_from_dict_server_proxmox_defaults_restart_policy_when_missing(self):
        config = SetupConfig.from_dict(
            'pve1',
            'server_proxmox',
            {'username': 'root'},
        )
        self.assertFalse(config.auto_restart)
        self.assertEqual(config.auto_restart_force_days, 0)

    def test_from_dict_maps_legacy_no_restart(self):
        config = SetupConfig.from_dict(
            'server1',
            'server_lite',
            {'username': 'root', 'no_restart': True},
        )
        self.assertFalse(config.auto_restart)


class TestSetupConfigHostedFields(unittest.TestCase):
    def _make_config(self, **kwargs):
        defaults: dict[str, object] = dict(host='testhost', username='testuser', system_type='server_lite')
        defaults.update(kwargs)
        return SetupConfig(**defaults)

    def test_hosted_defaults(self):
        config = self._make_config()
        self.assertIsNone(config.hosted_node)
        self.assertEqual(config.hosted_user, 'root')
        self.assertIsNone(config.hosted_key)
        self.assertIsNone(config.container_memory)
        self.assertIsNone(config.vm_balloon_min)
        self.assertIsNone(config.container_storage)
        self.assertIsNone(config.storage_mounts)
        self.assertIsNone(config.storage_caches)
        self.assertEqual(config.container_cores, 1)
        self.assertIsNone(config.vm_disk_settings)
        self.assertEqual(config.container_base, 'debian')

    def test_hosted_fields(self):
        config = self._make_config(
            hosted_node='10.0.0.1',
            hosted_user='admin',
            hosted_key='/path/to/key',
            container_memory='2G',
            vm_balloon_min='1G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
            storage_mounts=[['data', '/srv/data', 'xfs']],
            storage_caches=[['data', 'data-cache', 'writethrough']],
            vm_disk_settings=[['root', 'ssd=on'], ['data', 'discard=off']],
            container_cores=4,
            container_base='ubuntu',
            vm_image_storage='fast-files',
        )
        self.assertEqual(config.hosted_node, '10.0.0.1')
        self.assertEqual(config.hosted_user, 'admin')
        self.assertEqual(config.hosted_key, '/path/to/key')
        self.assertEqual(config.container_memory, '2G')
        self.assertEqual(config.vm_balloon_min, '1G')
        self.assertEqual(config.container_storage, [['root', 'auto', '10G'], ['template', 'local']])
        self.assertEqual(config.storage_mounts, [['data', '/srv/data', 'xfs']])
        self.assertEqual(
            config.storage_caches,
            [['data', 'data-cache', 'writethrough']],
        )
        self.assertEqual(
            config.vm_disk_settings,
            [['root', 'ssd=on'], ['data', 'discard=off']],
        )
        self.assertEqual(config.container_cores, 4)
        self.assertEqual(config.container_base, 'ubuntu')
        self.assertEqual(config.vm_image_storage, 'fast-files')

    def test_to_dict_includes_hosted_fields(self):
        config = self._make_config(
            hosted_node='10.0.0.1',
            container_memory='2G',
            vm_balloon_min='1G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
            storage_mounts=[['data', '/srv/data', 'xfs']],
            storage_caches=[['data', 'data-cache']],
            vm_disk_settings=[['root', 'ssd=on']],
            vm_image_storage='fast-files',
        )
        d = config.to_dict()
        self.assertEqual(d['hosted_node'], '10.0.0.1')
        self.assertEqual(d['container_memory'], '2G')
        self.assertEqual(d['vm_balloon_min'], '1G')
        self.assertEqual(d['container_storage'], [['root', 'auto', '10G'], ['template', 'local']])
        self.assertEqual(d['storage_mounts'], [['data', '/srv/data', 'xfs']])
        self.assertEqual(d['storage_caches'], [['data', 'data-cache']])
        self.assertEqual(d['vm_disk_settings'], [['root', 'ssd=on']])
        self.assertEqual(d['vm_image_storage'], 'fast-files')

    def test_from_dict_restores_hosted_fields(self):
        data = {
            'username': 'testuser',
            'hosted_node': '10.0.0.1',
            'container_memory': '4G',
            'vm_balloon_min': '2G',
            'container_storage': [['template', 'local'], ['root', 'auto', '5G']],
            'storage_mounts': [['data', '/srv/data', 'xfs']],
            'storage_caches': [['data', 'data-cache']],
            'vm_disk_settings': [['root', 'ssd=on']],
            'container_cores': 8,
            'container_base': 'fedora',
            'vm_image_storage': 'fast-files',
        }
        config = SetupConfig.from_dict('target', 'server_web', data)
        self.assertEqual(config.hosted_node, '10.0.0.1')
        self.assertEqual(config.container_memory, '4G')
        self.assertEqual(config.vm_balloon_min, '2G')
        self.assertEqual(config.container_storage, [['template', 'local'], ['root', 'auto', '5G']])
        self.assertEqual(config.storage_mounts, [['data', '/srv/data', 'xfs']])
        self.assertEqual(config.storage_caches, [['data', 'data-cache']])
        self.assertEqual(config.vm_disk_settings, [['root', 'ssd=on']])
        self.assertEqual(config.container_cores, 8)
        self.assertEqual(config.container_base, 'fedora')
        self.assertEqual(config.vm_image_storage, 'fast-files')

    def test_hosted_fields_not_in_remote_args(self):
        config = self._make_config(
            hosted_node='10.0.0.1',
            container_memory='2G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
        )
        args_str = ' '.join(config.to_remote_args())
        self.assertNotIn('--provision-on', args_str)
        self.assertNotIn('--memory', args_str)
        self.assertNotIn('--storage', args_str)
        self.assertNotIn('--cores', args_str)

    def test_vm_data_storage_is_sent_to_remote_setup(self):
        config = self._make_config(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_storage=[
                ['root', 'local-lvm', '32G'],
                ['agent-data', 'fast-lvm', '128G'],
                ['agent-cache', 'local-lvm', '16G'],
            ],
            storage_mounts=[['agent-data', '/srv/agent-workspace', 'ext4']],
            storage_caches=[['agent-data', 'agent-cache']],
            agent_workspace='/srv/agent-workspace',
        )

        args_str = ' '.join(config.to_remote_args())

        self.assertNotIn('--storage root', args_str)
        self.assertIn('--storage agent-data fast-lvm 128G', args_str)
        self.assertIn(
            '--storage-mount agent-data /srv/agent-workspace ext4',
            args_str,
        )
        self.assertIn('--storage-cache agent-data agent-cache', args_str)
        self.assertIn('--agent-workspace /srv/agent-workspace', args_str)

    def test_vm_data_storage_is_reconstructed_in_setup_command(self):
        config = self._make_config(
            machine_type='vm',
            hosted_node='10.0.0.1',
            container_storage=[
                ['root', 'local-lvm', '32G'],
                ['git-data', 'bulk-lvm', '256G'],
                ['git-cache', 'local-lvm', '32G'],
            ],
            storage_mounts=[['git-data', '/srv/gogs', 'xfs']],
            storage_caches=[['git-data', 'git-cache', 'writethrough']],
            vm_disk_settings=[
                ['root', 'ssd=on'],
                ['git-data', 'discard=off', 'ssd=off'],
            ],
        )

        command = ' '.join(config.to_setup_command())

        self.assertIn('--storage git-data bulk-lvm 256G', command)
        self.assertIn('--storage-mount git-data /srv/gogs xfs', command)
        self.assertIn(
            '--storage-cache git-data git-cache writethrough',
            command,
        )
        self.assertIn('--disk-ssd root', command)
        self.assertIn('--no-disk-discard git-data', command)
        self.assertIn('--no-disk-ssd git-data', command)

    def test_provisioning_fields_are_reconstructed_in_setup_command(self):
        config = self._make_config(
            host='10.0.0.50',
            hosted_node='10.0.0.1',
            hosted_user='admin',
            hosted_key='/path/to/proxmox-key',
            hosted_bridge='sdn-public',
            container_memory='2G',
            vm_balloon_min='1G',
            container_storage=[['root', 'auto', '10G'], ['template', 'local']],
            container_cores=4,
            container_base='ubuntu',
            vm_image_storage='fast-files',
            static_ipv4='10.0.0.50/24',
        )
        parts = config.to_setup_command()
        cmd = ' '.join(parts)
        self.assertIn('--provision-on 10.0.0.1', cmd)
        self.assertIn('--provision-user admin', cmd)
        self.assertIn('--provision-key /path/to/proxmox-key', cmd)
        self.assertIn('--bridge sdn-public', cmd)
        self.assertIn('--memory 2G', cmd)
        self.assertIn('--balloon-min 1G', cmd)
        self.assertIn('--storage root auto 10G', cmd)
        self.assertIn('--storage template local', cmd)
        self.assertIn('--image-storage fast-files', cmd)
        self.assertIn('--cores 4', cmd)
        self.assertIn('--base ubuntu', cmd)
        self.assertNotIn('--ip', cmd)
        self.assertEqual(parts[1], '10.0.0.50')

    def test_provisioning_command_keeps_non_default_prefix_in_target(self):
        config = self._make_config(
            host='10.0.0.50',
            hosted_node='10.0.0.1',
            machine_type='vm',
            ssh_key='/path/to/shared-key',
            hosted_key='/path/to/shared-key',
            static_ipv4='10.0.0.50/20',
        )

        parts = config.to_setup_command()

        self.assertEqual(parts[1], '10.0.0.50/20')
        self.assertNotIn('--ip 10.0.0.50/20', parts)
        self.assertNotIn('--machine vm', parts)
        self.assertFalse(any('--provision-key' in part for part in parts))


if __name__ == '__main__':
    unittest.main()
