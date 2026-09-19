"""Tests for local orchestration-host bootstrap helpers."""

from __future__ import annotations

import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import orchestrator_bootstrap
import infra_tools


def setUpModule():
    # These fixtures exercise Debian bootstrap, regardless of the developer OS.
    distro = patch("lib.cachyos.is_cachyos", return_value=False)
    distro.start()
    unittest.addModuleCleanup(distro.stop)


class TestResolveBootstrapUser(unittest.TestCase):
    @patch.dict("lib.orchestrator_bootstrap.os.environ", {"SUDO_USER": "admin"}, clear=False)
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    @patch("lib.orchestrator_bootstrap.validate_username", return_value=True)
    @patch("lib.orchestrator_bootstrap.pwd.getpwnam")
    def test_defaults_to_sudo_user_when_running_as_root(
        self,
        mock_getpwnam,
        _mock_validate_username,
        _mock_geteuid,
    ):
        mock_getpwnam.return_value = pwd.struct_passwd(("admin", "x", 1000, 1000, "", "/home/admin", "/bin/bash"))
        username, home_dir = orchestrator_bootstrap.resolve_bootstrap_user(None)
        self.assertEqual(username, "admin")
        self.assertEqual(home_dir, "/home/admin")


class TestInstallSystemPackages(unittest.TestCase):
    def setUp(self):
        available = patch("lib.orchestrator_bootstrap.shutil.which", return_value="/usr/bin/apt-get")
        available.start()
        self.addCleanup(available.stop)

    @patch("lib.orchestrator_bootstrap.ensure_debian_package_sources")
    @patch("lib.orchestrator_bootstrap._run_apt_command")
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    def test_checks_debian_sources_before_apt_update(
        self, _mock_geteuid, mock_run_apt, mock_sources
    ):
        mock_run_apt.side_effect = [0, 0]
        result = orchestrator_bootstrap.install_system_packages("bash")
        self.assertEqual(result, 0)
        mock_sources.assert_called_once_with()
        install_args = mock_run_apt.call_args_list[1].args[0]
        self.assertIn("bash-completion", install_args)
        self.assertIn("openssh-client", install_args)
        self.assertIn("rsync", install_args)
        self.assertIn("tar", install_args)
        update_args = mock_run_apt.call_args_list[0].args[0]
        self.assertIn("Dpkg::Use-Pty=0", update_args)
        self.assertIn("-q", update_args)
        self.assertNotIn("-qq", update_args)
        self.assertEqual(mock_run_apt.call_args_list[0].args[2], "APT package-list update")

    @patch("lib.orchestrator_bootstrap.subprocess.run")
    @patch("lib.orchestrator_bootstrap.ensure_debian_package_sources")
    @patch("lib.orchestrator_bootstrap._run_apt_command")
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    def test_qemu_guest_agent_is_installed_started_and_enabled(
        self,
        _mock_geteuid,
        mock_run_apt,
        _mock_sources,
        mock_run,
    ):
        mock_run_apt.side_effect = [0, 0]
        mock_run.return_value = unittest.mock.MagicMock(returncode=0)

        result = orchestrator_bootstrap.install_system_packages(
            "zsh",
            install_qemu_guest_agent=True,
        )

        self.assertEqual(result, 0)
        install_args = mock_run_apt.call_args_list[1].args[0]
        self.assertIn("qemu-guest-agent", install_args)
        mock_run.assert_called_once_with(
            ["systemctl", "enable", "--now", "qemu-guest-agent"],
            check=False,
        )

    @patch("lib.orchestrator_bootstrap.subprocess.run")
    @patch("lib.orchestrator_bootstrap.ensure_debian_package_sources")
    @patch("lib.orchestrator_bootstrap._run_apt_command")
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    def test_qemu_guest_agent_service_failure_fails_bootstrap_package_phase(
        self,
        _mock_geteuid,
        mock_run_apt,
        _mock_sources,
        mock_run,
    ):
        mock_run_apt.side_effect = [0, 0]
        mock_run.return_value = unittest.mock.MagicMock(returncode=1)

        result = orchestrator_bootstrap.install_system_packages(
            "zsh",
            install_qemu_guest_agent=True,
        )

        self.assertEqual(result, 1)


