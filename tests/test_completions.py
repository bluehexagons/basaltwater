"""Tests for shell completion setup and command migration cleanup."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import completions


class TestCompletionMigration(unittest.TestCase):
    @patch("lib.completions._find_register_argcomplete", return_value="/usr/bin/register-python-argcomplete")
    def test_new_completions_preserve_transition_registration_and_are_repeatable(self, _register):
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / f".{shell}rc"
                original = 'eval "$(register-python-argcomplete infra-tools)"\n'
                config.write_text(original, encoding="utf-8")
                with patch.object(completions, f"get_{shell}_config_file", return_value=config):
                    setup = getattr(completions, f"setup_{shell}_completions")
                    self.assertTrue(setup())
                    self.assertTrue(setup())
                content = config.read_text(encoding="utf-8")
                self.assertIn(original, content)
                self.assertEqual(content.count("register-python-argcomplete basaltw)"), 1)

    def test_retire_legacy_shell_registrations_preserves_other_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / ".bashrc"
            config_file.write_text(
                "export PATH=\"$PATH\"\n"
                "# infra_tools shell completions\n"
                "eval \"$(register-python-argcomplete infra_tools.py)\"\n"
                "# infra_tools shell completions\n"
                "eval \"$(register-python-argcomplete infra_tools)\"\n"
                "# keep this user setting\n"
                "alias ll='ls -la'\n",
                encoding="utf-8",
            )

            completions._retire_legacy_user_completions(config_file)

            content = config_file.read_text(encoding="utf-8")
            self.assertNotIn("infra_tools shell completions", content)
            self.assertNotIn("register-python-argcomplete infra_tools", content)
            self.assertIn("export PATH", content)
            self.assertIn("alias ll", content)

    @patch("lib.completions._find_register_argcomplete", return_value="/usr/bin/register-python-argcomplete")
    def test_bash_setup_replaces_legacy_user_registration(self, _register):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / ".bashrc"
            config_file.write_text(
                "# infra_tools shell completions\n"
                "eval \"$(register-python-argcomplete infra_tools)\"\n",
                encoding="utf-8",
            )

            with patch("lib.completions.get_bash_config_file", return_value=config_file):
                self.assertTrue(completions.setup_bash_completions())

            content = config_file.read_text(encoding="utf-8")
            self.assertNotIn("register-python-argcomplete infra_tools)", content)
            self.assertIn("register-python-argcomplete basaltw)", content)

    @patch("lib.completions._find_register_argcomplete", return_value="/usr/bin/register-python-argcomplete")
    @patch("lib.completions.subprocess.run")
    def test_fish_setup_removes_legacy_completion_files(self, mock_run, _register):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="complete --command basaltw\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as tmp:
            fish_dir = Path(tmp)
            (fish_dir / "completions").mkdir()
            for name in completions.LEGACY_INFRA_TOOLS_COMMANDS:
                (fish_dir / "completions" / f"{name}.fish").write_text("legacy", encoding="utf-8")

            with patch("lib.completions.get_fish_config_dir", return_value=fish_dir):
                self.assertTrue(completions.setup_fish_completions())

            self.assertTrue((fish_dir / "completions" / "basaltw.fish").is_file())
            for name in completions.LEGACY_INFRA_TOOLS_COMMANDS:
                self.assertFalse((fish_dir / "completions" / f"{name}.fish").exists())


if __name__ == "__main__":
    unittest.main()
