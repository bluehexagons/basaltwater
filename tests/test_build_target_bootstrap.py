"""Bootstrap transport authority without replacing saved application intent."""

from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import build_target_bootstrap as bootstrap
from lib.config import SetupConfig
from lib.operation_state import OperationStateStore
from lib.setup_common import _expand_remote_args
import remote_setup
from tests.test_build_target_setup import enrollment


class TargetBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.marker = str(self.root / "setup-operation.json")
        self.enterContext(patch.object(bootstrap, "SETUP_OPERATION_FILE", self.marker))
        self.enterContext(patch.object(bootstrap.os, "geteuid", return_value=0))
        self.enterContext(patch.object(bootstrap, "is_dry_run", return_value=False))
        self.configure = self.enterContext(patch.object(bootstrap, "configure_build_target"))
        self.config = SetupConfig(
            host="localhost", username="agent", system_type="custom_steps",
            machine_type="vm", custom_steps=bootstrap.BOOTSTRAP_STEP,
            build_target_enrollment=enrollment(),
        )

    def test_bootstrap_does_not_replace_saved_profile_or_create_setup_intent(self) -> None:
        saved = self.root / "setup.json"
        saved.write_bytes(b'{"system_type":"server_web","artifact_deployments":true}')
        before = saved.stat().st_ino, saved.read_bytes()

        def installer(config):
            self.assertIs(config, self.config)
            with self.assertRaisesRegex(ValueError, "owned by another process"):
                with OperationStateStore(self.marker).ownership():
                    self.fail("bootstrap lost setup exclusion")
            self.assertFalse(Path(self.marker).exists())

        self.configure.side_effect = installer
        bootstrap.bootstrap_build_target(self.config)
        self.assertEqual((saved.stat().st_ino, saved.read_bytes()), before)
        self.assertFalse(Path(self.marker).exists())
        self.assertFalse(Path(self.marker + ".last.json").exists())

    def test_existing_or_corrupt_setup_guard_refuses_before_enrollment(self) -> None:
        store = OperationStateStore(self.marker)
        record = store.begin("target_setup", "server_web", "applying")
        store.close()
        for recovery in (False, True):
            with self.subTest(recovery=recovery):
                if recovery:
                    store.transition(record.operation_id, "recovery", status="recovery_required")
                    store.close()
                before = Path(self.marker).read_bytes()
                with self.assertRaisesRegex(RuntimeError, "Unfinished"):
                    bootstrap.bootstrap_build_target(self.config)
                self.assertEqual(Path(self.marker).read_bytes(), before)
        Path(self.marker).write_text("invalid")
        with self.assertRaisesRegex(ValueError, "Invalid operation marker"):
            bootstrap.bootstrap_build_target(self.config)
        self.configure.assert_not_called()

    def test_dry_run_has_no_target_state_access_and_rejects_unrelated_options(self) -> None:
        self.config.dry_run = True
        with patch.object(bootstrap, "OperationStateStore") as state:
            bootstrap.bootstrap_build_target(self.config)
        state.assert_not_called()
        self.configure.assert_called_once_with(self.config)
        self.configure.reset_mock()
        for name, value in (("artifact_deployments", True), ("disable_build_target", True),
                            ("install_go", True), ("deploy_specs", [["site.test", "https://site.test/app.git"]])):
            with self.subTest(option=name):
                previous = getattr(self.config, name)
                setattr(self.config, name, value)
                with self.assertRaisesRegex(ValueError, "unrelated"):
                    bootstrap.bootstrap_build_target(self.config)
                setattr(self.config, name, previous)
        self.configure.assert_not_called()

    def test_remote_round_trip_dispatches_only_bootstrap_without_saved_setup(self) -> None:
        tokens = _expand_remote_args(self.config.to_remote_args())
        with patch.object(sys, "argv", ["remote_setup.py", *tokens]), \
             patch("lib.cachyos.is_cachyos", return_value=False), \
             patch.object(remote_setup, "save_machine_state") as save_machine, \
             patch.object(remote_setup, "save_setup_config") as save_config, \
             patch.object(remote_setup, "get_steps_for_system_type") as steps, \
             patch.object(remote_setup, "enable_detected_build_runtimes") as infer, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(remote_setup._run_main(), 0)
        self.configure.assert_called_once()
        save_machine.assert_not_called()
        save_config.assert_not_called()
        steps.assert_not_called()
        infer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
