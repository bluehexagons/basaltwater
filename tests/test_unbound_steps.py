"""Unbound activation, failure rollback and publication lifecycle exclusion."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import unbound_steps as steps
from lib.atomic_io import read_json_file, write_json_atomic
from lib.config import SetupConfig
from lib.headscale_dns_binding import DNSBinding, read_dns_binding, save_dns_binding
from lib.operation_state import OperationStateError, OperationStateStore
from lib import unbound_backup as recovery
from lib.state_read import StateReadError
from lib.unbound_config import LocalRecord, health_identity


class UnboundStepsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = temporary.name
        self.paths = steps.UnboundPaths(base + "/etc/unbound", base + "/state",
                                       base + "/etc/default/unbound", base + "/units/unbound.service.d/overlay.conf")
        self.config = SetupConfig(host="dns.example.com", username="agent", system_type="server_lite",
                                  tailscale_login_server="https://vpn.example.com", unbound=True,
                                  headscale_base_domain="mesh.example.net")
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",))
        self.installed = False
        self.active = False
        self.enabled = False
        self.rules = []
        self.commands = []
        self.fail_health = 0
        self.fail_candidate = False
        self.fail_stop = False
        self.fail_install = None
        for name, kwargs in (
            ("_preflight", {"return_value": None}),
            ("_observe", {"return_value": self.binding}),
            ("is_package_installed", {"side_effect": lambda package: self.installed}),
            ("inspect_unit_state", {"side_effect": self.state}),
            ("run", {"side_effect": self.command}),
        ):
            mock = patch.object(steps, name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def state(self, *_args):
        return {"LoadState": "loaded" if self.installed else "not-found",
                "ActiveState": "active" if self.active else "inactive", "SubState": "running" if self.active else "dead",
                "UnitFileState": "enabled" if self.enabled else "disabled" if self.installed else ""}

    def package(self):
        Path(self.paths.fragments).mkdir(parents=True)
        Path(self.paths.main).write_text(f'include-toplevel: "{self.paths.fragments}/*.conf"\n')
        Path(self.paths.main).chmod(0o644)
        for name, content in {
            "root-auto-trust-anchor-file.conf": 'server:\n auto-trust-anchor-file: "/var/lib/unbound/root.key"\n',
            "remote-control.conf": "remote-control:\n control-enable: yes\n control-interface: /run/unbound.ctl\n",
        }.items():
            Path(self.paths.fragments, name).write_text(content)
            Path(self.paths.fragments, name).chmod(0o644)
        self.installed = True

    def command(self, command, **_kwargs):
        self.commands.append(command)
        output = ""
        if command[:2] == ["apt-get", "update"] and self.fail_install == "update":
            raise RuntimeError("metadata refresh failed")
        if command[:2] == ["apt-get", "install"]:
            self.assertIn("RESOLVCONF=false", Path(self.paths.defaults).read_text())
            if self.fail_install == "before":
                raise RuntimeError("failed install")
            self.package()
            if self.fail_install == "after":
                raise RuntimeError("failed install")
        elif command[:3] == ["ufw", "status", "numbered"]:
            output = "Status: active\n" + "".join(f"[{i}] 53 ALLOW IN # {comment}\n" for i, comment in enumerate(self.rules, 1))
        elif command[:2] == ["ufw", "allow"]:
            if command[-1] not in self.rules:
                self.rules.append(command[-1])
        elif command[:3] == ["ufw", "--force", "delete"]:
            self.rules.pop(int(command[3]) - 1)
        elif command[:3] == ["systemctl", "show", steps.SERVICE]:
            if "--property=FragmentPath,DropInPaths" in command:
                dropin = self.paths.dropin if Path(self.paths.dropin).exists() else ""
                output = "FragmentPath=/usr/lib/systemd/system/unbound.service\nDropInPaths=" + dropin + "\n"
            else:
                output = "42\n" if self.active else "0\n"
        elif command[:2] == ["systemctl", "stop"]:
            if self.fail_stop:
                raise RuntimeError("uncertain stop")
            self.active = False
        elif command[:2] == ["systemctl", "restart"]:
            self.active = True
        elif command[:2] == ["systemctl", "enable"]:
            self.enabled = True
        elif command[:2] == ["systemctl", "disable"]:
            self.enabled = False
            if "--now" in command:
                self.active = False
        elif command[0] == "unbound-checkconf" and ".candidate-" in command[1] and self.fail_candidate:
            raise RuntimeError("invalid candidate")
        elif command[0] == "dig":
            if "TXT" in command:
                output = '"' + health_identity(self.binding) + '"\n'
            elif self.fail_health:
                self.fail_health -= 1
                output = ";; ->>HEADER<<- status: SERVFAIL\n;; flags: qr rd ra;\n"
            else:
                output = ";; ->>HEADER<<- status: NOERROR\n;; flags: qr rd ra ad;\n"
                if "dnssec-failed.org." in command and "+cdflag" not in command:
                    output = ";; ->>HEADER<<- status: SERVFAIL\n;; flags: qr rd ra;\n"
                else:
                    output += ". 300 IN DNSKEY 257 3 8 example\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    def configure(self, config=None, **kwargs):
        steps.configure_unbound(config or self.config, paths=self.paths, **kwargs)

    def test_fresh_setup_validates_before_publish_and_preserves_native_bootstrap(self):
        self.configure()
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))
        manifest = read_json_file(self.paths.manifest)
        self.assertTrue(manifest["enabled"])
        self.assertTrue(self.active and self.enabled)
        self.assertEqual(2, len(self.rules))
        first_check = next(i for i, command in enumerate(self.commands) if command[0] == "unbound-checkconf")
        first_start = self.commands.index(["systemctl", "restart", steps.SERVICE])
        self.assertLess(first_check, first_start)
        self.assertLess(self.commands.index(["systemctl", "mask", "--runtime", steps.SERVICE]),
                        next(i for i, command in enumerate(self.commands) if command[0] == "apt-get"))
        self.assertIn("RESOLVCONF=false", Path(self.paths.defaults).read_text())
        self.assertNotIn("nameserver", Path(self.paths.defaults).read_text())
        self.assertFalse(Path(self.paths.operation).exists())
        self.assertFalse(Path(self.paths.backup).exists())
        update = next(i for i, command in enumerate(self.commands) if command[:2] == ["apt-get", "update"])
        install = next(i for i, command in enumerate(self.commands) if command[:2] == ["apt-get", "install"])
        self.assertLess(update, install)
        self.assertEqual(1, sum(command[:2] == ["apt-get", "install"] for command in self.commands))

    def test_fresh_package_failures_always_retain_blocking_identity_and_snapshot(self):
        for failure in ("update", "before", "after", "health"):
            with self.subTest(failure=failure):
                # Each variant needs an independent absent installation.
                with tempfile.TemporaryDirectory() as root:
                    self.paths = steps.UnboundPaths(root + "/etc/unbound", root + "/state",
                                                   root + "/defaults", root + "/dropin")
                    self.installed = self.active = self.enabled = False
                    self.rules, self.commands = [], []
                    self.fail_install = failure
                    self.fail_health = int(failure == "health")
                    with self.assertRaisesRegex(RuntimeError, "fresh package recovery required"):
                        self.configure()
                    operation = read_json_file(self.paths.operation)
                    self.assertEqual("recovery_required", operation["status"])
                    self.assertEqual(self.binding.to_dict(), operation["context"]["fresh_binding"])
                    self.assertEqual(hashlib.sha256(Path(self.paths.backup).read_bytes()).hexdigest(),
                                     operation["context"]["backup_sha256"])
                    self.assertFalse(self.active or self.enabled)
                    self.assertIsNone(read_dns_binding(self.paths.binding))
                    self.assertEqual(0o600, Path(self.paths.backup).stat().st_mode & 0o777)
                    before = list(self.commands)
                    with self.assertRaises(OperationStateError):
                        self.configure()
                    self.assertEqual(before, self.commands)
                    if failure in {"update", "before"}:
                        self.assertNotIn(["systemctl", "stop", steps.SERVICE], self.commands)
                    if failure == "update":
                        self.assertFalse(any(command[:2] == ["apt-get", "install"] for command in self.commands))
                        self.assertIn(["systemctl", "unmask", "--runtime", steps.SERVICE], self.commands)

    def test_exact_fresh_recovery_and_successful_new_attempt(self):
        self.fail_install = "before"
        with self.assertRaises(RuntimeError):
            self.configure()
        review = dict(operation=read_json_file(self.paths.operation), binding=self.binding.to_dict(),
                      backup_sha256=hashlib.sha256(Path(self.paths.backup).read_bytes()).hexdigest())
        status = subprocess.CompletedProcess([], 1, "", "dpkg-query: no packages found matching unbound")
        with patch.object(recovery, "require_capabilities"), patch.object(recovery, "_observe", return_value=self.binding), patch.object(
            recovery, "inspect_unit_state", side_effect=self.state
        ), patch.object(recovery, "run", return_value=status), patch.object(recovery, "_ports"), patch.object(recovery, "_rules", return_value=[]):
            stale = dict(review, operation=dict(review["operation"], phase="changed"))
            with self.assertRaisesRegex(ValueError, "changed"):
                recovery.recover_fresh_unbound(reviewed=stale, paths=self.paths)
            self.installed = True
            with self.assertRaisesRegex(ValueError, "quiescent"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            self.installed = False
            recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
        self.assertFalse(Path(self.paths.operation).exists())
        self.assertTrue(list(Path(self.paths.state_dir).glob("recovery.json.*.resolved")))
        self.fail_install = None
        self.configure()
        self.assertTrue(self.active)

    def test_fresh_recovery_preserves_concurrent_and_published_state(self):
        self.fail_install = "before"
        with self.assertRaises(RuntimeError):
            self.configure()
        review = dict(operation=read_json_file(self.paths.operation), binding=self.binding.to_dict(),
                      backup_sha256=hashlib.sha256(Path(self.paths.backup).read_bytes()).hexdigest())
        status = subprocess.CompletedProcess([], 0, "not-installed", "")
        with patch.object(recovery, "require_capabilities"), patch.object(recovery, "_observe", return_value=self.binding) as observe, patch.object(
            recovery, "inspect_unit_state", side_effect=self.state
        ), patch.object(recovery, "run", return_value=status), patch.object(recovery, "_ports"), patch.object(recovery, "_rules", return_value=[]) as rules:
            observe.return_value = replace(self.binding, node_id="8")
            with self.assertRaisesRegex(ValueError, "identity changed"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            observe.return_value = self.binding
            status.stdout = "half-configured"
            with self.assertRaisesRegex(ValueError, "uncertain"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            status.stdout = "config-files"
            with self.assertRaisesRegex(ValueError, "uncertain"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            status.stdout = "not-installed"
            Path(self.paths.config_dir).mkdir(parents=True, exist_ok=True)
            Path(self.paths.main).write_text("operator configuration")
            Path(self.paths.main).chmod(0o644)
            with self.assertRaisesRegex(ValueError, "files differ"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            self.assertEqual("operator configuration", Path(self.paths.main).read_text())
            Path(self.paths.main).unlink()
            owner = OperationStateStore(self.paths.operation)
            with owner.ownership(), self.assertRaises(OperationStateError):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            published = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
            save_dns_binding(published, expected=None, path=self.paths.binding)
            with self.assertRaisesRegex(ValueError, "publication"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            self.assertEqual(published, read_dns_binding(self.paths.binding))
            # Independent withdrawal, while retaining identity, still blocks reset.
            save_dns_binding(self.binding, expected=published, path=self.paths.binding)
            with self.assertRaises(ValueError):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
        self.assertTrue(Path(self.paths.operation).exists())
        self.assertTrue(Path(self.paths.backup).exists())

    def test_legacy_fresh_failure_exact_review_and_changed_evidence(self):
        self.fail_install = "before"
        with self.assertRaises(RuntimeError):
            self.configure()
        operation = read_json_file(self.paths.operation)
        operation["context"] = {"backup": self.paths.backup}
        operation["phase"] = "rollback-failed"
        write_json_atomic(self.paths.operation, operation)
        review = dict(operation=operation, binding=self.binding.to_dict(),
                      backup_sha256=hashlib.sha256(Path(self.paths.backup).read_bytes()).hexdigest())
        status = subprocess.CompletedProcess([], 1, "", "dpkg-query: no packages found matching unbound")
        with patch.object(recovery, "require_capabilities"), patch.object(recovery, "_observe", return_value=self.binding), patch.object(
            recovery, "inspect_unit_state", side_effect=self.state
        ), patch.object(recovery, "run", return_value=status), patch.object(recovery, "_ports"), patch.object(recovery, "_rules", return_value=[]) as rules:
            def concurrent_change():
                changed = dict(operation, phase="concurrent-change")
                write_json_atomic(self.paths.operation, changed)
                return []
            rules.side_effect = concurrent_change
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
            self.assertTrue(Path(self.paths.backup).exists())
            write_json_atomic(self.paths.operation, operation)
            rules.side_effect = None
            recovery.recover_fresh_unbound(reviewed=review, paths=self.paths)
        self.assertFalse(Path(self.paths.operation).exists())

    def test_rerun_preserves_identity_records_and_unrelated_files(self):
        record = LocalRecord("lan.example.net", "files.lan.example.net", "A", "192.0.2.5")
        self.configure(records=(record,))
        unrelated = Path(self.paths.fragments, "operator-notes.txt")
        unrelated.write_text("preserve operator notes")
        before = Path(self.paths.records).read_bytes()
        self.commands.clear()
        self.configure()
        self.assertEqual(before, Path(self.paths.records).read_bytes())
        self.assertEqual("preserve operator notes", unrelated.read_text())
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))
        self.assertFalse(any(command[0] == "apt-get" for command in self.commands))

    def test_bad_candidate_preserves_working_configuration_without_restart(self):
        self.configure()
        before = Path(self.paths.fragment).read_bytes()
        self.commands.clear()
        self.fail_candidate = True
        with self.assertRaisesRegex(RuntimeError, "invalid candidate"):
            self.configure()
        self.assertEqual(before, Path(self.paths.fragment).read_bytes())
        self.assertTrue(self.active)
        self.assertNotIn(["systemctl", "restart", steps.SERVICE], self.commands)

    def test_failed_activation_restores_working_records_and_identity(self):
        self.configure()
        before = {path: Path(path).read_bytes() for path in steps._files(self.paths)}
        self.fail_health = 1
        with self.assertRaisesRegex(RuntimeError, "DNSSEC"):
            self.configure(records=(LocalRecord("lan.example.net", "new.lan.example.net", "A", "192.0.2.8"),))
        self.assertTrue(self.active and self.enabled)
        for path, raw in before.items():
            self.assertEqual(raw, Path(path).read_bytes())
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))
        self.assertFalse(Path(self.paths.operation).exists())

    def test_uncertain_stop_retains_candidate_and_backup_and_blocks_retry(self):
        self.configure()
        self.fail_health, self.fail_stop = 1, True
        with self.assertRaisesRegex(RuntimeError, "recovery required"):
            self.configure(records=(LocalRecord("lan.example.net", "new.lan.example.net", "A", "192.0.2.8"),))
        self.assertTrue(Path(self.paths.backup).exists())
        self.assertEqual("recovery_required", read_json_file(self.paths.operation)["status"])
        self.assertIn("new.lan.example.net", Path(self.paths.fragment).read_text())
        with self.assertRaises((OperationStateError, StateReadError)):
            self.configure()

    def test_published_dns_cannot_disable_or_silently_rebind(self):
        self.configure()
        published = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        save_dns_binding(published, expected=self.binding, path=self.paths.binding)
        self.commands.clear()
        with self.assertRaisesRegex(ValueError, "Withdraw or replace"):
            self.configure(replace(self.config, unbound=False))
        self.assertFalse(self.commands)
        with patch.object(steps, "_observe", return_value=replace(self.binding, node_id="8")):
            with self.assertRaisesRegex(ValueError, "withdraw"):
                self.configure(expected_binding=published)
        self.assertEqual(published, read_dns_binding(self.paths.binding))

    def test_disable_preserves_files_and_node_binding(self):
        self.configure()
        before = Path(self.paths.records).read_bytes()
        self.configure(replace(self.config, unbound=False))
        self.assertFalse(self.active or self.enabled)
        self.assertFalse(self.rules)
        self.assertEqual(before, Path(self.paths.records).read_bytes())
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))

    def test_drift_and_unmanaged_installation_fail_before_mutation(self):
        self.installed = True
        with self.assertRaisesRegex(ValueError, "adoption"):
            self.configure()
        self.assertFalse(self.commands)
        self.installed = False
        self.configure()
        Path(self.paths.fragment).write_text("server:\n interface: 0.0.0.0\n")
        self.commands.clear()
        with self.assertRaises(StateReadError):
            self.configure()
        self.assertFalse(self.commands)

    def test_dry_run_and_omission_do_not_create_state_or_run_commands(self):
        self.configure(replace(self.config, dry_run=True))
        self.configure(replace(self.config, unbound=None))
        self.assertFalse(Path(self.paths.state_dir).exists())
        self.assertFalse(self.commands)

    def test_port_observation_does_not_adopt_foreign_or_wildcard_listeners(self):
        for endpoint in ("0.0.0.0", "100.64.0.2", "[::]"):
            result = subprocess.CompletedProcess([], 0, f'udp UNCONN 0 0 {endpoint}:53 0.0.0.0:* users:(("other",pid=99,fd=7))\n', "")
            with patch.object(steps, "run", return_value=result), self.assertRaisesRegex(ValueError, "conflicts"):
                steps._ports(self.binding, managed=False)
