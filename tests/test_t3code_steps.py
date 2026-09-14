"""Tests for the T3 Code server setup path."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common.t3code_steps import (
    _active_t3_binary,
    _configure_firewall,
    _configure_t3_https,
    _install_t3_npm_shim,
    _install_t3_service,
    _rebuild_t3_native_runtime,
    _temporary_t3_loginctl_shim,
    _wait_for_t3_service,
    _write_passthrough_wrapper,
    install_t3code_web,
)
from lib.config import SetupConfig
from lib.validation import validate_web_interface_settings


class T3CodeWebTest(unittest.TestCase):
    def _config(self, **overrides) -> SetupConfig:
        values = {
            "host": "target",
            "username": "agent",
            "system_type": "server_dev",
            "agent_tools": ["codex"],
            "web_interfaces": ["t3code"],
            "web_interface_sources": ["192.168.0.0/24"],
        }
        values.update(overrides)
        return SetupConfig(**values)

    @staticmethod
    def _write_upstream_runtime(home: str, version: str = "0.0.34") -> str:
        runtime = os.path.join(home, ".t3", "runtime")
        binary = os.path.join(
            runtime,
            "versions",
            version,
            "node_modules",
            "t3",
            "dist",
            "bin.mjs",
        )
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        with open(binary, "w", encoding="utf-8") as file_obj:
            file_obj.write("#!/usr/bin/env node\n")
        os.chmod(binary, 0o755)
        with open(
            os.path.join(runtime, "service-state.json"),
            "w",
            encoding="utf-8",
        ) as file_obj:
            json.dump({"protocol": 2, "activeVersion": version}, file_obj)
        service = os.path.join(home, ".config", "systemd", "user", "t3code.service")
        os.makedirs(os.path.dirname(service), exist_ok=True)
        with open(service, "w", encoding="utf-8") as file_obj:
            file_obj.write("# upstream managed\n")
        return binary

    @staticmethod
    def _write_node_tools(home: str) -> str:
        """Create the minimal target-user Node tool directory used by tests."""

        node_bin = os.path.join(home, "node-bin")
        os.makedirs(node_bin, exist_ok=True)
        for executable in ("node", "npm"):
            path = os.path.join(node_bin, executable)
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        return node_bin

    def test_source_promotes_default_bind_to_all_interfaces(self) -> None:
        config = self._config()
        self.assertEqual(config.web_interface_host, "0.0.0.0")
        validate_web_interface_settings(config)

    def test_https_gateway_is_optional_when_managed_utility_is_unavailable(self) -> None:
        with patch("common.t3code_steps.os.path.isfile", return_value=False):
            self.assertEqual(_configure_t3_https(self._config(), 3773, None), {})

    def test_https_gateway_preserves_t3_file_upload_limit(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "listen": 8444,
                    "url": "https://target:8444/",
                }
            ),
            stderr="",
        )
        with (
            patch("common.t3code_steps.os.geteuid", return_value=0),
            patch("common.t3code_steps.os.path.isfile", return_value=True),
            patch("common.godot_web_steps.configure_internal_web_host"),
            patch(
                "common.godot_web_steps.identities_for_config",
                return_value=["target"],
            ),
            patch("common.t3code_steps.run", return_value=completed) as run_command,
        ):
            endpoints = _configure_t3_https(self._config(), 3773, None)

        self.assertEqual(
            endpoints,
            {"t3code": ("https://target:8444/", 8444)},
        )
        command = run_command.call_args.args[0]
        self.assertIn("forward add t3code", command)
        self.assertIn("--max-body-size 50m", command)

    def test_non_loopback_bind_requires_private_source(self) -> None:
        config = self._config(web_interface_host="0.0.0.0", web_interface_sources=None)
        with self.assertRaisesRegex(
            ValueError,
            "requires --access-source or --web-interface-source",
        ):
            validate_web_interface_settings(config)

    def test_loopback_bind_rejects_redundant_source_allowlist(self) -> None:
        config = self._config(web_interface_host="127.0.0.1")
        with self.assertRaisesRegex(ValueError, "requires a non-loopback"):
            validate_web_interface_settings(config)

    def test_global_source_is_rejected(self) -> None:
        config = self._config(web_interface_sources=["8.8.8.0/24"])
        with self.assertRaisesRegex(ValueError, "private or otherwise non-global"):
            validate_web_interface_settings(config)

    def test_firewall_reconciles_stale_managed_source_rules(self) -> None:
        config = self._config(
            web_interface_host="0.0.0.0",
            web_interface_sources=["10.0.0.0/24"],
        )
        state = {"allow_seen": False}

        def run_command(command: str, **_kwargs):
            if command == "ufw status numbered":
                rules = (
                    "[ 1] 3773/tcp ALLOW IN 192.168.0.0/24 "
                    "# infra_tools T3 Code 3773/tcp source 192.168.0.0/24\n"
                )
                if state["allow_seen"]:
                    rules += (
                        "[ 2] 3773/tcp ALLOW IN 10.0.0.0/24 "
                        "# infra_tools T3 Code 3773/tcp source 10.0.0.0/24\n"
                    )
                return SimpleNamespace(returncode=0, stdout=rules)
            if command.startswith("ufw allow from"):
                state["allow_seen"] = True
            return SimpleNamespace(returncode=0, stdout="")

        with patch("common.t3code_steps.run", side_effect=run_command) as mock_run:
            _configure_firewall(config, 3773, "0.0.0.0")

        self.assertTrue(
            any("ufw --force delete 1" in call.args[0] for call in mock_run.call_args_list)
        )

    def test_firewall_rejects_unmanaged_port_rule(self) -> None:
        config = self._config(web_interface_host="0.0.0.0")

        def run_command(command: str, **_kwargs):
            if command == "ufw status numbered":
                return SimpleNamespace(
                    returncode=0,
                    stdout="[ 1] 3773/tcp ALLOW IN Anywhere\n",
                )
            return SimpleNamespace(returncode=0, stdout="")

        with patch("common.t3code_steps.run", side_effect=run_command):
            with self.assertRaisesRegex(RuntimeError, "Unmanaged UFW allow rules"):
                _configure_firewall(config, 3773, "0.0.0.0")

    def test_active_binary_requires_upstream_protocol_and_exact_version(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            binary = self._write_upstream_runtime(home)
            self.assertEqual(_active_t3_binary(home), binary)
            state_file = os.path.join(home, ".t3", "runtime", "service-state.json")
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {"protocolVersion": 2, "activeVersion": "0.0.34"},
                    file_obj,
                )
            self.assertIsNone(_active_t3_binary(home))

            build_binary = self._write_upstream_runtime(
                home,
                "1.2.3-nightly.1+build.5",
            )
            self.assertEqual(_active_t3_binary(home), build_binary)

            self._write_upstream_runtime(home, "01.2.3")
            self.assertIsNone(_active_t3_binary(home))

    def test_service_install_uses_upstream_npx_update_and_managed_drop_in(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            commands: list[str] = []

            def run_as_user(_username, _home, command, **_kwargs):
                commands.append(command)
                self._write_upstream_runtime(home)
                return completed

            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps.shutil.which",
                    return_value="/bin/true",
                ),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch("common.t3code_steps._run_as_login_user", side_effect=run_as_user),
                patch("common.t3code_steps._t3_native_runtime_healthy", return_value=True),
                patch("common.t3code_steps._wait_for_t3_service"),
                patch("common.t3code_steps._user_systemctl", return_value=completed),
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                binary = _install_t3_service(
                    home,
                    "agent",
                    os.getuid(),
                    os.getgid(),
                    workspace,
                    "0.0.0.0",
                    3773,
                )

            self.assertEqual(binary, _active_t3_binary(home))
            update_command = next(
                command for command in commands if "--package=t3@latest" in command
            )
            self.assertIn("npx --yes --package=t3@latest -c", update_command)
            self.assertIn(".infra-tools-t3-loginctl-", update_command)
            self.assertIn(
                f"{home}/.local/share/infra-tools/t3-npm/bin",
                update_command,
            )
            self.assertIn('export PATH=', update_command)
            self.assertGreater(
                update_command.rindex("-u npm_config_allow_scripts"),
                update_command.index("npx --yes"),
            )
            self.assertGreater(
                update_command.rindex("t3 service update"),
                update_command.index("npx --yes"),
            )
            self.assertTrue(
                any(
                    "npm_config_strict_allow_scripts=false" in command
                    for command in commands
                )
            )
            self.assertTrue(
                all(
                    "dangerously_allow_all_scripts=true" not in command
                    for command in commands
                )
            )
            self.assertTrue(
                any(
                    "export CC=gcc" in command and "export CXX=g++" in command
                    for command in commands
                )
            )
            drop_in = os.path.join(
                home,
                ".config",
                "systemd",
                "user",
                "t3code.service.d",
                "infra-tools.conf",
            )
            with open(drop_in, encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn(f"WorkingDirectory={workspace}", content)
            self.assertIn("Environment=T3CODE_HOST=0.0.0.0", content)
            self.assertIn("Environment=T3CODE_PORT=3773", content)
            self.assertIn("Environment=CC=gcc", content)
            self.assertIn("Environment=CXX=g++", content)
            self.assertIn("Environment=npm_config_strict_allow_scripts=false", content)
            self.assertIn(
                f"Environment=PATH={home}/.local/share/infra-tools/t3-npm/bin:",
                content,
            )
            self.assertIn(
                "UnsetEnvironment=npm_config_allow_scripts NPM_CONFIG_ALLOW_SCRIPTS",
                content,
            )

    def test_npm_shim_only_allows_native_scripts_for_t3_staging(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            node_bin = os.path.join(home, "node-bin")
            os.makedirs(node_bin)
            real_npm = os.path.join(node_bin, "npm")
            with open(real_npm, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    "#!/bin/sh\n"
                    "prefix=\n"
                    "expect_prefix=false\n"
                    "for argument in \"$@\"; do\n"
                    "  if $expect_prefix; then prefix=$argument; "
                    "expect_prefix=false; continue; fi\n"
                    "  case \"$argument\" in\n"
                    "    --prefix) expect_prefix=true ;;\n"
                    "    --prefix=*) prefix=${argument#--prefix=} ;;\n"
                    "  esac\n"
                    "done\n"
                    "if [ -n \"$prefix\" ] && [ -f \"$prefix/.npmrc\" ]; then\n"
                    "  cp -- \"$prefix/.npmrc\" \"$NPM_CONFIG_LOG\"\n"
                    "fi\n"
                    "printf '%s\\n' \"$@\" > \"$NPM_ARGUMENT_LOG\"\n"
                )
            os.chmod(real_npm, 0o755)
            shim_bin, changed = _install_t3_npm_shim(
                home,
                node_bin,
                os.getuid(),
                os.getgid(),
            )
            wrapper = os.path.join(shim_bin, "npm")
            argument_log = os.path.join(home, "npm-arguments")
            config_log = os.path.join(home, "npm-config")
            environment = dict(
                os.environ,
                NPM_ARGUMENT_LOG=argument_log,
                NPM_CONFIG_LOG=config_log,
            )

            subprocess.run(
                [wrapper, "install", "example@1.0.0", "--prefix", home],
                check=True,
                env=environment,
            )
            with open(argument_log, encoding="utf-8") as file_obj:
                ordinary_arguments = file_obj.read().splitlines()

            staging = os.path.join(
                home,
                ".t3",
                "runtime",
                "versions",
                ".staging-update_123",
            )
            os.makedirs(staging)
            subprocess.run(
                [wrapper, "install", "t3@0.0.36", f"--prefix={staging}"],
                check=True,
                env=environment,
            )
            with open(argument_log, encoding="utf-8") as file_obj:
                staging_arguments = file_obj.read().splitlines()
            with open(config_log, encoding="utf-8") as file_obj:
                npm_config = file_obj.read()

            self.assertTrue(changed)
            self.assertEqual(
                ordinary_arguments,
                ["install", "example@1.0.0", "--prefix", home],
            )
            self.assertEqual(
                staging_arguments,
                ["install", "t3@0.0.36", f"--prefix={staging}"],
            )
            self.assertEqual(
                npm_config,
                "allow-scripts=node-pty,msgpackr-extract\n"
                "dangerously-allow-all-scripts=false\n"
                "ignore-scripts=false\n",
            )
            self.assertFalse(os.path.exists(os.path.join(staging, ".npmrc")))
            self.assertFalse(
                _install_t3_npm_shim(
                    home,
                    node_bin,
                    os.getuid(),
                    os.getgid(),
                )[1]
            )

    def test_npm_shim_requires_target_user_npm(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            node_bin = os.path.join(home, "node-bin")
            os.makedirs(node_bin)

            with self.assertRaisesRegex(RuntimeError, "updates require npm"):
                _install_t3_npm_shim(
                    home,
                    node_bin,
                    os.getuid(),
                    os.getgid(),
                )

    def test_healthy_service_is_not_silently_updated(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            self._write_upstream_runtime(home)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch("common.t3code_steps._run_as_login_user") as run_as_user,
                patch("common.t3code_steps._t3_native_runtime_healthy", return_value=True),
                patch("common.t3code_steps._wait_for_t3_service"),
                patch("common.t3code_steps._user_systemctl", return_value=completed),
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                _install_t3_service(
                    home,
                    "agent",
                    os.getuid(),
                    os.getgid(),
                    workspace,
                    "127.0.0.1",
                    3773,
                )
            run_as_user.assert_not_called()

    def test_refresh_keeps_an_unchanged_service_running(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            binary = self._write_upstream_runtime(home)
            service_file = os.path.join(
                home,
                ".config",
                "systemd",
                "user",
                "t3code.service",
            )
            os.makedirs(os.path.dirname(service_file), exist_ok=True)
            with open(service_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("# upstream managed\n")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch(
                    "common.t3code_steps._install_t3_npm_shim",
                    return_value=(os.path.join(home, "npm-shim"), False),
                ),
                patch(
                    "common.t3code_steps._configure_t3_service_drop_in",
                    return_value=False,
                ),
                patch(
                    "common.t3code_steps._run_as_login_user",
                    return_value=completed,
                ) as updater,
                patch(
                    "common.t3code_steps._t3_native_runtime_healthy",
                    return_value=True,
                ),
                patch("common.t3code_steps._wait_for_t3_service"),
                patch(
                    "common.t3code_steps._user_systemctl",
                    return_value=completed,
                ) as systemctl,
                patch(
                    "common.t3code_steps.shutil.which",
                    return_value="/bin/true",
                ),
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                self.assertEqual(
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                        refresh=True,
                    ),
                    binary,
                )

            updater.assert_called_once()
            actions = [call.args[2] for call in systemctl.call_args_list]
            self.assertIn("start", actions)
            self.assertNotIn("restart", actions)

    def test_t3_loginctl_shim_supplies_explicit_username(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            real_loginctl = os.path.join(home, "real-loginctl")
            with open(real_loginctl, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")
            os.chmod(real_loginctl, 0o755)

            with patch(
                "common.t3code_steps.shutil.which",
                return_value=real_loginctl,
            ):
                with _temporary_t3_loginctl_shim(home, "agent") as directory:
                    shim = os.path.join(directory, "loginctl")
                    with open(shim, encoding="utf-8") as file_obj:
                        content = file_obj.read()
                    self.assertTrue(os.access(shim, os.X_OK))

            self.assertFalse(os.path.exists(directory))
            self.assertIn(
                f"exec {real_loginctl} enable-linger agent",
                content,
            )
            self.assertIn(
                f"{real_loginctl} show-user agent --property=Linger --value",
                content,
            )
            self.assertIn(f'exec {real_loginctl} "$@"', content)

    def test_refresh_retains_valid_runtime_when_updater_fails(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            binary = self._write_upstream_runtime(home)
            update_failed = SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="npm error update failed\ngyp info ok",
            )
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps.shutil.which",
                    return_value="/bin/true",
                ),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch(
                    "common.t3code_steps._run_as_login_user",
                    return_value=update_failed,
                ),
                patch(
                    "common.t3code_steps._t3_native_runtime_healthy",
                    return_value=True,
                ),
                patch("common.t3code_steps._wait_for_t3_service"),
                patch(
                    "common.t3code_steps._user_systemctl",
                    return_value=completed,
                ) as systemctl,
                patch("common.t3code_steps.os.chown"),
                patch("builtins.print") as print_message,
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                self.assertEqual(
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                        refresh=True,
                    ),
                    binary,
                )

            actions = [call.args[2] for call in systemctl.call_args_list]
            self.assertIn("restart", actions)
            messages = [call.args[0] for call in print_message.call_args_list]
            self.assertTrue(
                any("retaining the valid" in message for message in messages)
            )
            self.assertTrue(
                any("npm error update failed" in message for message in messages)
            )
            self.assertTrue(
                any(
                    "retained and validated after updater failure" in message
                    for message in messages
                )
            )

    def test_failed_first_install_reports_start_and_end_of_updater_output(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            update_failed = SimpleNamespace(
                returncode=17,
                stdout="npm stdout context",
                stderr=(
                    "npm error actual failure\n"
                    + ("intermediate build output\n" * 100)
                    + "gyp info ok"
                ),
            )
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps.shutil.which",
                    return_value="/bin/true",
                ),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch(
                    "common.t3code_steps._run_as_login_user",
                    return_value=update_failed,
                ),
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                    )

            message = str(raised.exception)
            self.assertIn("exit code 17", message)
            self.assertIn("npm error actual failure", message)
            self.assertIn("... output omitted ...", message)
            self.assertIn("gyp info ok", message)
            self.assertIn("npm stdout context", message)

    def test_legacy_root_service_is_retired_after_user_service_starts(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            self._write_upstream_runtime(home)
            legacy_service = os.path.join(home, "infra-tools-t3code.service")
            with open(legacy_service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# legacy infra-tools unit\n")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch("common.t3code_steps._run_as_login_user") as run_as_user,
                patch("common.t3code_steps._t3_native_runtime_healthy", return_value=True),
                patch("common.t3code_steps._wait_for_t3_service"),
                patch("common.t3code_steps._user_systemctl", return_value=completed),
                patch("common.t3code_steps.run", return_value=completed) as run_command,
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    legacy_service,
                ),
            ):
                _install_t3_service(
                    home,
                    "agent",
                    os.getuid(),
                    os.getgid(),
                    workspace,
                    "127.0.0.1",
                    3773,
                )

            run_as_user.assert_not_called()
            self.assertFalse(os.path.exists(legacy_service))
            commands = [call.args[0] for call in run_command.call_args_list]
            self.assertIn(
                ["systemctl", "stop", "infra-tools-t3code.service"],
                commands,
            )
            self.assertIn(
                ["systemctl", "disable", "infra-tools-t3code.service"],
                commands,
            )
            self.assertIn(
                ["systemctl", "reset-failed", "infra-tools-t3code.service"],
                commands,
            )

    def test_legacy_service_is_restored_when_readiness_does_not_stabilize(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            self._write_upstream_runtime(home)
            legacy_service = os.path.join(home, "infra-tools-t3code.service")
            with open(legacy_service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# legacy infra-tools unit\n")
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch("common.t3code_steps._t3_native_runtime_healthy", return_value=True),
                patch(
                    "common.t3code_steps._wait_for_t3_service",
                    side_effect=RuntimeError("readiness failed"),
                ),
                patch("common.t3code_steps._user_systemctl", return_value=completed),
                patch("common.t3code_steps.run", return_value=completed) as run_command,
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    legacy_service,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "readiness failed"):
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                    )

            self.assertTrue(os.path.isfile(legacy_service))
            commands = [call.args[0] for call in run_command.call_args_list]
            self.assertIn(
                ["systemctl", "start", "infra-tools-t3code.service"],
                commands,
            )

    def test_incomplete_native_runtime_is_rebuilt_before_restart(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            binary = self._write_upstream_runtime(home)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch(
                    "common.t3code_steps._t3_native_runtime_healthy",
                    return_value=False,
                ),
                patch("common.t3code_steps._rebuild_t3_native_runtime") as rebuild,
                patch("common.t3code_steps._wait_for_t3_service"),
                patch(
                    "common.t3code_steps._user_systemctl",
                    return_value=completed,
                ) as systemctl,
                patch("common.t3code_steps.os.chown"),
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                self.assertEqual(
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                    ),
                    binary,
                )

            rebuild.assert_called_once_with("agent", home, node_bin, binary)
            actions = [call.args[2] for call in systemctl.call_args_list]
            self.assertIn("stop", actions)
            self.assertIn("restart", actions)

    def test_setup_repairs_retained_failed_update_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            active_binary = self._write_upstream_runtime(home, "0.0.35")
            candidate_binary = os.path.join(
                home,
                ".t3",
                "runtime",
                "versions",
                "0.0.36",
                "node_modules",
                "t3",
                "dist",
                "bin.mjs",
            )
            os.makedirs(os.path.dirname(candidate_binary), exist_ok=True)
            with open(candidate_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/usr/bin/env node\n")
            os.chmod(candidate_binary, 0o755)
            state_file = os.path.join(
                home,
                ".t3",
                "runtime",
                "service-state.json",
            )
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {
                        "protocol": 2,
                        "activeVersion": "0.0.35",
                        "update": {
                            "status": "rolled-back",
                            "targetVersion": "0.0.36",
                            "reason": "candidate-exited:1",
                        },
                    },
                    file_obj,
                )
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")

            def native_healthy(_username, _home, _node_bin, binary):
                return binary == active_binary

            with (
                patch("common.t3code_steps.install_package", return_value=True),
                patch("common.t3code_steps._ensure_user_manager"),
                patch(
                    "common.t3code_steps._node_bin_directory",
                    return_value=node_bin,
                ),
                patch("common.t3code_steps._run_as_login_user") as run_as_user,
                patch(
                    "common.t3code_steps._t3_native_runtime_healthy",
                    side_effect=native_healthy,
                ),
                patch("common.t3code_steps._rebuild_t3_native_runtime") as rebuild,
                patch("common.t3code_steps._wait_for_t3_service"),
                patch("common.t3code_steps._user_systemctl", return_value=completed),
                patch("common.t3code_steps.os.chown"),
                patch("builtins.print") as print_message,
                patch(
                    "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                    os.path.join(home, "legacy.service"),
                ),
            ):
                self.assertEqual(
                    _install_t3_service(
                        home,
                        "agent",
                        os.getuid(),
                        os.getgid(),
                        workspace,
                        "127.0.0.1",
                        3773,
                    ),
                    active_binary,
                )

            run_as_user.assert_not_called()
            rebuild.assert_called_once_with(
                "agent",
                home,
                node_bin,
                candidate_binary,
            )
            self.assertTrue(
                any(
                    "Repaired retained T3 Code v0.0.36 update candidate"
                    in call.args[0]
                    for call in print_message.call_args_list
                )
            )

    def test_native_rebuild_uses_scoped_npm_script_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            binary = self._write_upstream_runtime(home)
            node_bin = os.path.join(home, "node-bin")
            os.makedirs(node_bin)
            for executable in ("node", "npm"):
                path = os.path.join(node_bin, executable)
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("#!/bin/sh\n")
                os.chmod(path, 0o755)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            observed: dict[str, str] = {}

            def run_as_user(_username, _home, command, **_kwargs):
                match = re.search(r"NPM_CONFIG_USERCONFIG=([^ ]+)", command)
                self.assertIsNotNone(match)
                assert match is not None
                npm_config = shlex.split(match.group(1))[0]
                with open(npm_config, encoding="utf-8") as file_obj:
                    observed["content"] = file_obj.read()
                observed["path"] = npm_config
                observed["command"] = command
                return completed

            with (
                patch(
                    "common.t3code_steps._run_as_login_user",
                    side_effect=run_as_user,
                ),
                patch(
                    "common.t3code_steps._t3_native_runtime_healthy",
                    return_value=True,
                ),
            ):
                _rebuild_t3_native_runtime("agent", home, node_bin, binary)

            command = observed["command"]
            self.assertEqual(
                observed["content"],
                "allow-scripts=node-pty,msgpackr-extract\n",
            )
            self.assertFalse(os.path.exists(observed["path"]))
            self.assertIn("export CC=gcc", command)
            self.assertIn("export CXX=g++", command)
            self.assertIn("NPM_CONFIG_USERCONFIG=", command)
            self.assertIn("unset npm_config_allow_scripts", command)
            self.assertNotIn("dangerously_allow_all_scripts=true", command)
            self.assertNotIn("--dangerously-allow-all-scripts", command)
            self.assertIn("--foreground-scripts --prefix", command)
            self.assertIn("node-pty msgpackr-extract", command)

    def test_readiness_requires_three_consecutive_healthy_checks(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("common.t3code_steps._user_systemctl", return_value=completed),
            patch(
                "common.t3code_steps._t3_local_endpoint_reachable",
                side_effect=[True, False, True, True, True],
            ) as endpoint,
            patch("common.t3code_steps.time.sleep"),
        ):
            _wait_for_t3_service("agent", 1000, "0.0.0.0", 3773, "/home/agent")

        self.assertEqual(endpoint.call_count, 5)

    def test_stable_wrapper_follows_service_selected_version(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, ".local", "bin")
            os.makedirs(bin_dir)
            wrapper = os.path.join(bin_dir, "t3")
            self.assertTrue(_write_passthrough_wrapper(wrapper, home))
            with open(wrapper, encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn("service-state.json", content)
            self.assertIn('value.get("protocol") == 2', content)
            self.assertIn('NVM_DIR="$HOME/.nvm"', content)
            self.assertIn("versions", content)
            self.assertIn('exec "$binary" "$@"', content)
            self.assertNotIn("npx", content)

    def test_web_step_is_server_only_and_uses_upstream_service(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            account = SimpleNamespace(
                pw_dir=home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            binary = self._write_upstream_runtime(home)
            admin_pair_script = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "common",
                "service_tools",
                "t3code_admin_pair.py",
            )
            with (
                patch("common.t3code_steps.pwd.getpwnam", return_value=account),
                patch("common.t3code_steps.os.chown"),
                patch("common.t3code_steps._configure_firewall"),
                patch(
                    "common.t3code_steps._install_t3_service",
                    return_value=binary,
                ) as install_service,
                patch("common.t3code_steps._ensure_t3_agent_skill", return_value=False),
                patch("common.t3code_steps._remove_connect_restart_units"),
                patch("common.t3code_steps._remove_device_pairing"),
                patch("common.t3code_steps._remove_t3_https"),
                patch("common.t3code_steps._configure_t3_https", return_value={}),
                patch("common.t3code_steps.T3_ADMIN_PAIR_SCRIPT", admin_pair_script),
            ):
                install_t3code_web(
                    self._config(
                        web_interface_sources=None,
                        web_interface_host="127.0.0.1",
                        agent_workspace=workspace,
                    )
                )

            install_service.assert_called_once()
            self.assertTrue(install_service.call_args.kwargs["refresh"])

            wrapper = os.path.join(
                home,
                ".local",
                "bin",
                "infra-tools-t3code-pairing-provider",
            )
            pair_wrapper = os.path.join(home, ".local", "bin", "t3code-pair")
            self.assertTrue(os.path.isfile(wrapper))
            self.assertTrue(os.path.isfile(pair_wrapper))
            self.assertFalse(
                os.path.exists(os.path.join(home, ".local", "share", "t3code"))
            )
            self.assertFalse(
                os.path.exists(os.path.join(home, ".local", "share", "applications"))
            )


if __name__ == "__main__":
    unittest.main()
