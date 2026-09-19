"""Tests for canonical shell completion installation."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import completions


class TestCompletions(unittest.TestCase):
    @patch("lib.completions._find_register_argcomplete", return_value="/usr/bin/register-python-argcomplete")
    def test_shell_completion_is_repeatable_and_preserves_user_settings(self, _register):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / f".{shell}rc"
                original = "alias ll='ls -la'\n"
                config.write_text(original)
                with patch.object(completions, f"get_{shell}_config_file", return_value=config):
                    setup = getattr(completions, f"setup_{shell}_completions")
                    self.assertTrue(setup())
                    self.assertTrue(setup())
                content = config.read_text()
                self.assertIn(original, content)
                self.assertEqual(content.count("register-python-argcomplete basaltw)"), 1)

    @patch("lib.completions._find_register_argcomplete", return_value="/usr/bin/register-python-argcomplete")
    @patch("lib.completions.subprocess.run")
    def test_fish_completion_is_installed(self, run, _register):
        run.return_value = subprocess.CompletedProcess([], 0, "complete --command basaltw\n", "")
        with tempfile.TemporaryDirectory() as directory:
            with patch("lib.completions.get_fish_config_dir", return_value=Path(directory)):
                self.assertTrue(completions.setup_fish_completions())
            self.assertEqual((Path(directory) / "completions/basaltw.fish").read_text(), run.return_value.stdout)
