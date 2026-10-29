"""Tests for Node.js setup behavior."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import common.common_steps as common_steps
from lib.config import SetupConfig
from lib.update_policy import (
    DEPENDENCY_MIN_AGE_DAYS_ENV,
    ECOSYSTEM_AUTO_UPGRADE_ENV,
)
from web import dev_tools_steps


class TestNodeSetup(unittest.TestCase):
    def setUp(self):
        resolution = patch.object(common_steps, "resolve_node_release", return_value="v24.22.1")
        self.resolve = resolution.start()
        self.addCleanup(resolution.stop)

    def test_baseline_rejects_unsupported_defaults_even_when_tools_run(self):
        for version, accepted in (
            ("v24.22.1", True), ("v26.12.1", True), ("v24.23.0", True),
            ("v20.20.2", False), ("v22.23.4", False), ("v23.11.0", False),
            ("v24.22.0", False), ("v25.0.0", False), ("v26.12.0", False),
            ("v27.0.0", False), ("v26.12.1-rc.1", False), ("", False),
        ):
            result = SimpleNamespace(returncode=0, stdout=version + "\n")
            with self.subTest(version=version), patch.object(
                common_steps, "_run_as_login_user", return_value=result,
            ) as run:
                self.assertEqual(common_steps._node_baseline_is_usable("user", "/home/user", "nvm-env"), accepted)
                self.assertTrue(run.call_args.kwargs["capture_output"])
        with patch.object(common_steps, "_run_as_login_user", return_value=SimpleNamespace(
            returncode=1, stdout="v24.22.1",
        )):
            self.assertFalse(common_steps._node_baseline_is_usable("user", "/home/user", "nvm-env"))

    def test_ordinary_rerun_repairs_runnable_unsupported_default_without_removing_runtimes(self):
        baseline_versions = iter(("v22.23.4", "v24.22.1"))

        def command(_username, _home, script, **kwargs):
            version = next(baseline_versions) if "subprocess.run" in script else ""
            return SimpleNamespace(returncode=0, stdout=version, stderr="")

        with patch.object(common_steps.os.path, "exists", return_value=True), \
                patch.object(common_steps, "_chown_existing_paths"), \
                patch.object(common_steps, "_ensure_nvm_shell_init"), \
                patch.object(common_steps, "run") as host_run, \
                patch.object(common_steps, "_run_as_login_user", side_effect=command) as run:
            common_steps.install_node_for_user("user", "/home/user")
        commands = [call.args[2] for call in run.call_args_list]
        self.assertTrue(any("nvm install v24.22.1" in command for command in commands))
        self.assertTrue(any("nvm alias default v24.22.1" in command for command in commands))
        self.resolve.assert_called_once_with("lts/*")
        self.assertIn("libstdc++6 libatomic1", host_run.call_args.args[0])
        self.assertFalse(any("nvm uninstall" in command or "rm " in command for command in commands))

    def test_baseline_rejects_pnpm_shell_fallback_that_processes_cannot_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bin_dir = home / "bin"
            bin_dir.mkdir()
            nvm = home / "nvm.sh"
            nvm.write_text(
                "nvm() { :; }\nnode() { echo v24.22.1; }\nnpm() { :; }\n"
            )
            pnpm = bin_dir / "pnpm"

            def execute(_username, _home, command, **options):
                return subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-c", command],
                    env={"HOME": directory, "PATH": str(bin_dir) + os.pathsep + os.defpath},
                    cwd=directory, text=True, capture_output=True, timeout=10,
                    check=options.get("check", True),
                )

            with patch.object(common_steps, "_run_as_login_user", side_effect=execute):
                for header, status, accepted in (
                    ("", 0, False), ("#!/bin/sh\n", 0, True), ("#!/bin/sh\n", 1, False),
                ):
                    with self.subTest(header=header, status=status):
                        pnpm.write_text(header + f"echo 12.14.0\nexit {status}\n")
                        pnpm.chmod(0o755)
                        self.assertEqual(common_steps._node_baseline_is_usable(
                            "user", directory, ". " + shlex.quote(str(nvm)),
                        ), accepted)

    def test_t3_node_directory_checks_the_selected_executable_version(self):
        from common import t3code_steps

        for version, accepted in (("v24.22.1", True), ("v26.12.1", True),
                                  ("v24.22.0", False), ("v26.12.0", False), ("v25.1.0", False)):
            with self.subTest(version=version), patch.object(t3code_steps, "_run_as_login_user", side_effect=[
                SimpleNamespace(returncode=0, stdout="/fixture/node-bin\n"),
                SimpleNamespace(returncode=0, stdout=version + "\n"),
            ]) as run, patch.object(t3code_steps, "validate_filesystem_path"):
                if accepted:
                    self.assertEqual(t3code_steps._node_bin_directory("user", "/home/user"), "/fixture/node-bin")
                else:
                    with self.assertRaisesRegex(RuntimeError, "requires managed Node"):
                        t3code_steps._node_bin_directory("user", "/home/user")
                self.assertEqual(run.call_args.args[2], "/fixture/node-bin/node --version")

    @patch("web.dev_tools_steps.configure_maintenance_timer")
    @patch("web.dev_tools_steps.get_user_home", return_value="/srv/users/user")
    def test_configure_auto_update_node_disables_risky_auto_upgrades(self, _get_home, mock_configure):
        config = SetupConfig(host="host", username="user", system_type="server_web", install_node=True)
        dev_tools_steps.configure_auto_update_node(config)
        mock_configure.assert_called_once_with(
            service_name="auto-update-node",
            service_desc="Auto-update Node.js via nvm",
            timer_desc="Auto-update Node.js weekly",
            script_path="/opt/basaltwater/web/service_tools/auto_update_node.py",
            schedule="Sun *-*-* 03:00:00",
            check_path="/srv/users/user/.nvm/nvm.sh",
            check_name="Node.js",
            user="user",
            environment={ECOSYSTEM_AUTO_UPGRADE_ENV: "0"},
            purpose="auto-update",
        )

    @patch("common.common_steps.open", new_callable=mock_open, read_data="")
    @patch("common.common_steps.os.path.exists")
    @patch("common.common_steps.run")
    @patch("common.common_steps.get_user_home", return_value="/home/user")
    def test_install_node_uses_npm_freshness_cutoff(self, _get_home, mock_run, mock_exists, _open):
        nvm_installed = {"value": False}

        def exists(path: str) -> bool:
            if path == "/home/user/.nvm/nvm.sh":
                return nvm_installed["value"]
            if path == "/home/user/.nvm":
                return nvm_installed["value"]
            return path == "/home/user/.bashrc"

        def run_command(command: str, *args, **kwargs):
            if "vendor_installer.py nvm --accept-vendor-channel" in command:
                nvm_installed["value"] = True
            return SimpleNamespace(returncode=0, stdout="v24.22.1\n", stderr="")

        mock_exists.side_effect = exists
        mock_run.side_effect = run_command
        config = SetupConfig(host="host", username="user", system_type="server_web", install_node=True)

        with (
            patch.dict(os.environ, {DEPENDENCY_MIN_AGE_DAYS_ENV: "2"}),
            patch.object(common_steps, "_ensure_user_tool_shell_environment") as shell_env,
        ):
            common_steps.install_node(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        npm_commands = [command for command in commands if "npm install -g" in command]
        self.assertEqual(len(npm_commands), 2)
        self.assertTrue(all("--before=" in command for command in npm_commands))
        pnpm_command = next(command for command in npm_commands if "npm install -g pnpm" in command)
        self.assertIn("--ignore-scripts=false --allow-scripts=pnpm", pnpm_command)
        self.assertTrue(all("HOME=/home/user USER=user LOGNAME=user" in command for command in npm_commands))
        self.assertTrue(any("vendor_installer.py nvm --accept-vendor-channel" in command for command in commands))
        shell_env.assert_called_once_with("user", "/home/user")

    @patch("common.common_steps.open", new_callable=mock_open, read_data='export NVM_DIR="$HOME/.nvm"\n')
    @patch("common.common_steps.os.path.exists")
    @patch("common.common_steps.run")
    @patch("common.common_steps.get_user_home", return_value="/home/user")
    def test_install_node_repairs_existing_nvm_ownership_before_returning(self, _get_home, mock_run, mock_exists, _open):
        def exists(path: str) -> bool:
            return path in {
                "/home/user/.nvm",
                "/home/user/.nvm/nvm.sh",
                "/home/user/.npm",
            }

        mock_exists.side_effect = exists
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="v24.22.1\n", stderr="")
        config = SetupConfig(host="host", username="user", system_type="server_web", install_node=True)

        with patch.object(
            common_steps,
            "_ensure_user_tool_shell_environment",
        ) as shell_env:
            common_steps.install_node(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertIn("chown -R user:user /home/user/.nvm", commands)
        self.assertIn("chown -R user:user /home/user/.npm", commands)
        self.assertTrue(any("HOME=/home/user USER=user LOGNAME=user" in command and "nvm --version" in command for command in commands))
        self.assertFalse(any("apt-get install" in command for command in commands))
        shell_env.assert_called_once_with("user", "/home/user")

    @patch("common.common_steps.open", new_callable=mock_open, read_data='export NVM_DIR="$HOME/.nvm"\n')
    @patch("common.common_steps.os.path.exists")
    @patch("common.common_steps.run")
    @patch("common.common_steps.get_user_home", return_value="/home/user")
    def test_existing_nvm_repairs_missing_pnpm_baseline(
        self,
        _get_home,
        mock_run,
        mock_exists,
        _open,
    ):
        def exists(path: str) -> bool:
            return path in {
                "/home/user/.nvm",
                "/home/user/.nvm/nvm.sh",
                "/home/user/.npm",
            }

        baseline_checks = {"count": 0}

        def run_command(command: str, *args, **kwargs):
            if "subprocess.run" in command:
                baseline_checks["count"] += 1
                return SimpleNamespace(
                    returncode=1 if baseline_checks["count"] == 1 else 0,
                    stdout="v24.22.1\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="v24.22.1\n", stderr="")

        mock_exists.side_effect = exists
        mock_run.side_effect = run_command
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_web",
            install_node=True,
        )

        with patch.object(common_steps, "_ensure_user_tool_shell_environment"):
            common_steps.install_node(config)

        commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertTrue(any("nvm install v24.22.1" in command for command in commands))
        self.assertTrue(any(
            "npm install -g pnpm --ignore-scripts=false --allow-scripts=pnpm" in command
            for command in commands
        ))
        self.assertEqual(baseline_checks["count"], 2)
        self.assertFalse(any("command -v pnpm" in command for command in commands))

    @patch("common.common_steps.open", new_callable=mock_open, read_data='export NVM_DIR="$HOME/.nvm"\n')
    @patch("common.common_steps.os.path.exists")
    @patch("common.common_steps.run")
    @patch("common.common_steps.get_user_home", return_value="/home/user")
    def test_refresh_fails_when_pnpm_cannot_report_a_version(
        self,
        _get_home,
        mock_run,
        mock_exists,
        _open,
    ):
        mock_exists.side_effect = lambda path: path in {
            "/home/user/.nvm",
            "/home/user/.nvm/nvm.sh",
            "/home/user/.npm",
        }

        def run_command(command: str, *args, **kwargs):
            return SimpleNamespace(
                returncode=1 if "subprocess.run" in command else 0,
                stdout="",
                stderr="",
            )

        mock_run.side_effect = run_command
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_web",
            install_node=True,
            refresh_packages=True,
        )

        with (
            patch.object(common_steps, "_ensure_user_tool_shell_environment"),
            self.assertRaisesRegex(RuntimeError, "could not verify the refreshed"),
        ):
            common_steps.install_node(config)
        self.assertTrue(any(
            "npm install -g pnpm --ignore-scripts=false --allow-scripts=pnpm" in call.args[0]
            for call in mock_run.call_args_list
        ))

    @patch("common.common_steps.open", new_callable=mock_open, read_data='export NVM_DIR="$HOME/.nvm"\n')
    @patch("common.common_steps.os.path.exists")
    @patch("common.common_steps.run")
    @patch("common.common_steps.get_user_home", return_value="/home/user")
    def test_existing_unloadable_nvm_fails_setup(
        self,
        _get_home,
        mock_run,
        mock_exists,
        _open,
    ):
        mock_exists.side_effect = lambda path: path in {
            "/home/user/.nvm",
            "/home/user/.nvm/nvm.sh",
        }
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_web",
            install_node=True,
        )

        with (
            patch.object(common_steps, "_ensure_user_tool_shell_environment"),
            self.assertRaisesRegex(RuntimeError, "could not be loaded"),
        ):
            common_steps.install_node(config)


    def test_discovery_failure_never_bootstraps_nvm_or_installs_packages(self):
        for existing in (False, True):
            with self.subTest(existing=existing), \
                    patch.object(common_steps.os.path, "exists", return_value=existing), \
                    patch.object(common_steps, "_chown_existing_paths"), \
                    patch.object(common_steps, "_ensure_nvm_shell_init"), \
                    patch.object(common_steps, "_node_baseline_is_usable", return_value=False), \
                    patch.object(common_steps, "resolve_node_release", side_effect=ValueError("no supported LTS")), \
                    patch.object(common_steps, "_run_as_login_user", return_value=SimpleNamespace(returncode=0)) as user_run, \
                    patch.object(common_steps, "run") as host_run, self.assertRaisesRegex(ValueError, "no supported LTS"):
                common_steps.install_node_for_user("user", "/home/user", refresh=True)
            host_run.assert_not_called()
            self.assertTrue(all("nvm --version" in call.args[2] for call in user_run.call_args_list))

    def test_exact_baseline_activation_is_verified_before_alias_or_package_changes(self):
        for observed in ('v24.22.1', 'v24.22.2', 'v27.0.0'):
            with self.subTest(observed=observed), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                nvm = home / '.nvm'
                nvm.mkdir()
                (nvm / 'nvm.sh').write_text(
                    'nvm() { printf "nvm %s\\n" "$*" >> "$HOME/calls"; }\n'
                    f'node() {{ printf "{observed}\\n"; }}\n'
                    'npm() { printf "npm %s\\n" "$*" >> "$HOME/calls"; }\n'
                )

                def execute(_username, user_home, command, **options):
                    return subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                          env={**os.environ, 'HOME': user_home}, text=True,
                                          capture_output=True, check=options.get('check', True), timeout=10)

                with patch.object(common_steps, '_chown_existing_paths'), \
                        patch.object(common_steps, '_ensure_nvm_shell_init'), \
                        patch.object(common_steps, '_node_baseline_is_usable', return_value=True), \
                        patch.object(common_steps, 'run'), \
                        patch.object(common_steps, '_run_as_login_user', side_effect=execute):
                    if observed == 'v24.22.1':
                        common_steps.install_node_for_user('user', directory, refresh=True)
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            common_steps.install_node_for_user('user', directory, refresh=True)
                commands = (home / 'calls').read_text().splitlines()
                self.assertIn('nvm install v24.22.1', commands)
                self.assertIn('nvm use v24.22.1', commands)
                if observed == 'v24.22.1':
                    self.assertIn('nvm alias default v24.22.1', commands)
                    self.assertTrue(any(command.startswith('npm install -g ') for command in commands))
                else:
                    self.assertFalse(any(command.startswith(('nvm alias', 'npm ')) for command in commands))


if __name__ == "__main__":
    unittest.main()
