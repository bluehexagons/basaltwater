"""Tests for protected, provider-native device enrollment."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import remote_setup
from common.service_tools.device_pairing_service import (
    ConnectJob,
    PairingError,
    PairingRequestHandler,
    PairingState,
    _safe_pairing_url,
    _public_base_url,
    _connect_output_html,
    _sanitize_connect_output,
)
from common.t3code_steps import (
    _configure_device_pairing,
    _configure_firewall,
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
            os.makedirs(agent_payload)
            os.makedirs(pairing_payload)
            with (
                patch.object(
                    remote_setup, "REMOTE_AGENT_PAYLOAD_DIR", agent_payload
                ),
                patch.object(
                    remote_setup,
                    "REMOTE_DEVICE_PAIRING_PAYLOAD_DIR",
                    pairing_payload,
                ),
                patch.object(remote_setup, "is_dry_run", return_value=False),
            ):
                remote_setup._remove_secret_payloads()

            self.assertFalse(os.path.exists(agent_payload))
            self.assertFalse(os.path.exists(pairing_payload))


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

    def test_connect_output_links_authorization_url_without_raw_markup(self) -> None:
        rendered = _connect_output_html(
            "Authorize at https://connect.example/code?next=1&x=2."
        )
        self.assertIn(
            'href="https://connect.example/code?next=1&amp;x=2"',
            rendered,
        )
        self.assertNotIn("<script", rendered)

    def test_connect_output_ignores_malformed_urls(self) -> None:
        rendered = _connect_output_html("Output https://[malformed and continue")
        self.assertIn("https://[malformed and continue", rendered)

    def test_connect_output_sanitizer_hides_partial_terminal_sequences(self) -> None:
        self.assertEqual(_sanitize_connect_output("\x1b[2"), "")
        self.assertEqual(_sanitize_connect_output("\x1b]progress"), "")

    def test_connect_status_refresh_route_is_not_a_404(self) -> None:
        provider = self._provider()
        provider["connect"] = {
            "link_command": ["/opt/t3", "connect", "link"],
            "status_command": ["/opt/t3", "connect", "status"],
            "unlink_command": ["/opt/t3", "connect", "unlink"],
            "restart_request": "/tmp/t3-connect-restart",
        }
        state = PairingState({"t3code": provider})
        nonce = state.new_nonce()
        headers = Message()
        headers["X-Forwarded-Proto"] = "https"
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/connect/t3code"
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET /connect/t3code HTTP/1.1"
        handler.headers = headers
        handler.wfile = BytesIO()

        with patch.object(
            state,
            "connect_snapshot",
            return_value={"active": True, "output": "Waiting for authorization"},
        ):
            handler.do_GET()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn(" 200 ", response)
        self.assertNotIn(" 404 ", response)
        self.assertIn("Response or authorization code", response)
        self.assertIn("Refresh status", response)
        self.assertIn("Authorization in progress", response)
        self.assertIn("relay-install confirmation is accepted automatically", response)
        self.assertIn("Show T3 command output", response)
        self.assertNotIn('http-equiv="refresh"', response)
        self.assertIn(f'name="nonce" value="{nonce}"', response)
        self.assertIn("/connect/t3code", response)

    def test_connect_page_explains_completed_authorization(self) -> None:
        provider = self._provider()
        provider["connect"] = {
            "link_command": ["/opt/t3", "connect", "link"],
            "status_command": ["/opt/t3", "connect", "status"],
            "unlink_command": ["/opt/t3", "connect", "unlink"],
            "restart_request": "/tmp/t3-connect-restart",
        }
        state = PairingState({"t3code": provider})
        nonce = state.new_nonce()
        headers = Message()
        headers["X-Forwarded-Proto"] = "https"
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/connect/t3code"
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET /connect/t3code HTTP/1.1"
        handler.headers = headers
        handler.wfile = BytesIO()

        with patch.object(
            state,
            "connect_snapshot",
            return_value={
                "active": False,
                "returncode": 0,
                "output": "Connect authorization completed",
                "status": {
                    "desired": True,
                    "authenticated": True,
                    "linked": True,
                    "provisioned": True,
                    "relayUrl": "https://relay.example/agent",
                },
            },
        ):
            handler.do_GET()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn("Authorization completed", response)
        self.assertIn("The managed service is applying the new relay state", response)
        self.assertIn("Startup", response)
        self.assertIn("Enabled", response)
        self.assertIn("Relay address", response)
        self.assertIn("Show T3 command output", response)
        self.assertIn("Refresh status", response)
        self.assertNotIn("Authorization did not complete", response)

    def test_pairing_page_uses_clear_device_actions(self) -> None:
        provider = self._provider()
        provider["connect"] = {
            "link_command": ["/opt/t3", "connect", "link"],
            "status_command": ["/opt/t3", "connect", "status"],
            "unlink_command": ["/opt/t3", "connect", "unlink"],
            "restart_request": "/tmp/t3-connect-restart",
        }
        state = PairingState({"t3code": provider})
        nonce = state.new_nonce()
        headers = Message()
        headers["X-Forwarded-Proto"] = "https"
        headers["Cookie"] = f"basaltwater_pairing_nonce={nonce}"
        handler = PairingRequestHandler.__new__(PairingRequestHandler)
        handler.server = SimpleNamespace(pairing_state=state)
        handler.path = "/"
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET / HTTP/1.1"
        handler.headers = headers
        handler.wfile = BytesIO()

        with patch.object(
            state,
            "connect_snapshot",
            return_value={"active": False, "status": {"desired": False}},
        ):
            handler.do_GET()

        response = handler.wfile.getvalue().decode("iso-8859-1")
        self.assertIn("Pair a device", response)
        self.assertIn("Pair this browser", response)
        self.assertIn("Pair another T3 Code client", response)
        self.assertIn("Startup behavior", response)
        self.assertIn("Start authorization", response)
        self.assertIn("Never share a pairing link", response)
        self.assertNotIn("Create a link for this browser", response)

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

    def test_headless_connect_job_accepts_prompt_and_requests_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            restart_request = os.path.join(temporary, "restart")
            command = [
                sys.executable,
                "-c",
                (
                    "import sys; print('Authorize at https://connect.example/code', flush=True); "
                    "value = sys.stdin.readline().strip(); print('received ' + value, flush=True)"
                ),
            ]
            job = ConnectJob(
                {
                    "link_command": command,
                    "status_command": command,
                    "unlink_command": command,
                    "restart_request": restart_request,
                }
            )
            job.start()
            deadline = time.monotonic() + 2
            while "Authorize" not in str(job.snapshot()["output"]):
                if time.monotonic() > deadline:
                    self.fail("Connect prompt was not returned")
                time.sleep(0.01)
            job.send_input("approved")
            deadline = time.monotonic() + 2
            while job.snapshot()["active"]:
                if time.monotonic() > deadline:
                    self.fail("Connect job did not finish")
                time.sleep(0.01)
            snapshot = job.snapshot()
            self.assertEqual(snapshot["returncode"], 0)
            self.assertIn("received approved", str(snapshot["output"]))
            self.assertTrue(os.path.isfile(restart_request))

    def test_headless_connect_job_accepts_relay_install_and_cleans_terminal_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            restart_request = os.path.join(temporary, "restart")
            command = [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stdout.write('\\x1b[2K\\rThe T3 relay client is required for T3 Connect. "
                    "Download and install version 1.2.3? (y/N)\\n'); sys.stdout.flush(); "
                    "value = sys.stdin.readline().strip(); "
                    "print('received ' + value, flush=True)"
                ),
            ]
            job = ConnectJob(
                {
                    "link_command": command,
                    "status_command": command,
                    "unlink_command": command,
                    "restart_request": restart_request,
                }
            )
            job.start()
            deadline = time.monotonic() + 2
            while job.snapshot()["active"]:
                if time.monotonic() > deadline:
                    self.fail("Connect job did not accept the relay-install prompt")
                time.sleep(0.01)
            snapshot = job.snapshot()
            output = str(snapshot["output"])
            self.assertEqual(snapshot["returncode"], 0)
            self.assertTrue(output.startswith("The T3 relay client is required"))
            self.assertIn("received y", output)
            self.assertNotIn("\x1b", output)
            self.assertNotIn("\r", output)
            self.assertTrue(os.path.isfile(restart_request))

    def test_apply_preserves_an_already_enabled_connect_state(self) -> None:
        provider = self._provider()
        provider["connect"] = {
            "link_command": ["/opt/t3", "connect", "link"],
            "status_command": ["/opt/t3", "connect", "status"],
            "unlink_command": ["/opt/t3", "connect", "unlink"],
            "restart_request": "/tmp/t3-connect-restart",
        }
        state = PairingState({"t3code": provider})
        job = state._connect_jobs["t3code"]
        with (
            patch.object(
                state,
                "connect_snapshot",
                return_value={"status": {"desired": True}},
            ),
            patch.object(job, "start") as start,
        ):
            state.set_connect_enabled("t3code", True)
        start.assert_not_called()

        with (
            patch.object(
                state,
                "connect_snapshot",
                return_value={"status": {"desired": False}},
            ),
            patch.object(state, "unlink_connect") as unlink,
        ):
            state.set_connect_enabled("t3code", False)
        unlink.assert_called_once_with("t3code")

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
            }

            def run_command(_command: str, **_kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch.multiple("common.t3code_steps", **constants),
                patch("common.t3code_steps.pwd.getpwnam", return_value=account),
                patch("common.t3code_steps.os.chown"),
                patch("common.t3code_steps.run", side_effect=run_command),
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
            self.assertNotIn("$6$salt$hash", json.dumps(providers))
            with open(
                os.path.join(temporary, "pairing.service"), encoding="utf-8"
            ) as file_obj:
                service = file_obj.read()
            self.assertIn("Environment=T3CODE_PORT=3773", service)
            self.assertIn("Wants=network-online.target", service)
            self.assertNotIn("basaltwater-t3code.service", service)
            self.assertNotIn("Requires=basaltwater-t3code.service", service)
            self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", service)
            configure_ban.assert_called_once_with(
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
                patch("common.t3code_steps._configure_connect_restart_units"),
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
