"""Contracts for admitted release and scoped route publication composition."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from lib.build_manifest import DEBIAN_PLATFORM, validate_manifest_request
from lib.build_protocol import DEFAULT_LIMITS
from lib.build_target_activation import TargetReleaseActivator
from lib.build_target_policy import authorize_target_deployment


class TargetReleaseActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.verify = self.root / "verify"
        self.apps = self.root / "apps"
        for directory in (self.state, self.verify, self.apps):
            directory.mkdir(mode=0o700)
        self.opened = []
        self.manifests = []

        @contextmanager
        def open_artifact(*args, **kwargs):
            self.opened.append((args, kwargs))
            yield str(self.root / "verified.bundle")

        self.activator = TargetReleaseActivator(str(self.state), str(self.verify), open_artifact=open_artifact)
        self.deploy = self.enterContext(patch(
            "lib.build_target_activation.DeploymentOrchestrator.deploy_prebuilt",
            autospec=True, side_effect=self.deploy_prebuilt,
        ))
        self.publish = self.enterContext(patch("lib.build_target_activation.create_nginx_sites_for_groups"))

    def inputs(self, app="shop", *, route="/", domain="shop.example", service=False):
        name = "api" if service else "site"
        export = "bin/api" if service else "dist"
        component = {"name": name, "type": "service" if service else "static",
                     "artifacts": [export], "commands": ["build project"], "env": {"BUILD": "release"}}
        request = {
            "schema": 1, "request_id": uuid.uuid4().hex, "project": app + "-source",
            "source": {"revision": "a" * 40, "snapshot_sha256": "b" * 64, "manifest_sha256": "c" * 64},
            "platform": dict(DEBIAN_PLATFORM), "components": [component], "limits": dict(DEFAULT_LIMITS),
        }
        target_component = {key: component[key] for key in ("name", "type", "artifacts")}
        target_component.update(domain=domain, path=route)
        if service:
            target_component.update(binary=export, health="/health")
        else:
            target_component.update(output=export, spa=True)
        projection = {"schema": 1, "application": app, "project": request["project"],
                      "platform": dict(DEBIAN_PLATFORM), "components": [target_component]}
        policy = {"schema": 1, "applications": {app: {
            "project": request["project"], "principals": ["controller"],
            "deployment_root": str(self.apps / app), "platform": dict(DEBIAN_PLATFORM),
            "limits": {key: DEFAULT_LIMITS[key] for key in ("artifact_bytes", "artifact_entries")},
            "components": {name: {key: target_component[key] for key in ("type", "domain", "path")}},
        }}}
        return authorize_target_deployment(policy, principal="controller", application=app,
                                           request=request, deployment=projection)["bindings"], request

    def deploy_prebuilt(self, manager, manifest, bundle, request, digest, domain, path, git_url, **kwargs):
        self.manifests.append(manifest)
        validate_manifest_request(manifest, request)
        self.assertEqual(kwargs["target_platform"], DEBIAN_PLATFORM)
        self.assertEqual(kwargs["staging_root"], str(self.verify))
        self.assertEqual(bundle, str(self.root / "verified.bundle"))
        self.assertIsNone(domain)
        destination = manager.get_deployment_path(domain, path, git_url)
        Path(destination).mkdir(mode=0o755, exist_ok=True)
        deployments = []
        for component in manifest.components:
            if component.is_service:
                component.port = 8123
            deployments.append(manager._component_dep(component, destination, domain))
        return deployments

    def activate(self, bindings, request, *, generation=1, operation_id=None):
        return self.activator(bindings=bindings, request=request, operation_id=operation_id or uuid.uuid4().hex,
                              generation=generation, artifact_sha256="d" * 64, principal="controller")

    def test_static_uses_exact_admin_root_and_preserves_build_identity(self):
        bindings, request = self.inputs()
        result = self.activate(bindings, request)
        manifest = self.manifests[0]
        self.assertEqual(manifest.components[0].build, ["build project"])
        self.assertEqual(manifest.components[0].env, {"BUILD": "release"})
        self.assertTrue(manifest.components[0].spa)
        self.assertEqual(self.publish.call_args.args[0]["shop.example"][0]["dest_path"], str(self.apps / "shop"))
        self.assertEqual(self.publish.call_args.kwargs["managed_domains"], {"shop.example"})
        self.assertEqual(self.opened[0][1]["owner"], "controller")
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("BUILD", json.dumps(result))
        self.assertEqual(result["state"], "completed")
        self.assertFalse((self.state / "activation.json").exists())

    def test_service_gets_only_fixed_host_port_runtime_convention(self):
        bindings, request = self.inputs(service=True)
        self.activate(bindings, request)
        component = self.manifests[0].components[0]
        self.assertEqual(component.runtime_env, {"PORT": "{{port}}", "HOST": "127.0.0.1"})
        self.assertIsNone(component.exec)
        self.assertIsNone(component.env_file)
        self.assertIsNone(component.working_dir)
        self.assertEqual(component.health, "/health")

    def test_second_application_preserves_shared_domain_route_group(self):
        first, request = self.inputs()
        self.activate(first, request)
        second, other = self.inputs("api-app", route="/v1", service=True)
        self.activate(second, other)
        grouped = self.publish.call_args.args[0]
        self.assertEqual({item["path"] for item in grouped["shop.example"]}, {"/", "/v1"})
        state = json.loads((self.state / "releases.json").read_text())
        self.assertEqual(set(state["applications"]), {"shop", "api-app"})

    def test_route_replacement_explicitly_includes_retired_domain_in_scope(self):
        first, request = self.inputs()
        self.activate(first, request)
        replacement, other = self.inputs(domain="new.example")
        self.activate(replacement, other, generation=2)
        self.assertEqual(set(self.publish.call_args.args[0]), {"new.example"})
        self.assertEqual(self.publish.call_args.kwargs["managed_domains"], {"shop.example", "new.example"})

    def test_same_or_older_generation_cannot_reactivate(self):
        bindings, request = self.inputs()
        operation_id = uuid.uuid4().hex
        self.activate(bindings, request, generation=2, operation_id=operation_id)
        for generation, identifier in ((2, operation_id), (1, uuid.uuid4().hex), (2, uuid.uuid4().hex)):
            with self.subTest(generation=generation, operation_id=identifier):
                with self.assertRaisesRegex(ValueError, "already applied"):
                    self.activate(bindings, request, generation=generation, operation_id=identifier)
        self.assertEqual(self.deploy.call_count, 1)

    def test_conflicting_active_root_route_or_identity_is_rejected_before_deploy(self):
        first, request = self.inputs()
        self.activate(first, request)
        for label in ("root", "route", "identity"):
            with self.subTest(label=label):
                bindings, other = self.inputs("other", domain="other.example")
                if label == "root":
                    bindings["deployment_root"] = str(self.apps / "shop" / "nested")
                elif label == "identity":
                    nested = self.apps / "nested"
                    nested.mkdir(exist_ok=True)
                    bindings["deployment_root"] = str(nested / "shop")
                else:
                    bindings["components"][0]["domain"] = "shop.example"
                with self.assertRaisesRegex(ValueError, "conflicts"):
                    self.activate(bindings, other)
        self.assertEqual(self.deploy.call_count, 1)

    def test_existing_app_root_change_needs_reconciliation(self):
        bindings, request = self.inputs()
        self.activate(bindings, request)
        changed = copy.deepcopy(bindings)
        changed["deployment_root"] = str(self.apps / "moved")
        with self.assertRaisesRegex(ValueError, "root requires operator"):
            self.activate(changed, request, generation=2)
        self.assertEqual(self.deploy.call_count, 1)

    def test_nginx_failure_retains_guard_and_blocks_following_activation(self):
        bindings, request = self.inputs()
        self.publish.side_effect = RuntimeError("reload failed")
        with self.assertRaisesRegex(RuntimeError, "reload failed"):
            self.activate(bindings, request)
        marker = json.loads((self.state / "activation.json").read_text())
        self.assertEqual(marker["status"], "recovery_required")
        self.assertEqual(json.loads((self.state / "releases.json").read_text())["applications"], {})
        self.publish.side_effect = None
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.activate(bindings, request, generation=2)
        self.assertEqual(self.deploy.call_count, 1)

    def test_registry_save_failure_after_publication_stays_guarded(self):
        bindings, request = self.inputs()
        self.activate(bindings, request)
        changed, other = self.inputs(domain="new.example")
        with patch.object(self.activator, "_save", side_effect=OSError("state unavailable")):
            with self.assertRaises(OSError):
                self.activate(changed, other, generation=2)
        self.assertTrue((self.state / "activation.json").exists())
        self.assertEqual(json.loads((self.state / "releases.json").read_text())["applications"]["shop"]["generation"], 1)

    def test_missing_initialized_registry_or_witness_does_not_reset_history(self):
        bindings, request = self.inputs()
        self.activate(bindings, request)
        registry = self.state / "releases.json"
        saved = registry.read_bytes()
        registry.unlink()
        with self.assertRaisesRegex(ValueError, "registry is missing"):
            self.activate(bindings, request, generation=2)
        registry.write_bytes(saved)
        registry.chmod(0o600)
        (self.state / ".initialized").unlink()
        with self.assertRaisesRegex(ValueError, "witness is missing"):
            self.activate(bindings, request, generation=2)
        self.assertEqual(self.deploy.call_count, 1)

    def test_unsafe_private_roots_and_deployment_parent_refuse_without_native_work(self):
        bindings, request = self.inputs()
        self.apps.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "parent must not"):
            self.activate(bindings, request)
        self.apps.chmod(0o700)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "owned mode 0700"):
            self.activate(bindings, request)
        self.deploy.assert_not_called()

    def test_private_path_overlap_and_forged_principal_are_rejected(self):
        bindings, request = self.inputs()
        bindings["deployment_root"] = str(self.state / "release")
        with self.assertRaisesRegex(ValueError, "overlaps private"):
            self.activate(bindings, request)
        bindings, request = self.inputs()
        bindings["principal"] = "another"
        with self.assertRaises(PermissionError):
            self.activate(bindings, request)
        self.deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
