"""Hosted Debian CI falls back on image pulls, while failed checks run once."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import run_debian_ci as ci


class DebianCITests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = root / "tool-cache"
        self.python = self.cache / "Python" / "bin" / "python"
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        self.enterContext(patch.object(ci, "ROOT", root))

    def run_ci(self, **kwargs):
        return ci.run_debian_ci(str(self.python), str(self.cache), **kwargs)

    def test_registry_failure_and_timeout_fall_back_then_run_checks_once(self):
        for failure in (Mock(returncode=1), subprocess.TimeoutExpired("docker pull", 120)):
            with self.subTest(failure=failure), patch.object(ci.subprocess, "run", side_effect=[
                    failure, Mock(returncode=0), Mock(returncode=0), Mock(returncode=0)]) as run:
                self.assertEqual(self.run_ci(coverage=True), 0)
            calls = run.call_args_list
            self.assertEqual(calls[1].args[0], ["docker", "pull", ci.IMAGES[1]])
            argv = calls[2].args[0]
            self.assertIn(ci.IMAGES[1], argv)
            self.assertIn(f"type=bind,source={self.cache},target={self.cache},readonly", argv)
            self.assertIn("make coverage", argv[-1])
            self.assertNotIn("GITHUB_TOKEN", " ".join(argv))
            self.assertEqual(calls[2].kwargs["timeout"], 900)
            self.assertEqual(calls[3].args[0][:3], ["docker", "rm", "--force"])
            self.assertEqual(calls[3].args[0][3], argv[argv.index("--name") + 1])

    def test_all_registries_fail_without_starting_checks(self):
        with patch.object(ci.subprocess, "run", return_value=Mock(returncode=1)) as run:
            with self.assertRaisesRegex(RuntimeError, "checks were not started"):
                self.run_ci()
        self.assertEqual(run.call_count, len(ci.IMAGES))
        self.assertTrue(all(call.args[0][1] == "pull" for call in run.call_args_list))

    def test_check_failure_does_not_select_another_registry_or_repeat_commands(self):
        with patch.object(ci.subprocess, "run", side_effect=[
                Mock(returncode=0), Mock(returncode=7), Mock(returncode=0)]) as run:
            self.assertEqual(self.run_ci(), 7)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["pull", "run", "rm"])
        self.assertNotIn("make coverage", run.call_args_list[1].args[0][-1])

    def test_timeout_or_interruption_cleans_up_only_the_owned_container(self):
        for error in (subprocess.TimeoutExpired("docker run", 900), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), patch.object(ci.subprocess, "run", side_effect=[
                    Mock(returncode=0), error, Mock(returncode=0)]) as run:
                with self.assertRaises(type(error)):
                    self.run_ci()
            self.assertEqual(run.call_args_list[-1].args[0][:3], ["docker", "rm", "--force"])

    def test_invalid_paths_fail_before_any_docker_operation(self):
        for python, cache in ((str(self.python) + "\n", str(self.cache)),
                              ("relative", str(self.cache)),
                              (str(self.python), str(self.python)),
                              (str(self.python), str(self.python.parent))):
            if cache == str(self.python.parent):
                # A different existing Python must not escape the cache root.
                python = str(ci.ROOT / "outside-python")
                Path(python).touch()
            with self.subTest(python=python, cache=cache), patch.object(ci.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    ci.run_debian_ci(python, cache)
                run.assert_not_called()
