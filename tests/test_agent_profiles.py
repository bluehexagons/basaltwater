"""Tests for opinionated agent-host system type defaults."""

from __future__ import annotations

import unittest
from argparse import Namespace

from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type


def _setup_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "host": "192.0.2.10",
        "username": "agent",
        "timezone": "UTC",
        "desktop": None,
        "install_office": None,
        "enable_rdp": None,
    }
    values.update(overrides)
    return Namespace(**values)


class TestAgentProfiles(unittest.TestCase):
    def test_agent_vm_defaults_to_github_cli_and_codex(self) -> None:
        config = SetupConfig.from_args(_setup_args(), "agent_vm")

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertTrue(config.include_cli_tools)
        self.assertFalse(config.include_desktop)
        self.assertNotIn("--agent-tool", " ".join(config.to_setup_command()))
        self.assertIn("--agent-tool gh", config.to_remote_args())
        self.assertIn("--agent-tool codex", config.to_remote_args())

        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing CLI tools", step_names)
        self.assertIn("Installing GitHub CLI", step_names)
        self.assertIn("Installing Codex CLI", step_names)
        self.assertIn("Updating managed agent tools", step_names)
        self.assertIn("Ensuring python command alias", step_names)
        self.assertNotIn("Installing desktop environment", step_names)

    def test_agent_workstation_adds_firefox_desktop(self) -> None:
        config = SetupConfig.from_args(_setup_args(), "agent_workstation")

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertEqual(config.browser, "firefox")
        self.assertTrue(config.include_desktop)

        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing desktop environment", step_names)
        self.assertIn("Installing browser", step_names)
        self.assertIn("Installing GitHub CLI", step_names)
        self.assertIn("Installing Codex CLI", step_names)
        self.assertIn("Updating managed agent tools", step_names)

    def test_agent_code_vm_adds_t3_and_geany_without_browser_fallback(self) -> None:
        config = SetupConfig.from_args(_setup_args(), "agent_code_vm")

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertEqual(config.browser, "firefox")
        self.assertEqual(config.editor, "geany")
        self.assertEqual(config.web_interfaces, ["t3code"])
        self.assertIsNone(config.browser_automation)
        self.assertTrue(config.include_desktop)
        self.assertTrue(config.enable_rdp)
        self.assertTrue(config.install_node)
        self.assertFalse(config.install_data_analysis_tools)
        self.assertFalse(config.install_av_tools)
        self.assertFalse(config.install_gl_tools)
        self.assertFalse(config.install_go)
        self.assertEqual(config.git_access, "read-write")
        self.assertIsNone(config.web_interface_sources)
        self.assertIsNone(config.rdp_allowed_sources)
        self.assertEqual(config.web_interface_host, "127.0.0.1")
        self.assertEqual(config.device_pairing_providers, ["t3code"])

        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing workstation editor", step_names)
        self.assertIn("Installing T3 Code web interface", step_names)
        self.assertNotIn("Installing agent browser automation", step_names)
        self.assertNotIn("Installing data-analysis tools", step_names)
        self.assertNotIn("Installing image, audio, and video tools", step_names)
        self.assertNotIn("Installing OpenGL tools", step_names)

        command = " ".join(config.to_setup_command())
        self.assertNotIn("--editor", command)
        self.assertNotIn("--web-interface", command)
        self.assertNotIn("--browser-automation", command)
        self.assertNotIn("--web-interface-source", command)
        self.assertNotIn("--rdp-source", command)

    def test_agent_code_vm_accepts_explicit_playwright_fallback(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(browser_automation="playwright"),
            "agent_code_vm",
        )

        self.assertEqual(config.browser_automation, "playwright")
        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing agent browser automation", step_names)
        self.assertIn("--browser-automation playwright", config.to_setup_command())

    def test_agent_code_vm_accepts_optional_go_runtime(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(install_go=True),
            "agent_code_vm",
        )

        self.assertTrue(config.install_node)
        self.assertTrue(config.install_go)

    def test_agent_code_vm_accepts_opt_in_data_analysis_bundle(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(install_data_analysis_tools=True),
            "agent_code_vm",
        )

        self.assertTrue(config.install_data_analysis_tools)
        self.assertTrue(config.install_python)
        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing Python tooling (aliases + uv)", step_names)
        self.assertIn("Installing data-analysis tools", step_names)
        self.assertIn("--data-analysis", config.to_remote_args())
        self.assertIn("--data-analysis", config.to_setup_command())

    def test_agent_code_vm_accepts_opt_in_av_tools(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(install_av_tools=True),
            "agent_code_vm",
        )

        self.assertTrue(config.install_av_tools)
        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing image, audio, and video tools", step_names)
        self.assertIn("--av-tools", config.to_remote_args())
        self.assertIn("--av-tools", config.to_setup_command())

    def test_agent_code_vm_accepts_opt_in_gl_tools(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(install_gl_tools=True),
            "agent_code_vm",
        )

        self.assertTrue(config.install_gl_tools)
        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing OpenGL tools", step_names)
        self.assertIn("--gl-tools", config.to_remote_args())
        self.assertIn("--gl-tools", config.to_setup_command())

    def test_agent_code_vm_lan_access_is_explicit(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(host="192.168.50.10", lan_access=True),
            "agent_code_vm",
        )

        self.assertTrue(config.lan_access)
        self.assertEqual(
            config.effective_rdp_sources(),
            ["192.168.50.0/24"],
        )
        self.assertEqual(
            config.effective_web_interface_sources(),
            config.effective_rdp_sources(),
        )

    def test_t3code_ready_adds_headless_runtime_defaults(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(t3code_ready=True),
            "server_dev",
        )

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertEqual(config.web_interfaces, ["t3code"])
        self.assertEqual(config.device_pairing_providers, ["t3code"])
        self.assertEqual(config.git_access, "read-write")
        self.assertTrue(config.install_node)
        self.assertIn("--t3code-ready", config.to_remote_args())

    def test_t3code_ready_respects_explicit_agent_and_pairing_opt_outs(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(
                t3code_ready=True,
                no_agent_tools=["gh"],
                disable_device_pairing=True,
            ),
            "server_dev",
        )

        self.assertEqual(config.selected_agent_tools(), ["codex"])
        self.assertIsNone(config.device_pairing_providers)
        self.assertTrue(config.install_node)

    def test_agent_code_vm_explicit_editor_replaces_geany(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(editor="vscode"),
            "agent_code_vm",
        )

        self.assertEqual(config.editor, "vscode")
        self.assertIn("--editor vscode", " ".join(config.to_setup_command()))

    def test_explicit_agent_tool_list_augments_profile_defaults(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(agent_tools=["claude", "opencode"]),
            "agent_vm",
        )

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex", "claude", "opencode"])
        command = " ".join(config.to_setup_command())
        self.assertIn("--agent-tool claude", command)
        self.assertIn("--agent-tool opencode", command)
        self.assertNotIn("--agent-tool gh", command)
        self.assertNotIn("--agent-tool codex", command)

    def test_agent_tool_defaults_can_be_disabled(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(agent_tools=["opencode"], no_agent_tools=["gh"]),
            "agent_vm",
        )

        self.assertEqual(config.selected_agent_tools(), ["codex", "opencode"])
        self.assertIn("--no-agent-tool gh", " ".join(config.to_setup_command()))

    def test_empty_agent_tool_list_keeps_profile_defaults(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(agent_tools=[]),
            "agent_vm",
        )

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])

    def test_saved_agent_profile_restores_defaults_when_tools_are_missing(self) -> None:
        config = SetupConfig.from_dict(
            "192.0.2.10",
            "agent_vm",
            {"username": "agent"},
        )

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])

    def test_saved_agent_code_vm_restores_capability_defaults(self) -> None:
        config = SetupConfig.from_dict(
            "192.0.2.10",
            "agent_code_vm",
            {
                "username": "agent",
                "include_desktop": True,
                "include_cli_tools": True,
                "include_workstation_dev_apps": True,
                "browser": "firefox",
            },
        )

        self.assertEqual(config.editor, "geany")
        self.assertEqual(config.web_interfaces, ["t3code"])
        self.assertIsNone(config.browser_automation)
        self.assertTrue(config.install_node)
        self.assertFalse(config.install_go)
        self.assertIsNone(config.web_interface_sources)
        self.assertIsNone(config.rdp_allowed_sources)


if __name__ == "__main__":
    unittest.main()
