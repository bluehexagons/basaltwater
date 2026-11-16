"""Inventory CAS, native identity checks and the existing rollback boundary."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import unbound_steps as steps
from lib import unbound_records as records
from lib.atomic_io import read_json_file
from lib.headscale_dns_binding import read_dns_binding, save_dns_binding
from lib.operation_state import OperationStateError
from tests import test_unbound_steps as fixtures


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.lab = fixtures.UnboundStepsTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.lab.configure()
        self.paths = self.lab.paths
        self.binding = self.lab.binding
        self.status = dict(BackendState="Running", Self=dict(ID="7", HostName="dns", Online=True,
                           TailscaleIPs=["100.64.0.2"]),
                           CurrentTailnet=dict(MagicDNSEnabled=True, MagicDNSSuffix="mesh.example.net"))
        for name, kwargs in (
            ("require_private_daemon", {}),
            ("owned", {"return_value": dict(phase="enrolled", node_id="7")}),
            ("snapshot", {"side_effect": lambda: (self.status, {"ControlURL": self.binding.network_url})}),
        ):
            mock = patch("lib.headscale_client." + name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.lab.commands.clear()
        root = patch.object(records, "os", SimpleNamespace(geteuid=lambda: 0))
        root.start()
        self.addCleanup(root.stop)

    def review(self, entries=None, **changes):
        value = dict(version=1, binding=read_dns_binding(self.paths.binding).to_dict(),
                     records_sha256=hashlib.sha256(Path(self.paths.records).read_bytes()).hexdigest(),
                     magic_dns_suffix="mesh.example.net", onboarding_domain="join.example.com",
                     inventory=dict(version=1, records=entries or []))
        return records.parse_records_update(dict(value, **changes))

    def entry(self, name="files", value="192.0.2.8", zone="lan.example.net"):
        return dict(zone=zone, name=name + "." + zone, type="A", value=value, ttl=300)

    def apply(self, update):
        records.replace_records(update, paths=self.paths)

    def test_add_replace_remove_preserves_binding_publication_and_unrelated_files(self):
        published = replace(self.binding, advertised=True, publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        save_dns_binding(published, expected=self.binding, path=self.paths.binding)
        note = Path(self.paths.state_dir, "notes")
        note.write_text("preserve")
        for entries in ([self.entry()], [self.entry("new")], []):
            self.apply(self.review(entries))
            self.assertEqual(len(entries), records.inspect_records(expected=published, paths=self.paths)["record_count"])
            self.assertEqual(published, read_dns_binding(self.paths.binding))
            self.assertEqual("preserve", note.read_text())
            self.assertTrue(steps._manifest(self.paths)["enabled"])
        self.assertFalse(any(cmd[0] == "apt-get" for cmd in self.lab.commands))

    def test_stale_digest_or_publication_guard_refused_before_writes(self):
        stale = self.review([self.entry()])
        self.apply(self.review([self.entry("other")]))
        before = Path(self.paths.records).read_bytes()
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            self.apply(stale)
        update = self.review()
        save_dns_binding(replace(self.binding, advertised=True,
                         publication_id="ecdc8544-8516-4684-be66-1721a4bcd328"),
                         expected=self.binding, path=self.paths.binding)
        with self.assertRaises(ValueError):
            self.apply(update)
        self.assertEqual(before, Path(self.paths.records).read_bytes())
        self.assertFalse(Path(self.paths.operation).exists())

    def test_wrong_suffix_and_changed_identity_refused(self):
        update = self.review([self.entry()])
        for change in ("suffix", "node", "address", "privacy"):
            with self.subTest(change=change):
                with patch("lib.headscale_client.require_private_daemon") as privacy:
                    if change == "suffix":
                        self.status["CurrentTailnet"]["MagicDNSSuffix"] = "other.example.net"
                    elif change == "node":
                        self.status["Self"]["ID"] = "8"
                    elif change == "address":
                        self.status["Self"]["TailscaleIPs"] = ["100.64.0.3"]
                    else:
                        privacy.side_effect = ValueError("privacy unavailable")
                    with self.assertRaises(ValueError):
                        self.apply(update)
                self.status["Self"].update(ID="7", TailscaleIPs=["100.64.0.2"])
                self.status["CurrentTailnet"]["MagicDNSSuffix"] = "mesh.example.net"
        self.assertFalse(Path(self.paths.operation).exists())

    def test_collision_and_bounded_schema(self):
        for zone in ("mesh.example.net", "vpn.example.com", "join.example.com", "basaltwater-resolver.invalid"):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                self.review([self.entry(zone=zone)])
        with self.assertRaises(ValueError):
            self.review([dict(self.entry(), type="TXT")])

    def test_second_observation_cannot_authorize_rebind(self):
        update = self.review([self.entry()])
        with patch.object(steps, "_observe", return_value=replace(self.binding, node_id="8")):
            with self.assertRaisesRegex(ValueError, "cannot rebind"):
                self.apply(update)
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))
        self.assertFalse(Path(self.paths.operation).exists())

    def test_unit_override_refused_before_transaction(self):
        with patch.object(steps, "_unit_configuration", side_effect=ValueError("unowned unit")):
            with self.assertRaisesRegex(ValueError, "unowned unit"):
                self.apply(self.review([self.entry()]))
        self.assertFalse(Path(self.paths.operation).exists())

    def test_disabled_or_unmanaged_refused(self):
        update = self.review([self.entry()])
        self.lab.enabled = False
        with self.assertRaises(ValueError):
            self.apply(update)
        self.lab.enabled = True
        self.lab.configure(replace(self.lab.config, unbound=False))
        with self.assertRaises(ValueError):
            self.apply(update)
        Path(self.paths.manifest).unlink()
        with self.assertRaises(ValueError):
            self.apply(update)
        self.assertFalse(self.lab.enabled)

    def test_activation_failure_restores_inventory_and_manifest(self):
        before = {path: Path(path).read_bytes() for path in (*steps._files(self.paths), self.paths.manifest)}
        self.lab.fail_health = 1
        with self.assertRaises(RuntimeError):
            self.apply(self.review([self.entry()]))
        for path, raw in before.items():
            self.assertEqual(raw, Path(path).read_bytes())
        self.assertFalse(Path(self.paths.operation).exists())
        self.assertFalse(Path(self.paths.backup).exists())
        self.assertTrue(self.lab.active and self.lab.enabled)

    def test_inspect_read_only_and_corruption_preserved(self):
        before = set(Path(self.paths.state_dir).iterdir())
        with patch.object(steps, "dns_binding_lease", side_effect=AssertionError("lock")), patch.object(
                steps, "run", side_effect=AssertionError("native")):
            result = records.inspect_records(expected=self.binding, paths=self.paths)
        self.assertFalse(result["readiness_claim"])
        self.assertEqual(before, set(Path(self.paths.state_dir).iterdir()))
        Path(self.paths.records).write_text("corrupt")
        with self.assertRaises(ValueError):
            records.inspect_records(expected=self.binding, paths=self.paths)
        self.assertEqual("corrupt", Path(self.paths.records).read_text())

    def test_uncertain_rollback_keeps_candidate_and_blocks_replacement(self):
        update = self.review([self.entry()])
        self.lab.fail_health, self.lab.fail_stop = 1, True
        with self.assertRaisesRegex(RuntimeError, "recovery required"):
            self.apply(update)
        self.assertTrue(Path(self.paths.backup).exists())
        self.assertEqual("recovery_required", read_json_file(self.paths.operation)["status"])
        self.assertIn("files.lan.example.net", Path(self.paths.fragment).read_text())
        with self.assertRaises(ValueError):
            records.inspect_records(expected=self.binding, paths=self.paths)
        with self.assertRaises(OperationStateError):
            self.apply(update)
