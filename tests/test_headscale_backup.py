"""Consistent private snapshots and upgrade recovery, using temporary SQLite."""

from __future__ import annotations

from dataclasses import asdict
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.atomic_io import write_json_atomic
from lib import headscale_backup as backup
from lib import headscale_server as server
from tests.test_headscale_server import configuration, fixture_paths


class HeadscaleBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = fixture_paths(self.root)
        for directory in (self.paths.etc, self.paths.data, self.paths.private):
            directory.mkdir(mode=0o700)
        self.manifest = server.manifest_for(configuration())
        write_json_atomic(str(self.paths.manifest), asdict(self.manifest))
        self.paths.config.write_text(server.render_config(configuration(), self.paths))
        (self.paths.etc / "policy.hujson").write_text('{"acls": []}')
        for key in ("noise_private.key", "derp_server_private.key"):
            (self.paths.data / key).write_text("private-fixture-key")
        with closing(sqlite3.connect(self.paths.data / "db.sqlite")) as db, db:
            db.execute("create table fixture (value text)")
            db.execute("insert into fixture values ('original')")
        for path in backup.snapshot_files(self.paths).values():
            path.chmod(0o600)
        self.snapshot = self.root / "snapshot"
        self.addCleanup(patch.stopall)
        patch.object(backup, "stopped").start()
        patch.object(backup, "require_capabilities").start()
        patch.object(backup, "configtest").start()
        patch.object(server.os, "fchown").start()
        patch.object(backup, "installed_version", return_value="0.29.7").start()

    def capture(self):
        backup.backup_headscale(self.snapshot, paths=self.paths)

    def test_private_snapshot_roundtrip_and_policy_preservation(self):
        self.capture()
        contents = backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
        self.assertEqual(contents["policy.hujson"], b'{"acls": []}')
        self.assertEqual(set(contents), set(backup.snapshot_files(self.paths)))
        self.assertEqual(self.snapshot.stat().st_mode & 0o777, 0o700)
        for path in self.snapshot.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.snapshot / "RECOVERY.txt").exists())

    def test_wal_snapshot_captures_committed_data(self):
        with closing(sqlite3.connect(self.paths.data / "db.sqlite")) as db, db:
            db.execute("pragma journal_mode=wal")
            db.execute("insert into fixture values ('wal')")
            db.commit()
            self.capture()
            with closing(sqlite3.connect(self.snapshot / "db.sqlite")) as copy:
                self.assertEqual(copy.execute("select count(*) from fixture").fetchone()[0], 2)

    def test_mismatched_version_endpoint_corruption_and_permissions_refused(self):
        self.capture()
        for expected in (server.ServerManifest(1, "0.30.1", self.manifest.endpoint, self.manifest.base_domain),
                         server.ServerManifest(1, self.manifest.version, "https://other.example.com", self.manifest.base_domain)):
            with self.assertRaises(ValueError):
                backup.validate_backup(self.snapshot, expected=expected, paths=self.paths)
        (self.snapshot / "noise.key").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
        self.snapshot.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)

    @patch.object(backup, "run")
    def test_restore_requires_stopped_before_writes(self, run):
        self.capture()
        backup.stopped.side_effect = RuntimeError("must stop")
        with self.assertRaisesRegex(RuntimeError, "must stop"):
            backup.restore_headscale(self.snapshot, expected=self.manifest, paths=self.paths)
        run.assert_not_called()

    @patch.object(backup.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=-1, pw_gid=-1))
    @patch.object(backup, "run")
    def test_restore_exact_matching_version_without_activation(self, run, account):
        self.capture()
        run.return_value = SimpleNamespace(stdout="headscale version 0.29.7")
        (self.paths.etc / "policy.hujson").write_text("changed")
        backup.restore_headscale(self.snapshot, expected=self.manifest, paths=self.paths)
        self.assertEqual((self.paths.etc / "policy.hujson").read_text(), '{"acls": []}')
        self.assertNotIn(["systemctl", "start", "headscale"], [c.args[0] for c in run.call_args_list])

    def test_upgrade_sequential_latest_reviewed_patch(self):
        old = server.Release("0.29.7", "amd64", "1" * 64)
        new = server.Release("0.30.2", "amd64", "2" * 64)
        backup.validate_upgrade(old, new, reviewed_releases=[old, new])
        for target in (server.Release("0.31.1", "amd64", "3" * 64), old,
                       server.Release("0.30.1", "amd64", "4" * 64)):
            with self.assertRaises(ValueError):
                backup.validate_upgrade(old, target, reviewed_releases=[old, new, target])

    @patch.object(backup, "verify_package")
    @patch.object(backup, "install_deb")
    @patch.object(backup, "_restore")
    @patch.object(backup, "run")
    @patch.object(backup, "health", side_effect=[RuntimeError("health failed"), None])
    def test_upgrade_failure_recovers_matching_binary_and_database(self, health, run, restore, install, verify):
        old = server.Release("0.29.7", "amd64", "1" * 64)
        new = server.Release("0.30.2", "amd64", "2" * 64)
        with self.assertRaisesRegex(RuntimeError, "health failed"):
            backup.upgrade_headscale(self.root / "new.deb", self.root / "old.deb", self.snapshot,
                                     current=old, target=new, reviewed_releases=[old, new], paths=self.paths)
        self.assertEqual([call.args[1] for call in install.call_args_list], [new, old])
        restore.assert_called_once()
        self.assertEqual(restore.call_args.args[0]["policy.hujson"], b'{"acls": []}')
        self.assertTrue((self.paths.private / "operation.json").exists())
