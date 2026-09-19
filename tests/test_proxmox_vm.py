"""Tests for lib/proxmox_vm.py: parsers and check_vm_exists."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_vm import (
    _ResolvedImage,
    ProvisionError,
    _create_vm,
    _download_image_to_host,
    _resolve_image_storage,
    _iso_staging_filename,
    _needs_graphical_console,
    _parse_disk_size_gib,
    _parse_memory_mb,
    _preflight_data_disk_capacity,
    _render_user_data,
    _reconcile_existing_vm,
    _resolve_image,
    _destroy_vm_best_effort,
    _upload_user_data,
    _wait_for_guest_agent,
    check_vm_exists,
)
from lib.config import SetupConfig
from lib.vm_storage import VMDataDisk, VMDiskHardware, disk_hardware


class TestImageStorage(unittest.TestCase):
    def test_custom_image_requires_and_preserves_sha512(self):
        config = MagicMock(
            container_base="debian",
            vm_image_sha512="A" * 128,
        )
        resolved, catalog = _resolve_image(
            config,
            "https://example.com/custom.qcow2",
        )
        self.assertIsNone(catalog)
        self.assertEqual(resolved.sha512, "a" * 128)

    def test_custom_image_without_sha512_is_rejected(self):
        config = MagicMock(container_base="debian", vm_image_sha512=None)
        with self.assertRaisesRegex(ProvisionError, "image-sha512"):
            _resolve_image(config, "https://example.com/custom.qcow2")

    def test_iso_staging_uses_img_suffix(self):
        self.assertEqual(
            _iso_staging_filename("debian-13-genericcloud.qcow2"),
            "debian-13-genericcloud.img",
        )

    @patch("lib.proxmox_vm._resolve_storage_pool")
    def test_image_storage_prefers_import_content(self, mock_resolve):
        mock_resolve.return_value = "fast-files"

        result = _resolve_image_storage(
            "fast-files", "10.0.0.10", "root", [], dry_run=False
        )

        self.assertEqual(result, ("fast-files", "import"))
        mock_resolve.assert_called_once_with(
            "fast-files",
            "10.0.0.10",
            "root",
            [],
            "import",
            dry_run=False,
            strict_content=True,
        )

    @patch("lib.proxmox_vm._resolve_storage_pool")
    def test_image_storage_falls_back_to_iso_content(self, mock_resolve):
        mock_resolve.side_effect = [
            ProvisionError("import unavailable"),
            "local",
        ]

        result = _resolve_image_storage(
            "local", "10.0.0.10", "root", [], dry_run=False
        )

        self.assertEqual(result, ("local", "iso"))

    def test_download_dry_run_uses_import_volume_name(self):
        image = _ResolvedImage(
            url="https://example.com/debian.qcow2",
            sha512=None,
            filename="debian.qcow2",
            storage_ref=None,
        )

        path = _download_image_to_host(
            image,
            "local",
            "import",
            "10.0.0.10",
            "root",
            [],
            dry_run=True,
        )

        self.assertEqual(path, "/var/lib/vz/import/debian.qcow2")

    def test_download_dry_run_uses_iso_compatible_img_name(self):
        image = _ResolvedImage(
            url="https://example.com/debian.qcow2",
            sha512=None,
            filename="debian.qcow2",
            storage_ref=None,
        )

        path = _download_image_to_host(
            image,
            "local",
            "iso",
            "10.0.0.10",
            "root",
            [],
            dry_run=True,
        )

        self.assertEqual(path, "/var/lib/vz/template/iso/debian.img")

    @patch("lib.proxmox_vm._ssh_run")
    def test_download_resolves_iso_with_compatible_volume_name(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/var/lib/vz/template/iso/debian.img\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        image = _ResolvedImage(
            url="https://example.com/debian.qcow2",
            sha512=None,
            filename="debian.qcow2",
            storage_ref=None,
        )

        path = _download_image_to_host(
            image,
            "local",
            "iso",
            "10.0.0.10",
            "root",
            [],
            dry_run=False,
        )

        self.assertEqual(path, "/var/lib/vz/template/iso/debian.img")
        self.assertIn(
            "pvesm path local:iso/debian.img",
            mock_run.call_args_list[0].args[3],
        )
        download_command = mock_run.call_args_list[2].args[3]
        self.assertIn("flock --exclusive /run/lock/basaltwater-image-", download_command)
        self.assertIn("debian.img.part", download_command)
        self.assertEqual(mock_run.call_args_list[2].kwargs["timeout"], 1800)

    @patch("lib.proxmox_vm._ssh_run")
    def test_download_verifies_image_while_holding_remote_lock(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/var/lib/vz/import/debian.qcow2\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        image = _ResolvedImage(
            url="https://example.com/debian.qcow2",
            sha512="a" * 128,
            filename="debian.qcow2",
            storage_ref=None,
        )

        _download_image_to_host(
            image,
            "local",
            "import",
            "10.0.0.10",
            "root",
            [],
            dry_run=False,
        )

        command = mock_run.call_args_list[2].args[3]
        self.assertIn("flock --exclusive /run/lock/basaltwater-image-", command)
        self.assertIn("sha512sum -c -", command)
        self.assertEqual(mock_run.call_count, 3)


class TestGuestAgentWait(unittest.TestCase):
    @patch("lib.proxmox_vm.time.sleep")
    @patch("lib.proxmox_vm._ssh_run")
    def test_wait_suppresses_expected_retry_warnings(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="not running"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        _wait_for_guest_agent(111, "10.0.0.10", "root", [], poll_interval=5)

        self.assertTrue(mock_run.call_args.kwargs["quiet"])
        self.assertEqual(mock_sleep.call_count, 1)


class TestParseMemory(unittest.TestCase):
    def test_gigabytes(self):
        self.assertEqual(_parse_memory_mb("2G"), 2048)

    def test_fractional_gigabytes(self):
        self.assertEqual(_parse_memory_mb("1.5G"), 1536)
        self.assertEqual(_parse_memory_mb("0.5G"), 512)

    def test_megabytes(self):
        self.assertEqual(_parse_memory_mb("512M"), 512)

    def test_terabytes(self):
        self.assertEqual(_parse_memory_mb("1T"), 1024 * 1024)

    def test_invalid_raises(self):
        with self.assertRaises(ProvisionError):
            _parse_memory_mb("")
        with self.assertRaises(ProvisionError):
            _parse_memory_mb("abc")
        with self.assertRaises(ProvisionError):
            _parse_memory_mb("0G")
        with self.assertRaises(ProvisionError):
            _parse_memory_mb("1.1G")


class TestParseDiskSize(unittest.TestCase):
    def test_gigabytes(self):
        self.assertEqual(_parse_disk_size_gib("32G"), 32)

    def test_terabytes(self):
        self.assertEqual(_parse_disk_size_gib("2T"), 2048)

    def test_megabytes_rounded(self):
        self.assertEqual(_parse_disk_size_gib("8192M"), 8)

    def test_too_small_raises(self):
        with self.assertRaises(ProvisionError):
            _parse_disk_size_gib("100M")
        with self.assertRaises(ProvisionError):
            _parse_disk_size_gib("0G")


class TestRenderUserData(unittest.TestCase):
    def test_includes_root_key(self):
        out = _render_user_data(username="root", pubkey_contents="ssh-ed25519 AAAA test")
        self.assertIn("- name: root", out)
        self.assertIn("ssh-ed25519 AAAA test", out)
        self.assertIn("packages:", out)
        self.assertIn("qemu-guest-agent", out)
        self.assertIn("virtio_balloon", out)
        self.assertIn("basaltwater-virtio-balloon.conf", out)
        self.assertIn("systemctl enable --now qemu-guest-agent", out)

    def test_creates_non_root_user(self):
        out = _render_user_data(username="alice", pubkey_contents="ssh-ed25519 KEY")
        self.assertIn("- name: alice", out)
        self.assertIn("groups: sudo", out)
        self.assertNotIn("NOPASSWD:ALL", out)
        self.assertNotIn("/etc/sudoers.d/basaltwater-alice", out)
        # SSH key is added under both root and alice.
        self.assertEqual(out.count("ssh-ed25519 KEY"), 2)

    def test_nopasswd_creates_managed_sudoers_rule(self):
        out = _render_user_data(
            username="alice",
            pubkey_contents="ssh-ed25519 KEY",
            nopasswd=True,
        )

        self.assertIn("NOPASSWD:ALL", out)
        self.assertIn("/etc/sudoers.d/basaltwater-alice", out)
        self.assertIn("owner: root:root", out)
        self.assertIn("permissions: '0440'", out)
        self.assertEqual(out.count("ssh-ed25519 KEY"), 2)

    def test_can_defer_setup_user_until_home_is_mounted(self):
        out = _render_user_data(
            username="alice",
            pubkey_contents="ssh-ed25519 KEY",
            create_setup_user=False,
        )

        self.assertNotIn("- name: alice", out)
        self.assertNotIn("basaltwater-alice", out)
        self.assertEqual(out.count("ssh-ed25519 KEY"), 1)

    def test_no_pubkey(self):
        out = _render_user_data(username="root", pubkey_contents=None)
        self.assertNotIn("ssh_authorized_keys", out)
        self.assertIn("qemu-guest-agent", out)

    def test_rejects_multiline_pubkey(self):
        with self.assertRaisesRegex(ProvisionError, r"single line"):
            _render_user_data(
                username="root",
                pubkey_contents="ssh-ed25519 AAAA test\nruncmd:",
            )

    def test_rejects_invalid_username(self):
        with self.assertRaisesRegex(ProvisionError, "Invalid VM setup username"):
            _render_user_data(username="bad user", pubkey_contents=None)

    def test_quotes_pubkey_as_yaml_scalar(self):
        out = _render_user_data(
            username="root",
            pubkey_contents="ssh-ed25519 AAAA comment-with-'quote'",
        )
        self.assertIn("- 'ssh-ed25519 AAAA comment-with-''quote'''", out)


class TestUserDataUpload(unittest.TestCase):
    @patch("lib.proxmox_vm._ssh_run")
    def test_upload_reuses_proxmox_ssh_connection(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="/var/lib/vz/snippets/basaltwater-vm.yaml\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with patch("lib.proxmox_vm.secrets.token_hex", return_value="run123"):
            path = _upload_user_data(
                "#cloud-config\nhostname: __HOSTNAME__\n",
                "vm-01",
                "local",
                "192.0.2.10",
                "root",
                ["-i", "/keys/proxmox"],
                dry_run=False,
            )

        self.assertEqual(path, "/var/lib/vz/snippets/basaltwater-vm.yaml")
        self.assertIn(
            "pvesm path local:snippets/basaltwater-vm-01-run123.yaml",
            mock_run.call_args_list[0].args[3],
        )
        upload_call = mock_run.call_args_list[2]
        self.assertEqual(
            upload_call.kwargs["input_data"],
            "#cloud-config\nhostname: vm-01\n",
        )


class TestVMHardwareProfile(unittest.TestCase):
    def test_desktop_and_rdp_profiles_need_graphical_console(self):
        desktop = MagicMock(include_desktop=True, enable_rdp=False)
        rdp = MagicMock(include_desktop=False, enable_rdp=True)
        server = MagicMock(include_desktop=False, enable_rdp=False)

        self.assertTrue(_needs_graphical_console(desktop))
        self.assertTrue(_needs_graphical_console(rdp))
        self.assertFalse(_needs_graphical_console(server))

    def test_per_device_settings_override_vm_defaults(self):
        config = SetupConfig(
            host="10.0.0.50",
            username="agent",
            system_type="agent_code_vm",
            container_storage=[
                ["root", "local-lvm", "32G"],
                ["archive", "bulk-lvm", "2T"],
            ],
            vm_disk_discard=False,
            vm_disk_ssd=False,
            vm_disk_settings=[
                ["root", "discard=on", "ssd=on"],
                ["archive", "ssd=off"],
            ],
        )

        settings = disk_hardware(config)

        self.assertEqual(settings["root"], VMDiskHardware("root", True, True))
        self.assertEqual(
            settings["archive"],
            VMDiskHardware("archive", False, False),
        )

    @patch("lib.proxmox_vm._ssh_run")
    def test_graphical_vm_uses_virtio_gpu_and_disk_iothread(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        _create_vm(
            vmid=101,
            target_ip="10.0.0.50",
            image_remote_path="/var/lib/vz/import/debian.qcow2",
            storage_ref=None,
            memory_mb=8192,
            balloon_min_mb=4096,
            cores=4,
            root_pool="local-lvm",
            disk_size_gib=40,
            data_disk_specs=[],
            cidr_prefix="24",
            bridge="vmbr0",
            gateway="10.0.0.1",
            nameservers=["10.0.0.1"],
            hostname="agent-vm",
            user_data_path=None,
            user_data_ref="nfs-store:snippets/basaltwater-agent-vm.yaml",
            graphical_console=True,
            node_ip="10.0.0.10",
            user="root",
            ssh_opts=[],
            ipv6_cidr="2001:db8::50/64",
            gateway6="2001:db8::1",
        )

        commands = [call.args[3] for call in mock_run.call_args_list]
        self.assertIn("--serial0 socket", commands[0])
        self.assertIn("--vga virtio", commands[0])
        self.assertIn("--agent enabled=1,freeze-fs=1", commands[0])
        self.assertIn("--rng0 source=/dev/urandom", commands[0])
        self.assertIn(
            "--cicustom user=nfs-store:snippets/basaltwater-agent-vm.yaml",
            commands[0],
        )
        self.assertIn("--scsihw virtio-scsi-single", commands[0])
        self.assertIn("--cpu host", commands[0])
        self.assertIn("--memory 8192 --balloon 4096", commands[0])
        self.assertEqual(
            commands[1],
            "qm disk import 101 /var/lib/vz/import/debian.qcow2 local-lvm",
        )
        self.assertIn("ip6=2001:db8::50/64", commands[0])
        self.assertIn("gw6=2001:db8::1", commands[0])
        self.assertIn(
            "--scsi0 local-lvm:vm-101-disk-0,iothread=1,discard=on",
            commands[2],
        )
        self.assertNotIn("ssd=1", commands[2])

    @patch("lib.proxmox_vm._ssh_run")
    def test_partial_vm_is_destroyed_when_import_fails(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="import failed"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        with self.assertRaises(ProvisionError):
            _create_vm(
                vmid=101,
                target_ip="10.0.0.50",
                image_remote_path="/var/lib/vz/import/debian.qcow2",
                storage_ref=None,
                memory_mb=2048,
                balloon_min_mb=2048,
                cores=2,
                root_pool="local-lvm",
                disk_size_gib=20,
                data_disk_specs=[],
                cidr_prefix="24",
                bridge="vmbr0",
                gateway="10.0.0.1",
                nameservers=["10.0.0.1"],
                hostname="agent-vm",
                user_data_path=None,
                user_data_ref=None,
                graphical_console=False,
                node_ip="10.0.0.10",
                user="root",
                ssh_opts=[],
            )
        commands = [call.args[3] for call in mock_run.call_args_list]
        self.assertEqual(commands[-2:], [
            "qm stop 101 --skiplock 1",
            "qm destroy 101 --purge 1 --skiplock 1",
        ])


class TestVMCleanup(unittest.TestCase):
    @patch("lib.proxmox_vm._ssh_run")
    def test_cleanup_reports_failed_stop_or_destroy(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="stop failed"),
            MagicMock(returncode=1, stdout="", stderr="destroy failed"),
        ]
        with patch("builtins.print") as mock_print:
            _destroy_vm_best_effort(101, "10.0.0.10", "root", [])

        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("cleanup incomplete", output)
        self.assertIn("stop failed", output)
        self.assertIn("destroy failed", output)

    @patch("lib.proxmox_vm._ssh_run")
    def test_resize_failure_destroys_partial_vm(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="cannot shrink disk"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        with self.assertRaisesRegex(ProvisionError, "cannot shrink disk"):
            _create_vm(
                vmid=101,
                target_ip="10.0.0.50",
                image_remote_path="/var/lib/vz/import/debian.qcow2",
                storage_ref=None,
                memory_mb=2048,
                balloon_min_mb=2048,
                cores=2,
                root_pool="local-lvm",
                disk_size_gib=1,
                data_disk_specs=[],
                cidr_prefix="24",
                bridge="vmbr0",
                gateway="10.0.0.1",
                nameservers=["10.0.0.1"],
                hostname="agent-vm",
                user_data_path=None,
                user_data_ref=None,
                graphical_console=False,
                node_ip="10.0.0.10",
                user="root",
                ssh_opts=[],
            )
        commands = [call.args[3] for call in mock_run.call_args_list]
        self.assertEqual(commands[-2:], [
            "qm stop 101 --skiplock 1",
            "qm destroy 101 --purge 1 --skiplock 1",
        ])


class TestVMDataDisks(unittest.TestCase):
    @patch("lib.proxmox_vm._ssh_run")
    def test_attaches_and_verifies_stable_disk_identity(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout=(
                    "scsi0: local-lvm:vm-101-disk-0,iothread=1,discard=on,ssd=1\n"
                    "scsi1: bulk-lvm:vm-101-disk-1,iothread=1,"
                    "discard=on,"
                    "serial=bw-agent-data,size=128G\n"
                ),
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        _create_vm(
            vmid=101,
            target_ip="10.0.0.50",
            image_remote_path="/var/lib/vz/import/debian.qcow2",
            storage_ref=None,
            memory_mb=4096,
            balloon_min_mb=1024,
            cores=4,
            root_pool="local-lvm",
            disk_size_gib=32,
            data_disk_specs=[VMDataDisk("agent-data", "bulk-lvm", "128G")],
            cidr_prefix="24",
            bridge="vmbr0",
            gateway="10.0.0.1",
            nameservers=["10.0.0.1"],
            hostname="agent-vm",
            user_data_path=None,
            user_data_ref=None,
            graphical_console=False,
            node_ip="10.0.0.10",
            user="root",
            ssh_opts=[],
            disk_hardware_settings={
                "root": VMDiskHardware("root", True, True),
                "agent-data": VMDiskHardware("agent-data", True, False),
            },
        )

        commands = [call.args[3] for call in mock_run.call_args_list]
        self.assertIn(
            "--scsi0 local-lvm:vm-101-disk-0,iothread=1,discard=on,ssd=1",
            commands[2],
        )
        self.assertIn(
            "qm set 101 --scsi1 bulk-lvm:128,iothread=1,serial=bw-agent-data,discard=on",
            commands,
        )
        self.assertNotIn("bulk-lvm:128G", "\n".join(commands))
        self.assertLess(commands.index("qm config 101"), commands.index("qm start 101"))

    @patch("lib.proxmox_vm._ssh_run")
    def test_attach_failure_destroys_partial_vm(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="pool full"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        with self.assertRaisesRegex(ProvisionError, "pool full"):
            _create_vm(
                vmid=101,
                target_ip="10.0.0.50",
                image_remote_path="/var/lib/vz/import/debian.qcow2",
                storage_ref=None,
                memory_mb=4096,
                balloon_min_mb=1024,
                cores=4,
                root_pool="local-lvm",
                disk_size_gib=32,
                data_disk_specs=[VMDataDisk("git-data", "bulk-lvm", "64G")],
                cidr_prefix="24",
                bridge="vmbr0",
                gateway="10.0.0.1",
                nameservers=["10.0.0.1"],
                hostname="git-vm",
                user_data_path=None,
                user_data_ref=None,
                graphical_console=False,
                node_ip="10.0.0.10",
                user="root",
                ssh_opts=[],
            )

        commands = [call.args[3] for call in mock_run.call_args_list]
        self.assertEqual(
            commands[-2:],
            [
                "qm stop 101 --skiplock 1",
                "qm destroy 101 --purge 1 --skiplock 1",
            ],
        )

    @patch("lib.proxmox_vm._ssh_run")
    def test_capacity_preflight_groups_disks_by_pool(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Name Type Status Total Used Available %\n"
                "bulk lvmthin active 419430400 0 314572800 0.00%\n"
            ),
            stderr="",
        )

        _preflight_data_disk_capacity(
            [
                VMDataDisk("git-data", "bulk", "100G"),
                VMDataDisk("agent-data", "bulk", "128G"),
            ],
            "10.0.0.10",
            "root",
            [],
        )

        mock_run.assert_called_once()

    @patch("lib.proxmox_vm._ssh_run")
    def test_capacity_preflight_refuses_insufficient_space(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Name Type Status Total Used Available %\n"
                "bulk lvmthin active 419430400 0 104857600 0.00%\n"
            ),
            stderr="",
        )

        with self.assertRaisesRegex(ProvisionError, "228G.*requested"):
            _preflight_data_disk_capacity(
                [
                    VMDataDisk("git-data", "bulk", "100G"),
                    VMDataDisk("agent-data", "bulk", "128G"),
                ],
                "10.0.0.10",
                "root",
                [],
            )


class TestCheckVMExists(unittest.TestCase):
    @patch("lib.proxmox_vm._ssh_run")
    def test_finds_match(self, mock_run):
        # First call: qm list. Second call: qm config 100 (no match).
        # Third call: qm config 101 (match).
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n100 a running\n101 b running\n"),
            MagicMock(returncode=0, stdout="name: a\nnet0: virtio,bridge=vmbr0\nipconfig0: ip=10.0.0.1/24,gw=10.0.0.254\n"),
            MagicMock(returncode=0, stdout="name: b\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.254\n"),
            MagicMock(returncode=0, stdout="status: running\n"),
            MagicMock(returncode=0, stdout="READY\n"),
        ]
        self.assertTrue(check_vm_exists("10.0.0.1", "10.0.0.50", "root", []))

    @patch("lib.proxmox_vm._ssh_run")
    def test_no_match(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n100 a running\n"),
            MagicMock(returncode=0, stdout="ipconfig0: ip=10.0.0.99/24,gw=10.0.0.254\n"),
        ]
        self.assertFalse(check_vm_exists("10.0.0.1", "10.0.0.50", "root", []))

    @patch("lib.proxmox_vm._ssh_run")
    def test_vm_list_failure_is_not_treated_as_no_match(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=255,
            stdout="",
            stderr="ssh timeout",
        )

        with self.assertRaisesRegex(ProvisionError, "Failed to query VMs"):
            check_vm_exists("10.0.0.1", "10.0.0.50", "root", [])

    @patch("lib.proxmox_vm._ssh_run")
    def test_unreachable_match_is_not_silently_reused(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n101 b running\n"),
            MagicMock(returncode=0, stdout="ipconfig0: ip=10.0.0.50/24,gw=10.0.0.254\n"),
            MagicMock(returncode=0, stdout="status: stopped\n"),
            MagicMock(returncode=1, stdout=""),
        ]
        with self.assertRaisesRegex(ProvisionError, "not reachable"):
            check_vm_exists("10.0.0.1", "10.0.0.50", "root", [])

    @patch("lib.proxmox_vm._ssh_run")
    def test_dry_run_returns_false_without_calls(self, mock_run):
        self.assertFalse(check_vm_exists("10.0.0.1", "10.0.0.50", "root", [], dry_run=True))
        mock_run.assert_not_called()

    @patch("lib.proxmox_vm._ssh_run")
    def test_existing_target_is_renamed_and_verified(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n112 agent-min-1 running\n"),
            MagicMock(
                returncode=0,
                stdout="name: agent-min-1\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n",
            ),
            MagicMock(returncode=0, stdout="status: running\n"),
            MagicMock(returncode=0, stdout="READY\n"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout="name: agent-min-2\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n",
            ),
        ]

        self.assertTrue(
            _reconcile_existing_vm(
                "10.0.0.1", "10.0.0.50", "agent-min-2", "root", []
            )
        )
        self.assertEqual(
            mock_run.call_args_list[4].args[3],
            "qm set 112 --name agent-min-2",
        )
        self.assertEqual(mock_run.call_args_list[5].args[3], "qm config 112")

    @patch("lib.proxmox_vm._ssh_run")
    def test_existing_target_cores_are_reconciled_and_verified(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n112 agent-2 running\n"),
            MagicMock(
                returncode=0,
                stdout=(
                    "name: agent-2\n"
                    "cores: 2\n"
                    "ipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n"
                ),
            ),
            MagicMock(returncode=0, stdout="status: running\n"),
            MagicMock(returncode=0, stdout="READY\n"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout=(
                    "name: agent-2\n"
                    "cores: 3\n"
                    "ipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n"
                ),
            ),
        ]

        with patch("builtins.print") as mock_print:
            self.assertTrue(
                _reconcile_existing_vm(
                    "10.0.0.1",
                    "10.0.0.50",
                    "agent-2",
                    "root",
                    [],
                    desired_cores=3,
                )
            )

        self.assertEqual(
            mock_run.call_args_list[4].args[3],
            "qm set 112 --cores 3",
        )
        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("from 2 to 3 cores", output)
        self.assertIn("restart it", output)

    @patch("lib.proxmox_vm._ssh_run")
    def test_existing_target_matching_cores_are_not_rewritten(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n112 agent-2 running\n"),
            MagicMock(
                returncode=0,
                stdout=(
                    "name: agent-2\n"
                    "cores: 3\n"
                    "ipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n"
                ),
            ),
            MagicMock(returncode=0, stdout="status: running\n"),
            MagicMock(returncode=0, stdout="READY\n"),
        ]

        self.assertTrue(
            _reconcile_existing_vm(
                "10.0.0.1",
                "10.0.0.50",
                "agent-2",
                "root",
                [],
                desired_cores=3,
            )
        )

        self.assertEqual(len(mock_run.call_args_list), 4)

    @patch("lib.proxmox_vm._ssh_run")
    def test_existing_target_rejects_unverified_core_update(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n112 agent-2 running\n"),
            MagicMock(
                returncode=0,
                stdout=(
                    "name: agent-2\n"
                    "cores: 2\n"
                    "ipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n"
                ),
            ),
            MagicMock(returncode=0, stdout="status: running\n"),
            MagicMock(returncode=0, stdout="READY\n"),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=0,
                stdout=(
                    "name: agent-2\n"
                    "cores: 2\n"
                    "ipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n"
                ),
            ),
        ]

        with self.assertRaisesRegex(ProvisionError, "did not preserve"):
            _reconcile_existing_vm(
                "10.0.0.1",
                "10.0.0.50",
                "agent-2",
                "root",
                [],
                desired_cores=3,
            )

    @patch("lib.proxmox_vm._ssh_run")
    def test_duplicate_desired_name_stops_before_creation(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="VMID NAME STATUS\n112 agent-min-1 running\n"),
            MagicMock(
                returncode=0,
                stdout="name: agent-min-1\nipconfig0: ip=10.0.0.40/24,gw=10.0.0.1\n",
            ),
        ]

        with self.assertRaisesRegex(ProvisionError, "already exists.*VMIDs: 112"):
            _reconcile_existing_vm(
                "10.0.0.1", "10.0.0.50", "agent-min-1", "root", []
            )

    @patch("lib.proxmox_vm._ssh_run")
    def test_rename_refuses_name_owned_by_another_vm(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    "VMID NAME STATUS\n"
                    "112 agent-min-2 running\n"
                    "113 agent-min-1 running\n"
                ),
            ),
            MagicMock(
                returncode=0,
                stdout="name: agent-min-2\nipconfig0: ip=10.0.0.40/24,gw=10.0.0.1\n",
            ),
            MagicMock(
                returncode=0,
                stdout="name: agent-min-1\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n",
            ),
        ]

        with self.assertRaisesRegex(ProvisionError, "already used.*112"):
            _reconcile_existing_vm(
                "10.0.0.1", "10.0.0.50", "agent-min-2", "root", []
            )

    @patch("lib.proxmox_vm._ssh_run")
    def test_duplicate_target_ip_is_ambiguous(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="VMID NAME STATUS\n112 one running\n113 two running\n",
            ),
            MagicMock(
                returncode=0,
                stdout="name: one\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n",
            ),
            MagicMock(
                returncode=0,
                stdout="name: two\nipconfig0: ip=10.0.0.50/24,gw=10.0.0.1\n",
            ),
        ]

        with self.assertRaisesRegex(ProvisionError, "Multiple VMs.*112, 113"):
            check_vm_exists("10.0.0.1", "10.0.0.50", "root", [])


if __name__ == "__main__":
    unittest.main()
