"""Client bindings survive reference renames without silently moving networks."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib.headscale_registry import HeadscaleServer, mutate_servers
from lib.headscale_selection import reconcile_saved_selection, resolve_client_server
from lib.headscale_transport import SSHBinding
from lib.atomic_io import write_json_atomic
from lib.cache import _cache_path_for_host


class HeadscaleSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        Path(self.workspace).chmod(0o700)
        self.server = HeadscaleServer(
            "e23c52b1-1e80-4f91-9a68-f15c530c1d21", "Office",
            "https://vpn.example.com", "external", "https://join.example.com",
        )
        mutate_servers(self.workspace, lambda records: ([self.server], None))

    def test_new_selection_is_exact_case_insensitive_and_read_only(self):
        before = (Path(self.workspace) / "headscale-servers.json").read_bytes()
        with patch("lib.headscale_registry.resource_lock", side_effect=AssertionError("mutation")):
            selected = resolve_client_server("office", workspace=self.workspace)
        self.assertEqual(self.server, selected)
        self.assertEqual(before, (Path(self.workspace) / "headscale-servers.json").read_bytes())
        with self.assertRaises(ValueError):
            resolve_client_server("off", workspace=self.workspace)

    def test_saved_identity_follows_a_rename(self):
        renamed = replace(self.server, name="Home")
        mutate_servers(self.workspace, lambda records: ([renamed], None))
        selected = resolve_client_server(
            "Office", server_id=self.server.id, endpoint=self.server.url,
            workspace=self.workspace,
        )
        self.assertEqual(renamed, selected)

    def test_changed_endpoint_and_explicit_other_selection_are_refused(self):
        other = replace(self.server, id="092f1108-5af3-49f1-9c38-434b3d3b355c",
                        name="Other", url="https://other.example.com")
        mutate_servers(self.workspace, lambda records: ([self.server, other], None))
        with self.assertRaisesRegex(ValueError, "network change"):
            resolve_client_server(
                "Other", server_id=self.server.id, endpoint=self.server.url,
                reference_is_explicit=True, workspace=self.workspace,
            )
        changed = replace(self.server, url="https://replacement.example.com")
        mutate_servers(self.workspace, lambda records: ([changed, other], None))
        with self.assertRaisesRegex(ValueError, "endpoint changed"):
            resolve_client_server(
                "Office", server_id=self.server.id, endpoint=self.server.url,
                workspace=self.workspace,
            )

    def test_incomplete_binding_or_removed_reference_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "missing its endpoint"):
            resolve_client_server("Office", server_id=self.server.id, workspace=self.workspace)
        mutate_servers(self.workspace, lambda records: ([], None))
        with self.assertRaises(ValueError):
            resolve_client_server(
                "Office", server_id=self.server.id, endpoint=self.server.url,
                workspace=self.workspace,
            )


class ReconcileSelectionTests(unittest.TestCase):
    def setUp(self):
        HeadscaleSelectionTests.setUp(self)
        self.old_id = "092f1108-5af3-49f1-9c38-434b3d3b355c"
        self.host = "client.example.com"
        self.path = Path(_cache_path_for_host(self.host, self.workspace))
        self.path.parent.mkdir(mode=0o700)
        self.record = {"host": self.host, "system_type": "server_lite", "last_success": False,
                       "private_metadata": {"retained": [1, 2]}, "args": {"username": "operator",
                           "tailscale": True, "tailscale_server": "Old",
                           "tailscale_server_id": self.old_id,
                           "tailscale_login_server": "https://old.example.com"}}
        self.save()
        self.target = SSHBinding(self.host, "/missing/key", "/missing/known_hosts")
        self.kwargs = dict(expected_server_id=self.old_id, expected_url="https://old.example.com",
                           server=self.server.id, new_url=self.server.url, node_id="node-new", tags=[],
                           target=self.target, workspace=self.workspace)
        self.observed = {"phase": "enrolled", "identity": {
            "node_id": "node-new", "addresses": ["100.64.0.4"], "login_server": self.server.url,
            "tags": [], "online": True, "hostname": "client"}}
        self.enterContext(patch("lib.concurrency.resource_lock", side_effect=lambda *a, **k: nullcontext()))
        self.exchange = self.enterContext(patch("lib.headscale_transport.exchange", return_value=self.observed))

    def save(self):
        write_json_atomic(str(self.path), self.record)

    def reconcile(self, **overrides):
        reconcile_saved_selection(self.host, **(self.kwargs | overrides))

    def test_stale_binding_repaired_personal_and_tagged_metadata_preserved(self):
        for tags in ([], ["tag:service"]):
            self.save()
            self.observed["identity"]["tags"] = tags
            self.reconcile(tags=tags)
            result = json.loads(self.path.read_text())
            expected = json.loads(json.dumps(self.record))
            expected["args"].update(tailscale_server_id=self.server.id, tailscale_server=self.server.name,
                                    tailscale_login_server=self.server.url)
            self.assertEqual(expected, result)
            self.assertEqual(self.server, resolve_client_server(result["args"]["tailscale_server"],
                server_id=self.server.id, endpoint=self.server.url, workspace=self.workspace))
        self.exchange.assert_called_with(self.target, "client", {
            "action": "observe", "url": self.server.url, "tags": ["tag:service"]})

    def test_wrong_expected_binding_host_endpoint_and_node_preserve_cache(self):
        before = self.path.read_bytes()
        for override in ({"expected_server_id": self.server.id}, {"expected_url": self.server.url},
                         {"new_url": "https://wrong.example.com"}, {"node_id": "wrong"},
                         {"target": replace(self.target, host="other.example.com")}, {"tags": ["tag:service"]}):
            with self.subTest(override=override), self.assertRaises((ValueError, RuntimeError)):
                self.reconcile(**override)
            self.assertEqual(before, self.path.read_bytes())

    def test_unconfirmed_or_wrong_native_identity_preserves_cache(self):
        before = self.path.read_bytes()
        for field, value in (("login_server", "https://wrong.example.com"), ("node_id", "wrong"),
                             ("online", False), ("addresses", ["192.0.2.1"]), ("tags", None)):
            original = self.observed["identity"][field]
            self.observed["identity"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.reconcile()
            self.observed["identity"][field] = original
            self.assertEqual(before, self.path.read_bytes())
        self.observed["phase"] = "prepared"
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(before, self.path.read_bytes())

    def test_corrupt_and_unsafe_cache_retained_without_network(self):
        for content in ("{broken", '{"version":2}', '{"args":[]}'):
            self.path.write_text(content)
            with self.assertRaises(ValueError):
                self.reconcile()
            self.assertEqual(content, self.path.read_text())
        self.save()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.reconcile()
        self.exchange.assert_not_called()

    def test_wrong_cached_host_and_unsafe_links_are_retained(self):
        self.record["host"] = "wrong.example.com"
        self.save()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertEqual(before, self.path.read_bytes())
        self.record["host"] = self.host
        self.save()
        other = self.path.with_suffix(".retained")
        self.path.rename(other)
        self.path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.reconcile()
        self.assertTrue(self.path.is_symlink())
        self.exchange.assert_not_called()

    def test_concurrent_cache_and_registry_edits_fail_closed(self):
        def change_cache(*args):
            self.record["private_metadata"]["concurrent"] = True
            self.save()
            return self.observed
        self.exchange.side_effect = change_cache
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.reconcile()
        self.assertEqual(self.record, json.loads(self.path.read_text()))
        def change_registry(*args):
            mutate_servers(self.workspace, lambda records: ([replace(self.server, name="Renamed")], None))
            return self.observed
        self.exchange.side_effect = change_registry
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.reconcile()
        self.assertEqual(self.record, json.loads(self.path.read_text()))

    def test_dry_run_no_locks_credentials_connection_or_writes(self):
        before = self.path.read_bytes()
        paths = sorted(Path(self.workspace).rglob("*"))
        with patch("lib.concurrency.resource_lock", side_effect=AssertionError("lock")), \
                patch("lib.headscale_transport._credential", side_effect=AssertionError("credential")):
            self.reconcile(dry_run=True)
            with self.assertRaises(ValueError):
                self.reconcile(new_url="invalid", dry_run=True)
        self.exchange.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(paths, sorted(Path(self.workspace).rglob("*")))

    def test_setup_target_lock_spans_network_and_cache_lock_guards_publication(self):
        held = []
        @contextmanager
        def lock(namespace, key, **kwargs):
            held.append((namespace, key))
            try:
                yield
            finally:
                held.pop()
        def exchange(*args):
            self.assertEqual([("setup-target", self.host)], held)
            return self.observed
        from lib.atomic_io import write_json_atomic as publish
        def write(*args, **kwargs):
            self.assertEqual([("setup-target", self.host), ("setup-cache", str(self.path)),
                              ("headscale-registry", self.workspace)], held)
            return publish(*args, **kwargs)
        self.exchange.side_effect = exchange
        with patch("lib.concurrency.resource_lock", side_effect=lock), \
                patch("lib.atomic_io.write_json_atomic", side_effect=write):
            self.reconcile()
        self.assertEqual([], held)

    def test_omission_and_opt_out_remain_exact(self):
        for mode in (None, False, "absent"):
            if mode == "absent":
                self.record["args"].pop("tailscale", None)
            else:
                self.record["args"]["tailscale"] = mode
            if mode is False:
                self.record["args"]["tailscale_server"] = None
            self.save()
            self.reconcile()
            result = json.loads(self.path.read_text())["args"]
            self.assertEqual("tailscale" in self.record["args"], "tailscale" in result)
            self.assertEqual(self.record["args"].get("tailscale"), result.get("tailscale"))
            if mode is False:
                self.assertIsNone(result["tailscale_server"])


class RemoteSelectionObservationTests(unittest.TestCase):
    def test_observe_requires_owned_enrolled_private_matching_native_identity(self):
        from contextlib import ExitStack
        from common.service_tools import headscale_client_remote as remote
        from lib import headscale_client as client
        from tests.test_headscale_client import observed

        url = "https://vpn.example.com"
        request = {"action": "observe", "url": url, "tags": []}
        binding = {"phase": "enrolled", "node_id": "node123"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(remote.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(client, "client_lock", side_effect=nullcontext))
            stack.enter_context(patch.object(client, "reject_control_host"))
            stack.enter_context(patch.object(client, "owned", return_value=binding))
            native_state = stack.enter_context(patch.object(client, "check_native_state"))
            privacy = stack.enter_context(patch.object(remote, "_privacy"))
            stack.enter_context(patch.object(client, "snapshot", return_value=(observed(), {"ControlURL": url})))
            native = stack.enter_context(patch.object(client, "native", side_effect=AssertionError("mutation")))
            def execute():
                return remote.execute_request(json.dumps(request).encode())
            for tags in ([], ["tag:service"]):
                request["tags"] = tags
                with patch.object(client, "snapshot", return_value=(observed(tags=tags), {"ControlURL": url})):
                    result = execute()
                    self.assertTrue(result["ok"])
                    self.assertEqual(tags, list(result["result"]["identity"]["tags"]))
            request["tags"] = []
            privacy.side_effect = ValueError("privacy unconfirmed")
            self.assertFalse(execute()["ok"])
            privacy.side_effect = None
            native_state.side_effect = ValueError("unsafe state")
            self.assertFalse(execute()["ok"])
            native_state.side_effect = None
            for phase in ("prepared", "joining", "left", "resuming"):
                binding["phase"] = phase
                self.assertFalse(execute()["ok"])
            binding.update(phase="enrolled", node_id="wrong")
            self.assertFalse(execute()["ok"])
            native.assert_not_called()
