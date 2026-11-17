"""Runtime binding authority, privacy and shared deployment behavior."""

from __future__ import annotations

import copy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import runtime_bindings as bindings
from lib.build_admission import DeploymentAdmissionStore
from lib.build_setup_plan import prepare_delivery_plan
from lib.build_target_activation import _bindings_manifest
from lib.build_target_policy import authorize_target_deployment, capabilities_for_principal
from lib.build_target_service import BuildTargetService
from lib.manifest_serving import parse_runtime, runtime_from_component
from lib.project_manifest import parse_manifest
from lib.systemd_service import generate_managed_service
from tests import test_build_setup_plan as planning
from tests import test_build_target_activation as activation
from tests import test_manifest_deploy as deployment


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.registry = self.root / "bindings.json"
        self.secret = self.root / "api.env"
        self.secret.write_text("PRIVATE_TOKEN=fixture-only-secret\n")
        self.secret.chmod(0o600)
        self.destination = str(self.root / "apps" / "shop")
        self.document = {"schema": 1, "bindings": {"shop-api": {
            "deployment_root": self.destination, "component": "api", "env_file": str(self.secret),
        }}}
        self.save()
        # Exercise real no-follow filesystem operations without requiring root.
        self.enterContext(patch.object(bindings, "_ROOT_UID", os.getuid()))
        self.enterContext(patch.object(bindings, "REGISTRY_PATH", str(self.registry)))

    def save(self):
        self.registry.write_text(json.dumps(self.document))
        self.registry.chmod(0o600)

    def resolve(self, **overrides):
        return bindings.resolve_runtime_binding("shop-api", **{
            "deployment_root": self.destination, "component": "api", **overrides})

    def plan(self):
        data = planning.manifest_data()
        data["components"][1]["runtime_binding"] = "shop-api"
        manifest = parse_manifest(data)
        enrollment = planning.enrollment()
        app = enrollment["policy"]["applications"]["site"]
        app["deployment_root"] = self.destination
        app["components"]["api"]["runtime"] = runtime_from_component(manifest.components[1])
        plan = prepare_delivery_plan(
            manifest, destination="example.com", source={"revision": "b" * 40,
            "snapshot_sha256": "c" * 64, "manifest_sha256": "d" * 64},
            source_path=str(self.root / "snapshot"), request_id="a" * 32,
            idempotency_key="e" * 32, target_enrollment=enrollment,
            target_principal="alice", builder_capabilities=planning.capabilities(),
        )
        return plan, enrollment["policy"]

    def test_resolves_without_reading_or_changing_secret_values(self):
        original_read = os.read
        secret_inode = self.secret.stat().st_ino

        def registry_read(descriptor, length):
            self.assertNotEqual(os.fstat(descriptor).st_ino, secret_inode)
            return original_read(descriptor, length)

        before = self.secret.stat()
        with patch.object(bindings.os, "read", side_effect=registry_read):
            self.assertEqual(self.resolve(), str(self.secret))
        self.assertEqual(before, self.secret.stat())

    def test_registry_read_allows_atime_updates_and_rejects_content_changes(self):
        os.utime(self.registry, ns=(0, self.registry.stat().st_mtime_ns))
        self.assertEqual(self.resolve(), str(self.secret))
        original_read = os.read

        def changed_read(descriptor, length):
            chunk = original_read(descriptor, length)
            if chunk:
                before = self.registry.stat()
                self.registry.write_text(" " * len(chunk))
                os.utime(self.registry, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            return chunk

        with patch.object(bindings.os, "read", side_effect=changed_read):
            with self.assertRaisesRegex(ValueError, "changed during reading"):
                self.resolve()

    def test_scope_mismatch_and_revocation_refuse(self):
        for override in ({"deployment_root": self.destination + "-other"}, {"component": "worker"}):
            with self.subTest(override=override), self.assertRaises(PermissionError):
                self.resolve(**override)
        self.document["bindings"].clear()
        self.save()
        with self.assertRaises(PermissionError):
            self.resolve()

    def test_rechecks_missing_and_rotated_files(self):
        self.secret.unlink()
        with self.assertRaises(FileNotFoundError):
            self.resolve()
        replacement = self.root / "rotated.env"
        replacement.write_text("PRIVATE_TOKEN=new-fixture\n")
        replacement.chmod(0o600)
        self.document["bindings"]["shop-api"]["env_file"] = str(replacement)
        self.save()
        self.assertEqual(self.resolve(), str(replacement))

    def test_unsafe_modes_hardlinks_and_special_files_refuse(self):
        for target in (self.registry, self.secret):
            for mode in (0o644, 0o660, 0o400, 0o700):
                with self.subTest(target=target.name, mode=mode):
                    target.chmod(mode)
                    with self.assertRaises(ValueError):
                        self.resolve()
                    target.chmod(0o600)
            link = self.root / "alias"
            os.link(target, link)
            with self.assertRaises(ValueError):
                self.resolve()
            link.unlink()
        self.secret.unlink()
        os.mkfifo(self.secret, 0o600)
        with self.assertRaises(ValueError):
            self.resolve()

    def test_symlink_file_and_parent_refuse(self):
        original = self.root / "original.env"
        self.secret.rename(original)
        self.secret.symlink_to(original)
        with self.assertRaises((OSError, ValueError)):
            self.resolve()
        self.secret.unlink()
        original.rename(self.secret)
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        self.document["bindings"]["shop-api"]["env_file"] = str(linked / "api.env")
        self.save()
        with self.assertRaises(OSError):
            self.resolve()

    def test_writable_parent_and_foreign_owner_refuse(self):
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            self.resolve()
        self.root.chmod(0o700)
        with patch.object(bindings, "_ROOT_UID", os.getuid() + 1):
            with self.assertRaises(ValueError):
                self.resolve()

    def test_release_and_data_paths_refuse(self):
        for path in (self.destination + "/private.env",
                     str(Path(self.destination).parent / ".basaltwater_shared/shop/api.env")):
            self.document["bindings"]["shop-api"]["env_file"] = path
            self.save()
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "outside release"):
                self.resolve()

    def test_registry_is_strict_bounded_and_rejects_duplicate_keys(self):
        for raw in (b'{"schema":1,"schema":1,"bindings":{}}',
                    b" " * (bindings.MAX_REGISTRY_BYTES + 1), b"not-json"):
            self.registry.write_bytes(raw)
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                self.resolve()
        for field, value in (("env_file", "/etc/private%name.env"),
                             ("env_file", "/etc/../private.env"),
                             ("deployment_root", "relative"), ("component", "../api")):
            document = copy.deepcopy(self.document)
            document["bindings"]["shop-api"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                bindings.parse_runtime_bindings(document)

    def test_manifest_binding_is_service_only_literal_and_excludes_env_file(self):
        for value in (None, "", "../secret", "{{name}}", "UPPER", "a" * 65, [], True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                deployment._service_component(runtime_binding=value)
        with self.assertRaises(ValueError):
            deployment._service_component(runtime_binding="shop-api", env_file="/etc/api.env")
        with self.assertRaises(ValueError):
            deployment._static_component(runtime_binding="shop-api")

    def test_planning_and_retained_reconstruction_keep_ids_out_of_build_request(self):
        plan, policy = self.plan()
        self.assertNotIn("shop-api", json.dumps(plan.request))
        authorized = authorize_target_deployment(policy, principal="alice", application="site",
                                               request=plan.request, deployment=plan.deployment)
        checked, manifest = _bindings_manifest(authorized["bindings"], plan.request, "alice")
        api = next(item for item in manifest.components if item.name == "api")
        self.assertEqual(api.runtime_binding, "shop-api")
        self.assertIsNone(api.env_file)
        self.assertEqual(checked, authorized)
        private = json.dumps((plan.request, plan.deployment, authorized,
                              capabilities_for_principal(policy, principal="alice")))
        self.assertNotIn(str(self.secret), private)
        self.assertNotIn("fixture-only-secret", private)
        policy["applications"]["site"]["components"]["api"]["runtime"].pop("runtime_binding")
        with self.assertRaises(PermissionError):
            authorize_target_deployment(policy, principal="alice", application="site",
                                        request=plan.request, deployment=plan.deployment)

    def test_binding_only_runtime_does_not_disappear_in_legacy_projection(self):
        component = deployment._service_component(port="auto", runtime_binding="shop-api")
        self.assertEqual(runtime_from_component(component)["runtime_binding"], "shop-api")
        self.assertEqual(parse_runtime({"runtime_binding": "shop-api"})["runtime_binding"], "shop-api")

    def test_retained_refresh_rechecks_binding_before_any_release_changes(self):
        fixture = activation.TargetReleaseActivationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        authorized, request = fixture.inputs(service=True)
        authorized["components"][0]["runtime"] = parse_runtime({"runtime_binding": "shop-api"})
        self.destination = authorized["deployment_root"]
        self.document["bindings"]["shop-api"]["deployment_root"] = self.destination
        self.save()
        receipt = fixture.activate(authorized, request)
        fixture.activator.reconcile_installed()
        self.assertEqual(fixture.manifests[-1].components[0].runtime_binding, "shop-api")
        before = (fixture.state / "releases.json").read_bytes()
        self.document["bindings"].clear()
        self.save()
        fixture.deploy.reset_mock()
        with self.assertRaises(PermissionError):
            fixture.activator.reconcile_installed()
        fixture.deploy.assert_not_called()
        self.assertEqual((fixture.state / "releases.json").read_bytes(), before)
        self.assertFalse((fixture.state / "activation.json").exists())
        self.assertNotIn(str(self.secret), json.dumps(receipt))

    def test_missing_binding_refuses_reservation_and_later_activation(self):
        plan, policy = self.plan()
        admissions = DeploymentAdmissionStore(self.root / "admissions.json")
        activation = Mock()
        service = BuildTargetService(admissions=admissions, load_policy=lambda: policy,
                                   stage_artifact=Mock(), activate_release=activation)
        fields = dict(application="site", idempotency_key="e" * 32, request=plan.request,
                      deployment=plan.deployment, ttl_seconds=600)
        self.document["bindings"].clear()
        self.save()
        with patch.object(admissions, "reserve", wraps=admissions.reserve) as reserve:
            with self.assertRaises(PermissionError):
                service._dispatch("reserve", fields, owner="alice", input_stream=io.BytesIO())
            reserve.assert_not_called()
        self.document["bindings"]["shop-api"] = {"deployment_root": self.destination,
            "component": "api", "env_file": str(self.secret)}
        self.save()
        record = service._dispatch("reserve", fields, owner="alice", input_stream=io.BytesIO())
        self.document["bindings"].clear()
        self.save()
        with self.assertRaises(PermissionError):
            service._dispatch("activate", dict(operation_id=record["operation_id"],
                generation=record["generation"], request=plan.request, deployment=plan.deployment,
                artifact_sha256="f" * 64), owner="alice", input_stream=io.BytesIO())
        activation.assert_not_called()
        self.assertEqual(admissions.status(record["operation_id"], owner="alice")["state"], "reserved")

    def test_unit_receives_only_private_path_and_runtime_file_is_unchanged(self):
        component = deployment._service_component(binary="api", runtime_binding="shop-api")
        destination = Path(self.destination)
        destination.mkdir(parents=True)
        (destination / "api").touch()
        manager = deployment.DeploymentOrchestrator(base_dir=str(destination.parent))
        with ExitStack() as stack:
            stack.enter_context(patch("lib.deployment.run", return_value=Mock(returncode=0)))
            stack.enter_context(patch.object(manager, "_ensure_service_user"))
            create = stack.enter_context(patch("lib.deployment.create_managed_service"))
            manager._install_service_component(component, self.destination)
        args, kwargs = create.call_args
        self.assertEqual(kwargs["env_file"], str(self.secret))
        rendered = generate_managed_service(*args, **kwargs)
        self.assertIn("EnvironmentFile=" + str(self.secret), rendered)
        self.assertNotIn("fixture-only-secret", rendered)
        self.assertEqual(self.secret.read_text(), "PRIVATE_TOKEN=fixture-only-secret\n")

    def fixture(self):
        fixture = deployment.TestDeployManifest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        component = fixture.manifest.components[1]
        component.env_file = None
        component.runtime_binding = "shop-api"
        self.destination = os.path.join(fixture.base_dir, "example_com")
        self.document["bindings"]["shop-api"]["deployment_root"] = self.destination
        self.save()
        return fixture

    def deploy(self, fixture, *, prebuilt):
        if prebuilt:
            request, bundle, artifact = fixture._package_prebuilt()
            return fixture._deploy_prebuilt(request, bundle, artifact["sha256"])
        return fixture.orch.deploy_manifest(fixture.manifest, fixture.source, "example.com",
            "/", "https://git.example.com/shop.git", "b" * 40, keep_source=True)

    def mocked_system(self, fixture):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("lib.deployment.run", return_value=Mock(returncode=0)))
        stack.enter_context(patch.object(fixture.orch, "_ensure_build_user"))
        stack.enter_context(patch.object(fixture.orch, "_prepare_build_toolchain"))
        build = stack.enter_context(patch.object(fixture.orch, "_run_component_build"))
        create = stack.enter_context(patch("lib.deployment.create_managed_service"))
        health = stack.enter_context(patch.object(fixture.orch, "_poll_health"))
        return build, create, health

    def test_both_deployment_paths_install_the_same_binding(self):
        for prebuilt in (False, True):
            with self.subTest(prebuilt=prebuilt):
                fixture = self.fixture()
                build, create, _ = self.mocked_system(fixture)
                self.assertEqual(len(self.deploy(fixture, prebuilt=prebuilt)), 2)
                self.assertEqual(create.call_args.kwargs["env_file"], str(self.secret))
                if prebuilt:
                    build.assert_not_called()
                self.assertEqual(self.secret.read_text(), "PRIVATE_TOKEN=fixture-only-secret\n")

    def test_missing_binding_preserves_release_before_lock_or_service_changes(self):
        for prebuilt in (False, True):
            with self.subTest(prebuilt=prebuilt):
                fixture = self.fixture()
                destination = Path(self.destination)
                destination.mkdir()
                (destination / "old.txt").write_text("previous release")
                self.document["bindings"].clear()
                self.save()
                with patch.object(fixture.orch, "_open_deployment_lock") as lock:
                    with self.assertRaises(PermissionError):
                        self.deploy(fixture, prebuilt=prebuilt)
                    lock.assert_not_called()
                self.assertEqual((destination / "old.txt").read_text(), "previous release")
                # Restore the fixture grant before the next iteration.
                self.document["bindings"]["shop-api"] = {"deployment_root": self.destination,
                    "component": "api", "env_file": str(self.secret)}

    def test_revocation_during_build_refuses_before_stopping_services(self):
        fixture = self.fixture()
        build, create, _ = self.mocked_system(fixture)
        destination = Path(self.destination)
        destination.mkdir()
        (destination / "old.txt").write_text("previous release")

        def revoke(*args):
            self.document["bindings"].clear()
            self.save()

        build.side_effect = revoke
        with patch.object(fixture.orch, "_stop_app_unit") as stop:
            with self.assertRaises(PermissionError):
                self.deploy(fixture, prebuilt=False)
            stop.assert_not_called()
        create.assert_not_called()
        self.assertEqual((destination / "old.txt").read_text(), "previous release")

    def test_health_failure_restores_release_without_changing_private_file(self):
        for prebuilt in (False, True):
            with self.subTest(prebuilt=prebuilt):
                fixture = self.fixture()
                _, create, health = self.mocked_system(fixture)
                destination = Path(self.destination)
                destination.mkdir()
                (destination / "old.txt").write_text("previous release")
                health.side_effect = RuntimeError("fixture health failure")
                previous_secret = self.root / "previous.env"
                previous_secret.write_text("PRIVATE_TOKEN=previous-fixture\n")
                previous_secret.chmod(0o600)
                old_stat = previous_secret.stat()
                component = fixture.manifest.components[1]
                unit = Path(fixture.base_dir) / "units/app-example_com-api.service"
                old_unit = generate_managed_service(
                    "app-example_com-api", str(destination / "server/app"), str(destination),
                    env_file=str(previous_secret), description="basaltwater.json service: api",
                    writable_paths=[fixture.orch._component_data_dir(self.destination, component)])
                unit.write_text(old_unit)
                fixture.unit_state["ActiveState"] = "active"

                def install(*args, **kwargs):
                    unit.write_text(generate_managed_service(*args, **kwargs))

                create.side_effect = install
                before = self.secret.stat()
                with patch.object(fixture.orch, "_stop_app_unit", return_value=True):
                    with self.assertRaisesRegex(RuntimeError, "fixture health failure"):
                        self.deploy(fixture, prebuilt=prebuilt)
                self.assertEqual(create.call_args.kwargs["env_file"], str(self.secret))
                self.assertEqual((destination / "old.txt").read_text(), "previous release")
                self.assertEqual(unit.read_text(), old_unit)
                self.assertEqual(self.secret.stat(), before)
                self.assertEqual(previous_secret.stat(), old_stat)

    def test_interruption_keeps_operation_guard_and_private_file(self):
        fixture = self.fixture()
        _, create, _ = self.mocked_system(fixture)
        create.side_effect = KeyboardInterrupt()
        before = self.secret.stat()
        with self.assertRaises(KeyboardInterrupt):
            self.deploy(fixture, prebuilt=False)
        self.assertTrue(Path(fixture._operation_marker_path()).exists())
        self.assertEqual(self.secret.stat(), before)


if __name__ == "__main__":
    unittest.main()
