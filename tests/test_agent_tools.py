"""Tests for agent installer metadata, verification, and local diagnostics."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.agent_steps import (
    _chown_path,
    _copy_payload_directory,
    _copy_secret_file,
    _configure_git_default_branch,
    _configure_git_identity,
    _merge_github_credentials,
    _user_home,
    copy_agent_tooling_payload,
    clone_agent_repositories,
    install_claude,
    install_codex,
    install_agent_cli_launcher,
    install_git_for_agent_repositories,
    install_git_lfs_for_agent_repositories,
    install_opencode,
    update_managed_agent_tools,
)
from lib.agent_auth import (
    _remote_set_script,
    get_agent_auth_status,
    set_agent_credential,
)
from lib.agent_credentials import inspect_codex_auth_payload
from lib.remote_utils import CommandTimeoutError
from lib.agent_cli import (
    _repair_t3_native_runtime,
    _t3_port,
    _tool_path,
    add_agent_subparser,
    inspect_agent_tools,
    inspect_t3code,
    run_agent_command,
    update_agent_tools,
)
from lib.agent_cli import _download_codex_installer, _invoke_agent_update
from lib.config import SetupConfig


def _test_jwt(claims: dict[str, object]) -> str:
    def encode(value: dict[str, object]) -> str:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(claims)}.test-signature"


class TestCodexCredentialMetadata(unittest.TestCase):
    def test_expired_chatgpt_auth_reports_dates_without_tokens(self):
        access_token = _test_jwt({"iat": 1786789824, "exp": 1787653824})
        payload = json.dumps(
            {
                "auth_mode": "chatgpt",
                "last_refresh": "2026-08-15T10:30:24Z",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "never-print-refresh-token",
                    "account_id": "never-print-account-id",
                },
            }
        ).encode("utf-8")

        result = inspect_codex_auth_payload(
            payload,
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(result["status"], "refresh_required")
        self.assertTrue(result["access_token_expired"])
        self.assertEqual(result["access_token_expires_at"], "2026-08-25T10:30:24Z")
        self.assertIn("refresh_overdue", result["warnings"])
        rendered = json.dumps(result)
        self.assertNotIn(access_token, rendered)
        self.assertNotIn("never-print-refresh-token", rendered)
        self.assertNotIn("never-print-account-id", rendered)

    def test_current_chatgpt_auth_reports_refresh_metadata(self):
        payload = json.dumps(
            {
                "auth_mode": "chatgpt",
                "last_refresh": "2026-08-26T10:30:24Z",
                "tokens": {
                    "access_token": _test_jwt(
                        {"iat": 1787740224, "exp": 1788604224}
                    ),
                    "refresh_token": "refresh-token",
                },
            }
        ).encode("utf-8")

        result = inspect_codex_auth_payload(
            payload,
            now=datetime(2026, 8, 27, tzinfo=timezone.utc),
        )

        self.assertEqual(result["status"], "current")
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["refresh_token_present"])

    def test_malformed_codex_auth_is_invalid_without_echoing_input(self):
        result = inspect_codex_auth_payload(b"not-json-secret")

        self.assertEqual(result["status"], "invalid")
        self.assertNotIn("not-json-secret", json.dumps(result))

    def test_untrusted_auth_mode_is_not_returned_as_metadata(self):
        auth_mode = "never-print-auth-mode-secret"
        result = inspect_codex_auth_payload(
            json.dumps({"auth_mode": auth_mode}).encode("utf-8")
        )

        self.assertIsNone(result["auth_mode"])
        self.assertNotIn(auth_mode, json.dumps(result))

    def test_api_key_auth_reports_presence_without_key_contents(self):
        api_key = "never-print-api-key"
        result = inspect_codex_auth_payload(
            json.dumps(
                {
                    "auth_mode": "apikey",
                    "OPENAI_API_KEY": api_key,
                }
            ).encode("utf-8")
        )

        self.assertEqual(result["status"], "current")
        self.assertEqual(result["auth_mode"], "api_key")
        self.assertTrue(result["api_key_present"])
        self.assertNotIn(api_key, json.dumps(result))

    def test_non_string_credentials_are_not_treated_as_present(self):
        secret = "never-print-nested-credential"
        result = inspect_codex_auth_payload(
            json.dumps(
                {
                    "OPENAI_API_KEY": {"value": secret},
                    "tokens": {"refresh_token": {"value": secret}},
                }
            ).encode("utf-8")
        )

        self.assertFalse(result["api_key_present"])
        self.assertFalse(result["refresh_token_present"])
        self.assertNotIn(secret, json.dumps(result))

    def test_excessively_nested_auth_payload_is_invalid(self):
        with patch("lib.agent_credentials.json.loads", side_effect=RecursionError):
            result = inspect_codex_auth_payload(b"{}")

        self.assertEqual(result["status"], "invalid")


class TestOfficialAgentInstallers(unittest.TestCase):
    def setUp(self):
        self.config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )

    def test_cli_installers_use_official_scripts_without_npm(self):
        with patch('common.agent_steps._install_script_tool') as installer:
            install_codex(self.config)
            install_claude(self.config)
            install_opencode(self.config)

        commands = [call.kwargs['command'] for call in installer.call_args_list]
        self.assertEqual(commands, ['codex', 'claude', 'opencode'])

    def test_user_home_comes_from_account_database(self):
        account = type('Account', (), {'pw_dir': '/srv/agent'})()
        with patch('common.agent_steps.pwd.getpwnam', return_value=account):
            self.assertEqual(_user_home(self.config), '/srv/agent')

    def test_agent_paths_use_primary_group_and_fail_on_ownership_error(self):
        account = type(
            'Account',
            (),
            {'pw_dir': '/srv/agent', 'pw_uid': 1201, 'pw_gid': 1202},
        )()
        failed = type(
            'Completed',
            (),
            {'returncode': 1, 'stdout': '', 'stderr': 'operation not permitted'},
        )()
        with (
            patch('common.agent_steps.pwd.getpwnam', return_value=account),
            patch('common.agent_steps.run', return_value=failed) as run_command,
        ):
            with self.assertRaisesRegex(RuntimeError, 'operation not permitted'):
                _chown_path(self.config, '/srv/agent/.codex')

        self.assertEqual(
            run_command.call_args.args[0],
            ['chown', '-R', '1201:1202', '/srv/agent/.codex'],
        )

    def test_git_lfs_is_installed_and_initialized_for_target_user(self):
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': 'git-lfs/3.7.0', 'stderr': ''},
        )()
        with (
            patch('common.agent_steps.shutil.which', return_value=None),
            patch('common.agent_steps.install_package', return_value=True) as install,
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch(
                'common.agent_steps._run_as_login_user',
                return_value=completed,
            ) as run_as_user,
        ):
            install_git_lfs_for_agent_repositories(self.config)

        install.assert_called_once_with(
            'Git LFS', 'git-lfs', ['apt-get', 'install', '-y', '-qq', 'git-lfs']
        )
        self.assertEqual(
            [call.args[2] for call in run_as_user.call_args_list],
            ['git lfs install', 'git lfs version'],
        )

    def test_git_setup_defaults_new_repositories_to_main(self):
        missing = type(
            'Completed',
            (),
            {'returncode': 1, 'stdout': '', 'stderr': ''},
        )()
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': '', 'stderr': ''},
        )()
        with (
            patch('common.agent_steps.shutil.which', return_value='/usr/bin/git'),
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch(
                'common.agent_steps._run_as_login_user',
                side_effect=[missing, completed],
            ) as run_as_user,
        ):
            install_git_for_agent_repositories(self.config)

        self.assertEqual(
            [call.args[2] for call in run_as_user.call_args_list],
            [
                'git config --global --get init.defaultBranch',
                'git config --global init.defaultBranch main',
            ],
        )

    def test_git_setup_preserves_an_explicit_default_branch(self):
        existing = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': 'trunk\n', 'stderr': ''},
        )()
        with (
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch(
                'common.agent_steps._run_as_login_user',
                return_value=existing,
            ) as run_as_user,
        ):
            _configure_git_default_branch(self.config)

        run_as_user.assert_called_once()
        self.assertEqual(
            run_as_user.call_args.args[2],
            'git config --global --get init.defaultBranch',
        )

    def test_git_lfs_initialization_failure_is_fatal(self):
        failed = type(
            'Completed',
            (),
            {'returncode': 1, 'stdout': '', 'stderr': 'config failed'},
        )()
        with (
            patch('common.agent_steps.shutil.which', return_value='/usr/bin/git-lfs'),
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch('common.agent_steps._run_as_login_user', return_value=failed),
        ):
            with self.assertRaisesRegex(RuntimeError, 'config failed'):
                install_git_lfs_for_agent_repositories(self.config)

    def test_git_lfs_is_preserved_in_remote_and_saved_commands(self):
        self.config.install_git_lfs = True

        self.assertIn('--git-lfs', self.config.to_remote_args())
        self.assertIn('--git-lfs', self.config.to_setup_command())
        self.assertTrue(self.config.to_dict()['install_git_lfs'])

    def test_agent_vm_gets_managed_user_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'infra_tools.py')
            home = os.path.join(directory, 'home')
            os.makedirs(home)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('# target source\n')

            with (
                patch('common.agent_steps.AGENT_CLI_SOURCE', source),
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_path') as chown_path,
                patch('common.agent_steps._ensure_agent_shell_path'),
            ):
                install_agent_cli_launcher(self.config)

            launcher = os.path.join(home, '.local', 'bin', 'infra-tools')
            with open(launcher, encoding='utf-8') as file_obj:
                content = file_obj.read()
            self.assertIn('# Managed by infra_tools agent setup', content)
            self.assertIn(f'exec /usr/bin/python3 {source} "$@"', content)
            self.assertEqual(os.stat(launcher).st_mode & 0o777, 0o755)
            self.assertEqual(
                chown_path.call_args_list[0].args[1],
                os.path.join(home, '.local'),
            )

    def test_script_installer_repairs_local_bin_ownership_before_running(self):
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': '', 'stderr': ''},
        )()
        events: list[str] = []

        with (
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch(
                'common.agent_steps._tool_available',
                side_effect=[False, True],
            ),
            patch('common.agent_steps.run', return_value=completed),
            patch(
                'common.agent_steps._ensure_agent_directory',
                side_effect=lambda path, mode: events.append(f'ensure:{path}:{mode:o}'),
            ),
            patch(
                'common.agent_steps._chown_path',
                side_effect=lambda _config, path: events.append(f'chown:{path}'),
            ),
            patch(
                'common.agent_steps._run_as_login_user',
                side_effect=lambda *_args, **_kwargs: (
                    events.append('installer') or completed
                ),
            ),
            patch('common.agent_steps._ensure_agent_shell_path'),
        ):
            install_codex(self.config)

        self.assertEqual(
            events,
            [
                'ensure:/home/agent/.local/bin:755',
                'chown:/home/agent/.local',
                'installer',
            ],
        )

    def test_setup_updates_selected_agent_tools_as_target_user(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex", "claude", "opencode"],
        )
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "updated\n", "stderr": ""},
        )()
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch(
                "common.agent_steps._tool_path",
                side_effect=lambda _config, tool: f"/home/agent/.local/bin/{tool}",
            ),
            patch(
                "common.agent_steps._run_as_login_user",
                return_value=completed,
            ) as run_as_user,
        ):
            update_managed_agent_tools(config)

        run_as_user.assert_called_once()
        command = run_as_user.call_args.args[2]
        self.assertIn("agent update", command)
        self.assertIn("--tools-only-readiness", command)
        self.assertIn("--tool codex", command)
        self.assertIn("--tool claude", command)
        self.assertIn("--tool opencode", command)
        self.assertTrue(run_as_user.call_args.kwargs["capture_output"])

    def test_setup_agent_update_failure_is_visible(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
        )
        failed = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "", "stderr": "network unavailable"},
        )()
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch(
                "common.agent_steps._run_as_login_user",
                return_value=failed,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                update_managed_agent_tools(config)

    def test_setup_agent_update_failure_includes_both_output_streams(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
        )
        failed = type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": "Agent update result\n  ✗ codex: post_update_verification",
                "stderr": "codex: accepted vendor-rolling; SHA-256 abc123; provenance /state/codex.json",
            },
        )()
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch("common.agent_steps._run_as_login_user", return_value=failed),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "(?s)Agent update result.*post_update_verification.*accepted vendor-rolling",
            ):
                update_managed_agent_tools(config)

    def test_setup_agent_update_surfaces_broader_readiness_warning(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
        )
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": "Warning: broader post-update readiness is unhealthy\n",
                "stderr": "",
            },
        )()
        output = io.StringIO()
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch("common.agent_steps._tool_path", return_value=None),
            patch("common.agent_steps._run_as_login_user", return_value=completed),
            redirect_stdout(output),
        ):
            update_managed_agent_tools(config)

        self.assertIn(
            "broader host/T3 readiness reported unhealthy", output.getvalue()
        )

    def test_setup_skips_redundant_update_for_newly_installed_tool(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
        )
        config._freshly_installed_agent_tools = {"codex"}
        with patch("common.agent_steps._run_as_login_user") as run_as_user:
            update_managed_agent_tools(config)

        run_as_user.assert_not_called()

    def test_setup_leaves_externally_managed_tool_with_package_manager(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
        )
        with (
            patch("common.agent_steps._user_home", return_value="/home/agent"),
            patch("common.agent_steps._tool_path", return_value="/usr/bin/codex"),
            patch("common.agent_steps._run_as_login_user") as run_as_user,
        ):
            update_managed_agent_tools(config)

        run_as_user.assert_not_called()

    def test_agent_vm_launcher_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'infra_tools.py')
            home = os.path.join(directory, 'home')
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('# target source\n')
            os.symlink('/bin/true', os.path.join(bin_dir, 'infra-tools'))

            with (
                patch('common.agent_steps.AGENT_CLI_SOURCE', source),
                patch('common.agent_steps._user_home', return_value=home),
            ):
                with self.assertRaisesRegex(RuntimeError, 'symlinked agent destination'):
                    install_agent_cli_launcher(self.config)


class TestAgentDoctor(unittest.TestCase):
    def test_t3_port_rejects_malformed_or_out_of_range_values(self):
        with tempfile.TemporaryDirectory() as home:
            wrapper = os.path.join(home, 'wrapper')
            with open(wrapper, 'w', encoding='utf-8') as file_obj:
                file_obj.write('T3CODE_PORT=99999\n')
            self.assertIsNone(_t3_port(wrapper))

            with open(wrapper, 'w', encoding='utf-8') as file_obj:
                file_obj.write('T3CODE_PORT=not-a-port\n')
            self.assertEqual(_t3_port(wrapper), 3773)

    def test_t3code_doctor_reports_managed_checks_without_secrets(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = os.path.join(home, '.t3', 'runtime')
            version = '0.0.34'
            binary_dir = os.path.join(
                runtime, 'versions', version, 'node_modules', 't3', 'dist'
            )
            os.makedirs(binary_dir)
            t3_binary = os.path.join(binary_dir, 'bin.mjs')
            with open(t3_binary, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\necho t3 0.0.1\n')
            os.chmod(t3_binary, 0o755)
            with open(
                os.path.join(runtime, 'service-state.json'),
                'w',
                encoding='utf-8',
            ) as file_obj:
                json.dump({'protocol': 2, 'activeVersion': version}, file_obj)
            wrapper = os.path.join(
                home,
                '.local',
                'bin',
                'infra-tools-t3code-pairing-provider',
            )
            os.makedirs(os.path.dirname(wrapper))
            with open(wrapper, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\n')
            os.chmod(wrapper, 0o755)
            with (
                patch('lib.agent_cli._tool_path', side_effect=lambda tool, _home: t3_binary if tool == 't3' else None),
                patch('lib.agent_cli._run_check', return_value=type('Completed', (), {'returncode': 1, 'stdout': '', 'stderr': ''})()),
                patch('lib.agent_cli._t3_endpoint_reachable', return_value=False),
            ):
                result = inspect_t3code(home)

        self.assertFalse(result['healthy'])
        self.assertIn('git_identity', result['checks'])
        self.assertFalse(result['checks']['service_enabled'])
        self.assertNotIn('secret', str(result))

    def test_t3code_doctor_targets_current_user_systemd_bus(self):
        completed = type(
            'Completed',
            (),
            {'returncode': 1, 'stdout': '', 'stderr': ''},
        )()
        with (
            tempfile.TemporaryDirectory() as home,
            patch('lib.agent_cli._run_check', return_value=completed) as run_check,
            patch('lib.agent_cli._t3_endpoint_reachable', return_value=False),
        ):
            inspect_t3code(home)

        systemctl_call = next(
            call
            for call in run_check.call_args_list
            if call.args[0][:2] == ['systemctl', '--user']
        )
        environment = systemctl_call.kwargs['environment']
        self.assertEqual(environment['XDG_RUNTIME_DIR'], f'/run/user/{os.getuid()}')
        self.assertEqual(
            environment['DBUS_SESSION_BUS_ADDRESS'],
            f'unix:path=/run/user/{os.getuid()}/bus',
        )

    def test_t3code_doctor_fix_enables_service_for_future_boots(self):
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': 'enabled\n', 'stderr': ''},
        )
        runtime_enabled = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': 'enabled-runtime\n', 'stderr': ''},
        )
        commands: list[list[str]] = []
        enabled = False

        def run_check(command, **_kwargs):
            nonlocal enabled
            commands.append(command)
            if command[:3] == ['systemctl', '--user', 'is-enabled']:
                return completed if enabled else runtime_enabled
            if command[:3] == ['systemctl', '--user', 'enable']:
                enabled = True
            return completed

        with (
            tempfile.TemporaryDirectory() as home,
            patch('lib.agent_cli._t3_active_binary', return_value='/tmp/t3'),
            patch('lib.agent_cli._t3_node_binary', return_value='/usr/bin/node'),
            patch('lib.agent_cli._t3_native_runtime_healthy', return_value=True),
            patch('lib.agent_cli._tool_path', return_value=None),
            patch('lib.agent_cli._run_check', side_effect=run_check),
            patch('lib.agent_cli._t3_endpoint_reachable', return_value=True),
            patch('lib.agent_cli._tool_version', return_value='t3 v0.0.35'),
        ):
            result = inspect_t3code(home, fix=True)

        self.assertTrue(result['checks']['service_enabled'])
        self.assertIn('enabled T3 Code service at boot', result['fixes'])
        self.assertIn(
            ['systemctl', '--user', 'enable', 't3code.service'],
            commands,
        )

    def test_t3code_doctor_fix_repairs_native_runtime_before_restart(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = os.path.join(home, '.t3', 'runtime')
            version = '0.0.35'
            binary_dir = os.path.join(
                runtime, 'versions', version, 'node_modules', 't3', 'dist'
            )
            os.makedirs(binary_dir)
            t3_binary = os.path.join(binary_dir, 'bin.mjs')
            with open(t3_binary, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\necho t3 0.0.35\n')
            os.chmod(t3_binary, 0o755)
            with open(
                os.path.join(runtime, 'service-state.json'),
                'w',
                encoding='utf-8',
            ) as file_obj:
                json.dump({'protocol': 2, 'activeVersion': version}, file_obj)

            node_bin = os.path.join(home, '.nvm', 'bin')
            os.makedirs(node_bin)
            node = os.path.join(node_bin, 'node')
            with open(node, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\n')
            os.chmod(node, 0o755)
            drop_in = os.path.join(
                home,
                '.config',
                'systemd',
                'user',
                't3code.service.d',
                'infra-tools.conf',
            )
            os.makedirs(os.path.dirname(drop_in))
            with open(drop_in, 'w', encoding='utf-8') as file_obj:
                file_obj.write(f'Environment=PATH={node_bin}:/usr/bin\n')

            state = {'active_checks': 0}

            def run_check(command, **_kwargs):
                if command[:3] == ['systemctl', '--user', 'is-active']:
                    state['active_checks'] += 1
                    return type(
                        'Completed',
                        (),
                        {
                            'returncode': 1 if state['active_checks'] == 1 else 0,
                            'stdout': '',
                            'stderr': '',
                        },
                    )()
                if command[:3] == ['systemctl', '--user', 'is-enabled']:
                    return type(
                        'Completed',
                        (),
                        {'returncode': 0, 'stdout': 'enabled\n', 'stderr': ''},
                    )()
                return type(
                    'Completed',
                    (),
                    {'returncode': 0, 'stdout': '', 'stderr': ''},
                )()

            with (
                patch('lib.agent_cli._tool_path', return_value=None),
                patch('lib.agent_cli._run_check', side_effect=run_check),
                patch(
                    'lib.agent_cli._t3_native_runtime_healthy',
                    return_value=False,
                ),
                patch(
                    'lib.agent_cli._repair_t3_native_runtime',
                    return_value=True,
                ) as repair,
                patch('lib.agent_cli._t3_endpoint_reachable', return_value=True),
                patch('lib.agent_cli._tool_version', return_value='t3 v0.0.35'),
            ):
                result = inspect_t3code(home, fix=True)

        repair.assert_called_once()
        self.assertTrue(result['checks']['native_runtime'])
        self.assertIn('rebuilt T3 Code native runtime', result['fixes'])
        self.assertIn('restarted inactive T3 Code service', result['fixes'])
        self.assertTrue(result['service_log'].endswith('boot-service.log'))

    def test_t3code_doctor_repair_uses_temporary_npm_script_allowlist(self):
        with tempfile.TemporaryDirectory() as home:
            version_root = os.path.join(home, '.t3', 'runtime', 'versions', '0.0.35')
            binary = os.path.join(
                version_root,
                'node_modules',
                't3',
                'dist',
                'bin.mjs',
            )
            os.makedirs(os.path.dirname(binary))
            with open(binary, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\n')
            node_bin = os.path.join(home, 'node-bin')
            os.makedirs(node_bin)
            for executable in ('node', 'npm'):
                path = os.path.join(node_bin, executable)
                with open(path, 'w', encoding='utf-8') as file_obj:
                    file_obj.write('#!/bin/sh\n')
                os.chmod(path, 0o755)
            observed: dict[str, object] = {}
            completed = type(
                'Completed',
                (),
                {'returncode': 0, 'stdout': '', 'stderr': ''},
            )()

            def run_check(command, **kwargs):
                if len(command) > 1 and command[1] == 'rebuild':
                    environment = kwargs['environment']
                    npm_config = environment['NPM_CONFIG_USERCONFIG']
                    with open(npm_config, encoding='utf-8') as file_obj:
                        observed['npm_config'] = file_obj.read()
                    observed['command'] = command
                    observed['environment'] = environment.copy()
                return completed

            environment = {
                'npm_config_dangerously_allow_all_scripts': 'true',
                'NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS': 'true',
            }
            with patch('lib.agent_cli._run_check', side_effect=run_check):
                self.assertTrue(
                    _repair_t3_native_runtime(
                        os.path.join(node_bin, 'node'),
                        binary,
                        environment,
                    )
                )

        command = observed['command']
        repair_environment = observed['environment']
        self.assertEqual(
            observed['npm_config'],
            'allow-scripts=node-pty,msgpackr-extract\n',
        )
        self.assertNotIn('--dangerously-allow-all-scripts', command)
        self.assertNotIn('npm_config_allow_scripts', repair_environment)
        self.assertNotIn('NPM_CONFIG_ALLOW_SCRIPTS', repair_environment)
        self.assertNotIn('npm_config_dangerously_allow_all_scripts', repair_environment)
        self.assertNotIn('NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS', repair_environment)
        self.assertEqual(repair_environment['CC'], 'gcc')
        self.assertEqual(repair_environment['CXX'], 'g++')
        self.assertEqual(repair_environment['npm_config_strict_allow_scripts'], 'false')
        self.assertFalse(os.path.exists(repair_environment['NPM_CONFIG_USERCONFIG']))

    def test_inspects_user_tool_and_credentials_without_contents(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            codex_path = os.path.join(bin_dir, 'codex')
            with open(codex_path, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\necho "codex 1.2.3"\n')
            os.chmod(codex_path, 0o755)
            os.makedirs(os.path.join(home, '.codex'))
            with open(os.path.join(home, '.codex', 'auth.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"secret":"never-print-this"}\n')

            result = inspect_agent_tools(['codex'], home=home)

        self.assertTrue(result[0]['installed'])
        self.assertEqual(result[0]['version'], 'codex 1.2.3')
        self.assertTrue(result[0]['credential'])
        self.assertTrue(result[0]['credential_healthy'])
        self.assertEqual(result[0]['credential_status']['status'], 'unknown')
        self.assertNotIn('never-print-this', str(result))

    def test_known_unhealthy_codex_credentials_fail_doctor(self):
        args = argparse.Namespace(
            agent_command='doctor',
            agent_doctor_host=None,
            agent_doctor_username=None,
            agent_doctor_tools=['codex'],
            agent_doctor_capabilities=None,
            ssh_key=None,
            fix=False,
            json=True,
        )
        with patch('lib.agent_cli.inspect_agent_tools', return_value=[
            {
                'tool': 'codex',
                'installed': True,
                'path': '/home/agent/.local/bin/codex',
                'version': 'codex 1.2.3',
                'credential': True,
                'credential_healthy': False,
                'credential_status': {'status': 'refresh_required'},
            }
        ]):
            self.assertEqual(run_agent_command(args), 1)

    def test_explicit_missing_tool_is_unhealthy(self):
        args = argparse.Namespace(
            agent_command='doctor',
            agent_doctor_tools=['codex'],
            json=True,
        )
        with patch('lib.agent_cli.inspect_agent_tools', return_value=[
            {
                'tool': 'codex',
                'installed': False,
                'path': None,
                'version': None,
                'credential': None,
            }
        ]):
            self.assertEqual(run_agent_command(args), 1)

    def test_parser_accepts_remote_doctor_target(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest='command')
        add_agent_subparser(subparsers)

        args = parser.parse_args([
            'agent', 'doctor', 'vm.example', 'agent',
            '--tool', 'codex', '--capability', 't3code', '--fix', '--json',
        ])

        self.assertEqual(args.agent_doctor_host, 'vm.example')
        self.assertEqual(args.agent_doctor_username, 'agent')
        self.assertEqual(args.agent_doctor_tools, ['codex'])
        self.assertTrue(args.fix)

    def test_remote_doctor_runs_target_copy_with_selected_checks(self):
        args = argparse.Namespace(
            agent_command='doctor',
            agent_doctor_host='vm.example',
            agent_doctor_username='agent',
            agent_doctor_tools=['codex'],
            agent_doctor_capabilities=['browser'],
            ssh_key=None,
            json=True,
        )
        completed = argparse.Namespace(returncode=0)
        with (
            patch('lib.agent_cli.build_ssh_command', return_value=['ssh']) as builder,
            patch('lib.agent_cli.subprocess.run', return_value=completed),
        ):
            self.assertEqual(run_agent_command(args), 0)

        self.assertEqual(
            builder.call_args.kwargs['remote_command'],
            'python3 /opt/infra_tools/infra_tools.py agent doctor '
            '--tool codex --capability browser --json',
        )

    def test_remote_doctor_requires_complete_target(self):
        args = argparse.Namespace(
            agent_command='doctor',
            agent_doctor_host='vm.example',
            agent_doctor_username=None,
            agent_doctor_tools=None,
            agent_doctor_capabilities=None,
            ssh_key=None,
            json=False,
        )
        with patch('lib.agent_cli.subprocess.run') as run_command:
            self.assertEqual(run_agent_command(args), 1)
        run_command.assert_not_called()


class TestAgentCredentialRotation(unittest.TestCase):
    def test_parser_exposes_remote_auth_operations(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)

        args = parser.parse_args([
            "agent", "auth", "set", "vm.example", "agent",
            "--tool", "codex", "--file", "/run/secrets/codex.json",
        ])
        self.assertEqual(args.agent_auth_command, "set")
        self.assertEqual(args.agent_auth_tool, "codex")
        self.assertEqual(args.agent_auth_file, "/run/secrets/codex.json")

    def test_parser_exposes_remote_web_pairing(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)

        args = parser.parse_args([
            "agent", "web", "pair", "vm.example", "agent", "--key", "/run/id_ed25519",
        ])
        self.assertEqual(args.agent_web_command, "pair")
        self.assertEqual(args.agent_web_host, "vm.example")
        self.assertEqual(args.agent_web_username, "agent")
        self.assertEqual(args.ssh_key, "/run/id_ed25519")

    def test_remote_web_pairing_runs_target_helper(self):
        args = argparse.Namespace(
            agent_command="web",
            agent_web_command="pair",
            agent_web_host="vm.example",
            agent_web_username="agent",
            ssh_key="/run/id_ed25519",
        )
        completed = argparse.Namespace(returncode=0)
        with patch("lib.agent_cli.subprocess.run", return_value=completed) as run_command:
            self.assertEqual(run_agent_command(args), 0)

        command = run_command.call_args.args[0]
        self.assertIn("-i", command)
        self.assertIn("/run/id_ed25519", command)
        self.assertIn("exec ~/.local/bin/t3code-pair", command)

    def test_set_filters_github_hosts_and_does_not_send_secret_in_command(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "hosts.yml")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "github.com:\n"
                    "  oauth_token: selected-token\n"
                    "gitlab.com:\n"
                    "  oauth_token: unrelated-token\n"
                )
            os.chmod(source, 0o600)
            with patch("lib.agent_auth._run_remote_script") as remote:
                remote.return_value = type(
                    "Completed",
                    (),
                    {"returncode": 0, "stdout": "{}", "stderr": ""},
                )()
                result = set_agent_credential(
                    host="vm.example",
                    username="agent",
                    tool="gh",
                    ssh_key=None,
                    source=source,
                    use_active=False,
                )

            self.assertEqual(result, 0)
            payload = remote.call_args.args[4]
            self.assertIn(b"selected-token", payload)
            self.assertNotIn(b"unrelated-token", payload)
            self.assertNotIn(b"selected-token", remote.call_args.args[3].encode())

    def test_set_script_rejects_non_regular_remote_destination(self):
        with tempfile.TemporaryDirectory() as home:
            destination = os.path.join(home, ".codex", "auth.json")
            os.makedirs(destination)

            with patch.dict(os.environ, {"HOME": home}):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "unsafe credential destination",
                ):
                    exec(_remote_set_script("codex", "github.com"), {})

    def test_active_github_auth_reads_keyring_token_through_gh(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "hosts.yml")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write("github.com:\n  user: octocat\n")
            os.chmod(source, 0o600)
            completed = type(
                "Completed",
                (),
                {"returncode": 0, "stdout": "active-token\n", "stderr": ""},
            )()
            with (
                patch("lib.agent_auth._active_source_path", return_value=source),
                patch("lib.setup_common.shutil.which", return_value="/usr/bin/gh"),
                patch("lib.setup_common.subprocess.run", return_value=completed) as run,
                patch("lib.agent_auth._run_remote_script") as remote,
            ):
                remote.return_value = type(
                    "Completed",
                    (),
                    {"returncode": 0, "stdout": "{}", "stderr": ""},
                )()
                result = set_agent_credential(
                    host="vm.example",
                    username="agent",
                    tool="gh",
                    ssh_key=None,
                    source=None,
                    use_active=True,
                )

            self.assertEqual(result, 0)
            self.assertIn(b"active-token", remote.call_args.args[4])
            run.assert_called_once_with(
                ["/usr/bin/gh", "auth", "token", "--hostname", "github.com"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )

    def test_set_rejects_multiple_credential_sources(self):
        with self.assertRaisesRegex(ValueError, "choose exactly one"):
            set_agent_credential(
                host="vm.example",
                username="agent",
                tool="codex",
                ssh_key=None,
                source="/run/secrets/codex.json",
                use_active=True,
            )

        with self.assertRaisesRegex(ValueError, "choose exactly one"):
            set_agent_credential(
                host="vm.example",
                username="agent",
                tool="gh",
                ssh_key=None,
                source="/run/secrets/hosts.yml",
                use_active=False,
                token="token-value",
            )

    def test_status_reports_non_secret_remote_result(self):
        with patch("lib.agent_auth._run_remote_script") as remote:
            remote.return_value = type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps({
                        "tool": "codex",
                        "installed": True,
                        "path": "/home/agent/.local/bin/codex",
                        "credential": {
                            "path": "/home/agent/.codex/auth.json",
                            "present": True,
                            "mode": "0o600",
                        },
                        "authentication": None,
                    }),
                    "stderr": "",
                },
            )()
            results = get_agent_auth_status(
                host="vm.example",
                username="agent",
                tools=["codex"],
                ssh_key=None,
            )

        self.assertTrue(results[0]["credential"]["present"])
        self.assertNotIn("secret", json.dumps(results))


class TestAgentUpdate(unittest.TestCase):
    @staticmethod
    def _write_tool(path: str, version: str, *, healthy: bool = True) -> None:
        with open(path, 'w', encoding='utf-8') as file_obj:
            file_obj.write(
                '#!/bin/sh\n'
                f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n'
                f'if [ "$1" = "--help" ]; then echo "help"; exit {0 if healthy else 1}; fi\n'
                'exit 0\n'
            )
        os.chmod(path, 0o755)

    def test_dry_run_reports_method_without_updating_or_writing_state(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            codex_path = os.path.join(bin_dir, 'codex')
            self._write_tool(codex_path, 'codex 1.0.0')

            with patch('lib.agent_cli._invoke_agent_update') as updater:
                result = update_agent_tools(['codex'], home=home, dry_run=True)

            updater.assert_not_called()
            self.assertEqual(result[0]['status'], 'planned')
            self.assertEqual(result[0]['before_version'], 'codex 1.0.0')
            self.assertFalse(os.path.exists(os.path.join(
                home,
                '.local',
                'state',
                'infra_tools',
                'agent-tools.json',
            )))

    def test_successful_update_records_verified_versions_and_private_state(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            claude_path = os.path.join(bin_dir, 'claude')
            self._write_tool(claude_path, 'claude 1.0.0')

            def update(_tool, path, _home):
                self._write_tool(path, 'claude 2.0.0')
                return {
                    'returncode': 0,
                    'method': 'claude update',
                    'installer_sha256': None,
                }

            with patch('lib.agent_cli._invoke_agent_update', side_effect=update):
                result = update_agent_tools(['claude'], home=home)

            state_path = os.path.join(
                home,
                '.local',
                'state',
                'infra_tools',
                'agent-tools.json',
            )
            with open(state_path, encoding='utf-8') as file_obj:
                state = json.load(file_obj)
            self.assertEqual(result[0]['status'], 'updated')
            self.assertEqual(result[0]['after_version'], 'claude 2.0.0')
            self.assertEqual(state['tools']['claude']['status'], 'updated')
            self.assertEqual(os.stat(state_path).st_mode & 0o777, 0o600)

    def test_interrupted_update_leaves_visible_in_progress_state(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            codex_path = os.path.join(bin_dir, 'codex')
            self._write_tool(codex_path, 'codex 1.0.0')

            with patch('lib.agent_cli._invoke_agent_update', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    update_agent_tools(['codex'], home=home)

            state_path = os.path.join(
                home,
                '.local',
                'state',
                'infra_tools',
                'agent-tools.json',
            )
            with open(state_path, encoding='utf-8') as file_obj:
                state = json.load(file_obj)
            self.assertEqual(state['tools']['codex']['status'], 'in_progress')

    def test_failed_post_update_smoke_test_restores_previous_executable(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            os.makedirs(bin_dir)
            opencode_path = os.path.join(bin_dir, 'opencode')
            self._write_tool(opencode_path, 'opencode 1.0.0')

            def broken_update(_tool, path, _home):
                self._write_tool(path, 'opencode 2.0.0', healthy=False)
                return {
                    'returncode': 0,
                    'method': 'opencode upgrade',
                    'installer_sha256': None,
                }

            with patch('lib.agent_cli._invoke_agent_update', side_effect=broken_update):
                result = update_agent_tools(['opencode'], home=home)

            self.assertEqual(result[0]['status'], 'failed')
            self.assertEqual(result[0]['failure'], 'post_update_verification')
            self.assertTrue(result[0]['rollback'])
            self.assertEqual(result[0]['after_version'], 'opencode 1.0.0')
            self.assertTrue(os.path.islink(opencode_path))

    def test_system_managed_executable_is_not_mutated(self):
        with tempfile.TemporaryDirectory() as home:
            with (
                patch('lib.agent_cli._tool_path', return_value='/usr/bin/claude'),
                patch('lib.agent_cli._tool_version', return_value='claude 1.0.0'),
                patch('lib.agent_cli._invoke_agent_update') as updater,
            ):
                result = update_agent_tools(['claude'], home=home)

            updater.assert_not_called()
            self.assertEqual(result[0]['failure'], 'not_user_managed')

    def test_corrupt_state_stops_before_mutating_tools(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, '.local', 'bin')
            state_dir = os.path.join(home, '.local', 'state', 'infra_tools')
            os.makedirs(bin_dir)
            os.makedirs(state_dir)
            self._write_tool(os.path.join(bin_dir, 'codex'), 'codex 1.0.0')
            with open(os.path.join(state_dir, 'agent-tools.json'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('not-json')

            with patch('lib.agent_cli._invoke_agent_update') as updater:
                with self.assertRaisesRegex(RuntimeError, 'Cannot read agent update state'):
                    update_agent_tools(['codex'], home=home)

            updater.assert_not_called()

    def test_codex_installer_uses_shared_provenance_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('lib.agent_cli.download_installer', return_value=(
                'installer.sh', 'codex.json', {'observed_sha256': 'digest'},
            )) as download:
                self.assertEqual(_download_codex_installer(directory), ('installer.sh', 'digest'))
            download.assert_called_once_with('codex', directory)

    def test_native_updaters_use_vendor_subcommands_without_a_shell(self):
        completed = type('Completed', (), {'returncode': 0})()
        cases = ('claude', 'opencode')
        with tempfile.TemporaryDirectory() as home:
            for tool in cases:
                with self.subTest(tool=tool):
                    path = os.path.join(
                        home,
                        '.local' if tool == 'claude' else '.opencode',
                        'bin',
                        tool,
                    )
                    expected = [path, 'update' if tool == 'claude' else 'upgrade']
                    with patch('lib.agent_cli.run_command', return_value=completed) as runner:
                        result = _invoke_agent_update(tool, expected[0], home)
                    self.assertEqual(runner.call_args.args[0], expected)
                    self.assertNotIn('shell', runner.call_args.kwargs)
                    self.assertEqual(result['returncode'], 0)

    def test_updater_environment_is_rooted_in_target_home(self):
        completed = type('Completed', (), {'returncode': 0})()
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(
                os.environ,
                {
                    'HOME': '/home/loren',
                    'PWD': '/home/loren',
                    'OLDPWD': '/home/loren/old',
                    'PATH': '/home/loren/.local/bin:/usr/bin',
                    'XDG_CONFIG_HOME': '/home/loren/.config',
                    'CODEX_HOME': '/home/loren/.codex',
                    'NPM_CONFIG_PREFIX': '/home/loren/.npm-global',
                },
            ), patch('lib.agent_cli.run_command', return_value=completed) as runner:
                _invoke_agent_update(
                    'claude',
                    os.path.join(home, '.local', 'bin', 'claude'),
                    home,
                )

        environment = runner.call_args.kwargs['env']
        self.assertEqual(environment['HOME'], home)
        self.assertEqual(environment['PWD'], home)
        self.assertEqual(environment['CODEX_HOME'], os.path.join(home, '.codex'))
        self.assertEqual(environment['XDG_CONFIG_HOME'], os.path.join(home, '.config'))
        self.assertNotIn('/home/loren', environment['PATH'])
        self.assertNotIn('OLDPWD', environment)
        self.assertNotIn('NPM_CONFIG_PREFIX', environment)
        self.assertEqual(runner.call_args.kwargs['cwd'], home)

    def test_native_updater_timeout_reports_bounded_failure(self):
        with tempfile.TemporaryDirectory() as home:
            with patch(
                'lib.agent_cli.run_command',
                side_effect=CommandTimeoutError(
                    'claude update',
                    600,
                ),
            ) as runner:
                result = _invoke_agent_update(
                    'claude',
                    os.path.join(home, '.local', 'bin', 'claude'),
                    home,
                )

        self.assertEqual(result['failure'], 'timeout')
        self.assertIsNone(result['returncode'])
        self.assertEqual(runner.call_args.kwargs['timeout'], 600)

    def test_tool_lookup_does_not_use_another_account_path(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as other_home:
            other_tool = os.path.join(other_home, 'codex')
            with open(other_tool, 'w', encoding='utf-8') as file_obj:
                file_obj.write('#!/bin/sh\n')
            os.chmod(other_tool, 0o755)

            with patch.dict(os.environ, {'PATH': other_home}, clear=False):
                self.assertIsNone(_tool_path('codex', home))

    def test_update_requires_the_user_owning_the_agent_home(self):
        with tempfile.TemporaryDirectory() as home:
            with patch('lib.agent_cli.os.geteuid', return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(RuntimeError, 'must run as'):
                    update_agent_tools(['codex'], home=home)

    def test_remote_update_runs_as_target_user_with_requested_policy(self):
        args = argparse.Namespace(
            agent_command='update',
            agent_update_host='vm.example',
            agent_update_username='agent',
            agent_update_tools=['codex'],
            ssh_key=None,
            dry_run=True,
            json=True,
        )
        completed = argparse.Namespace(returncode=0)
        with (
            patch('lib.agent_cli.build_ssh_command', return_value=['ssh']) as builder,
            patch('lib.agent_cli.subprocess.run', return_value=completed),
        ):
            self.assertEqual(run_agent_command(args), 0)

        self.assertEqual(
            builder.call_args.kwargs['remote_command'],
            'python3 /opt/infra_tools/infra_tools.py agent update '
            '--tool codex --dry-run --json',
        )

    def test_remote_update_forwards_tools_only_readiness(self):
        args = argparse.Namespace(
            agent_command='update',
            agent_update_host='vm.example',
            agent_update_username='agent',
            agent_update_tools=['codex'],
            ssh_key=None,
            dry_run=True,
            json=False,
            tools_only_readiness=True,
        )
        completed = argparse.Namespace(returncode=0)
        with (
            patch('lib.agent_cli.build_ssh_command', return_value=['ssh']) as builder,
            patch('lib.agent_cli.subprocess.run', return_value=completed),
        ):
            self.assertEqual(run_agent_command(args), 0)

        self.assertEqual(
            builder.call_args.kwargs['remote_command'],
            'python3 /opt/infra_tools/infra_tools.py agent update '
            '--tool codex --dry-run --tools-only-readiness',
        )


class TestAgentPayloadInstallation(unittest.TestCase):
    def test_github_credentials_make_private_config_directories_user_owned(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        account = type(
            'Account',
            (),
            {'pw_dir': '/home/agent', 'pw_uid': 1000, 'pw_gid': 1001},
        )()
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': '', 'stderr': ''},
        )()
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, 'home')
            source = os.path.join(directory, 'hosts.yml')
            os.makedirs(home)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('github.com:\n  oauth_token: secret\n')

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps.pwd.getpwnam', return_value=account),
                patch('common.agent_steps.run', return_value=completed) as run_command,
            ):
                self.assertTrue(_merge_github_credentials(config, source))

            destination = os.path.join(home, '.config', 'gh', 'hosts.yml')
            commands = [call.args[0] for call in run_command.call_args_list]
            self.assertEqual(
                commands,
                [
                    ['chown', '1000:1001', os.path.join(home, ".config")],
                    ['chown', '1000:1001', os.path.join(home, ".config", "gh")],
                    ['chown', '-R', '1000:1001', destination],
                ],
            )
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)

    def test_existing_github_host_credentials_are_retained(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, 'home')
            source = os.path.join(directory, 'hosts.yml')
            destination = os.path.join(home, '.config', 'gh', 'hosts.yml')
            os.makedirs(os.path.dirname(destination))
            target_content = (
                'github.com:\n  oauth_token: target-refreshed-token\n'
                'gitlab.com:\n  oauth_token: unrelated-token\n'
            )
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('github.com:\n  oauth_token: source-token\n')
            with open(destination, 'w', encoding='utf-8') as file_obj:
                file_obj.write(target_content)

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
            ):
                self.assertFalse(_merge_github_credentials(config, source))

            with open(destination, encoding='utf-8') as file_obj:
                self.assertEqual(file_obj.read(), target_content)

    def test_github_credentials_reject_broken_symlink_destination(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, 'home')
            source = os.path.join(directory, 'hosts.yml')
            destination = os.path.join(home, '.config', 'gh', 'hosts.yml')
            os.makedirs(os.path.dirname(destination))
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('github.com:\n  oauth_token: source-token\n')
            os.symlink(os.path.join(directory, 'missing'), destination)

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'unsafe GitHub credentials',
                ):
                    _merge_github_credentials(config, source)

            self.assertTrue(os.path.islink(destination))

    def test_github_credentials_reject_non_regular_destination(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, 'home')
            source = os.path.join(directory, 'hosts.yml')
            destination = os.path.join(home, '.config', 'gh', 'hosts.yml')
            os.makedirs(destination)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('github.com:\n  oauth_token: source-token\n')

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'unsafe GitHub credentials',
                ):
                    _merge_github_credentials(config, source)

    def test_existing_codex_credentials_are_retained_on_setup(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source-auth.json')
            home = os.path.join(directory, 'home')
            destination = os.path.join(home, '.codex', 'auth.json')
            os.makedirs(os.path.dirname(destination))
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"stale-controller-token"}\n')
            with open(destination, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"target-refreshed-token"}\n')

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
            ):
                copied = _copy_secret_file(
                    config,
                    source,
                    destination,
                    'Codex',
                    credential_tool='codex',
                )

            self.assertFalse(copied)
            with open(destination, encoding='utf-8') as file_obj:
                self.assertEqual(
                    file_obj.read(),
                    '{"token":"target-refreshed-token"}\n',
                )

    def test_outdated_codex_credentials_are_refreshed_on_setup(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source-auth.json')
            home = os.path.join(directory, 'home')
            destination = os.path.join(home, '.codex', 'auth.json')
            os.makedirs(os.path.dirname(destination))
            source_content = json.dumps(
                {
                    'auth_mode': 'chatgpt',
                    'last_refresh': '2100-01-01T00:00:00Z',
                    'tokens': {
                        'access_token': _test_jwt({'exp': 4133980800}),
                        'refresh_token': 'fresh-refresh-token',
                    },
                }
            ) + '\n'
            target_content = json.dumps(
                {
                    'auth_mode': 'chatgpt',
                    'last_refresh': '2020-01-01T00:00:00Z',
                    'tokens': {
                        'access_token': _test_jwt({'exp': 1577923200}),
                        'refresh_token': 'expired-refresh-token',
                    },
                }
            ) + '\n'
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write(source_content)
            with open(destination, 'w', encoding='utf-8') as file_obj:
                file_obj.write(target_content)

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
                patch('builtins.print') as print_output,
            ):
                copied = _copy_secret_file(
                    config,
                    source,
                    destination,
                    'Codex',
                    credential_tool='codex',
                )

            self.assertTrue(copied)
            with open(destination, encoding='utf-8') as file_obj:
                self.assertEqual(file_obj.read(), source_content)
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)
            self.assertEqual(
                [call.args[0] for call in print_output.call_args_list],
                ['  Refreshed outdated Codex credentials'],
            )

    def test_outdated_codex_source_does_not_replace_target(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source-auth.json')
            home = os.path.join(directory, 'home')
            destination = os.path.join(home, '.codex', 'auth.json')
            os.makedirs(os.path.dirname(destination))
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"outdated-source-token"}\n')
            target_content = '{"token":"outdated-target-token"}\n'
            with open(destination, 'w', encoding='utf-8') as file_obj:
                file_obj.write(target_content)

            outdated_metadata = {
                'status': 'refresh_required',
                'warnings': ['refresh_overdue'],
                'last_refresh': '2020-01-01T00:00:00Z',
            }
            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
                patch(
                    'common.agent_steps.inspect_codex_auth_file',
                    side_effect=[outdated_metadata, outdated_metadata],
                ),
            ):
                copied = _copy_secret_file(
                    config,
                    source,
                    destination,
                    'Codex',
                    credential_tool='codex',
                )

            self.assertFalse(copied)
            with open(destination, encoding='utf-8') as file_obj:
                self.assertEqual(file_obj.read(), target_content)

    def test_git_identity_fills_missing_fields_from_staged_controller_values(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            payload_dir = os.path.join(directory, 'payload')
            identity_path = os.path.join(
                payload_dir,
                'config',
                'git',
                'identity.json',
            )
            os.makedirs(os.path.dirname(identity_path))
            with open(identity_path, 'w', encoding='utf-8') as file_obj:
                json.dump({'name': 'Octo Cat', 'email': 'octo@example.test'}, file_obj)
            completed = type(
                'Completed',
                (),
                {'returncode': 0, 'stdout': '', 'stderr': ''},
            )()
            responses = [
                type(
                    'Completed',
                    (),
                    {'returncode': 1, 'stdout': '', 'stderr': ''},
                )(),
                type(
                    'Completed',
                    (),
                    {'returncode': 1, 'stdout': '', 'stderr': ''},
                )(),
                completed,
                completed,
            ]
            with (
                patch('common.agent_steps.REMOTE_AGENT_PAYLOAD_DIR', payload_dir),
                patch('common.agent_steps._user_home', return_value='/home/agent'),
                patch(
                    'common.agent_steps._run_as_login_user',
                    side_effect=responses,
                ) as run_as_user,
            ):
                _configure_git_identity(config)

        commands = [call.args[2] for call in run_as_user.call_args_list]
        self.assertEqual(
            commands,
            [
                'git config --get user.name',
                'git config --get user.email',
                "git config --global user.name 'Octo Cat'",
                'git config --global user.email octo@example.test',
            ],
        )

    def test_git_identity_preserves_existing_target_values(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        responses = [
            type(
                'Completed',
                (),
                {'returncode': 0, 'stdout': 'Target Name\n', 'stderr': ''},
            )(),
            type(
                'Completed',
                (),
                {
                    'returncode': 0,
                    'stdout': 'target@example.test\n',
                    'stderr': '',
                },
            )(),
        ]
        with (
            patch('common.agent_steps._user_home', return_value='/home/agent'),
            patch(
                'common.agent_steps._run_as_login_user',
                side_effect=responses,
            ) as run_as_user,
        ):
            _configure_git_identity(config)

        self.assertEqual(run_as_user.call_count, 2)

    def test_git_identity_falls_back_to_authenticated_github_account(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        completed = type(
            'Completed',
            (),
            {'returncode': 0, 'stdout': '', 'stderr': ''},
        )()
        responses = [
            type(
                'Completed',
                (),
                {'returncode': 1, 'stdout': '', 'stderr': ''},
            )(),
            type(
                'Completed',
                (),
                {'returncode': 1, 'stdout': '', 'stderr': ''},
            )(),
            type(
                'Completed',
                (),
                {
                    'returncode': 0,
                    'stdout': json.dumps(
                        {'login': 'octocat', 'id': 1234, 'name': None, 'email': None}
                    ),
                    'stderr': '',
                },
            )(),
            completed,
            completed,
        ]
        with tempfile.TemporaryDirectory() as payload_dir:
            with (
                patch('common.agent_steps.REMOTE_AGENT_PAYLOAD_DIR', payload_dir),
                patch('common.agent_steps._user_home', return_value='/home/agent'),
                patch(
                    'common.agent_steps._run_as_login_user',
                    side_effect=responses,
                ) as run_as_user,
            ):
                _configure_git_identity(config)

        commands = [call.args[2] for call in run_as_user.call_args_list]
        self.assertIn('gh api user --hostname github.com', commands[2])
        self.assertEqual(commands[3], 'git config --global user.name octocat')
        self.assertEqual(
            commands[4],
            'git config --global user.email 1234+octocat@users.noreply.github.com',
        )

    def test_existing_codex_runtime_symlinks_do_not_block_secret_copy(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
            install_codex=True,
            copy_agent_keys=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'auth.json')
            home = os.path.join(directory, 'home')
            runtime_dir = os.path.join(home, '.codex', 'tmp', 'arg0')
            os.makedirs(runtime_dir)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')
            os.symlink('/bin/true', os.path.join(runtime_dir, 'codex-linux-sandbox'))

            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
            ):
                _copy_secret_file(
                    config,
                    source,
                    os.path.join(home, '.codex', 'auth.json'),
                    'Codex',
                )

            self.assertTrue(os.path.isfile(os.path.join(home, '.codex', 'auth.json')))
            self.assertTrue(os.path.islink(os.path.join(runtime_dir, 'codex-linux-sandbox')))

    def test_payload_directory_rejects_symlinks_at_the_written_path(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source')
            home = os.path.join(directory, 'home')
            destination = os.path.join(home, '.codex')
            os.makedirs(source)
            os.makedirs(home)
            os.symlink(directory, destination)

            with self.assertRaisesRegex(RuntimeError, 'symlinked agent destination'):
                _copy_payload_directory(config, source, destination, 'Codex')

    def test_payload_directory_rejects_nested_destination_symlink(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'source')
            home = os.path.join(directory, 'home')
            destination = os.path.join(home, '.codex')
            os.makedirs(source)
            os.makedirs(destination)
            with open(os.path.join(source, 'config.toml'), 'w', encoding='utf-8') as file_obj:
                file_obj.write('model = "test"\n')
            os.symlink(os.path.join(directory, 'outside'), os.path.join(destination, 'config.toml'))

            with patch('common.agent_steps._user_home', return_value=home):
                with self.assertRaisesRegex(RuntimeError, 'symlinked agent destination'):
                    _copy_payload_directory(config, source, destination, 'Codex')

    def test_copied_credentials_are_removed_from_uploaded_payload(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
            install_codex=True,
            copy_agent_keys=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            payload_dir = os.path.join(directory, 'payload')
            home = os.path.join(directory, 'home')
            source = os.path.join(payload_dir, 'secrets', 'codex', 'auth.json')
            os.makedirs(os.path.dirname(source))
            os.makedirs(home)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('{"token":"secret"}\n')

            with (
                patch('common.agent_steps.REMOTE_AGENT_PAYLOAD_DIR', payload_dir),
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_user_directory_chain'),
                patch('common.agent_steps._chown_path'),
            ):
                copy_agent_tooling_payload(config)

            destination = os.path.join(home, '.codex', 'auth.json')
            self.assertTrue(os.path.isfile(destination))
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(payload_dir))

    def test_secret_copy_rejects_symlinked_destination(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'auth.json')
            outside = os.path.join(directory, 'outside.json')
            home = os.path.join(directory, 'home')
            os.makedirs(home)
            with open(source, 'w', encoding='utf-8') as file_obj:
                file_obj.write('secret')
            with open(outside, 'w', encoding='utf-8') as file_obj:
                file_obj.write('untouched')
            os.symlink(directory, os.path.join(home, '.codex'))

            with self.assertRaisesRegex(RuntimeError, 'symlinked agent destination'):
                _copy_secret_file(
                    config,
                    source,
                    os.path.join(home, '.codex', 'auth.json'),
                    'Codex',
                )

            with open(outside, encoding='utf-8') as file_obj:
                self.assertEqual(file_obj.read(), 'untouched')

    def test_repository_is_cloned_on_target_without_controller_cache(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
            agent_repos=['https://gitlab.com/user/repo.git'],
        )
        with tempfile.TemporaryDirectory() as directory:
            home = os.path.join(directory, 'home')
            os.makedirs(home)
            with (
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._chown_path'),
                patch('common.agent_steps._run_as_login_user') as run_as_user,
            ):
                run_as_user.return_value = type('Completed', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
                clone_agent_repositories(config)

            command = run_as_user.call_args.args[2]
            self.assertIn('GIT_TERMINAL_PROMPT=0', command)
            self.assertIn('https://gitlab.com/user/repo.git', command)
            self.assertFalse(os.path.exists(os.path.join(directory, 'cache')))

    def test_repository_uses_declared_data_disk_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = os.path.join(directory, 'agent-workspace')
            config = SetupConfig(
                host='host',
                username='agent',
                system_type='server_dev',
                agent_repos=['https://example.com/team/repo.git'],
                agent_workspace=workspace,
                storage_mounts=[['agent-data', workspace]],
            )
            with (
                patch(
                    'common.storage_steps.assert_declared_storage_mount'
                ) as assert_mount,
                patch('common.agent_steps._user_home', return_value=directory),
                patch('common.agent_steps._chown_path'),
                patch('common.agent_steps._run_as_login_user') as run_as_user,
            ):
                run_as_user.return_value = type(
                    'Completed',
                    (),
                    {'returncode': 0, 'stdout': '', 'stderr': ''},
                )()
                clone_agent_repositories(config)

            assert_mount.assert_called_once_with(config, workspace)
            command = run_as_user.call_args.args[2]
            self.assertIn(os.path.join(workspace, 'repo'), command)

    def test_payload_is_removed_when_copying_fails(self):
        config = SetupConfig(
            host='host',
            username='agent',
            system_type='server_dev',
            install_codex=True,
            copy_agent_keys=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            payload_dir = os.path.join(directory, 'payload')
            home = os.path.join(directory, 'home')
            os.makedirs(payload_dir)
            os.makedirs(home)
            os.makedirs(os.path.join(payload_dir, 'secrets', 'codex'))
            with open(
                os.path.join(payload_dir, 'secrets', 'codex', 'auth.json'),
                'w',
                encoding='utf-8',
            ) as file_obj:
                file_obj.write('{}')
            with (
                patch('common.agent_steps.REMOTE_AGENT_PAYLOAD_DIR', payload_dir),
                patch('common.agent_steps._user_home', return_value=home),
                patch('common.agent_steps._copy_secret_file', side_effect=OSError('copy failed')),
            ):
                with self.assertRaisesRegex(OSError, 'copy failed'):
                    copy_agent_tooling_payload(config)

            self.assertFalse(os.path.exists(payload_dir))


if __name__ == '__main__':
    unittest.main()
