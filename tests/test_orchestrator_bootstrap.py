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
import basaltwater


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

    @patch("basaltwater.confirm_unsupported_environment", return_value=False)
    @patch("basaltwater.run_orchestrator_bootstrap")
    def test_bootstrap_refuses_unsupported_host_without_confirmation(
        self,
        mock_bootstrap,
        mock_confirm,
    ):
        with patch.object(sys, "argv", ["basaltw", "bootstrap"]):
            result = basaltwater.main()

        self.assertEqual(result, 1)
        mock_confirm.assert_called_once_with("local bootstrap")
        mock_bootstrap.assert_not_called()

    def test_qemu_guest_agent_requires_system_package_installation(self):
        with patch("lib.orchestrator_bootstrap.resolve_bootstrap_user", return_value=("admin", "/home/admin")), \
             patch("lib.orchestrator_bootstrap.install_system_packages") as mock_install:
            result = orchestrator_bootstrap.run_orchestrator_bootstrap(
                script_path="basaltwater.py",
                shell="bash",
                requested_user="admin",
                skip_system_packages=True,
                install_qemu_guest_agent=True,
            )

        self.assertEqual(result, 1)
        mock_install.assert_not_called()

    def test_self_setup_parser_accepts_qemu_guest_agent_flag(self):
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
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
            script_path="basaltwater.py",
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
            script_path="basaltwater.py",
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
    @patch("lib.orchestrator_bootstrap.subprocess.run")
    def test_can_skip_system_packages_for_current_user(
        self,
        mock_run,
        _mock_current_username,
        _mock_resolve_user,
    ):
        mock_run.return_value = unittest.mock.MagicMock(returncode=0)
        result = orchestrator_bootstrap.run_orchestrator_bootstrap(
            script_path="basaltwater.py",
            shell="zsh",
            requested_user="admin",
            skip_system_packages=True,
        )
        self.assertEqual(result, 0)
        command = mock_run.call_args.args[0]
        self.assertIn("python-tools", command)
        self.assertEqual(command[-4:-2], ["--shell", "zsh"])
        self.assertEqual(command[-2], "--script-path")


class TestInstallLauncher(unittest.TestCase):
    def test_primary_collision_preserves_all_existing_launchers(self):
        for kind in ("file", "binary", "symlink", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, "basaltwater.py")
                with open(source, "w") as handle:
                    handle.write("print('fixture')\n")
                target = os.path.join(tmp, "bin")
                os.mkdir(target)
                for name in ("infra-tools", "infra_tools"):
                    with open(os.path.join(target, name), "w") as handle:
                        handle.write("original")
                primary = os.path.join(target, "basaltw")
                if kind == "symlink":
                    os.symlink(source, primary)
                elif kind == "directory":
                    os.mkdir(primary)
                else:
                    with open(primary, "wb") as handle:
                        handle.write(b"\xff\x00" if kind == "binary" else b"#!/bin/sh\nexit 17\n")
                before = os.lstat(primary)
                with self.assertRaisesRegex(ValueError, "command collision"):
                    orchestrator_bootstrap.install_launcher(source, target_dir=target)
                after = os.lstat(primary)
                self.assertEqual(
                    (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_size),
                    (before.st_ino, before.st_mode, before.st_mtime_ns, before.st_size),
                )
                for name in ("infra-tools", "infra_tools"):
                    with open(os.path.join(target, name)) as handle:
                        self.assertEqual(handle.read(), "original")



    def test_install_launcher_writes_executable_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_script = os.path.join(tmp, "basaltwater.py")
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


    def test_install_launcher_safely_quotes_project_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "project with 'quote")
            os.makedirs(project_dir)
            project_script = os.path.join(project_dir, "basaltwater.py")
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


if __name__ == "__main__":
    unittest.main()
