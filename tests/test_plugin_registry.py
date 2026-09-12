"""Tests for built-in plugin discovery and registry behavior."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.plugin_registry import (
    PluginDefinition,
    SystemTypeDefinition,
    build_plugin_registry,
    get_plugin_registry,
    resolve_custom_step,
    resolve_validator,
    get_system_type_definition,
    get_system_type_names,
)
from lib.system_types import get_steps_for_system_type
from lib.config import SetupConfig


class TestPluginRegistry(unittest.TestCase):
    def test_builtin_system_types_match_expected_order(self):
        self.assertEqual(
            get_system_type_names(),
            [
                "workstation_desktop",
                "pc_dev",
                "workstation_dev",
                "agent_workstation",
                "agent_cachyos",
                "agent_code_vm",
                "control_plane",
                "agent_vm",
                "server_dev",
                "server_web",
                "server_lite",
                "server_proxmox",
                "custom_steps",
            ],
        )

    def test_builtin_registry_exposes_plugin_names(self):
        registry = get_plugin_registry()
        self.assertEqual(
            [plugin.name for plugin in registry.plugins],
            ["core", "cachyos", "common", "desktop", "security", "smb", "sync", "web", "proxmox", "server", "workstation"],
        )
        self.assertEqual(
            [plugin.plugin_kind for plugin in registry.plugins],
            [
                "base",
                "composition",
                "capability",
                "capability",
                "capability",
                "capability",
                "capability",
                "capability",
                "composition",
                "composition",
                "composition",
            ],
        )

    def test_builtin_step_builders_are_plugin_owned(self):
        self.assertEqual(
            get_system_type_definition("custom_steps").step_builder,
            "plugins.core:build_custom_steps",
        )
        self.assertEqual(
            get_system_type_definition("server_dev").step_builder,
            "plugins.server:build_server_steps",
        )
        self.assertEqual(
            get_system_type_definition("server_proxmox").step_builder,
            "plugins.proxmox:build_server_proxmox_steps",
        )
        self.assertEqual(
            get_system_type_definition("workstation_dev").step_builder,
            "plugins.workstation:build_workstation_steps",
        )
        self.assertEqual(
            get_system_type_definition("agent_vm").step_builder,
            "plugins.server:build_server_steps",
        )
        self.assertEqual(
            get_system_type_definition("agent_workstation").step_builder,
            "plugins.workstation:build_workstation_steps",
        )
        self.assertEqual(
            get_system_type_definition("agent_code_vm").step_builder,
            "plugins.workstation:build_workstation_steps",
        )

    def test_system_type_metadata_drives_defaults(self):
        system_type = get_system_type_definition("pc_dev")
        self.assertTrue(system_type.include_desktop)
        self.assertTrue(system_type.include_cli_tools)
        self.assertTrue(system_type.include_pc_dev_apps)
        self.assertTrue(system_type.default_install_office)
        self.assertTrue(system_type.default_enable_smbclient)
        self.assertEqual(system_type.default_browser, "firefox")
        self.assertIsNotNone(system_type.step_builder)

    def test_workstation_profiles_use_debian_browser_default(self):
        for profile in (
            "workstation_desktop",
            "pc_dev",
            "workstation_dev",
            "agent_workstation",
            "agent_code_vm",
        ):
            with self.subTest(profile=profile):
                system_type = get_system_type_definition(profile)
                self.assertEqual(system_type.default_browser, "firefox")

    def test_agent_profiles_declare_narrow_provider_defaults(self):
        for profile in ("agent_vm", "agent_workstation", "agent_code_vm"):
            with self.subTest(profile=profile):
                system_type = get_system_type_definition(profile)
                self.assertEqual(system_type.default_agent_tools, ("gh", "codex"))

    def test_agent_code_vm_declares_full_coding_defaults(self):
        system_type = get_system_type_definition("agent_code_vm")

        self.assertEqual(system_type.default_editor, "geany")
        self.assertEqual(system_type.default_web_interfaces, ("t3code",))
        self.assertIsNone(system_type.default_browser_automation)
        self.assertTrue(system_type.default_enable_rdp)
        self.assertEqual(system_type.default_git_access, "read-write")
        self.assertFalse(hasattr(system_type, "default_web_interface_sources"))
        self.assertFalse(hasattr(system_type, "default_rdp_sources"))

    def test_control_plane_profile_adds_administrator_tools(self):
        config = SetupConfig(
            host="localhost",
            username="user",
            system_type="control_plane",
            include_cli_tools=True,
            include_control_plane_tools=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing CLI tools", step_names)
        self.assertIn("Installing control-plane administrator tools", step_names)

    def test_control_plane_flag_extends_desktop_profile(self):
        config = SetupConfig(
            host="localhost",
            username="user",
            system_type="workstation_dev",
            include_desktop=True,
            include_cli_tools=True,
            include_control_plane_tools=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing control-plane administrator tools", step_names)

    def test_custom_step_builder_is_plugin_registered(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="custom_steps",
            custom_steps="install_node",
        )
        steps = get_steps_for_system_type(config)
        self.assertEqual([name for name, _ in steps], ["Running install_node"])

    def test_custom_step_resolution_is_plugin_owned(self):
        self.assertIs(resolve_custom_step("install_node"), resolve_custom_step("install_node"))
        with self.assertRaisesRegex(ValueError, "Unknown step: install_ruby"):
            resolve_custom_step("install_ruby")
        self.assertTrue(callable(resolve_custom_step("configure_smb_mount")))

    def test_plugin_validator_resolution_is_plugin_owned(self):
        self.assertTrue(callable(resolve_validator("parse_sync_spec")))
        self.assertTrue(callable(resolve_validator("validate_samba_share_credentials")))

    def test_proxmox_step_builder_is_plugin_registered(self):
        config = SetupConfig(host="host", username="user", system_type="server_proxmox")
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertEqual(step_names[0], "Creating remoteusers group")
        self.assertEqual(step_names[-1], "Checking if restart required")

    def test_build_server_adds_build_user_tool_steps(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_web",
            is_build_server=True,
            install_node=True,
            install_python=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing Node.js (nvm + latest LTS + PNPM)", step_names)
        self.assertIn("Installing build-user Node.js", step_names)
        self.assertIn("Installing build-user Python tooling", step_names)
        self.assertLess(
            step_names.index("Creating build workspace directories"),
            step_names.index("Installing build-user Node.js"),
        )

    def test_build_server_with_cicd_does_not_duplicate_shared_steps(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_web",
            enable_cicd=True,
            is_build_server=True,
        )

        step_names = [name for name, _ in get_steps_for_system_type(config)]
        for shared_step in (
            "Installing CI/CD dependencies",
            "Creating CI/CD user",
            "Generating webhook secret",
            "Creating default webhook configuration",
            "Creating webhook receiver service",
            "Creating CI/CD executor service",
            "Configuring nginx for webhook endpoint",
        ):
            with self.subTest(step=shared_step):
                self.assertEqual(step_names.count(shared_step), 1)

    def test_server_dev_adds_agent_vm_steps(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_dev",
            install_gh=True,
            install_codex=True,
            install_claude=True,
            install_opencode=True,
            agent_tools=["gh", "codex", "claude", "opencode"],
            web_interfaces=["t3code"],
            agent_payload=True,
            agent_repos=["https://github.com/user/repo.git"],
            include_cli_tools=True,
            include_desktop=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing GitHub CLI", step_names)
        self.assertIn("Installing agent VM management command", step_names)
        self.assertIn("Installing Codex CLI", step_names)
        self.assertIn("Installing Claude Code", step_names)
        self.assertIn("Installing OpenCode", step_names)
        self.assertIn("Installing T3 Code web interface", step_names)
        self.assertIn("Copying agent tool configuration", step_names)
        self.assertIn("Cloning agent repositories on target", step_names)
        self.assertLess(
            step_names.index("Installing CLI tools"),
            step_names.index("Installing GitHub CLI"),
        )
        self.assertLess(
            step_names.index("Copying agent tool configuration"),
            step_names.index("Installing T3 Code web interface"),
        )

    def test_t3_web_with_git_access_installs_git_without_cli_bundle(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="server_lite",
            agent_tools=["codex"],
            install_codex=True,
            web_interfaces=["t3code"],
            git_access="read-write",
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing Git for agent repositories", step_names)
        self.assertLess(
            step_names.index("Installing Git for agent repositories"),
            step_names.index("Installing T3 Code web interface"),
        )

    def test_vm_storage_is_prepared_before_agent_repository_clones(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            machine_type="vm",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["agent-data", "bulk-lvm", "128G"],
            ],
            storage_mounts=[["agent-data", "/srv/agent-workspace"]],
            agent_workspace="/srv/agent-workspace",
            agent_repos=["https://github.com/user/repo.git"],
        )

        step_names = [name for name, _ in get_steps_for_system_type(config)]
        storage_index = step_names.index("Preparing VM data storage")
        self.assertGreater(storage_index, step_names.index("Setting up user"))
        self.assertLess(
            storage_index,
            step_names.index("Cloning agent repositories on target"),
        )

    def test_home_storage_is_prepared_before_setup_user(self):
        config = SetupConfig(
            host="host",
            username="agent",
            system_type="server_dev",
            machine_type="vm",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["home-data", "local-lvm", "32G"],
            ],
            storage_mounts=[["home-data", "/home"]],
        )

        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertLess(
            step_names.index("Preparing VM data storage"),
            step_names.index("Setting up user"),
        )

    def test_workstation_dev_adds_agent_vm_steps(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="workstation_dev",
            agent_tools=["gh", "codex", "claude", "opencode"],
            agent_payload=True,
            agent_repos=["https://github.com/user/repo.git"],
            include_desktop=True,
            include_cli_tools=True,
        )
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Installing GitHub CLI", step_names)
        self.assertIn("Installing agent VM management command", step_names)
        self.assertIn("Installing Codex CLI", step_names)
        self.assertIn("Installing Claude Code", step_names)
        self.assertIn("Installing OpenCode", step_names)
        self.assertIn("Copying agent tool configuration", step_names)
        self.assertIn("Cloning agent repositories on target", step_names)
        self.assertLess(
            step_names.index("Installing CLI tools"),
            step_names.index("Installing GitHub CLI"),
        )

    def test_workstation_dev_editor_is_explicit(self):
        minimal = SetupConfig(
            host="host",
            username="user",
            system_type="workstation_dev",
            include_desktop=True,
            include_workstation_dev_apps=True,
            browser="firefox",
        )
        selected = SetupConfig(
            host="host",
            username="user",
            system_type="workstation_dev",
            include_desktop=True,
            include_workstation_dev_apps=True,
            browser="firefox",
            editor="geany",
        )

        minimal_steps = [name for name, _ in get_steps_for_system_type(minimal)]
        selected_steps = [name for name, _ in get_steps_for_system_type(selected)]

        self.assertIn("Installing browser", minimal_steps)
        self.assertNotIn("Installing workstation editor", minimal_steps)
        self.assertIn("Installing workstation editor", selected_steps)

    def test_workstation_desktop_uses_explicit_browser_step(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="workstation_desktop",
            include_desktop=True,
            include_desktop_apps=True,
            browser="firefox",
        )

        step_names = [name for name, _ in get_steps_for_system_type(config)]

        self.assertIn("Installing browser", step_names)
        self.assertNotIn("Installing desktop applications", step_names)
        self.assertNotIn("Installing Office", step_names)
        self.assertNotIn("Installing Remmina", step_names)

    def test_pc_dev_keeps_productivity_steps_without_app_bundle(self):
        config = SetupConfig(
            host="host",
            username="user",
            system_type="pc_dev",
            include_desktop=True,
            include_pc_dev_apps=True,
            install_office=True,
            enable_smbclient=True,
            browser="firefox",
        )

        step_names = [name for name, _ in get_steps_for_system_type(config)]

        self.assertIn("Installing browser", step_names)
        self.assertIn("Installing Office", step_names)
        self.assertIn("Installing Remmina", step_names)
        self.assertIn("Installing SMB client packages", step_names)
        self.assertNotIn("Installing desktop applications", step_names)

    def test_duplicate_plugin_names_fail(self):
        plugin = PluginDefinition(name="dup", module="plugins.one")
        with self.assertRaisesRegex(ValueError, "Duplicate plugin name"):
            build_plugin_registry([plugin, plugin])

    def test_unknown_plugin_dependency_fails(self):
        plugin = PluginDefinition(
            name="workstation",
            module="plugins.workstation",
            plugin_kind="composition",
            dependencies=("core",),
            system_types=(
                SystemTypeDefinition(name="workstation_dev", description="Workstation", order=10),
            ),
        )
        with self.assertRaisesRegex(ValueError, "depends on unknown plugin"):
            build_plugin_registry([plugin])

    def test_duplicate_system_type_names_fail(self):
        plugin_one = PluginDefinition(
            name="one",
            module="plugins.one",
            system_types=(
                SystemTypeDefinition(name="server_lite", description="One", order=10),
            ),
        )
        plugin_two = PluginDefinition(
            name="two",
            module="plugins.two",
            system_types=(
                SystemTypeDefinition(name="server_lite", description="Two", order=20),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Duplicate system type"):
            build_plugin_registry([plugin_one, plugin_two])

    def test_duplicate_custom_step_names_fail(self):
        plugin_one = PluginDefinition(
            name="one",
            module="plugins.one",
            custom_steps=("duplicate_step",),
            custom_step_provider="plugins.one:get_custom_step_functions",
        )
        plugin_two = PluginDefinition(
            name="two",
            module="plugins.two",
            custom_steps=("duplicate_step",),
            custom_step_provider="plugins.two:get_custom_step_functions",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate custom step"):
            build_plugin_registry([plugin_one, plugin_two])

    def test_duplicate_validator_names_fail(self):
        plugin_one = PluginDefinition(
            name="one",
            module="plugins.one",
            validators=("parse_sync_spec",),
            validator_provider="plugins.one:get_validator_functions",
        )
        plugin_two = PluginDefinition(
            name="two",
            module="plugins.two",
            validators=("parse_sync_spec",),
            validator_provider="plugins.two:get_validator_functions",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate validator"):
            build_plugin_registry([plugin_one, plugin_two])

    def test_cyclic_dependencies_fail(self):
        plugin_one = PluginDefinition(
            name="one",
            module="plugins.one",
            plugin_kind="composition",
            dependencies=("two",),
            system_types=(
                SystemTypeDefinition(name="one_system", description="One", order=10),
            ),
        )
        plugin_two = PluginDefinition(
            name="two",
            module="plugins.two",
            plugin_kind="composition",
            dependencies=("one",),
            system_types=(
                SystemTypeDefinition(name="two_system", description="Two", order=20),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Cyclic plugin dependencies"):
            build_plugin_registry([plugin_one, plugin_two])

    def test_base_plugins_cannot_declare_dependencies(self):
        plugin = PluginDefinition(
            name="base-with-deps",
            module="plugins.base_with_deps",
            plugin_kind="base",
            dependencies=("core",),
        )
        with self.assertRaisesRegex(ValueError, "Base plugin .* cannot declare dependencies"):
            build_plugin_registry([plugin])

    def test_capability_plugins_cannot_register_system_types(self):
        plugin = PluginDefinition(
            name="capability-with-system",
            module="plugins.capability_with_system",
            plugin_kind="capability",
            system_types=(
                SystemTypeDefinition(name="bad_system", description="Bad", order=10),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Capability plugin .* cannot register system types"):
            build_plugin_registry([plugin])

    def test_composition_plugins_must_register_system_types(self):
        plugin = PluginDefinition(
            name="composition-without-system",
            module="plugins.composition_without_system",
            plugin_kind="composition",
            dependencies=("core",),
        )
        core = PluginDefinition(name="core", module="plugins.core")
        with self.assertRaisesRegex(
            ValueError,
            "Composition plugin .* must register at least one system type",
        ):
            build_plugin_registry([core, plugin])

    def test_custom_step_provider_requires_custom_steps(self):
        plugin = PluginDefinition(
            name="provider-only",
            module="plugins.provider_only",
            custom_step_provider="plugins.provider_only:get_custom_step_functions",
        )
        with self.assertRaisesRegex(ValueError, "must declare both custom_steps and custom_step_provider"):
            build_plugin_registry([plugin])

    def test_validator_provider_requires_validators(self):
        plugin = PluginDefinition(
            name="validator-only",
            module="plugins.validator_only",
            validator_provider="plugins.validator_only:get_validator_functions",
        )
        with self.assertRaisesRegex(ValueError, "must declare both validators and validator_provider"):
            build_plugin_registry([plugin])
