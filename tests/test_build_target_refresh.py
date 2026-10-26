"""Trusted local artifact replay excludes requests before reading target state."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import build_target_refresh as refresh
from lib import build_target_maintenance as maintenance
from lib.build_target_activation import TargetReleaseActivator
from lib.config import SetupConfig
from tests.test_build_target_maintenance import root_view


class BuildTargetRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SetupConfig(
            host="localhost", username="root", system_type="server_lite",
            machine_type="vm", artifact_deployments=True,
        )
        self.enterContext(patch.object(refresh.os, "geteuid", return_value=0))
        self.enterContext(patch.object(refresh, "is_dry_run", return_value=False))
        self.platform = self.enterContext(patch.object(refresh, "require_platform"))
        self.lease = self.enterContext(patch.object(refresh, "target_maintenance"))
        self.load = self.enterContext(patch.object(refresh, "load_config", return_value={
            "roots": {name: "/protected/" + name for name in ("admission", "artifacts", "releases", "verify")},
            "max_records": 64, "max_bytes": 1024**3,
        }))
        self.store = self.enterContext(patch.object(refresh, "BuildTargetArtifactStore"))
        self.activator = self.enterContext(patch.object(refresh, "TargetReleaseActivator"))
        self.admissions = self.enterContext(patch("lib.build_admission.DeploymentAdmissionStore"))
        self.builder = self.enterContext(patch("lib.deployment.DeploymentOrchestrator._prepare_build_toolchain"))
        self.output = self.enterContext(patch("builtins.print"))

    def test_lease_covers_config_composition_reconciliation_and_response(self) -> None:
        events = []
        held = False

        @contextmanager
        def exclusive():
            nonlocal held
            held = True
            events.append("lease")
            try:
                yield
            finally:
                held = False
                events.append("released")

        def observed(name, value):
            def action(*_args, **_kwargs):
                self.assertTrue(held, name)
                events.append(name)
                return value
            return action

        self.lease.side_effect = exclusive
        installed = self.load.return_value
        self.load.side_effect = observed("config", installed)
        store = self.store.return_value
        self.store.side_effect = observed("artifacts", store)
        activator = self.activator.return_value
        self.activator.side_effect = observed("activator", activator)
        receipts = [{"application": "shop", "operation_id": "a" * 32, "generation": 3,
                     "artifact_sha256": "b" * 64, "state": "reconciled"}]
        activator.reconcile_installed.side_effect = observed("reconcile", receipts)
        self.output.side_effect = observed("response", None)

        result = refresh.reconcile_artifact_deployments(self.config)

        self.assertEqual(result, receipts)
        self.assertEqual(events, ["lease", "config", "artifacts", "activator", "reconcile", "response", "released"])
        self.load.assert_called_once_with()
        self.store.assert_called_once_with("/protected/artifacts", max_records=64, max_bytes=1024**3)
        self.activator.assert_called_once_with(
            "/protected/releases", "/protected/verify", open_artifact=store.open_verified_bundle,
        )
        activator.reconcile_installed.assert_called_once_with()
        self.admissions.assert_not_called()
        self.builder.assert_not_called()

    def test_real_maintenance_refuses_missing_unsafe_held_and_busy_enrollment_before_read(self) -> None:
        self.lease.side_effect = maintenance.target_maintenance
        with tempfile.TemporaryDirectory() as directory, root_view(), patch.object(
            maintenance, "LOCK_PATH", str(Path(directory) / ".maintenance.lock"),
        ):
            path = Path(maintenance.LOCK_PATH)
            Path(directory).chmod(0o700)

            def refused():
                with self.assertRaisesRegex(RuntimeError, "controller"):
                    refresh.reconcile_artifact_deployments(self.config)
                self.load.assert_not_called()
                self.store.assert_not_called()
                self.activator.assert_not_called()

            refused()  # No bootstrap of missing enrollment.
            self.assertFalse(path.exists())
            with maintenance.target_lease(installer=True, create=True):
                pass
            refused()  # Durable held/disabled gate.
            with maintenance.target_lease(installer=True) as descriptor:
                maintenance.set_serving(descriptor, True)
            path.chmod(0o644)
            refused()
            path.chmod(0o600)
            before = path.stat().st_ino, path.read_bytes()
            with maintenance.target_lease():
                refused()
            self.assertEqual((path.stat().st_ino, path.read_bytes()), before)
            with maintenance.target_lease(installer=True):
                pass  # All failed attempts released any acquired ownership.

    def test_real_lease_remains_exclusive_through_local_response(self) -> None:
        self.lease.side_effect = maintenance.target_maintenance
        with tempfile.TemporaryDirectory() as directory, root_view(), patch.object(
            maintenance, "LOCK_PATH", str(Path(directory) / ".maintenance.lock"),
        ):
            Path(directory).chmod(0o700)
            with maintenance.target_lease(installer=True, create=True) as descriptor:
                maintenance.set_serving(descriptor, True)

            def excluded(*_args, **_kwargs):
                for context in (maintenance.target_lease(), maintenance.target_maintenance()):
                    with self.assertRaisesRegex(RuntimeError, "active"), context:
                        self.fail("request/enrollment overlapped refresh")

            self.load.side_effect = lambda: (excluded(), self.load.return_value)[1]
            self.activator.return_value.reconcile_installed.side_effect = lambda: (excluded(), [])[1]
            self.output.side_effect = excluded
            refresh.reconcile_artifact_deployments(self.config)
            with maintenance.target_lease():
                pass

    def test_missing_config_or_retained_inputs_fail_with_controller_guidance(self) -> None:
        for boundary, error in (
            (self.load, FileNotFoundError("missing config")),
            (self.load, ValueError("unsafe config")),
            (self.activator.return_value.reconcile_installed, FileNotFoundError("missing bundle")),
            (self.activator.return_value.reconcile_installed, ValueError("missing descriptor")),
        ):
            with self.subTest(error=str(error)):
                boundary.side_effect = error
                with self.assertRaisesRegex(RuntimeError, "controller") as raised:
                    refresh.reconcile_artifact_deployments(self.config)
                self.assertIs(raised.exception.__cause__, error)
                boundary.side_effect = None
        self.admissions.assert_not_called()
        self.builder.assert_not_called()

    def test_first_setup_without_retained_releases_refuses_before_deployment(self) -> None:
        self.activator.side_effect = TargetReleaseActivator
        with tempfile.TemporaryDirectory() as directory, root_view():
            roots = {}
            for name in ("artifacts", "releases", "verify"):
                path = Path(directory) / name
                path.mkdir(mode=0o700)
                roots[name] = str(path)
            self.load.return_value["roots"] = roots
            with patch("lib.build_target_activation.DeploymentOrchestrator.deploy_prebuilt") as deploy, patch(
                "lib.build_target_activation.create_nginx_sites_for_groups",
            ) as nginx:
                with self.assertRaisesRegex(RuntimeError, "controller") as raised:
                    refresh.reconcile_artifact_deployments(self.config)
            self.assertIn("No retained target releases", str(raised.exception.__cause__))
            deploy.assert_not_called()
            nginx.assert_not_called()
        self.admissions.assert_not_called()
        self.builder.assert_not_called()

    def test_dry_run_does_not_open_target_state_or_check_installed_platform(self) -> None:
        for config_dry_run, global_dry_run in ((True, False), (False, True)):
            with self.subTest(config=config_dry_run, global_=global_dry_run):
                self.config.dry_run = config_dry_run
                with patch.object(refresh, "is_dry_run", return_value=global_dry_run):
                    self.assertEqual(refresh.reconcile_artifact_deployments(self.config), [])
        self.assertIn("Would reconcile retained target artifacts", self.output.call_args.args[0])
        for mock in (self.lease, self.load, self.store, self.activator, self.platform, self.builder, self.admissions):
            mock.assert_not_called()

    def test_requires_root_and_supported_machine_before_state_access(self) -> None:
        with patch.object(refresh.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(PermissionError, "root"):
                refresh.reconcile_artifact_deployments(self.config)
        for machine in ("oci", "privileged", "unprivileged", "wsl"):
            with self.subTest(machine=machine):
                self.config.machine_type = machine
                with self.assertRaisesRegex(RuntimeError, "system services"):
                    refresh.reconcile_artifact_deployments(self.config)
        self.config.machine_type = "vm"
        with patch.object(refresh, "can_manage_system_services", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "system services"):
                refresh.reconcile_artifact_deployments(self.config)
        self.lease.assert_not_called()
        self.load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
