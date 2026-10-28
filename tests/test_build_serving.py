"""Approved serving metadata uses the ordinary prebuilt activation boundary."""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lib.build_artifacts import create_artifact
from lib.build_manifest import request_from_manifest
from lib.build_setup_plan import prepare_delivery_plan
from lib.build_target_activation import _bindings_manifest, _runtime_collisions
from lib.build_target_policy import authorize_target_deployment, parse_target_policy, capabilities_for_principal
from lib.deployment import DeploymentOrchestrator
from lib.manifest_serving import parse_runtime, runtime_from_component
from lib.project_manifest import artifact_paths, parse_manifest
from tests.test_build_setup_plan import enrollment, capabilities
from tests.test_build_runtime import _elf64
from tests import test_build_target_activation as activation_tests


def service(exec_only=False, stun=3478):
    entry = {
        "name": "api", "type": "service", "domain": "{{domain}}", "path": "/v1",
        "port": "auto", "build": "go build ./...", "env": {"CGO_ENABLED": "0"},
        "health": "/up", "binary": ".basaltwater/bin/goclick",
        "runtime_env": {"GOCLICK_ADDR": "127.0.0.1:{{port}}",
                        "GOCLICK_DATABASE": "{{data_dir}}/goclick.db",
                        "SECURE_COOKIES": "true", "TRUST_PROXY": "true"},
        "sqlite_backup": "{{data_dir}}/goclick.db", "backup_retention": 14,
    }
    if exec_only:
        entry.pop("binary")
        entry.update(exec="{{release_dir}}/.basaltwater/bin/antistatic-server -host 127.0.0.1 "
                     "-port {{port}} -trust-proxy -trusted-proxy-cidrs 127.0.0.1/32,::1/128 "
                     f"-stun-port {stun}", runtime_env={"ANTISTATIC_DATA_DIR": "{{data_dir}}"},
                     health="/health", sqlite_backup=None, backup_retention=10,
                     public_udp=[{"port": 3478, "hostname": "stun.antistaticgame.com"}] if stun else [])
    return entry


