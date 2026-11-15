"""Offline native schema, safety, recovery and private transport contracts."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import headscale_admin as admin
from lib.headscale_admin_cli import add_headscale_admin_parser, run_headscale_admin_command
from lib.headscale_server import ServerPaths, ServerManifest
from common.service_tools.headscale_admin import execute_request


class ValidationTests(unittest.TestCase):
    def test_unknown_and_injection_inputs_fail_before_native(self):
        for action, value in [("shell", {}), ("nodes_list", {"socket": "/tmp/socket"}),
                              ("users_create", {"name": "alice;id"}),
                              ("node_delete", {"id": "1 --force"}),
                              ("node_expire", {"id": 1}),
                              ("register", {"user": "alice", "auth_id": "https://secret"}),
                              ("register", {"user": "alice", "auth_id": "x" * 38})]:
            with self.subTest(action=action, value=value), patch.object(admin.NativeAdmin, "verify") as verify:
                with self.assertRaises(admin.AdminError):
                    admin.execute_admin_action(action, value)
                verify.assert_not_called()

    def test_transport_rejects_duplicate_fields_and_never_echoes_secret(self):
        secret = "hskey-authreq-" + "a" * 24
        for payload in [b'{"action":"nodes_list","action":"users_list","parameters":{}}',
                        json.dumps({"action": "register", "parameters": {"user": "alice", "auth_id": secret}}).encode(),
                        b"x" * 8193]:
            with patch("os.geteuid", return_value=0), patch.object(admin.NativeAdmin, "verify", side_effect=RuntimeError(secret)):
                result = execute_request(payload)
            self.assertFalse(result["ok"])
            self.assertNotIn(secret, json.dumps(result))

    def test_offline_parser_dry_run_has_no_native_effects_or_secret_echo(self):
        parser = argparse.ArgumentParser()
        add_headscale_admin_parser(parser.add_subparsers())
        secret = "hskey-authreq-" + "a" * 24
        args = parser.parse_args(["headscale-admin", "approve", "--user", "alice", "--auth-id-stdin", "--dry-run"])
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(secret)), patch("sys.stdout", output), patch.object(admin.NativeAdmin, "verify") as verify:
            self.assertEqual(run_headscale_admin_command(args), 0)
        verify.assert_not_called()
        self.assertNotIn(secret, output.getvalue())


class NativeTests(unittest.TestCase):
    def setUp(self):
        try:
            from lib.headscale_admin_schema import messages
            self.types = messages()
        except ImportError:
            self.skipTest("Distro python3-protobuf required for native schema tests")
        self.adapter = admin.NativeAdmin()
        self.adapter.verify = Mock()
        self.adapter.rpc = Mock()

    def node(self, **kwargs):
        fields = dict(id=7, name="laptop", ip_addresses=["100.64.0.7"], user={"id": 2, "name": "alice"})
        fields.update(kwargs)
        return self.types["Node"](**fields)

    def test_native_wire_numbers_and_allowlist(self):
        node = self.node()
        # Unknown native machine/public/preauth key fields cannot reach output.
        node.ParseFromString(node.SerializeToString() + b'\x12\x06SECRET')
        result = admin.node_projection(node)
        self.assertEqual(result["node_id"], "7")
        self.assertEqual(result["user_id"], "2")
        self.assertNotIn("SECRET", json.dumps(result))
        tagged = admin.node_projection(self.node(tags=["tag:agent"]))
        self.assertIsNone(tagged["user_id"])
        self.assertEqual(self.types["AuthRegisterRequest"](user="alice", auth_id="exact").SerializeToString(),
                         b'\x0a\x05alice\x12\x05exact')

    def test_delete_user_with_nodes_is_refused(self):
        self.adapter.rpc.return_value = self.types["ListNodesResponse"](nodes=[self.node()])
        with self.assertRaisesRegex(admin.AdminError, "owned nodes"):
            self.adapter.execute("users_delete", {"id": "2"})
        self.assertEqual([call.args[0] for call in self.adapter.rpc.call_args_list], ["ListNodes"])

    def test_delete_user_requires_verified_absence(self):
        self.adapter.nodes = Mock(return_value=[])
        self.adapter.users = Mock(return_value=[{"id": "2", "name": "alice"}])
        with self.assertRaisesRegex(admin.AdminError, "not verified"):
            self.adapter.execute("users_delete", {"id": "2"})
        self.adapter.users.side_effect = [[{"id": "2", "name": "alice"}], []]
        self.assertEqual(self.adapter.execute("users_delete", {"id": "2"}), {"deleted": "2"})

    def test_exact_auth_register_and_expired_replayed_failures_are_safe(self):
        secret = "hskey-authreq-" + "a" * 24
        self.adapter.users = Mock(return_value=[{"id": "2", "name": "alice"}])
        self.adapter.nodes = Mock(return_value=[admin.node_projection(self.node())])
        self.adapter.rpc.return_value = self.types["AuthRegisterResponse"](node=self.node())
        result = self.adapter.execute("register", {"user": "alice", "auth_id": secret})
        self.assertEqual(result["node"]["id"], "7")
        self.adapter.rpc.assert_called_once_with("AuthRegister", user="alice", auth_id=secret)
        for reason in ("expired", "consumed"):
            with patch.object(admin, "NativeAdmin", return_value=self.adapter), patch.object(admin.os, "geteuid", return_value=0), patch.object(admin, "resource_lock", return_value=nullcontext()):
                self.adapter.rpc.side_effect = RuntimeError(reason + secret)
                with self.assertRaises(admin.AdminError) as error:
                    admin.execute_admin_action("register", {"user": "alice", "auth_id": secret})
                self.assertNotIn(secret, str(error.exception))

    def test_dns_guard_native_and_management_binding(self):
        self.adapter.endpoint = "https://vpn.example.com"
        node = admin.node_projection(self.node())
        for binding, addresses in [(None, ["100.64.0.7"]),
                                   (SimpleNamespace(network_url=self.adapter.endpoint, node_id="7", advertised=True), []),
                                   (SimpleNamespace(network_url="https://other.example.com", node_id="7", advertised=False), [])]:
            content = json.dumps({"dns": {"nameservers": {"global": addresses, "split": {}}}}).encode()
            with patch.object(admin, "dns_binding_lease", return_value=nullcontext(SimpleNamespace(binding=binding))), patch.object(admin, "regular_bytes", return_value=content):
                with self.assertRaises(admin.AdminError), self.adapter.dns_guard(node):
                    self.fail("Guard permitted removal")
        content = b'{"dns":{"nameservers":{"global":[],"split":{}}}}'
        with patch.object(admin, "dns_binding_lease", return_value=nullcontext(SimpleNamespace(binding=None))), patch.object(admin, "regular_bytes", return_value=content):
            with self.adapter.dns_guard(node):
                pass

    def test_wrong_endpoint_version_socket_log_policy_fails_before_native(self):
        manifest = ServerManifest(1, "0.29.7", "https://vpn.example.com", "mesh.example.net")
        base = {"server_url": manifest.endpoint, "unix_socket": str(self.adapter.paths.socket),
                "unix_socket_permission": "0600", "dns": {"base_domain": manifest.base_domain},
                "log": {"level": "warn"}}
        for change in ({"server_url": "https://other.example.com"}, {"unix_socket": "/tmp/evil"}, {"log": {"level": "debug"}}):
            with patch.object(admin.os, "geteuid", return_value=0), patch.object(admin, "load_manifest", return_value=manifest), patch.object(admin, "regular_bytes", return_value=json.dumps(base | change).encode()), patch.object(admin, "native") as native:
                with self.assertRaises(admin.AdminError):
                    admin.NativeAdmin().verify()
                native.assert_not_called()

    def test_rpc_uses_fixed_local_method_bounds_and_hides_raw_error(self):
        channel = Mock()
        channel.unary_unary.return_value.side_effect = RuntimeError("SECRET")
        grpc = SimpleNamespace(insecure_channel=Mock(return_value=nullcontext(channel)))
        with patch.dict("sys.modules", {"grpc": grpc}):
            adapter = admin.NativeAdmin()
            with self.assertRaises(admin.AdminError) as error:
                adapter.rpc("AuthRegister", user="alice", auth_id="SECRET")
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(grpc.insecure_channel.call_args.args[0], "unix:/run/headscale/headscale.sock")
        self.assertEqual(channel.unary_unary.return_value.call_args.kwargs["timeout"], 10)

    def test_service_binding_checks_running_binary_and_environment(self):
        manifest = ServerManifest(1, "0.29.7", "https://vpn.example.com", "mesh.example.net")
        config = {"server_url": manifest.endpoint, "unix_socket": str(self.adapter.paths.socket),
                  "unix_socket_permission": "0600", "dns": {"base_domain": manifest.base_domain},
                  "log": {"level": "warn"}}
        service = ("ActiveState=active\nUser=headscale\n"
                   "ExecStart={ path=/usr/bin/headscale ; argv[]=/usr/bin/headscale serve ; }\n"
                   "Environment=\nEnvironmentFiles=\nMainPID=123\n")
        binary = SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0, st_dev=1, st_ino=2)
        for response, running, accepted in [(service, binary, True),
                (service.replace("Environment=", "Environment=HEADSCALE_CONFIG=/tmp/evil"), binary, False),
                (service, SimpleNamespace(st_dev=1, st_ino=3), False)]:
            with patch.object(admin.os, "geteuid", return_value=0), patch.object(admin, "load_manifest", return_value=manifest), patch.object(admin, "regular_bytes", return_value=json.dumps(config).encode()), patch.object(admin, "check_directory_path"), patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=99)), patch.object(admin.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=99)), patch.object(admin.os, "stat", side_effect=[binary, running]), patch.object(admin, "native", side_effect=["headscale version v0.29.7", response]):
                adapter = admin.NativeAdmin()
                if accepted:
                    adapter.verify()
                    self.assertEqual(adapter.endpoint, manifest.endpoint)
                else:
                    with self.assertRaises(admin.AdminError):
                        adapter.verify()

    def test_key_issuance_private_one_use_hour_guard_and_secret_absence(self):
        with tempfile.TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            self.adapter.paths = ServerPaths(private=private)
            self.adapter.endpoint = "https://vpn.example.com"
            secret = "hskey-auth-" + "s" * 64
            def rpc(method, **fields):
                if method == "ListPreAuthKeys":
                    return self.types["ListPreAuthKeysResponse"]()
                self.assertEqual(method, "CreatePreAuthKey")
                self.assertEqual(set(fields), {"acl_tags", "expiration"})
                return self.types["CreatePreAuthKeyResponse"](pre_auth_key={
                    "id": 9, "key": secret, "acl_tags": fields["acl_tags"],
                    "expiration": fields["expiration"]})
            self.adapter.rpc.side_effect = rpc
            original = os.fstat
            def root_stat(fd):
                value = original(fd)
                return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
            with patch.object(admin, "NativeAdmin", return_value=self.adapter), patch.object(admin, "resource_lock", return_value=nullcontext()), patch.object(admin.os, "fstat", side_effect=root_stat):
                result = admin.issue_machine_key(["tag:agent"])
                with self.assertRaisesRegex(admin.AdminError, "Reconcile"):
                    admin.issue_machine_key(["tag:agent"])
            self.assertFalse(result["reusable"])
            self.assertFalse(result["ephemeral"])
            intent = json.loads((private / "key-intent.json").read_text())
            self.assertNotIn(secret, json.dumps(intent) + json.dumps(result))
            key_file = private / "keys" / intent["delivery"]
            self.assertEqual(key_file.read_text(), secret)
            self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key_file.parent.stat().st_mode), 0o700)
            self.assertAlmostEqual(intent["expiry"] - __import__('time').time(), 3600, delta=3)

    def test_no_tag_or_caller_owned_parameters_can_issue_key(self):
        for tags in ([], ["tag:agent;id"], ["tag:agent", "tag:agent"], "tag:agent"):
            with patch.object(admin, "NativeAdmin") as native:
                with self.assertRaises(admin.AdminError):
                    admin.issue_machine_key(tags)
                native.assert_not_called()

    def test_issuance_lost_ack_retains_intent_and_blocks_retry(self):
        with tempfile.TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            self.adapter.paths = ServerPaths(private=private)
            self.adapter.rpc.side_effect = [self.types["ListPreAuthKeysResponse"](), admin.AdminError("unconfirmed")]
            original = os.fstat
            def root_stat(fd):
                value = original(fd)
                return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
            with patch.object(admin, "NativeAdmin", return_value=self.adapter), patch.object(admin, "resource_lock", return_value=nullcontext()), patch.object(admin.os, "fstat", side_effect=root_stat):
                with self.assertRaises(admin.AdminError):
                    admin.issue_machine_key(["tag:agent"])
                self.assertTrue((private / "key-intent.json").exists())
                with self.assertRaisesRegex(admin.AdminError, "Reconcile"):
                    admin.issue_machine_key(["tag:agent"])
                self.assertEqual(self.adapter.rpc.call_count, 2)

    def test_wrong_key_attributes_keep_guard_without_delivery(self):
        for override in ({"reusable": True}, {"ephemeral": True}, {"used": True},
                         {"acl_tags": ["tag:other"]}, {"user": {"id": 2}}):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as root:
                private = Path(root) / "private"
                private.mkdir(mode=0o700)
                self.adapter.paths = ServerPaths(private=private)
                def rpc(method, **fields):
                    if method == "ListPreAuthKeys":
                        return self.types["ListPreAuthKeysResponse"]()
                    values = {"id": 9, "key": "hskey-auth-" + "s" * 64,
                              "acl_tags": fields["acl_tags"], "expiration": fields["expiration"]}
                    return self.types["CreatePreAuthKeyResponse"](pre_auth_key=values | override)
                self.adapter.rpc.side_effect = rpc
                original = os.fstat
                def root_stat(fd):
                    value = original(fd)
                    return SimpleNamespace(st_uid=0, st_mode=value.st_mode)
                with patch.object(admin, "NativeAdmin", return_value=self.adapter), patch.object(admin, "resource_lock", return_value=nullcontext()), patch.object(admin.os, "fstat", side_effect=root_stat):
                    with self.assertRaisesRegex(admin.AdminError, "attributes"):
                        admin.issue_machine_key(["tag:agent"])
                self.assertTrue((private / "key-intent.json").exists())
                self.assertFalse((private / "keys").exists())

    def test_key_expire_verifies_and_inspection_never_returns_key(self):
        key = self.types["PreAuthKey"](id=9, key="SECRET", acl_tags=["tag:agent"], expiration={"seconds": 1})
        listing = self.types["ListPreAuthKeysResponse"](pre_auth_keys=[key])
        self.adapter.rpc.side_effect = [listing, self.types["ExpirePreAuthKeyResponse"](), listing]
        with patch.object(admin, "NativeAdmin", return_value=self.adapter):
            self.assertEqual(admin.manage_machine_key("revoke", "9"), {"id": "9", "state": "expired"})
            self.adapter.rpc.side_effect = None
            self.adapter.rpc.return_value = listing
            self.adapter.nodes = Mock(return_value=[])
            result = admin.inspect_machine_keys()
        self.assertNotIn("SECRET", json.dumps(result))

    def test_reconcile_refuses_changed_snapshot_without_mutation(self):
        intent = {"schema": 1, "endpoint": "https://vpn.example.com", "tags": ["tag:agent"],
                  "expiry": 1234, "before": []}
        self.adapter.endpoint = intent["endpoint"]
        self.adapter.rpc.return_value = self.types["ListPreAuthKeysResponse"](pre_auth_keys=[{"id": 9}])
        self.adapter.nodes = Mock(return_value=[])
        with patch.object(admin, "NativeAdmin", return_value=self.adapter), patch.object(admin, "resource_lock", return_value=nullcontext()), patch.object(Path, "lstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)), patch.object(admin, "regular_bytes", return_value=json.dumps(intent).encode()):
            with self.assertRaisesRegex(admin.AdminError, "changed"):
                admin.reconcile_machine_key([], [])
        self.adapter.rpc.assert_called_once_with("ListPreAuthKeys")


if __name__ == "__main__":
    unittest.main()
