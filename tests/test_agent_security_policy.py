"""Tests for the managed Codex session security policy."""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.agent_security_steps import (
    configure_agent_user_security,
    configure_codex_security_policy,
)
from lib.config import SetupConfig
from plugins.common import (
    extend_agent_steps,
    get_common_steps,
    get_custom_step_functions,
)


class TestCodexSecurityPolicy(unittest.TestCase):
    def _config(
        self, *, hardened: bool = False, nopasswd: bool = False
    ) -> SetupConfig:
        return SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
            install_codex=True,
            harden_agent=hardened,
            nopasswd=nopasswd,
        )

    def _configure(
        self, directory: str, *, hardened: bool = False, nopasswd: bool = False
    ) -> None:
        with patch(
            "common.agent_security_steps.CODEX_SYSTEM_CONFIG_DIR", directory
        ), patch("common.agent_security_steps.os.chown"):
            configure_codex_security_policy(
                self._config(hardened=hardened, nopasswd=nopasswd)
            )

    def test_standard_policy_uses_auto_review_and_workspace_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            self._configure(policy_dir)

            with open(
                os.path.join(policy_dir, "config.toml"), "rb"
            ) as file_obj:
                config = tomllib.load(file_obj)
            requirements_path = os.path.join(policy_dir, "requirements.toml")
            self.assertFalse(os.path.exists(requirements_path))

        self.assertEqual(config["approval_policy"], "on-request")
        self.assertEqual(config["approvals_reviewer"], "auto_review")
        self.assertEqual(config["default_permissions"], ":workspace")
        self.assertFalse(config["sandbox_workspace_write"]["network_access"])

    def test_nopasswd_keeps_capable_standard_codex_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            self._configure(policy_dir, nopasswd=True)

            with open(
                os.path.join(policy_dir, "config.toml"), "rb"
            ) as file_obj:
                config = tomllib.load(file_obj)

            self.assertFalse(
                os.path.exists(os.path.join(policy_dir, "requirements.toml"))
            )

        self.assertEqual(config["approval_policy"], "on-request")
        self.assertEqual(config["approvals_reviewer"], "auto_review")
        self.assertEqual(config["default_permissions"], ":workspace")
        self.assertNotIn("allow_login_shell", config)
        self.assertNotIn("features", config)

    def test_hardened_policy_disables_approval_escalation_and_active_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            self._configure(policy_dir, hardened=True)

            with open(
                os.path.join(policy_dir, "config.toml"), "rb"
            ) as file_obj:
                config = tomllib.load(file_obj)
            with open(
                os.path.join(policy_dir, "requirements.toml"), "rb"
            ) as file_obj:
                requirements = tomllib.load(file_obj)

        self.assertEqual(config["approval_policy"], "never")
        self.assertFalse(config["allow_login_shell"])
        profile_name = config["default_permissions"]
        self.assertEqual(profile_name, "basaltwater_hardened_workspace")
        self.assertFalse(
            config["shell_environment_policy"]["ignore_default_excludes"]
        )
        self.assertEqual(requirements["allowed_approval_policies"], ["never"])
        self.assertEqual(requirements["allowed_web_search_modes"], [])
        self.assertTrue(requirements["allow_managed_hooks_only"])
        self.assertFalse(requirements["allow_browser_and_computer_use"])
        self.assertFalse(requirements["allow_remote_control"])
        self.assertTrue(
            requirements["allowed_permission_profiles"][profile_name]
        )
        self.assertNotIn(
            ":workspace", requirements["allowed_permission_profiles"]
        )
        self.assertFalse(
            requirements["permissions"][profile_name]["network"]["enabled"]
        )
        self.assertIn("~/.ssh", requirements["permissions"]["filesystem"]["deny_read"])
        self.assertIn(
            "~/.config/gh",
            requirements["permissions"]["filesystem"]["deny_read"],
        )
        self.assertIn(
            "~/.codex/auth.json",
            requirements["permissions"]["filesystem"]["deny_read"],
        )
        self.assertFalse(requirements["features"]["apps"])
        self.assertFalse(requirements["features"]["plugins"])
        self.assertFalse(requirements["features"]["browser_use"])
        self.assertEqual(requirements["mcp_servers"], {})

    def test_rerun_can_switch_an_basaltwater_owned_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            self._configure(policy_dir)
            self._configure(policy_dir, hardened=True)
            with open(
                os.path.join(policy_dir, "requirements.toml"), "rb"
            ) as file_obj:
                requirements = tomllib.load(file_obj)

        self.assertEqual(requirements["allowed_approval_policies"], ["never"])

    def test_returning_to_standard_removes_managed_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            self._configure(policy_dir, hardened=True)
            self._configure(policy_dir)

            with open(
                os.path.join(policy_dir, "config.toml"), "rb"
            ) as file_obj:
                config = tomllib.load(file_obj)

            self.assertFalse(
                os.path.exists(os.path.join(policy_dir, "requirements.toml"))
            )

        self.assertEqual(config["approval_policy"], "on-request")

    def test_standard_preserves_unmanaged_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o755)
            with open(
                os.path.join(policy_dir, "config.toml"), "w", encoding="utf-8"
            ) as file_obj:
                file_obj.write('approval_policy = "never"\n')
            os.chmod(os.path.join(policy_dir, "config.toml"), 0o644)

            self._configure(policy_dir)

            with open(
                os.path.join(policy_dir, "config.toml"),
                "r",
                encoding="utf-8",
            ) as file_obj:
                existing = file_obj.read()

        self.assertEqual(existing, 'approval_policy = "never"\n')

    def test_standard_preserves_unmanaged_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o755)
            requirements_path = os.path.join(policy_dir, "requirements.toml")
            with open(requirements_path, "w", encoding="utf-8") as file_obj:
                file_obj.write('allowed_approval_policies = ["never"]\n')
            os.chmod(requirements_path, 0o644)

            self._configure(policy_dir)

            with open(requirements_path, "r", encoding="utf-8") as file_obj:
                existing = file_obj.read()

        self.assertEqual(existing, 'allowed_approval_policies = ["never"]\n')

    def test_hardened_refuses_to_replace_unmanaged_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o755)
            with open(
                os.path.join(policy_dir, "config.toml"), "w", encoding="utf-8"
            ) as file_obj:
                file_obj.write('approval_policy = "never"\n')
            os.chmod(os.path.join(policy_dir, "config.toml"), 0o644)

            with self.assertRaisesRegex(
                RuntimeError, "Refusing to replace unmanaged Codex policy"
            ):
                self._configure(policy_dir, hardened=True)

    def test_preflights_both_policy_files_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o755)
            with open(
                os.path.join(policy_dir, "requirements.toml"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                file_obj.write('allowed_approval_policies = ["never"]\n')
            os.chmod(os.path.join(policy_dir, "requirements.toml"), 0o644)

            with self.assertRaisesRegex(
                RuntimeError, "Refusing to replace unmanaged Codex policy"
            ):
                self._configure(policy_dir, hardened=True)

            self.assertFalse(
                os.path.exists(os.path.join(policy_dir, "config.toml"))
            )

    def test_refuses_symlinked_policy_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_dir = os.path.join(temporary, "real")
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(real_dir)
            os.symlink(real_dir, policy_dir)

            with self.assertRaisesRegex(
                RuntimeError, "Refusing unsafe Codex policy directory"
            ):
                self._configure(policy_dir)

    def test_refuses_writable_policy_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o777)

            with self.assertRaisesRegex(
                RuntimeError, "Refusing unsafe Codex policy directory"
            ):
                self._configure(policy_dir)

    def test_refuses_writable_managed_policy_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            os.makedirs(policy_dir)
            os.chmod(policy_dir, 0o755)
            config_path = os.path.join(policy_dir, "config.toml")
            with open(config_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "# Managed by basaltwater coding-agent security policy.\n"
                )
            os.chmod(config_path, 0o666)

            with self.assertRaisesRegex(
                RuntimeError, "Refusing unsafe Codex policy path"
            ):
                self._configure(policy_dir, hardened=True)

    def test_hardened_writes_requirements_before_overridable_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy_dir = os.path.join(temporary, "codex")
            with patch(
                "common.agent_security_steps.CODEX_SYSTEM_CONFIG_DIR",
                policy_dir,
            ), patch(
                "common.agent_security_steps.os.chown"
            ), patch(
                "common.agent_security_steps._write_managed_codex_policy"
            ) as mock_write:
                configure_codex_security_policy(self._config(hardened=True))

        written_names = [
            os.path.basename(invocation.args[0])
            for invocation in mock_write.call_args_list
        ]
        self.assertEqual(written_names, ["requirements.toml", "config.toml"])


class TestCodexSecurityPolicySteps(unittest.TestCase):
    def test_policy_is_exported_as_a_custom_step(self):
        self.assertIs(
            get_custom_step_functions()["configure_codex_security_policy"],
            configure_codex_security_policy,
        )

    def test_user_policy_is_exported_and_runs_after_user_setup(self):
        self.assertIs(
            get_custom_step_functions()["configure_agent_user_security"],
            configure_agent_user_security,
        )
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
        )

        labels = [label for label, _step in get_common_steps(config)]

        self.assertLess(
            labels.index("Setting up user"),
            labels.index("Configuring agent user security"),
        )
        self.assertLess(
            labels.index("Configuring agent user security"),
            labels.index("Copying SSH keys to user"),
        )

    def test_policy_runs_before_t3_code_starts(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
            install_codex=True,
            web_interfaces=["t3code"],
        )
        steps = []

        extend_agent_steps(config, steps)

        labels = [label for label, _step in steps]
        self.assertLess(
            labels.index("Configuring Codex security policy"),
            labels.index("Installing T3 Code web interface"),
        )


if __name__ == "__main__":
    unittest.main()
