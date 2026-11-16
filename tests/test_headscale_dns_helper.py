"""No caller-provided health results; real helper performs fixed observations."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager, nullcontext
import json
import itertools
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools.headscale_dns import execute_request, publication_scope
from common import unbound_steps
from common.unbound_steps import verify_resolver
from lib.headscale_dns_binding import DNSBinding
from lib.unbound_config import health_identity


class DNSHelperTests(unittest.TestCase):
    def setUp(self):
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",))
        self.peer = replace(self.binding, node_id="8", addresses=("100.64.0.3",))
        self.p = dict(binding=self.binding.to_dict(), peer=self.peer.to_dict(), effective=None)
        self.scope = publication_scope(self.binding.network_url, self.binding.node_id, "resolver")
        self.peer_scope = publication_scope(self.peer.network_url, self.peer.node_id, "peer")
        self.ownership = {"phase": "enrolled", "node_id": self.peer.node_id}
        for name, kwargs in (("client_lock", dict(side_effect=lambda: nullcontext())),
                             ("owned", dict(side_effect=lambda url: self.ownership))):
            mocked = patch("lib.headscale_client." + name, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)

    def request(self, p=None):
        return execute_request(json.dumps(dict(action="peer", parameters=p or self.p)).encode(), scope=self.peer_scope)

    def test_native_binding_requires_running_privacy_before_observing_identity(self):
        with patch("lib.headscale_client.require_private_daemon", side_effect=ValueError("privacy unavailable")), \
                patch("lib.headscale_client.observe_client") as observe, \
                patch.object(unbound_steps, "run") as run, self.assertRaises(ValueError):
            unbound_steps._observe(self.binding.network_url)
        observe.assert_not_called()
        run.assert_not_called()

    def test_private_native_binding_checks_actual_interface_addresses(self):
        from lib.headscale_client import ClientIdentity
        identity = ClientIdentity("7", ("100.64.0.2",), self.binding.network_url, (), True, "dns")
        interface = json.dumps([{"addr_info": [{"local": "100.64.0.2"}]}])
        with patch("lib.headscale_client.require_private_daemon") as privacy, \
                patch("lib.headscale_client.observe_client", return_value=identity), \
                patch.object(unbound_steps, "run", return_value=subprocess.CompletedProcess([], 0, interface)):
            self.assertEqual(unbound_steps._observe(self.binding.network_url), self.binding)
        privacy.assert_called_once_with()

    def test_forged_health_and_unknown_commands_rejected(self):
        for p in ({**self.p, "health": True}, {"binding": self.binding.to_dict()},
                  {**self.p, "effective": {"success": True}}):
            self.assertFalse(self.request(p)["ok"])
        self.assertFalse(execute_request(b"x" * 16385)["ok"])

    def test_observed_peer_and_network_required(self):
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("common.unbound_steps._observe", return_value=self.binding), \
                patch("common.unbound_steps.verify_resolver") as verify:
            self.assertFalse(self.request()["ok"])
            verify.assert_not_called()

    def test_peer_queries_are_executed_and_failures_propagate(self):
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("common.unbound_steps._observe", return_value=self.peer), \
                patch("common.unbound_steps.verify_resolver") as verify:
            self.assertTrue(self.request()["ok"])
            verify.assert_called_once_with(self.binding)
            verify.side_effect = RuntimeError("failure")
            self.assertFalse(self.request()["ok"])

    def test_effective_status_requires_exact_resolvers_no_fallback(self):
        self.p["effective"] = "published"
        status = dict(TailscaleDNS=True, CurrentTailnet=dict(MagicDNSEnabled=True),
                      Resolvers=[dict(Addr="100.64.0.2")])
        def native(args, **kwargs):
            return json.dumps(status) if args[0] == "tailscale" else '"' + health_identity(self.binding) + '"'
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("common.unbound_steps._observe", return_value=self.peer), \
                patch("common.unbound_steps.verify_resolver"), \
                patch("lib.headscale_client.native", side_effect=native), \
                patch("common.service_tools.headscale_dns.time.monotonic", side_effect=itertools.count(0, 11)):
            self.assertTrue(self.request()["ok"])
            status["FallbackResolvers"] = [dict(Addr="192.0.2.1")]
            self.assertFalse(self.request()["ok"])
            status.pop("FallbackResolvers")
            status["TailscaleDNS"] = False
            self.assertFalse(self.request()["ok"])

    def test_effective_map_propagation_waits_without_repeating_dns_probes(self):
        published = dict(TailscaleDNS=True, CurrentTailnet=dict(MagicDNSEnabled=True),
                         Resolvers=[dict(Addr="100.64.0.2")])
        for effective, states in (("published", [{}, published]), ("withdrawn", [published, {}])):
            with self.subTest(effective=effective):
                self.p["effective"] = effective
                observations = iter(states)
                def native(args, **kwargs):
                    if args[0] == "tailscale":
                        self.assertEqual(kwargs, dict(timeout=5, limit=65536))
                        return json.dumps(next(observations))
                    return '"' + health_identity(self.binding) + '"'
                with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                        patch("common.unbound_steps._observe", return_value=self.peer), \
                        patch("common.unbound_steps.verify_resolver") as verify, \
                        patch("lib.headscale_client.native", side_effect=native) as calls, \
                        patch("common.service_tools.headscale_dns.time.monotonic", return_value=0), \
                        patch("common.service_tools.headscale_dns.time.sleep") as sleep:
                    self.assertTrue(self.request()["ok"])
                sleep.assert_called_once_with(.25)
                self.assertEqual(sum(c.args[0][0] == "tailscale" for c in calls.call_args_list), 2)
                self.assertEqual(verify.call_count, int(effective == "published"))

    def test_unconfirmed_map_times_out_and_malformed_response_is_not_retried(self):
        self.p["effective"] = "published"
        for output in ("{}", "[]", '{"Resolvers": [false]}', "invalid"):
            with self.subTest(output=output), patch("os.geteuid", return_value=0), \
                    patch("lib.headscale_client.reject_control_host"), \
                    patch("common.unbound_steps._observe", return_value=self.peer), \
                    patch("common.unbound_steps.verify_resolver") as verify, \
                    patch("lib.headscale_client.native", return_value=output) as native, \
                    patch("common.service_tools.headscale_dns.time.monotonic", side_effect=[0, 11]), \
                    patch("common.service_tools.headscale_dns.time.sleep") as sleep:
                self.assertFalse(self.request()["ok"])
                native.assert_called_once()
                verify.assert_not_called()
                sleep.assert_not_called()

    def test_udp_tcp_and_dnssec_required(self):
        commands = []
        failure = None
        def run(args, **kwargs):
            commands.append(args)
            if "TXT" in args:
                output = '"' + health_identity(self.binding) + '"'
            elif "dnssec-failed.org." in args and "+cdflag" not in args:
                output = ";; status: SERVFAIL\n;; flags: qr rd ra;\n"
            else:
                output = ";; status: NOERROR\n;; flags: qr rd ra ad;\n. 300 IN DNSKEY test\n"
            if failure and failure(args):
                output = "invalid"
            return subprocess.CompletedProcess(args, 0, output, "")
        with patch("common.unbound_steps.run", side_effect=run):
            verify_resolver(self.binding)
            self.assertTrue(any("+tcp" in c and "DNSKEY" in c for c in commands))
            self.assertTrue(any("+notcp" in c and "+ignore" in c for c in commands))
            for failure in (lambda c: "TXT" in c and "+tcp" in c,
                            lambda c: "TXT" in c and "+notcp" in c,
                            lambda c: "DNSKEY" in c,
                            lambda c: "dnssec-failed.org." in c and "+cdflag" not in c,
                            lambda c: "+cdflag" in c):
                with self.assertRaises(RuntimeError):
                    verify_resolver(self.binding)

    def test_node_requires_owned_manifest_and_checks_before_reserving(self):
        self.ownership["node_id"] = self.binding.node_id
        lease = Mock(binding=self.binding)
        reserved = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        payload = json.dumps(dict(action="cas", parameters=dict(binding=self.binding.to_dict(),
                                                               desired=reserved.to_dict()))).encode()
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("lib.headscale_dns_binding.dns_binding_lease", return_value=nullcontext(lease)), \
                patch("common.unbound_steps._manifest", return_value=None) as manifest, \
                patch("common.unbound_steps._observe", return_value=self.binding), \
                patch("common.unbound_steps._stock_fragments"), \
                patch("common.unbound_steps._unit_configuration"), \
                patch("common.service_tools.headscale_dns.os.path.lexists", return_value=False), \
                patch("common.unbound_steps.verify_resolver") as verify:
            self.assertFalse(execute_request(payload, scope=self.scope)["ok"])
            lease.save.assert_not_called()
            manifest.return_value = dict(enabled=True, binding=self.binding.to_dict())
            self.assertTrue(execute_request(payload, scope=self.scope)["ok"])
            verify.assert_called_once_with(self.binding)
            lease.save.assert_called_once_with(reserved)

    def test_recovery_evidence_blocks_observation_and_reservation_but_allows_release(self):
        self.ownership["node_id"] = self.binding.node_id
        reserved = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        lease = Mock(binding=self.binding)
        with tempfile.TemporaryDirectory() as root:
            paths = unbound_steps.UnboundPaths(state_dir=root)
            with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                    patch.object(unbound_steps, "UnboundPaths", return_value=paths), \
                    patch("lib.headscale_dns_binding.dns_binding_lease", return_value=nullcontext(lease)), \
                    patch.object(unbound_steps, "_manifest") as manifest, \
                    patch.object(unbound_steps, "_observe") as observe, \
                    patch.object(unbound_steps, "verify_resolver") as verify:
                for evidence in (paths.operation, paths.backup):
                    # Even an unreadable or dangling recovery path blocks publication.
                    Path(evidence).symlink_to(Path(root, "missing"))
                    for action in ("node", "cas"):
                        parameters = dict(binding=self.binding.to_dict())
                        if action == "cas":
                            parameters["desired"] = reserved.to_dict()
                        self.assertFalse(execute_request(json.dumps(dict(action=action, parameters=parameters)).encode(), scope=self.scope)["ok"])
                    lease.save.assert_not_called()
                    manifest.assert_not_called()
                    observe.assert_not_called()
                    verify.assert_not_called()
                    lease.binding = reserved
                    release = dict(binding=reserved.to_dict(), desired=self.binding.to_dict())
                    self.assertTrue(execute_request(json.dumps(dict(action="cas", parameters=release)).encode(), scope=self.scope)["ok"])
                    lease.save.assert_called_once_with(self.binding)
                    self.assertTrue(Path(evidence).is_symlink())
                    Path(evidence).unlink()
                    lease.reset_mock()
                    lease.binding = self.binding

    def test_scope_blocks_other_network_node_role_and_unscoped_legacy_key(self):
        reserved = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        lease = Mock(binding=reserved)
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("lib.headscale_dns_binding.dns_binding_lease", return_value=nullcontext(lease)) as lock:
            for scope in (None, "invalid", self.scope.upper(),
                          publication_scope("https://old.example.com", self.binding.node_id, "resolver"),
                          publication_scope(self.binding.network_url, "99", "resolver"),
                          publication_scope(self.binding.network_url, self.binding.node_id, "peer")):
                for action in ("inspect", "cas"):
                    parameters = dict(binding=reserved.to_dict())
                    if action == "cas":
                        parameters["desired"] = self.binding.to_dict()
                    with self.subTest(scope=scope, action=action):
                        self.assertFalse(execute_request(json.dumps(dict(action=action, parameters=parameters)).encode(), scope=scope)["ok"])
            lock.assert_not_called()
            lease.save.assert_not_called()

    def test_pending_or_wrong_client_blocks_node_reservation_and_peer_but_not_release(self):
        reserved = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        lease = Mock(binding=self.binding)
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("lib.headscale_dns_binding.dns_binding_lease", return_value=nullcontext(lease)), \
                patch.object(unbound_steps, "_manifest") as manifest, \
                patch.object(unbound_steps, "_observe") as observe, \
                patch.object(unbound_steps, "verify_resolver") as verify:
            for phase in ("prepared", "joining", "leaving", "migrating", "resuming", "disabled", "enrolled"):
                self.ownership = {"phase": phase, "node_id": "99" if phase == "enrolled" else self.binding.node_id}
                for action in ("node", "cas"):
                    p = dict(binding=self.binding.to_dict())
                    if action == "cas":
                        p["desired"] = reserved.to_dict()
                    self.assertFalse(execute_request(json.dumps(dict(action=action, parameters=p)).encode(), scope=self.scope)["ok"])
                self.assertFalse(self.request()["ok"])
            manifest.assert_not_called()
            observe.assert_not_called()
            verify.assert_not_called()
            lease.save.assert_not_called()
            lease.binding = reserved
            release = dict(binding=reserved.to_dict(), desired=self.binding.to_dict())
            with patch("lib.headscale_client.owned", side_effect=ValueError("outage")):
                self.assertTrue(execute_request(json.dumps(dict(action="cas", parameters=release)).encode(), scope=self.scope)["ok"])
            lease.save.assert_called_once_with(self.binding)

    def test_client_lock_precedes_binding_lease_and_covers_guard_release(self):
        active = []
        lease = Mock(binding=self.binding)
        @contextmanager
        def client_lock():
            active.append(True)
            try:
                yield
            finally:
                active.clear()
        @contextmanager
        def binding_lock(path):
            self.assertTrue(active)
            yield lease
            self.assertTrue(active)
        with patch("os.geteuid", return_value=0), patch("lib.headscale_client.reject_control_host"), \
                patch("lib.headscale_client.client_lock", side_effect=client_lock), \
                patch("lib.headscale_dns_binding.dns_binding_lease", side_effect=binding_lock):
            payload = dict(action="cas", parameters=dict(binding=self.binding.to_dict(), desired=self.binding.to_dict()))
            self.assertTrue(execute_request(json.dumps(payload).encode(), scope=self.scope)["ok"])
        self.assertEqual([], active)
