"""Proxmox collection, privilege boundary and dashboard inventory contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import proxmox_web_panel_steps as steps
from common.service_tools import web_panel_proxmox as ui
from common.service_tools import web_panel_service as panel
from lib import proxmox_panel as inventory
from lib.atomic_io import write_json_atomic


def resources() -> list[dict]:
    return [
        {"type": "qemu", "node": "pve1", "vmid": 100, "name": "build <agent>", "status": "running",
         "template": 0, "cpu": .25, "maxcpu": 4, "mem": 2 * 1024**3, "maxmem": 8 * 1024**3,
         "uptime": 90000, "maxdisk": 40 * 1024**3, "password": "never-publish", "sshkeys": "private"},
        {"type": "lxc", "node": "pve1", "vmid": 101, "name": "web", "status": "stopped", "maxmem": 1024**3},
        {"type": "qemu", "node": "pve1", "vmid": 9000, "template": 1, "status": "stopped"},
        {"type": "qemu", "node": "pve2", "vmid": 200, "name": "other-node", "status": "running"},
        {"type": "storage", "node": "pve1", "storage": "fast", "status": "available",
         "plugintype": "lvmthin", "disk": 95 * 1024**3, "maxdisk": 100 * 1024**3, "shared": 1,
         "path": "/private/backend/path", "secret": "never-publish"},
    ]


def cluster_rows() -> list[dict]:
    return [{"type": "cluster", "name": "lab", "quorate": 1},
            {"type": "node", "name": "pve1", "local": 1, "online": 1},
            {"type": "node", "name": "pve2", "local": 0, "online": 0}]


def snapshot() -> dict:
    with patch.object(inventory, "_query", side_effect=[cluster_rows(), resources(), {"version": "9.1.0", "repoid": "excluded"}]):
        return inventory.collect_snapshot()


class ProxmoxInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = str(self.home / "inventory.json")

    def save(self, value: dict | None = None) -> dict:
        write_json_atomic(self.path, snapshot() if value is None else value)
        return inventory.load_snapshot(self.path)

    def test_collects_only_local_public_inventory_with_fixed_queries(self) -> None:
        with patch.object(inventory, "_query", side_effect=[cluster_rows(), resources(), {"version": "9.1.0"}]) as query:
            value = inventory.collect_snapshot()
        self.assertEqual([call.args[0] for call in query.call_args_list], ["/cluster/status", "/cluster/resources", "/version"])
        self.assertEqual([row["vmid"] for row in value["guests"]], [100, 101, 9000])
        self.assertEqual(value["cluster"]["online"], 1)
        self.assertEqual(value["cluster"]["nodes"], 2)
        self.assertEqual(value["storage"][0]["disk"], 95 * 1024**3)
        for excluded in ("never-publish", "sshkeys", "/private/backend", "other-node", "repoid"):
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

    def test_invalid_metrics_remain_unavailable_and_records_are_bounded(self) -> None:
        for invalid in (True, -1, float("inf"), float("nan"), "12", {}, []):
            with self.subTest(invalid=invalid):
                row = {**resources()[0], "mem": invalid, "cpu": invalid, "maxdisk": invalid}
                guests, _, _ = inventory.sanitize_resources([row], "pve1")
                self.assertIsNone(guests[0]["mem"])
                self.assertIsNone(guests[0]["cpu"])
                self.assertIsNone(guests[0]["maxdisk"])
        row = {**resources()[0], "cpu": 1.1, "maxmem": .5, "mem": 1024.0}
        guests, _, _ = inventory.sanitize_resources([row], "pve1")
        self.assertIsNone(guests[0]["cpu"])
        self.assertIsNone(guests[0]["maxmem"])
        self.assertEqual(guests[0]["mem"], 1024)
        invalid = [{**resources()[0], "vmid": True}, {**resources()[-1], "storage": "../../etc"}]
        with patch.object(inventory, "MAX_ROWS", 2):
            guests, _, issues = inventory.sanitize_resources([*resources(), resources()[0], *invalid], "pve1")
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
        for change in ({"schema": 2}, {"inventory": "yes"}, {"node": "../pve1"}, {"generated_at": 10**100},
                       {"cluster": []}, {"cluster": {**value["cluster"], "online": 3}}):
            with self.subTest(change=change):
                self.assertFalse(self.save({**value, **change})["available"])
        with patch.object(inventory.time, "time", return_value=1000):
            fresh = {**value, "generated_at": 820}
            self.assertEqual(self.save(fresh)["issues"], [])
            stale = self.save({**fresh, "generated_at": 819})
            self.assertTrue(stale["available"])
            self.assertIn("more than three minutes", stale["issues"][0])
            self.assertFalse(self.save({**fresh, "generated_at": 1061})["available"])

    def test_query_bounds_output_and_deadline_and_discards_stderr(self) -> None:
        def run(command, **kwargs):
            self.assertEqual(command, ["/usr/bin/pvesh", "get", "/version", "--output-format", "json"])
            self.assertEqual(kwargs["timeout"], 8)
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
            kwargs["stdout"].write(b'{"version":"9.1.0"}')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(inventory.subprocess, "run", side_effect=run):
            self.assertEqual(inventory._query("/version"), {"version": "9.1.0"})
            with patch.object(inventory, "MAX_BYTES", 8), self.assertRaises(ValueError):
                inventory._query("/version")
        with patch.object(inventory.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), self.assertRaises(ValueError):
            inventory._query("/version")
        with patch.object(inventory.resource, "setrlimit") as limit:
            inventory._limit_output()
        limit.assert_called_once_with(inventory.resource.RLIMIT_FSIZE, (inventory.MAX_BYTES + 1, inventory.MAX_BYTES + 1))

    def test_export_uses_fixed_destination_and_panel_group_permissions(self) -> None:
        with patch.object(inventory, "SNAPSHOT", self.path), patch.object(inventory, "collect_snapshot", return_value=snapshot()):
            inventory.export_snapshot()
        info = os.stat(self.path)
        self.assertEqual(info.st_mode & 0o777, 0o640)
        self.assertEqual(info.st_gid, os.getegid())
        self.assertTrue(inventory.load_snapshot(self.path)["available"])

    def test_dashboard_links_and_metrics_are_escaped_and_local(self) -> None:
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
        self.assertIn("1 running", rendered)
        self.assertIn("2 guests · 1 template", rendered)
        self.assertIn("Cluster needs attention", rendered)
        self.assertIn('class="metric-value critical">95%', rendered)
        self.assertNotIn("other-node", rendered)
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
        service = units[steps.SNAPSHOT_SERVICE]
        self.assertIn("User=root\nGroup=991", service)
        self.assertIn("--export\n", service)
        self.assertIn("TimeoutStartSec=30", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("sudo", service)
        self.assertIn("OnUnitInactiveSec=60s", units[steps.SNAPSHOT_TIMER])
        self.run.assert_called_once_with(["systemctl", "start", steps.SNAPSHOT_SERVICE], check=True, timeout=40)

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


if __name__ == "__main__":
    unittest.main()
