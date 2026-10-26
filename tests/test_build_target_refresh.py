"""Trusted local artifact replay excludes requests before reading target state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from common import build_target_refresh as refresh
from lib import build_target_maintenance as maintenance
from lib.build_admission import DeploymentAdmissionStore
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, encode_document
from lib.build_target_activation import TargetReleaseActivator
from lib.build_target_policy import authorize_target_deployment
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
        self.admissions = self.enterContext(patch.object(refresh, "DeploymentAdmissionStore"))
        self.builder = self.enterContext(patch("lib.deployment.DeploymentOrchestrator._prepare_build_toolchain"))
        self.output = self.enterContext(patch("builtins.print"))
        self.expected = [{"application": "shop", "operation_id": "a" * 32, "generation": 3,
                          "artifact_sha256": "b" * 64}]

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

    def test_expected_wrapper_forwards_identities_and_admission_store_under_lease(self) -> None:
        held = False

        @contextmanager
        def exclusive():
            nonlocal held
            held = True
            try:
                yield
            finally:
                held = False

        def reconcile(**kwargs):
            self.assertTrue(held)
            self.assertEqual(kwargs, {"expected_releases": self.expected,
                                      "admissions": self.admissions.return_value})
            return []

        self.lease.side_effect = exclusive
        self.activator.return_value.reconcile_installed.side_effect = reconcile
        self.assertEqual(refresh.reconcile_artifact_deployments(self.config, expected_releases=self.expected), [])
        self.admissions.assert_called_once_with("/protected/admission/admissions.json")
        self.assertFalse(held)
        self.builder.assert_not_called()

    def test_expected_scope_checks_before_profile_and_reuses_lease_through_response(self) -> None:
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
        self.load.side_effect = observed("config", self.load.return_value)
        self.activator.return_value.check_expected_releases.side_effect = observed("check", None)
        self.activator.return_value.reconcile_installed.side_effect = observed("reconcile", [])
        self.output.side_effect = observed("response", None)
        original = copy.deepcopy(self.expected)
        config_before = copy.deepcopy(vars(self.config))
        with refresh.expected_artifact_setup(self.config, self.expected):
            self.assertTrue(held)
            events.append("profile")
            self.expected[0]["generation"] += 1  # Scope owns a detached snapshot.
            refresh.reconcile_artifact_deployments(self.config)
        self.assertEqual(events, ["lease", "config", "check", "profile", "reconcile", "response", "released"])
        self.lease.assert_called_once_with()
        self.load.assert_called_once_with()
        self.activator.assert_called_once()
        self.activator.return_value.check_expected_releases.assert_called_once_with(
            expected_releases=original, admissions=self.admissions.return_value,
        )
        self.activator.return_value.reconcile_installed.assert_called_once_with(
            expected_releases=original, admissions=self.admissions.return_value,
        )
        self.assertEqual(vars(self.config), config_before)

    def test_expected_scope_refuses_other_config_expectations_and_nesting(self) -> None:
        with refresh.expected_artifact_setup(self.config, self.expected):
            with self.assertRaisesRegex(RuntimeError, "controller"):
                refresh.reconcile_artifact_deployments(copy.copy(self.config))
            with self.assertRaisesRegex(RuntimeError, "controller"):
                refresh.reconcile_artifact_deployments(
                    self.config, expected_releases=[dict(self.expected[0], generation=4)],
                )
            with self.assertRaisesRegex(RuntimeError, "controller"), refresh.expected_artifact_setup(
                self.config, self.expected,
            ):
                self.fail("nested scope entered")
            self.activator.return_value.reconcile_installed.assert_not_called()
            refresh.reconcile_artifact_deployments(self.config, expected_releases=self.expected)
        self.lease.assert_called_once()
        self.load.assert_called_once()
        self.activator.return_value.reconcile_installed.assert_called_once()

    def test_expected_scope_resets_after_body_error_and_rejects_copied_stale_context(self) -> None:
        failure = RuntimeError("profile step failed")
        with self.assertRaises(RuntimeError) as raised:
            with refresh.expected_artifact_setup(self.config, self.expected):
                copied = copy_context()
                raise failure
        self.assertIs(raised.exception, failure)
        with self.assertRaisesRegex(RuntimeError, "controller"):
            copied.run(refresh.reconcile_artifact_deployments, self.config)
        self.activator.return_value.reconcile_installed.assert_not_called()
        refresh.reconcile_artifact_deployments(self.config)
        self.assertEqual(self.lease.call_count, 2)
        self.assertEqual(self.load.call_count, 2)
        self.activator.return_value.reconcile_installed.assert_called_once_with()

    def test_guard_refusal_does_not_enter_profile_and_scope_is_released(self) -> None:
        self.activator.return_value.check_expected_releases.side_effect = ValueError("superseded")
        with self.assertRaisesRegex(RuntimeError, "controller"), refresh.expected_artifact_setup(
            self.config, self.expected,
        ):
            self.fail("profile mutations entered after refused guard")
        self.lease.return_value.__exit__.assert_called_once()
        self.activator.return_value.reconcile_installed.assert_not_called()
        self.store.return_value.open_verified_bundle.assert_not_called()
        refresh.reconcile_artifact_deployments(self.config)
        self.assertEqual(self.lease.call_count, 2)

    def test_malformed_expectations_refuse_under_lease_before_config_read(self) -> None:
        held = False

        @contextmanager
        def exclusive():
            nonlocal held
            held = True
            try:
                yield
            finally:
                held = False

        original_parse = refresh.parse_expected_releases

        def validate(value):
            self.assertTrue(held)
            return original_parse(value)

        self.lease.side_effect = exclusive
        with patch.object(refresh, "parse_expected_releases", side_effect=validate):
            for value in ([], {}, [dict(self.expected[0], generation=True)], self.expected * 2):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(RuntimeError, "controller"):
                        refresh.reconcile_artifact_deployments(self.config, expected_releases=value)
                    with self.assertRaisesRegex(RuntimeError, "controller"), refresh.expected_artifact_setup(
                        self.config, value,
                    ):
                        self.fail("malformed guard entered profile")
        self.load.assert_not_called()
        self.activator.assert_not_called()
        self.admissions.assert_not_called()

    @contextmanager
    def installed_target(self):
        """Real private evidence and coordination, with all deployment I/O mocked."""
        with tempfile.TemporaryDirectory() as directory, root_view(), patch.object(
            maintenance, "LOCK_PATH", str(Path(directory) / ".maintenance.lock"),
        ):
            root = Path(directory)
            root.chmod(0o700)
            roots = {}
            for name in ("admission", "artifacts", "releases", "verify", "apps"):
                path = root / name
                path.mkdir(mode=0o700)
                roots[name] = str(path)
            self.load.return_value["roots"] = roots
            self.lease.side_effect = maintenance.target_maintenance
            self.activator.side_effect = TargetReleaseActivator
            self.admissions.side_effect = DeploymentAdmissionStore
            with maintenance.target_lease(installer=True, create=True) as descriptor:
                maintenance.set_serving(descriptor, True)

            request = {
                "schema": 1, "request_id": uuid.uuid4().hex, "project": "shop-source",
                "source": {"revision": "a" * 40, "snapshot_sha256": "b" * 64, "manifest_sha256": "c" * 64},
                "platform": dict(DEBIAN_PLATFORM), "limits": dict(DEFAULT_LIMITS),
                "components": [{"name": "site", "type": "static", "commands": ["build"],
                                "artifacts": ["dist"], "env": {}}],
            }
            projection = {"schema": 1, "application": "shop", "project": "shop-source",
                          "platform": dict(DEBIAN_PLATFORM), "components": [{
                              "name": "site", "type": "static", "artifacts": ["dist"],
                              "output": "dist", "domain": "shop.example", "path": "/", "spa": False,
                          }]}
            policy = {"schema": 1, "applications": {"shop": {
                "project": "shop-source", "principals": ["controller"],
                "deployment_root": str(root / "apps" / "shop"), "platform": dict(DEBIAN_PLATFORM),
                "limits": {key: DEFAULT_LIMITS[key] for key in ("artifact_bytes", "artifact_entries")},
                "components": {"site": {"type": "static", "domain": "shop.example", "path": "/"}},
            }}}
            checked = authorize_target_deployment(policy, principal="controller", application="shop",
                                                  request=request, deployment=projection)
            admissions = DeploymentAdmissionStore(root / "admission" / "admissions.json")
            reservation = admissions.reserve("shop", request, owner="controller", idempotency_key=uuid.uuid4().hex,
                                             bindings_sha256=checked["bindings_sha256"])

            @contextmanager
            def open_bundle(*_args, **_kwargs):
                yield str(root / "mock.bundle")

            self.store.return_value.open_verified_bundle.side_effect = open_bundle
            activator = TargetReleaseActivator(roots["releases"], roots["verify"], open_artifact=open_bundle)
            deployments = [{"dest_path": str(root / "apps" / "shop"), "domain": "shop.example", "path": "/",
                            "project_type": "static", "needs_proxy": False,
                            "serve_path": str(root / "apps" / "shop" / "dist"), "backend_port": None, "spa": False}]
            with patch("lib.build_target_activation.DeploymentOrchestrator.deploy_prebuilt",
                       return_value=deployments) as deploy, patch(
                "lib.build_target_activation.create_nginx_sites_for_groups",
            ) as nginx:
                receipt = admissions.activate(
                    reservation["operation_id"], 1, request, "d" * 64, owner="controller",
                    bindings_sha256=checked["bindings_sha256"], action=lambda: activator(
                        bindings=checked["bindings"], request=request, operation_id=reservation["operation_id"],
                        generation=1, artifact_sha256="d" * 64, principal="controller",
                    ),
                )
                deploy.reset_mock()
                nginx.reset_mock()
                expected = [{key: receipt[key] for key in ("application", "operation_id", "generation", "artifact_sha256")}]
                yield root, admissions, request, checked, expected, deploy, nginx

    def test_real_guard_precedes_profile_and_real_reconciliation_reuses_exclusive_lease(self) -> None:
        with self.installed_target() as (root, _admissions, _request, _checked, expected, deploy, nginx):
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with refresh.expected_artifact_setup(self.config, expected):
                self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
                self.store.return_value.open_verified_bundle.assert_not_called()
                deploy.assert_not_called()
                nginx.assert_not_called()
                for lease in (maintenance.target_lease(), maintenance.target_maintenance(),
                              maintenance.target_lease(installer=True)):
                    with self.assertRaisesRegex(RuntimeError, "active"), lease:
                        self.fail("request or enrollment entered guarded profile")
                receipts = refresh.reconcile_artifact_deployments(self.config)
                self.assertEqual(receipts[0]["operation_id"], expected[0]["operation_id"])
                deploy.assert_called_once()
                nginx.assert_called_once()
            self.lease.assert_called_once()
            self.load.assert_called_once()
            with maintenance.target_lease():
                pass
        self.builder.assert_not_called()

    def test_real_newer_reservation_refuses_guard_before_any_profile_mutation(self) -> None:
        with self.installed_target() as (root, admissions, request, checked, expected, deploy, nginx):
            admissions.reserve("shop", request, owner="controller", idempotency_key=uuid.uuid4().hex,
                               bindings_sha256=checked["bindings_sha256"])
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with self.assertRaisesRegex(RuntimeError, "controller"), refresh.expected_artifact_setup(self.config, expected):
                self.fail("profile mutated after a newer controller reservation")
            self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)
            self.store.return_value.open_verified_bundle.assert_not_called()
            deploy.assert_not_called()
            nginx.assert_not_called()
            with maintenance.target_lease():
                pass

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
                    with refresh.expected_artifact_setup(self.config, self.expected):
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
