"""Integration tests for the curl/wget shell installer."""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SCRIPT = os.path.join(PROJECT_ROOT, "install.sh")


class TestInstallScript(unittest.TestCase):
    def _mark_managed(self, install_dir: str) -> None:
        os.makedirs(os.path.join(install_dir, '.basaltwater'), exist_ok=True)
        with open(os.path.join(install_dir, '.basaltwater', 'managed-install'), 'w') as stream:
            stream.write('basaltwater-v1\n')

    def test_interruptions_restore_old_install_at_rename_boundaries(self):
        for boundary in ('before-backup', 'after-backup', 'after-activation'):
            for signal in ('HUP', 'INT', 'TERM'):
                with self.subTest(boundary=boundary, signal=signal), tempfile.TemporaryDirectory() as directory:
                    _, _, environment = self._create_fixture(directory)
                    install_dir = os.path.join(directory, 'installed')
                    self._mark_managed(install_dir)
                    old_file = os.path.join(install_dir, 'old-version')
                    with open(old_file, 'w') as stream:
                        stream.write('old')
                    wrapper = os.path.join(directory, 'bin', 'mv')
                    with open(wrapper, 'w') as stream:
                        stream.write(textwrap.dedent('''\
                            #!/bin/sh
                            case "$2" in
                                *.backup.*)
                                    if [ "$TEST_BOUNDARY" = before-backup ]; then
                                        kill -s "$TEST_SIGNAL" "$PPID"
                                        exit 1
                                    fi
                                    /usr/bin/mv "$@" || exit 1
                                    if [ "$TEST_BOUNDARY" = after-backup ]; then
                                        kill -s "$TEST_SIGNAL" "$PPID"
                                    fi
                                    ;;
                                *)
                                    /usr/bin/mv "$@" || exit 1
                                    case "$1" in
                                        *.new.*)
                                            if [ "$TEST_BOUNDARY" = after-activation ]; then
                                                kill -s "$TEST_SIGNAL" "$PPID"
                                            fi
                                            ;;
                                    esac
                                    ;;
                            esac
                            '''))
                    os.chmod(wrapper, 0o755)
                    environment.update(TEST_BOUNDARY=boundary, TEST_SIGNAL=signal)
                    result = subprocess.run(
                        ['sh', INSTALL_SCRIPT, '--install-dir', install_dir],
                        env=environment, text=True, capture_output=True, timeout=20,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    with open(old_file) as stream:
                        self.assertEqual(stream.read(), 'old')

    def test_refuses_unmanaged_and_symlink_install_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            home, _, environment = self._create_fixture(directory)
            unmanaged = os.path.join(directory, 'unmanaged')
            os.mkdir(unmanaged)
            symlink = os.path.join(directory, 'link')
            os.symlink(unmanaged, symlink)
            for target in (home, unmanaged, symlink, '/opt'):
                with self.subTest(target=target):
                    result = subprocess.run(
                        ['sh', INSTALL_SCRIPT, '--install-dir', target],
                        env=environment, text=True, capture_output=True, timeout=20,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('refusing install directory', result.stderr)
            self.assertTrue(os.path.isdir(unmanaged))


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
        source_root = os.path.join(directory, "source", "basaltwater-test")
        fake_bin = os.path.join(directory, "bin")
        log_path = os.path.join(directory, "calls.jsonl")
        os.makedirs(fake_home)
        os.makedirs(source_root)
        os.makedirs(fake_bin)

        fake_cli = os.path.join(source_root, "basaltwater.py")
        with open(fake_cli, "w", encoding="utf-8") as file_obj:
            file_obj.write(textwrap.dedent(
                """\
                from __future__ import annotations
                import json
                import os
                import sys

                with open(os.environ["BASALTWATER_TEST_LOG"], "a", encoding="utf-8") as log:
                    log.write(json.dumps(sys.argv[1:]) + "\\n")
                if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
                    if os.environ.get("BASALTWATER_TEST_BOOTSTRAP_FAIL") == "1":
                        raise SystemExit(7)
                    launcher_dir = os.path.join(os.environ["HOME"], ".local", "bin")
                    os.makedirs(launcher_dir, exist_ok=True)
                    launcher = os.path.join(launcher_dir, "basaltw")
                    with open(launcher, "w", encoding="utf-8") as output:
                        output.write("#!/bin/sh\\nexit 0\\n")
                    os.chmod(launcher, 0o755)
                """
            ))

        git_environment = os.environ.copy()
        git_environment.update({
            "GIT_AUTHOR_NAME": "basaltwater tests",
            "GIT_AUTHOR_EMAIL": "tests@example.invalid",
            "GIT_COMMITTER_NAME": "basaltwater tests",
            "GIT_COMMITTER_EMAIL": "tests@example.invalid",
        })
        for git_args in [
            ["git", "init", "--initial-branch=main", source_root],
            ["git", "-C", source_root, "add", "basaltwater.py"],
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
                f'printf "%s:x:%s:%s::%s:%s\\n" "$2" "$(id -u)" "$(id -g)" {fake_home!r} '
                '"${BASALTWATER_TEST_SHELL:-/bin/bash}"\n'
            )
        os.chmod(getent_path, 0o755)

        id_path = os.path.join(fake_bin, "id")
        with open(id_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ "${BASALTWATER_TEST_ROOT:-0}" = "1" ]; then\n'
                '    case "$1" in\n'
                '        -un) printf "root\\n" ;;\n'
                '        -u) printf "0\\n" ;;\n'
                '        *) exec /usr/bin/id "$@" ;;\n'
                '    esac\n'
                '    exit 0\n'
                "fi\n"
                'if [ "${BASALTWATER_TEST_NON_ROOT:-0}" = "1" ]; then\n'
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
                'if [ -n "${BASALTWATER_TEST_SUDO_LOG:-}" ]; then\n'
                '    printf "%s\\n" "$*" >> "$BASALTWATER_TEST_SUDO_LOG"\n'
                "fi\n"
                'exec "$@"\n'
            )
        os.chmod(sudo_path, 0o755)

        sed_path = os.path.join(fake_bin, "sed")
        with open(sed_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ "$#" -eq 3 ] && [ "$1" = "-n" ] && [ "$2" = "s/^ID=//p" ] && [ "$3" = "/etc/os-release" ]; then\n'
                '    printf "%s\\n" "${BASALTWATER_TEST_OS_ID:-debian}"\n'
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
        for name in (
            "BASALTWATER_CHANNEL", "BASALTWATER_REF", "BASALTWATER_REPOSITORY_URL",
            "BASALTWATER_CHANNEL", "BASALTWATER_REF", "BASALTWATER_REPOSITORY_URL",
        ):
            environment.pop(name, None)
        environment["PATH"] = os.pathsep.join((
            fake_bin,
            command_bin,
            environment.get("PATH", ""),
        ))
        environment["BASALTWATER_REPOSITORY_URL"] = source_root
        environment["BASALTWATER_TEST_LOG"] = log_path
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
            self.assertTrue(os.path.isfile(os.path.join(install_dir, "basaltwater.py")))
            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            with open(os.path.join(install_dir, ".basaltwater", "channel.json"), encoding="utf-8") as file_obj:
                self.assertEqual(json.load(file_obj)["channel"], "dev")
            self.assertTrue(os.access(
                os.path.join(fake_home, ".local", "bin", "basaltw"),
                os.X_OK,
            ))
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(calls[0][0], "bootstrap")
            self.assertEqual(
                calls[1],
                ["setup", "server_dev", "10.0.0.50", "agent", "--dry-run"],
            )

    def test_cachyos_installer_bootstraps_without_retired_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            _, log_path, environment = self._create_fixture(directory)
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
            install_dir = os.path.join(directory, "home", ".local", "share", "basaltwater")
            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(log_path, encoding="utf-8") as file_obj:
                calls = [json.loads(line) for line in file_obj]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "bootstrap")

    def test_installer_refuses_retired_data_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home, log_path, environment = self._create_fixture(directory)
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
            old = os.path.join(home, ".config", "infra_tools")
            os.makedirs(old)
            secret = os.path.join(old, "credentials.json")
            with open(secret, "w") as stream:
                stream.write("private")
            target = os.path.join(directory, "installed")
            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", target],
                env=environment, text=True, capture_output=True, timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("intermediate version", result.stderr)
            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(log_path))
            with open(secret) as stream:
                self.assertEqual(stream.read(), "private")

    def test_cachyos_installer_preserves_existing_t3_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, _log_path, environment = self._create_fixture(directory)
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
            install_dir = os.path.join(fake_home, ".local", "share", "basaltwater")
            t3_data = os.path.join(install_dir, "cachyos-t3", "state.json")
            os.makedirs(os.path.dirname(t3_data))
            with open(t3_data, "w", encoding="utf-8") as file_obj:
                file_obj.write("preserve me")

            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with open(t3_data, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "preserve me")
            rerun = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            with open(t3_data, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "preserve me")

    def test_cachyos_installer_rejects_other_unmanaged_data_beside_t3(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, _log_path, environment = self._create_fixture(directory)
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
            install_dir = os.path.join(fake_home, ".local", "share", "basaltwater")
            os.makedirs(os.path.join(install_dir, "cachyos-t3"))
            other_data = os.path.join(install_dir, "other.txt")
            with open(other_data, "w", encoding="utf-8") as file_obj:
                file_obj.write("keep me")

            result = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing install directory", result.stderr)
            with open(other_data, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "keep me")


    def test_empty_new_installer_settings_do_not_fall_back(self):
        for name in ("CHANNEL", "REPOSITORY_URL"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                _, _, environment = self._create_fixture(directory)
                environment[f"BASALTWATER_{name}"] = ""
                environment.setdefault(f"BASALTWATER_{name}", "dev")
                install_dir = os.path.join(directory, "installed")
                result = subprocess.run(
                    ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                    env=environment, text=True, capture_output=True, timeout=20,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(os.path.exists(install_dir))



    def test_root_install_uses_target_home_for_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, _log_path, environment = self._create_fixture(directory)
            environment["BASALTWATER_TEST_ROOT"] = "1"
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
                os.path.join(fake_home, ".local", "bin", "basaltw"),
                os.X_OK,
            ))

    def test_unsupported_host_prompts_and_skips_system_package_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_home, log_path, environment = self._create_fixture(directory)
            environment["BASALTWATER_TEST_ROOT"] = "1"
            environment["BASALTWATER_TEST_OS_ID"] = "fedora"
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
            environment["BASALTWATER_TEST_ROOT"] = "1"
            environment["BASALTWATER_TEST_OS_ID"] = "fedora"
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
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
            environment.pop("SSH_CONNECTION", None)
            environment.pop("SSH_TTY", None)
            sudo_log = os.path.join(directory, "sudo.log")
            environment["BASALTWATER_TEST_SUDO_LOG"] = sudo_log
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
            self.assertEqual(calls[0][0], "bootstrap")
            self.assertEqual(calls[1], ["setup", "agent_cachyos", "localhost", "testuser", "--node"])

    def test_cachyos_rejects_other_local_profiles_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            _home, log_path, environment = self._create_fixture(directory)
            environment.update(BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos")
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
            environment["BASALTWATER_TEST_ROOT"] = "1"
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
            environment["BASALTWATER_TEST_NON_ROOT"] = "1"
            sudo_log_path = os.path.join(directory, "sudo.log")
            environment["BASALTWATER_TEST_SUDO_LOG"] = sudo_log_path
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
            self.assertTrue(os.path.isfile(os.path.join(install_dir, "basaltwater.py")))
            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            self.assertTrue(os.access(
                os.path.join(fake_home, ".local", "bin", "basaltw"),
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
            environment["BASALTWATER_TEST_NON_ROOT"] = "1"
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
            environment["BASALTWATER_TEST_NON_ROOT"] = "1"
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
            self._mark_managed(install_dir)
            with open(os.path.join(install_dir, "old-version"), "w", encoding="utf-8") as file_obj:
                file_obj.write("old")
            old_state = os.path.join(install_dir, "state")
            os.makedirs(old_state)
            with open(os.path.join(old_state, "setup.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            for data_name in ("deployments", "worktrees"):
                data_dir = os.path.join(install_dir, data_name)
                os.makedirs(data_dir)
                with open(os.path.join(data_dir, "saved.txt"), "w", encoding="utf-8") as file_obj:
                    file_obj.write(data_name)

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
            for data_name in ("deployments", "worktrees"):
                with open(os.path.join(install_dir, data_name, "saved.txt"), encoding="utf-8") as file_obj:
                    self.assertEqual(file_obj.read(), data_name)

            self.assertTrue(os.path.isdir(os.path.join(install_dir, ".git")))
            self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o700)

            rerun = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            for data_name in ("deployments", "worktrees"):
                with open(os.path.join(install_dir, data_name, "saved.txt"), encoding="utf-8") as file_obj:
                    self.assertEqual(file_obj.read(), data_name)

            with open(os.path.join(install_dir, "basaltwater.py"), "a", encoding="utf-8") as file_obj:
                file_obj.write("# local source change\n")
            changed_source = subprocess.run(
                ["sh", INSTALL_SCRIPT, "--install-dir", install_dir],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(changed_source.returncode, 0)
            self.assertIn("local changes", changed_source.stderr)

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
            with open(os.path.join(install_dir, ".basaltwater", "channel.json"), encoding="utf-8") as file_obj:
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
            environment["BASALTWATER_TEST_BOOTSTRAP_FAIL"] = "1"
            install_dir = os.path.join(directory, "installed")
            os.makedirs(install_dir)
            self._mark_managed(install_dir)
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
        self.assertIn('installer=$(mktemp)', help_result.stdout)
        self.assertIn('cleanup() { rm -f -- "$installer"; }', help_result.stdout)
        self.assertIn("trap cleanup EXIT", help_result.stdout)
        self.assertNotIn("| sh", help_result.stdout)
        self.assertNotIn("sudo sh -s", help_result.stdout)
        self.assertNotIn("wget -qO-", help_result.stdout)

    def test_plan_does_not_download_bootstrap_or_create_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            home, log, environment = self._create_fixture(directory)
            target = os.path.join(directory, "installed")
            result = subprocess.run([
                "sh", INSTALL_SCRIPT, "--plan", "--install-dir", target,
                "--local-setup", "control_plane",
            ], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No packages, source, launcher, or setup changed", result.stdout)
            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(log))
            self.assertEqual(os.listdir(home), [])

    def _documented_installer_examples(self):
        for filename in ("README.md", "docs/INSTALLATION.md", "docs/CACHYOS.md"):
            document = Path(PROJECT_ROOT, filename).read_text()
            for index, example in enumerate(re.findall(r"```(?:sh|bash|fish)\n(.*?)```", document, re.S)):
                if "installer=$(mktemp)" not in example:
                    continue
                yield f"{filename}:{index}", example
        help_result = subprocess.run(["sh", INSTALL_SCRIPT, "--help"], text=True,
                                     capture_output=True, check=True, timeout=10)
        yield "install.sh --help", help_result.stdout.split("(copy the whole block):\n", 1)[1]

    def test_documented_installer_blocks_have_valid_shell_syntax(self):
        for label, example in self._documented_installer_examples():
            with self.subTest(example=label):
                result = subprocess.run(["sh", "-n"], input=example, text=True,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("fish"), "fish required for CachyOS shell checks")
    def test_cachyos_docs_and_skills_have_valid_fish_syntax(self):
        root = Path(PROJECT_ROOT)
        documents = sorted((root / "docs").glob("CACHYOS*.md"))
        documents.extend(sorted((root / "common/agent_skills").glob("basaltwater-cachyos-*/**/*.md")))
        for document in documents:
            for language, example in re.findall(r"```(sh|bash|fish)\n(.*?)```", document.read_text(), re.S):
                with self.subTest(document=document.relative_to(root), example=example):
                    self.assertEqual(language, "fish", "CachyOS command examples should use fish")
                    result = subprocess.run(["fish", "--no-config", "--no-execute"], input=example,
                                            text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_documented_download_cleans_up_and_preserves_failure_status(self):
        self._check_documented_downloads("sh")

    @unittest.skipUnless(shutil.which("fish"), "fish required for CachyOS shell checks")
    def test_documented_downloads_run_in_fish(self):
        self._check_documented_downloads("fish")

    @unittest.skipUnless(shutil.which("fish"), "fish required for CachyOS shell checks")
    def test_cachyos_quick_starts_install_and_select_fish_without_elevation(self):
        examples = [(label, example) for label, example in self._documented_installer_examples()
                    if label.startswith("docs/CACHYOS.md")]
        self.assertEqual(len(examples), 2)
        for label, example in examples:
            with self.subTest(example=label), tempfile.TemporaryDirectory() as directory:
                home, log_path, environment = self._create_fixture(directory)
                environment.update(
                    BASALTWATER_TEST_NON_ROOT="1", BASALTWATER_TEST_OS_ID="cachyos",
                    BASALTWATER_TEST_SHELL="/usr/bin/fish", HOME=home,
                    XDG_DATA_HOME=os.path.join(home, ".local", "share"),
                    XDG_CONFIG_HOME=os.path.join(home, ".config"),
                    TEST_INSTALL_SCRIPT=INSTALL_SCRIPT, TMPDIR=directory,
                    BASALTWATER_TEST_SUDO_LOG=os.path.join(directory, "sudo.log"),
                )
                environment.pop("SSH_CONNECTION", None)
                environment.pop("SSH_TTY", None)
                curl = Path(directory, "command-bin", "curl")
                curl.write_text('#!/bin/sh\nfor arg do target=$arg; done\n'
                                'cp "$TEST_INSTALL_SCRIPT" "$target"\n')
                result = subprocess.run(["fish", "--no-config", "-c", example],
                                        env=environment, text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in Path(log_path).read_text().splitlines()]
                self.assertEqual(calls[0], ["bootstrap", "--shell", "fish", "--user", "testuser",
                                            "--skip-system-packages"])
                selection = ["setup", "agent_cachyos", "localhost", "testuser"]
                if "--t3code-desktop" in example:
                    selection.append("--t3code-desktop")
                selection.extend(["--node", "--python", "--git-lfs"])
                self.assertEqual(calls[1:], [selection])
                self.assertTrue(Path(home, ".local", "bin", "basaltw").is_file())
                self.assertFalse(Path(directory, "sudo.log").exists())

    def _check_documented_downloads(self, shell):
        for label, example in self._documented_installer_examples():
            for temporary_status, download_status, installer_status in (
                (0, 0, 0), (0, 0, 7), (0, 22, 0), (73, 0, 0),
            ):
                with self.subTest(shell=shell, example=label, download_status=download_status,
                                  installer_status=installer_status, temporary_status=temporary_status), \
                        tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    # Cleanup must quote a path containing spaces and shell punctuation.
                    temporary = root / "temp ' downloads"
                    temporary.mkdir()
                    payload = root / "payload"
                    payload.write_text('#!/bin/sh\nprintf ran > "$TEST_RAN"\n'
                                       'for arg do printf "%s\\n" "$arg"; done > "$TEST_ARGS"\n'
                                       'exit "$TEST_INSTALLER_STATUS"\n')
                    curl = root / "curl"
                    curl.write_text('#!/bin/sh\nfor arg do target=$arg; done\n'
                                    'cp "$TEST_PAYLOAD" "$target"\nexit "$TEST_DOWNLOAD_STATUS"\n')
                    curl.chmod(0o755)
                    if temporary_status:
                        mktemp = root / "mktemp"
                        mktemp.write_text(f'#!/bin/sh\nexit {temporary_status}\n')
                        mktemp.chmod(0o755)
                    sudo = root / "sudo"
                    sudo.write_text('#!/bin/sh\nexec "$@"\n')
                    sudo.chmod(0o755)
                    command = [shell, "-c", example] if shell == "sh" else [shell, "--no-config", "-c", example]
                    result = subprocess.run(command, text=True, capture_output=True, env={
                        **os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                        "HOME": str(root), "USER": "testuser", "XDG_CONFIG_HOME": str(root / "config"),
                        "TMPDIR": str(temporary), "TEST_PAYLOAD": str(payload),
                        "TEST_RAN": str(root / "ran"), "TEST_ARGS": str(root / "args"),
                        "TEST_DOWNLOAD_STATUS": str(download_status),
                        "TEST_INSTALLER_STATUS": str(installer_status),
                    }, timeout=10)
                    self.assertEqual(result.returncode, temporary_status or download_status or installer_status,
                                     result.stderr)
                    self.assertEqual(list(temporary.iterdir()), [])
                    ran = temporary_status == download_status == 0
                    self.assertEqual((root / "ran").exists(), ran)
                    if ran and label.startswith("docs/CACHYOS.md"):
                        expected = ["--local-setup", "agent_cachyos"]
                        if "--t3code-desktop" in example:
                            expected.append("--t3code-desktop")
                        expected.extend(["--node", "--python", "--git-lfs"])
                        self.assertEqual((root / "args").read_text().splitlines(), expected)


if __name__ == "__main__":
    unittest.main()
