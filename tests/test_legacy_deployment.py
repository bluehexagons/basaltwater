"""Safety tests for static deployments and discontinued automatic builds."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from deploy.deploy_steps import deploy_repository
from lib.deployment import DeploymentOrchestrator
from lib.setup_common import prepare_deployments


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

    @patch("lib.setup_common.clone_repository")
    def test_controller_preflight_rejects_automatic_node(self, clone) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "package.json"), "w") as stream:
                stream.write("{}")
            clone.return_value = (directory, "commit")
            config = SimpleNamespace(
                deploy_specs=[("example.com", "https://example.test/site.git")], dry_run=True
            )
            with self.assertRaisesRegex(RuntimeError, "no longer supported.*basaltwater.json"):
                prepare_deployments(config, directory)


class TestAtomicLegacyDeployment(unittest.TestCase):
    @patch("lib.deployment.run")
    def test_automatic_node_is_rejected_without_changing_release(self, mock_run) -> None:
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

            with self.assertRaisesRegex(RuntimeError, "no longer supported.*basaltwater.json"):
                orchestrator.deploy_from_archive(
                    source_dir,
                    "example.com",
                    "/",
                    "https://example.test/site.git",
                    "new",
                )

            with open(marker, "r", encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "current release")
            mock_run.assert_not_called()

    @patch("deploy.deploy_steps.ensure_deploy_user")
    def test_node_without_manifest_is_rejected_before_user_setup(self, ensure_user) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            with open(os.path.join(source_dir, "package.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            with self.assertRaisesRegex(RuntimeError, "no longer supported"):
                deploy_repository(source_dir, "example.com", "https://example.test/site.git")
        ensure_user.assert_not_called()

    @patch("lib.deployment.run", return_value=MagicMock(returncode=0))
    def test_static_cleanup_failure_keeps_successful_activation(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as source_dir:
            orchestrator = DeploymentOrchestrator(base_dir=base_dir)
            destination = os.path.join(base_dir, "example_com")
            os.makedirs(destination)
            with open(os.path.join(destination, "index.html"), "w") as stream:
                stream.write("old")
            with open(os.path.join(source_dir, "index.html"), "w") as stream:
                stream.write("new")
            with patch("lib.deployment.shutil.rmtree", side_effect=OSError("busy")):
                result = orchestrator.deploy_from_archive(
                    source_dir, "example.com", "/", "https://example.test/site.git", "new"
                )
            self.assertEqual(result["dest_path"], destination)
            with open(os.path.join(destination, "index.html")) as stream:
                self.assertEqual(stream.read(), "new")
            backups = [name for name in os.listdir(base_dir) if ".previous-" in name]
            self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
