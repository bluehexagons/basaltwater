"""Tests for protected, provider-native device enrollment."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import remote_setup
from common.service_tools.device_pairing_service import (
    PairingError,
    PairingRequestHandler,
    PairingState,
    _safe_pairing_url,
    _load_provider_config,
    _public_base_url,
)
from common.t3code_steps import (
    _configure_device_pairing,
    _configure_firewall,
    _remove_connect_restart_units,
    _remove_device_pairing,
    _set_pairing_t3_https_port,
    _write_passthrough_wrapper,
    install_t3code_web,
)
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.cache import merge_setup_configs
from lib.interactive_setup import run_interactive_setup
from lib.setup_common import prepare_device_pairing_payload
from lib.validation import validate_web_interface_settings


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "agent-vm",
        "username": "agent",
        "system_type": "server_dev",
        "agent_tools": ["codex"],
        "web_interfaces": ["t3code"],
        "web_interface_sources": ["192.168.0.0/24"],
        "device_pairing_providers": ["t3code"],
    }
    values.update(overrides)
    return SetupConfig(**values)


class DevicePairingConfigTest(unittest.TestCase):
    def test_cli_and_config_preserve_policy_but_not_secret_source(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "agent-vm",
                "agent",
                "--agent-tool",
                "codex",
                "--web-interface",
                "t3code",
                "--web-interface-source",
                "192.168.0.0/24",
                "--device-pairing",
                "t3code",
                "--device-pairing-port",
                "4774",
                "--device-pairing-auth-file",
                "/run/secrets/pairing.htpasswd",
            ]
        )
        config = SetupConfig.from_args(args, "server_dev")
        validate_web_interface_settings(config)

        self.assertEqual(config.device_pairing_providers, ["t3code"])
        self.assertEqual(config.device_pairing_port, 4774)
        remote = " ".join(config.to_remote_args())
        saved = " ".join(config.to_setup_command())
        self.assertIn("--device-pairing t3code", remote)
        self.assertIn("--device-pairing-port 4774", remote)
        self.assertNotIn("pairing.htpasswd", remote)
        self.assertIn("--device-pairing t3code", saved)
        self.assertNotIn("pairing.htpasswd", saved)
        serialized = config.to_dict()
        self.assertNotIn("device_pairing_auth_file", serialized)
        self.assertNotIn("device_pairing_auth_password", serialized)

    def test_cli_password_defaults_portal_username_and_stays_transient(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "agent-vm",
                "agent",
                "--agent-tool",
                "codex",
                "--web-interface",
                "t3code",
                "--device-pairing",
                "t3code",
                "--device-pairing-password",
                "portal-secret",
            ]
        )
        config = SetupConfig.from_args(args, "server_dev")
        validate_web_interface_settings(config)

        self.assertEqual(config.device_pairing_auth_username, "agent")
        self.assertEqual(config.device_pairing_auth_password, "portal-secret")
        self.assertNotIn("portal-secret", " ".join(config.to_remote_args()))
        self.assertNotIn("portal-secret", " ".join(config.to_setup_command()))
        self.assertNotIn("portal-secret", str(config.to_dict()))

    def test_empty_cli_password_is_rejected(self) -> None:
        config = _config(device_pairing_auth_password="")
        with self.assertRaisesRegex(ValueError, "non-empty username and password"):
            validate_web_interface_settings(config)

    def test_provider_requires_matching_web_interface(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
            device_pairing_providers=["t3code"],
        )
        with self.assertRaisesRegex(ValueError, "requires --web-interface"):
            validate_web_interface_settings(config)

    def test_pairing_port_must_differ_from_t3_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "must differ"):
            _config(device_pairing_port=3773)

    def test_cli_does_not_silently_replace_invalid_zero_port(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "agent-vm",
                "agent",
                "--agent-tool",
                "codex",
                "--web-interface",
                "t3code",
                "--device-pairing",
                "t3code",
                "--device-pairing-port",
                "0",
            ]
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            SetupConfig.from_args(args, "server_dev")

    def test_auth_file_and_interactive_secret_are_mutually_exclusive(self) -> None:
        config = _config(
            device_pairing_auth_file="/tmp/auth",
            device_pairing_auth_username="agent",
            device_pairing_auth_password="secret",
        )
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            validate_web_interface_settings(config)

    def test_explicit_disable_removes_cached_pairing_policy(self) -> None:
        cached = _config()
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["agent-vm", "agent", "--no-device-pairing"])
        update = SetupConfig.from_args(args, "server_dev")

        merged = merge_setup_configs(cached, update)

        self.assertIsNone(merged.device_pairing_providers)
        self.assertTrue(merged.disable_device_pairing)

    def test_interactive_prompts_for_auth_when_provider_was_explicit(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "agent-vm",
                "agent",
                "--agent-tool",
                "codex",
                "--web-interface",
                "t3code",
                "--web-interface-source",
                "192.168.0.0/24",
                "--device-pairing",
                "t3code",
                "--interactive",
            ]
        )
        responses = ["", "none", "none", "no", "pair-admin"]
        with (
            patch("lib.interactive_setup.sys.stdin.isatty", return_value=True),
            patch("lib.interactive_setup.sys.stdout.isatty", return_value=True),
            patch("builtins.input", side_effect=responses),
            patch(
                "lib.interactive_setup.getpass.getpass",
                side_effect=["portal-secret", "portal-secret"],
            ),
        ):
            run_interactive_setup(args)

        self.assertEqual(args.device_pairing_auth_username, "pair-admin")
        self.assertEqual(args.device_pairing_auth_password, "portal-secret")


class DevicePairingPayloadTest(unittest.TestCase):
    def test_stages_valid_hashed_password_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source.htpasswd")
            destination = os.path.join(temporary, "payload")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent:$6$salt$hash\n")
            os.chmod(source, 0o600)
            config = _config(device_pairing_auth_file=source)

            prepare_device_pairing_payload(config, destination)

            staged = os.path.join(destination, "htpasswd")
            with open(staged, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "agent:$6$salt$hash\n")
            self.assertEqual(os.stat(staged).st_mode & 0o777, 0o600)

    def test_rejects_plaintext_password_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source.htpasswd")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent:plaintext\n")
            os.chmod(source, 0o600)
            config = _config(device_pairing_auth_file=source)
            with self.assertRaisesRegex(ValueError, "must use crypt"):
                prepare_device_pairing_payload(
                    config, os.path.join(temporary, "payload")
                )

    def test_rejects_unsalted_sha_password_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source.htpasswd")
            with open(source, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent:{SHA}qvTGHdzF6KLavt4PO0gs2a6pQ00=\n")
            os.chmod(source, 0o600)
            config = _config(device_pairing_auth_file=source)
            with self.assertRaisesRegex(ValueError, "crypt-style"):
                prepare_device_pairing_payload(
                    config, os.path.join(temporary, "payload")
                )

    def test_generates_sha512_crypt_hash_without_putting_password_in_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _config(
                device_pairing_auth_username="agent",
                device_pairing_auth_password="very-secret",
            )
            completed = SimpleNamespace(returncode=0, stdout="$6$salt$hash\n")
            with patch("lib.setup_common.subprocess.run", return_value=completed) as mock_run:
                prepare_device_pairing_payload(config, temporary)

            self.assertEqual(mock_run.call_args.args[0], ["openssl", "passwd", "-6", "-stdin"])
            self.assertEqual(mock_run.call_args.kwargs["input"], "very-secret\n")
            self.assertNotIn("very-secret", mock_run.call_args.args[0])

    def test_password_only_uses_target_username_for_htpasswd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _config(device_pairing_auth_password="very-secret")
            completed = SimpleNamespace(returncode=0, stdout="$6$salt$hash\n")
            with patch("lib.setup_common.subprocess.run", return_value=completed):
                prepare_device_pairing_payload(config, temporary)

            with open(
                os.path.join(temporary, "htpasswd"), encoding="utf-8"
            ) as file_obj:
                self.assertEqual(file_obj.read(), "agent:$6$salt$hash\n")

    def test_remote_cleanup_removes_all_uploaded_secret_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent_payload = os.path.join(temporary, "agent")
            pairing_payload = os.path.join(temporary, "pairing")
            web_payload = os.path.join(temporary, "web")
            os.makedirs(agent_payload)
            os.makedirs(pairing_payload)
            os.makedirs(web_payload)
            with (
                patch("lib.cachyos.is_cachyos", return_value=False),
                patch.object(
                    remote_setup, "REMOTE_AGENT_PAYLOAD_DIR", agent_payload
                ),
                patch.object(
                    remote_setup,
                    "REMOTE_DEVICE_PAIRING_PAYLOAD_DIR",
                    pairing_payload,
                ),
                patch.object(remote_setup, "is_dry_run", return_value=False),
                patch.object(remote_setup, "REMOTE_WEB_PANEL_PAYLOAD_DIR", web_payload),
            ):
                remote_setup._remove_secret_payloads()

            self.assertFalse(os.path.exists(agent_payload))
            self.assertFalse(os.path.exists(pairing_payload))
            self.assertFalse(os.path.exists(web_payload))


class PairingBrokerTest(unittest.TestCase):
    def _provider(self) -> dict[str, object]:
        return {
            "label": "T3 Code",
            "command": ["/opt/t3", "auth", "pairing", "create", "--json"],
            "base_url_flag": "--base-url",
            "url_field": "pairUrl",
            "expires_field": "expiresAt",
            "public_port": 3773,
        }

    def test_issues_provider_link_with_public_base_url_as_argv(self) -> None:
        state = PairingState({"t3code": self._provider()})
        output = json.dumps(
            {
                "pairUrl": "http://192.168.0.41:3773/pair#token=ONETIME",
                "expiresAt": "2026-08-20T18:00:00Z",
            }
        )
        completed = SimpleNamespace(returncode=0, stdout=output, stderr="")
        with patch(
            "common.service_tools.device_pairing_service.subprocess.run",
            return_value=completed,
        ) as mock_run:
            pair_url, expires = state.issue(
                "t3code", "http://192.168.0.41:3773"
            )

        command = mock_run.call_args.args[0]
        self.assertEqual(command[-2:], ["--base-url", "http://192.168.0.41:3773"])
        self.assertEqual(pair_url, "http://192.168.0.41:3773/pair#token=ONETIME")
        self.assertEqual(expires, "2026-08-20T18:00:00Z")

    def test_https_pairing_page_uses_the_t3_forward_port(self) -> None:
        headers = Message()
        headers["X-Forwarded-Host"] = "agent-vm:8445"
        headers["X-Forwarded-Proto"] = "https"
        handler = SimpleNamespace(headers=headers)
        self.assertEqual(
            _public_base_url(handler, 3773, 8444),
            "https://agent-vm:8444",
        )

    def test_pairing_page_hands_off_to_native_t3_without_issuing_credentials(self) -> None:
        provider = {**self._provider(), "https_public_port": 8444}
        state = PairingState({"t3code": provider})
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/devices"
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET /devices HTTP/1.1"
        handler.headers = Message()
        handler.headers["X-Forwarded-Proto"] = "https"
        handler.headers["X-Forwarded-Host"] = "agent-vm:8445"
        handler.wfile = BytesIO()
        with (
            patch.object(state, "issue") as issue,
            patch("common.service_tools.device_pairing_service.subprocess.run") as run,
            patch("common.service_tools.device_pairing_service.subprocess.Popen") as popen,
        ):
            handler.do_GET()
        response = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("Pair a device", response)
        self.assertIn("Pair this browser", response)
        self.assertIn("Pair another T3 Code client", response)
        self.assertIn('href="https://agent-vm:8444/"', response)
        self.assertIn('target="_blank" rel="noopener noreferrer"', response)
        self.assertIn("t3 connect link", response)
        self.assertIn("t3 service restart", response)
        self.assertIn("Never share a pairing link", response)
        self.assertNotIn('action="/connect/', response)
        self.assertNotIn("Start authorization", response)
        issue.assert_not_called()
        run.assert_not_called()
        popen.assert_not_called()

    def test_t3_handoff_uses_validated_origin_or_existing_connection_guidance(self) -> None:
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        for scheme, host, expected in (
            ("http", "agent-vm:3774", "http://agent-vm:3773/"),
            ("https", "[2001:db8::1]:8445", "https://[2001:db8::1]:8444/"),
            ("https", "", None),
            ("https", "<script>", None),
            ("javascript", "agent-vm", None),
        ):
            with self.subTest(scheme=scheme, host=host):
                handler.headers = Message()
                handler.headers["X-Forwarded-Proto"] = scheme
                handler.headers["X-Forwarded-Host"] = host
                body = handler._t3_connect_section({**self._provider(), "https_public_port": 8444})
                if expected:
                    self.assertIn(f'href="{expected}"', body)
                else:
                    self.assertNotIn("href=", body)
                    self.assertIn("Open your existing T3 Code connection", body)

    def test_retired_connect_routes_cannot_execute_legacy_provider_commands(self) -> None:
        provider = self._provider()
        provider["connect"] = {
            "link_command": ["/opt/t3", "connect", "link"],
            "status_command": ["/opt/t3", "connect", "status"],
            "unlink_command": ["/opt/t3", "connect", "unlink"],
            "restart_request": "/unused/t3-connect-restart",
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = os.path.join(temporary, "providers.json")
            with open(config_path, "w", encoding="utf-8") as file_obj:
                json.dump({"version": 1, "providers": {"t3code": provider}}, file_obj)
            state = PairingState(_load_provider_config(config_path))
        with (
            patch("common.service_tools.device_pairing_service.subprocess.run") as run,
            patch("common.service_tools.device_pairing_service.subprocess.Popen") as popen,
        ):
            for method, intent in (("GET", ""), ("POST", "start"), ("POST", "input"), ("POST", "toggle")):
                with self.subTest(method=method, intent=intent):
                    nonce = state.new_nonce()
                    body = urlencode({"nonce": nonce, "intent": intent, "input": "sample"}).encode()
                    handler = PairingRequestHandler.__new__(PairingRequestHandler)
                    handler.server = SimpleNamespace(pairing_state=state)
                    handler.path = "/connect/t3code"
                    handler.command = method
                    handler.request_version = "HTTP/1.1"
                    handler.requestline = f"{method} /connect/t3code HTTP/1.1"
                    handler.headers = Message()
                    handler.headers["Content-Type"] = "application/x-www-form-urlencoded"
                    handler.headers["Content-Length"] = str(len(body))
                    handler.headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
                    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
                    getattr(handler, "do_" + method)()
                    self.assertIn(" 404 ", handler.wfile.getvalue().decode())
        run.assert_not_called()
        popen.assert_not_called()

    def test_basic_auth_entry_submits_a_nonce_protected_browser_login(self) -> None:
        state = PairingState({"t3code": self._provider()})
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/"
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET / HTTP/1.1"
        handler.headers = Message()
        handler.headers["X-Forwarded-Proto"] = "https"
        handler.headers["X-Forwarded-Host"] = "agent-vm:8445"
        handler.wfile = BytesIO()
        with patch.object(state, "issue") as issue:
            handler.do_GET()
        response = handler.wfile.getvalue().decode("utf-8")
        self.assertIn('name="intent" value="open"', response)
        self.assertIn('action="/pair/t3code"', response)
        self.assertIn('requestSubmit()', response)
        self.assertIn("script-src 'nonce-", response)
        self.assertIn("form-action 'self' https://agent-vm:3773;", response)
        self.assertIn("HttpOnly; SameSite=Strict; Secure", response)
        self.assertIn('href="/devices"', response)
        self.assertIn("Cache-Control: no-store", response)
        issue.assert_not_called()

    def test_browser_open_redirect_preserves_token_fragment_and_limits_replay(self) -> None:
        state = PairingState({"t3code": {**self._provider(), "https_public_port": 8444}})
        nonce = state.new_nonce()
        body = urlencode({"nonce": nonce, "intent": "open"}).encode("utf-8")
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/pair/t3code"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /pair/t3code HTTP/1.1"
        handler.headers = Message()
        for name, value in {
            "Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body)),
            "Cookie": f"basaltwater_pairing_nonce={nonce}", "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "agent-vm:8445", "X-Real-IP": "192.168.0.12",
        }.items():
            handler.headers[name] = value
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        pair_url = "https://agent-vm:8444/pair#token=ONETIME"
        with patch.object(state, "issue", return_value=(pair_url, "soon")) as issue:
            handler.do_POST()
            response = handler.wfile.getvalue().decode("utf-8")
            self.assertIn(" 303 ", response)
            self.assertIn(f"Location: {pair_url}\r\n", response)
            self.assertIn("Cache-Control: no-store", response)
            self.assertIn("Referrer-Policy: no-referrer", response)
            handler.rfile = BytesIO(body)
            handler.wfile = BytesIO()
            handler.do_POST()
            self.assertIn(" 403 ", handler.wfile.getvalue().decode("utf-8"))
        issue.assert_called_once_with("t3code", "https://agent-vm:8444")

    def test_rejects_provider_redirect_to_another_origin(self) -> None:
        with self.assertRaises(PairingError):
            _safe_pairing_url(
                "https://attacker.example/pair#token=secret",
                "http://192.168.0.41:3773",
            )

    def test_rejects_control_characters_in_provider_url(self) -> None:
        with self.assertRaises(PairingError):
            _safe_pairing_url(
                "http://192.168.0.41:3773/pair#token=secret\r\nX-Bad: yes",
                "http://192.168.0.41:3773",
            )

    def test_rejects_malformed_provider_url(self) -> None:
        with self.assertRaises(PairingError):
            _safe_pairing_url(
                "https://[malformed/pair#token=secret",
                "https://agent-vm:8444",
            )

    def test_nonce_is_single_use(self) -> None:
        state = PairingState({"t3code": self._provider()})
        nonce = state.new_nonce()
        self.assertTrue(state.nonce_is_valid(nonce))
        self.assertTrue(state.consume_nonce(nonce))
        self.assertFalse(state.nonce_is_valid(nonce))
        self.assertFalse(state.consume_nonce(nonce))

    def test_pairing_issuance_is_rate_limited_per_source(self) -> None:
        state = PairingState({"t3code": self._provider()})
        for _index in range(5):
            self.assertTrue(state.allow_request("192.168.0.12"))
        self.assertFalse(state.allow_request("192.168.0.12"))
        self.assertTrue(state.allow_request("192.168.0.13"))

    def test_http_portal_renders_explicit_single_use_provider_link(self) -> None:
        state = PairingState({"t3code": self._provider()})
        nonce = state.new_nonce()
        body = urlencode({"nonce": nonce, "intent": "current"}).encode("utf-8")
        headers = Message()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(body))
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        headers["X-Forwarded-Host"] = "agent-vm"
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Real-IP"] = "192.168.0.12"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/pair/t3code"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /pair/t3code HTTP/1.1"
        handler.headers = headers
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        pairing_url = "http://agent-vm:3773/pair#token=ONETIME"

        with patch.object(
            state,
            "issue",
            return_value=(pairing_url, "2026-08-20T18:00:00Z"),
        ) as issue:
            handler.do_POST()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn(" 200 ", response)
        self.assertNotIn("Location:", response)
        self.assertIn(f'href="{pairing_url}"', response)
        self.assertIn("Pair this browser with T3 Code", response)
        issue.assert_called_once_with("t3code", "http://agent-vm:3773")

    def test_http_portal_rejects_missing_pairing_intent(self) -> None:
        state = PairingState({"t3code": self._provider()})
        nonce = state.new_nonce()
        body = urlencode({"nonce": nonce}).encode("utf-8")
        headers = Message()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(body))
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/pair/t3code"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /pair/t3code HTTP/1.1"
        handler.headers = headers
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()

        with patch.object(state, "issue") as issue:
            handler.do_POST()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn(" 403 ", response)
        issue.assert_not_called()

    def test_pair_route_rejects_connect_input_intent_before_rate_limit(self) -> None:
        state = PairingState({"t3code": self._provider()})
        nonce = state.new_nonce()
        body = urlencode({"nonce": nonce, "intent": "input"}).encode("utf-8")
        headers = Message()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Content-Length"] = str(len(body))
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        headers["X-Real-IP"] = "192.168.0.12"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/pair/t3code"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /pair/t3code HTTP/1.1"
        handler.headers = headers
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()

        with (
            patch.object(state, "allow_request") as allow_request,
            patch.object(state, "issue") as issue,
        ):
            handler.do_POST()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn(" 403 ", response)
        allow_request.assert_not_called()
        issue.assert_not_called()


class DevicePairingRemoteSetupTest(unittest.TestCase):
    def _pairing_setup_paths(self, temporary: str, stack: ExitStack) -> dict[str, str]:
        config_dir = Path(temporary, "config")
        config_dir.mkdir()
        payload = Path(temporary, "payload.htpasswd")
        payload.write_text("agent:$6$salt$hash\n", encoding="utf-8")
        broker = Path(temporary, "broker.py")
        broker.write_text("# broker fixture\n", encoding="utf-8")
        paths = {
            "DEVICE_PAIRING_CONFIG_DIR": str(config_dir),
            "DEVICE_PAIRING_AUTH_FILE": str(config_dir / "htpasswd"),
            "DEVICE_PAIRING_PROVIDERS_FILE": str(config_dir / "providers.json"),
            "DEVICE_PAIRING_PAYLOAD_FILE": str(payload),
            "DEVICE_PAIRING_SERVICE_FILE": str(Path(temporary, "pairing.service")),
            "DEVICE_PAIRING_NGINX_SITE": str(Path(temporary, "nginx-available")),
            "DEVICE_PAIRING_NGINX_LINK": str(Path(temporary, "nginx-enabled")),
            "DEVICE_PAIRING_SOURCE": str(broker),
        }
        stack.enter_context(patch.multiple("common.t3code_steps", **paths))
        account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        stack.enter_context(patch("common.t3code_steps.pwd.getpwnam", return_value=account))
        stack.enter_context(patch("common.t3code_steps.os.chown"))
        stack.enter_context(patch("common.t3code_steps.configure_nginx_auth_failure_ban"))
        stack.enter_context(patch("web.web_steps.install_nginx"))
        return paths

    def test_broker_activation_failures_retry_even_with_an_active_old_process(self) -> None:
        for failed_command in (
            "systemctl daemon-reload",
            "systemctl enable basaltwater-device-pairing.service",
            "systemctl restart basaltwater-device-pairing.service",
        ):
            with self.subTest(command=failed_command), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                paths = self._pairing_setup_paths(temporary, stack)
                execute = stack.enter_context(patch("common.t3code_steps.run"))
                execute.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
                providers_path = Path(paths["DEVICE_PAIRING_PROVIDERS_FILE"])
                providers = json.loads(providers_path.read_text(encoding="utf-8"))
                providers["providers"]["t3code"]["connect"] = {"legacy": True}
                providers_path.write_text(json.dumps(providers), encoding="utf-8")
                Path(paths["DEVICE_PAIRING_SOURCE"]).write_text("# replacement broker\n", encoding="utf-8")
                marker = Path(paths["DEVICE_PAIRING_CONFIG_DIR"], ".broker-restart-pending")

                def fail_activation(command: str, **_kwargs: object) -> SimpleNamespace:
                    if command == failed_command:
                        self.assertTrue(marker.is_file())
                        raise RuntimeError("injected activation failure")
                    disabled = command.startswith("systemctl is-enabled") and " enable " in failed_command
                    return SimpleNamespace(returncode=int(disabled), stdout="", stderr="")

                execute.side_effect = fail_activation
                with self.assertRaisesRegex(RuntimeError, "injected activation failure"):
                    _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
                self.assertTrue(marker.is_file())
                self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("connect", json.loads(providers_path.read_text())["providers"]["t3code"])
                execute.side_effect = None
                execute.reset_mock()
                _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
                execute.assert_any_call("systemctl daemon-reload")
                execute.assert_any_call("systemctl restart basaltwater-device-pairing.service")
                self.assertFalse(marker.exists())
                execute.reset_mock()
                _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
                self.assertFalse(any("systemctl restart" in call.args[0] for call in execute.call_args_list))

    def test_https_port_restart_failure_retries_the_already_written_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            paths = self._pairing_setup_paths(temporary, stack)
            execute = stack.enter_context(patch("common.t3code_steps.run"))
            execute.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)

            def fail_restart(command: str, **_kwargs: object) -> SimpleNamespace:
                if command == "systemctl restart basaltwater-device-pairing.service":
                    raise RuntimeError("injected restart failure")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            execute.side_effect = fail_restart
            with self.assertRaisesRegex(RuntimeError, "injected restart failure"):
                _set_pairing_t3_https_port(8444)
            providers = json.loads(Path(paths["DEVICE_PAIRING_PROVIDERS_FILE"]).read_text())
            self.assertEqual(providers["providers"]["t3code"]["https_public_port"], 8444)
            execute.side_effect = None
            execute.reset_mock()
            _set_pairing_t3_https_port(8444)
            execute.assert_any_call("systemctl restart basaltwater-device-pairing.service")
            execute.reset_mock()
            _set_pairing_t3_https_port(8444)
            execute.assert_not_called()

    def test_pending_broker_restart_refuses_symlinks_and_is_removed_on_opt_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            paths = self._pairing_setup_paths(temporary, stack)
            execute = stack.enter_context(patch("common.t3code_steps.run"))
            execute.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            stack.enter_context(patch("common.t3code_steps.remove_nginx_auth_failure_ban"))
            stack.enter_context(patch("common.t3code_steps.shutil.which", return_value="/usr/sbin/nginx"))
            marker = Path(paths["DEVICE_PAIRING_CONFIG_DIR"], ".broker-restart-pending")
            victim = Path(temporary, "unrelated")
            victim.write_text("preserved", encoding="utf-8")
            marker.symlink_to(victim)
            with self.assertRaisesRegex(RuntimeError, "unsafe device-pairing restart marker"):
                _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
            self.assertEqual(victim.read_text(), "preserved")
            execute.assert_not_called()
            marker.unlink()
            _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
            marker.write_text("pending\n", encoding="utf-8")
            _remove_device_pairing()
            self.assertFalse(Path(paths["DEVICE_PAIRING_CONFIG_DIR"]).exists())
            self.assertEqual(victim.read_text(), "preserved")

    def test_nginx_failure_restores_configuration_and_credentials_before_retry(self) -> None:
        for installed, failed_command in (
            (False, "nginx -t"), (False, "systemctl reload nginx"),
            (True, "nginx -t"), (True, "systemctl reload nginx"),
        ):
            with self.subTest(installed=installed, command=failed_command), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                paths = self._pairing_setup_paths(temporary, stack)
                execute = stack.enter_context(patch("common.t3code_steps.run"))
                execute.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                site = Path(paths["DEVICE_PAIRING_NGINX_SITE"])
                auth = Path(paths["DEVICE_PAIRING_AUTH_FILE"])
                if installed:
                    _configure_device_pairing(_config(), temporary, "/opt/t3", "127.0.0.1", 3773)
                old_site = site.read_bytes() if installed else None
                old_auth = auth.read_bytes() if installed else None
                Path(paths["DEVICE_PAIRING_PAYLOAD_FILE"]).write_text("agent:$6$new$hash\n", encoding="utf-8")

                def fail_nginx(command: str, **_kwargs: object) -> SimpleNamespace:
                    if command == failed_command:
                        if command == "nginx -t":
                            return SimpleNamespace(returncode=1, stdout="", stderr="invalid")
                        raise RuntimeError("injected reload failure")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                execute.side_effect = fail_nginx
                with self.assertRaisesRegex(RuntimeError, "Nginx rejected|injected reload"):
                    _configure_device_pairing(_config(device_pairing_port=4774), temporary, "/opt/t3", "127.0.0.1", 3773)
                self.assertEqual(site.read_bytes() if site.exists() else None, old_site)
                self.assertEqual(auth.read_bytes() if auth.exists() else None, old_auth)
                self.assertEqual(os.path.islink(paths["DEVICE_PAIRING_NGINX_LINK"]), installed)
                execute.side_effect = None
                execute.reset_mock()
                _configure_device_pairing(_config(device_pairing_port=4774), temporary, "/opt/t3", "127.0.0.1", 3773)
                execute.assert_any_call("systemctl reload nginx")
                self.assertIn("listen 127.0.0.1:4774", site.read_text())
                self.assertEqual(auth.read_text(), "agent:$6$new$hash\n")

    def test_connect_bridge_migration_preserves_native_state_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = os.path.join(temporary, ".t3")
            os.mkdir(state_dir)
            native_paths = [os.path.join(state_dir, name) for name in (
                "connect.json", "settings.json", "auth.json", "state.sqlite",
            )]
            for path in native_paths:
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("native state preserved")
            bridge_paths = [os.path.join(temporary, name) for name in ("connect.path", "connect.service")]
            request = os.path.join(state_dir, "basaltwater-connect-restart")
            for path in (*bridge_paths, request):
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("legacy bridge fixture")
            with (
                patch("common.t3code_steps.os.geteuid", return_value=0),
                patch("common.t3code_steps.T3_CONNECT_RESTART_PATH_FILE", bridge_paths[0]),
                patch("common.t3code_steps.T3_CONNECT_RESTART_SERVICE_FILE", bridge_paths[1]),
                patch("common.t3code_steps.run") as run,
            ):
                _remove_connect_restart_units(state_dir)
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    "systemctl disable --now basaltwater-t3code-connect.path",
                    "systemctl disable --now basaltwater-t3code-connect.service",
                    "systemctl daemon-reload",
                ])
                run.reset_mock()
                _remove_connect_restart_units(state_dir)
                run.assert_not_called()
            for path in (*bridge_paths, request):
                self.assertFalse(os.path.lexists(path))
            for path in native_paths:
                with open(path, encoding="utf-8") as file_obj:
                    self.assertEqual(file_obj.read(), "native state preserved")

    def test_pairing_rejects_symlinked_t3_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = os.path.join(temporary, "home")
            os.mkdir(home)
            os.symlink(temporary, os.path.join(home, ".t3"))
            with self.assertRaisesRegex(
                RuntimeError, "unsafe T3 Connect state directory"
            ):
                _configure_device_pairing(
                    _config(),
                    home,
                    "/opt/t3",
                    "127.0.0.1",
                    3773,
                )

    def test_provider_wrapper_refuses_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            victim = os.path.join(temporary, "victim")
            wrapper = os.path.join(temporary, "wrapper")
            with open(victim, "w", encoding="utf-8") as file_obj:
                file_obj.write("unchanged")
            os.symlink(victim, wrapper)

            with self.assertRaisesRegex(RuntimeError, "unsafe managed executable"):
                _write_passthrough_wrapper(wrapper, temporary)

            with open(victim, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "unchanged")

    def test_writes_basic_auth_nginx_and_native_t3_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _config()
            config_dir = os.path.join(temporary, "config")
            payload_dir = os.path.join(temporary, "payload")
            nginx_available = os.path.join(temporary, "nginx-available")
            nginx_enabled = os.path.join(temporary, "nginx-enabled")
            broker_source = os.path.join(temporary, "broker.py")
            with open(broker_source, "w", encoding="utf-8") as file_obj:
                file_obj.write("# initial implementation\n")
            os.makedirs(payload_dir)
            os.makedirs(config_dir)
            with open(os.path.join(payload_dir, "htpasswd"), "w", encoding="utf-8") as file_obj:
                file_obj.write("agent:$6$salt$hash\n")
            with open(
                os.path.join(config_dir, "providers.json"),
                "w",
                encoding="utf-8",
            ) as file_obj:
                json.dump(
                    {
                        "version": 1,
                        "providers": {
                            "t3code": {"https_public_port": 8444},
                        },
                    },
                    file_obj,
                )
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())

            constants = {
                "DEVICE_PAIRING_CONFIG_DIR": config_dir,
                "DEVICE_PAIRING_AUTH_FILE": os.path.join(config_dir, "htpasswd"),
                "DEVICE_PAIRING_PROVIDERS_FILE": os.path.join(
                    config_dir, "providers.json"
                ),
                "DEVICE_PAIRING_PAYLOAD_FILE": os.path.join(payload_dir, "htpasswd"),
                "DEVICE_PAIRING_SERVICE_FILE": os.path.join(temporary, "pairing.service"),
                "DEVICE_PAIRING_NGINX_SITE": nginx_available,
                "DEVICE_PAIRING_NGINX_LINK": nginx_enabled,
                "DEVICE_PAIRING_SOCKET": "/run/basaltwater-device-pairing/http.sock",
                "DEVICE_PAIRING_SCRIPT": "/opt/basaltwater/common/service_tools/device_pairing_service.py",
                "DEVICE_PAIRING_SOURCE": broker_source,
            }

            def run_command(_command: str, **_kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch.multiple("common.t3code_steps", **constants),
                patch("common.t3code_steps.pwd.getpwnam", return_value=account),
                patch("common.t3code_steps.os.chown"),
                patch("common.t3code_steps.run", side_effect=run_command) as execute,
                patch(
                    "common.t3code_steps.configure_nginx_auth_failure_ban"
                ) as configure_ban,
                patch("web.web_steps.install_nginx"),
            ):
                _configure_device_pairing(
                    config,
                    temporary,
                    "/home/agent/.local/bin/basaltwater-t3code-pairing-provider",
                    "0.0.0.0",
                    3773,
                )
                execute.reset_mock()
                _configure_device_pairing(
                    config, temporary,
                    "/home/agent/.local/bin/basaltwater-t3code-pairing-provider",
                    "0.0.0.0", 3773,
                )
                self.assertFalse(any(
                    "systemctl restart" in call.args[0] for call in execute.call_args_list
                ))
                with open(broker_source, "w", encoding="utf-8") as file_obj:
                    file_obj.write("# updated implementation\n")
                execute.reset_mock()
                _configure_device_pairing(
                    config, temporary,
                    "/home/agent/.local/bin/basaltwater-t3code-pairing-provider",
                    "0.0.0.0", 3773,
                )
                execute.assert_any_call("systemctl restart basaltwater-device-pairing.service")

            self.assertEqual(
                os.stat(os.path.join(temporary, ".t3")).st_mode & 0o777,
                0o700,
            )

            with open(nginx_available, encoding="utf-8") as file_obj:
                nginx = file_obj.read()
            self.assertIn('auth_basic "Device pairing"', nginx)
            self.assertIn("rate=5r/m", nginx)
            self.assertIn(
                "limit_req zone=basaltwater_device_pairing_auth burst=5 nodelay",
                nginx,
            )
            self.assertIn("basaltwater-auth-failure", nginx)
            self.assertIn("listen 0.0.0.0:3774", nginx)
            self.assertIn(
                "proxy_pass http://unix:"
                "/run/basaltwater-device-pairing/http.sock:/",
                nginx,
            )
            self.assertIn(
                "map $realip_remote_addr "
                "$basaltwater_device_pairing_gateway_request",
                nginx,
            )
            self.assertIn('"1:https" https;', nginx)
            self.assertIn(
                "~^1:(?<basaltwater_device_pairing_forwarded_host>.+)$",
                nginx,
            )
            self.assertIn("set_real_ip_from 127.0.0.1", nginx)
            self.assertIn("real_ip_header X-Forwarded-For", nginx)
            self.assertIn(
                "proxy_set_header X-Forwarded-Host "
                "$basaltwater_device_pairing_public_host",
                nginx,
            )
            self.assertIn(
                "proxy_set_header X-Forwarded-Proto "
                "$basaltwater_device_pairing_public_proto",
                nginx,
            )
            with open(
                os.path.join(config_dir, "providers.json"), encoding="utf-8"
            ) as file_obj:
                providers = json.load(file_obj)
            command = providers["providers"]["t3code"]["command"]
            self.assertTrue(any(item.endswith("/t3code_admin_pair.py") for item in command))
            self.assertIn("--t3-binary", command)
            self.assertIn("--server-url", command)
            self.assertNotIn("--port", command)
            self.assertIn("--json", command)
            self.assertEqual(
                providers["providers"]["t3code"]["https_public_port"],
                8444,
            )
            self.assertNotIn("connect", providers["providers"]["t3code"])
            self.assertNotIn("$6$salt$hash", json.dumps(providers))
            with open(
                os.path.join(temporary, "pairing.service"), encoding="utf-8"
            ) as file_obj:
                service = file_obj.read()
            self.assertIn("Environment=T3CODE_PORT=3773", service)
            self.assertIn("# Pairing broker revision ", service)
            self.assertIn("Wants=network-online.target", service)
            self.assertNotIn("basaltwater-t3code.service", service)
            self.assertNotIn("Requires=basaltwater-t3code.service", service)
            self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", service)
            configure_ban.assert_called_with(
                "device-pairing",
                "/var/log/nginx/basaltwater-device-pairing-auth-failures.log",
            )

    def test_https_pairing_uses_the_primary_t3_forward_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = os.path.join(temporary, "home")
            workspace = os.path.join(home, "repos")
            os.makedirs(workspace)
            admin_pair_script = os.path.join(temporary, "t3code_admin_pair.py")
            with open(admin_pair_script, "w", encoding="utf-8") as file_obj:
                file_obj.write("# test helper\n")
            account = SimpleNamespace(
                pw_dir=home,
                pw_uid=os.getuid(),
                pw_gid=os.getgid(),
            )
            https_endpoints = {
                "t3code-pairing": ("https://agent-vm:8445/", 8445),
                "t3code": ("https://agent-vm:8444/", 8444),
            }

            with (
                patch("common.t3code_steps.is_dry_run", return_value=False),
                patch("common.t3code_steps.pwd.getpwnam", return_value=account),
                patch("common.t3code_steps.os.chown"),
                patch("common.t3code_steps._configure_firewall"),
                patch("common.t3code_steps._install_t3_service"),
                patch("common.t3code_steps._ensure_t3_agent_skill"),
                patch("common.t3code_steps._configure_device_pairing"),
                patch("common.t3code_steps._remove_connect_restart_units") as remove_connect_bridge,
                patch(
                    "common.t3code_steps._configure_t3_https",
                    return_value=https_endpoints,
                ),
                patch(
                    "common.t3code_steps._set_pairing_t3_https_port"
                ) as set_https_port,
                patch(
                    "common.t3code_steps.T3_ADMIN_PAIR_SCRIPT",
                    admin_pair_script,
                ),
            ):
                install_t3code_web(
                    _config(
                        agent_workspace=workspace,
                        web_interface_host="0.0.0.0",
                    )
                )

            set_https_port.assert_called_once_with(8444)
            remove_connect_bridge.assert_called_once_with(os.path.join(home, ".t3"))

    def test_pairing_https_port_rejects_invalid_values(self) -> None:
        for value in (True, 443, 65536):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "between 1024 and 65535",
            ):
                _set_pairing_t3_https_port(value)

    def test_firewall_includes_pairing_port(self) -> None:
        config = _config()
        comments: list[str] = []

        def run_command(command: str, **_kwargs: object) -> SimpleNamespace:
            if command == "ufw status numbered":
                lines = [
                    f"[ {index}] {3772 + index}/tcp ALLOW IN 192.168.0.0/24 # {comment}"
                    for index, comment in enumerate(comments, 1)
                ]
                return SimpleNamespace(returncode=0, stdout="\n".join(lines))
            if command.startswith("ufw allow from"):
                marker = command.split(" comment ", 1)[1].strip("'")
                comments.append(marker)
            return SimpleNamespace(returncode=0, stdout="")

        with patch("common.t3code_steps.run", side_effect=run_command):
            _configure_firewall(config, 3773, "0.0.0.0")

        self.assertTrue(any("3773/tcp" in comment for comment in comments))
        self.assertTrue(any("pairing 3774/tcp" in comment for comment in comments))


if __name__ == "__main__":
    unittest.main()
