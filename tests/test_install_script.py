"""Integration tests for the curl/wget shell installer."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SCRIPT = os.path.join(PROJECT_ROOT, "install.sh")


class TestInstallScript(unittest.TestCase):
    def test_help_uses_explicit_agent_tool_options(self):
        result = subprocess.run(
            ["sh", INSTALL_SCRIPT, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--agent-tool gh", result.stdout)
        self.assertNotIn("--agent-suite", result.stdout)

    def _create_fixture(
        self,
        directory: str,
    ) -> tuple[str, str, dict[str, str]]:
        fake_home = os.path.join(directory, "home")
        source_root = os.path.join(directory, "source", "infra_tools-test")
        fake_bin = os.path.join(directory, "bin")
        log_path = os.path.join(directory, "calls.jsonl")
        os.makedirs(fake_home)
        os.makedirs(source_root)
        os.makedirs(fake_bin)

        fake_cli = os.path.join(source_root, "infra_tools.py")
        with open(fake_cli, "w", encoding="utf-8") as file_obj:
            file_obj.write(textwrap.dedent(
                """\
                from __future__ import annotations
                import json
                import os
                import sys

                with open(os.environ["INFRA_TOOLS_TEST_LOG"], "a", encoding="utf-8") as log:
                    log.write(json.dumps(sys.argv[1:]) + "\\n")
                if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
                    if os.environ.get("INFRA_TOOLS_TEST_BOOTSTRAP_FAIL") == "1":
                        raise SystemExit(7)
                    launcher_dir = os.path.join(os.environ["HOME"], ".local", "bin")
                    os.makedirs(launcher_dir, exist_ok=True)
                    launcher = os.path.join(launcher_dir, "infra-tools")
                    with open(launcher, "w", encoding="utf-8") as output:
                        output.write("#!/bin/sh\\nexit 0\\n")
                    os.chmod(launcher, 0o755)
                """
            ))

        git_environment = os.environ.copy()
        git_environment.update({
            "GIT_AUTHOR_NAME": "infra_tools tests",
            "GIT_AUTHOR_EMAIL": "tests@example.invalid",
            "GIT_COMMITTER_NAME": "infra_tools tests",
            "GIT_COMMITTER_EMAIL": "tests@example.invalid",
        })
        for git_args in [
            ["git", "init", "--initial-branch=main", source_root],
            ["git", "-C", source_root, "add", "infra_tools.py"],
            ["git", "-C", source_root, "commit", "-m", "fixture"],
            ["git", "-C", source_root, "tag", "v1.0.0"],
        ]:
            result = subprocess.run(
                git_args,
                check=False,
                capture_output=True,
                text=True,
                env=git_environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        getent_path = os.path.join(fake_bin, "getent")
        with open(getent_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                f'printf "%s:x:%s:%s::%s:/bin/bash\\n" "$2" "$(id -u)" "$(id -g)" {fake_home!r}\n'
            )
        os.chmod(getent_path, 0o755)

        id_path = os.path.join(fake_bin, "id")
        with open(id_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ "${INFRA_TOOLS_TEST_ROOT:-0}" = "1" ]; then\n'
                '    case "$1" in\n'
                '        -un) printf "root\\n" ;;\n'
                '        -u) printf "0\\n" ;;\n'
                '        *) exec /usr/bin/id "$@" ;;\n'
                '    esac\n'
                '    exit 0\n'
                "fi\n"
                'if [ "${INFRA_TOOLS_TEST_NON_ROOT:-0}" = "1" ]; then\n'
                '    case "$1" in\n'
                '        -un) printf "testuser\\n" ;;\n'
                '        -u) printf "1000\\n" ;;\n'
                '        *) exit 0 ;;\n'
                "    esac\n"
                "    exit 0\n"
                "fi\n"
                'exec /usr/bin/id "$@"\n'
            )
        os.chmod(id_path, 0o755)

        sudo_path = os.path.join(fake_bin, "sudo")
        with open(sudo_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ -n "${INFRA_TOOLS_TEST_SUDO_LOG:-}" ]; then\n'
                '    printf "%s\\n" "$*" >> "$INFRA_TOOLS_TEST_SUDO_LOG"\n'
                "fi\n"
                'exec "$@"\n'
            )
        os.chmod(sudo_path, 0o755)

        sed_path = os.path.join(fake_bin, "sed")
        with open(sed_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ "$#" -eq 3 ] && [ "$1" = "-n" ] && [ "$2" = "s/^ID=//p" ] && [ "$3" = "/etc/os-release" ]; then\n'
                '    printf "%s\\n" "${INFRA_TOOLS_TEST_OS_ID:-debian}"\n'
                "    exit 0\n"
                "fi\n"
                'exec /usr/bin/sed "$@"\n'
            )
        os.chmod(sed_path, 0o755)

        command_bin = os.path.join(directory, "command-bin")
        os.makedirs(command_bin)
        for command_name in ("ssh", "rsync", "curl"):
            command_path = os.path.join(command_bin, command_name)
            with open(command_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\nexit 0\n")
            os.chmod(command_path, 0o755)
        os.symlink(sys.executable, os.path.join(command_bin, "python3"))
        os.symlink(shutil.which("git"), os.path.join(command_bin, "git"))

        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join((
            fake_bin,
            command_bin,
            environment.get("PATH", ""),
        ))
        environment["INFRA_TOOLS_REPOSITORY_URL"] = source_root
        environment["INFRA_TOOLS_TEST_LOG"] = log_path
        return fake_home, log_path, environment

    def test_installs_and_forwards_optional_setup_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, log_path, environment = self._create_fixture(directory)
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--shell",
                    "bash",
                    "--setup",
                    "server_dev",
                    "10.0.0.50",
                    "agent",
                    "--dry-run",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isfile(os.path.join(install_dir, "infra_tools.py")))
            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            with open(os.path.join(install_dir, ".infra_tools", "channel.json"), encoding="utf-8") as file_obj:
                self.assertEqual(json.load(file_obj)["channel"], "dev")
            self.assertTrue(os.access(
                os.path.join(fake_home, ".local", "bin", "infra-tools"),
                os.X_OK,
            ))
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(calls[0][0], "bootstrap")
            self.assertEqual(
                calls[1],
                ["setup", "server_dev", "10.0.0.50", "agent", "--dry-run"],
            )

    def test_root_install_uses_target_home_for_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, _log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_ROOT"] = "1"
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.access(
                os.path.join(fake_home, ".local", "bin", "infra-tools"),
                os.X_OK,
            ))

    def test_unsupported_host_prompts_and_skips_system_package_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_ROOT"] = "1"
            environment["INFRA_TOOLS_TEST_OS_ID"] = "fedora"
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                ],
                check=False,
                capture_output=True,
                text=True,
                input="y\n",
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Continue installing the remote-management tools anyway? [y/N]", result.stdout)
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertIn("--skip-system-packages", calls[0])

    def test_unsupported_host_fails_when_controller_commands_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            _fake_home, _log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_ROOT"] = "1"
            environment["INFRA_TOOLS_TEST_OS_ID"] = "fedora"
            minimal_bin = os.path.join(directory, "minimal-bin")
            os.makedirs(minimal_bin)
            for command_name in ("sh", "head", "awk", "basename"):
                os.symlink(shutil.which(command_name), os.path.join(minimal_bin, command_name))
            environment["PATH"] = os.pathsep.join((environment["PATH"].split(os.pathsep)[0], minimal_bin))

            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", os.path.join(directory, "installed")],
                check=False,
                capture_output=True,
                text=True,
                input="y\n",
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required controller commands", result.stderr)
            self.assertIn("python3", result.stderr)
            self.assertIn("rsync", result.stderr)

    def test_cachyos_local_setup_keeps_the_human_user_without_elevation(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, log_path, environment = self._create_fixture(directory)
            environment.update(INFRA_TOOLS_TEST_NON_ROOT="1", INFRA_TOOLS_TEST_OS_ID="cachyos")
            environment.pop("SSH_CONNECTION", None)
            environment.pop("SSH_TTY", None)
            sudo_log = os.path.join(directory, "sudo.log")
            environment["INFRA_TOOLS_TEST_SUDO_LOG"] = sudo_log
            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", os.path.join(directory, "installed"),
                 "--local-setup", "agent_cachyos", "--node"],
                env=environment, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Continue installing", result.stdout)
            self.assertFalse(os.path.exists(sudo_log))
            with open(log_path) as handle:
                calls = [json.loads(line) for line in handle]
            self.assertEqual(calls[1], ["setup", "agent_cachyos", "localhost", "testuser", "--node"])

    def test_cachyos_rejects_other_local_profiles_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, log_path, environment = self._create_fixture(directory)
            environment.update(INFRA_TOOLS_TEST_NON_ROOT="1", INFRA_TOOLS_TEST_OS_ID="cachyos")
            environment.pop("SSH_CONNECTION", None)
            environment.pop("SSH_TTY", None)
            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", os.path.join(directory, "installed"),
                 "--local-setup", "agent_workstation"],
                env=environment, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("only agent_cachyos", result.stderr)
            self.assertFalse(os.path.exists(log_path))

    def test_qemu_guest_agent_flag_is_forwarded_to_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            _fake_home, log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_ROOT"] = "1"
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--qemu-guest-agent",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(calls[0][-1], "--qemu-guest-agent")

    def test_local_setup_elevates_and_defaults_to_install_user(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_NON_ROOT"] = "1"
            sudo_log_path = os.path.join(directory, "sudo.log")
            environment["INFRA_TOOLS_TEST_SUDO_LOG"] = sudo_log_path
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--shell",
                    "bash",
                    "--setup",
                    "server_lite",
                    "localhost",
                    "--machine",
                    "hardware",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--dry-run",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isfile(os.path.join(install_dir, "infra_tools.py")))
            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            self.assertTrue(os.access(
                os.path.join(fake_home, ".local", "bin", "infra-tools"),
                os.X_OK,
            ))
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(
                calls[1],
                [
                    "setup",
                    "server_lite",
                    "localhost",
                    "testuser",
                    "--machine",
                    "hardware",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--dry-run",
                ],
            )
            with open(sudo_log_path, encoding="utf-8") as file_obj:
                sudo_call = file_obj.read()
            self.assertIn("python3", sudo_call)
            self.assertIn("setup server_lite localhost testuser", sudo_call)

    def test_local_setup_option_supplies_localhost_and_user(self):
        with tempfile.TemporaryDirectory() as directory:
            _fake_home, log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_NON_ROOT"] = "1"
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--local-setup",
                    "control_plane",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--dry-run",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(
                calls[1],
                [
                    "setup",
                    "control_plane",
                    "localhost",
                    "testuser",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--dry-run",
                ],
            )

    def test_local_desktop_setup_forwards_control_plane_and_desktop_options(self):
        with tempfile.TemporaryDirectory() as directory:
            _fake_home, log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_NON_ROOT"] = "1"
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--local-setup",
                    "workstation_dev",
                    "--control-plane",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--desktop",
                    "xfce",
                    "--rdp",
                    "--rdp-existing-password",
                    "--dry-run",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(
                calls[1],
                [
                    "setup",
                    "workstation_dev",
                    "localhost",
                    "testuser",
                    "--control-plane",
                    "--agent-tool", "gh",
                    "--agent-tool", "codex",
                    "--agent-tool", "claude",
                    "--agent-tool", "opencode",
                    "--desktop",
                    "xfce",
                    "--rdp",
                    "--rdp-existing-password",
                    "--dry-run",
                ],
            )

    def test_update_keeps_previous_source_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, _log_path, environment = self._create_fixture(directory)
            install_dir = os.path.join(directory, "installed")
            os.makedirs(install_dir)
            with open(os.path.join(install_dir, "old-version"), "w", encoding="utf-8") as file_obj:
                file_obj.write("old")
            old_state = os.path.join(install_dir, "state")
            os.makedirs(old_state)
            with open(os.path.join(old_state, "setup.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")

            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            backups = glob.glob(f"{install_dir}.backup.*")
            self.assertEqual(len(backups), 1)
            self.assertTrue(os.path.isfile(os.path.join(backups[0], "old-version")))
            self.assertTrue(os.path.isfile(
                os.path.join(install_dir, "state", "setup.json")
            ))
            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o700)

    def test_channel_option_checks_out_release_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, _log_path, environment = self._create_fixture(directory)
            install_dir = os.path.join(directory, "installed")
            result = subprocess.run(
                [
                    "sh",
                    INSTALL_SCRIPT,
                    "--install-dir",
                    install_dir,
                    "--channel",
                    "stable",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with open(os.path.join(install_dir, ".infra_tools", "channel.json"), encoding="utf-8") as file_obj:
                self.assertEqual(json.load(file_obj)["channel"], "stable")
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", install_dir, "describe", "--tags", "--exact-match"],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "v1.0.0",
            )

    def test_bootstrap_failure_restores_previous_source(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, _log_path, environment = self._create_fixture(directory)
            environment["INFRA_TOOLS_TEST_BOOTSTRAP_FAIL"] = "1"
            install_dir = os.path.join(directory, "installed")
            os.makedirs(install_dir)
            old_marker = os.path.join(install_dir, "old-version")
            with open(old_marker, "w", encoding="utf-8") as file_obj:
                file_obj.write("old")

            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(os.path.isfile(old_marker))
            self.assertIn("previous install restored", result.stderr)

    def test_shell_syntax_and_help(self):
        syntax = subprocess.run(
            ["sh", "-n", INSTALL_SCRIPT],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        help_result = subprocess.run(
            ["sh", INSTALL_SCRIPT, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--setup", help_result.stdout)
        self.assertIn("--qemu-guest-agent", help_result.stdout)
        self.assertIn('--timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh"', help_result.stdout)
        self.assertIn('-O "$HOME/.infra_tools-install.sh"', help_result.stdout)
        self.assertNotIn("|", help_result.stdout)
        self.assertNotIn("sudo sh -s", help_result.stdout)
        self.assertNotIn("wget -qO-", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
