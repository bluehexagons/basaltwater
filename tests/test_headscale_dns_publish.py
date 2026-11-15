"""DNS publication ordering, uncertainty, replacement and recovery offline."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.atomic_io import read_json_file
from lib.headscale_dns_binding import DNSBinding, dns_binding_lease, read_dns_binding, resolver_lifecycle_guard
from lib.headscale_dns_peer import DNSRemote
from lib.headscale_admin import NativeAdmin
from lib.headscale_server import ServerPaths
from lib import headscale_dns_publish as pub


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.paths = ServerPaths(base / "etc", base / "data", base / "private", base / "nginx", base / "socket")
        self.paths.etc.mkdir()
        self.paths.private.mkdir(mode=0o700)
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",))
        self.peer = replace(self.binding, node_id="8", addresses=("100.64.0.3",))
        self.new = replace(self.binding, node_id="9", addresses=("100.64.0.4",))
        self.config = {"server_url": self.binding.network_url, "policy": {"path": "preserved"},
                       "dns": {"magic_dns": True, "base_domain": "mesh.example.net",
                               "override_local_dns": False, "nameservers": {"global": [], "split": {}},
                               "extra_records": [{"name": "preserved"}]}}
        self.paths.config.write_text(json.dumps(self.config))
        self.paths.config.chmod(0o600)
        self.admin = Mock(paths=self.paths, endpoint=self.binding.network_url)
        self.admin.nodes.side_effect = lambda: [dict(node_id=b.node_id, addresses=list(b.addresses), online=True)
                                                for b in (self.binding, self.peer, self.new)]
        self.events = []
        self.states = {"old": self.binding, "new": self.new}
        self.old_remote, self.new_remote, self.peer_remote = Mock(), Mock(), Mock()
        self.old_remote.call.side_effect = lambda action, **p: self.remote("old", action, p)
        self.new_remote.call.side_effect = lambda action, **p: self.remote("new", action, p)
        self.peer_remote.call.side_effect = self.peer_call
        ownership = patch("lib.headscale_server.os.fchown")
        ownership.start()
        self.addCleanup(ownership.stop)
        for target, kwargs in (
            ("NativeAdmin", {"return_value": self.admin}),
            ("health", {}), ("publish_config", {"side_effect": self.activate}),
            ("pwd.getpwnam", {"return_value": SimpleNamespace(pw_gid=0, pw_uid=os.geteuid())}),
        ):
            mock = patch("lib.headscale_dns_publish." + target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def remote(self, name, action, p):
        self.events.append((name, action))
        current = self.states[name]
        if action != "inspect":
            self.assertEqual(current.to_dict(), p["binding"])
        if action == "cas":
            from lib.headscale_dns_binding import parse_dns_binding
            self.states[name] = parse_dns_binding(p["desired"])
        return {"binding": self.states[name].to_dict()}

    def peer_call(self, action, **p):
        self.events.append(("peer", p["effective"]))
        self.assertEqual("peer", action)
        return {"peer": self.peer.to_dict(), "checked": True}

    def activate(self, content, paths, **kwargs):
        self.events.append(("control", "publish"))
        guard = read_dns_binding(str(paths.private / "dns-publication.json"))
        if json.loads(content)["dns"]["nameservers"]["global"]:
            self.assertTrue(guard.advertised)
            self.assertIn(guard, self.states.values())
        paths.config.write_text(content)

    def publish(self, **kwargs):
        return pub.publish_dns(self.binding, dns_remote=self.old_remote, peer=self.peer,
                               peer_remote=self.peer_remote, **kwargs)

    def test_publication_order_preserves_policy_and_candidate_is_not_automatic(self):
        self.assertFalse(self.events)
        result = self.publish()
        self.assertTrue(result["binding"]["advertised"])
        self.assertLess(self.events.index(("peer", None)), self.events.index(("old", "cas")))
        self.assertLess(self.events.index(("old", "cas")), self.events.index(("control", "publish")))
        self.assertLess(self.events.index(("control", "publish")), self.events.index(("peer", "published")))
        self.assertEqual(self.config["policy"], read_json_file(str(self.paths.config))["policy"])
        self.assertFalse((self.paths.private / "dns-transaction.json").exists())

    def test_peer_forgery_and_failure_never_publish(self):
        for result in ({}, {"peer": self.peer.to_dict()}, {"peer": self.peer.to_dict(), "checked": False}):
            self.peer_remote.call.side_effect = None
            self.peer_remote.call.return_value = result
            with self.assertRaises(ValueError):
                self.publish()
        self.assertNotIn(("control", "publish"), self.events)
        self.assertFalse(self.states["old"].advertised)

    def test_wrong_network_node_addresses_and_same_peer_rejected(self):
        for b in (replace(self.binding, network_url="https://other.example.com"),
                  replace(self.binding, node_id="99"), replace(self.binding, addresses=("100.64.0.9",))):
            with self.assertRaises((ValueError, AssertionError)):
                pub.publish_dns(b, dns_remote=self.old_remote, peer=self.peer, peer_remote=self.peer_remote)
        with self.assertRaises(ValueError):
            pub.publish_dns(self.peer, dns_remote=self.old_remote, peer=self.peer, peer_remote=self.peer_remote)
        self.assertNotIn(("control", "publish"), self.events)

    def test_lost_reservation_ack_blocks_retry_and_reconciles_before(self):
        original = self.old_remote.call.side_effect
        def lost(action, **p):
            value = original(action, **p)
            if action == "cas":
                raise ValueError("lost acknowledgement")
            return value
        self.old_remote.call.side_effect = lost
        with self.assertRaises(ValueError):
            self.publish()
        self.assertTrue(self.states["old"].advertised)
        with self.assertRaises(ValueError):
            self.publish()
        self.old_remote.call.side_effect = original
        inspected = pub.inspect_dns()
        pub.reconcile_dns(expected_journal=inspected["journal"], keep="before", peer=self.peer,
                          peer_remote=self.peer_remote, new_remote=self.old_remote)
        self.assertFalse(self.states["old"].advertised)
        self.assertFalse((self.paths.private / "operation.json").exists())

    def test_effective_peer_failure_retains_both_guards_recover_after(self):
        original = self.peer_remote.call.side_effect
        def fail(action, **p):
            if p["effective"]:
                raise ValueError("map propagation unconfirmed")
            return original(action, **p)
        self.peer_remote.call.side_effect = fail
        with self.assertRaises(ValueError):
            self.publish()
        self.assertTrue(pub.inspect_dns()["binding"]["advertised"])
        self.peer_remote.call.side_effect = original
        pub.reconcile_dns(expected_journal=pub.inspect_dns()["journal"], keep="after", peer=self.peer,
                          peer_remote=self.peer_remote, new_remote=self.old_remote)
        self.assertTrue(self.states["old"].advertised)

    def test_replace_reserves_new_and_checks_peer_before_old_release(self):
        self.publish()
        old = self.states["old"]
        self.events.clear()
        pub.publish_dns(self.new, dns_remote=self.new_remote, peer=self.peer, peer_remote=self.peer_remote,
                        expected=old, old_remote=self.old_remote)
        self.assertLess(self.events.index(("new", "cas")), self.events.index(("control", "publish")))
        self.assertLess(self.events.index(("peer", "published")), self.events.index(("old", "cas")))
        self.assertFalse(self.states["old"].advertised)
        self.assertTrue(self.states["new"].advertised)

    def test_withdraw_checks_peer_before_release_and_clears_management(self):
        self.publish()
        self.events.clear()
        pub.withdraw_dns(self.states["old"], dns_remote=self.old_remote,
                         peer=self.peer, peer_remote=self.peer_remote)
        self.assertLess(self.events.index(("peer", "withdrawn")), self.events.index(("old", "cas")))
        self.assertIsNone(pub.inspect_dns()["binding"])
        self.assertFalse(self.states["old"].advertised)

    def test_dry_run_creates_no_lock_or_network_or_state(self):
        self.publish(dry_run=True)
        self.assertFalse(self.events)
        self.assertEqual([], list(self.paths.private.iterdir()))
        self.admin.verify.assert_not_called()

    def test_foreign_dns_policy_fails_without_overwrite(self):
        for field, value in (("override_local_dns", True), ("nameservers", {"global": ["192.0.2.1"], "split": {}})):
            config = deepcopy(self.config)
            config["dns"][field] = value
            with self.assertRaises(ValueError):
                pub.dns_candidate(config, None, self.binding)

    def test_node_lifecycle_guard_retains_reservation(self):
        path = str(self.paths.private / "node-binding.json")
        reserved = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        with dns_binding_lease(path) as lease:
            lease.save(reserved)
        with self.assertRaises(ValueError), resolver_lifecycle_guard(path):
            self.fail("Guard bypassed")

    def test_native_teardown_guard_uses_publication_lease(self):
        self.publish()
        admin = NativeAdmin(self.paths)
        admin.endpoint = self.binding.network_url
        node = dict(node_id=self.binding.node_id, addresses=list(self.binding.addresses))
        with self.assertRaisesRegex(RuntimeError, "Withdraw or replace"), admin.dns_guard(node):
            self.fail("Advertised native node removal allowed")

    def test_lost_withdraw_release_ack_reconciles_without_replay(self):
        self.publish()
        old = self.states["old"]
        original = self.old_remote.call.side_effect
        def lost(action, **p):
            result = original(action, **p)
            if action == "cas":
                raise ValueError("lost release acknowledgement")
            return result
        self.old_remote.call.side_effect = lost
        with self.assertRaises(ValueError):
            pub.withdraw_dns(old, dns_remote=self.old_remote, peer=self.peer, peer_remote=self.peer_remote)
        self.assertFalse(self.states["old"].advertised)
        self.old_remote.call.side_effect = original
        self.events.clear()
        pub.reconcile_dns(expected_journal=pub.inspect_dns()["journal"], keep="after",
                          peer=self.peer, peer_remote=self.peer_remote, old_remote=self.old_remote)
        self.assertNotIn(("old", "cas"), self.events)
        self.assertIsNone(pub.inspect_dns()["binding"])

    def test_stale_recovery_and_changed_config_preserve_guards(self):
        with patch.object(pub, "publish_config", side_effect=RuntimeError("activation uncertain")):
            with self.assertRaises(RuntimeError):
                self.publish()
        journal = pub.inspect_dns()["journal"]
        journal["new"]["node_id"] = "99"
        with self.assertRaises(ValueError):
            pub.reconcile_dns(expected_journal=journal, keep="before", peer=self.peer,
                              peer_remote=self.peer_remote, new_remote=self.old_remote)
        self.assertTrue(self.states["old"].advertised)


class SSHValidationTests(unittest.TestCase):
    def test_no_shell_or_nonroot_authority(self):
        for host in ("-oProxyCommand=x", "host;id", "host\n"):
            with self.assertRaises(ValueError):
                DNSRemote(host, "/key", "/known")
        with self.assertRaises(ValueError):
            DNSRemote("dns.example.com", "/key", "/known", "agent")

    def test_fixed_command_and_strict_bindings_no_retry(self):
        remote = DNSRemote("dns.example.com", "/key", "/known")
        with patch.object(DNSRemote, "validate_files"), \
                patch("lib.headscale_dns_peer.exchange", side_effect=RuntimeError("unconfirmed")) as exchange:
            with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                remote.call("inspect", binding={})
            exchange.assert_called_once_with(
                remote._binding(), "dns", {"action": "inspect", "parameters": {"binding": {}}}, timeout=300)
