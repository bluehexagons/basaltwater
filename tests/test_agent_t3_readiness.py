"""T3 service readiness must not require optional Git integrations."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
import shlex
from pathlib import Path
import subprocess
import tempfile
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

    def inspect(self, *, gh=True, endpoint=True, fix=False, native=True, standalone=False):
        with ExitStack() as stack:
            home = stack.enter_context(tempfile.TemporaryDirectory())
            for name in ('basaltwater-t3code-pairing-provider', 't3code-pair'):
                path = Path(home, '.local', 'bin', name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n')
                path.chmod(0o700)
            commands = []
            binary = '/fake/t3'
            if standalone:
                runtime = Path(home, '.t3', 'runtime', 'versions', '0.0.45')
                runtime.mkdir(parents=True)
                binary = str(runtime / 't3')

            def run(command, **kwargs):
                commands.append(command)
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

    def test_doctor_reports_damaged_archive_without_stopping_active_service(self):
        result, _, commands = self.inspect(fix=True, native=False, standalone=True)
        self.assertFalse(result['healthy'])
        self.assertTrue(result['checks']['service_active'])
        self.assertEqual(result['fixes'], [])
        self.assertTrue(any('restore the matching upstream release archive' in warning
                            for warning in result['warnings']))
        self.assertFalse(any('stop' in command or 'restart' in command or 'rebuild' in command
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