class ServingTests(unittest.TestCase):
    def plan(self, entry, grant=True):
        manifest = parse_manifest({"version": 1, "build_runtimes": ["go"], "components": [entry]})
        configured = enrollment()
        routes = configured["policy"]["applications"]["site"]["components"]
        routes.pop("web")
        if grant:
            routes["api"]["runtime"] = runtime_from_component(manifest.components[0])
        plan = prepare_delivery_plan(
            manifest, destination="example.com", source={"revision": "b" * 40,
            "snapshot_sha256": "c" * 64, "manifest_sha256": "d" * 64},
            source_path="/tmp/absent.snapshot", request_id="a" * 32, idempotency_key="e" * 32,
            target_enrollment=configured, target_principal="alice", builder_capabilities=capabilities())
        checked = authorize_target_deployment(configured["policy"], principal="alice", application="site",
                                              request=plan.request, deployment=plan.deployment)
        return manifest, plan, checked, configured

    def test_ordinary_manifests_preserve_runtime_through_real_prebuilt_activation(self):
        for entry in (service(), service(True), service(True, 0)):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as temporary:
                original, plan, checked, _ = self.plan(entry)
                _, reconstructed = _bindings_manifest(checked["bindings"], plan.request, "alice")
                expected = original.components[0]
                actual = reconstructed.components[0]
                for field in ("exec", "runtime_env", "sqlite_backup", "backup_retention", "public_udp", "health"):
                    self.assertEqual(getattr(actual, field), getattr(expected, field))
                self.assertEqual(plan.request, request_from_manifest(original, request_id="a" * 32,
                                 project="approved-source", source=plan.request["source"],
                                 platform=plan.request["platform"], limits=plan.request["limits"]))
                self.assertNotIn("runtime", plan.request["components"][0])
                root = Path(temporary)
                workspace = root / "workspace"
                binary = workspace / artifact_paths(expected)[0]
                binary.parent.mkdir(parents=True)
                binary.write_bytes(_elf64())
                binary.chmod(0o755)
                bundle = root / "release.bundle"
                artifact = create_artifact(str(workspace), plan.request, str(bundle))
                staging = root / "staging"
                staging.mkdir(mode=0o700)
                units = root / "units"
                units.mkdir()
                response = MagicMock()
                response.__enter__.return_value.status = 200
                manager = DeploymentOrchestrator(base_dir=str(root / "apps"), deploy_user="root", deploy_group="root")
                with patch("lib.deployment.SYSTEMD_UNIT_DIR", str(units)), patch(
                    "lib.deployment.run", return_value=MagicMock(returncode=0, stdout="", stderr="")
                ), patch("lib.unit_transaction.run", return_value=MagicMock(returncode=0, stdout="")), patch.object(
                    manager, "_get_used_ports", return_value=set()
                ), patch.object(manager, "_find_free_port", return_value=8123), patch(
                    "lib.deployment.create_managed_service"
                ) as install, patch("lib.deployment.open_loopback", return_value=response) as health:
                    manager.deploy_prebuilt(reconstructed, str(bundle), plan.request, artifact["sha256"],
                                            None, "/app", "https://example.com/source.git", staging_root=str(staging),
                                            target_platform=plan.request["platform"])
                self.assertEqual(health.call_args.args[0].full_url, "http://127.0.0.1:8123" + entry["health"])
                env = install.call_args.kwargs["runtime_env"]
                if expected.exec:
                    self.assertIn("-port 8123", install.call_args.args[1])
                    self.assertIn("-stun-port " + ("3478" if expected.public_udp else "0"), install.call_args.args[1])
                    self.assertIn("ANTISTATIC_DATA_DIR", env)
                else:
                    self.assertEqual(env["GOCLICK_ADDR"], "127.0.0.1:8123")
                    self.assertTrue(env["GOCLICK_DATABASE"].endswith("/data/goclick.db"))
                    self.assertEqual(env["SECURE_COOKIES"], "true")

    def test_runtime_requires_exact_grant_and_affects_binding_digest(self):
        with self.assertRaises(PermissionError):
            self.plan(service(), grant=False)
        _, plan, checked, configured = self.plan(service())
        changed = copy.deepcopy(plan.deployment)
        changed["components"][0]["runtime"]["backup_retention"] = 13
        with self.assertRaises(PermissionError):
            authorize_target_deployment(configured["policy"], principal="alice", application="site",
                                        request=plan.request, deployment=changed)
        configured["policy"]["applications"]["site"]["components"]["api"]["runtime"]["backup_retention"] = 13
        other = authorize_target_deployment(configured["policy"], principal="alice", application="site",
                                           request=plan.request, deployment=changed)
        self.assertNotEqual(checked["bindings_sha256"], other["bindings_sha256"])
        self.assertNotIn("runtime", capabilities_for_principal(configured["policy"], principal="alice")["applications"]["site"]["components"][0])
        with self.assertRaises(PermissionError):
            _bindings_manifest(checked["bindings"], plan.request, "bob")

    def test_runtime_rejects_unsafe_commands_paths_expansion_and_secret_bindings(self):
        values = [{"exec": command} for command in (
            "/bin/sh", "{{release_dir}}/bin/python3", "{{release_dir}}/../outside",
            "'{{release_dir}}/bin/api'", "{{release_dir}}/bin/api --value $TOKEN",
            "{{release_dir}}/bin/api --value %n", '{{release_dir}}/bin/api "unterminated',
        )] + [{"working_dir": path} for path in (
            "/etc", "{{release_dir}}/../other", "{{data_dir}}/{{path}}", "{{release_dir}}//bin",
        )] + [{"env_file": "/etc/secret"}, {"secret": "name"}, {"port": True},
              {"sqlite_backup": "/tmp/app.db"}, {"runtime_env": {"VALUE": "{{env_file}}"}},
              {"runtime_env": {"VALUE": "%n"}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_runtime(value)

    def test_policy_and_retained_manifests_reject_udp_and_port_conflicts(self):
        original, _, _, configured = self.plan(service(True))
        with self.assertRaisesRegex(ValueError, "declared by both"):
            _runtime_collisions([original, original])
        grant = copy.deepcopy(configured["policy"]["applications"]["site"])
        grant["deployment_root"] = "/srv/apps/other"
        grant["components"]["api"]["domain"] = "other.example"
        configured["policy"]["applications"]["other"] = grant
        with self.assertRaisesRegex(ValueError, "UDP"):
            parse_target_policy(configured["policy"])
        original.components[0].public_udp = []
        original.components[0].port = 8123
        with self.assertRaisesRegex(ValueError, "service port"):
            _runtime_collisions([original], {8123})

    def test_retained_runtime_replay_and_conflicts_precede_release_changes(self):
        fixture = activation_tests.TargetReleaseActivationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first, request = fixture.inputs(service=True)
        runtime = parse_runtime({"runtime_env": {"DATA": "{{data_dir}}"},
                                 "public_udp": [{"port": 3478}]})
        first["components"][0]["runtime"] = runtime
        fixture.activate(first, request)
        before = fixture.evidence()
        fixture.deploy.reset_mock()
        fixture.activator.reconcile_installed()
        self.assertEqual(fixture.manifests[-1].components[0].runtime_env, {"DATA": "{{data_dir}}"})
        self.assertEqual(fixture.evidence()["state/releases.json"], before["state/releases.json"])
        before = fixture.evidence()
        fixture.deploy.reset_mock()
        fixture.publish.reset_mock()
        other, job = fixture.inputs("other", service=True, domain="other.example")
        other["components"][0]["runtime"] = copy.deepcopy(runtime)
        with self.assertRaisesRegex(ValueError, "declared by both"):
            fixture.activate(other, job)
        fixture.deploy.assert_not_called()
        fixture.publish.assert_not_called()
        self.assertEqual(fixture.evidence(), before)
