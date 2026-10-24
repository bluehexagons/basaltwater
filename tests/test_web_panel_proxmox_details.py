"""Public details, API bounds and failure contracts without host access."""

from __future__ import annotations

import copy
import html
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from lib import proxmox_panel_details as details


def guest(vmid: int = 100, kind: str = "qemu", **fields: object) -> dict:
    return {"node": "pve1", "type": kind, "vmid": vmid, "status": "running", **fields}


def task(vmid: int = 100, node: str = "pve1", **fields: object) -> dict:
    return {"upid": f"UPID:{node}:00000001:000000001:67800000:qmstart:{vmid}:root@pam:",
            "node": node, "type": "qmstart", "id": str(vmid), "starttime": 1000, **fields}


def snapshot(**fields: object) -> dict:
    return {"tasks": {"available": True, "items": []}, "guest_details": [],
            "health": {"zfs": {"status": "not_configured", "node": "pve1", "pools": []},
                       "ceph": {"status": "not_configured", "state": None, "checks": []}}, **fields}


class ProxmoxDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(details.time, "monotonic", return_value=100.0))
        self.enterContext(patch.object(details.time, "time", return_value=0.0))
        # Any accidental command execution is a test failure. All API work uses
        # the injected query callback; no filesystem fixtures are needed.
        self.enterContext(patch.object(details.subprocess, "run", side_effect=AssertionError("host command")))
        self.query = Mock(return_value=[])

    def collect(self, guests: list[dict] | None = None, storage: list[dict] | None = None,
                **kwargs: object) -> dict:
        return details.collect_details(self.query, "pve1", guests or [], storage or [], deadline=kwargs.get("deadline", 1000.0))

    def responses(self, values: dict) -> None:
        def query(path: str, *, timeout: float) -> object:
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 3)
            response = values.get(path, [])
            if isinstance(response, Exception):
                raise response
            return response
        self.query.side_effect = query

    def test_cluster_tasks_are_public_deduplicated_and_outcomes_are_fixed(self) -> None:
        rows = [task(), task(101, "pve2", status="OK", endtime=1100),
                task(102, status="WARNINGS: 2", endtime=1100),
                task(103, status="secret=do-not-publish", endtime=1100),
                task(104, status="unexpected status", endtime=1100)]
        rows[0].update(user="root@pam", tokenid="private-token", password="private-password")
        rows.extend([copy.deepcopy(rows[0]), task(105, node="../bad"), task(106, node="evil<script>"),
                     task(107, upid="invalid"), task(108, node="pve3", type="mismatch")])
        self.responses({"/cluster/tasks": rows})
        value = self.collect()["tasks"]
        self.assertFalse(value["available"])  # Valid rows survive, with partial coverage.
        self.assertEqual(len(value["items"]), 5)
        self.assertEqual({row["vmid"]: row["status"] for row in value["items"]},
                         {100: "running", 101: "success", 102: "warning", 103: "failed", 104: "unknown"})
        self.assertEqual(next(row for row in value["items"] if row["vmid"] == 101)["node"], "pve2")
        public = json.dumps(value)
        for secret in ("do-not-publish", "private-token", "private-password", '"user"', '"tokenid"'):
            self.assertNotIn(secret, public)
        self.assertEqual(set(value["items"][0]), {"upid", "node", "type", "vmid", "starttime", "endtime", "status"})

    def test_task_limit_prioritizes_running_and_recent_failures(self) -> None:
        rows = [task(vmid, status="OK", endtime=10000 + vmid) for vmid in range(100, 170)]
        rows.extend([task(900), task(901, status="failed secret", endtime=20),
                     task(902, status="WARNINGS: 1", endtime=30)])
        self.responses({"/cluster/tasks": list(reversed(rows))})
        items = self.collect()["tasks"]["items"]
        self.assertEqual(len(items), 50)
        self.assertEqual([row["vmid"] for row in items[:4]], [900, 902, 901, 169])

    def test_task_failure_and_malformed_shapes_are_unavailable(self) -> None:
        for payload in ({"data": []}, None, [None], [{"upid": "broken"}],
                        OSError("secret"), ValueError("secret"),
                        subprocess.TimeoutExpired("secret", 2)):
            with self.subTest(payload=type(payload)):
                self.responses({"/cluster/tasks": payload})
                self.assertEqual(self.collect()["tasks"], {"available": False, "items": []})
        self.responses({"/cluster/tasks": []})
        self.assertEqual(self.collect()["tasks"], {"available": True, "items": []})

    def test_backends_absent_or_only_remote_do_not_probe_local_health(self) -> None:
        storage = [{"node": "pve2", "plugintype": "zfspool"},
                   {"node": "pve2", "plugintype": "rbd"},
                   {"node": "pve1", "plugintype": "zfs"}]
        value = self.collect(storage=storage)
        self.assertEqual(value["health"]["zfs"]["status"], "not_configured")
        self.assertEqual(value["health"]["ceph"]["status"], "not_configured")
        self.query.assert_called_once_with("/cluster/tasks", timeout=3.0)

    def test_qemu_agent_addresses_and_saved_notes_links_only(self) -> None:
        config = {"agent": "enabled=1,type=virtio", "password": "secret", "description":
                  '[Console <&>](https://app.example.test/a?one=1&two=2)\n'
                  '[IPv6](http://[2001:db8::1]:8080/)\n'
                  '[Bad](javascript:alert(1)) [Auth](https://user:secret@app.test/)\n'
                  '![Image](https://image.test/a) [duplicate](https://app.example.test/a?one=1&two=2)'}
        interfaces = {"result": [{"name": "eth0", "hardware-address": "secret", "ip-addresses": [
            {"ip-address": ip} for ip in ["10.0.0.10", "2001:0db8:0:0::1", "10.0.0.10", "127.0.0.1",
                                        "169.254.1.1", "fe80::1", "::", "224.0.0.1", "ff02::1", "::ffff:127.0.0.1"]]}]}
        self.responses({"/nodes/pve1/qemu/100/config": config,
                        "/nodes/pve1/qemu/100/agent/network-get-interfaces": interfaces})
        record = self.collect([guest()])["guest_details"][0]
        self.assertEqual(record["addresses"], ["10.0.0.10", "2001:db8::1"])
        self.assertEqual(record["address_status"], "available")
        self.assertEqual(record["links"], [{"label": "Console <&>", "url": "https://app.example.test/a?one=1&two=2"},
                                           {"label": "IPv6", "url": "http://[2001:db8::1]:8080/"}])
        # Sanitization returns plain text; consumers must escape at the HTML
        # boundary, preserving intentional ampersands in the stored URL.
        self.assertEqual(html.escape(record["links"][0]["label"]), "Console &lt;&amp;&gt;")
        self.assertIn("one=1&amp;two=2", html.escape(record["links"][0]["url"], quote=True))
        self.assertNotIn("secret", json.dumps(record))

    def test_lxc_modern_and_legacy_interfaces(self) -> None:
        for interfaces in ([{"inet": "192.0.2.4/24", "inet6": "2001:db8::4/64"}],
                           [{"ip-addresses": [{"ip-address": "192.0.2.4"}, {"ip-address": "2001:db8::4"}],
                             "inet": "192.0.2.4/24"}]):
            with self.subTest(interfaces=interfaces):
                self.responses({"/nodes/pve1/lxc/100/config": {"description": "[app](https://app.test/)"},
                                "/nodes/pve1/lxc/100/interfaces": interfaces})
                row = self.collect([guest(kind="lxc")])["guest_details"][0]
                self.assertEqual(row["addresses"], ["192.0.2.4", "2001:db8::4"])
                self.assertEqual(row["address_status"], "available")

    def test_agent_disabled_not_running_failure_and_unknown_are_distinct(self) -> None:
        for agent in (None, 0, False, "0", "enabled=0,type=virtio", "type=virtio"):
            with self.subTest(agent=agent):
                self.responses({"/nodes/pve1/qemu/100/config": {"agent": agent}})
                self.query.reset_mock()
                self.assertEqual(self.collect([guest()])["guest_details"][0]["address_status"], "not_enabled")
                self.assertEqual(self.query.call_count, 2)
        for payload in (OSError("secret"), {}, {"result": "bad"}, {"result": [None]}):
            with self.subTest(payload=payload):
                self.responses({"/nodes/pve1/qemu/100/config": {"agent": 1},
                                "/nodes/pve1/qemu/100/agent/network-get-interfaces": payload})
                self.assertEqual(self.collect([guest()])["guest_details"][0]["address_status"], "unavailable")
        self.responses({"/nodes/pve1/qemu/100/config": OSError("secret")})
        self.assertEqual(self.collect([guest(status="stopped")])["guest_details"][0]["address_status"], "not_running")
        self.assertEqual(self.collect([guest(status="unknown")])["guest_details"][0]["address_status"], "unavailable")
        for invalid in ("garbage", "enabled=garbage", "garbage=1", "1,enabled=0", "type=bad", [], {}, 3):
            with self.subTest(invalid=invalid):
                self.responses({"/nodes/pve1/qemu/100/config": {"agent": invalid}})
                self.assertEqual(self.collect([guest()])["guest_details"][0]["address_status"], "unavailable")

    def test_enabled_agent_forms_and_empty_address_list(self) -> None:
        for agent in (1, True, "1", "enabled=1", "1,type=isa", "enabled=1,freeze-fs=0"):
            with self.subTest(agent=agent):
                self.responses({"/nodes/pve1/qemu/100/config": {"agent": agent},
                                "/nodes/pve1/qemu/100/agent/network-get-interfaces": {"result": []}})
                row = self.collect([guest()])["guest_details"][0]
                self.assertEqual(row["address_status"], "available")
                self.assertEqual(row["addresses"], [])

    def test_config_failure_does_not_prevent_lxc_interface_probe(self) -> None:
        self.responses({"/nodes/pve1/lxc/100/config": ValueError("secret config"),
                        "/nodes/pve1/lxc/100/interfaces": []})
        row = self.collect([guest(kind="lxc")])["guest_details"][0]
        self.assertEqual(row["address_status"], "available")
        self.assertEqual(row["links"], [])
        self.assertEqual(row["addresses"], [])

    def test_local_identity_validation_precedes_queries(self) -> None:
        for node in ("../bad", "pve/remote", "pve1.example.test", "pve1\n", "-pve", "pve-", None, []):
            with self.subTest(node=node), self.assertRaises(ValueError):
                details.collect_details(self.query, node, [], [], deadline=1000)
        self.query.assert_not_called()
        guests = [guest(), guest(node="pve2"), guest(vmid=True), guest(vmid=99), guest(vmid=10**10),
                  guest(vmid="100"), guest(kind=[]), guest(), {"node": "../bad", "vmid": 200}]
        self.responses({"/nodes/pve1/qemu/100/config": {}})
        self.assertEqual(len(self.collect(guests)["guest_details"]), 1)
        self.assertTrue(all("pve2" not in call.args[0] and "../" not in call.args[0] for call in self.query.call_args_list))

    def test_deadline_exhaustion_makes_no_calls_and_reports_guests(self) -> None:
        value = self.collect([guest(), guest(101, status="stopped")], deadline=100)
        self.query.assert_not_called()
        self.assertFalse(value["tasks"]["available"])
        self.assertEqual([row["address_status"] for row in value["guest_details"]], ["budget_exhausted", "not_running"])

    def test_timeout_shrinks_and_deadline_stops_further_queries(self) -> None:
        with patch.object(details.time, "monotonic", side_effect=[100, 101.5, 102.1]):
            self.responses({"/nodes/pve1/qemu/100/config": {"agent": 1}})
            row = self.collect([guest()], deadline=102)["guest_details"][0]
        self.assertEqual([call.kwargs["timeout"] for call in self.query.call_args_list], [2, 0.5])
        self.assertEqual(row["address_status"], "budget_exhausted")

    def test_invalid_deadlines_fail_closed(self) -> None:
        for deadline in (float("inf"), float("nan"), "tomorrow", True, None, 10**10000):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                self.collect(deadline=deadline)
        self.query.assert_not_called()

    def test_fixed_operation_cap_is_independent_of_timer(self) -> None:
        self.responses({f"/nodes/pve1/qemu/{vmid}/config": {"agent": 1} for vmid in range(100, 120)})
        with patch.object(details, "MAX_OPERATIONS", 4):
            rows = self.collect([guest(vmid) for vmid in range(100, 120)])["guest_details"]
        self.assertEqual(self.query.call_count, 4)
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["address_status"], "unavailable")  # malformed agent response
        self.assertEqual(rows[1]["address_status"], "budget_exhausted")
        self.assertEqual(rows[-1]["address_status"], "budget_exhausted")

    def test_minute_rotation_includes_every_guest_even_with_one_probe_budget(self) -> None:
        guests = [guest(vmid) for vmid in range(100, 133)]
        firsts = []
        with patch.object(details, "MAX_OPERATIONS", 2):
            for minute in range(len(guests)):
                with patch.object(details.time, "time", return_value=minute * 60):
                    self.query.reset_mock()
                    rows = self.collect(list(reversed(guests)))["guest_details"]
                self.assertEqual(len(rows), 16)
                self.assertEqual(self.query.call_count, 2)
                firsts.append(rows[0]["vmid"])
        self.assertEqual(set(firsts), {row["vmid"] for row in guests})

    def test_zfs_detail_uses_pool_vdev_counters_and_normalizes_scrub(self) -> None:
        self.responses({"/nodes/pve1/disks/zfs": [{"name": "rpool", "health": "ONLINE"}],
                        "/nodes/pve1/disks/zfs/rpool": {"name": "rpool", "state": "DEGRADED",
                            "scan": "scrub repaired 0B in 00:05:00 with 0 errors on Sun Jul 26 00:00:00 2026",
                            "errors": "secret", "action": "secret", "read": 999,
                            "children": [{"name": "rpool", "read": 1, "write": 2, "cksum": 3,
                                          "children": [{"name": "/dev/secret", "read": 8}]}]}})
        value = self.collect(storage=[{"node": "pve1", "plugintype": "zfspool"}])["health"]["zfs"]
        self.assertEqual(value, {"status": "available", "node": "pve1", "pools": [
            {"name": "rpool", "state": "DEGRADED", "scan": "scrub completed", "read": 1, "write": 2, "cksum": 3}]})
        self.assertNotIn("secret", json.dumps(value))

    def test_zfs_partial_failure_retains_known_state_with_unknown_counters(self) -> None:
        self.responses({"/nodes/pve1/disks/zfs": [{"name": "good", "health": "ONLINE"},
                                                               {"name": "bad", "health": "FAULTED"},
                                                               {"name": "../escape", "health": "ONLINE"}],
                        "/nodes/pve1/disks/zfs/good": {"name": "good", "state": "ONLINE", "scan": "none requested"},
                        "/nodes/pve1/disks/zfs/bad": subprocess.TimeoutExpired("private", 3)})
        value = self.collect(storage=[{"node": "pve1", "plugintype": "zfspool"}])["health"]["zfs"]
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(len(value["pools"]), 2)
        self.assertEqual(value["pools"][1]["state"], "FAULTED")
        self.assertIsNone(value["pools"][1]["read"])
        self.assertFalse(any("escape" in call.args[0] for call in self.query.call_args_list))

    def test_health_failures_and_exhausted_budget_are_explicit(self) -> None:
        storage = [{"node": "pve1", "plugintype": kind} for kind in ("zfspool", "rbd")]
        for payload in (None, {}, [None], OSError("secret")):
            with self.subTest(payload=payload):
                self.responses({"/nodes/pve1/disks/zfs": payload})
                value = self.collect(storage=storage)["health"]
                self.assertEqual(value["zfs"]["status"], "unavailable")
                self.assertEqual(value["ceph"]["status"], "unavailable")
        self.query.reset_mock()
        value = self.collect(storage=storage, deadline=100)["health"]
        self.query.assert_not_called()
        self.assertEqual(value["zfs"]["status"], "unavailable")
        self.assertEqual(value["ceph"]["status"], "unavailable")

    def test_zfs_pool_cap_and_scrub_states(self) -> None:
        scans = ["none requested", "scrub in progress since yesterday", "scrub canceled on yesterday",
                 "resilver in progress since yesterday", "resilvered 1G in 00:05:00 with 0 errors",
                 "resilver canceled on yesterday", "private unrecognized message", None]
        values = {"/nodes/pve1/disks/zfs": [{"name": f"pool{i}", "health": "ONLINE"} for i in range(9)]}
        values.update({f"/nodes/pve1/disks/zfs/pool{i}": {"name": f"pool{i}", "state": "ONLINE", "scan": scan}
                       for i, scan in enumerate(scans)})
        self.responses(values)
        value = self.collect(storage=[{"node": "pve1", "plugintype": "zfspool"}])["health"]["zfs"]
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(len(value["pools"]), 8)
        self.assertEqual([row["scan"] for row in value["pools"]], ["none requested", "scrub in progress", "scrub canceled",
                         "resilver in progress", "resilver completed", "resilver canceled", None, None])
        self.assertEqual(self.query.call_count, 10)
        self.assertFalse(any(call.args[0].endswith("pool8") for call in self.query.call_args_list))

    def test_collector_does_not_reuse_previously_successful_details(self) -> None:
        self.responses({"/nodes/pve1/qemu/100/config": {"agent": 1, "description": "[app](https://app.test)"},
                        "/nodes/pve1/qemu/100/agent/network-get-interfaces": {"result": [{"ip-addresses": [{"ip-address": "10.0.0.1"}]}]}})
        self.assertEqual(self.collect([guest()])["guest_details"][0]["addresses"], ["10.0.0.1"])
        self.responses({"/nodes/pve1/qemu/100/config": OSError("private")})
        row = self.collect([guest()])["guest_details"][0]
        self.assertEqual(row["address_status"], "unavailable")
        self.assertEqual(row["addresses"], [])
        self.assertEqual(row["links"], [])

    def test_ceph_copies_only_fixed_state_and_bounded_codes(self) -> None:
        self.responses({"/nodes/pve1/ceph/status": {"health": {"status": "HEALTH_WARN", "checks": {
            "OSD_DOWN": {"summary": {"message": "secret"}, "detail": "secret"},
            "PG_DEGRADED": {"severity": "HEALTH_ERR"}, "secret<script>": {}}}, "keyring": "secret"}})
        value = self.collect(storage=[{"node": "pve1", "plugintype": "cephfs"}])["health"]["ceph"]
        self.assertEqual(value, {"status": "available", "state": "HEALTH_WARN", "checks": ["OSD_DOWN", "PG_DEGRADED"]})
        for payload in (OSError("secret"), {"health": {"status": "garbage"}}, {}, None):
            with self.subTest(payload=payload):
                self.responses({"/nodes/pve1/ceph/status": payload})
                self.assertEqual(self.collect(storage=[{"node": "pve1", "plugintype": "rbd"}])["health"]["ceph"],
                                 {"status": "unavailable", "state": None, "checks": []})


