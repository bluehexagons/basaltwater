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
        self.node = self.cache / "node" / "bin" / "node"
        self.node.parent.mkdir(parents=True)
        self.node.touch()
        self.probe = self.enterContext(patch.object(ci, "run_probe", return_value=Mock(issue=None, output="v24.22.1")))
        self.enterContext(patch.object(ci, "ROOT", root))

    def run_ci(self, **kwargs):
        return ci.run_debian_ci(str(self.python), str(self.cache), node=str(self.node), **kwargs)

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
            self.assertIn(f"BASALTWATER_CI_NODE_DIR={self.node.parent}", argv)
            script_words = argv[-1].split()
            self.assertNotIn("nodejs", script_words)
            self.assertIn("libstdc++6", script_words)
            self.assertIn("libatomic1", script_words)
            self.assertEqual(argv[-1].splitlines()[-1], "ci_phase checks make check-ci")
            self.assertNotIn("make check\n", argv[-1])
            self.assertNotIn("make coverage\n", argv[-1])
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

    def test_node_compatibility_runs_its_suite_once_and_preserves_failure(self):
        with patch.object(ci.subprocess, "run", side_effect=[
                Mock(returncode=0), Mock(returncode=7), Mock(returncode=0)]) as run:
            self.assertEqual(self.run_ci(node_only=True), 7)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["pull", "run", "rm"])
        self.assertEqual(run.call_args_list[1].args[0][-1].splitlines()[-1],
                         "ci_phase checks make test SUITE=node TEST_ARGS='--durations 10'")

    def test_incompatible_check_modes_fail_before_any_commands(self):
        with patch.object(ci.subprocess, "run") as run, self.assertRaises(ValueError):
            self.run_ci(coverage=True, node_only=True)
        self.probe.assert_not_called()
        run.assert_not_called()

    def test_check_failure_does_not_select_another_registry_or_repeat_commands(self):
        with patch.object(ci.subprocess, "run", side_effect=[
                Mock(returncode=0), Mock(returncode=7), Mock(returncode=0)]) as run:
            self.assertEqual(self.run_ci(), 7)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["pull", "run", "rm"])
        self.assertEqual(run.call_args_list[1].args[0][-1].splitlines()[-1], "ci_phase checks make check")

    def test_parallel_checks_run_one_make_and_preserve_failure(self):
        for coverage in (False, True):
            with self.subTest(coverage=coverage), patch.object(ci.subprocess, "run", side_effect=[
                    Mock(returncode=0), Mock(returncode=7), Mock(returncode=0)]) as run:
                self.assertEqual(self.run_ci(coverage=coverage, check_jobs=2), 7)
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["pull", "run", "rm"])
            target = "check-ci" if coverage else "check"
            self.assertEqual(run.call_args_list[1].args[0][-1].splitlines()[-1],
                             f"ci_phase checks make -j2 {target}")

    def test_invalid_job_limits_fail_before_any_commands(self):
        for jobs in (0, -1, 3, True, "2", 1.5):
            with self.subTest(jobs=jobs), patch.object(ci.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "CI check jobs"):
                    self.run_ci(check_jobs=jobs)
                self.probe.assert_not_called()
                run.assert_not_called()

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
                    ci.run_debian_ci(python, cache, node=str(self.node))
                run.assert_not_called()

    def test_unsupported_node_fails_before_any_container_mutation(self):
        for version in ("v20.19.2", "v24.22.0", "v26.12.0", "v27.0.0", "invalid"):
            with self.subTest(version=version), patch.object(ci.subprocess, "run") as run:
                self.probe.return_value.output = version
                with self.assertRaisesRegex(ValueError, "CI Node requires"):
                    self.run_ci()
                run.assert_not_called()

    def test_node_outside_mounted_cache_is_rejected_before_probe(self):
        outside = ci.ROOT / "outside-node"
        outside.touch()
        with self.assertRaisesRegex(ValueError, "CI Node must"):
            ci.run_debian_ci(str(self.python), str(self.cache), node=str(outside))
        self.probe.assert_not_called()
