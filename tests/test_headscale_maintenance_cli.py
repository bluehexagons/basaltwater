"""Maintenance dispatch never exposes snapshot contents or native diagnostics."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import headscale_maintenance_cli as cli
from lib.atomic_io import write_json_atomic


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.parser = argparse.ArgumentParser()
        cli.add_headscale_maintenance_parser(self.parser.add_subparsers(required=True))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "manifest.json"
        write_json_atomic(str(self.manifest), dict(schema=1, version="0.29.7",
                          endpoint="https://vpn.example.com", base_domain="mesh.example.net"))

    def invoke(self, words):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run_headscale_maintenance_command(self.parser.parse_args(["headscale-maintenance", *words]))
        return code, out.getvalue(), err.getvalue()

    def test_dryrun_all_actions_no_native_locks_or_writes(self):
        binding = self.root / "binding.json"
        write_json_atomic(str(binding), dict(version=1, network_url="https://vpn.example.com", node_id="7",
                          addresses=["100.64.0.2"], advertised=False, publication_id=None))
        current, target, inventory = [self.root / name for name in ("old.json", "new.json", "inventory.json")]
        old = dict(version="0.29.7", architecture="amd64", sha256="1" * 64)
        new = dict(version="0.30.2", architecture="amd64", sha256="2" * 64)
        for path, value in ((current, old), (target, new), (inventory, [old, new])):
            write_json_atomic(str(path), value)
        cases = [
            ["backup", "--destination", str(self.root / "absent")],
            ["unbound-backup", "--destination", str(self.root / "absent")],
            *[[action, "--source", str(self.root / "absent"), "--manifest", str(self.manifest)]
              for action in ("validate", "restore")],
            *[[action, "--source", str(self.root / "absent"), "--binding", str(binding)]
              for action in ("unbound-validate", "unbound-restore")],
            ["upgrade", "--current-release", str(current), "--target-release", str(target),
             "--release-inventory", str(inventory), "--target-package", str(self.root / "new.deb"),
             "--recovery-package", str(self.root / "old.deb"), "--backup", str(self.root / "absent")],
        ]
        before = set(self.root.iterdir())
        with patch("lib.remote_utils.run", side_effect=AssertionError("native")), patch(
            "lib.operation_state.OperationStateStore.acquire", side_effect=AssertionError("lock")
        ), patch("lib.headscale_dns_binding.dns_binding_lease", side_effect=AssertionError("lease")), patch.object(
            cli, "_private_output", side_effect=AssertionError("output mutation")
        ):
            for case in cases:
                with self.subTest(action=case[0]):
                    self.assertEqual(0, self.invoke([*case, "--dry-run"])[0])
        self.assertEqual(before, set(self.root.iterdir()))

    def test_dispatch_exact_manifest_and_activation_and_safe_error(self):
        manifest = dict(schema=1, version="0.29.7", endpoint="https://vpn.example.com", base_domain="mesh.example.net")
        with patch("lib.unbound_backup.private_json", return_value=manifest), patch.object(
            cli.os, "geteuid", return_value=0
        ), patch("lib.headscale_backup.restore_headscale") as restore:
            code, out, err = self.invoke(["restore", "--source", str(self.root / "snapshot"),
                                          "--manifest", str(self.manifest), "--activate"])
        self.assertEqual(0, code)
        self.assertTrue(restore.call_args.kwargs["activate"])
        self.assertEqual("0.29.7", restore.call_args.kwargs["expected"].version)
        with patch("lib.unbound_backup.private_json", return_value=dict(schema=1, version="0.29.7",
                   endpoint="https://vpn.example.com", base_domain="mesh.example.net")), patch.object(
            cli.os, "geteuid", return_value=0
        ), patch("lib.headscale_backup.validate_backup", side_effect=RuntimeError("SECRET private fixture")):
            code, out, err = self.invoke(["validate", "--source", str(self.root / "snapshot"),
                                         "--manifest", str(self.manifest)])
        self.assertEqual(1, code)
        self.assertNotIn("SECRET", out + err)
        self.assertNotIn("vpn.example.com", out + err)

    def test_invalid_private_input_fails_secret_free(self):
        self.manifest.write_text('{"secret":"SECRET", "schema":99}')
        code, out, err = self.invoke(["validate", "--source", str(self.root / "snapshot"),
                                     "--manifest", str(self.manifest), "--dry-run"])
        self.assertEqual(1, code)
        self.assertNotIn("SECRET", out + err)

    def test_standalone_main(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(["headscale-maintenance", "backup", "--destination",
                                          str(self.root / "new"), "--dry-run"]))

    def test_fresh_recovery_dispatch_and_dryrun(self):
        review = dict(operation={"operation_id": "reviewed"}, backup_sha256="a" * 64,
                      binding=dict(version=1, network_url="https://vpn.example.com", node_id="7",
                                   addresses=["100.64.0.2"], advertised=False, publication_id=None))
        source = self.root / "review.json"
        write_json_atomic(str(source), review)
        words = ["unbound-recover-fresh", "--review", str(source)]
        with patch("lib.unbound_backup.recover_fresh_unbound") as recover:
            self.assertEqual(0, self.invoke([*words, "--dry-run"])[0])
            recover.assert_not_called()
            with patch.object(cli.os, "geteuid", return_value=0), patch("lib.unbound_backup.private_json", return_value=review):
                self.assertEqual(0, self.invoke(words)[0])
            recover.assert_called_once_with(reviewed=review)
