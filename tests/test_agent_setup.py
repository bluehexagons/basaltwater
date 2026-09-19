"""Tests for agent VM repository/config payload preparation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from lib.setup_common import prepare_agent_payload


class TestAgentPayloadPreparation(unittest.TestCase):
    def test_github_credentials_stage_only_global_git_identity(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as payload_dir,
        ):
            gh_config_dir = os.path.join(home, '.config', 'gh')
            os.makedirs(gh_config_dir)
            with open(
                os.path.join(gh_config_dir, 'hosts.yml'),
                'w',
                encoding='utf-8',
            ) as file_obj:
                file_obj.write('github.com:\n  oauth_token: secret\n')
            os.chmod(os.path.join(gh_config_dir, 'hosts.yml'), 0o600)
            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                install_gh=True,
                git_access='read-write',
                git_auth_source='active',
                copy_agent_keys=True,
            )
            responses = [
                type(
                    'Completed',
                    (),
                    {'returncode': 0, 'stdout': 'Octo Cat\n', 'stderr': ''},
                )(),
                type(
                    'Completed',
                    (),
                    {
                        'returncode': 0,
                        'stdout': 'octo@example.test\n',
                        'stderr': '',
                    },
                )(),
            ]

            with (
                patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}),
                patch('lib.setup_common.shutil.which', return_value='/usr/bin/git'),
                patch(
                    'lib.setup_common.subprocess.run',
                    side_effect=responses,
                ) as run_command,
            ):
                prepare_agent_payload(config, payload_dir)

            identity_path = os.path.join(
                payload_dir,
                'config',
                'git',
                'identity.json',
            )
            with open(identity_path, encoding='utf-8') as file_obj:
                self.assertEqual(
                    json.load(file_obj),
                    {'name': 'Octo Cat', 'email': 'octo@example.test'},
                )
            self.assertTrue(config.github_auth_payload)
            self.assertTrue(config.git_identity_payload)
            self.assertEqual(os.stat(identity_path).st_mode & 0o777, 0o600)
            self.assertEqual(
                [call.args[0] for call in run_command.call_args_list],
                [
                    ['/usr/bin/git', 'config', '--global', '--get', 'user.name'],
                    ['/usr/bin/git', 'config', '--global', '--get', 'user.email'],
                ],
            )

    def test_stages_selected_config_and_credentials(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as payload_dir:
            opencode_skill_dir = os.path.join(home, '.config', 'opencode', 'skills', 'review')
            os.makedirs(opencode_skill_dir)
            with open(os.path.join(opencode_skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('---\nname: review\ndescription: Review code\n---\n')

            opencode_auth_dir = os.path.join(home, '.local', 'share', 'opencode')
            os.makedirs(opencode_auth_dir)
            with open(os.path.join(opencode_auth_dir, 'auth.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')
            os.chmod(os.path.join(opencode_auth_dir, 'auth.json'), 0o600)

            codex_dir = os.path.join(home, '.codex')
            os.makedirs(os.path.join(codex_dir, 'skills', 'review'))
            with open(os.path.join(codex_dir, 'config.toml'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('model = "test"\n')
            with open(os.path.join(codex_dir, 'auth.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')
            os.chmod(os.path.join(codex_dir, 'auth.json'), 0o600)

            claude_dir = os.path.join(home, '.claude')
            os.makedirs(os.path.join(claude_dir, 'commands'))
            with open(os.path.join(claude_dir, 'settings.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('{}\n')
            with open(os.path.join(claude_dir, '.credentials.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')
            os.chmod(os.path.join(claude_dir, '.credentials.json'), 0o600)

            gh_config_dir = os.path.join(home, '.config', 'gh')
            os.makedirs(gh_config_dir)
            with open(os.path.join(gh_config_dir, 'config.yml'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('editor: nvim\n')
            with open(os.path.join(gh_config_dir, 'hosts.yml'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('github.com:\n  oauth_token: secret\n')
            os.chmod(os.path.join(gh_config_dir, 'hosts.yml'), 0o600)

            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                install_gh=True,
                install_codex=True,
                install_claude=True,
                install_opencode=True,
                git_auth_source='active',
                agent_auth_files=[
                    ['codex', os.path.join(codex_dir, 'auth.json')],
                    ['claude', os.path.join(claude_dir, '.credentials.json')],
                    ['opencode', os.path.join(opencode_auth_dir, 'auth.json')],
                ],
                copy_agent_config=True,
                copy_agent_keys=True,
            )

            with patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}):
                prepare_agent_payload(config, payload_dir)

            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'config', 'opencode', 'skills', 'review', 'SKILL.md')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'config', 'codex', 'config.toml')
            ))
            self.assertFalse(os.path.exists(
                os.path.join(payload_dir, 'config', 'codex', 'auth.json')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'secrets', 'codex', 'auth.json')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'config', 'claude', 'settings.json')
            ))
            self.assertFalse(os.path.exists(
                os.path.join(payload_dir, 'config', 'claude', '.credentials.json')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'secrets', 'claude', '.credentials.json')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'secrets', 'opencode', 'auth.json')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'config', 'gh', 'config.yml')
            ))
            self.assertFalse(os.path.exists(
                os.path.join(payload_dir, 'config', 'gh', 'hosts.yml')
            ))
            self.assertTrue(os.path.exists(
                os.path.join(payload_dir, 'secrets', 'gh', 'hosts.yml')
            ))

    def test_specified_agent_file_uses_canonical_target_name(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as payload_dir:
            source = os.path.join(home, 'mounted-secret.txt')
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')
            os.chmod(source, 0o600)

            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                agent_tools=['codex'],
                agent_auth_files=[['codex', source]],
                copy_agent_keys=True,
            )

            with patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}):
                prepare_agent_payload(config, payload_dir)

            canonical = os.path.join(payload_dir, 'secrets', 'codex', 'auth.json')
            self.assertTrue(os.path.isfile(canonical))
            with open(canonical, encoding='utf-8') as file_obj:
                self.assertEqual(file_obj.read(), '{"token":"secret"}\n')

    def test_stale_codex_source_warns_without_printing_tokens(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as payload_dir,
        ):
            source = os.path.join(home, 'codex-auth.json')
            with open(source, 'w', encoding='utf-8') as file_obj:
                json.dump(
                    {
                        'auth_mode': 'chatgpt',
                        'last_refresh': '2020-01-01T00:00:00Z',
                        'tokens': {'refresh_token': 'never-print-this-token'},
                    },
                    file_obj,
                )
            os.chmod(source, 0o600)
            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                agent_tools=['codex'],
                agent_auth_files=[['codex', source]],
                copy_agent_keys=True,
            )

            with (
                patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}),
                patch('builtins.print') as output,
            ):
                prepare_agent_payload(config, payload_dir)

            rendered = '\n'.join(str(call.args[0]) for call in output.call_args_list)
            self.assertIn('last refresh was 2020-01-01T00:00:00Z', rendered)
            self.assertNotIn('never-print-this-token', rendered)

    def test_rejects_symlinked_agent_config_file(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as payload_dir,
        ):
            codex_dir = os.path.join(home, '.codex')
            os.makedirs(codex_dir)
            outside = os.path.join(home, 'outside-config.toml')
            with open(outside, 'w', encoding='utf-8') as file_obj:
                file_obj.write('secret = "must-not-stage"\n')
            os.symlink(outside, os.path.join(codex_dir, 'config.toml'))
            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                agent_tools=['codex'],
                copy_agent_config=True,
            )

            with patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}):
                with self.assertRaisesRegex(ValueError, 'symlinked source path'):
                    prepare_agent_payload(config, payload_dir)

            self.assertFalse(
                os.path.exists(
                    os.path.join(payload_dir, 'config', 'codex', 'config.toml')
                )
            )

    def test_rejects_symlinks_nested_in_agent_config_directory(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as payload_dir,
        ):
            opencode_dir = os.path.join(home, '.config', 'opencode')
            os.makedirs(os.path.join(opencode_dir, 'skills'))
            outside_dir = os.path.join(home, 'outside-skills')
            os.makedirs(outside_dir)
            with open(
                os.path.join(outside_dir, 'SKILL.md'),
                'w',
                encoding='utf-8',
            ) as file_obj:
                file_obj.write('must not stage\n')
            os.symlink(outside_dir, os.path.join(opencode_dir, 'skills', 'linked'))
            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                agent_tools=['opencode'],
                copy_agent_config=True,
            )

            with patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}):
                with self.assertRaisesRegex(ValueError, 'symlinked source path'):
                    prepare_agent_payload(config, payload_dir)

    def test_rejects_symlinked_agent_config_directory(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as payload_dir,
        ):
            outside_dir = os.path.join(home, 'outside-opencode')
            os.makedirs(outside_dir)
            config_root = os.path.join(home, '.config')
            os.makedirs(config_root)
            os.symlink(outside_dir, os.path.join(config_root, 'opencode'))
            config = SetupConfig(
                host='10.0.0.10',
                username='agentuser',
                system_type='server_dev',
                agent_tools=['opencode'],
                copy_agent_config=True,
            )

            with patch.dict(os.environ, {'HOME': home, 'SUDO_USER': ''}):
                with self.assertRaisesRegex(ValueError, 'symlinked source path'):
                    prepare_agent_payload(config, payload_dir)


if __name__ == '__main__':
    unittest.main()