class ProxmoxDetailsSanitizationTest(unittest.TestCase):
    def test_saved_tasks_require_valid_items_and_preserve_partial_records(self) -> None:
        for tasks in ({"available": True}, {"available": True, "items": None},
                      {"available": True, "items": [None]},
                      {"available": True, "items": [{"upid": "broken"}]}):
            with self.subTest(tasks=tasks):
                value = details.sanitize_details(snapshot(tasks=tasks), "pve1", [])
                self.assertEqual(value["tasks"], {"available": False, "items": []})
        source = snapshot(tasks={"available": True, "items": [task(status="failed"), None]})
        value = details.sanitize_details(source, "pve1", [])
        self.assertFalse(value["tasks"]["available"])
        self.assertEqual(len(value["tasks"]["items"]), 1)
        self.assertEqual(details.sanitize_details(value, "pve1", []), value)

    def test_missing_and_unexpected_top_level_inputs_have_fixed_shape(self) -> None:
        for payload in (None, [], "private", 7, {}, {"tasks": [], "health": "private", "guest_details": [None]}):
            with self.subTest(payload=payload):
                value = details.sanitize_details(payload, "pve1", [guest()])
                self.assertEqual(set(value), {"tasks", "guest_details", "health"})
                self.assertEqual(value["tasks"], {"available": False, "items": []})
                self.assertEqual(value["guest_details"], [])
                self.assertEqual(value["health"]["zfs"]["status"], "unavailable")

    def test_cached_guests_require_local_inventory_identity_and_public_fields(self) -> None:
        source = snapshot(guest_details=[
            {**guest(), "address_status": "available", "addresses": ["10.0.0.2", "::1", "bad", "10.0.0.2"],
             "links": [{"label": "app", "url": "https://app.test", "password": "secret"}], "description": "secret"},
            {**guest(), "address_status": "available", "addresses": ["10.0.0.3"]},
            {**guest(101), "address_status": "not_enabled", "addresses": ["10.0.0.4"]},
            guest(102, node="pve2"), guest(103), guest(104, kind=[]), None])
        before = copy.deepcopy(source)
        value = details.sanitize_details(source, "pve1", [guest(), guest(101), guest(102, node="pve2")])
        self.assertEqual(source, before)
        self.assertEqual(len(value["guest_details"]), 2)
        self.assertEqual(value["guest_details"][0]["addresses"], ["10.0.0.2"])
        self.assertEqual(value["guest_details"][1]["addresses"], [])
        self.assertNotIn("secret", json.dumps(value))

    def test_url_rejection_and_html_escaping_boundary(self) -> None:
        urls = ["https://user:password@app.test", "https://user@app.test", "file:///etc/passwd", "javascript:alert(1)",
                "//app.test/", "https://app.test/\n", "https://app.test/%0d%0AHeader", "https://app.test/%00",
                "https://app.test/\x7f", "https://app.test:99999/", "https://app.test:0/", "https://app.test/\\evil",
                'https://app.test/\"onload=', "https:///app.test", "https://bad%40host/", "https://[fe80::1%25eth0]/"]
        for url in urls:
            with self.subTest(url=url):
                value = details.sanitize_details(snapshot(guest_details=[{
                    **guest(), "links": [{"label": "app", "url": url}], "address_status": "available"}]), "pve1", [guest()])
                self.assertEqual(value["guest_details"][0]["links"], [])

    def test_all_public_lists_and_numbers_are_bounded(self) -> None:
        guests = [guest(vmid) for vmid in range(100, 200)]
        source = snapshot(guest_details=[{**row, "address_status": "available",
                           "addresses": [f"10.0.0.{i}" for i in range(1, 100)],
                           "links": [{"label": "app", "url": f"https://app.test/{i}"} for i in range(100)]} for row in guests])
        source["health"] = {"zfs": {"node": "pve1", "status": "available", "pools": [
            {"name": f"pool{i}", "state": "ONLINE", "read": True, "write": -1, "cksum": 2**80, "scan": "private"}
            for i in range(100)]}, "ceph": {"status": "available", "state": "HEALTH_ERR", "checks": [f"CHECK_{i}" for i in range(100)]}}
        source["tasks"] = {"available": True, "items": [task(i, status="success", starttime=True, endtime=2**100) for i in range(100, 200)]}
        value = details.sanitize_details(source, "pve1", guests)
        self.assertEqual(len(value["guest_details"]), 16)
        self.assertEqual(len(value["guest_details"][0]["addresses"]), 32)
        self.assertEqual(len(value["guest_details"][0]["links"]), 8)
        self.assertEqual(len(value["health"]["zfs"]["pools"]), 8)
        self.assertEqual(len(value["health"]["ceph"]["checks"]), 32)
        self.assertEqual(len(value["tasks"]["items"]), 50)
        self.assertIsNone(value["tasks"]["items"][0]["starttime"])
        self.assertIsNone(value["tasks"]["items"][0]["endtime"])
        self.assertEqual([value["health"]["zfs"]["pools"][0][key] for key in ("read", "write", "cksum", "scan")], [None] * 4)

    def test_cached_health_rejects_foreign_node_and_inconsistent_ceph(self) -> None:
        source = snapshot()
        source["health"] = {"zfs": {"node": "pve2", "status": "available", "pools": [{"name": "pool", "state": "ONLINE"}]},
                            "ceph": {"status": "available", "state": [], "checks": ["OSD_DOWN"]}}
        value = details.sanitize_details(source, "pve1", [])
        self.assertEqual(value["health"]["zfs"], {"node": "pve1", "status": "unavailable", "pools": []})
        self.assertEqual(value["health"]["ceph"], {"status": "unavailable", "state": None, "checks": []})

    def test_stopped_inventory_overrides_cached_addresses_and_sanitization_is_stable(self) -> None:
        value = details.sanitize_details(snapshot(guest_details=[{
            **guest(), "address_status": "available", "addresses": ["10.0.0.1"],
            "links": [{"label": "app", "url": "https://app.test"}]}]), "pve1", [guest(status="stopped")])
        self.assertEqual(value["guest_details"][0]["address_status"], "not_running")
        self.assertEqual(value["guest_details"][0]["addresses"], [])
        self.assertEqual(len(value["guest_details"][0]["links"]), 1)
        self.assertEqual(details.sanitize_details(value, "pve1", [guest(status="stopped")]), value)

    def test_unexpected_nested_values_never_escape_or_raise(self) -> None:
        for bad in (None, [], {}, True, 2**100, float("nan"), "x" * 10000):
            with self.subTest(bad=type(bad)):
                source = snapshot(guest_details=[{**guest(), "addresses": [bad], "links": [{"label": bad, "url": bad}],
                                                 "address_status": bad}])
                source["tasks"] = {"available": True, "items": [task(status=bad, starttime=bad, endtime=bad)]}
                source["health"] = {"zfs": {"node": "pve1", "status": bad, "pools": [{"name": bad, "state": bad}]},
                                    "ceph": {"status": bad, "state": bad, "checks": [bad]}}
                value = details.sanitize_details(source, "pve1", [guest()])
                json.dumps(value, allow_nan=False)
                self.assertEqual(value["guest_details"][0]["addresses"], [])
                self.assertEqual(value["guest_details"][0]["links"], [])


if __name__ == "__main__":
    unittest.main()
