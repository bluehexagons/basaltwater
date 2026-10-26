"""Tests for built-in plugin discovery and registry behavior."""

from __future__ import annotations

import base64
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.plugin_registry import (
    PluginDefinition,
    SystemTypeDefinition,
    build_plugin_registry,
    resolve_custom_step,
    get_system_type_definition,
)
from lib.system_types import get_steps_for_system_type
from lib.config import SetupConfig


class TestPluginRegistry(unittest.TestCase):
    def test_smb_client_packages_precede_mounts_once_on_headless_and_desktop(self):
        from desktop.desktop_environment_steps import install_smbclient
        from plugins.post_setup import build_post_setup_steps

        for profile, desktop in (("server_lite", False), ("server_web", False), ("pc_dev", True)):
            with self.subTest(profile=profile):
                config = SetupConfig(
                    host="192.0.2.10", username="user", system_type=profile,
                    include_desktop=desktop, enable_smbclient=True,
                    smb_mounts=[["/mnt/share", "192.0.2.11", "user:fixture", "share", "/"]],
                )
                steps = get_steps_for_system_type(config) + build_post_setup_steps(config, lambda *_: ("", ""))
                functions = [function for _, function in steps]
                self.assertEqual(functions.count(install_smbclient), 1)
                names = [name for name, _ in steps]
                self.assertLess(names.index("Installing SMB client packages"), names.index("Configuring SMB mounts"))

    def test_smb_client_package_selection_respects_desktop(self):
        from desktop import desktop_environment_steps as module

        for desktop in (False, True):
            with self.subTest(desktop=desktop), \
                    patch.object(module, "is_package_installed", return_value=False), \
                    patch.object(module, "run") as run, patch.dict(os.environ):
                module.install_smbclient(SetupConfig(host="host", username="user", system_type="server_lite", include_desktop=desktop))
                command = run.call_args.args[0]
                self.assertIn("cifs-utils", command)
                self.assertIn("smbclient", command)
                self.assertEqual("gvfs-backends" in command, desktop)

    def test_web_rdp_uses_one_shared_firewall_before_desktop_setup(self):
        from security.steps import configure_firewall, configure_firewall_web

        config = SetupConfig(
            host="192.0.2.10", username="user", system_type="server_web",
            include_web_firewall=True, include_web_server=True,
            enable_rdp=True, include_desktop=True, rdp_allowed_sources=["192.0.2.0/24"],
        )
        steps = get_steps_for_system_type(config)
        functions = [function for _, function in steps]
        self.assertEqual(functions.count(configure_firewall), 1)
        self.assertNotIn(configure_firewall_web, functions)
        firewall_index = functions.index(configure_firewall)
        rdp_indices = [index for index, (_, function) in enumerate(steps) if "rdp" in function.__name__]
        self.assertTrue(rdp_indices)
        self.assertLess(firewall_index, min(rdp_indices))

    def test_registry_orders_dependencies_and_system_types_independently(self):
        core = PluginDefinition(name="z_core", module="plugins.core")
        app = PluginDefinition(
            name="a_app", module="plugins.app", plugin_kind="composition",
            dependencies=("z_core",),
            system_types=(
                SystemTypeDefinition(name="z_later", description="Later", order=20),
                SystemTypeDefinition(name="b_first", description="First B", order=10),
                SystemTypeDefinition(name="a_first", description="First A", order=10),
            ),
        )
        for definitions in ([app, core], [core, app]):
            with self.subTest(input_order=[plugin.name for plugin in definitions]):
                registry = build_plugin_registry(definitions)
                self.assertEqual([plugin.name for plugin in registry.plugins], ["z_core", "a_app"])
                self.assertEqual([system.name for system in registry.system_types],
                                 ["a_first", "b_first", "z_later"])

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

    def test_unknown_custom_step_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown step: install_ruby"):
            resolve_custom_step("install_ruby")

    def test_proxmox_step_builder_is_plugin_registered(self):
        config = SetupConfig(host="host", username="user", system_type="server_proxmox")
        step_names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertEqual(step_names[0], "Checking Proxmox release and repositories")
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

    def test_managed_build_enrollment_selects_only_the_shared_worker(self):
        from common.build_host_steps import configure_build_host
        from lib.build_manifest import DEBIAN_PLATFORM
        from lib.build_protocol import DEFAULT_LIMITS

        wire_key = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(32)
        enrollment = {
            "schema": 1,
            "policy": {"schema": 1, "principals": {"controller": {"projects": {
                "site": {"platform": dict(DEBIAN_PLATFORM), "runtimes": [],
                         "limits": dict(DEFAULT_LIMITS)},
            }}}},
            "keys": {"controller": "ssh-ed25519 " + base64.b64encode(wire_key).decode("ascii")},
            "max_records": 16, "max_bytes": 4 * 1024**3,
        }
        for profile in ("server_lite", "server_web"):
            with self.subTest(profile=profile):
                config = SetupConfig(
                    host="host", username="user", system_type=profile,
                    machine_type="vm", is_build_server=True,
                    build_host_enrollment=enrollment,
                )
                steps = get_steps_for_system_type(config)
                self.assertEqual([function for _, function in steps].count(configure_build_host), 1)
                names = [name for name, _ in steps]
                self.assertNotIn("Creating CI/CD executor service", names)
                self.assertNotIn("Generating deploy SSH key", names)
                self.assertIs(resolve_custom_step("configure_build_host"), configure_build_host)

                config.enable_cicd = True
                with self.assertRaisesRegex(ValueError, "shared-capacity adapter"):
                    get_steps_for_system_type(config)

    def test_explicit_build_host_disable_selects_managed_teardown(self):
        from common.build_host_steps import configure_build_host

        config = SetupConfig(
            host="host", username="user", system_type="server_lite",
            machine_type="vm", disable_build_host=True,
        )
        steps = get_steps_for_system_type(config)
        names = [name for name, _ in steps]
        self.assertIn("Removing managed on-demand build host access", names)
        self.assertEqual([function for _, function in steps].count(configure_build_host), 1)
        self.assertNotIn("Creating CI/CD executor service", names)

    def test_managed_target_enrollment_and_disable_replace_legacy_app_access(self):
        from common.build_target_steps import configure_build_target
        from tests.test_build_target_setup import enrollment

        for profile in ("server_lite", "server_web", "workstation_dev"):
            for disable in (False, True):
                with self.subTest(profile=profile, disable=disable):
                    config = SetupConfig(
                        host="host", username="user", system_type=profile,
                        machine_type="vm", is_app_server=True,
                        build_target_enrollment=None if disable else enrollment(),
                        disable_build_target=disable,
                    )
                    steps = get_steps_for_system_type(config)
                    self.assertEqual([function for _, function in steps].count(configure_build_target), 1)
                    names = [name for name, _ in steps]
                    self.assertNotIn("Configuring deploy sudoers", names)
                    self.assertNotIn("Configuring deploy SSH access", names)
                    self.assertNotIn("Installing app server dependencies", names)
        self.assertIs(resolve_custom_step("configure_build_target"), configure_build_target)

    def test_target_enrollment_omission_keeps_ordinary_app_server_steps(self):
        config = SetupConfig(host="host", username="user", system_type="server_web", is_app_server=True)
        names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertIn("Configuring deploy sudoers", names)
        self.assertIn("Configuring deploy SSH access", names)
        self.assertNotIn("Configuring managed target deployment access", names)

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
        self.assertIn("Updating managed agent tools", step_names)
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

    def test_task_worker_requires_codex_across_server_and_workstation_profiles(self):
        for profile in ("server_lite", "server_web", "server_dev", "agent_vm", "agent_workstation", "agent_code_vm"):
            for tools in ([], ["claude"], ["codex"]):
                for panel_port in (None, 443):
                    with self.subTest(profile=profile, tools=tools, panel_port=panel_port):
                        config = SetupConfig(
                            host="example.test", username="user", system_type=profile,
                            agent_tools=tools, web_panel_port=panel_port,
                        )
                        names = [name for name, _ in get_steps_for_system_type(config)]
                        self.assertEqual("Configuring independent agent task worker" in names, "codex" in tools)
                        self.assertEqual("Configuring Basaltwater web panel" in names, panel_port is not None)

    def test_multi_service_monitoring_panel_does_not_select_agent_setup(self):
        config = SetupConfig(
            host="192.168.0.51", username="gitadmin", system_type="server_lite", machine_type="vm",
            agent_tools=[], enable_ssl=True, web_panel_port=443,
            gogs=[":3000", "/srv/gogs"], enable_syncthing=True, syncthing_root="/srv/syncthing",
            enable_samba=True, samba_shares=[["write", "cluster", "/srv/cluster", "share-admin,cluster"]],
        )
        names = [name for name, _ in get_steps_for_system_type(config)]
        self.assertFalse(config.has_agent_features())
        self.assertIn("Configuring Basaltwater web panel", names)
        self.assertIn("Setting up Gogs service", names)
        self.assertTrue(any("Syncthing" in name for name in names))
        for name in ("Installing Git for agent repositories", "Installing Codex CLI",
                     "Installing agent VM management command", "Configuring independent agent task worker"):
            self.assertNotIn(name, names)

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
        self.assertIn("Updating managed agent tools", step_names)
        self.assertIn("Copying agent tool configuration", step_names)
        self.assertIn("Cloning agent repositories on target", step_names)
        self.assertLess(
            step_names.index("Installing CLI tools"),
            step_names.index("Installing GitHub CLI"),
        )

    def test_workstation_profiles_compose_requested_services_once(self):
        expected = (
            "Setting up antistatic lobby server",
            "Setting up antistatic-db service",
            "Setting up Gogs service",
            "Configuring Gogs auto-update",
        )
        for profile in ("workstation_desktop", "pc_dev", "workstation_dev",
                        "agent_workstation", "agent_code_vm"):
            for enabled in (False, True):
                with self.subTest(profile=profile, enabled=enabled):
                    config = SetupConfig(
                        host="192.0.2.10", username="user", system_type=profile,
                        antistatic_server=":8080" if enabled else None,
                        antistatic_db=":8081" if enabled else None,
                        gogs=[":3000"] if enabled else None,
                    )
                    names = [name for name, _ in get_steps_for_system_type(config)]
                    for name in expected:
                        self.assertEqual(names.count(name), int(enabled))
                        if enabled:
                            self.assertLess(names.index(name), names.index("Running setup maintenance"))

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
