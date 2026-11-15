"""Public local recovery commands retain native guards and private inputs."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basaltwater
from lib import headscale_client as client
from lib.concurrency import ResourceBusyError, ResourceLockSafetyError
from tests.test_headscale_client import observed, URL, OTHER
from tests import test_headscale_selection as selection_tests


class LocalCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.root.chmod(0o700)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(client, "CLIENT_STATE", str(self.root / "state" / "client.json")))
        self.stack.enter_context(patch.object(client, "CONTROL_CONFIG", str(self.root / "control.yaml")))
        self.stack.enter_context(patch.object(client, "client_lock", side_effect=nullcontext))
        self.stack.enter_context(patch.object(client, "resolver_lifecycle_guard", side_effect=nullcontext))
        self.stack.enter_context(patch.object(client, "require_private_daemon"))
        self.stack.enter_context(patch("lib.headscale_client_cli.os", SimpleNamespace(geteuid=lambda: 0, isatty=lambda fd: True)))
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.stack.enter_context(redirect_stderr(self.output))

    def invoke(self, action, *flags):
        with patch("sys.argv", ["basaltw", "headscale-client", action, "--url", URL, *flags]):
            return basaltwater.main()

    def identity(self, tags=None):
        return client._identity(observed(tags=tags), {"ControlURL": URL}, URL, True)

    def test_status_filters_native_auth_url(self):
        with patch.object(client, "native", side_effect=[json.dumps({"short": "1.104.7"}),
                json.dumps(observed()), json.dumps({"ControlURL": URL})]):
            self.assertEqual(0, self.invoke("status"))
        self.assertNotIn("private-pending-id", self.output.getvalue())
        self.assertEqual("node123", json.loads(self.output.getvalue())["node_id"])

    def test_every_dry_run_avoids_native_calls_locks_and_state(self):
        key = self.root / "join.key"
        key.write_text("synthetic-private-key")
        key.chmod(0o600)
        cases = {"status": (), "enroll-personal": (), "enroll-machine": (
            "--key-file", str(key), "--approved-tag", "tag:dns"), "reconcile": (),
            "adopt": ("--node-id", "node123"), "disable": (), "leave": ("--node-id", "node123"),
            "migrate": ("--node-id", "node123", "--new-url", OTHER), "abandon": ("--confirm",)}
        with patch.object(client, "native", side_effect=AssertionError("native")), \
                patch.object(client, "client_lock", side_effect=AssertionError("lock")):
            for action, flags in cases.items():
                self.assertEqual(0, self.invoke(action, *flags, "--dry-run"))
        self.assertFalse(Path(client.CLIENT_STATE).exists())
        self.assertTrue(key.exists())
        self.assertNotIn("synthetic-private-key", self.output.getvalue())

    def test_machine_lost_response_consumes_file_and_never_replays(self):
        client.save_binding(URL, "prepared")
        key = self.root / "join.key"
        key.write_text("synthetic-private-key")
        key.chmod(0o600)
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), \
                patch.object(client, "native", side_effect=client.ClientError("synthetic-private-key")) as native:
            self.assertEqual(1, self.invoke("enroll-machine", "--key-file", str(key), "--approved-tag", "tag:dns"))
            self.assertFalse(key.exists())
            key.write_text("replacement-key")
            key.chmod(0o600)
            self.assertEqual(1, self.invoke("enroll-machine", "--key-file", str(key), "--approved-tag", "tag:dns"))
            self.assertEqual(1, native.call_count)
        self.assertEqual("joining", client.read_binding()["phase"])
        self.assertNotIn("synthetic-private-key", self.output.getvalue())

    def test_personal_requires_all_private_terminal_descriptors(self):
        with patch("lib.headscale_client_cli.os", SimpleNamespace(geteuid=lambda: 0, isatty=lambda fd: fd != 0)), \
                patch.object(client, "enroll_client") as enroll:
            self.assertEqual(1, self.invoke("enroll-personal"))
            enroll.assert_not_called()

    def test_adoption_and_reconciliation_use_exact_native_identity(self):
        with patch.object(client, "observe_client", return_value=self.identity()):
            self.assertEqual(1, self.invoke("adopt", "--node-id", "wrong"))
            self.assertFalse(Path(client.CLIENT_STATE).exists())
            self.assertEqual(0, self.invoke("adopt", "--node-id", "node123"))
            self.assertEqual(0, self.invoke("reconcile"))
        self.assertEqual("node123", client.read_binding()["node_id"])

    def test_dns_guards_block_all_teardown_through_public_cli(self):
        with patch.object(client, "resolver_lifecycle_guard", side_effect=ValueError("advertised")), \
                patch.object(client, "native") as native:
            for action, flags in (("disable", ()), ("leave", ("--node-id", "node123")),
                                 ("migrate", ("--node-id", "node123", "--new-url", OTHER)),
                                 ("abandon", ("--confirm",))):
                self.assertEqual(1, self.invoke(action, *flags))
            native.assert_not_called()

    def test_successful_leave_migration_and_abandon_update_only_reviewed_binding(self):
        for action, flags, phase, endpoint in (
            ("leave", ("--node-id", "node123"), "enrolled", URL),
            ("migrate", ("--node-id", "node123", "--new-url", OTHER), "enrolled", OTHER),
            ("abandon", ("--confirm",), "joining", URL),
        ):
            client.save_binding(URL, phase, "node123" if phase == "enrolled" else "")
            with patch.object(client, "observe_client", return_value=self.identity()), \
                    patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), \
                    patch.object(client, "native") as native:
                self.assertEqual(0, self.invoke(action, *flags))
                native.assert_called_once_with(["tailscale", "logout"])
            self.assertEqual(endpoint, client.read_binding()["url"])
            self.assertEqual("left", client.read_binding()["phase"])

    def test_disable_preserves_native_identity(self):
        client.save_binding(URL, "enrolled", "node123")
        with patch.object(client, "native", side_effect=["inactive", "", "ActiveState=inactive\nUnitFileState=disabled\n"]):
            self.assertEqual(0, self.invoke("disable"))
        self.assertEqual("node123", client.read_binding()["node_id"])

    def test_corrupt_binding_is_retained_and_lock_errors_are_bounded(self):
        client.save_binding(URL, "prepared")
        state = Path(client.CLIENT_STATE)
        state.write_text("{corrupt")
        self.assertEqual(1, self.invoke("reconcile"))
        self.assertEqual("{corrupt", state.read_text())
        for error in (ResourceBusyError, ResourceLockSafetyError):
            with patch.object(client, "client_lock", side_effect=error("sensitive native evidence")):
                self.assertEqual(1, self.invoke("reconcile"))
        self.assertNotIn("sensitive native evidence", self.output.getvalue())
        self.assertNotIn("Traceback", self.output.getvalue())

    def test_root_admin_dispatch_and_lock_diagnostics(self):
        for error in (ResourceBusyError, ResourceLockSafetyError):
            with patch("sys.argv", ["basaltw", "headscale-admin", "nodes-list"]), \
                    patch("lib.headscale_admin.execute_admin_action", side_effect=error("private")):
                self.assertEqual(1, basaltwater.main())
        with patch("sys.argv", ["basaltw", "headscale-admin", "nodes-list", "--dry-run"]), \
                patch("lib.headscale_admin.execute_admin_action", side_effect=AssertionError("native")):
            self.assertEqual(0, basaltwater.main())


class SelectionCliTests(unittest.TestCase):
    def test_public_dispatch_reconciles_saved_binding(self):
        fixture = selection_tests.ReconcileSelectionTests("test_omission_and_opt_out_remain_exact")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        flags = ["basaltw", "headscale-client", "reconcile-selection", fixture.host,
                 "--expected-server-id", fixture.old_id, "--expected-url", fixture.kwargs["expected_url"],
                 "--server", fixture.server.id, "--new-url", fixture.server.url, "--node-id", "node-new",
                 "--target-host", fixture.host, "--target-key", fixture.target.identity_file,
                 "--target-known-hosts", fixture.target.known_hosts_file, "--workspace", fixture.workspace]
        with redirect_stdout(io.StringIO()), patch("sys.argv", flags + ["--dry-run"]):
            self.assertEqual(0, basaltwater.main())
        fixture.exchange.assert_not_called()
        with redirect_stdout(io.StringIO()), patch("sys.argv", flags):
            self.assertEqual(0, basaltwater.main())
        self.assertEqual(fixture.server.id, json.loads(fixture.path.read_text())["args"]["tailscale_server_id"])
