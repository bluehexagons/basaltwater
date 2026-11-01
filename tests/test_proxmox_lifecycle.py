"""Provisioned guest startup policy, persistence, and safe reconciliation."""

from __future__ import annotations

from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
import shlex
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import Mock, patch

import basaltwater
from lib.config import SetupConfig
from lib.proxmox_guest import ProvisionError
from lib.proxmox_lifecycle import LIFECYCLE_FIELD_FLAGS, reconcile_guest_lifecycle
from lib.proxmox_node import ContainerAlreadyExists, _create_container, _provision_container_locked
from lib.proxmox_vm import _ResolvedImage, _create_vm, _provision_vm_locked, _reconcile_existing_vm
from lib.validation import validate_guest_lifecycle_settings, validate_hosted_flags


def _config(**overrides: object) -> SetupConfig:
    values = {
        "host": "192.0.2.50", "username": "agent", "system_type": "server_lite",
        "machine_type": "vm", "hosted_node": "192.0.2.10",
        "static_ipv4": "192.0.2.50/24", "network_gateway4": "192.0.2.1",
        "network_dns": ["192.0.2.1"], "container_memory": "1G",
        "container_storage": [["root", "local", "12G"]],
    }
    values.update(overrides)
    return SetupConfig(**values)


def _result(stdout: str = "", *, error: str = "") -> CompletedProcess[str]:
    return CompletedProcess([], 1 if error else 0, stdout, error)


class TestLifecycleConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        self.parser, _, _ = basaltwater.create_basaltwater_parser()
        self.base_args = [
            "setup", "server_lite", "192.0.2.50", "agent",
            "--provision-on", "192.0.2.10",
        ]

    def test_omission_preserves_unspecified_policy(self) -> None:
        args = self.parser.parse_args(self.base_args)
        config = SetupConfig.from_args(args, args.system_type)
        for field in LIFECYCLE_FIELD_FLAGS:
            self.assertIsNone(getattr(args, field, None))
            self.assertIsNone(getattr(config, field))

    def test_explicit_false_and_zero_survive_saved_command_roundtrip(self) -> None:
        args = self.parser.parse_args(self.base_args + [
            "--no-autostart", "--start-order", "0", "--start-delay", "0",
            "--shutdown-timeout", "0", "--no-protection",
        ])
        config = SetupConfig.from_args(args, args.system_type)
        restored = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        command = shlex.split(" ".join(restored.to_setup_command()))
        reparsed = self.parser.parse_args(command[1:])
        for field in LIFECYCLE_FIELD_FLAGS:
            self.assertEqual(getattr(restored, field), getattr(config, field))
            self.assertEqual(getattr(reparsed, field), getattr(config, field))
        self.assertIs(restored.guest_autostart, False)
        self.assertIs(restored.guest_protection, False)

    def test_provider_options_are_not_forwarded_to_guest_setup(self) -> None:
        config = _config(guest_autostart=True, guest_start_order=3, guest_protection=True)
        command = " ".join(config.to_remote_args())
        for flag in LIFECYCLE_FIELD_FLAGS.values():
            self.assertNotIn(flag, command)

    def test_invalid_saved_values_are_rejected(self) -> None:
        for field, value in (
            ("guest_autostart", "false"), ("guest_protection", 1),
            ("guest_start_order", -1), ("guest_start_delay", True),
            ("guest_shutdown_timeout", "60"), ("guest_start_delay", 1.5),
        ):
            with self.subTest(field=field, value=value):
                data = _config().to_dict()
                data[field] = value
                with self.assertRaises(ValueError):
                    SetupConfig.from_dict("192.0.2.50", "server_lite", data)

    def test_all_explicit_settings_require_provisioning_even_false_and_zero(self) -> None:
        for field, value in (
            ("guest_autostart", False), ("guest_protection", False),
            ("guest_start_order", 0), ("guest_start_delay", 0),
            ("guest_shutdown_timeout", 0),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "requires --provision-on"):
                    validate_hosted_flags(_config(hosted_node=None, **{field: value}))

    def test_schedule_can_be_retained_while_autostart_is_disabled(self) -> None:
        validate_guest_lifecycle_settings(_config(guest_autostart=False, guest_start_order=2))

    def test_patch_rejects_provider_changes_before_loading_or_saving(self) -> None:
        for flag in ("--autostart", "--no-autostart", "--no-protection", "--start-delay"):
            with self.subTest(flag=flag):
                flags = [flag, "0"] if flag == "--start-delay" else [flag]
                args = self.parser.parse_args(["patch", "192.0.2.50", "agent", *flags])
                with patch("basaltwater.load_setup_command") as load, \
                     patch("basaltwater.save_setup_command") as save, \
                     patch("builtins.print"):
                    self.assertEqual(basaltwater.run_patch_command(args), 1)
                load.assert_not_called()
                save.assert_not_called()


