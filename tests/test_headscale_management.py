"""Controller authorization, private delivery, and interrupted-attempt contracts."""

from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tempfile
import uuid

from common.service_tools import headscale_management as server, headscale_client_remote as target
from lib import headscale_management as management
from lib import headscale_registry as registry
from lib.headscale_management_cli import add_headscale_management_parser, run_headscale_management_command
from lib.headscale_private import read_private, write_private
from lib.headscale_server import ServerPaths
from lib.headscale_transport import SSHBinding, TransportError

URL = "https://vpn.example.com"
SECRET = "hskey-auth-" + "p" * 12 + "-" + "s" * 64
TAGS = ["tag:service"]
IDENTITY = {"node_id": "7", "addresses": ["100.64.0.7"], "login_server": URL,
            "tags": TAGS, "online": True, "hostname": "worker"}
NODE = {"id": "7", "node_id": "7", "addresses": ["100.64.0.7"], "tags": TAGS,
        "online": True, "hostname": "worker", "user_id": None, "expiry": None}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = str(Path(temporary.name) / "workspace")
        self.record = registry.HeadscaleServer(str(uuid.uuid4()), "office", URL, "external")
        with patch.object(registry, "resource_lock", return_value=nullcontext()):
            registry.mutate_servers(self.workspace, lambda records: (records + [self.record], None))
        self.ssh = SSHBinding("control.example.com", "/private/id_ed25519", "/private/known_hosts")
        self.target = SSHBinding("worker.example.com", "/private/worker_key", "/private/known_hosts")
        self.lock = patch.object(management, "resource_lock", side_effect=lambda *args, **kwargs: nullcontext())
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.transport = patch.object(management, "exchange", side_effect=self.exchange).start()
        self.addCleanup(patch.stopall)
        self.calls = []
        self.failure = None
        management.bind_management("office", self.ssh, workspace=self.workspace)
        self.calls.clear()

    def exchange(self, ssh, service, request):
        action = request["action"]
        self.calls.append((service, action))
        if self.failure == (service, action):
            raise TransportError("Unconfirmed")
        if service == "client":
            if action == "inspect":
                return {"phase": "prepared", "identity": None}
            self.assertEqual(request["key"], SECRET)
            return {"identity": dict(IDENTITY)}
        if action == "identity":
            return {"endpoint": URL, "version": management.VERSION}
        if action == "issue":
            self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], "issuing")
            return {"id": "9", "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "tags": TAGS, "reusable": False, "ephemeral": False}
        if action == "take":
            self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], "delivering")
            return {"id": "9", "key": SECRET, "tags": TAGS, "endpoint": URL}
        if action == "complete":
            self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], "verifying")
            return {"key_id": "9", "node": dict(NODE)}
        if action == "inspect":
            return {"keys": [{"id": "9"}], "nodes": [dict(NODE)], "reconciliation_required": True}
        if action == "reconcile":
            return {"reconciled": ["9"], "nodes": ["7"]}
        self.fail("Unexpected action")

    def test_reference_does_not_grant_management_and_rename_keeps_binding(self):
        with patch.object(registry, "resource_lock", return_value=nullcontext()):
            registry.mutate_servers(self.workspace, lambda records: ([replace(self.record, name="renamed")], None))
        self.assertEqual(management.resolve_management("renamed", workspace=self.workspace).server_id, self.record.id)
        with patch.object(registry, "resource_lock", return_value=nullcontext()):
            registry.mutate_servers(self.workspace, lambda records: ([replace(self.record, url="https://other.example.com")], None))
        with self.assertRaisesRegex(ValueError, "endpoint changed"):
            management.resolve_management(self.record.id, workspace=self.workspace)
        self.assertEqual(self.calls, [])

    def test_enrollment_orders_intent_delivery_observation_and_completion_without_saved_secret(self):
        result = management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)
        self.assertEqual(result["node"]["node_id"], "7")
        self.assertEqual(self.calls, [("management", "identity"), ("client", "inspect"),
                                     ("management", "issue"), ("management", "take"),
                                     ("client", "enroll"), ("management", "complete")])
        self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], "complete")
        for path in Path(self.workspace).iterdir():
            self.assertNotIn(SECRET.encode(), path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_each_lost_ack_retains_guard_and_never_reissues(self):
        for failure, phase in [(("management", "issue"), "issuing"), (("management", "take"), "delivering"),
                               (("client", "enroll"), "delivering"), (("management", "complete"), "verifying")]:
            with self.subTest(failure=failure):
                Path(self.workspace, "headscale-enrollment.json").unlink(missing_ok=True)
                self.failure = failure
                with self.assertRaises(TransportError):
                    management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)
                self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], phase)
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, "previous enrollment"):
                    management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)
                with self.assertRaisesRegex(ValueError, "retained enrollment"):
                    management.remove_management("office", workspace=self.workspace)
                self.assertEqual(self.calls, [])

    def test_dry_runs_do_not_lock_connect_or_create_state(self):
        self.lock.stop()
        with patch.object(management, "resource_lock") as lock:
            management.bind_management("office", self.ssh, workspace=self.workspace, dry_run=True)
            management.enroll_machine("office", self.target, TAGS, workspace=self.workspace, dry_run=True)
            management.remove_management("office", workspace=self.workspace, dry_run=True)
            lock.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertIsNone(management.inspect_enrollment(workspace=self.workspace))

    def test_wrong_server_and_unprepared_target_fail_before_issuance(self):
        for reply in ({"endpoint": "https://other.example.com", "version": management.VERSION},
                      {"endpoint": URL, "version": "0.28.0"}):
            self.transport.side_effect = None
            self.transport.return_value = reply
            with self.assertRaisesRegex(ValueError, "identity differs"):
                management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)
            self.assertIsNone(management.inspect_enrollment(workspace=self.workspace))
        self.transport.side_effect = [{"endpoint": URL, "version": management.VERSION}, {"phase": "joining", "identity": None}]
        with self.assertRaisesRegex(ValueError, "not prepared"):
            management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)

    def test_explicit_reconciliation_does_not_replay_enrollment(self):
        self.failure = ("client", "enroll")
        with self.assertRaises(TransportError):
            management.enroll_machine("office", self.target, TAGS, workspace=self.workspace)
        self.failure = None
        self.calls.clear()
        management.reconcile_enrollment("office", ["9"], ["7"], workspace=self.workspace)
        self.assertEqual(self.calls, [("management", "inspect"), ("management", "reconcile")])
        self.assertEqual(management.inspect_enrollment(workspace=self.workspace)["phase"], "reconciled")

    def test_cli_suppresses_even_unexpected_secret_exception(self):
        parser = argparse.ArgumentParser()
        add_headscale_management_parser(parser.add_subparsers())
        args = parser.parse_args(["headscale-manage", "inspect", "office", "--remote", "--workspace", self.workspace])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(management, "inspect_management", side_effect=RuntimeError(SECRET)), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(run_headscale_management_command(args), 1)
        self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())