class TestRunOrchestratorBootstrap(unittest.TestCase):
    def setUp(self):
        cleanup = patch("lib.orchestrator_bootstrap.retire_legacy_tmpfiles_conf", return_value=False)
        cleanup.start()
        self.addCleanup(cleanup.stop)

    @patch("infra_tools.confirm_unsupported_environment", return_value=False)
    @patch("infra_tools.run_orchestrator_bootstrap")
    def test_bootstrap_refuses_unsupported_host_without_confirmation(
        self,
        mock_bootstrap,
        mock_confirm,
    ):
        with patch.object(sys, "argv", ["basaltw", "bootstrap"]):
            result = infra_tools.main()

        self.assertEqual(result, 1)
        mock_confirm.assert_called_once_with("local bootstrap")
        mock_bootstrap.assert_not_called()

    def test_qemu_guest_agent_requires_system_package_installation(self):
        with patch("lib.orchestrator_bootstrap.resolve_bootstrap_user", return_value=("admin", "/home/admin")), \
             patch("lib.orchestrator_bootstrap.install_system_packages") as mock_install:
            result = orchestrator_bootstrap.run_orchestrator_bootstrap(
                script_path="infra_tools.py",
                shell="bash",
                requested_user="admin",
                skip_system_packages=True,
                install_qemu_guest_agent=True,
            )

        self.assertEqual(result, 1)
        mock_install.assert_not_called()

    def test_self_setup_parser_accepts_qemu_guest_agent_flag(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["self-setup", "--qemu-guest-agent"])
        self.assertEqual(args.command, "self-setup")
        self.assertTrue(args.qemu_guest_agent)

    @patch(
        "lib.orchestrator_bootstrap.install_launcher",
        side_effect=OSError("read-only"),
    )
    @patch(
        "lib.orchestrator_bootstrap.resolve_bootstrap_user",
        return_value=("admin", "/home/admin"),
    )
    @patch("lib.orchestrator_bootstrap.install_system_packages", return_value=0)
    def test_system_launcher_failure_fails_bootstrap(
        self,
        _mock_install_packages,
        _mock_resolve_user,
        _mock_install_launcher,
    ):
        result = orchestrator_bootstrap.run_orchestrator_bootstrap(
            script_path="infra_tools.py",
            shell="bash",
            requested_user="admin",
        )
        self.assertEqual(result, 1)

    @patch("lib.orchestrator_bootstrap.install_launcher", return_value="/usr/local/bin/basaltw")
    @patch("lib.orchestrator_bootstrap.resolve_bootstrap_user", return_value=("admin", "/home/admin"))
    @patch("lib.orchestrator_bootstrap.install_system_packages", return_value=0)
    @patch("lib.orchestrator_bootstrap.get_current_username", return_value="root")
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    @patch("lib.orchestrator_bootstrap.subprocess.run")
    def test_runs_python_tools_as_target_user(
        self,
        mock_run,
        _mock_geteuid,
        _mock_current_username,
        _mock_install_packages,
        _mock_resolve_user,
        mock_install_launcher,
    ):
        mock_run.return_value = unittest.mock.MagicMock(returncode=0)
        result = orchestrator_bootstrap.run_orchestrator_bootstrap(
            script_path="infra_tools.py",
            shell="bash",
            requested_user="admin",
        )
        self.assertEqual(result, 0)
        mock_install_launcher.assert_called_once()
        command = mock_run.call_args.args[0]
        self.assertEqual(command[:4], ["runuser", "-u", "admin", "--"])
        self.assertIn("python-tools", command)
        self.assertIn("--script-path", command)

    @patch("lib.orchestrator_bootstrap.resolve_bootstrap_user", return_value=("admin", "/home/admin"))
    @patch("lib.orchestrator_bootstrap.get_current_username", return_value="admin")
    @patch("lib.orchestrator_bootstrap.retire_legacy_launcher", return_value=True)
    @patch("lib.orchestrator_bootstrap.subprocess.run")
    def test_can_skip_system_packages_for_current_user(
        self,
        mock_run,
        mock_retire_legacy_launcher,
        _mock_current_username,
        _mock_resolve_user,
    ):
        mock_run.return_value = unittest.mock.MagicMock(returncode=0)
        result = orchestrator_bootstrap.run_orchestrator_bootstrap(
            script_path="infra_tools.py",
            shell="zsh",
            requested_user="admin",
            skip_system_packages=True,
        )
        self.assertEqual(result, 0)
        command = mock_run.call_args.args[0]
        self.assertIn("python-tools", command)
        self.assertEqual(command[-4:-2], ["--shell", "zsh"])
        self.assertEqual(command[-2], "--script-path")
        mock_retire_legacy_launcher.assert_called_once_with("/usr/local/bin")


