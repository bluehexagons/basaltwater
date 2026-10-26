"""Pure controller planning: exact target authority and fail-closed manifests."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from lib.build_manifest import DEBIAN_PLATFORM, request_from_manifest
from lib.build_protocol import DEFAULT_LIMITS
from lib.build_setup_plan import prepare_delivery_plan
from lib.build_target_policy import authorize_target_deployment
from lib.deployment import DeploymentOrchestrator
from lib.project_manifest import parse_manifest


def manifest_data():
    return {"version": 1, "build_runtimes": ["go"], "components": [
        {"name": "web", "type": "static", "domain": "{{domain}}", "output": "./dist",
         "spa": True},
        {"name": "api", "type": "service", "domain": "{{domain}}", "path": "/v1/",
         "binary": "./bin/api", "port": "auto", "health": "/health",
         "build": "go build -o bin/api .", "env": {"CGO_ENABLED": "0"}},
    ]}


def enrollment():
    wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"k" * 32
    return {"schema": 1, "keys": {"alice": "ssh-ed25519 " + base64.b64encode(wire).decode()},
            "max_records": 128, "max_bytes": 2 * 1024**3,
            "policy": {"schema": 1, "applications": {"site": {
                "project": "approved-source", "principals": ["alice"],
                "deployment_root": "/srv/apps/administrator-owned",
                "platform": dict(DEBIAN_PLATFORM),
                "limits": {"artifact_bytes": 1024**3, "artifact_entries": 4096},
                "components": {
                    "web": {"type": "static", "domain": "example.com", "path": "/"},
                    "api": {"type": "service", "domain": "example.com", "path": "/v1"},
                },
            }}}}


def capabilities():
    return {"schema": 1, "tool_versions": {"go": "go1.26.2", "python": "3.13.7"},
            "projects": {"approved-source": {
                "platform": dict(DEBIAN_PLATFORM), "runtimes": ["go", "python"],
                "limits": dict(DEFAULT_LIMITS),
            }}}


class BuildSetupPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = {"revision": "b" * 40, "snapshot_sha256": "c" * 64,
                       "manifest_sha256": "d" * 64}
        self.options = {
            "destination": "example.com/releases", "source": self.source,
            # Deliberately absent: planning must not inspect the snapshot.
            "source_path": os.path.join(self.temporary.name, "source.snapshot"),
            "request_id": "a" * 32, "idempotency_key": "e" * 32,
            "target_enrollment": enrollment(), "target_principal": "alice",
            "builder_capabilities": capabilities(),
        }

    def plan(self, data=None, **overrides):
        manifest = parse_manifest(manifest_data() if data is None else data)
        return prepare_delivery_plan(manifest, **dict(self.options, **overrides))

    def static_data(self, **updates):
        data = {"version": 1, "components": [manifest_data()["components"][0]]}
        data["components"][0].update(updates)
        self.options["target_enrollment"]["policy"]["applications"]["site"]["components"].pop("api", None)
        return data

    def test_multicomponent_plan_matches_local_routes_without_destination_prefix(self):
        manifest = parse_manifest(manifest_data())
        local = DeploymentOrchestrator()
        for destination in ("example.com", "example.com/", "example.com/releases"):
            with self.subTest(destination=destination):
                plan = prepare_delivery_plan(manifest, **dict(self.options, destination=destination))
                serving = {item["name"]: item for item in plan.deployment["components"]}
                for component in manifest.components:
                    descriptor = local._component_dep(component, "/unused/local-release", "example.com")
                    self.assertEqual(serving[component.name]["domain"], descriptor["domain"])
                    self.assertEqual(serving[component.name]["path"], descriptor["path"].rstrip("/") or "/")
                self.assertEqual({item["path"] for item in serving.values()}, {"/", "/v1"})
                self.assertTrue(serving["web"]["spa"])
                self.assertEqual(serving["web"]["output"], "dist")
                self.assertEqual(serving["api"]["binary"], "bin/api")
                self.assertEqual(serving["api"]["health"], "/health")
                self.assertEqual(plan.required_tools, ("go",))
                self.assertEqual(plan.idempotency_key, "e" * 32)
                self.assertEqual(plan.source_path, self.options["source_path"])

    def test_request_reuses_exact_manifest_commands_environment_and_exports(self):
        data = manifest_data()
        data["components"][1]["artifacts"] = ["assets", "bin"]
        plan = self.plan(data)
        expected = request_from_manifest(
            parse_manifest(data), request_id="a" * 32, project="approved-source",
            source=self.source, platform=DEBIAN_PLATFORM, limits=plan.request["limits"],
        )
        self.assertEqual(plan.request, expected)
        self.assertEqual(plan.request["components"][1]["artifacts"], ["assets", "bin"])
        self.assertNotIn("commands", str(plan.deployment))
        self.assertNotIn("CGO_ENABLED", str(plan.deployment))

    def test_selected_application_is_sole_project_and_target_path_authority(self):
        configured = self.options["target_enrollment"]
        other = copy.deepcopy(configured["policy"]["applications"]["site"])
        other.update(project="unrelated", deployment_root="/srv/apps/other")
        other["components"]["api"]["domain"] = "other.example"
        other["components"]["web"]["domain"] = "other.example"
        configured["policy"]["applications"]["other"] = other
        plan = self.plan()
        self.assertEqual(plan.deployment["application"], "site")
        self.assertEqual(plan.request["project"], "approved-source")
        self.assertNotIn("deployment_root", str(plan))
        self.assertNotIn("administrator-owned", str(plan))
        authorized = authorize_target_deployment(
            configured["policy"], principal="alice", application="site",
            request=plan.request, deployment=plan.deployment,
        )
        self.assertEqual(authorized["bindings"]["deployment_root"], "/srv/apps/administrator-owned")

    def test_missing_partial_name_type_route_and_principal_matches_fail(self):
        changes = (
            lambda app: app["components"].pop("api"),
            lambda app: app["components"].update(extra={"type": "static", "domain": "extra.example", "path": "/"}),
            lambda app: app["components"].update(frontend=app["components"].pop("web")),
            lambda app: app["components"]["api"].update(type="static"),
            lambda app: app["components"]["api"].update(path="/v2"),
            lambda app: app["components"]["web"].update(domain="other.example"),
        )
        for change in changes:
            configured = enrollment()
            change(configured["policy"]["applications"]["site"])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "exactly one"):
                self.plan(target_enrollment=configured)
        with self.assertRaises(PermissionError):
            self.plan(target_principal="bob")

    def test_ambiguous_application_routes_are_refused_by_strict_policy(self):
        configured = enrollment()
        duplicate = copy.deepcopy(configured["policy"]["applications"]["site"])
        duplicate["deployment_root"] = "/srv/apps/duplicate"
        configured["policy"]["applications"]["duplicate"] = duplicate
        with self.assertRaisesRegex(ValueError, "route .* shared"):
            self.plan(target_enrollment=configured)

    def test_enrolled_principal_cannot_select_another_principals_application(self):
        configured = enrollment()
        other = copy.deepcopy(configured["policy"]["applications"]["site"])
        other.update(principals=["bob"], deployment_root="/srv/apps/bob")
        for route in other["components"].values():
            route["domain"] = "bob.example"
        configured["policy"]["applications"]["bob"] = other
        wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"b" * 32
        configured["keys"]["bob"] = "ssh-ed25519 " + base64.b64encode(wire).decode()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.plan(target_enrollment=configured, target_principal="bob")

    def test_limits_intersect_defaults_builder_and_target(self):
        builder = capabilities()
        ceilings = builder["projects"]["approved-source"]["limits"]
        ceilings.update(timeout_seconds=7200, memory_bytes=4 * 1024**3,
                        cpu_percent=25, scratch_bytes=512 * 1024**2,
                        log_bytes=100, artifact_entries=100)
        plan = self.plan(builder_capabilities=builder)
        self.assertEqual(plan.request["limits"], dict(
            DEFAULT_LIMITS, cpu_percent=25, scratch_bytes=512 * 1024**2,
            log_bytes=100, artifact_bytes=1024**3, artifact_entries=100,
        ))
        self.options["target_enrollment"]["policy"]["applications"]["site"]["limits"].update(
            artifact_bytes=1, artifact_entries=1)
        self.assertEqual(self.plan().request["limits"]["artifact_bytes"], 1)

    def test_builder_project_platform_runtime_and_installed_tool_mismatches_fail(self):
        changes = (
            lambda cap: cap["projects"].clear(),
            lambda cap: cap["projects"].update(other=cap["projects"].pop("approved-source")),
            lambda cap: cap["projects"]["approved-source"]["platform"].update(architecture="aarch64"),
            lambda cap: cap["projects"]["approved-source"].update(runtimes=["python"]),
            lambda cap: cap["tool_versions"].pop("go"),
            lambda cap: cap["tool_versions"].update(go=""),
            lambda cap: cap["projects"]["approved-source"]["limits"].update(memory_bytes=1),
            lambda cap: cap["projects"]["approved-source"]["limits"].update(scratch_bytes=268435457),
        )
        for change in changes:
            cap = capabilities()
            change(cap)
            with self.subTest(change=change), self.assertRaises((ValueError, PermissionError)):
                self.plan(builder_capabilities=cap)
        configured = enrollment()
        configured["policy"]["applications"]["site"]["platform"]["baseline"] = "debian-12"
        with self.assertRaises(ValueError):
            self.plan(target_enrollment=configured)

    def test_ready_static_and_explicit_python_builds_are_supported(self):
        data = self.static_data(output=".")
        self.assertEqual(self.plan(data).required_tools, ())
        data.update(build_runtimes=["python"])
        data["components"][0].update(build="python3 build.py", env={"MODE": "release"})
        self.assertEqual(self.plan(data).required_tools, ("python",))

    def test_explicit_runtimes_required_and_node_readiness_never_assumed(self):
        for runtimes in (None, [], ["python"], ["node"], ["go", "node"]):
            data = manifest_data()
            if runtimes is None:
                data.pop("build_runtimes")
            else:
                data["build_runtimes"] = runtimes
            with self.subTest(runtimes=runtimes), self.assertRaises(ValueError):
                self.plan(data)
        data = self.static_data(build="python3 build.py")
        with self.assertRaisesRegex(ValueError, "explicit build_runtimes"):
            self.plan(data)
        data.update(build_runtimes=["node"])
        data["components"][0]["build"] = "npm run build"
        cap = capabilities()
        cap["projects"]["approved-source"]["runtimes"].insert(1, "node")
        cap["tool_versions"]["node"] = "v26.0.0"
        with self.assertRaisesRegex(ValueError, "package-manager readiness"):
            self.plan(data, builder_capabilities=cap)

    def test_unsupported_execution_and_routing_fields_are_never_dropped(self):
        changes = (
            {"runtime_env": {"PORT": "{{port}}"}}, {"env_file": "/etc/app.env"},
            {"working_dir": "{{release_dir}}"}, {"working_dir": "."},
            {"sqlite_backup": "{{data_dir}}/app.db"}, {"backup_retention": 11},
            {"public_udp": [{"port": 9000}]}, {"port": 8080}, {"reverse_proxy": False},
        )
        for change in changes:
            data = manifest_data()
            data["components"][1].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "unsupported execution"):
                self.plan(data)
        for command in ("{{release_dir}}/bin/api", "node app.js", "python3 app.py"):
            data = manifest_data()
            data["components"][1].pop("binary")
            data["components"][1].update(exec=command, artifacts=["bin/api"])
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.plan(data)
        for binary in ("bin/python3", "bin/node", "bin/bash"):
            data = manifest_data()
            data["components"][1]["binary"] = binary
            with self.subTest(binary=binary), self.assertRaisesRegex(ValueError, "Interpreter"):
                self.plan(data)

    def test_godot_workflows_and_unmodeled_static_policy_are_refused(self):
        data = self.static_data()
        data["components"][0].pop("spa")
        data["components"][0]["type"] = "godot-web"
        with self.assertRaises(ValueError):
            self.plan(data)
        data = self.static_data()
        data["ci"] = {"build": [{"argv": ["python3", "ci.py"]}]}
        with self.assertRaisesRegex(ValueError, "CI and publishing"):
            self.plan(data)
        for key, value in (("gzip", True), ("expires", "1d"), ("data_dir", "/srv/data")):
            manifest = parse_manifest(self.static_data())
            setattr(manifest.components[0], key, value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                prepare_delivery_plan(manifest, **self.options)

    def test_exports_must_cover_served_output_or_binary_exactly_as_requested(self):
        for index, exports in ((0, ["public"]), (1, ["bin/other"]), (1, ["assets"])):
            data = manifest_data()
            data["components"][index]["artifacts"] = exports
            with self.subTest(index=index, exports=exports), self.assertRaisesRegex(ValueError, "not covered"):
                self.plan(data)
        data = manifest_data()
        data["components"][0].update(output="./dist/site", artifacts=["dist"])
        self.assertEqual(self.plan(data).deployment["components"][1]["output"], "dist/site")

    def test_domain_templates_use_only_local_domain_context_and_paths_stay_literal(self):
        data = self.static_data(domain="docs.{{domain}}")
        routes = self.options["target_enrollment"]["policy"]["applications"]["site"]["components"]
        routes["web"]["domain"] = "docs.example.com"
        self.assertEqual(self.plan(data).deployment["components"][0]["domain"], "docs.example.com")
        data["components"][0]["domain"] = "DOCS.EXAMPLE.COM."
        self.assertEqual(self.plan(data).deployment["components"][0]["domain"], "docs.example.com")
        for domain in ("{{name}}.example.com", "{{path}}.example.com", "{{base_dir}}.example.com"):
            data["components"][0]["domain"] = domain
            with self.subTest(domain=domain), self.assertRaisesRegex(ValueError, "unknown template"):
                self.plan(data)
        manifest = parse_manifest(self.static_data())
        manifest.components[0].path = "/{{path}}"
        with self.assertRaises(ValueError):
            prepare_delivery_plan(manifest, **self.options)

    def test_invalid_destinations_paths_ids_and_pinned_source_are_refused(self):
        for destination in ("/srv/apps/site", "https://example.com", "example.com,a.example",
                            "example.com/../private", "example.com//path", "example.com\n"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.plan(destination=destination)
        for key, value in (("source_path", "relative"), ("source_path", "/tmp/../source"),
                           ("request_id", "new"), ("idempotency_key", "new"),
                           ("source", dict(self.source, revision="main"))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.plan(**{key: value})
        for root in ("/opt/basaltwater/app", "/var/lib/basaltwater/build-target/app", "../apps"):
            configured = enrollment()
            configured["policy"]["applications"]["site"]["deployment_root"] = root
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.plan(target_enrollment=configured)

    def test_planning_has_no_io_and_detaches_all_mutable_input_values(self):
        manifest = parse_manifest(manifest_data())
        original = copy.deepcopy((manifest, self.options))
        with ExitStack() as stack:
            for name in ("builtins.open", "os.open", "os.stat", "os.lstat", "os.scandir",
                         "subprocess.Popen", "socket.socket"):
                stack.enter_context(patch(name, side_effect=AssertionError("Planning attempted I/O")))
            plan = prepare_delivery_plan(manifest, **self.options)
        self.assertEqual((manifest, self.options), original)
        plan.request["components"][1]["env"]["CGO_ENABLED"] = "changed"
        plan.request["source"]["revision"] = "changed"
        plan.deployment["components"][0]["artifacts"].append("changed")
        self.assertEqual((manifest, self.options), original)

    def test_request_and_component_size_bounds_apply_before_delivery(self):
        data = manifest_data()
        data["components"][1]["build"] = "x" * 65537
        with self.assertRaises(ValueError):
            self.plan(data)
        manifest = parse_manifest(manifest_data())
        manifest.components *= 51
        with self.assertRaises(ValueError):
            prepare_delivery_plan(manifest, **self.options)


if __name__ == "__main__":
    unittest.main()
