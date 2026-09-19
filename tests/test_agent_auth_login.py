"""Target-owned login, transport redaction, and setup authorization tests."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from common import agent_steps
from common.service_tools import codex_auth_login as target
from lib import agent_login, setup_common
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.validation import validate_agent_git_settings


class TargetLoginTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.auth_dir = self.home / '.codex'
        self.auth_dir.mkdir(mode=0o700)
        self.auth = self.auth_dir / 'auth.json'
        self.auth.write_bytes(b'old-credential')
        self.auth.chmod(0o600)
        self.config = self.auth_dir / 'config.toml'
        self.config.write_text('model = "example"\n')
        self.config.chmod(0o644)
        self.events = []
        self.process = MagicMock()
        self.process.poll.return_value = None
        self.process.wait.return_value = 0
        self.popen = self.stack.enter_context(patch.object(target.subprocess, 'Popen', return_value=self.process))
        self.stack.enter_context(patch.object(target.shutil, 'which', return_value='/fake/codex'))
        self.protocol = self.stack.enter_context(patch.object(target, 'Protocol')).return_value
        self.protocol.request.side_effect = self.respond
        self.protocol.receive.return_value = {'params': {'success': True}}

    def respond(self, identifier, method, params):
        staging = Path(self.popen.call_args.kwargs['env']['CODEX_HOME'])
        if method == 'config/value/write':
            self.assertEqual(params['value'], 'file')
            with (staging / 'config.toml').open('a') as stream:
                stream.write('cli_auth_credentials_store = "file"\n')
        if method == 'account/login/start':
            payload = ({'OPENAI_API_KEY': params['apiKey']} if params['type'] == 'apiKey'
                       else {'tokens': {'access_token': 'fake-access', 'refresh_token': 'fake-refresh'}})
            credential = staging / 'auth.json'
            credential.write_text(json.dumps(payload))
            credential.chmod(0o600)
            return {'type': params['type'], 'loginId': 'example',
                    'verificationUrl': target.DEVICE_URL, 'userCode': 'ABCD-1234'}
        return {}

    def login(self, method='subscription', key=None):
        target.login(str(self.home), method, key, self.events.append)

    def test_subscription_installs_private_target_owned_session_and_preserves_config(self):
        self.login()
        self.assertEqual(json.loads(self.auth.read_text())['tokens']['refresh_token'], 'fake-refresh')
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o600)
        self.assertIn('model = "example"', self.config.read_text())
        self.assertIn('cli_auth_credentials_store = "file"', self.config.read_text())
        self.assertEqual(self.events[0]['event'], 'device')
        self.assertEqual(self.events[-1], {'event': 'complete', 'method': 'subscription'})
        self.assertNotIn('fake-refresh', json.dumps(self.events))
        self.assertEqual(list(self.auth_dir.glob('.login-*')), [])
        self.process.terminate.assert_called_once()

    def test_api_key_uses_stdin_rpc_not_argv_or_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'ambient-secret', 'CODEX_API_KEY': 'ambient-secret'}):
            self.login('api-key', 'fixture-key')
        self.assertEqual(json.loads(self.auth.read_text()), {'OPENAI_API_KEY': 'fixture-key'})
        self.assertNotIn('fixture-key', str(self.popen.call_args))
        self.assertNotIn('ambient-secret', str(self.popen.call_args))
        self.assertEqual(self.events, [{'event': 'complete', 'method': 'api-key'}])

    def test_rejected_or_cancelled_login_preserves_live_auth_and_config(self):
        for failure in (target.LoginError('authorization_failed'), KeyboardInterrupt(), target.LoginError('authorization_timed_out')):
            with self.subTest(failure=type(failure).__name__):
                self.protocol.receive.side_effect = failure
                with self.assertRaises(type(failure)):
                    self.login()
                self.assertEqual(self.auth.read_bytes(), b'old-credential')
                self.assertEqual(self.config.read_text(), 'model = "example"\n')
                self.assertEqual(list(self.auth_dir.glob('.login-*')), [])

    def test_failed_vendor_completion_does_not_install_written_credentials(self):
        self.protocol.receive.return_value = {'params': {'success': False, 'error': 'secret-provider-text'}}
        with self.assertRaisesRegex(target.LoginError, '^authorization_failed$'):
            self.login()
        self.assertEqual(self.auth.read_bytes(), b'old-credential')

    def test_rejects_changed_target_during_authorization(self):
        def completed(_match):
            self.auth.write_bytes(b'concurrent-refresh')
            return {'params': {'success': True}}
        self.protocol.receive.side_effect = completed
        with self.assertRaisesRegex(target.LoginError, 'target_credentials_changed'):
            self.login()
        self.assertEqual(self.auth.read_bytes(), b'concurrent-refresh')

    def test_parallel_login_is_rejected_before_launch(self):
        with patch.object(target.fcntl, 'flock', side_effect=BlockingIOError):
            with self.assertRaisesRegex(target.LoginError, 'another_login_is_running'):
                self.login()
        self.popen.assert_not_called()

    def test_missing_vendor_payload_does_not_replace_auth(self):
        original = self.respond
        def response(identifier, method, params):
            result = original(identifier, method, params)
            if method == 'account/login/start':
                staging = Path(self.popen.call_args.kwargs['env']['CODEX_HOME'])
                (staging / 'auth.json').unlink()
            return result
        self.protocol.request.side_effect = response
        with self.assertRaisesRegex(target.LoginError, 'login_did_not_save_credentials'):
            self.login()
        self.assertEqual(self.auth.read_bytes(), b'old-credential')

    def test_rejects_symlink_and_public_credential_before_launch(self):
        self.auth.chmod(0o644)
        with self.assertRaises(target.LoginError):
            self.login()
        self.auth.unlink()
        self.auth.symlink_to(self.config)
        with self.assertRaises(OSError):
            self.login()
        self.popen.assert_not_called()

    def test_rejects_untrusted_authorization_url(self):
        original = self.respond
        def response(identifier, method, params):
            result = original(identifier, method, params)
            if method == 'account/login/start':
                result['verificationUrl'] = 'https://example.invalid/login'
            return result
        self.protocol.request.side_effect = response
        with self.assertRaisesRegex(target.LoginError, 'invalid_device'):
            self.login()
        self.assertEqual(self.events, [])
        self.assertEqual(self.auth.read_bytes(), b'old-credential')


class HelperShutdownTests(unittest.TestCase):
    def run_helper(self, outcome):
        # Exercise actual interpreter shutdown with SSH-like pipes, while
        # mocking login so no vendor process or account is touched.
        script = '''
import signal
import sys
import time
from unittest.mock import patch
from common.service_tools import codex_auth_login as target
outcome = sys.argv.pop()
def cancel(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, cancel)
def fake_login(home, method, key, emit):
    emit({"event": "started"})
    time.sleep(10 if outcome == "disconnect" else 0.2)
    if outcome == "error":
        raise target.LoginError("authorization_failed")
    emit({"event": "complete", "method": method})
with patch.object(target, "login", side_effect=fake_login):
    raise SystemExit(target.main())
'''
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, '-u', '-c', script, outcome],
                cwd=directory, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                process.stdin.write(b'{"method":"subscription"}\n')
                process.stdin.flush()
                protocol = target.Protocol(process, time.monotonic() + 5)
                self.assertEqual(protocol.receive(lambda event: True), {'event': 'started'})
                if outcome == 'disconnect':
                    process.stdin.close()
                # Keep stdin open through exit on success/failure, as SSH does.
                self.assertEqual(process.wait(timeout=5), 0 if outcome == 'success' else 3)
                event = protocol.receive(lambda event: True)
                self.assertEqual(event['event'], 'complete' if outcome == 'success' else 'error')
                if outcome == 'disconnect':
                    self.assertEqual(event['reason'], 'authorization_cancelled')
                self.assertEqual(process.stderr.read(), b'')
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()

    def test_success_exits_with_controller_stdin_open(self):
        self.run_helper('success')

    def test_rejection_exits_with_controller_stdin_open(self):
        self.run_helper('error')

    def test_controller_disconnect_cancels_and_exits(self):
        self.run_helper('disconnect')


class ProtocolTests(unittest.TestCase):
    def test_retains_completion_that_precedes_rpc_reply(self):
        process = MagicMock()
        protocol = target.Protocol(process, time.monotonic() + 1)
        protocol.buffer.extend(b'{"method":"account/login/completed","params":{"loginId":"a","success":true}}\n{"id":2,"result":{"type":"apiKey"}}\n')
        self.assertEqual(protocol.request(2, 'account/login/start', {})['type'], 'apiKey')
        self.assertTrue(protocol.receive(lambda event: event.get('method') == 'account/login/completed')['params']['success'])

    def test_error_and_malformed_responses_are_redacted(self):
        for line in (b'not-json-secret\n', b'{"id":2,"error":{"message":"secret"}}\n'):
            protocol = target.Protocol(MagicMock(), time.monotonic() + 1)
            protocol.buffer.extend(line)
            with self.assertRaises(target.LoginError) as error:
                protocol.request(2, 'account/login/start', {})
            self.assertNotIn('secret', str(error.exception))

    def test_deadline_is_bounded(self):
        protocol = target.Protocol(MagicMock(), time.monotonic() - 1)
        with self.assertRaisesRegex(target.LoginError, 'authorization_timed_out'):
            protocol.receive(lambda event: True)


class ControllerLoginTests(unittest.TestCase):
    def parser(self):
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest='command'))
        return parser

    def test_defaults_to_subscription_and_dispatches(self):
        args = self.parser().parse_args(['agent', 'auth', 'login', 'vm.example', 'agent'])
        self.assertEqual(args.method, 'subscription')
        with patch.object(agent_login, 'run_agent_auth_login', return_value=3) as login:
            self.assertEqual(run_agent_command(args), 3)
        login.assert_called_once_with(args)

    def test_pull_has_been_removed(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser().parse_args(['agent', 'auth', 'pull', 'vm.example', 'agent'])

    def test_api_key_stdin_requires_explicit_mode_and_prints_billing_notice(self):
        args = self.parser().parse_args(['agent', 'auth', 'login', 'vm.example', 'agent', '--api-key-stdin'])
        with self.assertRaisesRegex(ValueError, '--method api-key'):
            agent_login.run_agent_auth_login(args)
        args.method = 'api-key'
        output = io.StringIO()
        with patch.object(agent_login.sys, 'stdin', io.StringIO('fixture-key\n')), redirect_stderr(output), patch.object(agent_login, 'login_agent', return_value=0) as login:
            self.assertEqual(agent_login.run_agent_auth_login(args), 0)
        self.assertEqual(login.call_args.kwargs['api_key'], 'fixture-key')
        self.assertIn('separately billed', output.getvalue())
        self.assertNotIn('fixture-key', output.getvalue())

    def test_controller_streams_only_sanitized_events_and_optional_browser(self):
        process = MagicMock()
        process.stdin = io.BytesIO()
        process.wait.return_value = 0
        process.poll.return_value = 0
        events = [{'event': 'device', 'url': target.DEVICE_URL, 'code': 'TEST-1234'},
                  {'event': 'complete', 'method': 'subscription'}]
        with patch.object(agent_login.subprocess, 'Popen', return_value=process), patch.object(agent_login, 'build_ssh_command', return_value=['ssh', 'fixture']) as ssh, patch.object(agent_login, 'Protocol') as protocol, patch.object(agent_login.webbrowser, 'open', return_value=False) as browser, redirect_stdout(io.StringIO()) as output:
            protocol.return_value.receive.side_effect = events
            self.assertEqual(agent_login.login_agent(host='vm.example', username='agent', open_browser=True), 0)
        browser.assert_called_once_with(target.DEVICE_URL)
        self.assertIn('another device', output.getvalue())
        self.assertIn('python3 -u -c', ssh.call_args.kwargs['remote_command'])

    def test_api_key_file_must_be_private_and_oversize_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('fixture-key')
            key.chmod(0o644)
            args = self.parser().parse_args(['agent', 'auth', 'login', 'vm.example', 'agent', '--method', 'api-key', '--api-key-file', str(key)])
            with redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, 'private'):
                agent_login.run_agent_auth_login(args)
            key.chmod(0o600)
            key.write_text('fixture-key' + ' ' * 16384)
            with redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, 'Invalid API-key'):
                agent_login.run_agent_auth_login(args)

    def test_remote_error_text_is_not_echoed(self):
        process = MagicMock()
        process.poll.return_value = 0
        with patch.object(agent_login.subprocess, 'Popen', return_value=process), patch.object(agent_login, 'build_ssh_command', return_value=['ssh']), patch.object(agent_login, 'Protocol') as protocol, redirect_stderr(io.StringIO()) as output:
            protocol.return_value.receive.return_value = {'event': 'error', 'reason': 'private token = fixture'}
            self.assertEqual(agent_login.login_agent(host='vm.example', username='agent'), 3)
        self.assertNotIn('fixture', output.getvalue())


class SetupLoginTests(unittest.TestCase):
    def test_active_coding_credentials_are_rejected_before_read_or_upload(self):
        from lib import agent_auth
        parser = create_setup_argument_parser('test')
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['vm.example', 'agent', '--agent-auth', 'active'])
        for tool in ('codex', 'claude', 'opencode'):
            with self.subTest(tool=tool), patch.object(agent_auth, '_read_credential') as read, patch.object(agent_auth, '_run_remote_script') as remote:
                with self.assertRaisesRegex(ValueError, 'only for gh'):
                    agent_auth.set_agent_credential(host='vm.example', username='agent', tool=tool,
                                                   ssh_key=None, source=None, use_active=True)
                read.assert_not_called()
                remote.assert_not_called()

    def test_provider_file_overrides_do_not_disable_other_provider_defaults(self):
        parser = create_setup_argument_parser('test')
        for tool in ('gh', 'claude', 'opencode', 'codex'):
            with self.subTest(tool=tool):
                args = parser.parse_args(['vm.example', 'agent', '--agent-tool', tool,
                                          '--agent-auth-file', tool, '/fake/credential'])
                config = SetupConfig.from_args(args, 'agent_code_vm')
                validate_agent_git_settings(config)
                self.assertEqual(config.agent_auth_source, None if tool == 'codex' else 'login')
                self.assertEqual(config.git_auth_source, None if tool == 'gh' else 'active')

    def test_explicit_login_combines_with_other_provider_files_only(self):
        parser = create_setup_argument_parser('test')
        for tool in ('claude', 'codex'):
            args = parser.parse_args(['vm.example', 'agent', '--agent-tool', tool,
                                      '--agent-auth', 'login', '--agent-auth-file', tool, '/fake/credential'])
            config = SetupConfig.from_args(args, 'agent_vm')
            self.assertEqual(config.agent_auth_source, 'login')
            if tool == 'codex':
                with self.assertRaisesRegex(ValueError, 'not both'):
                    validate_agent_git_settings(config)
            else:
                validate_agent_git_settings(config)

    def test_codex_opt_outs_preserve_github_default(self):
        parser = create_setup_argument_parser('test')
        for options in (['--agent-auth', 'none'], ['--no-agent-tool', 'codex', '--agent-tool', 'claude']):
            config = SetupConfig.from_args(parser.parse_args(['vm.example', 'agent', *options]), 'agent_code_vm')
            validate_agent_git_settings(config)
            self.assertIsNone(config.agent_auth_source)
            self.assertEqual(config.git_auth_source, 'active')
            restored = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
            self.assertIsNone(restored.agent_auth_source)

    def test_login_only_profile_does_not_stage_or_read_controller_credentials(self):
        parser = create_setup_argument_parser('test')
        config = SetupConfig.from_args(parser.parse_args(['vm.example', 'agent']), 'agent_vm')
        self.assertFalse(config.copy_agent_keys)
        with tempfile.TemporaryDirectory() as directory, patch.object(setup_common, '_local_user_home') as home:
            setup_common.prepare_agent_payload(config, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        home.assert_not_called()

    def test_controller_propagates_login_only_when_input_and_output_are_terminals(self):
        for stdin_tty, stdout_tty, expected in ((True, True, 'login'), (False, True, 'check'), (True, False, 'check')):
            with self.subTest(stdin=stdin_tty, stdout=stdout_tty):
                config = SetupConfig(system_type='agent_vm', host='vm.example', username='agent',
                                     agent_tools=['codex'], agent_auth_source='login', dry_run=True)
                with redirect_stdout(io.StringIO()), patch.object(setup_common.sys.stdin, 'isatty', return_value=stdin_tty), patch.object(setup_common.sys.stdout, 'isatty', return_value=stdout_tty), patch.object(setup_common, 'get_ssh_control_path', return_value='/fake/control'), patch.object(setup_common, 'copy_project_files'), patch.object(setup_common, '_write_remote_args_file') as write:
                    self.assertEqual(setup_common._run_remote_setup_locked(config), 0)
                tokens = write.call_args.args[1]
                self.assertEqual(tokens[tokens.index('--agent-auth-mode') + 1], expected)

    def test_profile_is_subscription_first_and_remote_execution_is_noninteractive_by_default(self):
        parser = create_setup_argument_parser('test')
        for profile in ('agent_vm', 'agent_workstation', 'agent_code_vm'):
            config = SetupConfig.from_args(parser.parse_args(['vm.example', 'agent']), profile)
            self.assertEqual(config.agent_auth_source, 'login', profile)
            restored = SetupConfig.from_dict(config.host, profile, config.to_dict())
            self.assertEqual(restored.agent_auth_source, 'login', profile)
        remote = create_setup_argument_parser('remote', for_remote=True)
        args = remote.parse_args(['--system-type', 'agent_code_vm', '--username', 'agent'])
        args.host = 'vm.example'
        self.assertIsNone(SetupConfig.from_args(args, 'agent_code_vm').agent_auth_source)

    def test_login_does_not_copy_controller_credentials_or_require_github_access(self):
        config = SetupConfig(system_type='agent_workstation', host='vm.example', username='agent',
                             agent_tools=['gh', 'codex'], agent_auth_source='login', copy_agent_keys=True)
        validate_agent_git_settings(config)
        with tempfile.TemporaryDirectory() as directory, patch.object(setup_common, '_stage_secret_file') as stage, patch.object(setup_common, '_stage_github_auth') as github, redirect_stdout(io.StringIO()):
            setup_common.prepare_agent_payload(config, directory)
        stage.assert_not_called()
        github.assert_not_called()

    def test_setup_keeps_current_auth_or_logs_in_after_refresh_failure(self):
        config = SetupConfig(system_type='agent_code_vm', host='vm.example', username='agent', agent_auth_source='login')
        with patch.object(agent_steps, 'is_dry_run', return_value=False), patch.object(agent_steps, '_user_home', return_value='/fake/agent'), patch.object(agent_steps, 'inspect_codex_auth_file', return_value={'status': 'current'}), patch.object(agent_steps, '_run_as_login_user') as run, redirect_stdout(io.StringIO()):
            run.return_value = subprocess.CompletedProcess([], 0, '', '')
            agent_steps.run_codex_auth_maintenance(config)
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            run.side_effect = [subprocess.CompletedProcess([], 1, 'refresh_token_reused', ''), subprocess.CompletedProcess([], 0, '', '')]
            agent_steps.run_codex_auth_maintenance(config)
            self.assertIn('codex_auth_login.py --setup', run.call_args.args[2])
            self.assertFalse(run.call_args.kwargs['capture_output'])

    def test_unattended_missing_auth_fails_without_starting_login(self):
        config = SetupConfig(system_type='agent_code_vm', host='vm.example', username='agent', agent_auth_source='check')
        with patch.object(agent_steps, 'is_dry_run', return_value=False), patch.object(agent_steps, '_user_home', return_value='/fake/agent'), patch.object(agent_steps, 'inspect_codex_auth_file', return_value={'status': 'invalid'}), patch.object(agent_steps, '_run_as_login_user', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            with self.assertRaisesRegex(RuntimeError, 'agent auth login vm.example agent'):
                agent_steps.run_codex_auth_maintenance(config)
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