class TestLifecycleReconciliation(unittest.TestCase):
    def reconcile(self, config: SetupConfig, runner: Mock, tool: str = "qm") -> None:
        reconcile_guest_lifecycle(
            config, tool=tool, vmid=101, node_ip="192.0.2.10",
            user="root", ssh_opts=[], ssh_run=runner,
        )

    def test_omitted_settings_do_not_contact_provider(self) -> None:
        runner = Mock()
        self.reconcile(_config(), runner)
        runner.assert_not_called()

    def test_partial_schedule_preserves_other_members_for_both_guest_types(self) -> None:
        for tool in ("qm", "pct"):
            with self.subTest(tool=tool):
                runner = Mock(side_effect=[
                    _result("onboot: 1\nstartup: 2,up=10,down=90\nprotection: 1\n"),
                    _result(),
                    _result("onboot: 1\nstartup: down=90,order=2,up=0\nprotection: 1\n"),
                ])
                self.reconcile(_config(guest_start_delay=0), runner, tool)
                self.assertEqual(shlex.split(runner.call_args_list[1].args[3]), [
                    tool, "set", "101", "--startup", "order=2,up=0,down=90",
                ])

    def test_explicit_disable_changes_only_requested_settings(self) -> None:
        runner = Mock(side_effect=[
            _result("onboot: 1\nprotection: 1\nstartup: order=2,up=30\n"),
            _result(), _result("onboot: 0\nstartup: order=2,up=30\n"),
        ])
        self.reconcile(_config(guest_autostart=False, guest_protection=False), runner)
        self.assertEqual(shlex.split(runner.call_args_list[1].args[3]), [
            "qm", "set", "101", "--onboot", "0", "--protection", "0",
        ])

    def test_verified_equal_values_do_not_write(self) -> None:
        runner = Mock(return_value=_result("onboot: 1\nstartup: 0,up=0\n"))
        self.reconcile(_config(
            guest_autostart=True, guest_protection=False, guest_start_order=0,
            guest_start_delay=0,
        ), runner)
        runner.assert_called_once()

    def test_missing_boolean_properties_mean_provider_defaults(self) -> None:
        runner = Mock(return_value=_result("name: vm-example\n"))
        self.reconcile(_config(guest_autostart=False, guest_protection=False), runner)
        runner.assert_called_once()

    def test_read_and_write_errors_stop_reconciliation(self) -> None:
        for replies, expected in (
            ([_result(error="permission denied")], "Could not inspect"),
            ([_result("onboot: 0\n"), _result(error="permission denied")], "Could not configure"),
            ([_result("onboot: 0\n"), _result(), _result(error="connection lost")], "Could not inspect"),
            ([_result("onboot: 0\n"), _result(), _result("onboot: 0\n")], "did not preserve"),
        ):
            with self.subTest(expected=expected, replies=len(replies)):
                runner = Mock(side_effect=replies)
                with self.assertRaisesRegex(ProvisionError, expected):
                    self.reconcile(_config(guest_autostart=True), runner)
                self.assertEqual(runner.call_count, len(replies))

    def test_readback_detects_loss_of_unspecified_startup_members(self) -> None:
        runner = Mock(side_effect=[
            _result("startup: order=3,down=120\n"), _result(),
            _result("startup: up=5\n"),
        ])
        with self.assertRaisesRegex(ProvisionError, "did not preserve"):
            self.reconcile(_config(guest_start_delay=5), runner)

    def test_locked_or_malformed_guest_is_not_modified(self) -> None:
        for text in (
            "lock: backup\n", "startup: order=2,unknown=5\n",
            "startup: order=2,order=3\n", "startup: up=$(id)\n",
        ):
            with self.subTest(text=text):
                runner = Mock(return_value=_result(text))
                with self.assertRaises(ProvisionError):
                    self.reconcile(_config(guest_start_delay=5), runner)
                runner.assert_called_once()


