"""Tests for the T3 Code server setup path."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from common.t3code_steps import (
    _active_t3_binary,
    _configure_firewall,
    _configure_t3_https,
    _install_t3_npm_shim,
    _install_t3_service,
    _rebuild_t3_native_runtime,
    _remove_t3_https,
    _temporary_t3_loginctl_shim,
    _t3_local_endpoint_reachable,
    _t3_native_runtime_healthy,
    _t3_service_invocation,
    _wait_for_t3_service,
    _write_passthrough_wrapper,
    install_t3code_web,
    remove_device_pairing,
)
from lib.config import SetupConfig
from lib.remote_utils import CommandExecutionError, CommandTimeoutError
from lib.validation import validate_web_interface_settings
from plugins.common import extend_agent_steps


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
    def _write_upstream_native_runtime(
        home: str,
        version: str = "0.0.42",
        protocol: int = 2,
    ) -> str:
        """Create the current T3 native executable runtime layout."""

        runtime = os.path.join(home, ".t3", "runtime")
        binary = os.path.join(runtime, "versions", version, "t3")
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        with open(binary, "w", encoding="utf-8") as file_obj:
            file_obj.write("#!/bin/sh\n")
        os.chmod(binary, 0o755)
        with open(
            os.path.join(runtime, "service-state.json"),
            "w",
            encoding="utf-8",
        ) as file_obj:
            json.dump({"protocol": protocol, "activeVersion": version}, file_obj)
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

    def _service_fixture(self, home: str, stack: ExitStack) -> SimpleNamespace:
        """Run real setup file reconciliation with all host operations mocked."""

        workspace = os.path.join(home, "repos")
        os.makedirs(workspace)
        node_bin = self._write_node_tools(home)
        binary = self._write_upstream_runtime(home, "0.1.2")
        state = SimpleNamespace(
            invocation="a" * 32,
            arguments=(home, "agent", os.getuid(), os.getgid(), workspace, "127.0.0.1", 3773),
            marker=os.path.join(home, ".config", "systemd", "user", ".basaltwater-t3-reconcile.json"),
            node_bin=node_bin,
            binary=binary,
        )

        def systemctl(_username, _uid, action, *_args):
            if action == "restart":
                state.invocation = f"{int(state.invocation or '0', 16) + 1:032x}"
            elif action == "stop":
                state.invocation = None
            return SimpleNamespace(
                returncode=0, stdout=state.invocation if action == "show" else "", stderr="",
            )

        state.systemctl = systemctl
        stack.enter_context(patch("common.t3code_steps.install_package", return_value=True))
        stack.enter_context(patch("common.t3code_steps._ensure_user_manager"))
        stack.enter_context(patch("common.t3code_steps._node_bin_directory", return_value=node_bin))
        stack.enter_context(patch("common.t3code_steps.shutil.which", return_value="/bin/true"))
        stack.enter_context(patch("common.t3code_steps.os.chown"))
        stack.enter_context(patch(
            "common.t3code_steps.LEGACY_T3_SERVICE_FILE", os.path.join(home, "legacy.service"),
        ))
        stack.enter_context(patch(
            "common.t3code_steps.run", side_effect=AssertionError("Unexpected host call"),
        ))
        state.manager = stack.enter_context(patch(
            "common.t3code_steps._user_systemctl", side_effect=systemctl,
        ))
        state.updater = stack.enter_context(patch(
            "common.t3code_steps._run_as_login_user",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ))
        state.native = stack.enter_context(patch(
            "common.t3code_steps._t3_native_runtime_healthy", return_value=True,
        ))
        state.repair = stack.enter_context(patch("common.t3code_steps._rebuild_t3_native_runtime"))
        state.readiness = stack.enter_context(patch("common.t3code_steps._wait_for_t3_service"))
        state.messages = stack.enter_context(patch("builtins.print"))
        return state

    @staticmethod
    def _pairing_cleanup_paths(
        home: str, stack: ExitStack, *, installed: bool = True
    ) -> dict[str, str]:
        config_dir = os.path.join(home, "device-pairing")
        paths = {
            "DEVICE_PAIRING_CONFIG_DIR": config_dir,
            "DEVICE_PAIRING_AUTH_FILE": os.path.join(config_dir, "htpasswd"),
            "DEVICE_PAIRING_PROVIDERS_FILE": os.path.join(config_dir, "providers.json"),
            "DEVICE_PAIRING_SERVICE_FILE": os.path.join(home, "pairing.service"),
            "DEVICE_PAIRING_NGINX_SITE": os.path.join(home, "nginx-site"),
            "DEVICE_PAIRING_NGINX_LINK": os.path.join(home, "nginx-link"),
            "T3_CONNECT_RESTART_PATH_FILE": os.path.join(home, "connect.path"),
            "T3_CONNECT_RESTART_SERVICE_FILE": os.path.join(home, "connect.service"),
        }
        if installed:
            os.makedirs(config_dir)
        for name, path in paths.items():
            stack.enter_context(patch(f"common.t3code_steps.{name}", path))
            if installed and name not in {
                "DEVICE_PAIRING_CONFIG_DIR", "DEVICE_PAIRING_NGINX_LINK"
            }:
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("managed fixture\n")
        if installed:
            os.symlink(paths["DEVICE_PAIRING_NGINX_SITE"], paths["DEVICE_PAIRING_NGINX_LINK"])
        stack.enter_context(patch("common.t3code_steps.is_dry_run", return_value=False))
        stack.enter_context(patch("common.t3code_steps._user_home", return_value=home))
        stack.enter_context(patch("common.t3code_steps.os.geteuid", return_value=0))
        stack.enter_context(patch(
            "common.t3code_steps.shutil.which", return_value="/usr/sbin/nginx"
        ))
        return paths

    def test_source_promotes_default_bind_to_all_interfaces(self) -> None:
        config = self._config()
        self.assertEqual(config.web_interface_host, "0.0.0.0")
        validate_web_interface_settings(config)

    def test_pairing_opt_out_plan_with_and_without_web_interface(self) -> None:
        for disable_web in (False, True):
            for disable_pairing in (False, True):
                with self.subTest(web=disable_web, pairing=disable_pairing):
                    config = self._config(
                        disable_web_interface=disable_web,
                        disable_device_pairing=disable_pairing,
                    )
                    steps = []
                    extend_agent_steps(config, steps)
                    functions = [function for _label, function in steps]
                    self.assertEqual(install_t3code_web in functions, not disable_web)
                    self.assertEqual(
                        functions.count(remove_device_pairing),
                        int(disable_web and disable_pairing),
                    )

    def test_pairing_opt_out_removes_existing_portal_without_installing_web(self) -> None:
        config = self._config(
            disable_web_interface=True,
            disable_device_pairing=True,
        )
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            paths = self._pairing_cleanup_paths(home, stack)
            state_dir = os.path.join(home, ".t3")
            os.makedirs(state_dir)
            request = os.path.join(state_dir, "basaltwater-connect-restart")
            with open(request, "w", encoding="utf-8") as file_obj:
                file_obj.write("restart\n")
            utility = "/usr/local/bin/basaltwater-web"
            path_exists = os.path.exists
            stack.enter_context(patch(
                "common.t3code_steps.os.path.exists",
                side_effect=lambda path: path == utility or path_exists(path),
            ))
            remove_ban = stack.enter_context(patch(
                "common.t3code_steps.remove_nginx_auth_failure_ban"
            ))
            routes = {"t3code", "t3code-pairing"}

            def run(command, **_kwargs):
                if " forward list " in command:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"forwards": [{"name": name} for name in routes]}),
                        stderr="",
                    )
                if " forward remove " in command:
                    routes.remove(shlex.split(command)[4])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            run_command = stack.enter_context(patch(
                "common.t3code_steps.run",
                side_effect=run,
            ))
            install_service = stack.enter_context(patch(
                "common.t3code_steps._install_t3_service"
            ))
            install_package = stack.enter_context(patch(
                "common.t3code_steps.install_package"
            ))
            steps = []
            extend_agent_steps(config, steps)
            cleanup = next(
                function for _label, function in steps
                if function is remove_device_pairing
            )
            cleanup(config)
            first_run_calls = run_command.call_count
            cleanup(config)

            for path in (*paths.values(), request):
                self.assertFalse(os.path.lexists(path), path)
            commands = [call.args[0] for call in run_command.call_args_list]
            self.assertIn("systemctl disable --now basaltwater-device-pairing.service", commands)
            self.assertIn("systemctl disable --now basaltwater-t3code-connect.path", commands)
            self.assertIn("systemctl disable --now basaltwater-t3code-connect.service", commands)
            self.assertIn("nginx -t", commands)
            self.assertIn("systemctl reload nginx", commands)
            forwards = [command for command in commands if "forward remove " in command]
            self.assertEqual(len(forwards), 1)
            self.assertIn("forward remove t3code-pairing --json", forwards[0])
            self.assertEqual(routes, {"t3code"})
            self.assertEqual(
                [call.args[0] for call in run_command.call_args_list[first_run_calls:]],
                [f"SUDO_USER=agent {utility} forward list --json"],
            )
            self.assertFalse(any(
                "restart" in command or "forward add" in command
                for command in commands
            ))
            self.assertEqual(remove_ban.call_count, 2)
            remove_ban.assert_called_with("device-pairing")
            install_service.assert_not_called()
            install_package.assert_not_called()

    def test_pairing_removal_propagates_required_service_stop_failures(self) -> None:
        for key, unit in (
            ("DEVICE_PAIRING_SERVICE_FILE", "basaltwater-device-pairing.service"),
            ("T3_CONNECT_RESTART_PATH_FILE", "basaltwater-t3code-connect.path"),
            ("T3_CONNECT_RESTART_SERVICE_FILE", "basaltwater-t3code-connect.service"),
        ):
            with (
                self.subTest(unit=unit),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                paths = self._pairing_cleanup_paths(home, stack)
                failed_command = f"systemctl disable --now {unit}"

                def run(command, *, check=True, **_kwargs):
                    code = int(command == failed_command)
                    if check and code:
                        raise CommandExecutionError(command, code, "stop refused")
                    return SimpleNamespace(returncode=code, stdout="", stderr="")

                stack.enter_context(patch("common.t3code_steps.run", side_effect=run))
                stack.enter_context(patch("common.t3code_steps.remove_nginx_auth_failure_ban"))
                remove_https = stack.enter_context(patch("common.t3code_steps._remove_t3_https"))
                with self.assertRaisesRegex(CommandExecutionError, "stop refused"):
                    remove_device_pairing(self._config(disable_device_pairing=True))
                self.assertTrue(os.path.exists(paths[key]))
                remove_https.assert_not_called()

    def test_pairing_removal_retries_failed_nginx_validation_and_reload(self) -> None:
        for failed_command in ("nginx -t", "systemctl reload nginx"):
            for symlink in (True, False):
                with (
                    self.subTest(command=failed_command, symlink=symlink),
                    tempfile.TemporaryDirectory() as home,
                    ExitStack() as stack,
                ):
                    paths = self._pairing_cleanup_paths(home, stack)
                    link = paths["DEVICE_PAIRING_NGINX_LINK"]
                    if not symlink:
                        os.unlink(link)
                        with open(link, "w", encoding="utf-8") as file_obj:
                            file_obj.write("managed enabled fixture\n")
                        os.chmod(link, 0o640)
                    failed = False

                    def run(command, *, check=True, **_kwargs):
                        nonlocal failed
                        if command == failed_command and not failed:
                            failed = True
                            if check:
                                raise CommandExecutionError(command, 1, "reload refused")
                            return SimpleNamespace(returncode=1, stdout="", stderr="invalid")
                        return SimpleNamespace(returncode=0, stdout="", stderr="")

                    commands = stack.enter_context(patch(
                        "common.t3code_steps.run", side_effect=run
                    ))
                    stack.enter_context(patch("common.t3code_steps.remove_nginx_auth_failure_ban"))
                    remove_https = stack.enter_context(patch("common.t3code_steps._remove_t3_https"))
                    with self.assertRaisesRegex(RuntimeError, "invalid|reload refused"):
                        remove_device_pairing(self._config(disable_device_pairing=True))
                    remove_https.assert_not_called()
                    for key in (
                        "DEVICE_PAIRING_NGINX_SITE", "DEVICE_PAIRING_AUTH_FILE",
                        "DEVICE_PAIRING_PROVIDERS_FILE",
                    ):
                        self.assertTrue(os.path.exists(paths[key]))
                    if symlink:
                        self.assertEqual(os.readlink(link), paths["DEVICE_PAIRING_NGINX_SITE"])
                    else:
                        with open(link, encoding="utf-8") as file_obj:
                            self.assertEqual(file_obj.read(), "managed enabled fixture\n")
                        self.assertEqual(os.stat(link).st_mode & 0o777, 0o640)

                    remove_device_pairing(self._config(disable_device_pairing=True))
                    for path in paths.values():
                        self.assertFalse(os.path.lexists(path), path)
                    reloads = [
                        call for call in commands.call_args_list
                        if call.args[0] == "systemctl reload nginx"
                    ]
                    self.assertEqual(len(reloads), 2 if "reload" in failed_command else 1)

    def test_pairing_removal_without_an_install_does_not_run_system_commands(self) -> None:
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            self._pairing_cleanup_paths(home, stack, installed=False)
            stack.enter_context(patch("common.t3code_steps.os.path.exists", return_value=False))
            stack.enter_context(patch("common.t3code_steps.remove_nginx_auth_failure_ban"))
            commands = stack.enter_context(patch("common.t3code_steps.run"))
            remove_device_pairing(self._config(disable_device_pairing=True))
            remove_device_pairing(self._config(disable_device_pairing=True))
            commands.assert_not_called()

    def test_pairing_removal_refuses_symlinked_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            config_dir = os.path.join(home, "device-pairing")
            destination = os.path.join(home, "unmanaged")
            os.makedirs(destination)
            credentials = os.path.join(destination, "htpasswd")
            with open(credentials, "w", encoding="utf-8") as file_obj:
                file_obj.write("preserved fixture\n")
            os.symlink(destination, config_dir)
            with (
                patch("common.t3code_steps.is_dry_run", return_value=False),
                patch("common.t3code_steps._user_home", return_value=home),
                patch("common.t3code_steps.DEVICE_PAIRING_CONFIG_DIR", config_dir),
                patch("common.t3code_steps.run") as run_command,
            ):
                with self.assertRaisesRegex(RuntimeError, "Refusing unsafe device-pairing"):
                    remove_device_pairing(self._config(disable_device_pairing=True))
            self.assertTrue(os.path.isfile(credentials))
            run_command.assert_not_called()

    def test_pairing_removal_dry_run_has_no_side_effects(self) -> None:
        with (
            patch("common.t3code_steps.is_dry_run", return_value=True),
            patch("common.t3code_steps._user_home") as user_home,
            patch("common.t3code_steps._remove_device_pairing") as remove_portal,
            patch("common.t3code_steps._remove_connect_restart_units") as remove_units,
            patch("common.t3code_steps.run") as run_command,
        ):
            remove_device_pairing(self._config(disable_device_pairing=True))
        user_home.assert_not_called()
        remove_portal.assert_not_called()
        remove_units.assert_not_called()
        run_command.assert_not_called()

    def test_https_gateway_is_optional_when_managed_utility_is_unavailable(self) -> None:
        with patch("common.t3code_steps.os.path.isfile", return_value=False):
            self.assertEqual(_configure_t3_https(self._config(), 3773, None), {})

    def test_https_removal_skips_missing_utility(self) -> None:
        with (
            patch("common.t3code_steps.os.path.exists", return_value=False),
            patch("common.t3code_steps.run") as commands,
        ):
            _remove_t3_https(self._config())
        commands.assert_not_called()

    def test_https_removal_skips_absent_routes_and_preserves_other_routes(self) -> None:
        for routes in ([], [{"name": "another-app"}], [{"name": "t3code"}]):
            for pairing_only in (True, False):
                with (
                    self.subTest(routes=routes, pairing_only=pairing_only),
                    patch("common.t3code_steps.os.path.exists", return_value=True),
                    patch("common.t3code_steps.run", return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps({"forwards": routes}), stderr=""
                    )) as commands,
                ):
                    _remove_t3_https(self._config(), pairing_only=pairing_only)
                    removals = [
                        call.args[0] for call in commands.call_args_list
                        if " forward remove " in call.args[0]
                    ]
                    self.assertEqual(
                        removals,
                        ["SUDO_USER=agent /usr/local/bin/basaltwater-web forward remove t3code --json"]
                        if routes == [{"name": "t3code"}] and not pairing_only else [],
                    )

    def test_https_removal_propagates_list_and_existing_route_failures(self) -> None:
        for failure in ("list", "t3code-pairing", "t3code"):
            with (
                self.subTest(failure=failure),
                patch("common.t3code_steps.os.path.exists", return_value=True),
            ):
                def run(command, *, check=True, **_kwargs):
                    fails = (
                        " forward list " in command if failure == "list"
                        else f" forward remove {failure} --json" in command
                    )
                    if fails and check:
                        raise CommandExecutionError(command, 1, "gateway refused")
                    return SimpleNamespace(
                        returncode=int(fails),
                        stdout=json.dumps({"forwards": [
                            {"name": "t3code-pairing"}, {"name": "t3code"},
                        ]}),
                        stderr="gateway refused" if fails else "",
                    )

                with patch("common.t3code_steps.run", side_effect=run) as commands:
                    with self.assertRaisesRegex(CommandExecutionError, "gateway refused"):
                        _remove_t3_https(self._config())
                if failure == "list":
                    self.assertEqual(commands.call_count, 1)

    def test_https_removal_rejects_invalid_route_inventory(self) -> None:
        for output in (
            "invalid json", "null", "[]", "{}", '{"forwards": {}}',
            '{"forwards": [null]}', '{"forwards": [{}]}',
            '{"forwards": [{"name": 1}]}',
        ):
            with (
                self.subTest(output=output),
                patch("common.t3code_steps.os.path.exists", return_value=True),
                patch("common.t3code_steps.run", return_value=SimpleNamespace(
                    returncode=0, stdout=output, stderr=""
                )) as commands,
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid forward list"):
                    _remove_t3_https(self._config())
                self.assertEqual(commands.call_count, 1)

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

    def test_https_gateway_uses_literal_loopback_for_both_routes(self) -> None:
        for host, target in (
            ("127.0.0.1", "127.0.0.1"), ("127.0.0.2", "127.0.0.2"),
            ("::1", "[::1]"), ("0:0:0:0:0:0:0:1", "[::1]"),
            ("0.0.0.0", "127.0.0.1"), ("::", "[::1]"),
        ):
            with (
                self.subTest(host=host),
                patch("common.t3code_steps.os.geteuid", return_value=0),
                patch("common.t3code_steps.os.path.isfile", return_value=True),
                patch("common.godot_web_steps.configure_internal_web_host"),
                patch("common.godot_web_steps.identities_for_config", return_value=["target"]),
                patch("common.t3code_steps.open_loopback") as probe,
                patch("common.t3code_steps.run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps({"listen": 8444, "url": "https://target:8444/"}),
                    stderr="",
                )) as commands,
            ):
                _configure_t3_https(self._config(web_interface_host=host), 3773, 3774)
                for call, name, port in zip(
                    commands.call_args_list, ("t3code", "t3code-pairing"), (3773, 3774),
                ):
                    arguments = shlex.split(call.args[0])
                    self.assertIn(name, arguments)
                    self.assertEqual(arguments[arguments.index("--to") + 1], f"{target}:{port}")
                    self.assertIn(f"--to {shlex.quote(f'{target}:{port}')} ", call.args[0])
                self.assertEqual(commands.call_count, 2)
                probe.assert_not_called()

    def test_localhost_gateway_selects_each_listener_family_including_protected_pairing(self) -> None:
        from urllib.error import HTTPError

        for t3_host, pairing_host in (("127.0.0.1", "::1"), ("::1", "127.0.0.1")):
            for status in (200, 301, 401):
                with self.subTest(t3=t3_host, pairing=pairing_host, status=status):
                    response = MagicMock()
                    response.__enter__.return_value.status = 200
                    expected = {
                        f"http://{t3_host if ':' not in t3_host else '[' + t3_host + ']'}:3773/",
                        f"http://{pairing_host if ':' not in pairing_host else '[' + pairing_host + ']'}:3774/",
                    }

                    def probe(url, **_kwargs):
                        if url not in expected:
                            raise OSError("No listener")
                        if url.endswith(":3774/") and status != 200:
                            raise HTTPError(url, status, "protected portal", {}, None)
                        return response

                    with (
                        patch("common.t3code_steps.os.geteuid", return_value=0),
                        patch("common.t3code_steps.os.path.isfile", return_value=True),
                        patch("common.godot_web_steps.configure_internal_web_host"),
                        patch("common.godot_web_steps.identities_for_config", return_value=["target"]),
                        patch("common.t3code_steps.open_loopback", side_effect=probe),
                        patch("common.t3code_steps.run", return_value=SimpleNamespace(
                            returncode=0, stdout=json.dumps({"listen": 8444, "url": "https://target:8444/"}),
                            stderr="",
                        )) as commands,
                    ):
                        _configure_t3_https(self._config(web_interface_host="LOCALHOST"), 3773, 3774)
                    for call, host, port in zip(commands.call_args_list, (t3_host, pairing_host), (3773, 3774)):
                        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
                        self.assertIn(f"--to {shlex.quote(target)} ", call.args[0])

    def test_t3_bind_validation_rejects_specific_nonloopback_and_cidr_addresses(self) -> None:
        for host in ("192.168.1.2", "fd00::2", "8.8.8.8", "127.0.0.1/32"):
            with self.subTest(host=host), self.assertRaises(ValueError) as raised:
                validate_web_interface_settings(self._config(web_interface_host=host))
            if "/" not in host:
                self.assertIn("0.0.0.0 or ::", str(raised.exception))
                self.assertIn("--access-source", str(raised.exception))

    def test_t3_bind_validation_accepts_all_literal_loopbacks_and_localhost(self) -> None:
        for host in ("127.0.0.1", "127.0.0.2", "127.255.255.254", "::1", "LOCALHOST"):
            with self.subTest(host=host):
                validate_web_interface_settings(self._config(
                    web_interface_host=host, web_interface_sources=None,
                ))

    def test_t3_bind_restriction_does_not_apply_to_other_web_interfaces(self) -> None:
        config = SimpleNamespace(
            web_interfaces=["another-interface"], web_interface_host="192.168.1.2",
            web_interface_sources=["192.168.1.0/24"],
        )
        with patch("lib.config.WEB_INTERFACES", ("t3code", "another-interface")):
            validate_web_interface_settings(config)

    def test_unsupported_t3_bind_is_rejected_before_target_operations(self) -> None:
        with (
            patch("common.t3code_steps._user_home") as home,
            patch("common.t3code_steps._configure_firewall") as firewall,
            patch("common.t3code_steps._t3_reconciliation_path") as lock,
            patch("common.t3code_steps.install_package") as packages,
        ):
            with self.assertRaisesRegex(ValueError, "specific non-loopback"):
                install_t3code_web(self._config(web_interface_host="192.168.1.2"))
            with self.assertRaisesRegex(ValueError, "specific non-loopback"):
                _install_t3_service("/unused", "agent", 1000, 1000, "/unused", "fd00::2", 3773)
        for operation in (home, firewall, lock, packages):
            operation.assert_not_called()

    def test_non_loopback_bind_requires_private_source(self) -> None:
        for host in ("0.0.0.0", "::"):
            with self.subTest(host=host):
                config = self._config(web_interface_host=host, web_interface_sources=None)
                with self.assertRaisesRegex(
                    ValueError,
                    "requires --access-source or --web-interface-source",
                ):
                    validate_web_interface_settings(config)
                validate_web_interface_settings(self._config(web_interface_host=host))

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
                    "# basaltwater T3 Code 3773/tcp source 192.168.0.0/24\n"
                )
                if state["allow_seen"]:
                    rules += (
                        "[ 2] 3773/tcp ALLOW IN 10.0.0.0/24 "
                        "# basaltwater T3 Code 3773/tcp source 10.0.0.0/24\n"
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

    def test_active_binary_accepts_forward_protocols_with_valid_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            binary = self._write_upstream_runtime(home)
            self.assertEqual(_active_t3_binary(home), binary)
            native_binary = self._write_upstream_native_runtime(home)
            self.assertEqual(_active_t3_binary(home), native_binary)
            state_file = os.path.join(home, ".t3", "runtime", "service-state.json")
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump({"protocol": 3, "activeVersion": "0.0.42"}, file_obj)
            self.assertEqual(_active_t3_binary(home), native_binary)
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {"protocolVersion": 2, "activeVersion": "0.0.42"},
                    file_obj,
                )
            self.assertIsNone(_active_t3_binary(home))
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {"protocol": 4, "activeVersion": "0.0.42"},
                    file_obj,
                )
            self.assertEqual(_active_t3_binary(home), native_binary)
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    {"protocol": 1, "activeVersion": "0.0.42"},
                    file_obj,
                )
            self.assertIsNone(_active_t3_binary(home))
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump({"protocol": True, "activeVersion": "0.0.42"}, file_obj)
            self.assertIsNone(_active_t3_binary(home))

            build_binary = self._write_upstream_runtime(
                home,
                "1.2.3-nightly.1+build.5",
            )
            self.assertEqual(_active_t3_binary(home), build_binary)

            self._write_upstream_runtime(home, "01.2.3")
            self.assertIsNone(_active_t3_binary(home))

            self._write_upstream_runtime(home, "1.2.3-01")
            self.assertIsNone(_active_t3_binary(home))

    def test_service_upgrade_uses_latest_native_runtime_without_second_restart(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            self._write_upstream_native_runtime(home, "0.0.45", protocol=3)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            commands: list[str] = []

            def run_as_user(_username, _home, command, **_kwargs):
                commands.append(command)
                self._write_upstream_native_runtime(home, "0.1.0", protocol=3)
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
                patch(
                    "common.t3code_steps._user_systemctl", return_value=completed
                ) as systemctl,
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
                    refresh=True,
                )

            self.assertEqual(binary, _active_t3_binary(home))
            actions = [call.args[2] for call in systemctl.call_args_list]
            self.assertIn("start", actions)
            self.assertNotIn("restart", actions)
            update_command = next(
                command for command in commands if "--package=t3@latest" in command
            )
            self.assertIn("npx --yes --package=t3@latest -c", update_command)
            self.assertIn(".basaltwater-t3-loginctl-", update_command)
            self.assertIn(
                f"{home}/.local/share/basaltwater/t3-npm/bin",
                update_command,
            )
            self.assertIn('export PATH=', update_command)
            self.assertGreater(
                update_command.rindex("-u npm_config_allow_scripts"),
                update_command.index("npx --yes"),
            )
            self.assertGreater(
                update_command.rindex("t3 service install"),
                update_command.index("npx --yes"),
            )
            self.assertNotIn("t3 service update", update_command)
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
                "basaltwater.conf",
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
                f"Environment=PATH={home}/.local/share/basaltwater/t3-npm/bin:",
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
                patch(
                    "common.t3code_steps._install_t3_npm_shim",
                    return_value=(os.path.join(home, "npm-shim"), False),
                ),
                patch(
                    "common.t3code_steps._configure_t3_service_drop_in",
                    return_value=False,
                ),
                patch("common.t3code_steps._run_as_login_user") as run_as_user,
                patch("common.t3code_steps._t3_native_runtime_healthy", return_value=True),
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
            actions = [call.args[2] for call in systemctl.call_args_list]
            self.assertIn("start", actions)
            self.assertNotIn("restart", actions)

    def test_refresh_restarts_an_unchanged_service(self) -> None:
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
            self.assertIn("restart", actions)
            self.assertNotIn("start", actions)

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

    def test_same_version_refresh_restarts_only_when_updater_did_not(self) -> None:
        for updater_code, updater_restarts in ((0, True), (0, False), (1, True), (1, False)):
            with (
                self.subTest(updater_code=updater_code, updater_restarts=updater_restarts),
                tempfile.TemporaryDirectory() as home,
            ):
                workspace = os.path.join(home, "repos")
                os.makedirs(workspace)
                node_bin = self._write_node_tools(home)
                binary = self._write_upstream_native_runtime(
                    home, "0.1.0", protocol=3
                )
                invocation = ["a" * 32]
                completed = SimpleNamespace(returncode=0, stdout="", stderr="")

                def update(*_args, **_kwargs):
                    if updater_restarts:
                        invocation[0] = "b" * 32
                    return SimpleNamespace(returncode=updater_code, stdout="", stderr="updater output")

                def systemctl(_username, _uid, action, *_args):
                    return SimpleNamespace(
                        returncode=0,
                        stdout=invocation[0] if action == "show" else "",
                        stderr="",
                    )

                with (
                    patch("common.t3code_steps.install_package", return_value=True),
                    patch("common.t3code_steps._ensure_user_manager"),
                    patch(
                        "common.t3code_steps._node_bin_directory", return_value=node_bin
                    ),
                    patch("common.t3code_steps.shutil.which", return_value="/bin/true"),
                    patch("common.t3code_steps._run_as_login_user", side_effect=update),
                    patch(
                        "common.t3code_steps._t3_native_runtime_healthy", return_value=True
                    ),
                    patch("common.t3code_steps._wait_for_t3_service") as readiness,
                    patch(
                        "common.t3code_steps._user_systemctl", side_effect=systemctl
                    ) as manager,
                    patch("common.t3code_steps.os.chown"),
                    patch(
                        "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                        os.path.join(home, "legacy"),
                    ),
                ):
                    self.assertEqual(
                        _install_t3_service(
                            home, "agent", os.getuid(), os.getgid(), workspace,
                            "127.0.0.1", 3773, refresh=True,
                        ),
                        binary,
                    )

                actions = [call.args[2] for call in manager.call_args_list]
                self.assertIn("start" if updater_restarts else "restart", actions)
                self.assertEqual("restart" in actions, not updater_restarts)
                readiness.assert_called_once()

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
                patch("common.t3code_steps._user_systemctl", return_value=SimpleNamespace(
                    returncode=0, stdout="", stderr="",
                )),
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
            legacy_service = os.path.join(home, "basaltwater-t3code.service")
            with open(legacy_service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# legacy basaltwater unit\n")
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
                ["systemctl", "stop", "basaltwater-t3code.service"],
                commands,
            )
            self.assertIn(
                ["systemctl", "disable", "basaltwater-t3code.service"],
                commands,
            )
            self.assertIn(
                ["systemctl", "reset-failed", "basaltwater-t3code.service"],
                commands,
            )

    def test_legacy_service_is_restored_when_readiness_does_not_stabilize(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            node_bin = self._write_node_tools(home)
            self._write_upstream_runtime(home)
            legacy_service = os.path.join(home, "basaltwater-t3code.service")
            with open(legacy_service, "w", encoding="utf-8") as file_obj:
                file_obj.write("# legacy basaltwater unit\n")
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
                ["systemctl", "start", "basaltwater-t3code.service"],
                commands,
            )

    def test_legacy_stop_failure_aborts_before_any_runtime_repair_or_activation(self) -> None:
        for times_out in (False, True):
            with (
                self.subTest(times_out=times_out),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                legacy = os.path.join(home, "legacy.service")
                with open(legacy, "w", encoding="utf-8") as file_obj:
                    file_obj.write("# legacy fixture\n")
                candidate = stack.enter_context(patch(
                    "common.t3code_steps._retained_failed_t3_binary",
                    return_value=("0.1.3", os.path.join(home, "candidate")),
                ))
                commands = stack.enter_context(patch(
                    "common.t3code_steps.run",
                    side_effect=CommandTimeoutError("systemctl stop", 60) if times_out else None,
                    return_value=SimpleNamespace(
                        returncode=7, stdout="context", stderr="stop refused\n" + "x" * 5000 + "\ntail",
                    ),
                ))
                with self.assertRaisesRegex(RuntimeError, "stop.*legacy|stop timed out") as raised:
                    _install_t3_service(*state.arguments, refresh=True)
                self.assertLess(len(str(raised.exception)), 2700)
                if not times_out:
                    for text in ("exit code 7", "stop refused", "output omitted", "tail", "context"):
                        self.assertIn(text, str(raised.exception))
                commands.assert_called_once_with(
                    ["systemctl", "stop", "basaltwater-t3code.service"],
                    check=False, capture_output=True, timeout=60,
                )
                for operation in (candidate, state.native, state.repair, state.updater, state.readiness):
                    operation.assert_not_called()
                actions = [call.args[2] for call in state.manager.call_args_list]
                self.assertFalse(set(actions) & {"stop", "start", "restart", "enable"})
                self.assertTrue(os.path.isfile(legacy))
                self.assertTrue(os.path.isfile(state.marker))

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

    def test_native_repair_requires_successful_stop_and_recovers_on_retry(self) -> None:
        for times_out in (False, True):
            with (
                self.subTest(times_out=times_out),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                state.native.return_value = False

                def fail_stop(username, uid, action, *arguments):
                    if action == "stop":
                        if times_out:
                            raise CommandTimeoutError("systemctl stop t3code", 10)
                        return SimpleNamespace(returncode=1, stdout="", stderr="stop refused")
                    return state.systemctl(username, uid, action, *arguments)

                state.manager.side_effect = fail_stop
                with self.assertRaisesRegex((RuntimeError, TimeoutError), "stop|timed out"):
                    _install_t3_service(*state.arguments)
                state.repair.assert_not_called()
                state.readiness.assert_not_called()
                self.assertTrue(os.path.isfile(state.marker))
                self.assertFalse(any(
                    "✓ T3 Code user service" in call.args[0]
                    for call in state.messages.call_args_list
                ))
                actions = [call.args[2] for call in state.manager.call_args_list]
                self.assertNotIn("restart", actions)
                self.assertNotIn("start", actions)

                state.manager.side_effect = state.systemctl
                self.assertEqual(_install_t3_service(*state.arguments), state.binary)
                state.repair.assert_called_once_with("agent", home, state.node_bin, state.binary)
                state.readiness.assert_called_once()
                self.assertFalse(os.path.exists(state.marker))

    def test_optional_candidate_probe_and_repair_failures_allow_current_runtime_checks(self) -> None:
        for phase, error in (
            ("probe", RuntimeError("probe failed")),
            ("probe", CommandTimeoutError("candidate probe", 10)),
            ("repair", CommandTimeoutError("candidate repair", 10)),
        ):
            for updater_code in (0, 1):
                with (
                    self.subTest(phase=phase, error=type(error), updater_code=updater_code),
                    tempfile.TemporaryDirectory() as home,
                    ExitStack() as stack,
                ):
                    state = self._service_fixture(home, stack)
                    candidate = os.path.join(home, "retained", "t3")
                    stack.enter_context(patch(
                        "common.t3code_steps._retained_failed_t3_binary",
                        return_value=("0.1.1", candidate),
                    ))

                    def probe(_user, _home, _node, binary):
                        if binary == candidate:
                            if phase == "probe":
                                raise error
                            return False
                        return True

                    state.native.side_effect = probe
                    state.repair.side_effect = error
                    state.updater.return_value = SimpleNamespace(
                        returncode=updater_code, stdout="", stderr="update unavailable",
                    )
                    self.assertEqual(
                        _install_t3_service(*state.arguments, refresh=True), state.binary,
                    )
                    state.updater.assert_called_once()
                    state.native.assert_any_call("agent", home, state.node_bin, state.binary)
                    state.readiness.assert_called_once()
                    self.assertEqual(state.repair.call_count, int(phase == "repair"))
                    self.assertTrue(any(
                        "Could not repair retained" in call.args[0]
                        for call in state.messages.call_args_list
                    ))

    def test_pending_configuration_survives_failures_and_reconciles_without_extra_restart(self) -> None:
        for phase in (
            "initial-reload", "initial-reload-timeout", "final-reload",
            "enable-new-invocation", "restart", "restart-timeout", "readiness",
        ):
            with (
                self.subTest(phase=phase),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                reloads = 0

                def fail_action(username, uid, action, *arguments):
                    nonlocal reloads
                    if action == "daemon-reload":
                        reloads += 1
                    fail = (
                        (phase.startswith("initial-reload") and action == "daemon-reload" and reloads == 1)
                        or (phase == "final-reload" and action == "daemon-reload" and reloads == 2)
                        or (phase == "enable-new-invocation" and action == "enable")
                        or (phase.startswith("restart") and action == "restart")
                    )
                    if fail:
                        if phase == "restart-timeout":
                            state.systemctl(username, uid, action, *arguments)
                        if "timeout" in phase:
                            raise CommandTimeoutError(f"systemctl {action}", 10)
                        return SimpleNamespace(returncode=1, stdout="", stderr="injected failure")
                    return state.systemctl(username, uid, action, *arguments)

                def update(*_args, **_kwargs):
                    if phase == "enable-new-invocation":
                        state.invocation = "b" * 32
                    return SimpleNamespace(returncode=1, stdout="", stderr="update failed")

                state.manager.side_effect = fail_action
                state.updater.side_effect = update
                if phase == "readiness":
                    state.readiness.side_effect = RuntimeError("readiness failed")
                with self.assertRaises((RuntimeError, TimeoutError)):
                    _install_t3_service(*state.arguments, refresh=True)
                with open(state.marker, encoding="utf-8") as file_obj:
                    pending = json.load(file_obj)
                self.assertEqual(pending["original_invocation"], "a" * 32)
                self.assertEqual(pending["reload_confirmed"], not phase.startswith("initial-reload"))
                self.assertEqual(os.stat(state.marker).st_mode & 0o777, 0o600)
                already_started = state.invocation != "a" * 32

                state.manager.reset_mock()
                state.manager.side_effect = state.systemctl
                state.readiness.side_effect = None
                self.assertEqual(_install_t3_service(*state.arguments), state.binary)
                actions = [call.args[2] for call in state.manager.call_args_list]
                self.assertEqual("restart" in actions, not already_started)
                self.assertIn("start" if already_started else "restart", actions)
                self.assertFalse(os.path.exists(state.marker))

                state.manager.reset_mock()
                self.assertEqual(_install_t3_service(*state.arguments), state.binary)
                self.assertNotIn("restart", [call.args[2] for call in state.manager.call_args_list])
                self.assertFalse(os.path.exists(state.marker))

    def test_configuration_recovery_marker_is_written_before_managed_files(self) -> None:
        for shim_present in (False, True):
            with (
                self.subTest(shim_present=shim_present),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                from lib.atomic_io import write_json_atomic

                if shim_present:
                    _install_t3_npm_shim(home, state.node_bin, os.getuid(), os.getgid())

                def record(path, payload, **kwargs):
                    self.assertEqual(os.path.exists(os.path.join(
                        home, ".local", "share", "basaltwater", "t3-npm", "bin", "npm",
                    )), shim_present)
                    self.assertFalse(os.path.exists(os.path.join(
                        home, ".config", "systemd", "user", "t3code.service.d", "basaltwater.conf",
                    )))
                    write_json_atomic(path, payload, **kwargs)

                with patch("common.t3code_steps.write_json_atomic", side_effect=record):
                    # Stop before recording the successful reload checkpoint.
                    state.manager.side_effect = lambda *_args: SimpleNamespace(
                        returncode=1, stdout="", stderr="reload refused",
                    )
                    with self.assertRaisesRegex(RuntimeError, "Could not load"):
                        _install_t3_service(*state.arguments)
                self.assertTrue(os.path.isfile(state.marker))

    def test_start_racing_reload_does_not_falsely_apply_pending_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            state = self._service_fixture(home, stack)
            reloads = 0

            def racing_reload(username, uid, action, *arguments):
                nonlocal reloads
                if action == "daemon-reload":
                    reloads += 1
                    if reloads == 1:
                        state.invocation = "b" * 32
                return state.systemctl(username, uid, action, *arguments)

            state.manager.side_effect = racing_reload
            _install_t3_service(*state.arguments, refresh=True)
            actions = [call.args[2] for call in state.manager.call_args_list]
            self.assertEqual(actions.count("restart"), 1)
            self.assertNotEqual(state.invocation, "b" * 32)
            self.assertFalse(os.path.exists(state.marker))

    def test_failed_updater_with_new_invocation_still_requires_active_runtime_validation(self) -> None:
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            state = self._service_fixture(home, stack)

            def update(*_args, **_kwargs):
                state.invocation = "b" * 32
                return SimpleNamespace(returncode=1, stdout="", stderr="update failed")

            state.updater.side_effect = update
            state.native.side_effect = CommandTimeoutError("active native probe", 30)
            with self.assertRaises(CommandTimeoutError):
                _install_t3_service(*state.arguments, refresh=True)
            state.native.assert_called_once_with("agent", home, state.node_bin, state.binary)
            state.readiness.assert_not_called()
            self.assertTrue(os.path.exists(state.marker))
            self.assertNotIn("restart", [call.args[2] for call in state.manager.call_args_list])
            self.assertFalse(any(
                "✓ T3 Code user service" in call.args[0]
                for call in state.messages.call_args_list
            ))

    def test_reconciliation_rejects_unsafe_and_malformed_state_before_host_calls(self) -> None:
        for kind in (
            "symlink", "hardlink", "directory", "fifo", "malformed", "oversized",
            "schema", "boolean-version", "duplicate", "invocation", "public",
        ):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                payload = b'{"version":1,"original_invocation":null,"reload_confirmed":false}'
                destination = os.path.join(home, "preserved")
                with open(destination, "wb") as file_obj:
                    file_obj.write(payload)
                os.chmod(destination, 0o600)
                if kind == "symlink":
                    os.symlink(destination, state.marker)
                elif kind == "hardlink":
                    os.link(destination, state.marker)
                elif kind == "directory":
                    os.mkdir(state.marker)
                elif kind == "fifo":
                    os.mkfifo(state.marker, 0o600)
                else:
                    content = {
                        "malformed": b"not JSON",
                        "oversized": b" " * 4097,
                        "schema": payload.replace(b'"version":1', b'"version":2'),
                        "boolean-version": payload.replace(b'"version":1', b'"version":true'),
                        "duplicate": payload.replace(b'"version":1', b'"version":1,"version":1'),
                        "invocation": payload.replace(b"null", b'"invalid"'),
                        "public": payload,
                    }[kind]
                    with open(state.marker, "wb") as file_obj:
                        file_obj.write(content)
                    os.chmod(state.marker, 0o644 if kind == "public" else 0o600)
                with self.assertRaisesRegex(RuntimeError, "Invalid T3 Code reconciliation state"):
                    _install_t3_service(*state.arguments)
                self.assertTrue(os.path.lexists(state.marker))
                with open(destination, "rb") as file_obj:
                    self.assertEqual(file_obj.read(), payload)
                state.manager.assert_not_called()
                state.updater.assert_not_called()

    def test_reconciliation_rejects_unsafe_lock_files(self) -> None:
        for kind in ("symlink", "hardlink", "directory", "fifo"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as home,
                ExitStack() as stack,
            ):
                state = self._service_fixture(home, stack)
                destination = os.path.join(home, "preserved")
                with open(destination, "w", encoding="utf-8") as file_obj:
                    file_obj.write("preserved\n")
                os.chmod(destination, 0o600)
                lock = state.marker + ".lock"
                if kind == "symlink":
                    os.symlink(destination, lock)
                elif kind == "hardlink":
                    os.link(destination, lock)
                elif kind == "directory":
                    os.mkdir(lock)
                else:
                    os.mkfifo(lock, 0o600)
                with self.assertRaises((OSError, RuntimeError)):
                    _install_t3_service(*state.arguments)
                self.assertTrue(os.path.lexists(lock))
                with open(destination, encoding="utf-8") as file_obj:
                    self.assertEqual(file_obj.read(), "preserved\n")
                state.manager.assert_not_called()

    def test_reconciliation_lock_prevents_concurrent_setup_and_false_marker_clear(self) -> None:
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            state = self._service_fixture(home, stack)

            def readiness(*_args):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    _install_t3_service(*state.arguments)
                # A replaced checkpoint must not be cleared by an earlier attempt.
                with open(state.marker, encoding="utf-8") as file_obj:
                    pending = json.load(file_obj)
                pending["original_invocation"] = "c" * 32
                with open(state.marker, "w", encoding="utf-8") as file_obj:
                    json.dump(pending, file_obj)

            state.readiness.side_effect = readiness
            _install_t3_service(*state.arguments)
            with open(state.marker, encoding="utf-8") as file_obj:
                self.assertEqual(json.load(file_obj)["original_invocation"], "c" * 32)

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
                        "protocol": 3,
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

    def test_failed_retained_candidate_repair_does_not_block_managed_runtime(self) -> None:
        for update_succeeds in (True, False):
            with (
                self.subTest(update_succeeds=update_succeeds),
                tempfile.TemporaryDirectory() as home,
            ):
                workspace = os.path.join(home, "repos")
                os.makedirs(workspace)
                node_bin = self._write_node_tools(home)
                active_binary = self._write_upstream_native_runtime(
                    home, "0.0.45", protocol=3
                )
                candidate_binary = os.path.join(home, "retained", "t3")
                completed = SimpleNamespace(returncode=0, stdout="", stderr="")

                def update(*_args, **_kwargs):
                    if update_succeeds:
                        self._write_upstream_native_runtime(home, "0.1.0", protocol=3)
                        return completed
                    return SimpleNamespace(returncode=1, stdout="", stderr="unavailable")

                with (
                    patch("common.t3code_steps.install_package", return_value=True),
                    patch("common.t3code_steps._ensure_user_manager"),
                    patch(
                        "common.t3code_steps._node_bin_directory", return_value=node_bin
                    ),
                    patch("common.t3code_steps.shutil.which", return_value="/bin/true"),
                    patch(
                        "common.t3code_steps._run_as_login_user", side_effect=update
                    ) as updater,
                    patch(
                        "common.t3code_steps._retained_failed_t3_binary",
                        return_value=("0.0.46", candidate_binary),
                    ),
                    patch(
                        "common.t3code_steps._t3_native_runtime_healthy",
                        side_effect=lambda _user, _home, _node, binary: binary != candidate_binary,
                    ),
                    patch(
                        "common.t3code_steps._rebuild_t3_native_runtime",
                        side_effect=RuntimeError("cannot repair this candidate"),
                    ) as repair,
                    patch("common.t3code_steps._wait_for_t3_service") as readiness,
                    patch("common.t3code_steps._user_systemctl", return_value=completed),
                    patch("common.t3code_steps.os.chown"),
                    patch("builtins.print") as messages,
                    patch(
                        "common.t3code_steps.LEGACY_T3_SERVICE_FILE",
                        os.path.join(home, "legacy"),
                    ),
                ):
                    result = _install_t3_service(
                        home, "agent", os.getuid(), os.getgid(), workspace,
                        "127.0.0.1", 3773, refresh=True,
                    )

                self.assertEqual(result, _active_t3_binary(home))
                self.assertEqual(result == active_binary, not update_succeeds)
                updater.assert_called_once()
                repair.assert_called_once_with("agent", home, node_bin, candidate_binary)
                readiness.assert_called_once()
                self.assertTrue(
                    any(
                        "Could not repair retained" in call.args[0]
                        for call in messages.call_args_list
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
                ) as rebuild_command,
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
            self.assertEqual(rebuild_command.call_args.kwargs["timeout"], 600)

    def test_native_probe_has_a_short_bound_and_propagates_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            binary = self._write_upstream_runtime(home)
            node_bin = self._write_node_tools(home)
            with patch(
                "common.t3code_steps._run_as_login_user",
                side_effect=CommandTimeoutError("native probe", 30),
            ) as probe:
                with self.assertRaises(CommandTimeoutError):
                    _t3_native_runtime_healthy("agent", home, node_bin, binary)
            self.assertEqual(probe.call_args.kwargs["timeout"], 30)

    def test_endpoint_readiness_rejects_http_errors(self) -> None:
        from urllib.error import HTTPError

        for status in (200, 401, 404, 503):
            with self.subTest(status=status):
                response = MagicMock()
                response.__enter__.return_value.status = status
                error = HTTPError("http://127.0.0.1:3773/", status, "error", {}, None)
                with patch(
                    "common.t3code_steps.open_loopback",
                    return_value=response,
                    side_effect=error if status >= 400 else None,
                ):
                    self.assertEqual(
                        _t3_local_endpoint_reachable("0.0.0.0", 3773), status == 200
                    )

    def test_endpoint_readiness_uses_only_literal_loopbacks(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.status = 200
        for host, target in (
            ("127.0.0.2", "127.0.0.2"), ("0.0.0.0", "127.0.0.1"),
            ("::", "[::1]"), ("[::]", "[::1]"), ("::1", "[::1]"),
            ("localhost", "127.0.0.1"),
        ):
            with self.subTest(host=host), patch(
                "common.t3code_steps.open_loopback", return_value=response,
            ) as probe:
                self.assertTrue(_t3_local_endpoint_reachable(host, 3773))
                probe.assert_called_once_with(f"http://{target}:3773/", timeout=2)

    def test_localhost_readiness_falls_back_to_ipv6_without_accepting_http_errors(self) -> None:
        from urllib.error import HTTPError

        response = MagicMock()
        response.__enter__.return_value.status = 200
        for first_error in (OSError("no IPv4"), HTTPError("http://127.0.0.1:3773/", 401, "auth", {}, None)):
            with self.subTest(error=first_error), patch(
                "common.t3code_steps.open_loopback", side_effect=[first_error, response],
            ) as probe:
                self.assertTrue(_t3_local_endpoint_reachable("LOCALHOST", 3773))
                self.assertEqual([call.args[0] for call in probe.call_args_list], [
                    "http://127.0.0.1:3773/", "http://[::1]:3773/",
                ])
        with patch("common.t3code_steps.open_loopback", side_effect=OSError("not listening")):
            self.assertFalse(_t3_local_endpoint_reachable("localhost", 3773))

    def test_service_invocation_requires_a_valid_systemd_id(self) -> None:
        for code, output, expected in (
            (0, "A" * 32 + "\n", "a" * 32),
            (0, "", None),
            (0, "unexpected output", None),
            (1, "a" * 32, None),
        ):
            with self.subTest(code=code, output=output), patch(
                "common.t3code_steps._user_systemctl",
                return_value=SimpleNamespace(returncode=code, stdout=output),
            ):
                self.assertEqual(_t3_service_invocation("agent", 1000), expected)

    def test_readiness_requires_three_consecutive_healthy_checks(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="a" * 32, stderr="")
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

    def test_readiness_does_not_count_checks_across_service_restarts(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("common.t3code_steps._user_systemctl", return_value=completed),
            patch(
                "common.t3code_steps._t3_local_endpoint_reachable", return_value=True
            ) as endpoint,
            patch(
                "common.t3code_steps._t3_service_invocation",
                side_effect=["a" * 32, "b" * 32, "b" * 32, "b" * 32],
            ),
            patch("common.t3code_steps.time.sleep"),
        ):
            _wait_for_t3_service("agent", 1000, "127.0.0.1", 3773, "/home/agent")

        self.assertEqual(endpoint.call_count, 4)

    def test_readiness_waits_until_service_identity_can_be_verified(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch("common.t3code_steps._user_systemctl", return_value=completed),
            patch(
                "common.t3code_steps._t3_local_endpoint_reachable", return_value=True
            ) as endpoint,
            patch(
                "common.t3code_steps._t3_service_invocation",
                side_effect=[None, None, "a" * 32, "a" * 32, "a" * 32],
            ),
            patch("common.t3code_steps.time.sleep"),
        ):
            _wait_for_t3_service("agent", 1000, "127.0.0.1", 3773, "/home/agent")

        self.assertEqual(endpoint.call_count, 5)

    def test_readiness_allows_slow_database_migration_without_restarting(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="a" * 32, stderr="")
        elapsed = [0.0]

        def sleep(seconds):
            elapsed[0] += seconds

        with (
            patch("common.t3code_steps._user_systemctl", return_value=completed) as service,
            patch(
                "common.t3code_steps._t3_local_endpoint_reachable",
                side_effect=lambda *_args: elapsed[0] >= 90,
            ),
            patch("common.t3code_steps.time.monotonic", side_effect=lambda: elapsed[0]),
            patch("common.t3code_steps.time.sleep", side_effect=sleep),
            patch("builtins.print") as progress,
        ):
            _wait_for_t3_service("agent", 1000, "0.0.0.0", 3773, "/home/agent")

        self.assertEqual(elapsed[0], 92)
        self.assertEqual(progress.call_count, 3)
        self.assertTrue(
            all(call.args[2] in {"is-active", "show"} for call in service.call_args_list)
        )

    def test_readiness_fails_promptly_when_systemd_exhausts_restarts(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        inactive = SimpleNamespace(returncode=3, stdout="", stderr="")

        def systemctl(_username, _uid, action, *_args):
            return completed if action == "is-failed" else inactive

        with (
            patch("common.t3code_steps._user_systemctl", side_effect=systemctl),
            patch("common.t3code_steps._t3_local_endpoint_reachable") as endpoint,
            patch("common.t3code_steps.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "systemd reports a failed service"):
                _wait_for_t3_service("agent", 1000, "0.0.0.0", 3773, "/home/agent")

        endpoint.assert_not_called()
        sleep.assert_not_called()

    def test_readiness_timeout_bounds_slow_endpoint_checks(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        elapsed = [0.0]

        def endpoint(*_args):
            elapsed[0] += 2
            return False

        def sleep(seconds):
            elapsed[0] += seconds

        with (
            patch("common.t3code_steps._T3_READINESS_TIMEOUT", 5),
            patch("common.t3code_steps._user_systemctl", return_value=completed),
            patch("common.t3code_steps._t3_local_endpoint_reachable", side_effect=endpoint),
            patch("common.t3code_steps.time.monotonic", side_effect=lambda: elapsed[0]),
            patch("common.t3code_steps.time.sleep", side_effect=sleep),
        ):
            with self.assertRaisesRegex(RuntimeError, "within 5 seconds; inspect"):
                _wait_for_t3_service("agent", 1000, "0.0.0.0", 3773, "/home/agent")

        self.assertEqual(elapsed[0], 5)

    def test_stable_wrapper_follows_service_selected_version(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, ".local", "bin")
            os.makedirs(bin_dir)
            wrapper = os.path.join(bin_dir, "t3")
            self.assertTrue(_write_passthrough_wrapper(wrapper, home))
            with open(wrapper, encoding="utf-8") as file_obj:
                content = file_obj.read()
            self.assertIn("service-state.json", content)
            self.assertIn("minimum_protocol=2", content)
            self.assertIn('value["protocol"] >= minimum_protocol', content)
            self.assertIn('NVM_DIR="$HOME/.nvm"', content)
            self.assertIn("versions", content)
            self.assertIn(
                'paths=[["t3"], ["node_modules", "t3", "dist", "bin.mjs"]]',
                content,
            )
            self.assertIn("binary=next(", content)
            self.assertIn('exec "$binary" "$@"', content)
            self.assertNotIn("npx", content)

            runtime = os.path.join(home, ".t3", "runtime")
            version = os.path.join(runtime, "versions", "0.0.44")
            binary = os.path.join(version, "t3")
            os.makedirs(version, exist_ok=True)
            with open(binary, "w", encoding="utf-8") as file_obj:
                file_obj.write('#!/bin/sh\nprintf "%s\\n" "$1"\n')
            os.chmod(binary, 0o755)
            state_file = os.path.join(runtime, "service-state.json")
            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump({"protocol": 3, "activeVersion": "0.0.44"}, file_obj)

            result = subprocess.run(
                [wrapper, "protocol-three"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "protocol-three")

            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump({"protocol": 4, "activeVersion": "0.0.44"}, file_obj)
            result = subprocess.run(
                [wrapper, "protocol-four"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "protocol-four")

            os.unlink(binary)
            legacy_binary = os.path.join(
                version,
                "node_modules",
                "t3",
                "dist",
                "bin.mjs",
            )
            os.makedirs(os.path.dirname(legacy_binary), exist_ok=True)
            with open(legacy_binary, "w", encoding="utf-8") as file_obj:
                file_obj.write('#!/bin/sh\nprintf "%s\\n" "$1"\n')
            os.chmod(legacy_binary, 0o755)
            result = subprocess.run(
                [wrapper, "legacy-layout"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "legacy-layout")

            with open(state_file, "w", encoding="utf-8") as file_obj:
                json.dump({"protocol": 1, "activeVersion": "0.0.44"}, file_obj)
            result = subprocess.run(
                [wrapper, "unsupported-protocol"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)

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
                patch("common.t3code_steps._remove_device_pairing") as remove_portal,
                patch("common.t3code_steps._remove_t3_https"),
                patch("common.t3code_steps._configure_t3_https", return_value={}),
                patch("common.t3code_steps.T3_ADMIN_PAIR_SCRIPT", admin_pair_script),
            ):
                install_t3code_web(
                    self._config(
                        web_interface_sources=None,
                        web_interface_host="127.0.0.1",
                        agent_workspace=workspace,
                        disable_device_pairing=True,
                    )
                )

            install_service.assert_called_once()
            remove_portal.assert_called_once_with()
            self.assertTrue(install_service.call_args.kwargs["refresh"])

            wrapper = os.path.join(
                home,
                ".local",
                "bin",
                "basaltwater-t3code-pairing-provider",
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
