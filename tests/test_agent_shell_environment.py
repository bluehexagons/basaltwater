"""Tests for developer-tool access from non-interactive agent shells."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.agent_steps import _ensure_agent_shell_path
from common.common_steps import _ensure_user_tool_shell_environment, _run_as_login_user
from lib.config import SetupConfig


class UserToolShellEnvironmentTest(unittest.TestCase):
    def test_first_run_does_not_load_the_callers_shell_hook_or_nvm_directory(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as caller_home:
            foreign_hook = os.path.join(caller_home, "shell-env.sh")
            with open(foreign_hook, "w", encoding="utf-8") as stream:
                stream.write("printf 'foreign shell hook was loaded\\n' >&2\n")
            os.chmod(foreign_hook, 0o000)
            environment = {
                "HOME": caller_home, "PATH": "/usr/bin:/bin", "BASH_ENV": foreign_hook,
                "ENV": foreign_hook, "NVM_DIR": caller_home + "/.nvm",
                "NVM_BIN": caller_home + "/.nvm/bin", "NVM_INC": caller_home + "/.nvm/include",
            }
            with patch("common.common_steps.run") as run:
                _run_as_login_user(
                    "agent", home,
                    'printf "%s\\n" "$HOME" "${BASH_ENV-unset}" "${ENV-unset}" '
                    '"${NVM_DIR-unset}" "${NVM_BIN-unset}" "${NVM_INC-unset}"',
                )
            arguments = shlex.split(run.call_args.args[0])
            self.assertEqual(arguments[:4], ["runuser", "-u", "agent", "--"])
            # Mock the account switch; execute only its env/Bash payload against
            # temporary homes so neither real account nor root files are used.
            result = subprocess.run(arguments[4:], env=environment, cwd=home,
                                    capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout.splitlines(), [home, "unset", "unset", "unset", "unset", "unset"])
            self.assertEqual(os.stat(foreign_hook).st_mode & 0o777, 0o000)
            self.assertEqual(os.listdir(home), [])

    def test_login_and_nested_noninteractive_shells_load_user_tools(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as caller_home:
            bashrc = os.path.join(home, ".bashrc")
            profile = os.path.join(home, ".profile")
            with open(bashrc, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "case $- in\n"
                    "    *i*) ;;\n"
                    "    *) return ;;\n"
                    "esac\n"
                )
            with open(profile, "w", encoding="utf-8") as file_obj:
                file_obj.write("# login setup\n")

            nvm_bin = os.path.join(home, ".nvm", "versions", "node", "v24", "bin")
            opencode_bin = os.path.join(home, ".opencode", "bin")
            local_bin = os.path.join(home, ".local", "bin")
            os.makedirs(nvm_bin)
            os.makedirs(opencode_bin)
            os.makedirs(local_bin)
            with open(os.path.join(home, ".nvm", "nvm.sh"), "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "nvm() { :; }\n"
                    f'export NVM_BIN="{nvm_bin}"\n'
                    'export PATH="$NVM_BIN:$PATH"\n'
                )
            for path in (
                os.path.join(nvm_bin, "node"),
                os.path.join(nvm_bin, "pnpm"),
                os.path.join(opencode_bin, "opencode"),
                os.path.join(local_bin, "uv"),
            ):
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("#!/bin/sh\n")
                os.chmod(path, 0o755)

            with patch("common.common_steps.run"):
                _ensure_user_tool_shell_environment("agent", home)

            command = (
                "command -v nvm; command -v node; command -v pnpm; "
                "command -v opencode; command -v uv; "
                'printf "%s\\n" "$NVM_DIR" "$BASH_ENV"; '
                "bash -c 'command -v nvm; command -v node; command -v opencode; "
                'printf "%s\\n" "$NVM_DIR" "$BASH_ENV"\''
            )
            foreign_hook = os.path.join(caller_home, "shell-env.sh")
            with open(foreign_hook, "w", encoding="utf-8") as stream:
                stream.write("printf 'foreign shell hook was loaded\\n' >&2\n")
            caller_environment = {
                "HOME": caller_home, "PATH": "/usr/bin:/bin", "BASH_ENV": foreign_hook,
                "ENV": foreign_hook, "NVM_DIR": caller_home + "/.nvm",
                "NVM_BIN": caller_home + "/.nvm/bin", "NVM_INC": caller_home + "/.nvm/include",
            }
            with patch("common.common_steps.run") as run:
                _run_as_login_user("agent", home, command)
            switched_arguments = shlex.split(run.call_args.args[0])[4:]
            for arguments, environment in (
                (["/bin/bash", "-lc", command], {"HOME": home, "PATH": "/usr/bin:/bin"}),
                (switched_arguments, caller_environment),
            ):
                with self.subTest(arguments=arguments[:2]):
                    result = subprocess.run(
                        arguments, env=environment, cwd=home, check=False,
                        capture_output=True, text=True, timeout=10,
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    output = result.stdout.splitlines()
                    self.assertEqual(output.count("nvm"), 2)
                    self.assertEqual(output.count(os.path.join(nvm_bin, "node")), 2)
                    self.assertIn(os.path.join(nvm_bin, "pnpm"), output)
                    self.assertEqual(output.count(os.path.join(opencode_bin, "opencode")), 2)
                    self.assertIn(os.path.join(local_bin, "uv"), output)
                    self.assertEqual(output.count(os.path.join(home, ".nvm")), 2)
                    self.assertEqual(output.count(os.path.join(home, ".local/share/basaltwater/shell-env.sh")), 2)
            with open(bashrc, encoding="utf-8") as file_obj:
                self.assertTrue(
                    file_obj.read().startswith("# basaltwater user tool environment\n")
                )

    def test_existing_bash_profile_is_the_managed_login_file(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            profile = os.path.join(home, ".profile")
            bash_profile = os.path.join(home, ".bash_profile")
            with open(profile, "w", encoding="utf-8") as file_obj:
                file_obj.write("# ignored by Bash when .bash_profile exists\n")
            with open(bash_profile, "w", encoding="utf-8") as file_obj:
                file_obj.write("# preferred Bash login file\n")

            with patch("common.common_steps.run"):
                _ensure_user_tool_shell_environment("agent", home)

            with open(profile, encoding="utf-8") as file_obj:
                self.assertNotIn("basaltwater user tool environment", file_obj.read())
            with open(bash_profile, encoding="utf-8") as file_obj:
                self.assertIn("basaltwater user tool environment", file_obj.read())

    def test_agent_tools_use_the_shared_shell_environment(self) -> None:
        config = SetupConfig(host="host", username="agent", system_type="agent_vm")
        account = SimpleNamespace(pw_dir="/home/agent")
        with (
            patch("common.agent_steps.pwd.getpwnam", return_value=account),
            patch("common.agent_steps._ensure_user_tool_shell_environment") as ensure,
        ):
            _ensure_agent_shell_path(config)

        ensure.assert_called_once_with("agent", "/home/agent")


if __name__ == "__main__":
    unittest.main()
