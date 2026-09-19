"""Regression tests for safety boundaries used during setup upgrades."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common.common_steps import copy_ssh_keys_to_user
from lib.config import SetupConfig
from lib.setup_common import prepare_deployments
from web.service_tools.cicd_executor import perform_remote_deployment


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "example.com",
        "username": "alice",
        "system_type": "server_web",
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestDeploymentPreflight(unittest.TestCase):
    @patch("lib.setup_common.clone_repository")
    def test_dry_run_validates_staged_manifest(
        self, mock_clone: MagicMock
    ) -> None:
        config = _config(
            dry_run=True,
            deploy_specs=[["example.com", "https://example.com/app.git"]],
        )
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as target_dir:
            with open(os.path.join(repo_dir, "basaltwater.json"), "w", encoding="utf-8") as handle:
                handle.write('{"version": 1, "components": []}\n')
            mock_clone.return_value = (repo_dir, "abc123")

            with self.assertRaisesRegex(ValueError, "non-empty array"):
                prepare_deployments(config, target_dir)

    @patch("lib.setup_common.clone_repository", return_value=None)
    def test_any_clone_failure_aborts_staging(self, _mock_clone: MagicMock) -> None:
        config = _config(
            deploy_specs=[["example.com", "https://example.com/app.git"]]
        )

        with tempfile.TemporaryDirectory() as target_dir:
            with self.assertRaisesRegex(RuntimeError, "no target changes were started"):
                prepare_deployments(config, target_dir)

    @patch("lib.setup_common.clone_repository")
    def test_ruby_repository_aborts_before_remote_setup(
        self, mock_clone: MagicMock
    ) -> None:
        config = _config(
            deploy_specs=[["legacy.example.com", "https://example.com/legacy.git"]]
        )
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as target_dir:
            with open(os.path.join(repo_dir, "Gemfile"), "w", encoding="utf-8") as handle:
                handle.write("source 'https://rubygems.org'\n")
            mock_clone.return_value = (repo_dir, "abc123")

            with self.assertRaisesRegex(RuntimeError, "pinned legacy release"):
                prepare_deployments(config, target_dir)

            self.assertFalse(os.path.exists(os.path.join(target_dir, "legacy.commit")))

    @patch("lib.remote_deploy.get_deploy_target", return_value={"base_dir": "/var/www"})
    def test_cicd_remote_deploy_refuses_ruby_workspace(
        self, _mock_target: MagicMock
    ) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, "Gemfile"), "w", encoding="utf-8") as handle:
                handle.write("gem 'rails'\n")
            log_file = os.path.join(workspace, "build.log")

            result = perform_remote_deployment(
                workspace,
                "app.example.com",
                "legacy.example.com",
                "https://example.com/legacy.git",
                "abc123",
                log_file,
                {},
            )

            self.assertFalse(result)
            with open(log_file, encoding="utf-8") as handle:
                self.assertIn("pinned legacy basaltwater release", handle.read())


class TestSshKeyUpgrade(unittest.TestCase):
    def test_existing_user_keys_are_preserved_and_root_keys_deduplicated(self) -> None:
        def open_for_read(path: str, *_args: object, **_kwargs: object) -> io.StringIO:
            if path == "/root/.ssh/authorized_keys":
                return io.StringIO("ssh-ed25519 ROOT\nssh-ed25519 EXISTING\n")
            return io.StringIO("ssh-ed25519 EXISTING\nssh-rsa USERONLY\n")

        with patch("common.common_steps.get_user_home", return_value="/home/alice"), \
             patch("common.common_steps.os.path.exists", return_value=True), \
             patch("common.common_steps.os.path.lexists", return_value=True), \
             patch("common.common_steps.os.path.islink", return_value=False), \
             patch("common.common_steps.os.path.isfile", return_value=True), \
             patch("common.common_steps.os.path.isdir", return_value=True), \
             patch("builtins.open", side_effect=open_for_read), \
             patch("common.common_steps.write_text_atomic") as write_atomic, \
             patch("common.common_steps.run"):
            copy_ssh_keys_to_user(_config())

        write_atomic.assert_called_once_with(
            "/home/alice/.ssh/authorized_keys",
            "ssh-ed25519 EXISTING\nssh-rsa USERONLY\nssh-ed25519 ROOT\n",
            mode=0o600,
        )

    def test_symlinked_authorized_keys_is_refused(self) -> None:
        with patch("common.common_steps.get_user_home", return_value="/home/alice"), \
             patch("common.common_steps.os.path.exists", return_value=True), \
             patch("common.common_steps.os.path.lexists", return_value=True), \
             patch(
                 "common.common_steps.os.path.islink",
                 side_effect=lambda path: path.endswith("authorized_keys")
                 and path != "/root/.ssh/authorized_keys",
             ), \
             patch("common.common_steps.os.path.isfile", return_value=True), \
             patch("common.common_steps.os.path.isdir", return_value=True), \
             patch("common.common_steps.run"):
            with self.assertRaisesRegex(RuntimeError, "non-regular authorized_keys"):
                copy_ssh_keys_to_user(_config())


if __name__ == "__main__":
    unittest.main()
