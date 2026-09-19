"""T3 service readiness must not require optional Git integrations."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_cli
from lib.agent_readiness import build_agent_readiness_record


class T3ReadinessTests(unittest.TestCase):
    def inspect(self, *, gh=True, endpoint=True, fix=False):
        with ExitStack() as stack:
            home = stack.enter_context(tempfile.TemporaryDirectory())
            for name in ('basaltwater-t3code-pairing-provider', 't3code-pair'):
                path = Path(home, '.local', 'bin', name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n')
                path.chmod(0o700)
            commands = []

            def run(command, **kwargs):
                commands.append(command)
                if command[0] == 'systemctl':
                    return subprocess.CompletedProcess(command, 0, 'enabled\n', '')
                return subprocess.CompletedProcess(command, 1, '', '')

            for name, value in (
                ('_t3_active_binary', '/fake/t3'),
                ('_t3_node_binary', '/fake/node'),
                ('_t3_native_runtime_healthy', True),
                ('_t3_endpoint_reachable', endpoint),
                ('_tool_version', 't3 fixture'),
            ):
                stack.enter_context(patch.object(agent_cli, name, return_value=value))
            stack.enter_context(patch.object(agent_cli, '_tool_path', side_effect=lambda tool, _home:
                                            '/fake/' + tool if tool == 'git' or (tool == 'gh' and gh) else None))
            stack.enter_context(patch.object(agent_cli, '_run_check', side_effect=run))
            result = agent_cli.inspect_t3code(home, fix=fix)
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


if __name__ == '__main__':
    unittest.main()
