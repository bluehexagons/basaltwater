"""Proxmox collection, privilege boundary and dashboard inventory contracts."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from common import proxmox_web_panel_steps as steps
from common.service_tools import web_panel_proxmox as ui
from common.service_tools import web_panel_service as panel
from lib import proxmox_panel as inventory
from lib.atomic_io import write_json_atomic


def resources() -> list[dict]:
    return [
        {"type": "qemu", "node": "pve1", "vmid": 100, "name": "build <agent>", "status": "running",
         "template": 0, "cpu": .25, "maxcpu": 4, "mem": 2 * 1024**3, "maxmem": 8 * 1024**3,
         "uptime": 90000, "maxdisk": 40 * 1024**3, "tags": "linux;dev;linux;bad/tag",
         "password": "never-publish", "sshkeys": "private"},
        {"type": "lxc", "node": "pve1", "vmid": 101, "name": "web", "status": "stopped", "maxmem": 1024**3},
        {"type": "qemu", "node": "pve1", "vmid": 9000, "template": 1, "status": "stopped"},
        {"type": "qemu", "node": "pve2", "vmid": 200, "name": "other-node", "status": "running"},
        {"type": "storage", "node": "pve1", "storage": "fast", "status": "available",
         "plugintype": "lvmthin", "disk": 95 * 1024**3, "maxdisk": 100 * 1024**3, "shared": 1,
         "path": "/private/backend/path", "secret": "never-publish"},
        {"type": "storage", "node": "pve2", "storage": "fast", "status": "available", "shared": 1},
        {"type": "node", "node": "pve1", "status": "online", "maxcpu": 8,
         "cpu": .3, "mem": 16 * 1024**3, "maxmem": 32 * 1024**3},
        {"type": "node", "node": "pve2", "status": "offline", "maxcpu": 16,
         "cpu": .1, "mem": 8 * 1024**3, "maxmem": 64 * 1024**3},
    ]


def cluster_rows() -> list[dict]:
    return [{"type": "cluster", "name": "lab", "quorate": 1},
            {"type": "node", "name": "pve1", "local": 1, "online": 1},
            {"type": "node", "name": "pve2", "local": 0, "online": 0}]


def snapshot() -> dict:
    with patch.object(inventory, "_query", side_effect=[cluster_rows(), resources(), {"version": "9.1.0", "repoid": "excluded"}]), patch(
        "lib.proxmox_panel_details.collect_details", return_value={},
    ):
        return inventory.collect_snapshot()


class ProxmoxInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = str(self.home / "inventory.json")
        self.details = self.enterContext(patch("lib.proxmox_panel_details.collect_details", return_value={}))

    def save(self, value: dict | None = None) -> dict:
        write_json_atomic(self.path, snapshot() if value is None else value)
        return inventory.load_snapshot(self.path)

    def test_collects_cluster_public_inventory_with_fixed_cached_queries(self) -> None:
        with patch.object(inventory, "_query", side_effect=[cluster_rows(), resources(), {"version": "9.1.0"}]) as query:
            value = inventory.collect_snapshot()
        self.assertEqual([call.args[0] for call in query.call_args_list], ["/cluster/status", "/cluster/resources", "/version"])
        self.assertEqual([row["vmid"] for row in value["guests"]], [100, 101, 200, 9000])
        self.assertEqual(value["scope"], "cluster")
        self.assertTrue(value["inventory_complete"])
        self.assertEqual(value["guests"][0]["tags"], ["dev", "linux"])
        self.assertEqual(value["cluster"]["online"], 1)
        self.assertEqual(value["cluster"]["nodes"], 2)
        self.assertEqual(value["storage"][0]["disk"], 95 * 1024**3)
        self.assertEqual([row["node"] for row in value["storage"]], ["pve1", "pve2"])
        self.assertEqual(value["nodes"][0]["maxmem"], 32 * 1024**3)
        self.assertEqual(value["nodes"][1]["status"], "offline")
        for excluded in ("never-publish", "sshkeys", "/private/backend", "repoid"):
            self.assertNotIn(excluded, json.dumps(value))
        loaded = self.save(value)
        self.assertTrue(loaded["available"])
        self.assertEqual(loaded["guests"], value["guests"])

    def test_partial_failures_are_explicit_and_never_include_command_output(self) -> None:
        failures = (OSError("sensitive path"), ValueError("private payload"), subprocess.TimeoutExpired("pvesh", 8))
        for failure in failures:
            with self.subTest(failure=type(failure)), patch.object(inventory, "_query", side_effect=[
                cluster_rows(), failure, {"version": "9.1.0"},
            ]):
                value = inventory.collect_snapshot()
            self.assertFalse(value["inventory"])
            self.assertEqual(value["guests"], [])
            self.assertEqual(value["version"], "9.1.0")
            self.assertEqual(value["issues"], ["Guest and storage inventory could not be collected."])
        with patch.object(inventory.socket, "gethostname", return_value="pve1.example.test"), patch.object(
            inventory, "_query", side_effect=[ValueError(), resources(), ValueError()],
        ):
            value = inventory.collect_snapshot()
        self.assertEqual(value["node"], "pve1")
        self.assertTrue(value["inventory"])
        self.assertIsNone(value["cluster"])
        self.assertIsNone(value["version"])
        self.assertEqual(len(value["issues"]), 2)

    def test_standalone_and_unknown_quorum_are_distinct(self) -> None:
        standalone = inventory.sanitize_cluster(cluster_rows()[1:2])
        self.assertFalse(standalone["clustered"])
        self.assertIsNone(standalone["quorate"])
        for flag, expected in ((0, False), (1, True), (None, None), ("yes", None)):
            with self.subTest(flag=flag):
                rows = cluster_rows()
                rows[0]["quorate"] = flag
                self.assertIs(inventory.sanitize_cluster(rows)["quorate"], expected)
        for rows in ([], cluster_rows()[0:1], [*cluster_rows(), cluster_rows()[1]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                inventory.sanitize_cluster(rows)

    def test_standalone_inventory_and_failed_resource_metrics_keep_node_membership(self) -> None:
        with patch.object(inventory, "_query", side_effect=[cluster_rows()[1:2], [], {"version": "9.1.0"}]):
            standalone = inventory.collect_snapshot()
        self.assertEqual(standalone["scope"], "node")
        self.assertTrue(standalone["inventory"])
        self.assertEqual(standalone["nodes"][0]["node"], "pve1")
        self.assertIsNone(standalone["nodes"][0]["maxmem"])
        with patch.object(inventory, "_query", side_effect=[cluster_rows(), ValueError(), {"version": "9.1.0"}]):
            failed = inventory.collect_snapshot()
        self.assertFalse(failed["inventory"])
        self.assertFalse(failed["inventory_complete"])
        self.assertEqual([row["status"] for row in failed["nodes"]], ["online", "offline"])

    def test_omitted_records_mark_inventory_partial_and_tags_round_trip(self) -> None:
        invalid = {**resources()[0], "node": "../pve1", "vmid": 300}
        with patch.object(inventory, "_query", side_effect=[cluster_rows(), [*resources(), invalid], {"version": "9.1.0"}]):
            partial = inventory.collect_snapshot()
        self.assertTrue(partial["inventory"])
        self.assertFalse(partial["inventory_complete"])
        self.assertIn("invalid node", partial["issues"][0])
        self.assertEqual(self.save(partial)["guests"][0]["tags"], ["dev", "linux"])
        with patch.object(inventory, "MAX_ROWS", 2):
            limited = self.save()
        self.assertFalse(limited["inventory_complete"])
        self.assertEqual(len(limited["guests"]), 2)
        self.assertTrue(any("limited" in issue for issue in limited["issues"]))

    def test_node_metrics_are_bounded_and_membership_wins_over_cached_status(self) -> None:
        rows = [{"type": "node", "node": "pve1", "status": "online", "cpu": 2, "mem": True},
                {"type": "node", "node": "pve2", "status": "online", "maxcpu": 16},
                {"type": "node", "node": "../bad", "status": "online"}]
        nodes = inventory.sanitize_nodes(rows)
        self.assertEqual(len(nodes), 2)
        self.assertIsNone(nodes[0]["cpu"])
        self.assertIsNone(nodes[0]["mem"])
        with patch.object(inventory, "_query", side_effect=[cluster_rows(), rows, {"version": "9.1.0"}]):
            value = inventory.collect_snapshot()
        self.assertEqual(value["nodes"][1]["status"], "offline")

    def test_incomplete_inventory_cannot_establish_backend_absence(self) -> None:
        absent = {"health": {"zfs": {"status": "not_configured", "node": "pve1", "pools": []},
                             "ceph": {"status": "not_configured", "state": None, "checks": []}}}
        for payload in (ValueError(), [*resources(), {**resources()[0], "node": "../bad"}]):
            with self.subTest(payload=type(payload)), patch.object(
                inventory, "_query", side_effect=[cluster_rows(), payload, {"version": "9.1.0"}],
            ), patch("lib.proxmox_panel_details.collect_details", return_value=absent):
                value = inventory.collect_snapshot()
            self.assertFalse(value["inventory_complete"])
            self.assertTrue(all(backend["status"] == "unavailable" for backend in value["health"].values()))
        partial = {**snapshot(), **absent, "inventory_complete": False}
        self.assertTrue(all(backend["status"] == "unavailable" for backend in self.save(partial)["health"].values()))

    def test_invalid_metrics_remain_unavailable_and_records_are_bounded(self) -> None:
        for invalid in (True, -1, float("inf"), float("nan"), "12", {}, []):
            with self.subTest(invalid=invalid):
                row = {**resources()[0], "mem": invalid, "cpu": invalid, "maxdisk": invalid}
                guests, _, _ = inventory.sanitize_resources([row])
                self.assertIsNone(guests[0]["mem"])
                self.assertIsNone(guests[0]["cpu"])
                self.assertIsNone(guests[0]["maxdisk"])
        row = {**resources()[0], "cpu": 1.1, "maxmem": .5, "mem": 1024.0}
        guests, _, _ = inventory.sanitize_resources([row])
        self.assertIsNone(guests[0]["cpu"])
        self.assertIsNone(guests[0]["maxmem"])
        self.assertEqual(guests[0]["mem"], 1024)
        invalid = [{**resources()[0], "vmid": True}, {**resources()[4], "storage": "../../etc"}]
        with patch.object(inventory, "MAX_ROWS", 2):
            guests, _, issues = inventory.sanitize_resources([*resources(), resources()[0], *invalid])
        self.assertEqual(len(guests), 2)
        self.assertEqual(len(issues), 4)

    def test_missing_corrupt_oversized_and_symlink_snapshots_are_unavailable(self) -> None:
        self.assertFalse(inventory.load_snapshot(self.path)["available"])
        for raw in ('{"schema": 1', '[{}]', '{"schema": 1, "schema": 1}', '{"schema": NaN}'):
            with self.subTest(raw=raw):
                Path(self.path).write_text(raw)
                self.assertFalse(inventory.load_snapshot(self.path)["available"])
        self.save()
        linked = self.home / "link.json"
        linked.symlink_to(self.path)
        self.assertFalse(inventory.load_snapshot(str(linked))["available"])
        with patch.object(inventory, "MAX_BYTES", 10):
            self.assertFalse(inventory.load_snapshot(self.path)["available"])

    def test_invalid_schema_time_and_cluster_are_rejected_and_stale_data_warns(self) -> None:
        value = snapshot()
        for change in ({"schema": 1}, {"schema": True}, {"inventory": "yes"}, {"scope": []},
                       {"inventory_complete": "yes"}, {"nodes": {}}, {"node": "../pve1"}, {"generated_at": 10**100},
                       {"cluster": []}, {"cluster": {**value["cluster"], "online": 3}}):
            with self.subTest(change=change):
                self.assertFalse(self.save({**value, **change})["available"])
        with patch.object(inventory.time, "time", return_value=1000):
            fresh = {**value, "generated_at": 820}
            self.assertEqual(self.save(fresh)["issues"], [])
            stale = self.save({**fresh, "generated_at": 819})
            self.assertTrue(stale["available"])
            self.assertTrue(stale["inventory_complete"])
            self.assertIn("more than three minutes", stale["issues"][0])
            self.assertFalse(self.save({**fresh, "generated_at": 1061})["available"])

    def test_query_bounds_output_and_deadline_and_discards_stderr(self) -> None:
        process = Mock()
        process.wait.return_value = 0

        def start(command, **kwargs):
            self.assertEqual(command, ["/usr/bin/pvesh", "get", "/version", "--output-format", "json"])
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["group"], 0)
            self.assertTrue(kwargs["start_new_session"])
            kwargs["stdout"].write(b'{"version":"9.1.0"}')
            return process

        with patch.object(inventory.subprocess, "Popen", side_effect=start):
            self.assertEqual(inventory._query("/version"), {"version": "9.1.0"})
            process.wait.assert_called_once_with(timeout=8)
            with patch.object(inventory, "MAX_BYTES", 8), self.assertRaises(ValueError):
                inventory._query("/version")
        process.wait.return_value = 1
        with patch.object(inventory.subprocess, "Popen", return_value=process), self.assertRaises(ValueError):
            inventory._query("/version")
        with patch.object(inventory.resource, "setrlimit") as limit:
            inventory._limit_output()
        limit.assert_called_once_with(inventory.resource.RLIMIT_FSIZE, (inventory.MAX_BYTES + 1, inventory.MAX_BYTES + 1))

    def test_query_allowlist_refuses_remote_nodes_mutations_and_arbitrary_endpoints(self) -> None:
        allowed = ["/cluster/status", "/cluster/resources", "/cluster/tasks", "/version",
                   "/nodes/pve1/qemu/100/config", "/nodes/pve1/qemu/100/agent/network-get-interfaces",
                   "/nodes/pve1/lxc/101/interfaces", "/nodes/pve1/lxc/101/config",
                   "/nodes/pve1/disks/zfs", "/nodes/pve1/disks/zfs/rpool", "/nodes/pve1/ceph/status"]
        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(inventory._allowed_query(path, "pve1"))
        rejected = ["/nodes/pve2/qemu/100/config", "/nodes/pve1/qemu/100/status/start",
                    "/nodes/pve1/qemu/99/config", "/nodes/pve1/qemu/１００/config",
                    "/nodes/pve1/qemu/100/agent/exec", "/nodes/pve1/disks/zfs/../../etc",
                    "/nodes/pve1/tasks/anything/log", "/cluster/backup", "--help", None, []]
        with patch.object(inventory.subprocess, "Popen") as run:
            for path in rejected:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    inventory._query(path, local_node="pve1")
            for timeout in (0, -1, True, float("inf"), 9):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    inventory._query("/version", timeout=timeout)
        run.assert_not_called()

    def test_query_timeout_kills_and_reaps_the_probe_process_group(self) -> None:
        process = Mock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired("pvesh", 2), -9]
        with patch.object(inventory.subprocess, "Popen", return_value=process), patch.object(
            inventory.os, "killpg",
        ) as kill, self.assertRaises(subprocess.TimeoutExpired):
            inventory._query("/version", timeout=2)
        kill.assert_called_once_with(12345, inventory.signal.SIGKILL)
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list], [2, 1])

    def test_export_uses_fixed_destination_and_panel_group_permissions(self) -> None:
        with patch.object(inventory, "SNAPSHOT", self.path), patch.object(inventory, "collect_snapshot", return_value=snapshot()):
            inventory.export_snapshot()
        info = os.stat(self.path)
        self.assertEqual(info.st_mode & 0o777, 0o640)
        self.assertEqual(info.st_gid, os.getegid())
        self.assertTrue(inventory.load_snapshot(self.path)["available"])

    def test_oversized_export_preserves_the_last_readable_snapshot(self) -> None:
        self.save()
        previous = Path(self.path).read_bytes()
        with patch.object(inventory, "SNAPSHOT", self.path), patch.object(
            inventory, "collect_snapshot", return_value=snapshot(),
        ), patch.object(inventory, "MAX_BYTES", 100), self.assertRaisesRegex(ValueError, "publication size limit"):
            inventory.export_snapshot()
        self.assertEqual(Path(self.path).read_bytes(), previous)
        self.assertTrue(inventory.load_snapshot(self.path)["available"])

    def test_dashboard_links_and_metrics_are_escaped_and_cluster_scoped(self) -> None:
        loaded = self.save()
        manifest = {"host": "2001:db8::5", "services": []}
        rendered = ui.render_proxmox(loaded, manifest, panel._format_bytes, panel._format_uptime)
        self.assertIn('href="https://[2001:db8::5]:8006/"', rendered)
        self.assertIn("qemu%2F100", rendered)
        self.assertIn("lxc%2F101", rendered)
        self.assertIn("storage%2Fpve1%2Ffast", rendered)
        self.assertIn("build &lt;agent&gt;", rendered)
        self.assertIn("25.0%", rendered)
        self.assertIn("2.0 GiB / 8.0 GiB", rendered)
        self.assertIn("1d 1h", rendered)
        self.assertIn("2 running", rendered)
        self.assertIn("3 guests · 1 template", rendered)
        self.assertIn("Cluster needs attention", rendered)
        self.assertIn('class="metric-value critical">95%', rendered)
        self.assertIn("other-node", rendered)
        self.assertIn("storage%2Fpve2%2Ffast", rendered)
        self.assertNotIn("never-publish", rendered)
        self.assertNotIn("build <agent>", rendered)
        parsed = urllib_fragment(ui._resource_url("https://pve1:8006/", "qemu/100", "qemu"))
        self.assertEqual(parsed[7], "5")
        self.assertEqual(urllib_fragment(ui._resource_url("https://pve1:8006/", "lxc/101", "lxc"))[8], "5")

    def test_empty_failed_and_stale_views_are_distinct(self) -> None:
        value = snapshot()
        for inventory_ok, expected in ((True, "No VMs or containers"), (False, "Inventory collection failed")):
            with self.subTest(inventory_ok=inventory_ok):
                loaded = self.save({**value, "guests": [], "storage": [], "inventory": inventory_ok})
                rendered = ui.render_proxmox(loaded, {"host": "pve1"}, panel._format_bytes, panel._format_uptime)
                self.assertIn(expected, rendered)
                self.assertEqual("No Proxmox storage pools" in rendered, inventory_ok)
        rendered = ui.render_proxmox({"available": False, "issues": ["Unavailable <snapshot>"]}, {"host": "bad/host"}, panel._format_bytes, panel._format_uptime)
        self.assertIn("Unavailable &lt;snapshot&gt;", rendered)
        self.assertNotIn('href=', rendered)
        self.assertNotIn("0 running", rendered)


def urllib_fragment(url: str) -> list[str]:
    return url.split("#v1:")[1].split(":")


class ProxmoxSnapshotSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.home / "proxmox"
        self.path = self.directory / "inventory.json"
        self.enterContext(patch.object(steps, "SNAPSHOT_DIR", str(self.directory)))
        self.enterContext(patch.object(steps, "SNAPSHOT", str(self.path)))
        self.units = self.enterContext(patch.object(steps, "snapshot_unit_file", return_value=None))
        self.replace = self.enterContext(patch.object(steps, "replace_units"))
        self.remove = self.enterContext(patch.object(steps, "remove_units"))
        self.run = self.enterContext(patch.object(steps, "run"))

    def test_snapshot_install_keeps_privilege_in_fixed_oneshot_and_activates_timer(self) -> None:
        with patch.object(steps.os, "fchown") as chown:
            steps.configure_proxmox_snapshot(991)
        self.assertEqual(chown.call_args.args[1:], (0, 991))
        self.assertEqual(self.directory.stat().st_mode & 0o7777, 0o2750)
        units = self.replace.call_args.args[0]
        self.assertEqual(self.replace.call_args.kwargs["activate"], (steps.SNAPSHOT_TIMER,))
        self.assertEqual(self.replace.call_args.kwargs["deactivate"], (steps.SNAPSHOT_TIMER,))
        self.assertEqual(self.replace.call_args.kwargs["start_once"], (steps.SNAPSHOT_SERVICE,))
        service = units[steps.SNAPSHOT_SERVICE]
        self.assertIn("User=root\nGroup=991", service)
        self.assertIn("--export\n", service)
        self.assertIn("TimeoutStartSec=60", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ProtectHome=true", service)
        self.assertIn("PrivateDevices=true\nBindReadOnlyPaths=-/dev/zfs\nDeviceAllow=/dev/zfs rw", service)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", service)
        self.assertNotIn("sudo", service)
        self.assertIn("OnUnitInactiveSec=60s", units[steps.SNAPSHOT_TIMER])
        self.run.assert_not_called()

    def test_unmanaged_units_and_symlink_storage_fail_before_mutations(self) -> None:
        self.units.return_value = {"content": "[Service]\n# operator"}
        for action in (lambda: steps.configure_proxmox_snapshot(991), steps.remove_proxmox_snapshot):
            with self.assertRaisesRegex(ValueError, "unmanaged"):
                action()
        self.units.return_value = None
        self.directory.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            steps.configure_proxmox_snapshot(991)
        self.replace.assert_not_called()
        self.remove.assert_not_called()
        self.run.assert_not_called()

    def test_removal_stops_units_before_deleting_snapshot_and_is_repeatable(self) -> None:
        self.directory.mkdir()
        self.path.write_text("{}")
        self.units.return_value = {"content": steps._MARKER + "\n"}
        self.remove.side_effect = lambda *_: self.assertTrue(self.path.exists())
        steps.remove_proxmox_snapshot()
        self.remove.assert_called_once_with((steps.SNAPSHOT_TIMER, steps.SNAPSHOT_SERVICE))
        self.assertFalse(self.directory.exists())
        self.units.return_value = None
        steps.remove_proxmox_snapshot()
        self.assertEqual(self.remove.call_count, 1)

    def test_removal_preserves_leftover_files_without_blocking_teardown(self) -> None:
        self.directory.mkdir()
        leftovers = (self.directory / ".inventory.json-interrupted", self.directory / "operator-note")
        for leftover in leftovers:
            leftover.write_text("retain")
        self.path.write_text("{}")
        self.units.return_value = {"content": steps._MARKER + "\n"}
        steps.remove_proxmox_snapshot()
        self.remove.assert_called_once_with((steps.SNAPSHOT_TIMER, steps.SNAPSHOT_SERVICE))
        self.assertFalse(self.path.exists())
        self.units.return_value = None
        steps.remove_proxmox_snapshot()
        self.assertEqual(self.remove.call_count, 1)
        self.assertEqual([leftover.read_text() for leftover in leftovers], ["retain", "retain"])

    def test_removal_does_not_suppress_unrelated_directory_errors(self) -> None:
        self.directory.mkdir()
        with patch.object(steps.os, "rmdir", side_effect=PermissionError(errno.EACCES, "denied")):
            with self.assertRaises(PermissionError):
                steps.remove_proxmox_snapshot()


if __name__ == "__main__":
    unittest.main()