class TestLifecycleCache(unittest.TestCase):
    def test_explicit_flags_force_provider_verification_even_when_cache_matches(self) -> None:
        for machine_type in ("vm", "unprivileged"):
            for field, value in (
                ("guest_autostart", False), ("guest_protection", True),
                ("guest_start_order", 0), ("guest_start_delay", 0),
                ("guest_shutdown_timeout", 120),
            ):
                with self.subTest(machine_type=machine_type, field=field):
                    config = _config(machine_type=machine_type, **{field: value})
                    cached = _config(machine_type=machine_type, **{field: value})
                    args = Namespace(**{field: value})
                    self.assertFalse(basaltwater._reuse_cached_provisioning_metadata(
                        config, args, cached,
                    ))

    def test_omitted_settings_are_hydrated_and_explicit_values_preserved(self) -> None:
        config = _config(guest_autostart=False, guest_start_delay=0)
        cached = _config(
            guest_autostart=True, guest_start_order=3, guest_start_delay=30,
            guest_shutdown_timeout=120, guest_protection=True,
        )
        reused = basaltwater._reuse_cached_provisioning_metadata(
            config, Namespace(guest_autostart=False, guest_start_delay=0), cached,
        )
        self.assertFalse(reused)
        self.assertIs(config.guest_autostart, False)
        self.assertEqual(config.guest_start_delay, 0)
        self.assertEqual(config.guest_start_order, 3)
        self.assertEqual(config.guest_shutdown_timeout, 120)
        self.assertIs(config.guest_protection, True)

    def test_existing_vm_and_lxc_accept_lifecycle_changes(self) -> None:
        for machine_type in ("vm", "unprivileged", "privileged"):
            with self.subTest(machine_type=machine_type):
                current = _config(machine_type=machine_type, guest_autostart=False)
                cached = _config(machine_type=machine_type, guest_autostart=True)
                self.assertEqual(basaltwater._unsupported_cached_provisioning_changes(
                    current, cached, Namespace(guest_autostart=False),
                ), [])


