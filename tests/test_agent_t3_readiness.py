"""T3 service readiness must not require optional Git integrations."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import shlex
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import agent_cli
from lib.agent_readiness import build_agent_readiness_record
from lib.t3code_runtime import t3_native_probe_command
from common import t3code_steps


class T3ReadinessTests(unittest.TestCase):
    def test_host_inventory_counts_scratch_files_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            scratch = Path(home, '.t3', 'scratch', 'thread-folder')
            scratch.mkdir(parents=True)
            generated = scratch / 'output.txt'
            generated.write_bytes(b'generated output')
            external = Path(outside, 'private.txt')
            external.write_bytes(b'external contents must not be counted')
            (scratch / 'external').symlink_to(outside, target_is_directory=True)
            inventory = agent_cli._agent_storage_inventory(home)
            self.assertEqual(inventory['paths']['t3_scratch'], str(scratch.parent))
            self.assertEqual(inventory['size_bytes']['t3_scratch'], len(b'generated output'))
            self.assertTrue(generated.is_file())
            self.assertTrue(external.is_file())

    def test_standalone_probe_uses_embedded_node_without_host_node(self):
        with tempfile.TemporaryDirectory(prefix="t3 home ") as home:
            binary = Path(home, "versions", "0.0.45", "t3")
            binary.parent.mkdir(parents=True)
            binary.write_text("fixture")
            command = t3_native_probe_command(str(binary), None)
            self.assertEqual(command[0], "/usr/bin/env")
            self.assertTrue(command[1].startswith("NODE_OPTIONS=--require="))
            self.assertEqual(command[2:], [str(binary), "--version"])

            completed = subprocess.CompletedProcess([], 0, "", "")
            with patch.object(agent_cli, "_run_check", return_value=completed) as run:
                self.assertTrue(agent_cli._t3_native_runtime_healthy(None, str(binary), {}))
            self.assertEqual(run.call_args.args[0], command)
            with patch.object(t3code_steps, "_run_as_login_user", return_value=completed) as run:
                self.assertTrue(t3code_steps._t3_native_runtime_healthy(
                    "agent", home, "/absent/node-bin", str(binary)))
            self.assertEqual(shlex.split(run.call_args.args[2]), command)

    def test_legacy_probe_requires_the_service_node(self):
        with tempfile.TemporaryDirectory() as home:
            binary = Path(home, "versions", "0.0.34", "node_modules", "t3", "dist", "bin.mjs")
            binary.parent.mkdir(parents=True)
            binary.write_text("fixture")
            self.assertIsNone(t3_native_probe_command(str(binary), None))
            node = Path(home, "node")
            node.write_text("fixture")
            node.chmod(0o700)
            self.assertEqual(t3_native_probe_command(str(binary), str(node)), [
                str(node), "-e", "require(process.argv[1])",
                str(Path(home, "versions", "0.0.34", "node_modules", "node-pty")),
            ])

    def test_standalone_repair_does_not_rebuild_with_host_npm(self):
        with tempfile.TemporaryDirectory() as home:
            binary = Path(home, "versions", "0.0.45", "t3")
            binary.parent.mkdir(parents=True)
            binary.write_text("fixture")
            with patch.object(agent_cli, "_run_check") as doctor_run, \
                    patch.object(t3code_steps, "_run_as_login_user") as setup_run:
                self.assertFalse(agent_cli._repair_t3_native_runtime("/fake/node", str(binary), {}))
                with self.assertRaisesRegex(RuntimeError, "restore the matching"):
                    t3code_steps._rebuild_t3_native_runtime("agent", home, "/fake", str(binary))
            doctor_run.assert_not_called()
            setup_run.assert_not_called()

    def test_active_binary_accepts_forward_protocol_with_known_runtime_layout(self):
        with tempfile.TemporaryDirectory() as home:
            runtime = Path(home, ".t3", "runtime")
            version_root = runtime / "versions" / "0.0.44"
            binary = version_root / "t3"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o700)
            (runtime / "service-state.json").write_text(
                json.dumps({"protocol": 3, "activeVersion": "0.0.44"})
            )

            self.assertEqual(agent_cli._t3_active_binary(home), str(binary))

            (runtime / "service-state.json").write_text(
                json.dumps({"protocol": 4, "activeVersion": "0.0.44"})
            )
            self.assertEqual(agent_cli._t3_active_binary(home), str(binary))

            (runtime / "service-state.json").write_text(
                json.dumps({"protocol": 1, "activeVersion": "0.0.44"})
            )
            self.assertIsNone(agent_cli._t3_active_binary(home))

            (runtime / "service-state.json").write_text(
                json.dumps({"protocol": True, "activeVersion": "0.0.44"})
            )
            self.assertIsNone(agent_cli._t3_active_binary(home))

    def inspect(self, *, gh=True, endpoint=True, fix=False, native=True, standalone=False,
                wait_for_startup=False, initial_service_status=0):
        with ExitStack() as stack:
            home = stack.enter_context(tempfile.TemporaryDirectory())
            for name in ('basaltwater-t3code-pairing-provider', 't3code-pair'):
                path = Path(home, '.local', 'bin', name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n')
                path.chmod(0o700)
            commands = []
            active_checks = 0
            binary = '/fake/t3'
            if standalone:
                runtime = Path(home, '.t3', 'runtime', 'versions', '0.0.45')
                runtime.mkdir(parents=True)
                binary = str(runtime / 't3')

            def run(command, **kwargs):
                nonlocal active_checks
                commands.append(command)
                if command[:3] == ['systemctl', '--user', 'is-active']:
                    active_checks += 1
                    if active_checks == 1:
                        return subprocess.CompletedProcess(command, initial_service_status,
                                                           'deactivating\n' if initial_service_status else 'active\n', '')
                if command[0] == 'systemctl':
                    return subprocess.CompletedProcess(command, 0, 'enabled\n', '')
                return subprocess.CompletedProcess(command, 1, '', '')

            for name, value in (
                ('_t3_active_binary', binary),
                ('_t3_node_binary', '/fake/node'),
                ('_t3_native_runtime_healthy', native),
                ('_t3_endpoint_reachable', endpoint),
                ('_tool_version', 't3 fixture'),
            ):
                stack.enter_context(patch.object(agent_cli, name, return_value=value))
            stack.enter_context(patch.object(agent_cli, '_tool_path', side_effect=lambda tool, _home:
                                            '/fake/' + tool if tool == 'git' or (tool == 'gh' and gh) else None))
            stack.enter_context(patch.object(agent_cli, '_run_check', side_effect=run))
            result = agent_cli.inspect_t3code(home, fix=fix, wait_for_startup=wait_for_startup)
            record = build_agent_readiness_record([], [result], trigger='manual',
                                                  boot_id_path=str(Path(home, 'absent-boot-id')))
            return result, record, commands

    def test_installed_unauthenticated_github_is_optional(self):
        result, record, _ = self.inspect()
        self.assertTrue(result['healthy'])
        self.assertEqual(result['status'], 'warning')
        for check in ('git_identity', 'gh_authenticated', 'git_credential_helper'):
            self.assertFalse(result['checks'][check])
            self.assertNotIn(check, result['required_checks'])
        self.assertEqual(len(result['warnings']), 3)
        self.assertTrue(record['healthy'])
        self.assertEqual(record['capabilities'][0]['warnings'], result['warnings'])
        self.assertNotIn('git_identity', record['capabilities'][0]['required_checks'])

    def test_absent_github_needs_no_github_authentication(self):
        result, _, commands = self.inspect(gh=False)
        self.assertTrue(result['healthy'])
        self.assertNotIn('gh_authenticated', result['checks'])
        self.assertEqual(len(result['warnings']), 1)
        self.assertFalse(any(command[0] == '/fake/gh' for command in commands))

    def test_service_failure_still_blocks_and_reports_only_required_failures(self):
        result, record, _ = self.inspect(endpoint=False)
        self.assertFalse(result['healthy'])
        self.assertFalse(record['healthy'])
        self.assertEqual(result['status'], 'unhealthy')
        self.assertEqual(agent_cli._readiness_failure_details(record), ['t3code: failed checks: endpoint'])

    def test_diagnostics_do_not_restart_or_reauthenticate(self):
        for fix in (False, True):
            with self.subTest(fix=fix):
                result, _, commands = self.inspect(fix=fix)
                self.assertEqual(result['fixes'], [])
                self.assertFalse(any('restart' in command or 'setup-git' in command or 'login' in command
                                     for command in commands))

    def test_doctor_reports_damaged_archive_without_stopping_active_service(self):
        result, _, commands = self.inspect(fix=True, native=False, standalone=True)
        self.assertFalse(result['healthy'])
        self.assertTrue(result['checks']['service_active'])
        self.assertEqual(result['fixes'], [])
        self.assertTrue(any('restore the matching upstream release archive' in warning
                            for warning in result['warnings']))
        self.assertFalse(any('stop' in command or 'restart' in command or 'rebuild' in command
                             for command in commands))

    def test_failed_stop_prevents_native_repair_and_reports_diagnostic(self):
        for initial_service_status in (0, 3):
            with self.subTest(initial_service_status=initial_service_status):
                self.assert_failed_stop_blocks_repair(initial_service_status)

    def assert_failed_stop_blocks_repair(self, initial_service_status):
        for outcome, diagnostic in (
            (subprocess.CompletedProcess([], 1, '', 'stop denied'), 'stop denied'),
            (subprocess.CompletedProcess([], 2, 'stop refused', ''), 'stop refused'),
            (subprocess.CompletedProcess([], 3, '', ''), 'status 3'),
            (subprocess.TimeoutExpired('systemctl', 30, output=b'still stopping', stderr=b'busy'), 'timed out'),
            (OSError('stop unavailable'), 'stop unavailable'),
        ):
            with self.subTest(diagnostic=diagnostic), \
                    patch.object(agent_cli.subprocess, 'run',
                                 **({'side_effect': outcome} if isinstance(outcome, Exception)
                                    else {'return_value': outcome})) as stop, \
                    patch.object(agent_cli, '_repair_t3_native_runtime', return_value=True) as repair:
                result, _, commands = self.inspect(fix=True, native=False, endpoint=True,
                                                   initial_service_status=initial_service_status)
            repair.assert_not_called()
            self.assertFalse(result['healthy'])
            self.assertFalse(result['checks']['native_runtime'])
            self.assertTrue(result['checks']['endpoint'])
            self.assertEqual(result['fixes'], [])
            self.assertFalse(any('restart' in command for command in commands))
            self.assertTrue(any(diagnostic in warning for warning in result['warnings']))
            if isinstance(outcome, subprocess.TimeoutExpired):
                self.assertTrue(any('still stopping' in warning and 'busy' in warning
                                    for warning in result['warnings']))
            stop.assert_called_once()
            self.assertEqual(stop.call_args.args[0], ['systemctl', '--user', 'stop', 't3code.service'])
            self.assertEqual(stop.call_args.kwargs['timeout'], 30)

    def test_successful_stop_allows_native_repair_and_one_restart(self):
        for initial_service_status in (0, 3):
            with self.subTest(initial_service_status=initial_service_status), \
                    patch.object(agent_cli.subprocess, 'run', return_value=
                                 subprocess.CompletedProcess([], 0, '', '')) as stop, \
                    patch.object(agent_cli, '_repair_t3_native_runtime', return_value=True) as repair:
                result, _, commands = self.inspect(fix=True, native=False,
                                                   initial_service_status=initial_service_status)
            stop.assert_called_once()
            repair.assert_called_once()
            self.assertTrue(result['healthy'])
            self.assertIn('rebuilt T3 Code native runtime', result['fixes'])
            self.assertEqual(sum('restart' in command for command in commands), 1)

    def test_wait_flag_reaches_local_inspection_without_repairs(self):
        result, _, _ = self.inspect()
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest='command'))
        args = parser.parse_args(['agent', 'doctor', '--wait-for-t3-startup', '--json'])
        with patch.object(agent_cli, 'inspect_t3code', return_value=result) as inspect, \
                patch.object(agent_cli, 'inspect_agent_tools', return_value=[]), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent_cli.run_agent_command(args), 0)
        inspect.assert_called_once_with(fix=False, wait_for_startup=True)
        self.assertEqual(json.loads(output.getvalue()), [result])

    def test_unstable_startup_fails_even_when_last_endpoint_probe_answered(self):
        with patch.object(agent_cli, '_wait_for_t3_startup', return_value=(True, True, 'startup deadline exceeded')):
            result, record, commands = self.inspect(wait_for_startup=True)
        self.assertFalse(result['healthy'])
        self.assertFalse(record['healthy'])
        self.assertTrue(result['checks']['endpoint'])
        self.assertFalse(result['checks']['startup_ready'])
        self.assertIn('startup_ready', result['required_checks'])
        self.assertIn('startup deadline exceeded', result['warnings'])
        self.assertFalse(any('restart' in command for command in commands))

    def test_remote_wait_preserves_other_capability_and_repair_budgets(self):
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest='command'))
        for flags, capability_count, repair_budget, storage_budget in (
            ([], 1, 0, 0),
            (['--all-capabilities', '--fix', '--container-storage'], 4, 360, 40),
        ):
            with self.subTest(flags=flags):
                args = parser.parse_args(['agent', 'doctor', 'vm.example', 'agent',
                                          '--wait-for-t3-startup', '--json', *flags])
                with patch.object(agent_cli, '_run_remote_agent_lifecycle', return_value=0) as remote:
                    self.assertEqual(agent_cli.run_agent_command(args), 0)
                self.assertIn('--wait-for-t3-startup', remote.call_args.args[2])
                self.assertEqual(remote.call_args.kwargs['timeout'],
                                 agent_cli._T3_STARTUP_WAIT_SECONDS
                                 + capability_count * agent_cli._REMOTE_DOCTOR_TIMEOUT_SECONDS
                                 + repair_budget + storage_budget)

    def test_doctor_text_displays_warnings_and_returns_success(self):
        result, _, _ = self.inspect()
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest='command'))
        args = parser.parse_args(['agent', 'doctor', '--capability', 't3code'])
        with patch.object(agent_cli, 'inspect_t3code', return_value=result), \
                patch.object(agent_cli, 'inspect_agent_tools', return_value=[]), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(agent_cli.run_agent_command(args), 0)
        self.assertIn('provider sessions are not tested', output.getvalue())
        self.assertIn('warning: Git author identity', output.getvalue())
        self.assertNotIn('readiness checks failed', output.getvalue())


class T3StartupWaitTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0.0
        self.environment = {'XDG_RUNTIME_DIR': '/fake/runtime'}
        # Replace the owning module's clock; never change other modules' timers.
        self.enterContext(patch.object(agent_cli, 'time', SimpleNamespace(
            monotonic=lambda: self.elapsed, sleep=self.advance)))
        self.commands = []

    def advance(self, seconds):
        self.elapsed += seconds

    @staticmethod
    def state(invocation='a' * 32, active='active', loaded='loaded'):
        return subprocess.CompletedProcess([], 0,
            f'ActiveState={active}\nInvocationID={invocation}\nLoadState={loaded}\n', '')

    def test_delayed_startup_reaches_stable_readiness_without_restarts(self):
        def run(command, **kwargs):
            self.commands.append(command)
            self.assertEqual(kwargs['environment'], self.environment)
            return self.state()

        with patch.object(agent_cli, '_run_check', side_effect=run), \
                patch.object(agent_cli, '_t3_endpoint_reachable', side_effect=[False, False, True, True, True]) as endpoint:
            result = agent_cli._wait_for_t3_startup(3773, self.environment)
        self.assertEqual(result, (True, True, ''))
        self.assertEqual(endpoint.call_count, 5)
        self.assertEqual(self.elapsed, 4)
        self.assertTrue(all(command[:3] == ['systemctl', '--user', 'show'] for command in self.commands))

    def test_changed_or_unknown_invocation_cannot_pass_early(self):
        for invocations, elapsed in (
            (['a' * 32] * 2 + ['b' * 32] * 6, 3),
            (['a' * 32, 'b' * 32] + ['b' * 32] * 6, 3),
            ([''] + ['b' * 32] * 6, 3),
            (['invalid'] + ['b' * 32] * 6, 3),
        ):
            with self.subTest(invocations=invocations):
                self.elapsed = 0
                with patch.object(agent_cli, '_run_check', side_effect=[self.state(value) for value in invocations]), \
                        patch.object(agent_cli, '_t3_endpoint_reachable', return_value=True):
                    self.assertEqual(agent_cli._wait_for_t3_startup(3773, self.environment), (True, True, ''))
                self.assertEqual(self.elapsed, elapsed)

    def test_failed_or_missing_service_fails_early_without_endpoint_probe(self):
        for state in (self.state(active='failed'), self.state(loaded='not-found')):
            with self.subTest(state=state.stdout), \
                    patch.object(agent_cli, '_run_check', return_value=state), \
                    patch.object(agent_cli, '_t3_endpoint_reachable') as endpoint:
                result = agent_cli._wait_for_t3_startup(3773, self.environment)
            self.assertIn('failed or missing', result[2])
            endpoint.assert_not_called()
            self.assertEqual(self.elapsed, 0)

    def test_process_failure_during_endpoint_probe_is_not_success(self):
        with patch.object(agent_cli, '_run_check', side_effect=[self.state(), self.state(active='failed')]), \
                patch.object(agent_cli, '_t3_endpoint_reachable', return_value=True):
            active, endpoint, failure = agent_cli._wait_for_t3_startup(3773, self.environment)
        self.assertFalse(active)
        self.assertTrue(endpoint)
        self.assertIn('failed service', failure)
        self.assertEqual(self.elapsed, 0)

    def test_permanently_unready_endpoint_exhausts_one_deadline(self):
        with patch.object(agent_cli, '_T3_STARTUP_WAIT_SECONDS', 3), \
                patch.object(agent_cli, '_run_check', return_value=self.state()) as run, \
                patch.object(agent_cli, '_t3_endpoint_reachable', return_value=False) as endpoint:
            result = agent_cli._wait_for_t3_startup(3773, self.environment)
        self.assertEqual(self.elapsed, 3)
        self.assertEqual(endpoint.call_count, 3)
        self.assertEqual(run.call_count, 3)
        self.assertIn('within 3 seconds', result[2])

    def test_slow_probes_share_the_real_deadline_and_progress_uses_stderr(self):
        probe_timeouts = []

        def run(_command, **kwargs):
            probe_timeouts.append(kwargs['timeout'])
            self.advance(min(0.75, kwargs['timeout']))
            return self.state()

        def endpoint(_port, **kwargs):
            probe_timeouts.append(kwargs['timeout'])
            self.advance(kwargs['timeout'])
            return False

        with patch.object(agent_cli, '_T3_STARTUP_WAIT_SECONDS', 3), \
                patch.object(agent_cli, '_T3_STARTUP_PROGRESS_SECONDS', 1), \
                patch.object(agent_cli, '_run_check', side_effect=run), \
                patch.object(agent_cli, '_t3_endpoint_reachable', side_effect=endpoint), \
                redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()) as output:
            result = agent_cli._wait_for_t3_startup(3773, self.environment)
        self.assertEqual(self.elapsed, 3)
        self.assertEqual(probe_timeouts, [3, 2.25])
        self.assertIn('within 3 seconds', result[2])
        self.assertIn('Waiting for T3 Code startup', error.getvalue())
        self.assertEqual(output.getvalue(), '')

    def test_endpoint_answer_at_deadline_does_not_pass(self):
        def endpoint(_port, **kwargs):
            self.advance(kwargs['timeout'])
            return True

        with patch.object(agent_cli, '_T3_STARTUP_WAIT_SECONDS', 2), \
                patch.object(agent_cli, '_run_check', return_value=self.state()) as run, \
                patch.object(agent_cli, '_t3_endpoint_reachable', side_effect=endpoint):
            result = agent_cli._wait_for_t3_startup(3773, self.environment)
        self.assertTrue(result[2])
        self.assertEqual(self.elapsed, 2)
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
