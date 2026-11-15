"""Read-only T3 dotenv checks use Node's parser and disclose only conflicting keys."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_cli
from lib.t3code_runtime import t3_service_environment_probe_command


class T3ServiceEnvironmentTests(unittest.TestCase):
    def test_missing_env_needs_no_runtime_or_process(self):
        with tempfile.TemporaryDirectory() as home, patch.object(agent_cli, '_run_check') as run:
            self.assertEqual(agent_cli._t3_service_environment_ready(
                home, None, None, '127.0.0.1', 3773), (True, ''))
        run.assert_not_called()

    def test_valid_probe_reports_only_conflicting_bind_keys(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home, '.t3/service.env')
            path.parent.mkdir()
            path.write_text('TOKEN=fixture-secret\n')
            for conflicts in ([], ['T3CODE_HOST'], ['T3CODE_HOST', 'T3CODE_PORT']):
                with self.subTest(conflicts=conflicts), \
                        patch.object(agent_cli, 't3_service_environment_probe_command', return_value=['fixture']), \
                        patch.object(agent_cli, '_run_check', return_value=subprocess.CompletedProcess(
                            [], 0, json.dumps({'conflicts': conflicts}), 'fixture-secret')):
                    ready, warning = agent_cli._t3_service_environment_ready(
                        home, None, None, '127.0.0.1', 3773)
                self.assertEqual(ready, not conflicts)
                self.assertNotIn('fixture-secret', warning)
                for key in conflicts:
                    self.assertIn(key, warning)
                if not conflicts:
                    self.assertEqual(warning, '')
            self.assertEqual(path.read_text(), 'TOKEN=fixture-secret\n')

    def test_unavailable_or_invalid_probe_is_not_success_and_never_echoes_output(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home, '.t3/service.env')
            path.parent.mkdir()
            path.write_text('TOKEN=fixture-secret\n')
            for status, stdout in ((1, 'fixture-secret'), (0, 'fixture-secret'),
                                   (0, '{}'), (0, '{"conflicts":["fixture-secret"]}')):
                with self.subTest(status=status, stdout=stdout), \
                        patch.object(agent_cli, 't3_service_environment_probe_command', return_value=['fixture']), \
                        patch.object(agent_cli, '_run_check', return_value=
                                     subprocess.CompletedProcess([], status, stdout, 'fixture-secret')):
                    result = agent_cli._t3_service_environment_ready(home, None, None, '127.0.0.1', 3773)
                self.assertFalse(result[0])
                self.assertNotIn('fixture-secret', result[1])
            with patch.object(agent_cli, '_run_check') as run:
                self.assertFalse(agent_cli._t3_service_environment_ready(
                    home, None, None, '127.0.0.1', 3773)[0])
            run.assert_not_called()
            self.assertEqual(path.read_text(), 'TOKEN=fixture-secret\n')

    def test_standalone_probe_uses_embedded_node_and_replaces_inherited_node_options(self):
        with tempfile.TemporaryDirectory(prefix='t3 home ') as home:
            binary = Path(home, 'versions/0.1.9/t3')
            binary.parent.mkdir(parents=True)
            binary.write_text('fixture')
            command = t3_service_environment_probe_command(str(binary), None, home, '::1', 3773)
            self.assertEqual(command[0], '/usr/bin/env')
            self.assertIn('BASALTW_T3_EXPECTED_HOST=::1', command)
            self.assertIn('BASALTW_T3_EXPECTED_PORT=3773', command)
            self.assertTrue(command[-3].startswith('NODE_OPTIONS=--require='))
            self.assertEqual(command[-2:], [str(binary), '--version'])


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the T3 dotenv probe')
class T3ServiceEnvironmentNodeTests(unittest.TestCase):
    def probe(self, path: Path, *, expected_host='127.0.0.1', expected_port=3773):
        command = t3_service_environment_probe_command(
            None, shutil.which('node'), str(path.parent), expected_host, expected_port)
        # This executes only the repository's parser against a temporary file;
        # no T3 server, service manager, credentials or network is involved.
        return subprocess.run(command, capture_output=True, text=True, timeout=10,
                              env={**os.environ, 'NODE_OPTIONS': '--require=/absent/fixture'})

    def test_dotenv_quotes_comments_duplicates_exports_and_multiline_values(self):
        cases = (
            ('TOKEN=fixture-secret\n', []),
            ('T3CODE_HOST="127.0.0.1" # same\nexport T3CODE_PORT=3773\n', []),
            ('T3CODE_HOST=0.0.0.0\nT3CODE_HOST=127.0.0.1\n', []),
            ('TOKEN="first line\nT3CODE_HOST=fixture-secret\nlast line"\n', []),
            ('# T3CODE_PORT=fixture-secret\nT3CODE_PORT="3773"\n', []),
            ('T3CODE_HOST=fixture-secret\nT3CODE_PORT=4999\n', ['T3CODE_HOST', 'T3CODE_PORT']),
            ('T3CODE_PORT=\n', ['T3CODE_PORT']),
            ('T3CODE_HOST="$(fixture-secret)"\n', ['T3CODE_HOST']),
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'service.env')
            for contents, conflicts in cases:
                with self.subTest(contents=contents):
                    path.write_text(contents)
                    result = self.probe(path)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {'conflicts': conflicts})
                    self.assertEqual(result.stderr, '')
                    self.assertNotIn('fixture-secret', result.stdout)
                    self.assertEqual(path.read_text(), contents)

    def test_symlink_directory_oversize_and_missing_files_fail_without_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'service.env'
            other = root / 'private'
            other.write_text('TOKEN=fixture-secret\n')
            path.symlink_to(other)
            result = self.probe(path)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), ('', ''))
            path.unlink()
            path.mkdir()
            self.assertNotEqual(self.probe(path).returncode, 0)
            path.rmdir()
            path.write_bytes(b'x' * (1024 * 1024 + 1))
            self.assertNotEqual(self.probe(path).returncode, 0)
            path.unlink()
            self.assertNotEqual(self.probe(path).returncode, 0)
            self.assertEqual(other.read_text(), 'TOKEN=fixture-secret\n')
