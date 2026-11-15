"""No caller-provided health results; real helper performs fixed observations."""

from __future__ import annotations

from dataclasses import replace
from contextlib import nullcontext
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from common.service_tools.headscale_dns import execute_request
from common import unbound_steps
from common.unbound_steps import verify_resolver
from lib.headscale_dns_binding import DNSBinding
from lib.unbound_config import health_identity


class DNSHelperTests(unittest.TestCase):
    def setUp(self):
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",))
        self.peer = replace(self.binding, node_id="8", addresses=("100.64.0.3",))
        self.p = dict(binding=self.binding.to_dict(), peer=self.peer.to_dict(), effective=None)

    def request(self, p=None):
        return execute_request(json.dumps(dict(action="peer", parameters=p or self.p)).encode())

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
                patch("lib.headscale_client.native", side_effect=native):
            self.assertTrue(self.request()["ok"])
            status["FallbackResolvers"] = [dict(Addr="192.0.2.1")]
            self.assertFalse(self.request()["ok"])
            status.pop("FallbackResolvers")
            status["TailscaleDNS"] = False
            self.assertFalse(self.request()["ok"])

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
                patch("common.unbound_steps.verify_resolver") as verify:
            self.assertFalse(execute_request(payload)["ok"])
            lease.save.assert_not_called()
            manifest.return_value = dict(enabled=True, binding=self.binding.to_dict())
            self.assertTrue(execute_request(payload)["ok"])
            verify.assert_called_once_with(self.binding)
            lease.save.assert_called_once_with(reserved)
