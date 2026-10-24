"""Cluster monitoring presentation and strict, command-free filter contracts."""

from __future__ import annotations

import base64
import copy
import hashlib
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from common.service_tools import web_panel_proxmox as ui
from common.service_tools import web_panel_proxmox_views as views
from common.service_tools import web_panel_service as panel
from scripts.panel_scenarios import proxmox_snapshot


class ProxmoxViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = proxmox_snapshot()
        self.manifest = {"host": "pve.example.test", "services": []}
        self.enterContext(patch("subprocess.run", side_effect=AssertionError("Unexpected host command")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("Unexpected host process")))

    def render(self, **query: str) -> str:
        parsed = views.parse_proxmox_query(urlencode({"pve_" + key: value for key, value in query.items()}))
        return ui.render_proxmox(self.snapshot, self.manifest, panel._format_bytes, panel._format_uptime, query=parsed)

    def selected(self, **query: str) -> list[int]:
        parsed = views.parse_proxmox_query(urlencode({"pve_" + key: value for key, value in query.items()}))
        return [row["vmid"] for row in views.filter_guests(self.snapshot["guests"], parsed)]

    def allocations(self, **query: str) -> str:
        return views.render_allocations(self.snapshot, views.ProxmoxQuery(**query),
                                        lambda resource, kind, label: ui._link(ui.native_url(self.manifest), resource, kind, label),
                                        ui._badge, panel._format_bytes)

    def test_search_node_type_state_and_case_sensitive_tags(self) -> None:
        cases = [({"q": "BUILD"}, [100]), ({"q": "00", "node": "pve2"}, [200]),
                 ({"type": "lxc", "state": "running"}, [201, 101]), ({"type": "template"}, [9000]),
                 ({"tag": "production", "type": "qemu"}, [100, 200]), ({"tag": "Production"}, []),
                 ({"state": "unknown"}, [300]), ({"node": "pve3"}, [300]), ({"node": "unreported"}, [])]
        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(self.selected(**query), expected)

    def test_sort_current_metrics_descending_with_missing_last_and_stable_ties(self) -> None:
        for query, expected in (({"sort": "cpu"}, [100, 200, 101, 300, 9000, 201, 102]),
                                ({"sort": "memory"}, [100, 200, 101, 201, 300, 9000, 102]),
                                ({"sort": "vmid"}, [100, 101, 102, 200, 201, 300, 9000])):
            with self.subTest(query=query):
                self.assertEqual(self.selected(**query), expected)
        # A stopped guest's stale CPU counter must not sort ahead of live readings.
        self.snapshot["guests"][2]["cpu"] = 1
        self.assertEqual(self.selected(sort="cpu")[-1], 102)

    def test_strict_query_parser_bounds_all_fields_and_form_round_trip(self) -> None:
        query = views.parse_proxmox_query("pve_q=%3Cscript%3E&pve_node=pve2&pve_type=qemu&pve_state=running&pve_tag=production&pve_sort=memory")
        self.assertEqual(query.q, "<script>")
        form = views.render_filters(self.snapshot, query)
        self.assertIn('value="&lt;script&gt;"', form)
        self.assertIn('action="/#proxmox-heading"', form)
        self.assertIn('href="/#proxmox-heading"', form)
        self.assertIn('value="production" selected', form)
        for raw in ("pve_q=x&pve_q=y", "extra=1", "pve_node=../node", "pve_node=" + "a" * 254,
                    "pve_tag=" + "a" * 65, "pve_q=" + "x" * 129, "pve_q=%0A", "pve_q=%C2%85",
                    "pve_q=%", "pve_q=%FE", "pve_q=" + "x" * 2049, "pve_state=offline", "pve_sort=desc",
                    "pve_q=x&", "pve_q"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                views.parse_proxmox_query(raw)

    def test_allocations_exclude_templates_use_all_guests_and_keep_unknowns_partial(self) -> None:
        allocations = self.allocations(node="pve1")
        self.assertIn("24 vCPUs · 1.50× host", allocations)
        self.assertIn("Running: 16 vCPUs · 1.00× host", allocations)
        self.assertIn("72.0 GiB · 1.12× host", allocations)
        self.assertIn("Running: 40.0 GiB · 0.62× host", allocations)
        filtered = self.render(q="web", type="lxc", state="running", tag="web", node="pve1")
        self.assertIn("Showing 1 of 7 reported", filtered)
        self.assertIn(allocations, filtered)
        remote = self.allocations(node="pve3")
        self.assertIn("Offline · cached readings", remote)
        self.assertIn("8 logical CPUs · 32.0 GiB RAM", remote)
        self.assertIn("Unavailable", remote)
        self.assertNotIn("0 vCPUs · 0.00× host", remote)
        self.snapshot["guests"][0]["maxcpu"] = None
        partial = self.allocations(node="pve1")
        self.assertIn("12 vCPUs known · partial allocation", partial)
        self.assertIn("Running: 4 vCPUs known · partial allocation", partial)

    def test_incomplete_inventory_never_presents_allocations_as_complete(self) -> None:
        self.snapshot["inventory_complete"] = False
        body = self.render()
        self.assertIn("Partial inventory", body)
        self.assertIn("Reported guests", body)
        self.assertIn("24 vCPUs known · partial allocation", body)
        self.assertIn("Showing 7 of 7 reported guests and templates · partial inventory", body)
        self.snapshot.update(inventory=False, guests=[], storage=[])
        body = self.render()
        self.assertIn("Inventory collection failed", body)
        self.assertNotIn("0 vCPUs", body)

    def test_native_links_use_cluster_host_and_actual_storage_node(self) -> None:
        self.manifest["host"] = "2001:db8::5"
        body = self.render()
        self.assertIn('href="https://[2001:db8::5]:8006/"', body)
        self.assertIn("storage%2Fpve1%2Fshared-ceph", body)
        self.assertIn("storage%2Fpve2%2Fshared-ceph", body)
        self.assertIn("qemu%2F200", body)
        self.assertIn("node%2Fpve3", body)
        self.assertNotIn("https://pve2:8006", body)
        # StateProvider slots 3/7/8 select native node/VM/container Task History.
        for kind, resource, index in (("node", "node/pve2", 3), ("qemu", "qemu/200", 7), ("lxc", "lxc/101", 8)):
            with self.subTest(kind=kind):
                tokens = ui._resource_url("https://pve:8006/", resource, kind, tasks=True).split("#v1:")[1].split(":")
                self.assertEqual(tokens[index], "4")

    def test_node_filter_applies_to_capacity_storage_tasks_without_guest_filters(self) -> None:
        body = self.render(node="pve2", q="no matching guest")
        self.assertIn("Showing 0 of 7 reported", body)
        self.assertIn("10 vCPUs · 0.62× host", body)
        self.assertIn("storage%2Fpve2%2Fshared-ceph", body)
        self.assertNotIn("storage%2Fpve1", body)
        self.assertIn("2 cached tasks · 0 running · 1 failed · 1 warnings", body)
        self.assertNotIn("qmigrate</th>", body)
        self.assertIn("selected remote node is not covered", body)
        self.assertIn("HEALTH_WARN", body)

    def test_tasks_show_known_outcomes_times_not_raw_errors_or_upids(self) -> None:
        self.snapshot["tasks"]["items"][0]["exitstatus"] = "private traceback"
        body = self.render()
        self.assertIn("4 cached tasks · 1 running · 1 failed · 1 warnings", body)
        self.assertIn("In progress", body)
        self.assertIn(">Failed</span>", body)
        self.assertIn(">Success</span>", body)
        self.assertIn("qemu%2F200:4:::::4", body)
        self.assertNotIn("private traceback", body)
        self.assertNotIn("synthetic-0", body)
        self.snapshot["tasks"]["items"][1].update(status="unknown", starttime=None, endtime=None)
        self.assertIn(">Unknown</span>", self.render())
        self.snapshot["tasks"]["available"] = False
        body = self.render()
        self.assertIn("Task activity is incomplete", body)
        self.assertIn("qmigrate</th>", body)
        self.assertNotIn("No recent tasks", body)
        self.snapshot["tasks"]["available"] = True
        self.snapshot["tasks"]["items"] = []
        self.assertIn("No recent tasks", self.render())
        self.snapshot["tasks"]["available"] = False
        self.assertIn("Task activity unavailable", self.render())

    def test_addresses_and_saved_links_have_local_coverage_and_selectable_fallback(self) -> None:
        body = self.render()
        self.assertIn("addresses and Notes links not collected here", body)
        self.assertIn("Guest is not running", body)
        self.assertIn('readonly value="2001:db8::100"', body)
        self.assertIn('data-pve-copy hidden', body)
        self.assertIn('href="https://build.example.test/"', body)
        detail = self.snapshot["guest_details"][0]
        detail["links"] = [{"label": "unsafe", "url": value} for value in (
            "javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.test/", "https://@example.test/",
            "https://example.test:99999/", "https://example.test/\npath")]
        detail["addresses"] = []
        for status, label in (("available", "No usable addresses reported"), ("not_enabled", "Guest agent is not enabled"),
                              ("unavailable", "Address reading unavailable"), ("budget_exhausted", "Address reading deferred")):
            detail["address_status"] = status
            body = self.render()
            self.assertIn(label, body)
            self.assertNotIn('>unsafe ↗</a>', body)

    def test_health_distinguishes_warnings_absence_missing_and_partial_readings(self) -> None:
        body = self.render()
        for value in ("DEGRADED", "READ: 2", "CKSUM: 1", "scrub repaired", "HEALTH_WARN", "1 host is down"):
            self.assertIn(value, body)
        for status, expected in (("not_configured", "not configured"), ("unavailable", "health unavailable")):
            self.snapshot["health"] = {"zfs": {"status": status}, "ceph": {"status": status}}
            self.assertIn(expected, self.render())
        self.snapshot["health"] = {"zfs": {"status": "available", "pools": []},
                                   "ceph": {"status": "available", "state": None, "checks": []}}
        body = self.render()
        self.assertIn("health is unknown", body)
        self.assertIn("Ceph health unknown", body)
        self.assertNotIn("HEALTH_OK", body)
        self.snapshot["health"]["zfs"]["pools"] = [{"name": "rpool", "state": "ONLINE", "scan": None,
                                                    "read": None, "write": 0, "cksum": 0}]
        self.assertIn("Partial error counters", self.render())
        self.snapshot["health"]["zfs"]["status"] = "unavailable"
        body = self.render()
        self.assertIn("ZFS health is incomplete", body)
        self.assertIn("rpool</th>", body)
        self.assertIn("Partial error counters", body)

    def test_unavailable_empty_standalone_and_escaped_content(self) -> None:
        original = copy.deepcopy(self.snapshot)
        self.snapshot = {"available": False, "issues": ["Snapshot <unavailable>"]}
        body = self.render()
        self.assertIn("Open Proxmox VE", body)
        self.assertIn("Snapshot &lt;unavailable&gt;", body)
        self.assertNotIn("0 running", body)
        self.snapshot = original
        self.snapshot.update(scope="node", guests=[], storage=[], tasks={"available": True, "items": []})
        body = self.render()
        self.assertIn("Local node inventory", body)
        self.assertIn("No VMs or containers", body)
        self.assertIn("No Proxmox storage pools", body)
        self.snapshot = original
        self.snapshot["guests"] = proxmox_snapshot()["guests"]
        self.snapshot["guests"][0]["name"] = 'build <script> & "guest"'
        self.snapshot["issues"] = ["Issue <script>"]
        self.snapshot["guest_details"][0]["links"][0]["label"] = "<img onerror=alert(1)>"
        self.snapshot["health"]["ceph"]["checks"] = ["<script>private</script>"]
        body = self.render()
        self.assertIn("build &lt;script&gt; &amp; &quot;guest&quot;", body)
        self.assertIn("&lt;img onerror=alert(1)&gt;", body)
        self.assertIn("&lt;script&gt;private&lt;/script&gt;", body)
        self.assertNotIn("<script>private", body)

    def test_static_script_hash_matches_exact_emitted_script(self) -> None:
        body = self.render()
        script = body.split("<script>", 1)[1].split("</script>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertEqual(ui.SCRIPT_CSP, "'sha256-" + digest + "'")
        self.assertIn("field.select()", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertNotIn("onclick=", body)
        self.assertNotIn("eval(", script)


if __name__ == "__main__":
    unittest.main()
