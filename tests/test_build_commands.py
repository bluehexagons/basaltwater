from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from lib import build_commands as commands
from lib.remote_utils import CommandTimeoutError
from lib.command_display import redacted_saved_args, redacted_setup_parts


class TestBuildCommands(unittest.TestCase):
    def test_forwarding(self):
        with patch.object(commands, 'run') as run:
            self.assertIs(commands.run_command(['git'], timeout=3, input='data', cwd='/tmp'), run.return_value)
            run.assert_called_once_with(['git'], check=False, input_data='data', timeout=3, cwd='/tmp')

    def test_timeout_conversion(self):
        with patch.object(commands, 'run', side_effect=CommandTimeoutError('git', 3)):
            with self.assertRaises(subprocess.TimeoutExpired) as error:
                commands.run_command(['git'], timeout=3)
            self.assertEqual(error.exception.timeout, 3)

    def test_invalid_timeout_stops_execution(self):
        with patch.object(commands, 'run') as run:
            for value in (0, -1, float('inf'), float('nan'), True, '3', None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    commands.run_command(['git'], timeout=value)
            run.assert_not_called()

    def test_secret_display(self):
        for option in ('--webhook-secret', '--webhook-github-token', '--ci-registration-token'):
            for parts in ([option, 'private'], [option + ' private'], [option + '=private']):
                self.assertNotIn('private', ' '.join(redacted_setup_parts(parts)))
            args = {option[2:].replace('-', '_'): 'private'}
            self.assertNotIn('private', str(redacted_saved_args(args)))
            self.assertIn('private', str(args))