class TestInstallLauncher(unittest.TestCase):
    def test_both_launchers_survive_source_rollback_and_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "infra_tools.py")
            target = os.path.join(tmp, "bin")
            for version in ("previous", "renamed", "previous"):
                with open(source, "w", encoding="utf-8") as handle:
                    handle.write(f"print({version!r})\n")
                orchestrator_bootstrap.install_launcher(source, target_dir=target)
                for name in ("basaltw", "infra-tools"):
                    result = subprocess.run(
                        [os.path.join(target, name)], capture_output=True,
                        text=True, check=True, timeout=10,
                    )
                    self.assertEqual(result.stdout.strip(), version)
            self.assertEqual(set(os.listdir(target)), {"basaltw", "infra-tools"})

    def test_agent_launcher_upgrade_retains_user_command_and_adds_basaltw(self):
        from common import agent_steps
        from lib.config import SetupConfig

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".local", "bin")
            os.makedirs(target)
            old = os.path.join(target, "infra-tools")
            with open(old, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 17\n")
            os.chmod(old, 0o755)
            config = SetupConfig(host="example.com", username="agent", system_type="agent_vm")
            with patch.object(agent_steps, "_user_home", return_value=tmp), \
                 patch.object(agent_steps, "_chown_path"), \
                 patch.object(agent_steps, "_ensure_agent_shell_path"), \
                 patch.object(agent_steps, "is_dry_run", return_value=False):
                agent_steps.install_agent_cli_launcher(config)
                agent_steps.install_agent_cli_launcher(config)
            self.assertEqual(subprocess.run([old], check=False).returncode, 17)
            result = subprocess.run(
                [os.path.join(target, "basaltw"), "--version"],
                capture_output=True, text=True, check=True, timeout=10,
            )
            self.assertEqual(result.stdout, "basaltw 2.0.0\n")

    def test_install_launcher_writes_executable_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_script = os.path.join(tmp, "infra_tools.py")
            with open(project_script, "w", encoding="utf-8") as handle:
                handle.write("# fake script\n")
            target_dir = os.path.join(tmp, "bin")
            launcher = orchestrator_bootstrap.install_launcher(
                project_script, target_dir=target_dir
            )
            self.assertEqual(launcher, os.path.join(target_dir, "basaltw"))
            self.assertTrue(os.access(launcher, os.X_OK))
            with open(launcher, encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn("#!/bin/sh", content)
            self.assertIn(project_script, content)
            self.assertIn("exec python3", content)

    def test_install_launcher_removes_legacy_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_script = os.path.join(tmp, "infra_tools.py")
            with open(project_script, "w", encoding="utf-8") as handle:
                handle.write("# fake script\n")
            target_dir = os.path.join(tmp, "bin")
            os.makedirs(target_dir)
            legacy_launcher = os.path.join(target_dir, "infra_tools")
            with open(legacy_launcher, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\nexit 1\n")

            launcher = orchestrator_bootstrap.install_launcher(
                project_script, target_dir=target_dir
            )

            self.assertEqual(launcher, os.path.join(target_dir, "basaltw"))
            self.assertFalse(os.path.lexists(legacy_launcher))

    def test_install_launcher_safely_quotes_project_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "project with 'quote")
            os.makedirs(project_dir)
            project_script = os.path.join(project_dir, "infra_tools.py")
            with open(project_script, "w", encoding="utf-8") as handle:
                handle.write("import sys\nprint(sys.argv[1])\n")
            launcher = orchestrator_bootstrap.install_launcher(
                project_script,
                target_dir=os.path.join(tmp, "bin"),
            )
            result = subprocess.run(
                [launcher, "argument with spaces"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "argument with spaces")

    def test_install_launcher_rejects_missing_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                orchestrator_bootstrap.install_launcher(
                    os.path.join(tmp, "nope.py"), target_dir=tmp
                )


class TestRetireLegacyTmpfilesConf(unittest.TestCase):
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    def test_removes_existing_conf_file(self, _mock_geteuid):
        with tempfile.TemporaryDirectory() as tmp:
            conf_path = os.path.join(tmp, "infra_tools.conf")
            with open(conf_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("legacy")
            self.assertTrue(orchestrator_bootstrap.retire_legacy_tmpfiles_conf(conf_path))
            self.assertFalse(os.path.exists(conf_path))

    def test_missing_conf_is_already_retired(self):
        self.assertFalse(
            orchestrator_bootstrap.retire_legacy_tmpfiles_conf("/missing/infra_tools.conf")
        )

    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=1000)
    def test_retire_raises_without_root(self, _mock_geteuid):
        with tempfile.NamedTemporaryFile() as file_obj:
            with self.assertRaises(PermissionError):
                orchestrator_bootstrap.retire_legacy_tmpfiles_conf(file_obj.name)

    @patch("lib.orchestrator_bootstrap.retire_legacy_tmpfiles_conf", return_value=True)
    @patch("lib.orchestrator_bootstrap.install_launcher", return_value="/usr/local/bin/basaltw")
    @patch("lib.orchestrator_bootstrap.subprocess.run")
    @patch("lib.orchestrator_bootstrap.install_system_packages", return_value=0)
    @patch("lib.orchestrator_bootstrap.os.geteuid", return_value=0)
    def test_bootstrap_retires_tmpfiles_conf(
        self, _mock_geteuid, _mock_packages, mock_run, _mock_launcher, mock_tmpfiles
    ):
        mock_run.side_effect = [
            unittest.mock.MagicMock(returncode=0, stdout="", stderr=""),
            unittest.mock.MagicMock(returncode=0, stdout="", stderr=""),
            unittest.mock.MagicMock(returncode=0, stdout="", stderr=""),
        ]
        with patch("lib.orchestrator_bootstrap.resolve_bootstrap_user", return_value=("admin", "/home/admin")):
            with patch("lib.orchestrator_bootstrap.get_current_username", return_value="admin"):
                result = orchestrator_bootstrap.run_orchestrator_bootstrap("infra_tools.py", "bash", None)
        self.assertEqual(result, 0)
        mock_tmpfiles.assert_called_once()


if __name__ == "__main__":
    unittest.main()
