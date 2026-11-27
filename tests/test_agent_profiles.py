"""Tests for opinionated agent-host system type defaults."""

from __future__ import annotations

import argparse
import shlex
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lib.arg_parser import add_setup_arguments, create_setup_argument_parser
from lib.config import SetupConfig
from lib.plugin_registry import get_system_type_definition, get_system_type_names
from lib.system_types import get_steps_for_system_type
from lib.validation import validate_web_interface_settings


def _setup_args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "host": "192.0.2.10",
        "username": "agent",
        "timezone": "UTC",
        "desktop": None,
        "install_office": None,
        "enable_rdp": None,
        "agent_tools": ["codex"],
    }
    values.update(overrides)
    return Namespace(**values)


class TestAgentProfiles(unittest.TestCase):
    def test_simple_provider_flags_combine_and_roundtrip(self) -> None:
        parser = create_setup_argument_parser("test")
        for flags, expected in (
            (["--claude"], ["gh", "claude"]),
            (["--codex", "--claude", "--opencode"], ["gh", "codex", "claude", "opencode"]),
            (["--claude", "--agent-tool", "opencode,claude", "--codex", "--claude"],
             ["gh", "codex", "claude", "opencode"]),
        ):
            with self.subTest(flags=flags):
                args = parser.parse_args(["192.0.2.10", "agent", *flags])
                config = SetupConfig.from_args(args, "agent_vm")
                config.validate_agent_provider_selection()
                self.assertEqual(config.selected_agent_tools(), expected)
                saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
                remote_parser = create_setup_argument_parser("remote", for_remote=True)
                remote_args = remote_parser.parse_args(shlex.split(" ".join(saved.to_remote_args())))
                remote_args.host = config.host
                self.assertEqual(SetupConfig.from_args(remote_args, "agent_vm").selected_agent_tools(), expected)
        self.assertIsNone(parser.parse_args(["192.0.2.10", "agent"]).agent_tools)
        removed = parser.parse_args(["192.0.2.10", "agent", "--claude", "--no-agent-tool", "claude"])
        with self.assertRaisesRegex(ValueError, "at least one provider"):
            SetupConfig.from_args(removed, "agent_vm").validate_agent_provider_selection()

    def test_cli_rejects_missing_provider_before_target_work(self) -> None:
        import basaltwater

        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["setup", "agent_vm", "192.0.2.10", "agent", "--dry-run"])
        with patch.object(basaltwater, "_load_cached_provisioning_metadata") as target:
            self.assertEqual(basaltwater.run_setup_command(args), 1)
        target.assert_not_called()

    def test_agent_setups_require_explicit_providers(self) -> None:
        for profile in ("agent_vm", "agent_workstation", "agent_cachyos"):
            with self.subTest(profile=profile):
                config = SetupConfig.from_args(_setup_args(agent_tools=None), profile)
                self.assertEqual(config.selected_agent_tools(), ["gh"])
                with self.assertRaisesRegex(ValueError, "at least one provider"):
                    config.validate_agent_provider_selection()

    def test_claude_only_roundtrips_without_codex_login_or_install(self) -> None:
        parser = create_setup_argument_parser("test", for_remote=True)
        for profile in ("agent_vm", "agent_workstation", "agent_code_vm", "server_dev"):
            config = SetupConfig.from_args(_setup_args(agent_tools=["claude"], t3code_ready=True), profile)
            config.validate_agent_provider_selection()
            self.assertEqual(config.selected_agent_tools(), ["gh", "claude"])
            self.assertIsNone(config.agent_auth_source)
            saved = SetupConfig.from_dict(config.host, profile, config.to_dict())
            args = parser.parse_args(shlex.split(" ".join(saved.to_remote_args())))
            args.host = config.host
            remote = SetupConfig.from_args(args, profile)
            self.assertEqual(remote.selected_agent_tools(), ["gh", "claude"])
            names = [name for name, _ in get_steps_for_system_type(remote)]
            self.assertIn("Installing Claude Code", names)
            self.assertNotIn("Installing Codex CLI", names)

    def test_blender_is_opt_in_for_debian_server_and_workstation_profiles(self) -> None:
        from desktop.apps_steps import install_blender

        for profile in get_system_type_names():
            if get_system_type_definition(profile).step_builder not in {
                "plugins.server:build_server_steps",
                "plugins.workstation:build_workstation_steps",
            }:
                continue
            with self.subTest(profile=profile):
                config = SetupConfig.from_args(_setup_args(), profile)
                self.assertNotIn(install_blender, [step for _, step in get_steps_for_system_type(config)])
                config = SetupConfig.from_args(_setup_args(install_blender=True), profile)
                steps = [step for _, step in get_steps_for_system_type(config)]
                self.assertEqual(steps.count(install_blender), 1)
                self.assertEqual(config.include_desktop, get_system_type_definition(profile).include_desktop)
                restored = SetupConfig.from_dict(config.host, profile, config.to_dict())
                self.assertTrue(restored.install_blender)
                self.assertIn(install_blender, [step for _, step in get_steps_for_system_type(restored)])

    def test_blender_flag_round_trips_through_setup_and_remote_parsers(self) -> None:
        parser = argparse.ArgumentParser()
        add_setup_arguments(parser, include_system_type=True)
        args = parser.parse_args(["agent_vm", "192.0.2.10", "agent", "--agent-tool", "codex", "--blender"])
        config = SetupConfig.from_args(args, args.system_type)
        command = shlex.split(" ".join(config.to_setup_command()))
        restored_args = parser.parse_args(command[2:])
        restored = SetupConfig.from_args(restored_args, restored_args.system_type)
        self.assertTrue(restored.install_blender)
        remote_parser = create_setup_argument_parser("test", for_remote=True)
        remote_args = remote_parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
        remote_args.host = config.host
        remote = SetupConfig.from_args(remote_args, remote_args.system_type)
        self.assertTrue(remote.install_blender)
        self.assertFalse(remote.include_desktop)
        disabled = parser.parse_args(["agent_vm", "192.0.2.10", "agent", "--blender", "--no-blender"])
        self.assertFalse(SetupConfig.from_args(disabled, disabled.system_type).install_blender)

    def test_agent_vm_installs_explicit_codex_with_github_cli(self) -> None:
        config = SetupConfig.from_args(_setup_args(), "agent_vm")

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertTrue(config.include_cli_tools)
        self.assertFalse(config.include_desktop)
        self.assertIn("--agent-tool codex", " ".join(config.to_setup_command()))
        self.assertIn("--agent-tool gh", config.to_remote_args())
        self.assertIn("--agent-tool codex", config.to_remote_args())

        step_names = [name for name, _step in get_steps_for_system_type(config)]
        self.assertIn("Installing CLI tools", step_names)
        self.assertIn("Installing GitHub CLI", step_names)
        self.assertIn("Installing Codex CLI", step_names)
        self.assertIn("Updating managed agent tools", step_names)
        self.assertIn("Ensuring python command alias", step_names)
        self.assertNotIn("Installing desktop environment", step_names)

    def test_lite_vm_keeps_the_interactive_baseline_across_setup_and_refresh(self) -> None:
        import basaltwater

        parser, _, _ = basaltwater.create_basaltwater_parser()
        remote_parser = create_setup_argument_parser("remote", for_remote=True)
        for t3_flags, pairing in (
            ([], False),
            (["--web-interface", "t3code"], True),
            (["--web-interface", "t3code", "--web-interface-source", "192.168.1.42/32"], True),
            (["--web-interface", "t3code", "--no-device-pairing"], False),
        ):
            with self.subTest(t3_flags=t3_flags):
                args = parser.parse_args([
                    "setup", "agent_vm_lite", "192.0.2.10", "agent", "--machine", "vm",
                    "--codex", *t3_flags,
                ])
                config = SetupConfig.from_args(args, args.system_type)
                config.validate_agent_provider_selection()
                saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
                self.assertEqual(config.agent_auth_source, "login")
                self.assertEqual(saved.agent_auth_source, "login")
                remote_args = remote_parser.parse_args(shlex.split(" ".join(saved.to_remote_args())))
                remote_args.host = config.host
                remote = SetupConfig.from_args(remote_args, remote_args.system_type)
                replay_args = parser.parse_args(shlex.split(" ".join(saved.to_setup_command()))[1:])
                replay = SetupConfig.from_args(replay_args, replay_args.system_type)
                for selected in (config, saved, remote, replay):
                    validate_web_interface_settings(selected)
                    self.assertEqual(selected.selected_agent_tools(), ["codex"])
                    self.assertEqual(selected.effective_web_ports(), [8443] if pairing else [])
                    self.assertEqual(selected.publish_t3_https, pairing)
                    self.assertEqual(selected.device_pairing_providers, ["t3code"] if pairing else None)
                    self.assertFalse(selected.include_desktop)
                    self.assertFalse(selected.enable_rdp)
                    self.assertEqual(bool(selected.install_node), bool(t3_flags))
                    steps = {name for name, _ in get_steps_for_system_type(selected)}
                    self.assertTrue({
                        "Configuring firewall", "Configuring automatic security updates",
                        "Installing CLI tools", "Installing Codex CLI",
                    } <= steps)
                    self.assertFalse({
                        "Configuring independent agent task worker",
                        "Reconciling existing agent browser automation",
                        "Installing agent browser automation",
                    } & steps)
                    self.assertEqual("Installing T3 Code web interface" in steps, bool(t3_flags))
                    if t3_flags:
                        self.assertEqual(selected.web_interface_host, "127.0.0.1")
                    if "--web-interface-source" in t3_flags:
                        self.assertEqual(selected.effective_web_interface_sources(), ["192.168.1.42/32"])

    def test_lite_vm_preserves_a_saved_t3_install_without_a_portal(self) -> None:
        config = SetupConfig.from_args(_setup_args(web_interfaces=["t3code"]), "agent_vm_lite")
        previous = config.to_dict()
        previous["device_pairing_providers"] = None
        restored = SetupConfig.from_dict(config.host, config.system_type, previous)
        self.assertTrue(restored.disable_device_pairing)
        self.assertFalse(restored.publish_t3_https)
        self.assertIn("--no-device-pairing", restored.to_setup_command())

    def test_lite_vm_setup_prompts_for_default_portal_credentials_only_with_t3(self) -> None:
        from lib.interactive_setup import prompt_for_missing_passwords

        for interfaces, disable_pairing, expected in (
            (None, False, None), (["t3code"], False, "fixture-password"),
            (["t3code"], True, None),
        ):
            with self.subTest(interfaces=interfaces, disable_pairing=disable_pairing):
                args = _setup_args(
                    web_interfaces=interfaces, disable_device_pairing=disable_pairing,
                    device_pairing_auth_password=None,
                )
                with (
                    patch("lib.interactive_setup.sys.stdin.isatty", return_value=True),
                    patch("lib.interactive_setup.sys.stdout.isatty", return_value=True),
                    patch("builtins.input", return_value=""),
                    patch("lib.interactive_setup.getpass.getpass", return_value="fixture-password"),
                ):
                    prompt_for_missing_passwords(args, "agent_vm_lite")
                self.assertEqual(args.device_pairing_auth_password, expected)

    def test_lite_vm_patch_enables_the_portal_with_a_new_t3_install(self) -> None:
        import basaltwater

        cached = SetupConfig.from_args(_setup_args(), "agent_vm_lite")
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args([
            "patch", cached.host, cached.username, "--web-interface", "t3code",
        ])
        with (
            patch("basaltwater.load_setup_command", return_value=cached),
            patch("basaltwater._execute_patch_config", return_value=0) as apply,
        ):
            self.assertEqual(basaltwater.run_patch_command(args), 0)
        updated = apply.call_args.args[0]
        self.assertEqual(updated.selected_agent_tools(), ["codex"])
        self.assertEqual(updated.device_pairing_providers, ["t3code"])

    def test_lite_vm_patch_disables_a_saved_t3_service_and_portal(self) -> None:
        import basaltwater

        cached = SetupConfig.from_args(_setup_args(
            web_interfaces=["t3code"], web_interface_host="0.0.0.0",
            web_interface_sources=["192.168.1.42/32"], web_interface_port=7381,
            device_pairing_port=7382,
        ), "agent_vm_lite")
        validate_web_interface_settings(cached)
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["patch", cached.host, cached.username, "--no-web-interface"])
        with (
            patch("basaltwater.load_setup_command", return_value=cached),
            patch("basaltwater._execute_patch_config", return_value=0) as apply,
        ):
            self.assertEqual(basaltwater.run_patch_command(args), 0)
        updated = apply.call_args.args[0]
        validate_web_interface_settings(updated)
        self.assertIsNone(updated.web_interfaces)
        self.assertIsNone(updated.device_pairing_providers)
        self.assertEqual(updated.effective_web_ports(), [])
        steps = {name for name, _ in get_steps_for_system_type(updated)}
        self.assertIn("Disabling managed T3 Code", steps)
        self.assertNotIn("Installing T3 Code web interface", steps)

    def test_lite_vm_allows_explicit_web_additions(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(
                web_interfaces=["t3code"], device_pairing_providers=["t3code"],
                web_ports=[8082], browser_automation="playwright",
            ),
            "agent_vm_lite",
        )
        self.assertTrue(config.publish_t3_https)
        self.assertEqual(config.effective_web_ports(), [8082, 8443])
        steps = {name for name, _ in get_steps_for_system_type(config)}
        self.assertIn("Installing agent browser automation", steps)

    def test_lite_vm_provisioning_passes_a_positional_prefix_to_the_provider(self) -> None:
        import basaltwater

        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args([
            "setup", "agent_vm_lite", "192.168.40.10/22", "agent", "--codex",
            "--agent-auth", "none", "--provision-on", "pve1", "--memory", "2G",
            "--cores", "2", "--storage", "root", "16G", "--dry-run",
        ])
        with TemporaryDirectory() as directory:
            key = Path(directory) / "id_ed25519"
            key.write_text("test private key\n", encoding="utf-8")
            key.with_suffix(".pub").write_text("test public key\n", encoding="utf-8")
            with (
                patch("basaltwater.load_setup_command", return_value=None),
                patch("lib.setup_common.find_proxmox_host", return_value=None),
                patch("lib.setup_common.resolve_guest_ssh_key", return_value=str(key)),
                patch("lib.proxmox_vm.provision_vm") as provision,
                patch("basaltwater.run_remote_setup", return_value=0),
                redirect_stdout(StringIO()) as output,
            ):
                result = basaltwater.run_setup_command(args)
            self.assertEqual(result, 0, output.getvalue())
        provision.assert_called_once()
        config = provision.call_args.args[0]
        self.assertEqual(config.host, "192.168.40.10")
        self.assertEqual(config.static_ipv4, "192.168.40.10/22")

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

    def test_agent_code_vm_adds_t3_without_a_default_graphical_editor(self) -> None:
        config = SetupConfig.from_args(_setup_args(), "agent_code_vm")

        self.assertEqual(config.selected_agent_tools(), ["gh", "codex"])
        self.assertEqual(config.browser, "firefox")
        self.assertIsNone(config.editor)
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
        self.assertNotIn("Installing workstation editor", step_names)
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

    def test_browser_opt_out_survives_remote_setup_and_saved_refresh(self) -> None:
        from plugins.common import extend_agent_steps

        parser = create_setup_argument_parser("test", for_remote=True)
        for profile in ("agent_vm", "agent_code_vm"):
            config = SetupConfig.from_args(
                _setup_args(disable_browser_automation=True), profile,
            )
            saved = SetupConfig.from_dict(config.host, profile, config.to_dict())
            for phase, selected in (("setup", config), ("refresh", saved)):
                with self.subTest(profile=profile, phase=phase):
                    args = parser.parse_args(shlex.split(" ".join(selected.to_remote_args())))
                    args.host = selected.host
                    remote = SetupConfig.from_args(args, args.system_type)
                    self.assertTrue(remote.disable_browser_automation)
                    self.assertIsNone(remote.browser_automation)
                    steps = []
                    extend_agent_steps(remote, steps)
                    self.assertFalse(any("browser automation" in name for name, _ in steps))

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

    def test_agent_code_vm_explicit_editor_selects_vscode(self) -> None:
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

        self.assertEqual(config.selected_agent_tools(), ["gh", "claude", "opencode"])
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

        self.assertEqual(config.selected_agent_tools(), ["opencode"])
        self.assertIn("--no-agent-tool gh", " ".join(config.to_setup_command()))

    def test_empty_agent_tool_list_keeps_profile_defaults(self) -> None:
        config = SetupConfig.from_args(
            _setup_args(agent_tools=[]),
            "agent_vm",
        )

        self.assertEqual(config.selected_agent_tools(), ["gh"])

    def test_saved_agent_profile_restores_defaults_when_tools_are_missing(self) -> None:
        config = SetupConfig.from_dict(
            "192.0.2.10",
            "agent_vm",
            {"username": "agent"},
        )

        self.assertEqual(config.selected_agent_tools(), ["gh"])

    def test_saved_agent_code_vm_restores_capability_defaults(self) -> None:
        config = SetupConfig.from_dict(
            "192.0.2.10",
            "agent_code_vm",
            {
                "username": "agent",
                "agent_tools": ["gh", "codex"],
                "include_desktop": True,
                "include_cli_tools": True,
                "include_workstation_dev_apps": True,
                "browser": "firefox",
            },
        )

        self.assertIsNone(config.editor)
        self.assertEqual(config.web_interfaces, ["t3code"])
        self.assertIsNone(config.browser_automation)
        self.assertTrue(config.install_node)
        self.assertFalse(config.install_go)
        self.assertIsNone(config.web_interface_sources)
        self.assertIsNone(config.rdp_allowed_sources)


if __name__ == "__main__":
    unittest.main()
