"""Migration workflow and failure recovery with temporary state and mocked SSH."""

from __future__ import annotations

import copy
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lib.atomic_io import write_json_atomic
from lib.cache import get_cache_path_for_host, load_setup_command, save_setup_command
from lib.config import SetupConfig
from lib.proxmox_hosts import ProxmoxHost, add_proxmox_host
from lib.proxmox_migrate import ProxmoxMigrateError, _run, migrate_guest
from lib.proxmox_migration_setups import provider_size_bytes
from lib.setup_common import _apply_hosted_proxmox_defaults


GIB = 1024 ** 3


class TestMigrateGuest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        env = patch.dict(os.environ, {"BASALTWATER_WORKSPACE": self.workspace})
        env.start()
        self.addCleanup(env.stop)
        self.source = ProxmoxHost("pve1", "10.0.0.10")
        self.target = ProxmoxHost("pve2", "10.0.0.11", ssh_key=str(Path(self.workspace) / "destination-key"))
        self.node = "pve1"
        self.state = "running"
        self.kind = "qemu"
        self.name = "agent-one"
        self.ca = {"pve1": "a" * 64, "pve2": "a" * 64}
        self.quorate = 1
        self.tasks = []
        self.pending = []
        self.snapshots = [{"name": "current"}]
        self.bridges = [{"ifname": "vmbr0"}]
        self.extra_guests = []
        self.other_configs = {}
        self.hastate = None
        self.failure = None
        self.ignore_shutdown = False
        self.before_migration = None
        self.after_transfer = None
        self.efi_size = 528 * 1024
        self.config = {
            "name": self.name, "memory": 4096, "cpu": "host", "onboot": 1, "boot": "order=scsi0",
            "net0": "virtio=02:00:00:00:00:01,bridge=vmbr0",
            "ipconfig0": "ip=10.0.0.50/24,gw=10.0.0.1",
            "scsi0": "hdd:vm-100-disk-0,size=8G,discard=on,ssd=0",
            "scsi1": "hdd:vm-100-disk-1,size=16G,serial=bw-data,backup=1",
            "ide2": "hdd:vm-100-cloudinit,size=4M,media=cdrom",
            "sshkeys": "SENSITIVE GUEST KEY MATERIAL", "cipassword": "SENSITIVE PASSWORD",
        }
        self.destination_config = None
        self.destination_volumes = []
        self.source_pools = [self._pool("hdd")]
        self.target_pools = [self._pool("local")]
        self.volumes = [
            {"volid": "hdd:vm-100-disk-0", "size": 8 * GIB},
            {"volid": "hdd:vm-100-disk-1", "size": 16 * GIB},
            {"volid": "hdd:vm-100-cloudinit", "size": 4 * 1024 ** 2},
        ]
        self.actions = []
        transport = patch("lib.proxmox_migrate._run", side_effect=self._fake_run)
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        leases = patch("lib.proxmox_migrate.remote_proxmox_locks", side_effect=lambda *_args, **_kwargs: nullcontext())
        self.leases = leases.start()
        self.addCleanup(leases.stop)

    @staticmethod
    def _pool(name: str, *, shared: bool = False) -> dict:
        return {"storage": name, "content": "images,rootdir,iso", "active": 1,
                "enabled": 1, "shared": int(shared), "avail": 64 * GIB}

    def _fake_run(self, host, command, workspace, **options):
        self.assertEqual(workspace, self.workspace)
        tokens = shlex.split(command)
        if command == "sha256sum /etc/pve/pve-root-ca.pem":
            return self.ca[host.name] + "  /etc/pve/pve-root-ca.pem\n"
        if command == "ip -j link show type bridge":
            return json.dumps(self.bridges)
        if tokens[0] == "perl":
            self.assertIn("PVE::QemuServer::get_efivars_size($conf)", tokens[-1])
            return str(self.efi_size)
        if command.startswith("test ! -e /etc/pve/local/"):
            if self.node != "pve2" or self.failure == "source_runtime":
                raise ProxmoxMigrateError("source still has a guest configuration or running process")
            return ""
        if tokens[:2] == ["pvesh", "get"]:
            path = tokens[2]
            if path == "/cluster/status":
                return json.dumps([
                    {"type": "cluster", "name": "test", "quorate": self.quorate},
                    {"type": "node", "name": "pve1", "online": 1, "local": int(host.name == "pve1"), "ip": self.source.address},
                    {"type": "node", "name": "pve2", "online": 1, "local": int(host.name == "pve2"), "ip": self.target.address},
                ])
            if path == "/cluster/resources":
                return json.dumps([{"vmid": 100, "name": self.name, "node": self.node,
                                    "type": self.kind, "hastate": self.hastate}, *self.extra_guests])
            if path.endswith("/tasks"):
                return json.dumps(self.tasks)
            if path.endswith("/pending"):
                return json.dumps(self.pending)
            if path.endswith("/snapshot"):
                return json.dumps(self.snapshots)
            if path.endswith("/config"):
                self.assertIn("--current", tokens)
                if path.split("/")[-2] != "100":
                    return json.dumps(self.other_configs[int(path.split("/")[-2])])
                return json.dumps(self.config if host.name == "pve1" else self.destination_config)
            if path.endswith("/status/current"):
                return json.dumps({"status": self.state})
            if path.endswith("/storage"):
                return json.dumps(self.source_pools if host.name == "pve1" else self.target_pools)
            if path.endswith("/content"):
                volumes = self.volumes if host.name == "pve1" else self.destination_volumes
                return json.dumps([row for row in volumes if row["volid"].startswith(path.split("/")[-2] + ":")])
            self.fail(f"Unexpected API request {command}")
        self.assertTrue(options.get("mutation"), command)
        self.actions.append((host.name, command))
        if tokens[1] == "shutdown":
            if self.failure == "shutdown":
                raise ProxmoxMigrateError("shutdown failed")
            if not self.ignore_shutdown:
                self.state = "stopped"
        elif tokens[1] == "migrate":
            if self.before_migration:
                self.before_migration()
            if self.failure == "migration":
                raise ProxmoxMigrateError("native migration failed")
            self.destination_config = copy.deepcopy(self.config)
            for key, value in self.destination_config.items():
                if isinstance(value, str) and value.startswith("hdd:"):
                    self.destination_config[key] = value.replace("hdd:", "local:", 1)
            self.node = "pve2"
            self.destination_volumes = [
                {**volume, "volid": volume["volid"].replace("hdd:", "local:", 1)}
                for volume in self.volumes
            ]
            if self.kind == "qemu":
                for device in ("scsi0", "scsi1", "ide2", "efidisk0", "tpmstate0"):
                    if device not in self.destination_config:
                        continue
                    volume, *properties = self.destination_config[device].split(",")
                    row = next(row for row in self.destination_volumes if row["volid"] == volume)
                    if volume.startswith("shared:") and device not in {"efidisk0", "tpmstate0"}:
                        continue
                    size = self.efi_size if device == "efidisk0" else 4 * 1024 ** 2 if device == "tpmstate0" else row["size"]
                    properties = [option for option in properties if not option.startswith("size=")]
                    self.destination_config[device] = ",".join([volume, *properties, f"size={size}"])
                    if device == "efidisk0":
                        row["size"] = size
            if self.after_transfer:
                self.after_transfer()
            if self.failure == "missing_destination_volume":
                self.destination_volumes.pop(0)
            if self.failure == "undersized_destination_volume":
                self.destination_volumes[0]["size"] = GIB
            if self.failure == "lost_result":
                raise OSError("SSH connection lost after transfer")
            if self.failure == "wrong_disk":
                self.destination_config["scsi1"] = self.destination_config["scsi1"].replace("bw-data", "bw-other")
            if self.failure == "wrong_pool":
                self.destination_config["scsi0"] = self.destination_config["scsi0"].replace("local:", "hdd:")
            if self.failure == "wrong_identity":
                self.destination_config["net0"] = "virtio=02:00:00:00:00:99,bridge=vmbr0"
            if self.failure == "source_not_moved":
                self.node = "pve1"
        elif tokens[1] == "start":
            if self.failure == "start":
                raise ProxmoxMigrateError("start failed")
            self.state = "running"
        else:
            self.fail(f"Unexpected mutation {command}")
        return ""

    def _migrate(self, **options):
        output = io.StringIO()
        with redirect_stdout(output):
            migrate_guest(self.source, options.pop("guest", 100), self.target,
                          target_storage="local", workspace=self.workspace, **options)
        return output.getvalue()

    def _save_setup(self):
        save_setup_command(SetupConfig(
            host="10.0.0.50", username="agent", system_type="workstation_dev", machine_type="vm",
            friendly_name=self.name, hosted_node=self.source.address, hosted_key="/tmp/source-key",
            hosted_bridge="vmbr0", container_storage=[["root", "hdd", "8G"], ["data", "hdd", "16G"]],
            storage_mounts=[["data", "/srv/data", "ext4", "empty"]],
            timezone="UTC", tags=["test"],
        ), start_time=1, end_time=2, success=True)
        return Path(get_cache_path_for_host("10.0.0.50"))

    def _marker(self):
        return Path(self.workspace) / "migrations" / ("a" * 24 + "-100.json")

    def test_offline_move_shuts_down_verifies_updates_and_starts(self):
        path = self._save_setup()
        original = json.loads(path.read_text())
        history = {str(p): p.read_bytes() for p in (Path(self.workspace) / "history").glob("*.json")}
        output = self._migrate(guest=self.name, timeout=14400, shutdown_timeout=240)
        self.assertEqual(self.actions, [("pve1", "qm shutdown 100 --timeout 240"),
                                      ("pve1", "qm migrate 100 pve2 --targetstorage local"),
                                      ("pve2", "qm start 100")])
        self.assertEqual(self.state, "running")
        self.assertEqual(self.node, "pve2")
        saved = load_setup_command("10.0.0.50")
        self.assertEqual(saved.hosted_node, self.target.address)
        self.assertEqual(saved.hosted_key, self.target.ssh_key)
        self.assertEqual(saved.hosted_bridge, "vmbr0")
        self.assertEqual(saved.container_storage, [["root", "local", "8G"], ["data", "local", "16G"]])
        self.assertEqual(saved.storage_mounts, [["data", "/srv/data", "ext4", "empty"]])
        self.assertFalse(saved.vm_disk_ssd)
        current = json.loads(path.read_text())
        self.assertEqual({key: value for key, value in current.items() if key != "args"},
                         {key: value for key, value in original.items() if key != "args"})
        self.assertEqual(history, {str(p): p.read_bytes() for p in (Path(self.workspace) / "history").glob("*.json")})
        self.assertIn("Verified destination", output)
        self.assertFalse(self._marker().exists())
        result = Path(str(self._marker()) + ".last.json")
        self.assertTrue(result.exists())
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("SENSITIVE", result.read_text())
        self.assertEqual(self.leases.call_count, 2)
        mutations = [call for call in self.transport.call_args_list if call.kwargs.get("mutation")]
        self.assertEqual([call.kwargs["timeout"] for call in mutations], [270, 14400, 14400])

    def test_dry_run_inspects_without_shutdown_state_files_or_cache_writes(self):
        self._save_setup()
        before = {str(p): p.read_bytes() for p in Path(self.workspace).rglob("*") if p.is_file()}
        output = self._migrate(dry_run=True)
        self.assertIn("Graceful shutdown", output)
        self.assertIn("--targetstorage local", output)
        self.assertEqual(self.actions, [])
        self.assertEqual(before, {str(p): p.read_bytes() for p in Path(self.workspace).rglob("*") if p.is_file()})
        self.leases.assert_not_called()

    def test_stopped_guest_remains_stopped(self):
        self.state = "stopped"
        self._migrate()
        self.assertEqual(self.actions, [("pve1", "qm migrate 100 pve2 --targetstorage local")])
        self.assertEqual(self.state, "stopped")

    def test_saved_destination_default_ssh_auth_survives_later_setup_preparation(self):
        self.target.ssh_key = None
        add_proxmox_host(self.target, self.workspace)
        path = self._save_setup()
        data = json.loads(path.read_text())
        data["args"]["ssh_key"] = "/tmp/separate-guest-key"
        write_json_atomic(str(path), data)
        self._migrate()
        saved = load_setup_command("10.0.0.50")
        _apply_hosted_proxmox_defaults(saved, self.workspace)
        self.assertIsNone(saved.hosted_key)
        self.assertEqual(saved.ssh_key, "/tmp/separate-guest-key")

    def test_live_migration_preserves_running_state_without_shutdown(self):
        self._migrate(online=True, with_local_disks=True)
        self.assertEqual(self.actions, [("pve1", "qm migrate 100 pve2 --online 1 --with-local-disks 1 --targetstorage local")])

    def test_live_local_disks_require_explicit_flag_before_shutdown(self):
        with self.assertRaisesRegex(ProxmoxMigrateError, "requires --with-local-disks"):
            self._migrate(online=True)
        self.assertEqual(self.actions, [])

    def test_shutdown_failure_does_not_migrate_and_can_resume_shutdown(self):
        self.failure = "shutdown"
        with self.assertRaisesRegex(ProxmoxMigrateError, "shutdown failed"):
            self._migrate()
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(json.loads(self._marker().read_text())["phase"], "shutting_down")
        self.failure = None
        self._migrate(resume=True)
        self.assertEqual(self.node, "pve2")

    def test_shutdown_success_without_stopped_state_does_not_migrate(self):
        self.ignore_shutdown = True
        with self.assertRaisesRegex(ProxmoxMigrateError, "did not stop"):
            self._migrate()
        self.assertEqual(len(self.actions), 1)

    def test_failed_migration_blocks_normal_retry_and_resume_does_not_replay(self):
        self.failure = "migration"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        with self.assertRaisesRegex(ProxmoxMigrateError, "Unfinished migration"):
            self._migrate()
        self.failure = None
        with self.assertRaisesRegex(ProxmoxMigrateError, "will not replay"):
            self._migrate(resume=True)
        self.assertEqual(len(self.actions), 2)
        self.assertEqual(self.state, "stopped")

    def test_lost_result_after_transfer_resumes_verification_without_copying_again(self):
        path = self._save_setup()
        self.failure = "lost_result"
        with self.assertRaisesRegex(ProxmoxMigrateError, "SSH connection lost"):
            self._migrate()
        self.assertEqual(json.loads(path.read_text())["args"]["hosted_node"], self.source.address)
        self.failure = None
        self._migrate(resume=True)
        self.assertEqual(len([action for _, action in self.actions if " migrate " in action]), 1)
        self.assertEqual(self.state, "running")
        self.assertEqual(load_setup_command("10.0.0.50").hosted_node, self.target.address)

    def test_resume_dry_run_verifies_completed_transfer_and_only_previews_remaining_work(self):
        self._save_setup()
        self.failure = "lost_result"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        self.failure = None
        before = {str(p): p.read_bytes() for p in Path(self.workspace).rglob("*") if p.is_file()}
        actions = list(self.actions)
        output = self._migrate(resume=True, dry_run=True)
        self.assertIn("transfer already complete", output)
        self.assertIn("update saved setups → start", output)
        self.assertNotIn("qm migrate", output)
        self.assertEqual(actions, self.actions)
        self.assertEqual(before, {str(p): p.read_bytes() for p in Path(self.workspace).rglob("*") if p.is_file()})

    def test_resume_dry_run_rejects_missing_destination_data(self):
        self.failure = "lost_result"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        self.failure = None
        self.destination_volumes.pop(0)
        with self.assertRaisesRegex(ProxmoxMigrateError, "missing or smaller"):
            self._migrate(resume=True, dry_run=True)
        self.assertEqual(self.state, "stopped")

    def test_resume_dry_run_does_not_promise_replaying_an_uncertain_source_transfer(self):
        self.failure = "migration"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        with self.assertRaisesRegex(ProxmoxMigrateError, "will not replay"):
            self._migrate(resume=True, dry_run=True)
        self.assertEqual(len(self.actions), 2)

    def test_start_failure_keeps_destination_binding_and_can_resume(self):
        self._save_setup()
        self.failure = "start"
        with self.assertRaisesRegex(ProxmoxMigrateError, "start failed"):
            self._migrate()
        self.assertEqual(load_setup_command("10.0.0.50").hosted_node, self.target.address)
        self.assertEqual(self.state, "stopped")
        self.failure = None
        self._migrate(resume=True)
        self.assertEqual(self.state, "running")
        self.assertEqual(len([action for _, action in self.actions if " migrate " in action]), 1)

    def test_wrong_destination_configuration_does_not_start_or_update(self):
        path = self._save_setup()
        original = path.read_bytes()
        for failure in ("wrong_disk", "wrong_pool", "wrong_identity", "source_not_moved", "source_runtime",
                        "missing_destination_volume", "undersized_destination_volume"):
            with self.subTest(failure=failure):
                self.failure = failure
                with self.assertRaises(ProxmoxMigrateError):
                    self._migrate()
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(any(" start " in action for _, action in self.actions))
                self._marker().unlink()
                self.node, self.state = "pve1", "running"
                self.actions = []

    def test_concurrent_unrelated_cache_changes_are_preserved(self):
        path = self._save_setup()
        def change():
            data = json.loads(path.read_text())
            data["args"]["timezone"] = "America/Chicago"
            data["last_success"] = False
            write_json_atomic(str(path), data)
        self.before_migration = change
        self._migrate()
        self.assertEqual(load_setup_command("10.0.0.50").timezone, "America/Chicago")
        self.assertFalse(json.loads(path.read_text())["last_success"])

    def test_concurrent_provider_changes_are_not_overwritten(self):
        path = self._save_setup()
        def change():
            data = json.loads(path.read_text())
            data["args"]["hosted_node"] = "10.0.0.99"
            write_json_atomic(str(path), data)
        self.before_migration = change
        with self.assertRaisesRegex(ProxmoxMigrateError, "binding changed"):
            self._migrate()
        self.assertEqual(load_setup_command("10.0.0.50").hosted_node, "10.0.0.99")
        self.assertEqual(self.state, "stopped")

    def test_saved_identity_or_disk_drift_blocks_before_shutdown(self):
        path = self._save_setup()
        original = path.read_bytes()
        for key, value in (("friendly_name", "another-vm"), ("container_storage", [["root", "wrong", "8G"]]),
                           ("container_storage", [["root", "hdd", "8G"], ["missing", "hdd", "16G"]])):
            with self.subTest(key=key, value=value):
                data = json.loads(original)
                data["args"][key] = value
                write_json_atomic(str(path), data)
                with self.assertRaises(ProxmoxMigrateError):
                    self._migrate()
                self.assertEqual(self.actions, [])

    def test_preflight_blocks_unhealthy_cluster_tasks_pending_and_ha(self):
        changes = [("quorate", 0), ("tasks", [{"status": "RUNNING"}]),
                   ("pending", [{"key": "memory", "pending": "8192"}]), ("hastate", "started")]
        for field, value in changes:
            with self.subTest(field=field):
                previous = getattr(self, field)
                setattr(self, field, value)
                with self.assertRaises(ProxmoxMigrateError):
                    self._migrate()
                setattr(self, field, previous)
                self.assertEqual(self.actions, [])
        self.ca["pve2"] = "b" * 64
        with self.assertRaisesRegex(ProxmoxMigrateError, "different Proxmox clusters"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_storage_policy_and_aggregate_capacity_fail_before_shutdown(self):
        for update in ({"content": "iso,backup"}, {"active": 0}, {"enabled": 0}, {"avail": 20 * GIB}, {"avail": True}):
            with self.subTest(update=update):
                self.target_pools = [{**self._pool("local"), **update}]
                with self.assertRaises(ProxmoxMigrateError):
                    self._migrate(dry_run=True)
                self.assertEqual(self.actions, [])

    def test_missing_destination_bridge_blocks_before_shutdown(self):
        self.bridges = [{"ifname": "vmbr1"}]
        with self.assertRaisesRegex(ProxmoxMigrateError, "bridge.*unavailable"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_duplicate_volume_inventory_blocks_before_shutdown(self):
        self.volumes.append(dict(self.volumes[0]))
        with self.assertRaisesRegex(ProxmoxMigrateError, "duplicate storage volume"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_native_informational_size_correction_preserves_larger_actual_disk(self):
        self._save_setup()
        self.volumes[0]["size"] = 12 * GIB
        self._migrate()
        self.assertIn(f"size={12 * GIB}", self.destination_config["scsi0"])
        self.assertEqual(self.state, "running")
        self.assertEqual(load_setup_command("10.0.0.50").container_storage[0], ["root", "local", "8G"])

    def test_native_size_added_to_disk_without_size_metadata_is_allowed(self):
        self.config["scsi0"] = self.config["scsi0"].replace(",size=8G", "")
        self._migrate()
        self.assertEqual(self.state, "running")

    def test_destination_cannot_shrink_to_stale_informational_size(self):
        path = self._save_setup()
        original = path.read_bytes()
        self.volumes[0]["size"] = 12 * GIB
        def shrink():
            self.destination_volumes[0]["size"] = 8 * GIB
            self.destination_config["scsi0"] = self.destination_config["scsi0"].replace(
                f"size={12 * GIB}", "size=8G")
        self.after_transfer = shrink
        with self.assertRaisesRegex(ProxmoxMigrateError, "smaller than the verified disk"):
            self._migrate()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.state, "stopped")

    def test_unexpected_informational_size_change_does_not_start(self):
        def change_size():
            self.destination_config["scsi0"] = self.destination_config["scsi0"].replace(
                f"size={8 * GIB}", "size=20G")
        self.after_transfer = change_size
        with self.assertRaisesRegex(ProxmoxMigrateError, "unverified size change"):
            self._migrate()
        self.assertEqual(self.state, "stopped")

    def test_saved_capacity_must_fit_actual_volume_despite_larger_metadata(self):
        path = self._save_setup()
        original = path.read_bytes()
        self.volumes[0]["size"] = 4 * GIB
        with self.assertRaisesRegex(ProxmoxMigrateError, "smaller than saved disk"):
            self._migrate()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.actions, [])

    def test_native_efi_and_tpm_size_normalization_preserves_special_disks(self):
        self.config["efidisk0"] = "hdd:vm-100-disk-2,size=4M,efitype=4m,pre-enrolled-keys=1"
        self.config["tpmstate0"] = "hdd:vm-100-disk-3,size=16K,version=v2.0"
        self.volumes.extend([{"volid": "hdd:vm-100-disk-2", "size": 4 * 1024 ** 2},
                             {"volid": "hdd:vm-100-disk-3", "size": 16 * 1024}])
        self._migrate()
        self.assertIn(f"size={self.efi_size}", self.destination_config["efidisk0"])
        self.assertIn(f"size={4 * 1024 ** 2}", self.destination_config["tpmstate0"])
        self.assertEqual(self.state, "running")

    def test_invalid_native_efi_size_blocks_before_shutdown(self):
        self.config["efidisk0"] = "hdd:vm-100-disk-2,size=4M,efitype=4m"
        self.volumes.append({"volid": "hdd:vm-100-disk-2", "size": 4 * 1024 ** 2})
        self.efi_size = 0
        with self.assertRaisesRegex(ProxmoxMigrateError, "EFI variables size"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_provider_sizes_accept_native_formats_and_reject_invalid_values(self):
        for value, expected in (("1.5G", GIB * 3 // 2), ("8GiB", 8 * GIB), ("528K", 528 * 1024),
                                ("540673", 540673), ("2T", 2 * 1024 ** 4)):
            with self.subTest(value=value):
                self.assertEqual(provider_size_bytes(value), expected)
        for value in ("0G", "-1G", "nan", "1e3G", "8G\n", "1Q", "١G", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                provider_size_bytes(value)

    def test_boot_configuration_drift_does_not_start_or_update(self):
        path = self._save_setup()
        original = path.read_bytes()
        self.after_transfer = lambda: self.destination_config.update({"boot": "order=net0"})
        with self.assertRaisesRegex(ProxmoxMigrateError, "identity or hardware changed"):
            self._migrate()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.state, "stopped")

    def test_snapshots_are_rejected_before_shutdown(self):
        self.snapshots.append({"name": "before-upgrade"})
        with self.assertRaisesRegex(ProxmoxMigrateError, "snapshot-only volumes"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_hook_added_after_inspection_blocks_before_shutdown(self):
        real_transport = self._fake_run
        def transport(host, command, workspace, **options):
            if "/nodes/pve1/qemu/100/config" in command and self.leases.call_count == 2:
                self.config["hookscript"] = "hdd:snippets/new-hook.pl"
            return real_transport(host, command, workspace, **options)
        self.transport.side_effect = transport
        with self.assertRaisesRegex(ProxmoxMigrateError, "External snippets, hooks"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_hook_added_during_saved_update_blocks_before_start(self):
        self._save_setup()
        from lib.proxmox_migration_setups import apply_setup_updates
        def update(updates, workspace):
            apply_setup_updates(updates, workspace)
            self.destination_config["hookscript"] = "local:snippets/new-hook.pl"
        with patch("lib.proxmox_migrate.apply_setup_updates", side_effect=update):
            with self.assertRaisesRegex(ProxmoxMigrateError, "External snippets, hooks"):
                self._migrate()
        self.assertEqual(self.state, "stopped")
        self.assertFalse(any(" start " in command for _, command in self.actions))

    def test_destination_bridge_disappearing_during_transfer_blocks_start_and_updates(self):
        path = self._save_setup()
        original = path.read_bytes()
        self.after_transfer = lambda: self.bridges.clear()
        with self.assertRaisesRegex(ProxmoxMigrateError, "bridge.*unavailable"):
            self._migrate()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.state, "stopped")

    def test_serial_socket_is_portable(self):
        self.config["serial0"] = "socket"
        self._migrate()
        self.assertEqual(self.state, "running")

    def test_local_iso_and_external_resources_block_before_shutdown(self):
        original = copy.deepcopy(self.config)
        for additions in ({"ide3": "hdd:iso/cloudinit-installer.iso,media=cdrom"},
                          {"cicustom": "user=local:snippets/custom.yaml"},
                          {"hostpci0": "0000:00:01.0"}, {"hookscript": "local:snippets/hook.pl"},
                          {"serial0": "/dev/ttyS0"}, {"parallel0": "/dev/parport0"},
                          {"ivshmem": "size=32"}, {"args": "-drive file=/tmp/local.img"},
                          {"dev0": "path=/dev/dri/renderD128"}):
            with self.subTest(additions=additions):
                self.config = {**original, **additions}
                with self.assertRaises(ProxmoxMigrateError):
                    self._migrate()
                self.assertEqual(self.actions, [])

    def test_completed_tasks_do_not_block_migration(self):
        self.tasks = [{"endtime": 1700000000, "status": "OK"}]
        self._migrate()
        self.assertEqual(self.node, "pve2")

    def test_uncertain_start_result_resumes_without_starting_twice(self):
        self._save_setup()
        real_transport = self._fake_run
        def transport(host, command, workspace, **options):
            result = real_transport(host, command, workspace, **options)
            if command == "qm start 100":
                raise KeyboardInterrupt()
            return result
        self.transport.side_effect = transport
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        self.assertEqual(self.state, "running")
        self.transport.side_effect = real_transport
        self._migrate(resume=True)
        self.assertEqual(len([action for _, action in self.actions if " start " in action]), 1)

    def test_shared_disk_retains_pool_and_volume_in_saved_setup(self):
        self.source_pools.append(self._pool("shared", shared=True))
        self.target_pools.append(self._pool("shared", shared=True))
        self.config["scsi1"] = self.config["scsi1"].replace("hdd:", "shared:")
        self.volumes[1]["volid"] = "shared:vm-100-disk-1"
        path = self._save_setup()
        data = json.loads(path.read_text())
        data["args"]["container_storage"][1][1] = "shared"
        write_json_atomic(str(path), data)
        self._migrate()
        self.assertEqual(load_setup_command("10.0.0.50").container_storage[1], ["data", "shared", "16G"])

    def test_lxc_uses_offline_lifecycle_and_native_storage_option(self):
        self.kind = "lxc"
        self.config = {"hostname": self.name, "net0": "name=eth0,bridge=vmbr0,ip=10.0.0.50/24",
                       "rootfs": "hdd:vm-100-disk-0,size=8G"}
        self._migrate()
        self.assertEqual(self.actions, [("pve1", "pct shutdown 100 --timeout 180"),
                                      ("pve1", "pct migrate 100 pve2 --target-storage local"),
                                      ("pve2", "pct start 100")])

    def test_lxc_rejects_qemu_flags_before_mutation(self):
        self.kind = "lxc"
        for options in ({"online": True}, {"with_local_disks": True}):
            with self.assertRaisesRegex(ProxmoxMigrateError, "only for QEMU"):
                self._migrate(**options)
        self.assertEqual(self.actions, [])

    def test_ambiguous_guest_names_and_wrong_source_are_rejected(self):
        self.extra_guests = [{"vmid": 101, "name": self.name, "node": "pve1", "type": "qemu"}]
        with self.assertRaisesRegex(ProxmoxMigrateError, "ambiguous"):
            self._migrate(guest=self.name)
        self.extra_guests = []
        self.node = "pve2"
        with self.assertRaisesRegex(ProxmoxMigrateError, "not the requested source"):
            self._migrate()
        self.assertEqual(self.actions, [])

    def test_destination_name_or_address_conflicts_block_before_shutdown(self):
        for name in (self.name.upper(), "another-name"):
            with self.subTest(name=name):
                self.extra_guests = [{"vmid": 101, "name": name, "node": "pve2", "type": "qemu"}]
                self.other_configs[101] = {"ipconfig0": "ip=10.0.0.50/24"}
                with self.assertRaisesRegex(ProxmoxMigrateError, "already uses"):
                    self._migrate()
                self.assertEqual(self.actions, [])

    def test_other_destination_guests_with_distinct_identity_are_allowed(self):
        self.extra_guests = [{"vmid": 101, "name": "other", "node": "pve2", "type": "qemu"}]
        self.other_configs[101] = {"ipconfig0": "ip=10.0.0.51/24"}
        self._migrate()
        self.assertEqual(self.node, "pve2")

    def test_dhcp_tokens_are_not_treated_as_duplicate_static_addresses(self):
        self.config["ipconfig0"] = "ip=dhcp"
        self.extra_guests = [{"vmid": 101, "name": "other", "node": "pve2", "type": "qemu"}]
        self.other_configs[101] = {"ipconfig0": "ip=dhcp"}
        self._migrate()
        self.assertEqual(self.node, "pve2")

    def test_invalid_inputs_are_rejected_before_ssh(self):
        for options in ({"guest": 0}, {"guest": -1}, {"guest": True}, {"guest": "name\n"},
                        {"timeout": 0}, {"shutdown_timeout": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self._migrate(**options)
        self.transport.assert_not_called()

    def test_resume_requires_matching_checkpoint_and_no_active_task(self):
        with self.assertRaisesRegex(ProxmoxMigrateError, "No unfinished"):
            self._migrate(resume=True)
        self.failure = "lost_result"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        with self.assertRaisesRegex(ProxmoxMigrateError, "Resume arguments"):
            self._migrate(resume=True, timeout=7200)
        self.tasks = [{"status": "RUNNING"}]
        with self.assertRaisesRegex(ProxmoxMigrateError, "still active"):
            self._migrate(resume=True)

    def test_resume_rejects_setup_update_for_an_unverified_destination(self):
        path = self._save_setup()
        self.failure = "lost_result"
        with self.assertRaises(ProxmoxMigrateError):
            self._migrate()
        checkpoint = json.loads(self._marker().read_text())
        checkpoint["context"]["setup_updates"][0]["updates"]["hosted_node"] = "10.0.0.99"
        write_json_atomic(str(self._marker()), checkpoint)
        with self.assertRaisesRegex(ProxmoxMigrateError, "disagrees with verified destination"):
            self._migrate(resume=True)
        self.assertEqual(json.loads(path.read_text())["args"]["hosted_node"], self.source.address)
        self.assertEqual(self.state, "stopped")

    def test_transport_uses_selected_workspace_trust_and_bounded_mutation_wait(self):
        with patch("lib.proxmox_migrate.run", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as execute:
            self.assertEqual(_run(self.target, "qm start 100", self.workspace, timeout=7200, mutation=True), "ok")
        argv = execute.call_args.args[0]
        self.assertIn(f"UserKnownHostsFile={self.workspace}/known_hosts", argv)
        self.assertEqual(execute.call_args.kwargs["timeout"], 7200)


if __name__ == "__main__":
    unittest.main()
