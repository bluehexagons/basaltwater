"""Current runtime activation and retired-installation safeguards."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import setup_common, setup_upgrade


class SetupUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.runtime = self.root / "opt/basaltwater"
        self.source = self.root / "incoming"
        self.source.mkdir()
        (self.source / "basaltwater.py").write_text("# new runtime\n")
        self.enterContext(patch.object(setup_common, "REMOTE_INSTALL_DIR", str(self.runtime)))
        self.enterContext(patch.object(setup_common, "PERSISTENT_STATE_DIR", str(self.root / "var/lib/basaltwater")))

    def journal(self, value):
        directory = self.root / "var/lib/basaltwater-migration"
        directory.mkdir(parents=True, mode=0o700)
        journal = directory / "journal.json"
        journal.write_text(json.dumps(value))
        journal.chmod(0o600)
        return journal

    def test_fresh_setup_activates_current_runtime(self):
        setup_upgrade.prepare_target_runtime(str(self.source), "agent")
        self.assertEqual((self.runtime / "basaltwater.py").read_text(), "# new runtime\n")
        self.assertEqual((self.runtime / "state").resolve(), self.root / "var/lib/basaltwater")

    def test_retired_runtime_or_state_is_untouched(self):
        for relative in ("opt/infra_tools", "opt/infra-tools", "var/lib/infra_tools", "var/lib/infra-tools"):
            with self.subTest(relative=relative):
                legacy = self.root / relative
                legacy.mkdir(parents=True)
                secret = legacy / "credentials.json"
                secret.write_text("private")
                before = secret.read_bytes(), secret.stat().st_ino
                with patch.object(setup_common, "_activate_local_runtime") as activate:
                    with self.assertRaisesRegex(ValueError, "Retired.*intermediate"):
                        setup_upgrade.prepare_target_runtime(str(self.source), "agent")
                activate.assert_not_called()
                self.assertEqual((secret.read_bytes(), secret.stat().st_ino), before)
                secret.unlink()
                legacy.rmdir()

    def test_completed_journal_remains_opaque_and_setup_proceeds(self):
        journal = self.journal({"status": "complete", "actions": ["historical data"]})
        before = journal.read_bytes(), journal.stat().st_ino
        setup_upgrade.prepare_target_runtime(str(self.source), "agent")
        self.assertEqual((journal.read_bytes(), journal.stat().st_ino), before)

    def test_incomplete_or_malformed_journal_blocks_activation(self):
        journal = self.journal({"status": "planned"})
        with patch.object(setup_common, "_activate_local_runtime") as activate:
            for content in ('{"status":"planned"}', '[]', '{"status":"recovered"}', "invalid"):
                with self.subTest(content=content):
                    journal.write_text(content)
                    with self.assertRaises(ValueError):
                        setup_upgrade.prepare_target_runtime(str(self.source), "agent")
                    self.assertEqual(journal.read_text(), content)
        activate.assert_not_called()

    def test_unfinished_unit_recovery_blocks_replacement(self):
        directory = self.root / "etc/systemd/system"
        directory.mkdir(parents=True)
        for brand in ("infra-tools", "basaltwater"):
            marker = directory / f".{brand}-unit-operation.json"
            marker.write_text("{}")
            with patch.object(setup_common, "_activate_local_runtime") as activate:
                with self.assertRaisesRegex(ValueError, "Unfinished systemd unit replacement"):
                    setup_upgrade.prepare_target_runtime(str(self.source), "agent")
            activate.assert_not_called()
            self.assertEqual(marker.read_text(), "{}")
            marker.unlink()

    def test_runtime_replacement_preserves_syncthing_traversal(self):
        home = self.root / "var/lib/basaltwater/syncthing"
        home.mkdir(parents=True)
        original_stat = Path.stat

        def owned_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == home:
                fields = list(result)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result

        with patch.object(Path, "stat", owned_stat), \
             patch.object(setup_upgrade.shutil, "which", return_value="/usr/bin/tool"), \
             patch.object(setup_upgrade.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "user::rwx\ngroup::---\nother::---\n", "")) as run:
            setup_upgrade.prepare_target_runtime(str(self.source), "agent")
        self.assertEqual(run.call_args.args[0], ["setfacl", "--set-file=-", "--", str(home.parent)])
        self.assertIn(f"user:{os.geteuid() + 1}:--x", run.call_args.kwargs["input"])

    def test_traversal_does_not_unmask_existing_acl_rights(self):
        before = "user::rwx\nuser:1001:rwx\ngroup::rwx\nmask::r--\nother::---\n"
        after = setup_upgrade._traversal_acl(before, 1001)
        self.assertIn("user:1001:r-x\n", after)
        self.assertIn("group::r--\n", after)


if __name__ == "__main__":
    unittest.main()