class TestLifecycleProvisioning(unittest.TestCase):
    def test_new_lxc_forwards_policy_and_protects_only_after_ssh_bootstrap(self) -> None:
        events: list[str] = []
        defaults = {
            "auto_detect_bridge": "vmbr0", "check_container_exists": False,
            "_resolve_storage_pool": "local", "_resolve_template_storage": "local",
            "_resolve_template_name": "/tmp/template.tar.zst",
            "_resolve_public_key_path": "/tmp/test.pub",
            "_upload_pubkey_to_host": "/tmp/uploaded.pub", "_get_next_vmid": 101,
            "_wait_for_container_ssh": None, "_ssh_run": _result(),
        }
        for name, value in defaults.items():
            self.enterContext(patch(f"lib.proxmox_node.{name}", return_value=value))
        self.enterContext(patch(
            "lib.proxmox_node.remote_proxmox_locks", side_effect=lambda *a, **kw: nullcontext(),
        ))
        self.enterContext(patch(
            "lib.proxmox_node.enroll_provisioned_guest_host_keys",
            side_effect=lambda *a, **kw: events.append("enroll"),
        ))
        self.enterContext(patch(
            "lib.proxmox_node.reconcile_guest_lifecycle",
            side_effect=lambda *a, **kw: events.append("policy"),
        ))
        with patch("lib.proxmox_node._create_container") as create:
            _provision_container_locked(_config(
                machine_type="unprivileged", guest_autostart=False,
                guest_start_delay=0, guest_protection=True,
            ))
        self.assertIs(create.call_args.kwargs["autostart"], False)
        self.assertEqual(create.call_args.kwargs["startup"], "up=0")
        self.assertEqual(events, ["enroll", "policy"])

    def test_creating_vm_still_starts_it_when_future_autostart_is_disabled(self) -> None:
        with patch("lib.proxmox_vm._ssh_run", return_value=_result()) as run:
            _create_vm(
                vmid=101, target_ip="192.0.2.50", image_remote_path=None,
                storage_ref="local:import/test.img", memory_mb=1024,
                balloon_min_mb=1024, cores=1, root_pool="local", disk_size_gib=12,
                data_disk_specs=[], cidr_prefix="24", bridge="vmbr0",
                gateway="192.0.2.1", nameservers=["192.0.2.1"], hostname="example",
                user_data_path=None, user_data_ref=None, graphical_console=False,
                node_ip="192.0.2.10", user="root", ssh_opts=[], dry_run=True,
                autostart=False, startup="order=2,up=0,down=90",
            )
        create_command = run.call_args_list[0].args[3]
        self.assertIn("--onboot 0", create_command)
        self.assertIn("--startup order=2,up=0,down=90", create_command)
        self.assertNotIn("--protection", create_command)
        self.assertEqual(run.call_args_list[-1].args[3], "qm start 101")

    def test_creating_lxc_still_starts_it_when_future_autostart_is_disabled(self) -> None:
        with patch("lib.proxmox_node._ssh_run", return_value=_result()) as run:
            _create_container(
                vmid=101, target_ip="192.0.2.50", template_path="/tmp/template.tar.zst",
                memory_mb=1024, cores=1, root_pool="local", storage_amount="12G",
                cidr_prefix="24", bridge="vmbr0", gateway="192.0.2.1",
                nameservers=["192.0.2.1"], hostname="example", node_ip="192.0.2.10",
                user="root", ssh_opts=[], dry_run=True, autostart=False,
                startup="order=2,up=0,down=90",
            )
        command = run.call_args.args[3]
        self.assertIn("--onboot 0", command)
        self.assertIn("--start 1", command)
        self.assertIn("--startup order=2,up=0,down=90", command)
        self.assertNotIn("--protection", command)

    def test_existing_lxc_reconciles_only_after_identity_verification(self) -> None:
        config = _config(machine_type="unprivileged", guest_autostart=False)
        with patch("lib.proxmox_node.auto_detect_bridge", return_value="vmbr0"), \
             patch("lib.proxmox_node.check_container_exists", return_value=True), \
             patch("lib.proxmox_node._ensure_container_running_locked", return_value=101) as verify, \
             patch("lib.proxmox_node.reconcile_guest_lifecycle") as reconcile:
            with self.assertRaises(ContainerAlreadyExists):
                _provision_container_locked(config)
            verify.assert_called_once()
            self.assertEqual(reconcile.call_args.kwargs["vmid"], 101)
            self.assertEqual(reconcile.call_args.kwargs["tool"], "pct")
            verify.side_effect = ProvisionError("identity mismatch")
            reconcile.reset_mock()
            with self.assertRaisesRegex(ProvisionError, "identity mismatch"):
                _provision_container_locked(config)
            reconcile.assert_not_called()

    def test_existing_vm_policy_is_applied_after_identity_and_reachability_checks(self) -> None:
        config_text = "name: example\nonboot: 1\nipconfig0: ip=192.0.2.50/24,gw=192.0.2.1\n"
        replies = [
            _result("VMID NAME STATUS\n101 example running\n"),
            _result(config_text), _result("status: running\n"), _result("READY\n"),
            _result(config_text), _result(), _result(config_text.replace("onboot: 1", "onboot: 0")),
        ]
        with patch("lib.proxmox_vm._ssh_run", side_effect=replies) as run:
            self.assertTrue(_reconcile_existing_vm(
                "192.0.2.10", "192.0.2.50", "example", "root", [],
                lifecycle_config=_config(guest_autostart=False), require_existing_name=True,
            ))
        self.assertEqual(run.call_args_list[5].args[3], "qm set 101 --onboot 0")

    def test_existing_vm_identity_mismatch_never_changes_policy(self) -> None:
        with patch("lib.proxmox_vm._ssh_run", side_effect=[
            _result("VMID NAME STATUS\n101 other running\n"),
            _result("name: other\nipconfig0: ip=192.0.2.50/24,gw=192.0.2.1\n"),
        ]) as run:
            with self.assertRaisesRegex(ProvisionError, "expected saved name"):
                _reconcile_existing_vm(
                    "192.0.2.10", "192.0.2.50", "example", "root", [],
                    lifecycle_config=_config(guest_autostart=False), require_existing_name=True,
                )
        self.assertEqual(run.call_count, 2)


