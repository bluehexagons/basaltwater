"""The privileged registration boundary accepts one bounded volatile input."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
import io
import os
from pathlib import Path
import socket
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import github_runner_registration as helper
from common import web_panel_steps as panel_setup
from common.web_panel_steps import render_web_panel_nginx
from lib import github_runner_gate as gate
from lib import github_runner_registration as protocol
from tests.test_github_runner import config, policy
from web import github_runner_registration_steps as units
from web import github_runner_steps as runner


class HandoffTests(unittest.TestCase):
    def exchange(self, payload: bytes, *, peer: int = 1000, error: Exception | None = None) -> tuple[bytes, Mock]:
        server, client = socket.socketpair()
        with server, client, patch.object(helper, "peer_uid", return_value=peer), patch.object(helper, "register_pending_runner", side_effect=error) as register:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            helper.handle_connection(server, 1000)
            server.shutdown(socket.SHUT_WR)
            result = client.recv(128)
        return result, register

    def test_only_one_token_is_accepted_and_output_is_fixed(self) -> None:
        token = b"A" * 40
        result, register = self.exchange(token)
        self.assertEqual(result, b"registered\n")
        register.assert_called_once_with(token)
        for payload in (b"", b"short", b"A" * 4097, token + b"\n", b'{"token":"' + token + b'","repository":"other/repo"}'):
            with self.subTest(size=len(payload)):
                result, register = self.exchange(payload)
                self.assertEqual(result, b"failed\n")
                register.assert_not_called()

    def test_kernel_peer_identity_and_generic_error_responses(self) -> None:
        for peer in (0, 1234):
            result, register = self.exchange(b"A" * 40, peer=peer)
            self.assertEqual(result, b"failed\n")
            register.assert_not_called()
        for error in (ValueError("sensitive token"), RuntimeError("sensitive token"), OSError("sensitive token")):
            result, _ = self.exchange(b"A" * 40, error=error)
            self.assertEqual(result, b"failed\n")

    def test_failure_categories_survive_handoff_without_exception_text(self) -> None:
        for code in ("start", "configure", "timeout", "credentials", "activate", "installation"):
            failure = protocol.RunnerRegistrationError(code)
            # Even replaced exception text must never cross the protocol.
            failure.args = ("sensitive token",)
            result, _ = self.exchange(b"A" * 40, error=failure)
            self.assertEqual(result, f"failed {code}\n".encode())
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            connection.recv.side_effect = [result[:4], result[4:], b""]
            with (patch.object(protocol.socket, "socket", return_value=connection),
                  patch.object(protocol, "peer_uid", return_value=0),
                  self.assertRaises(protocol.RunnerRegistrationError) as caught):
                protocol.submit_registration(b"A" * 40)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("sensitive", str(caught.exception))

    def test_unknown_or_extended_failure_responses_remain_generic(self) -> None:
        for response in (b"failed sensitive-token\n", b"failed start\nextra", b"failed " + b"A" * 100):
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            connection.recv.side_effect = [response[:33], b""]
            with (patch.object(protocol.socket, "socket", return_value=connection),
                  patch.object(protocol, "peer_uid", return_value=0),
                  self.assertRaises(protocol.RunnerRegistrationError) as caught):
                protocol.submit_registration(b"A" * 40)
            self.assertEqual(caught.exception.code, "failed")
            self.assertNotIn("sensitive", str(caught.exception))

    def test_peer_credentials_come_from_kernel_and_client_checks_root_server(self) -> None:
        first, second = socket.socketpair()
        with first, second:
            self.assertEqual(protocol.peer_uid(first), os.getuid())
        for peer, response, succeeds in ((0, [b"reg", b"istered\n", b""], True), (0, [b"failed\n", b""], False),
                                         (0, [b"registered\nextra", b""], False), (1000, [], False)):
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            connection.recv.side_effect = response
            with patch.object(protocol.socket, "socket", return_value=connection), patch.object(protocol, "peer_uid", return_value=peer):
                if succeeds:
                    protocol.submit_registration(b"A" * 40)
                    connection.sendall.assert_called_once_with(b"A" * 40)
                    connection.shutdown.assert_called_once_with(socket.SHUT_WR)
                else:
                    with self.assertRaises(RuntimeError):
                        protocol.submit_registration(b"A" * 40)
                    if peer != 0:
                        connection.sendall.assert_not_called()

    def test_incomplete_request_timeout_never_invokes_registration(self) -> None:
        connection = Mock()
        connection.recv.side_effect = TimeoutError("private details")
        with patch.object(helper, "peer_uid", return_value=1000), patch.object(helper, "register_pending_runner") as register:
            helper.handle_connection(connection, 1000)
        register.assert_not_called()
        connection.sendall.assert_called_once_with(b"failed\n")

    def test_setup_output_never_reaches_the_protocol_or_logs(self) -> None:
        server, client = socket.socketpair()
        with (server, client, patch.object(helper, "peer_uid", return_value=1000),
              patch.object(helper, "register_pending_runner", side_effect=lambda token: print(token.decode())),
              patch("sys.stdout", new_callable=io.StringIO) as output):
            client.sendall(b"A" * 40)
            client.shutdown(socket.SHUT_WR)
            helper.handle_connection(server, 1000)
            self.assertEqual(client.recv(128), b"registered\n")
            self.assertEqual(output.getvalue(), "")


class PrivilegedOperationTests(unittest.TestCase):
    def test_fixed_operation_uses_installed_policy_under_lock(self) -> None:
        pending = policy() | {"registration": "pending"}
        account = SimpleNamespace(pw_uid=123, pw_gid=123)
        with (patch.object(runner, "runner_lock", side_effect=nullcontext) as lock,
              patch.object(gate, "check_installation", return_value=pending) as check,
              patch.object(runner, "_account", return_value=account), patch.object(runner, "_register_pending") as register):
            runner.register_pending_runner(b"A" * 40)
        lock.assert_called_once()
        check.assert_called_once_with(require_registration=False)
        register.assert_called_once_with(pending, b"A" * 40, account)

    def test_registered_policy_and_partial_credentials_cannot_be_replaced(self) -> None:
        with patch.object(runner, "_register") as register:
            with self.assertRaisesRegex(ValueError, "already registered"):
                runner._register_pending(policy(), b"A" * 40, Mock())
            with patch.object(runner.os.path, "lexists", return_value=True):
                with self.assertRaisesRegex(ValueError, "Partial"):
                    runner._register_pending(policy() | {"registration": "pending"}, b"A" * 40, Mock())
        register.assert_not_called()

    def test_pending_state_is_denied_at_boot_and_partial_registration_needs_recovery(self) -> None:
        with patch.object(gate, "read_json_file", return_value=policy() | {"registration": "pending"}):
            with self.assertRaisesRegex(ValueError, "pending"):
                gate.check_installation()
            with patch.object(gate.os.path, "lexists", return_value=True):
                with self.assertRaisesRegex(ValueError, "recovery"):
                    gate.check_installation(require_registration=False)

    def test_lock_rejects_concurrent_operations_and_unsafe_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (patch.object(runner, "HOOK_DIR", temporary), patch.object(runner, "_directory"),
                  patch.object(runner.os, "fstat", return_value=SimpleNamespace(st_uid=0, st_nlink=1, st_mode=stat.S_IFREG | 0o600))):
                with runner.runner_lock():
                    with self.assertRaisesRegex(RuntimeError, "already running"), runner.runner_lock():
                        pass
                with runner.runner_lock():
                    pass
                for metadata in (dict(st_uid=1000, st_nlink=1, st_mode=stat.S_IFREG | 0o600),
                                 dict(st_uid=0, st_nlink=2, st_mode=stat.S_IFREG | 0o600),
                                 dict(st_uid=0, st_nlink=1, st_mode=stat.S_IFREG | 0o660)):
                    with patch.object(runner.os, "fstat", return_value=SimpleNamespace(**metadata)):
                        with self.assertRaisesRegex(ValueError, "Unsafe"), runner.runner_lock():
                            pass
            lock = Path(temporary, "registration.lock")
            self.assertTrue(lock.is_file())
            lock.unlink()
            lock.symlink_to(Path(temporary, "target"))
            with patch.object(runner, "HOOK_DIR", temporary), patch.object(runner, "_directory"):
                with self.assertRaises(ValueError), runner.runner_lock():
                    pass


class RegistrationUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(units, "runner_lock", side_effect=nullcontext))

    def test_socket_changes_leave_an_active_registration_alone(self) -> None:
        with (patch.object(units, "runner_lock", side_effect=RuntimeError("registration is running")),
              patch.object(units, "snapshot_unit_file", return_value=None),
              patch.object(units, "replace_units") as replace):
            with self.assertRaisesRegex(RuntimeError, "registration is running"):
                units.configure_registration_service(SimpleNamespace(pw_uid=1000, pw_gid=1000))
        replace.assert_not_called()
        with (patch.object(units, "runner_lock", side_effect=RuntimeError("registration is running")),
              patch.object(units, "preflight_registration_units"),
              patch.object(units, "snapshot_unit_file", return_value={"content": "managed"}),
              patch.object(units, "remove_units") as remove):
            with self.assertRaisesRegex(RuntimeError, "registration is running"):
                units.remove_registration_service()
        remove.assert_not_called()

    def test_panel_setup_installs_handoff_only_for_https_runner_profile(self) -> None:
        account = SimpleNamespace(pw_name="operator", pw_uid=1000, pw_gid=1000, pw_dir="/home/operator")
        for target, tls, expected in (("server_github_runner", True, True), ("server_github_runner", False, False),
                                      ("server_lite", True, False)):
            with self.subTest(profile=target, tls=tls):
                with ExitStack() as stack:
                    for attribute in ("_ensure_nginx", "_ensure_event_storage", "_validate_ingest_token_path", "_install_ingest_token",
                                      "write_json_atomic", "_configure_audit_exporter", "_configure_service", "configure_nginx_auth_failure_ban"):
                        stack.enter_context(patch.object(panel_setup, attribute))
                    for target_path in ("desktop.guacamole_steps.preflight_units", "desktop.guacamole_steps.preflight_configuration",
                                        "desktop.guacamole_steps.remove_guacamole", "common.web_panel_steps.os.makedirs"):
                        stack.enter_context(patch(target_path))
                    stack.enter_context(patch.object(panel_setup, "is_dry_run", return_value=False))
                    stack.enter_context(patch("common.godot_web_steps.discover_local_web_identities", return_value=[]))
                    stack.enter_context(patch("common.godot_web_steps.configure_internal_web_host", return_value=("https://host", "/ca", "/cert", "/key", False)))
                    stack.enter_context(patch.object(panel_setup, "_install_auth_file", return_value=(False, None)))
                    stack.enter_context(patch.object(panel_setup, "_ensure_web_panel_service_account", return_value=account))
                    nginx = stack.enter_context(patch.object(panel_setup, "_write_nginx_site"))
                    configure = stack.enter_context(patch.object(units, "configure_registration_service"))
                    remove = stack.enter_context(patch.object(units, "remove_registration_service"))
                    values = dict(web_panel_port=9443 if tls else 8080, enable_ssl=tls, ssl_email="operator@example.test")
                    if target != "server_github_runner":
                        values.update(system_type=target, ci_repository="", ci_allowed_users=None, ci_labels=None)
                    panel_setup.configure_web_panel(config(**values))
                    self.assertEqual(configure.called, expected)
                    self.assertEqual(remove.called, not expected)
                    self.assertEqual(protocol.ENDPOINT in nginx.call_args.args[0], expected)

    def test_units_limit_privilege_peer_and_lifetime_without_persistent_requests(self) -> None:
        with patch.object(units, "snapshot_unit_file", return_value=None), patch.object(units, "replace_units") as replace:
            units.configure_registration_service(SimpleNamespace(pw_uid=1000, pw_gid=1000))
        desired = replace.call_args.args[0]
        socket_unit, service = desired[units.SOCKET_UNIT], desired[units.SERVICE_UNIT]
        for text in ("SocketUser=root", "SocketGroup=1000", "SocketMode=0660", "Accept=yes", "MaxConnections=1", protocol.SOCKET):
            self.assertIn(text, socket_unit)
        for text in ("--panel-uid 1000", "StandardInput=socket", "StandardOutput=null", "StandardError=null", "LimitCORE=0", "RuntimeMaxSec=240", "PartOf=" + units.SOCKET_UNIT):
            self.assertIn(text, service)
        self.assertEqual(replace.call_args.kwargs["activate"], (units.SOCKET_UNIT,))
        self.assertNotIn("EnvironmentFile", service)
        self.assertNotIn("sudo", service)
        self.assertNotIn("broker", service)

    def test_unmanaged_units_and_root_panel_are_rejected_before_changes(self) -> None:
        with patch.object(units, "snapshot_unit_file", return_value={"content": "unmanaged"}), patch.object(units, "replace_units") as replace:
            with self.assertRaisesRegex(ValueError, "unmanaged"):
                units.configure_registration_service(SimpleNamespace(pw_uid=1000, pw_gid=1000))
            with self.assertRaises(ValueError):
                units.configure_registration_service(SimpleNamespace(pw_uid=0, pw_gid=0))
        replace.assert_not_called()

    def test_removal_stops_socket_before_template_and_is_noop_when_absent(self) -> None:
        with patch.object(units, "preflight_registration_units"), patch.object(units, "snapshot_unit_file", return_value={"content": "managed"}), patch.object(units, "remove_units") as remove:
            units.remove_registration_service()
            remove.assert_called_once_with((units.SOCKET_UNIT, units.SERVICE_UNIT))
            remove.reset_mock()
            with patch.object(units, "snapshot_unit_file", return_value=None):
                units.remove_registration_service()
            remove.assert_not_called()

    def test_nginx_requires_auth_and_https_and_keeps_token_body_out_of_files(self) -> None:
        rendered = render_web_panel_nginx(["host"], 443, cert_path="/cert.pem", key_path="/key.pem", github_runner_registration=True)
        registration = rendered.split("location = " + protocol.ENDPOINT)[1].split("\n    }")[0]
        self.assertIn("auth_basic_user_file", rendered)
        self.assertNotIn("auth_basic off", registration)
        for text in ("limit_except POST", "client_max_body_size 8192", "client_body_buffer_size 8192", "client_body_in_file_only off", "proxy_request_buffering off", "X-Basaltwater-User $remote_user", 'Authorization ""'):
            self.assertIn(text, registration)
        self.assertIn("log_format basaltwater_web_panel_auth '$remote_addr [$time_local] basaltwater-auth-failure'", rendered)
        self.assertNotIn("$request", rendered)
        self.assertNotIn(protocol.ENDPOINT, render_web_panel_nginx(["host"], 80, github_runner_registration=True))
