"""Safety tests for the supported legacy static/Node deployment path."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from deploy.deploy_steps import deploy_repository
from lib.deployment import DeploymentOrchestrator


class TestRemovedRubyDeployment(unittest.TestCase):
    @patch("deploy.deploy_steps.ensure_deploy_user")
    def test_ruby_source_is_rejected_before_user_setup(self, ensure_user) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            with open(os.path.join(source_dir, "Gemfile"), "w", encoding="utf-8") as file_obj:
                file_obj.write("source 'https://rubygems.org'\n")

            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                deploy_repository(
                    source_dir,
                    "example.com",
                    "https://example.test/legacy.git",
                )

        ensure_user.assert_not_called()


class TestAtomicLegacyDeployment(unittest.TestCase):
    @patch("lib.deployment.run")
    def test_failed_node_build_keeps_existing_release(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as source_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            destination = orchestrator.get_deployment_path(
                "example.com", "/", "https://example.test/site.git"
            )
            os.makedirs(destination)
            marker = os.path.join(destination, "live.txt")
            with open(marker, "w", encoding="utf-8") as file_obj:
                file_obj.write("current release")
            with open(os.path.join(source_dir, "package.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write('{"scripts":{"build":"false"}}')

            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=1, stdout="", stderr="build failed"),
            ]
            with self.assertRaisesRegex(RuntimeError, "Node build failed"):
                orchestrator.deploy_from_archive(
                    source_dir,
                    "example.com",
                    "/",
                    "https://example.test/site.git",
                    "new",
                )

            with open(marker, "r", encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "current release")
            self.assertIn("runuser -u build-example_com", mock_run.call_args.args[0])

    @patch("lib.deployment.run")
    def test_node_without_build_script_does_not_run_npm(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            with open(os.path.join(source_dir, "package.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            orchestrator = DeploymentOrchestrator()

            self.assertFalse(orchestrator._build_node_project(source_dir))

        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