class TestProtectionBootstrap(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        public_key = Path(directory) / "id.pub"
        public_key.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
        defaults = {
            "_resolve_public_key_path": str(public_key),
            "_resolve_image": (_ResolvedImage(None, None, None, "local:import/test.img"), None),
            "auto_detect_bridge": "vmbr0", "_reconcile_existing_vm": False,
            "_resolve_storage_pool": "local", "_upload_user_data": "/tmp/test.yaml",
            "_report_memory_capacity": True, "_preflight_data_disk_capacity": None,
            "_get_next_vmid": 101, "_create_vm": True,
            "_wait_for_guest_agent": None, "_wait_for_guest_ssh": None,
            "enroll_provisioned_guest_host_keys": None,
        }
        for name, value in defaults.items():
            self.enterContext(patch(f"lib.proxmox_vm.{name}", return_value=value))
        self.enterContext(patch(
            "lib.proxmox_vm.remote_proxmox_locks", side_effect=lambda *a, **kw: nullcontext(),
        ))
        self.events: list[str] = []
        self.bootstrap = self.enterContext(patch(
            "lib.proxmox_vm._verify_cloud_init_bootstrap",
            side_effect=lambda *a, **kw: self.events.append("bootstrap verified"),
        ))
        self.protected = False
        self.runner = self.enterContext(patch("lib.proxmox_vm._ssh_run", side_effect=self.run_ssh))
        self.config = _config(guest_protection=True)

    def run_ssh(self, node: str, user: str, opts: list[str], command: str, **kw: object) -> CompletedProcess[str]:
        self.events.append(command)
        if command == "qm set 101 --protection 1":
            self.protected = True
        return _result(f"onboot: 1\nprotection: {int(self.protected)}\n")

    def test_protection_is_applied_and_verified_after_bootstrap(self) -> None:
        _provision_vm_locked(self.config)
        self.assertGreater(
            self.events.index("qm set 101 --protection 1"),
            self.events.index("bootstrap verified"),
        )
        self.assertEqual(self.events[-1], "qm config 101")

    def test_new_vm_forwards_explicit_false_and_zero_policy(self) -> None:
        self.config.guest_autostart = False
        self.config.guest_start_delay = 0
        with patch("lib.proxmox_vm._create_vm") as create, \
             patch("lib.proxmox_vm.reconcile_guest_lifecycle"):
            _provision_vm_locked(self.config)
        self.assertIs(create.call_args.kwargs["autostart"], False)
        self.assertEqual(create.call_args.kwargs["startup"], "up=0")

    def test_failed_bootstrap_can_destroy_unprotected_vm(self) -> None:
        self.bootstrap.side_effect = ProvisionError("bootstrap failed")
        with self.assertRaisesRegex(ProvisionError, "bootstrap failed"):
            _provision_vm_locked(self.config)
        self.assertIn("qm destroy 101 --purge 1 --skiplock 1", self.events)
        self.assertFalse(self.protected)

    def test_failed_policy_application_preserves_bootstrapped_guest(self) -> None:
        def fail_protection(*args: object, **kwargs: object) -> CompletedProcess[str]:
            if "--protection" in args[3]:
                return _result(error="permission denied")
            return self.run_ssh(*args, **kwargs)

        self.runner.side_effect = fail_protection
        with self.assertRaisesRegex(ProvisionError, "Could not configure"):
            _provision_vm_locked(self.config)
        self.assertIn("bootstrap verified", self.events)
        self.assertFalse(any("qm destroy" in command for command in self.events))


if __name__ == "__main__":
    unittest.main()
