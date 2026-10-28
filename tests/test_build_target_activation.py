"""Contracts for admitted release and scoped route publication composition."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import uuid

from lib.build_manifest import DEBIAN_PLATFORM, validate_manifest_request
from lib.build_admission import DeploymentAdmissionStore
from lib.build_artifacts import create_artifact
from lib.build_protocol import DEFAULT_LIMITS, encode_document
from lib.build_target_activation import TargetReleaseActivator, parse_expected_releases
from lib.build_target_artifacts import BuildTargetArtifactStore
from lib.build_target_policy import authorize_target_deployment
from lib.deployment import DeploymentOrchestrator
from lib.nginx_config import generate_merged_nginx_config
from lib.systemd_service import generate_managed_service
from tests.test_manifest_deploy import _prebuilt_elf64


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
        self.bundle_path = self.root / "verified.bundle"

        @contextmanager
        def open_artifact(*args, **kwargs):
            self.opened.append((args, kwargs))
            yield str(self.bundle_path)

        self.activator = TargetReleaseActivator(str(self.state), str(self.verify), open_artifact=open_artifact)
        self.real_deploy_prebuilt = DeploymentOrchestrator.deploy_prebuilt
        self.deploy = self.enterContext(patch(
            "lib.build_target_activation.DeploymentOrchestrator.deploy_prebuilt",
            autospec=True, side_effect=self.deploy_prebuilt,
        ))
        self.publish = self.enterContext(patch("lib.build_target_activation.create_nginx_sites_for_groups"))

    def inputs(self, app="shop", *, route="/", domain="shop.example", service=False, with_api=False):
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
        if with_api:
            request["components"].append({
                "name": "api", "type": "service", "artifacts": ["bin/api"],
                "commands": ["CGO_ENABLED=0 go build -o bin/api ./cmd/api"], "env": {},
            })
            projection["components"].append({
                "name": "api", "type": "service", "artifacts": ["bin/api"],
                "domain": domain, "path": "/api", "binary": "bin/api", "health": "/health",
            })
            policy["applications"][app]["components"]["api"] = {
                "type": "service", "domain": domain, "path": "/api",
            }
        return authorize_target_deployment(policy, principal="controller", application=app,
                                           request=request, deployment=projection)["bindings"], request

    def deploy_prebuilt(self, manager, manifest, bundle, request, digest, domain, path, git_url, **kwargs):
        self.manifests.append(manifest)
        self.assertEqual((manager.deploy_user, manager.deploy_group), ("root", "root"))
        validate_manifest_request(manifest, request)
        self.assertEqual(kwargs["target_platform"], DEBIAN_PLATFORM)
        self.assertEqual(kwargs["staging_root"], str(self.verify))
        self.assertEqual(bundle, str(self.bundle_path))
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

    @staticmethod
    def identity(receipt):
        return {key: receipt[key] for key in ("application", "operation_id", "generation", "artifact_sha256")}

    def admission_store(self, *, clock=None):
        root = self.root / "admission"
        root.mkdir(mode=0o700)
        return DeploymentAdmissionStore(root / "admissions.json", clock=clock)

    def admit_release(self, admissions, bindings, request):
        digest = hashlib.sha256(encode_document(bindings)).hexdigest()
        reservation = admissions.reserve(bindings["application"], request, owner="controller",
                                         idempotency_key=uuid.uuid4().hex, bindings_sha256=digest)
        receipt = admissions.activate(
            reservation["operation_id"], reservation["generation"], request, "d" * 64,
            owner="controller", bindings_sha256=digest,
            action=lambda: self.activate(bindings, request, generation=reservation["generation"],
                                         operation_id=reservation["operation_id"]),
        )
        return self.identity(receipt)

    def evidence(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

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
        self.assertEqual(self.opened[0][1]["application"], "shop")
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

    def test_combined_spa_api_admission_activation_and_retained_shared_domain_reconcile(self):
        # Exercise real release verification/activation, without host services,
        # compilers, source fetches or sockets. The ELF fixture models the
        # self-contained Go output accepted by the runtime validator.
        self.deploy.side_effect = self.real_deploy_prebuilt
        units = self.root / "units"
        units.mkdir(mode=0o700)
        self.enterContext(patch("lib.deployment.SYSTEMD_UNIT_DIR", str(units)))
        self.enterContext(patch.object(DeploymentOrchestrator, "_get_used_ports", return_value=set()))
        find_port = self.enterContext(patch.object(
            DeploymentOrchestrator, "_find_free_port", return_value=8123,
        ))
        self.enterContext(patch("lib.unit_transaction.run", return_value=MagicMock(
            returncode=0, stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=enabled",
        )))

        def run(command, **_kwargs):
            self.assertFalse(any(token in command for token in ("git ", "curl ", "wget ", "go build")))
            return MagicMock(returncode=0, stdout="", stderr="")

        self.enterContext(patch("lib.deployment.run", side_effect=run))
        for method in ("_ensure_build_user", "_prepare_build_toolchain", "_run_component_build"):
            self.enterContext(patch.object(DeploymentOrchestrator, method, side_effect=AssertionError(method)))

        def install_unit(*args, **kwargs):
            (units / (args[0] + ".service")).write_text(generate_managed_service(*args, **kwargs))

        service = self.enterContext(patch("lib.deployment.create_managed_service", side_effect=install_unit))
        response = MagicMock()
        response.__enter__.return_value.status = 200
        health = self.enterContext(patch("lib.deployment.open_loopback", return_value=response))
        admissions = self.admission_store()
        artifact_root = self.root / "artifacts"
        artifact_root.mkdir(mode=0o700)
        artifacts = BuildTargetArtifactStore(artifact_root)
        self.activator = TargetReleaseActivator(str(self.state), str(self.verify),
                                               open_artifact=artifacts.open_verified_bundle)

        def stage_and_activate(bindings, request, *, api=False):
            app = bindings["application"]
            request["components"][0].update(commands=[], env={})
            workspace = self.root / (app + "-workspace")
            (workspace / "dist").mkdir(parents=True, mode=0o700)
            (workspace / "dist" / "index.html").write_text(app + " frontend")
            if api:
                (workspace / "bin").mkdir()
                (workspace / "bin" / "api").write_bytes(_prebuilt_elf64())
                (workspace / "bin" / "api").chmod(0o755)
            bundle = self.root / (app + ".bundle")
            artifact = create_artifact(str(workspace), request, str(bundle))
            digest = hashlib.sha256(encode_document(bindings)).hexdigest()
            reservation = admissions.reserve(app, request, owner="controller",
                                             idempotency_key=uuid.uuid4().hex, bindings_sha256=digest)
            with bundle.open("rb") as stream:
                staged = artifacts.stage(
                    reservation["operation_id"], reservation["generation"], request,
                    application=app, owner="controller", bindings_sha256=digest,
                    artifact_sha256=artifact["sha256"], artifact_size=artifact["size"],
                    source=stream, admissions=admissions,
                )

            def activate():
                return self.activator(bindings=bindings, request=request,
                                      operation_id=reservation["operation_id"],
                                      generation=reservation["generation"],
                                      artifact_sha256=artifact["sha256"], principal="controller")

            result = admissions.activate(
                reservation["operation_id"], reservation["generation"], request, artifact["sha256"],
                owner="controller", bindings_sha256=digest, action=activate,
            )
            replay = admissions.activate(
                reservation["operation_id"], reservation["generation"], request, artifact["sha256"],
                owner="controller", bindings_sha256=digest, action=activate,
            )
            self.assertEqual(result, replay)
            self.assertEqual(staged["request_id"], request["request_id"])
            self.assertEqual(staged["generation"], result["generation"])
            self.assertEqual(staged["artifact_sha256"], result["artifact_sha256"])
            shutil.rmtree(workspace)
            bundle.unlink()
            return result

        neighbor, neighbor_request = self.inputs("neighbor", route="/neighbor")
        neighbor_result = stage_and_activate(neighbor, neighbor_request)
        neighbor_record = json.loads((self.state / "releases.json").read_text())["applications"]["neighbor"]
        neighbor_bytes = (self.apps / "neighbor" / "dist" / "index.html").read_bytes()
        self.deploy.reset_mock()
        self.publish.reset_mock()
        bindings, request = self.inputs(with_api=True)
        with patch.object(admissions, "reserve", wraps=admissions.reserve) as reserve:
            result = stage_and_activate(bindings, request, api=True)
        reserve.assert_called_once()
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.deploy.call_count, 1)
        self.assertEqual(self.publish.call_count, 1)
        self.assertEqual(len(list(artifact_root.glob("*.bundle"))), 2)
        staged_bundle = artifact_root / (result["operation_id"] + ".bundle")
        self.assertEqual(staged_bundle.stat().st_mode & 0o777, 0o400)
        self.assertEqual((self.apps / "shop" / "dist" / "index.html").read_text(), "shop frontend")
        self.assertEqual((self.apps / "shop" / "bin" / "api").read_bytes(), _prebuilt_elf64())
        self.assertTrue((self.apps / "shop" / "bin" / "api").stat().st_mode & 0o111)
        service.assert_called_once()
        self.assertEqual(service.call_args.args[:5], (
            "app-shop-api", str(self.apps / "shop" / "bin" / "api"), str(self.apps / "shop"),
            "app-shop-api", "app-shop-api",
        ))
        self.assertEqual(service.call_args.kwargs["runtime_env"], {"PORT": "8123", "HOST": "127.0.0.1"})
        self.assertEqual(health.call_args.args[0].full_url, "http://127.0.0.1:8123/health")
        groups = self.publish.call_args.args[0]
        self.assertEqual(set(groups), {"shop.example"})
        routes = {item["path"]: item for item in groups["shop.example"]}
        self.assertEqual(set(routes), {"/", "/api", "/neighbor"})
        self.assertEqual(routes["/neighbor"], neighbor_record["deployments"][0])
        self.assertTrue(routes["/"]["spa"])
        self.assertTrue(routes["/api"]["preserve_path"])
        self.assertEqual(self.publish.call_args.kwargs["managed_domains"], {"shop.example"})
        rendered = generate_merged_nginx_config("shop.example", groups["shop.example"], enable_https_redirect=False)
        self.assertIn("try_files $uri $uri.html $uri/ /index.html;", rendered)
        self.assertIn("location /api/ {", rendered)
        self.assertIn("proxy_pass http://127.0.0.1:8123;", rendered)
        self.assertIn("location /neighbor/ {", rendered)
        before = (self.state / "releases.json").read_bytes()
        state = json.loads(before)["applications"]
        self.assertEqual(set(state), {"shop", "neighbor"})
        self.assertEqual(state["neighbor"], neighbor_record)
        self.assertEqual(state["shop"]["request"], request)
        self.assertEqual(state["shop"]["operation_id"], result["operation_id"])
        self.assertEqual(len(state["shop"]["deployments"]), 2)
        self.deploy.reset_mock()
        self.publish.reset_mock()
        original_unit = (units / "app-shop-api.service").read_bytes()
        with patch.object(admissions, "reserve") as reserve, patch.object(admissions, "activate") as activate:
            refreshed = self.activator.reconcile_installed(expected_releases=[self.identity(result)], admissions=admissions)
        reserve.assert_not_called()
        activate.assert_not_called()
        self.assertEqual({item["application"]: self.identity(item) for item in refreshed},
                         {item["application"]: self.identity(item) for item in (result, neighbor_result)})
        self.assertTrue(all(item["state"] == "reconciled" for item in refreshed))
        self.assertEqual((self.state / "releases.json").read_bytes(), before)
        self.assertEqual((self.apps / "neighbor" / "dist" / "index.html").read_bytes(), neighbor_bytes)
        self.assertEqual((self.apps / "shop" / "dist" / "index.html").read_text(), "shop frontend")
        self.assertEqual((self.apps / "shop" / "bin" / "api").read_bytes(), _prebuilt_elf64())
        self.assertEqual((units / "app-shop-api.service").read_bytes(), original_unit)
        find_port.assert_called_once()
        self.assertEqual(self.deploy.call_count, 2)
        self.publish.assert_called_once_with(groups, managed_domains={"shop.example"}, enable_https_redirect=True)
        self.assertFalse((self.state / "activation.json").exists())

    def test_second_application_preserves_shared_domain_route_group(self):
        first, request = self.inputs()
        self.activate(first, request)
        second, other = self.inputs("api-app", route="/v1", service=True)
        self.activate(second, other)
        grouped = self.publish.call_args.args[0]
        self.assertEqual({item["path"] for item in grouped["shop.example"]}, {"/", "/v1"})
        state = json.loads((self.state / "releases.json").read_text())
        self.assertEqual(set(state["applications"]), {"shop", "api-app"})

    def test_local_refresh_reuses_installed_inputs_and_preserves_generation(self):
        bindings, request = self.inputs()
        first = self.activate(bindings, request)
        other, service = self.inputs("api-app", route="/v1", service=True)
        second = self.activate(other, service)
        before = json.loads((self.state / "releases.json").read_text())
        self.deploy.reset_mock()
        self.publish.reset_mock()
        refreshed = self.activator.reconcile_installed()
        self.assertEqual(self.deploy.call_count, 2)
        self.assertEqual(self.publish.call_count, 1)
        self.assertEqual({item["operation_id"] for item in refreshed},
                         {first["operation_id"], second["operation_id"]})
        self.assertTrue(all(item["state"] == "reconciled" for item in refreshed))
        self.assertEqual(json.loads((self.state / "releases.json").read_text()), before)
        self.assertEqual({item["path"] for item in self.publish.call_args.args[0]["shop.example"]},
                         {"/", "/v1"})
        self.assertEqual(self.manifests[-2].components[0].runtime_env,
                         {"PORT": "{{port}}", "HOST": "127.0.0.1"})
        self.assertFalse((self.state / "activation.json").exists())

    def test_refresh_checks_all_retained_bundles_before_any_release_change(self):
        first, request = self.inputs()
        self.activate(first, request)
        other, service = self.inputs("api-app", route="/v1", service=True)
        self.activate(other, service)
        self.deploy.reset_mock()
        self.publish.reset_mock()

        @contextmanager
        def missing_second(*_args, **kwargs):
            if kwargs["application"] == "shop":
                raise FileNotFoundError("retained bundle unavailable")
            yield str(self.bundle_path)

        self.activator.open_artifact = missing_second
        with self.assertRaises(FileNotFoundError):
            self.activator.reconcile_installed()
        self.deploy.assert_not_called()
        self.publish.assert_not_called()

    def test_expected_refresh_checks_every_identity_before_preflight_or_writes(self):
        bindings, request = self.inputs()
        first = self.identity(self.activate(bindings, request))
        other, second_request = self.inputs("other", domain="other.example")
        second = self.identity(self.activate(other, second_request))
        self.deploy.reset_mock()
        self.publish.reset_mock()
        self.opened.clear()
        before = self.evidence()
        for field, changed in (("operation_id", "e" * 32), ("generation", 2),
                               ("artifact_sha256", "e" * 64), ("application", "missing")):
            with self.subTest(field=field), patch("lib.build_target_activation.OperationStateStore") as operation:
                stale = dict(second, **{field: changed})
                with self.assertRaisesRegex(ValueError, "controller"):
                    self.activator.reconcile_installed(expected_releases=[first, stale])
                operation.assert_not_called()
                self.assertEqual(self.evidence(), before)
        self.deploy.assert_not_called()
        self.publish.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_expected_refresh_rejects_noncanonical_or_duplicate_input_without_state_access(self):
        valid = {"application": "shop", "operation_id": "a" * 32, "generation": 1,
                 "artifact_sha256": "b" * 64}
        invalid = [[], {}, (valid,), "[]", [None], [dict(valid, extra=True)],
                   [{key: value for key, value in valid.items() if key != "application"}],
                   [valid, valid], [valid, dict(valid, application="other")],
                   [valid, dict(valid, operation_id="c" * 32)], [valid] * 17]
        for field, values in {
            "application": (None, 1, "Shop", "shop\n", "a" * 65, ""),
            "operation_id": (None, 1, "A" * 32, "a" * 31, "a" * 32 + "\n"),
            "generation": (None, True, False, 1.0, "1", 0, -1, 2**63),
            "artifact_sha256": (None, 1, "B" * 64, "b" * 63, "b" * 64 + "\n"),
        }.items():
            invalid.extend([dict(valid, **{field: value})] for value in values)
        with patch.object(self.activator, "_load") as load, patch(
            "lib.build_target_activation.OperationStateStore",
        ) as operation:
            for value in invalid:
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "[Ee]xpected"):
                    self.activator.reconcile_installed(expected_releases=value)
            load.assert_not_called()
            operation.assert_not_called()
        self.assertEqual(self.evidence(), {})
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()
        self.publish.assert_not_called()

    def test_expected_refresh_missing_registry_does_not_initialize_it(self):
        expected = [{"application": "shop", "operation_id": "a" * 32, "generation": 1,
                     "artifact_sha256": "b" * 64}]
        with self.assertRaisesRegex(ValueError, "controller"):
            self.activator.reconcile_installed(expected_releases=expected)
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()

    def test_expected_identity_list_accepts_boundaries_and_detaches_input(self):
        expected = [{"application": "app-" + str(index), "operation_id": f"{index:032x}",
                     "generation": 2**63 - 1, "artifact_sha256": "a" * 64} for index in range(16)]
        parsed = parse_expected_releases(expected)
        self.assertEqual(parsed, expected)
        expected[0]["generation"] = 1
        expected.clear()
        self.assertEqual(len(parsed), 16)
        self.assertEqual(parsed[0]["generation"], 2**63 - 1)

    def test_exact_expected_refresh_preserves_unrelated_apps_and_does_not_admit(self):
        admissions = self.admission_store()
        bindings, request = self.inputs()
        expected = self.admit_release(admissions, bindings, request)
        other, second_request = self.inputs("other", domain="other.example")
        unrelated = self.activate(other, second_request)
        retained = (self.state / "releases.json", Path(admissions.path))
        before = {path: path.read_bytes() for path in retained}
        self.deploy.reset_mock()
        self.publish.reset_mock()
        with patch.object(admissions, "reserve") as reserve, patch.object(admissions, "activate") as activate:
            receipts = self.activator.reconcile_installed(expected_releases=[expected], admissions=admissions)
        reserve.assert_not_called()
        activate.assert_not_called()
        self.assertEqual(self.deploy.call_count, 2)
        self.publish.assert_called_once()
        self.assertEqual({path: path.read_bytes() for path in retained}, before)
        self.assertEqual({item["operation_id"] for item in receipts},
                         {expected["operation_id"], unrelated["operation_id"]})

    def test_expected_guard_is_read_only_and_checks_admission_before_activation_ownership(self):
        clock = [100.0]
        admissions = self.admission_store(clock=lambda: clock[0])
        bindings, request = self.inputs()
        expected = self.admit_release(admissions, bindings, request)
        clock[0] += 3601  # Completion is retained after the reservation TTL.
        self.assertTrue(admissions.status(expected["operation_id"], owner="controller")["expired"])
        before = self.evidence()
        self.opened.clear()
        self.deploy.reset_mock()
        self.publish.reset_mock()
        self.activator.check_expected_releases(expected_releases=[expected], admissions=admissions)
        self.assertEqual(self.evidence(), before)
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()
        self.publish.assert_not_called()

        original_status = admissions.status

        def status(*args, **kwargs):
            # A status query must be outside activation ownership. If it were
            # nested here, this second flock would fail instead of acquiring.
            from lib.operation_state import OperationStateStore
            with OperationStateStore(str(self.state / "activation.json")).ownership():
                return original_status(*args, **kwargs)

        with patch.object(admissions, "status", side_effect=status):
            receipts = self.activator.reconcile_installed(expected_releases=[expected], admissions=admissions)
        self.assertEqual(self.identity(receipts[0]), expected)

    def test_pending_or_cancelled_or_expired_newer_intent_refuses_expected_old_release(self):
        clock = [100.0]
        admissions = self.admission_store(clock=lambda: clock[0])
        bindings, request = self.inputs()
        expected = self.admit_release(admissions, bindings, request)
        self.deploy.reset_mock()
        self.publish.reset_mock()
        self.opened.clear()
        for state in ("reserved", "cancelled", "expired"):
            with self.subTest(state=state):
                newer = admissions.reserve("shop", request, owner="controller", idempotency_key=uuid.uuid4().hex,
                                           bindings_sha256=hashlib.sha256(encode_document(bindings)).hexdigest())
                if state == "cancelled":
                    admissions.cancel(newer["operation_id"], owner="controller")
                elif state == "expired":
                    clock[0] += 3601
                before = self.evidence()
                with self.assertRaisesRegex(ValueError, "current completed admission.*controller"):
                    self.activator.reconcile_installed(expected_releases=[expected], admissions=admissions)
                self.assertEqual(self.evidence(), before)
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()
        self.publish.assert_not_called()

    def test_expected_refresh_requires_existing_initialized_admission_evidence(self):
        bindings, request = self.inputs()
        expected = self.identity(self.activate(bindings, request))
        admissions = self.admission_store()
        self.opened.clear()
        self.deploy.reset_mock()
        self.publish.reset_mock()
        for evidence in ("absent", "empty-witness", "unknown-operation"):
            with self.subTest(evidence=evidence):
                if evidence != "absent":
                    admissions.reserve("shop", request, owner="controller", idempotency_key=uuid.uuid4().hex,
                                       bindings_sha256=hashlib.sha256(encode_document(bindings)).hexdigest())
                if evidence == "empty-witness":
                    Path(admissions.path + ".lock").write_bytes(b"")
                before = self.evidence()
                with self.assertRaisesRegex(ValueError, "controller"):
                    self.activator.reconcile_installed(expected_releases=[expected], admissions=admissions)
                self.assertEqual(self.evidence(), before)
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()
        self.publish.assert_not_called()

    def test_expected_guard_refuses_current_incomplete_admission_without_mutations(self):
        admissions = self.admission_store()
        for state in ("reserved", "cancelled", "uncertain"):
            with self.subTest(state=state):
                bindings, request = self.inputs(state, domain=state + ".example")
                digest = hashlib.sha256(encode_document(bindings)).hexdigest()
                reservation = admissions.reserve(state, request, owner="controller", idempotency_key=uuid.uuid4().hex,
                                                 bindings_sha256=digest)

                def install():
                    return self.activate(bindings, request, operation_id=reservation["operation_id"])

                if state == "uncertain":
                    def interrupted():
                        install()
                        raise RuntimeError("completion not acknowledged")

                    with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                        admissions.activate(reservation["operation_id"], 1, request, "d" * 64,
                                             owner="controller", bindings_sha256=digest, action=interrupted)
                    expected = self.identity(self.activator._load()["applications"][state] | {"application": state})
                else:
                    expected = self.identity(install())
                    if state == "cancelled":
                        admissions.cancel(reservation["operation_id"], owner="controller")
                before = self.evidence()
                self.opened.clear()
                self.deploy.reset_mock()
                self.publish.reset_mock()
                with self.assertRaisesRegex(ValueError, "controller"):
                    self.activator.check_expected_releases(expected_releases=[expected], admissions=admissions)
                self.assertEqual(self.evidence(), before)
                self.assertEqual(self.opened, [])
                self.deploy.assert_not_called()
                self.publish.assert_not_called()

    def test_expected_guard_refuses_unfinished_refresh_before_profile_mutations(self):
        admissions = self.admission_store()
        bindings, request = self.inputs()
        expected = self.admit_release(admissions, bindings, request)
        self.deploy.side_effect = RuntimeError("refresh failed")
        with self.assertRaisesRegex(RuntimeError, "refresh failed"):
            self.activator.reconcile_installed()
        before = self.evidence()
        self.opened.clear()
        self.deploy.reset_mock()
        with self.assertRaisesRegex(ValueError, "operator reconciliation.*controller"):
            self.activator.check_expected_releases(expected_releases=[expected], admissions=admissions)
        self.assertEqual(self.evidence(), before)
        self.assertEqual(self.opened, [])
        self.deploy.assert_not_called()

    def test_refresh_rejects_missing_or_changed_installed_descriptor(self):
        bindings, request = self.inputs()
        self.activate(bindings, request)
        path = self.state / "releases.json"
        original = json.loads(path.read_text())
        self.deploy.reset_mock()
        for change in ("missing", "request", "root"):
            with self.subTest(change=change):
                state = copy.deepcopy(original)
                record = state["applications"]["shop"]
                if change == "missing":
                    del record["request"]
                elif change == "request":
                    record["request"]["components"][0]["commands"] = ["different build"]
                else:
                    record["bindings"]["deployment_root"] = str(self.apps / "elsewhere")
                path.write_bytes(encode_document(state))
                with self.assertRaises(ValueError):
                    self.activator.reconcile_installed()
        self.deploy.assert_not_called()

    def test_refresh_failure_retains_guard_and_prevents_replay(self):
        bindings, request = self.inputs()
        self.activate(bindings, request)
        self.deploy.reset_mock()
        self.deploy.side_effect = RuntimeError("unit replacement failed")
        with self.assertRaisesRegex(RuntimeError, "unit replacement failed"):
            self.activator.reconcile_installed()
        marker = json.loads((self.state / "activation.json").read_text())
        self.assertEqual(marker["status"], "recovery_required")
        self.assertEqual(marker["operation_type"], "target-release-refresh")
        self.deploy.side_effect = self.deploy_prebuilt
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.activator.reconcile_installed()
        self.assertEqual(self.deploy.call_count, 1)

    def test_refresh_without_installed_releases_requires_controller(self):
        with self.assertRaisesRegex(ValueError, "run deployment from the controller"):
            self.activator.reconcile_installed()
        self.deploy.assert_not_called()

    def test_large_installed_descriptor_is_refused_before_release_changes(self):
        bindings, request = self.inputs()
        with patch("lib.build_target_activation.MAX_REGISTRY_BYTES", 1024**2 + 64):
            with self.assertRaises(ValueError):
                self.activate(bindings, request)
        self.deploy.assert_not_called()

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
                    nested.mkdir(mode=0o700, exist_ok=True)
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
