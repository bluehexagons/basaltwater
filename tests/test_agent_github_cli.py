from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from subprocess import CompletedProcess
from types import SimpleNamespace
import unittest
from unittest.mock import mock_open, patch

from common.agent_steps import install_github_cli


class TestGitHubCliSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(web_interfaces=["t3code"])
        self.version = "2.46.0-3"
        self.candidate = "2.105.0"
        self.install = ["apt-get", "install", "-y", "-qq", "gh"]
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(patch.dict(os.environ))
        self.dry_run = self.enterContext(patch("common.agent_steps.is_dry_run", return_value=False))
        self.gh_path = self.enterContext(patch("common.agent_steps._tool_path", return_value="/usr/bin/gh"))
        self.source = self.enterContext(patch("common.agent_steps.open", mock_open()))
        self.run = self.enterContext(patch("common.agent_steps.run", side_effect=self.run_command))
        self.install_run = self.enterContext(patch("lib.remote_utils.run", side_effect=self.run_command))

    def run_command(self, cmd, **kwargs):
        if cmd[0] == "dpkg-query":
            status = f"installed {self.version}" if self.version else ""
            return CompletedProcess(cmd, 0 if self.version else 1, status)
        if cmd[:2] == ["dpkg", "--compare-versions"]:
            self.assertEqual(cmd[3:], ["ge", "2.81.0"])
            self.assertIn(cmd[2], {"2.46.0-3", "2.81.0", "2.105.0"})
            return CompletedProcess(cmd, int(cmd[2] == "2.46.0-3"))
        if cmd == self.install:
            self.version = self.candidate
        return CompletedProcess(cmd, 0, "amd64" if cmd == "dpkg --print-architecture" else "")

    def test_upgrades_existing_old_debian_package_with_t3_selected(self) -> None:
        install_github_cli(self.config)

        self.install_run.assert_called_once_with(self.install, check=False)
        self.run.assert_any_call(["apt-get", "update", "-qq"])
        self.assertIn("https://cli.github.com/packages stable main", self.source().write.call_args.args[0])
        self.assertIn("GitHub CLI 2.81.0 or newer installed", self.output.getvalue())

    def test_rejects_an_upgrade_that_leaves_an_older_package(self) -> None:
        self.candidate = self.version

        with self.assertRaisesRegex(RuntimeError, "requires GitHub CLI 2.81.0 or newer"):
            install_github_cli(self.config)

    def test_ignores_debian_epoch_when_comparing_upstream_version(self) -> None:
        self.version = "1:2.46.0-3"
        self.candidate = "1:2.105.0"
        install_github_cli(self.config)

        self.install_run.assert_called_once_with(self.install, check=False)

    def test_installs_when_no_cli_or_package_is_present(self) -> None:
        self.gh_path.return_value = None
        self.version = None
        install_github_cli(self.config)

        self.install_run.assert_called_once_with(self.install, check=False)

    def test_keeps_a_compatible_debian_package_without_calling_apt(self) -> None:
        self.version = "2.81.0"
        install_github_cli(self.config)

        self.assertEqual(self.run.call_count, 2)
        self.install_run.assert_not_called()
        self.source.assert_not_called()

    def test_keeps_a_user_managed_executable(self) -> None:
        self.gh_path.return_value = "/home/example/.local/bin/gh"
        install_github_cli(self.config)

        self.run.assert_not_called()
        self.install_run.assert_not_called()
        self.source.assert_not_called()
        self.assertIn("requires gh 2.81.0 or newer", self.output.getvalue())

    def test_only_compares_package_versions_on_t3_hosts(self) -> None:
        self.config.web_interfaces = []
        with patch("common.agent_steps.shutil.which", return_value="/usr/bin/gh"):
            install_github_cli(self.config)

        self.gh_path.assert_not_called()
        self.run.assert_not_called()
        self.install_run.assert_not_called()

    def test_dry_run_does_not_inspect_target_user_or_change_apt(self) -> None:
        self.dry_run.return_value = True
        install_github_cli(self.config)

        self.gh_path.assert_not_called()
        self.run.assert_not_called()
        self.install_run.assert_not_called()
        self.source.assert_not_called()