class PrivateStateTests(unittest.TestCase):
    def test_corrupt_unsafe_linked_and_unknown_state_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_private(str(path), {"schema": 1})
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                read_private(str(path))
            os.chmod(path, 0o600)
            os.link(path, Path(directory) / "linked")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                read_private(str(path))
            (Path(directory) / "linked").unlink()
            path.write_text('{"secret":"retained","secret":"duplicate"}')
            with self.assertRaises(ValueError):
                read_private(str(path))
            self.assertIn("retained", path.read_text())
            path.unlink()
            path.symlink_to("missing")
            with self.assertRaises((OSError, ValueError)):
                read_private(str(path))


class NativeDeliveryTests(unittest.TestCase):
    def setUp(self):
        try:
            from lib.headscale_admin_schema import messages
            self.types = messages()
        except ImportError:
            self.skipTest("Distribution protobuf required")
        self.adapter = SimpleNamespace(paths=ServerPaths(), endpoint=URL, rpc=Mock())
        self.intent = {"schema": 1, "endpoint": URL, "tags": TAGS, "expiry": 2**32,
                       "before": [], "id": "9", "delivery": "a" * 32}

    def test_claim_precedes_return_and_replay_is_rejected(self):
        key = self.types["PreAuthKey"](id=9, key="hskey-auth-" + "p" * 12 + "-***",
                                        acl_tags=TAGS, expiration={"seconds": self.intent["expiry"]})
        self.adapter.rpc.return_value = self.types["ListPreAuthKeysResponse"](pre_auth_keys=[key])
        events = []
        with patch.object(server, "resource_lock", return_value=nullcontext()), patch.object(server, "_intent", return_value=self.intent), \
                patch.object(server, "read_private", return_value=None), patch.object(Path, "lstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)), \
                patch.object(server.admin, "regular_bytes", return_value=SECRET.encode()), \
                patch.object(server, "write_private", side_effect=lambda *args: events.append("claim")), \
                patch.object(server, "remove_file_durable", side_effect=lambda *args: events.append("remove")):
            result = server.take_key(self.adapter, "9")
            self.assertEqual(events, ["claim", "remove"])
            self.assertEqual(result["key"], SECRET)
            with patch.object(server, "read_private", return_value={"schema": 1}):
                with self.assertRaisesRegex(ValueError, "already claimed"):
                    server.take_key(self.adapter, "9")

    def test_wrong_native_prefix_or_malformed_secret_retains_delivery_without_claim(self):
        for public, private in (("hskey-auth-" + "q" * 12 + "-***", SECRET),
                                ("hskey-auth-" + "p" * 12 + "-***", SECRET[:-1]),
                                (SECRET, SECRET)):
            with self.subTest(public=public[:11], private_length=len(private)):
                key = self.types["PreAuthKey"](id=9, key=public, acl_tags=TAGS,
                                                expiration={"seconds": self.intent["expiry"]})
                self.adapter.rpc.return_value = self.types["ListPreAuthKeysResponse"](pre_auth_keys=[key])
                with patch.object(server, "resource_lock", return_value=nullcontext()), \
                        patch.object(server, "_intent", return_value=self.intent), \
                        patch.object(server, "read_private", return_value=None), \
                        patch.object(Path, "lstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o600)), \
                        patch.object(server.admin, "regular_bytes", return_value=private.encode()), \
                        patch.object(server, "write_private") as claim, \
                        patch.object(server, "remove_file_durable") as remove:
                    with self.assertRaisesRegex(ValueError, "differs from native"):
                        server.take_key(self.adapter, "9")
                    claim.assert_not_called()
                    remove.assert_not_called()

    def test_completion_requires_native_key_to_node_link(self):
        key = self.types["PreAuthKey"](id=9, used=True, acl_tags=TAGS, expiration={"seconds": 1})
        for linked in (False, True):
            node = self.types["Node"](id=7, name="worker", ip_addresses=["100.64.0.7"], tags=TAGS, online=True,
                                      pre_auth_key={"id": 9 if linked else 10})
            def rpc(method, **kwargs):
                if method == "ListNodes":
                    return self.types["ListNodesResponse"](nodes=[node])
                return self.types["ListPreAuthKeysResponse"](pre_auth_keys=[key])
            self.adapter.rpc.side_effect = rpc
            with patch.object(server, "resource_lock", return_value=nullcontext()), patch.object(server, "_intent", return_value=self.intent), \
                    patch.object(server, "read_private", return_value={"schema": 1, "id": "9", "delivery": self.intent["delivery"]}), \
                    patch.object(server, "remove_file_durable") as remove:
                if linked:
                    self.assertEqual(server.complete_delivery(self.adapter, "9", "7", TAGS)["key_id"], "9")
                else:
                    with self.assertRaisesRegex(ValueError, "association unconfirmed"):
                        server.complete_delivery(self.adapter, "9", "7", TAGS)
                    remove.assert_not_called()

    def test_private_helpers_reject_unknown_schema_and_hide_failures(self):
        payloads = [b'x' * 16385, b'{"action":"shell","url":"https://vpn.example.com","tags":["tag:service"]}',
                    json.dumps({"endpoint": URL, "action": "take", "parameters": {"id": SECRET}}).encode()]
        with patch.object(server.os, "geteuid", return_value=0), patch.object(server.admin, "NativeAdmin", side_effect=RuntimeError(SECRET)):
            for payload in payloads:
                self.assertNotIn(SECRET, json.dumps(server.execute_request(payload)))
                self.assertFalse(target.execute_request(payload)["ok"])


if __name__ == "__main__":
    unittest.main()
