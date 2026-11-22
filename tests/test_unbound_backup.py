"""Private stopped resolver recovery, corruption and identity exclusion."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common.unbound_steps import UnboundPaths
from lib import unbound_backup as backup
from lib.atomic_io import write_bytes_atomic, write_json_atomic
from lib.headscale_dns_binding import DNSBinding, read_dns_binding, save_dns_binding
from lib.unbound_config import records_document, render_unbound


class UnboundBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = UnboundPaths(str(self.root / "etc"), str(self.root / "state"),
                                  str(self.root / "defaults"), str(self.root / "dropin"))
        self.binding = DNSBinding("https://vpn.example.com", "7", ("100.64.0.2",))
        self.enterContext(patch("common.unbound_steps.grp.getgrnam", return_value=SimpleNamespace(gr_gid=os.getegid())))
        Path(self.paths.state_dir).mkdir(mode=0o700)
        Path(self.paths.fragments).mkdir(parents=True)
        self.contents = {
            "fragment": render_unbound(self.binding).encode(),
            "defaults": b"# Managed by Basaltwater\nRESOLVCONF=false\nROOT_TRUST_ANCHOR_UPDATE=true\n",
            "dropin": b"[Unit]\nAfter=network-online.target tailscaled.service\nWants=network-online.target tailscaled.service\n",
        }
        write_json_atomic(self.paths.records, records_document(()))
        self.contents["records"] = Path(self.paths.records).read_bytes()
        for name, path in backup._mapping(self.paths).items():
            write_bytes_atomic(path, self.contents[name], mode={"records": 0o600, "fragment": 0o640}.get(name, 0o644))
        write_json_atomic(self.paths.manifest, {
            "version": 1, "enabled": True, "binding": self.binding.to_dict(),
            "files": {path: hashlib.sha256(self.contents[name]).hexdigest()
                      for name, path in backup._mapping(self.paths).items()},
        })
        save_dns_binding(self.binding, expected=None, path=self.paths.binding)
        self.snapshot = self.root / "snapshot.json"
        self.mocks = {}
        for name, kwargs in {
            "require_capabilities": {}, "_observe": {"return_value": self.binding},
            "_stock_fragments": {"return_value": []},
            "run": {"return_value": SimpleNamespace(stdout="inactive\n")},
        }.items():
            mocker = patch.object(backup, name, **kwargs)
            self.mocks[name] = mocker.start()
            self.addCleanup(mocker.stop)
        mocker = patch("common.unbound_steps.run")
        mocker.start()
        self.addCleanup(mocker.stop)

    def capture(self):
        return backup.backup_unbound(self.snapshot, paths=self.paths)

    def restore(self, **kwargs):
        backup.restore_unbound(self.snapshot, expected=self.binding, paths=self.paths, **kwargs)

    def test_roundtrip_private_permissions_stopped_and_staged_before_write(self):
        self.capture()
        self.assertEqual(0o600, self.snapshot.stat().st_mode & 0o777)
        Path(self.paths.fragment).write_text("damaged")
        events = []
        original = backup._write
        with patch.object(backup, "_candidate", side_effect=lambda *a: events.append("check")), patch.object(
            backup, "_write", side_effect=lambda *a: (events.append("write"), original(*a))
        ):
            self.restore()
        self.assertEqual("check", events[0])
        self.assertEqual(self.contents["fragment"], Path(self.paths.fragment).read_bytes())
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))
        for name, path in backup._mapping(self.paths).items():
            self.assertEqual({"records": 0o600, "fragment": 0o640}.get(name, 0o644), Path(path).stat().st_mode & 0o777)
        self.assertEqual(os.getegid(), Path(self.paths.fragment).stat().st_gid)
        self.assertFalse(Path(self.paths.operation).exists())
        self.assertFalse(any(c.args[0][:2] in (["systemctl", "start"], ["systemctl", "stop"])
                             for c in self.mocks["run"].call_args_list))

    def test_corruption_rejected_before_native_or_writes(self):
        self.capture()
        doc = backup.private_json(self.snapshot)
        doc["files"]["records"]["sha256"] = "0" * 64
        write_json_atomic(str(self.snapshot), doc)
        self.mocks["run"].reset_mock()
        with patch.object(backup, "_write") as write, self.assertRaises(ValueError):
            self.restore()
        write.assert_not_called()
        self.mocks["run"].assert_not_called()

    def test_snapshot_cannot_restore_world_readable_local_inventory(self):
        self.capture()
        doc = backup.private_json(self.snapshot)
        doc["files"]["fragment"]["mode"] = 0o644
        write_json_atomic(str(self.snapshot), doc)
        with patch.object(backup, "_write_fragment") as write, self.assertRaisesRegex(ValueError, "metadata"):
            self.restore()
        write.assert_not_called()

    def test_live_service_and_changed_identity_block_capture(self):
        self.mocks["run"].return_value.stdout = "active"
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.capture()
        self.assertFalse(self.snapshot.exists())
        self.mocks["run"].return_value.stdout = "inactive"
        self.mocks["_observe"].return_value = replace(self.binding, node_id="8")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.capture()
        self.assertFalse(self.snapshot.exists())

    def test_publication_reservation_blocks_restore_under_shared_lease(self):
        self.capture()
        published = replace(self.binding, advertised=True,
                            publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        save_dns_binding(published, expected=self.binding, path=self.paths.binding)
        with patch.object(backup, "_write") as write, self.assertRaisesRegex(ValueError, "advertised/reserved"):
            self.restore()
        write.assert_not_called()
        self.assertEqual(published, read_dns_binding(self.paths.binding))

    def test_advertised_snapshot_never_replays_lease(self):
        published = replace(self.binding, advertised=True,
                            publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")
        save_dns_binding(published, expected=self.binding, path=self.paths.binding)
        self.capture()
        save_dns_binding(self.binding, expected=published, path=self.paths.binding)
        backup.restore_unbound(self.snapshot, expected=published, paths=self.paths)
        self.assertEqual(self.binding, read_dns_binding(self.paths.binding))

    def test_new_native_identity_requires_reviewed_unadvertised_rebind(self):
        self.capture()
        new = replace(self.binding, node_id="8", addresses=("100.64.0.3",))
        self.mocks["_observe"].return_value = new
        with self.assertRaisesRegex(ValueError, "explicit reviewed rebind"):
            self.restore()
        self.restore(reviewed_rebind=new)
        self.assertEqual(new, read_dns_binding(self.paths.binding))
        self.assertEqual(render_unbound(new).encode(), Path(self.paths.fragment).read_bytes())

    def test_native_candidate_failure_preserves_files_and_postwrite_failure_blocks_retry(self):
        self.capture()
        before = Path(self.paths.fragment).read_bytes()
        with patch.object(backup, "_candidate", side_effect=RuntimeError("invalid")), self.assertRaises(RuntimeError):
            self.restore()
        self.assertEqual(before, Path(self.paths.fragment).read_bytes())
        self.assertFalse(Path(self.paths.operation).exists())
        with patch.object(backup, "_write", side_effect=OSError("uncertain")), self.assertRaises(OSError):
            self.restore()
        self.assertTrue(Path(self.paths.operation).exists())
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.restore()

    def test_validation_reads_only_and_rejects_links_modes_and_overwrite(self):
        self.capture()
        with patch.object(backup, "run") as native, patch.object(backup, "dns_binding_lease") as lease:
            backup.validate_unbound_backup(self.snapshot, expected=self.binding, paths=self.paths)
        native.assert_not_called()
        lease.assert_not_called()
        with self.assertRaises(FileExistsError):
            self.capture()
        self.snapshot.chmod(0o644)
        with self.assertRaises(ValueError):
            backup.validate_unbound_backup(self.snapshot, expected=self.binding, paths=self.paths)
        link = self.root / "linked.json"
        link.symlink_to(self.snapshot)
        with self.assertRaises(OSError):
            backup.private_json(link)

    def test_rebind_rejects_advertised_and_other_network_and_preserves_restricted_mode(self):
        Path(self.paths.defaults).chmod(0o600)
        self.capture()
        for binding in (replace(self.binding, network_url="https://other.example.com"),
                        replace(self.binding, advertised=True,
                                publication_id="ecdc8544-8516-4684-be66-1721a4bcd328")):
            with self.subTest(binding=binding), self.assertRaisesRegex(ValueError, "unpublished"):
                self.restore(reviewed_rebind=binding)
        self.restore()
        self.assertEqual(0o600, Path(self.paths.defaults).stat().st_mode & 0o777)
