"""Tests for hosted/container CLI flag parsing in lib/arg_parser.py."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.arg_parser import create_setup_argument_parser


class TestHostedFlagParsing(unittest.TestCase):
    def setUp(self):
        self.parser = create_setup_argument_parser("Test", for_remote=False)

    def test_provision_on_flag(self):
        args = self.parser.parse_args(["10.0.0.50", "--provision-on", "10.0.0.1"])
        self.assertEqual(args.hosted_node, "10.0.0.1")

    def test_hosted_user_default_is_deferred_to_config(self):
        args = self.parser.parse_args(["10.0.0.50", "--provision-on", "10.0.0.1"])
        self.assertFalse(hasattr(args, "hosted_user"))
        self.assertFalse(hasattr(args, "hosted_key"))

    def test_hosted_user_override(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--provision-on", "10.0.0.1",
            "--provision-user", "admin"
        ])
        self.assertEqual(args.hosted_user, "admin")

    def test_hosted_key(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--provision-on", "10.0.0.1",
            "--provision-key", "/path/to/key"
        ])
        self.assertEqual(args.hosted_key, "/path/to/key")

    def test_hosted_bridge(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--provision-on", "10.0.0.1", "--bridge", "vmbr20"
        ])
        self.assertEqual(args.hosted_bridge, "vmbr20")

    def test_memory_flag(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--memory", "1.5G", "--balloon-min", "0.5G"
        ])
        self.assertEqual(args.container_memory, "1.5G")
        self.assertEqual(args.vm_balloon_min, "0.5G")

    def test_balloon_min_flag(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--balloon-min", "1G"
        ])
        self.assertEqual(args.vm_balloon_min, "1G")

    def test_storage_flag_three_args(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--storage", "root", "auto", "10G"
        ])
        self.assertEqual(args.container_storage, [["root", "auto", "10G"]])

    def test_multiple_storage_specs(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--storage", "root", "auto", "10G",
            "--storage", "template", "local"
        ])
        self.assertEqual(
            args.container_storage,
            [["root", "auto", "10G"], ["template", "local"]]
        )

    def test_named_vm_data_disk_and_mount(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--storage", "root", "local-lvm", "32G",
            "--storage", "agent-data", "fast-lvm", "128G",
            "--storage-mount", "agent-data", "/srv/agent-workspace", "xfs",
            "--agent-workspace", "/srv/agent-workspace",
        ])
        self.assertEqual(
            args.container_storage,
            [["root", "local-lvm", "32G"], ["agent-data", "fast-lvm", "128G"]],
        )
        self.assertEqual(
            args.storage_mounts,
            [["agent-data", "/srv/agent-workspace", "xfs"]],
        )
        self.assertEqual(args.agent_workspace, "/srv/agent-workspace")

    def test_named_vm_block_cache(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--storage", "data", "bulk-lvm", "3T",
            "--storage", "data-cache", "local-lvm", "128G",
            "--storage-mount", "data", "/srv/data", "xfs",
            "--storage-cache", "data", "data-cache", "writethrough",
        ])

        self.assertEqual(
            args.storage_caches,
            [["data", "data-cache", "writethrough"]],
        )

    def test_cores_default_is_deferred_to_setup_config(self):
        args = self.parser.parse_args(["10.0.0.50"])
        self.assertFalse(hasattr(args, "container_cores"))

    def test_cores_override(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--cores", "4"
        ])
        self.assertEqual(args.container_cores, 4)

    def test_vm_hardware_defaults_are_deferred_to_setup_config(self):
        args = self.parser.parse_args(["10.0.0.50"])

        self.assertFalse(hasattr(args, "vm_cpu_type"))
        self.assertFalse(hasattr(args, "vm_disk_discard"))
        self.assertFalse(hasattr(args, "vm_disk_ssd"))
        self.assertFalse(hasattr(args, "vm_disk_backup"))
        self.assertFalse(hasattr(args, "vm_disk_settings"))

    def test_vm_hardware_overrides(self):
        args = self.parser.parse_args(
            [
                "10.0.0.50",
                "--cpu-type",
                "x86-64-v2-AES",
                "--no-disk-discard",
                "--disk-ssd",
            ]
        )

        self.assertEqual(args.vm_cpu_type, "x86-64-v2-AES")
        self.assertFalse(args.vm_disk_discard)
        self.assertTrue(args.vm_disk_ssd)

    def test_vm_hardware_per_device_overrides(self):
        args = self.parser.parse_args(
            [
                "10.0.0.50",
                "--disk-ssd",
                "root",
                "--no-disk-discard",
                "archive",
                "--no-disk-ssd",
                "archive",
                "--no-disk-backup",
                "archive",
            ]
        )

        self.assertFalse(hasattr(args, "vm_disk_ssd"))
        self.assertFalse(hasattr(args, "vm_disk_discard"))
        self.assertEqual(
            args.vm_disk_settings,
            [
                ["root", "ssd=on"],
                ["archive", "discard=off", "ssd=off", "backup=off"],
            ],
        )

    def test_vm_hardware_global_default_and_named_exception(self):
        args = self.parser.parse_args(
            [
                "10.0.0.50",
                "--disk-ssd",
                "--no-disk-ssd",
                "archive",
            ]
        )

        self.assertTrue(args.vm_disk_ssd)
        self.assertEqual(args.vm_disk_settings, [["archive", "ssd=off"]])

    def test_swap_tiers_and_tuning(self):
        args = self.parser.parse_args(
            [
                "10.0.0.50",
                "--swap-zram", "fast", "1G", "priority=300", "algorithm=zstd",
                "--swap-device", "bulk", "archive", "priority=10", "discard=once",
                "--swappiness", "120",
                "--swap-resume", "bulk",
            ]
        )

        self.assertEqual(
            args.swap_zram,
            [["fast", "1G", "priority=300", "algorithm=zstd"]],
        )
        self.assertEqual(
            args.swap_devices,
            [["bulk", "archive", "priority=10", "discard=once"]],
        )
        self.assertEqual(args.swappiness, 120)
        self.assertEqual(args.swap_resume, "bulk")

    def test_swap_resume_can_be_cleared(self):
        args = self.parser.parse_args(["10.0.0.50", "--no-swap-resume"])

        self.assertEqual(args.swap_resume, "")

    def test_swap_resume_set_and_clear_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(
                    [
                        "10.0.0.50",
                        "--swap-resume",
                        "bulk",
                        "--no-swap-resume",
                    ]
                )

    def test_base_default_is_deferred_to_setup_config(self):
        args = self.parser.parse_args(["10.0.0.50"])
        self.assertFalse(hasattr(args, "container_base"))

    def test_base_override(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--base", "ubuntu"
        ])
        self.assertEqual(args.container_base, "ubuntu")

    def test_image_storage_flag(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--image-storage", "fast-files"
        ])
        self.assertEqual(args.vm_image_storage, "fast-files")

    def test_full_hosted_command(self):
        args = self.parser.parse_args([
            "10.0.0.50", "--provision-on", "10.0.0.1",
            "--memory", "2G", "--storage", "root", "auto", "10G",
            "--cores", "2", "--base", "debian",
            "--name", "web-01"
        ])
        self.assertEqual(args.hosted_node, "10.0.0.1")
        self.assertEqual(args.container_memory, "2G")
        self.assertEqual(args.container_storage, [["root", "auto", "10G"]])
        self.assertEqual(args.container_cores, 2)
        self.assertEqual(args.container_base, "debian")
        self.assertEqual(args.friendly_name, "web-01")

    def test_node_flag_still_installs_nodejs(self):
        """Ensure --node still means install Node.js, not proxmox node."""
        args = self.parser.parse_args(["10.0.0.50", "--node"])
        self.assertTrue(args.install_node)
        self.assertIsNone(args.hosted_node)

    def test_explicit_agent_tool_flags(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--agent-tool", "gh",
            "--agent-tool", "codex",
            "--agent-tool", "claude",
            "--agent-tool", "opencode",
            "--web-interface", "t3code",
            "--git-access", "read-write",
            "--git-auth", "active",
            "--agent-auth", "login",
            "--agent-config", "active",
            "--repo", "https://github.com/user/one.git",
            "--repo", "https://gitlab.com/user/two.git",
        ])
        self.assertEqual(args.agent_tools, ["gh", "codex", "claude", "opencode"])
        self.assertEqual(args.web_interfaces, ["t3code"])
        self.assertEqual(args.git_access, "read-write")
        self.assertEqual(args.git_auth_source, "active")
        self.assertEqual(args.agent_auth_source, "login")
        self.assertEqual(args.agent_config_source, "active")
        self.assertEqual(args.agent_repos, [
            "https://github.com/user/one.git",
            "https://gitlab.com/user/two.git",
        ])

    def test_agent_tool_accepts_comma_separated_values_and_opt_outs(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--agent-tool", "opencode,gh,codex",
            "--no-agent-tool", "gh",
            "--git-auth", "none",
            "--agent-auth", "none",
        ])
        self.assertEqual(args.agent_tools, ["opencode", "gh", "codex"])
        self.assertEqual(args.no_agent_tools, ["gh"])
        self.assertEqual(args.git_auth_source, "none")
        self.assertEqual(args.agent_auth_source, "none")

    def test_rdp_policy_flags(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--rdp",
            "--rdp-bind-address", "10.0.0.50",
            "--rdp-source", "10.0.0.0/24",
            "--rdp-source", "2001:db8::/64",
            "--no-rdp-clipboard",
            "--rdp-drive-redirection",
            "--rdp-audio",
            "--rdp-idle-timeout", "14400",
        ])
        self.assertEqual(args.rdp_bind_address, "10.0.0.50")
        self.assertEqual(args.rdp_allowed_sources, ["10.0.0.0/24", "2001:db8::/64"])
        self.assertFalse(args.rdp_clipboard)
        self.assertTrue(args.rdp_drive_redirection)
        self.assertTrue(args.rdp_audio)
        self.assertEqual(args.rdp_idle_timeout, 14400)

    def test_removed_desktop_policy_flags_report_migration(self):
        for flags in (["--rdp-max-sessions", "1"], ["--rdp-kill-disconnected"],
                      ["--no-rdp-kill-disconnected"], ["--rdp-disconnected-timeout", "0"]):
            with self.subTest(flags=flags):
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(SystemExit):
                    self.parser.parse_args(["vm", *flags])
                self.assertIn("removed", output.getvalue())

    def test_rdp_existing_password_flag(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--rdp",
            "--rdp-existing-password",
        ])
        self.assertTrue(args.rdp_existing_password)

    def test_antistatic_flags(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--antistatic-server",
            "lobby.example.com:9090",
            "--antistatic-admin",
            "operator",
            "--antistatic-db",
            "db.example.com:9091",
        ])
        self.assertEqual(args.antistatic_server, "lobby.example.com:9090")
        self.assertEqual(args.antistatic_admin, "operator")
        self.assertEqual(args.antistatic_db, "db.example.com:9091")

    def test_antistatic_admin_can_be_disabled(self):
        args = self.parser.parse_args(["10.0.0.50", "--no-antistatic-admin"])
        self.assertEqual(args.antistatic_admin, "")

    def test_gogs_flag_with_domain_only(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--gogs",
            "git.example.com:3000",
        ])
        self.assertEqual(args.gogs, ["git.example.com:3000"])

    def test_gogs_flag_with_optional_data_path(self):
        args = self.parser.parse_args([
            "10.0.0.50",
            "--gogs",
            "git.example.com:3000",
            "/srv/gogs",
        ])
        self.assertEqual(args.gogs, ["git.example.com:3000", "/srv/gogs"])


class TestHostedFlagsNotInRemoteParser(unittest.TestCase):
    def setUp(self):
        self.parser = create_setup_argument_parser(
            "Remote", for_remote=True
        )

    def test_no_provision_on_flag(self):
        """The --provision-on flag should not exist in the remote parser."""
        args = self.parser.parse_args([
            "--system-type", "server_lite", "--username", "root"
        ])
        self.assertFalse(hasattr(args, 'hosted_node'))

    def test_no_memory_flag(self):
        args = self.parser.parse_args([
            "--system-type", "server_lite", "--username", "root"
        ])
        self.assertFalse(hasattr(args, 'container_memory'))

    def test_no_balloon_min_flag(self):
        args = self.parser.parse_args([
            "--system-type", "server_lite", "--username", "root"
        ])
        self.assertFalse(hasattr(args, 'vm_balloon_min'))

    def test_no_vm_hardware_flags(self):
        args = self.parser.parse_args([
            "--system-type", "server_lite", "--username", "root"
        ])

        self.assertFalse(hasattr(args, "vm_cpu_type"))
        self.assertFalse(hasattr(args, "vm_disk_discard"))
        self.assertFalse(hasattr(args, "vm_disk_ssd"))
        self.assertFalse(hasattr(args, "vm_disk_backup"))
        self.assertFalse(hasattr(args, "vm_disk_settings"))

    def test_explicit_agent_tool_flags_exist_remotely(self):
        args = self.parser.parse_args([
            "--system-type", "server_dev",
            "--username", "agentuser",
            "--agent-tool", "gh",
            "--agent-tool", "codex",
            "--agent-tool", "claude",
            "--agent-tool", "opencode",
            "--web-interface", "t3code",
            "--git-access", "read",
            "--repo", "https://github.com/user/repo.git",
        ])
        self.assertEqual(args.agent_tools, ["gh", "codex", "claude", "opencode"])
        self.assertEqual(args.web_interfaces, ["t3code"])
        self.assertEqual(args.git_access, "read")
        self.assertEqual(args.agent_repos, ["https://github.com/user/repo.git"])

    def test_vm_data_declarations_exist_remotely(self):
        args = self.parser.parse_args([
            "--system-type", "server_dev",
            "--username", "agentuser",
            "--machine", "vm",
            "--storage", "agent-data", "fast-lvm", "128G",
            "--storage-mount", "agent-data", "/srv/agent-workspace", "ext4",
            "--storage", "agent-cache", "fast-lvm", "16G",
            "--storage-cache", "agent-data", "agent-cache",
            "--agent-workspace", "/srv/agent-workspace",
        ])
        self.assertEqual(
            args.container_storage,
            [
                ["agent-data", "fast-lvm", "128G"],
                ["agent-cache", "fast-lvm", "16G"],
            ],
        )
        self.assertEqual(
            args.storage_mounts,
            [["agent-data", "/srv/agent-workspace", "ext4"]],
        )
        self.assertEqual(args.storage_caches, [["agent-data", "agent-cache"]])


if __name__ == '__main__':
    unittest.main()
