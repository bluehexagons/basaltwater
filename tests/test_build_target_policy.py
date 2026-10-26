"""Pure target policy and deployment projection contracts."""

from __future__ import annotations

import copy
import unittest

from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS
from lib.build_target_policy import (
    MAX_POLICY_BYTES,
    authorize_target_deployment,
    capabilities_for_principal,
    parse_target_deployment,
    parse_target_policy,
)


TARGET_LIMITS = {
    "artifact_bytes": DEFAULT_LIMITS["artifact_bytes"],
    "artifact_entries": DEFAULT_LIMITS["artifact_entries"],
}


def policy(*, applications: dict | None = None) -> dict:
    return {
        "schema": 1,
        "applications": applications or {
            "shop": {
                "project": "shop-source",
                "principals": ["alice@example"],
                "deployment_root": "/srv/basaltwater/apps/shop",
                "platform": dict(DEBIAN_PLATFORM),
                "limits": dict(TARGET_LIMITS),
                "components": {
                    "web": {"type": "static", "domain": "shop.example",
                            "path": "/"},
                },
            },
        },
    }


def request(*, project: str = "shop-source", platform: dict | None = None,
            artifacts: list[str] | None = None,
            limits: dict[str, int] | None = None) -> dict:
    return {
        "schema": 1,
        "request_id": "a" * 32,
        "project": project,
        "source": {
            "revision": "b" * 40,
            "snapshot_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
        },
        "platform": dict(DEBIAN_PLATFORM if platform is None else platform),
        "components": [{
            "name": "web",
            "type": "static",
            "artifacts": ["dist"] if artifacts is None else artifacts,
            "commands": ["npm run build"],
            "env": {"BUILD_MODE": "release"},
        }],
        "limits": dict(DEFAULT_LIMITS if limits is None else limits),
    }


def deployment(*, application: str = "shop", project: str = "shop-source",
               platform: dict | None = None, artifacts: list[str] | None = None,
               component: dict | None = None) -> dict:
    serving = {
        "name": "web",
        "type": "static",
        "domain": "shop.example",
        "path": "/",
        "artifacts": ["dist"] if artifacts is None else artifacts,
        "output": "dist",
        "spa": True,
    }
    if component is not None:
        serving.update(component)
    return {
        "schema": 1,
        "application": application,
        "project": project,
        "platform": dict(DEBIAN_PLATFORM if platform is None else platform),
        "components": [serving],
    }


class TargetPolicyTests(unittest.TestCase):
    def test_policy_normalizes_and_returns_detached_sorted_grants(self):
        configured = policy(applications={
            "z-app": {
                "project": "z-project", "principals": ["zeta", "alice@example"],
                "deployment_root": "/srv/apps/z", "platform": dict(DEBIAN_PLATFORM),
                "limits": dict(TARGET_LIMITS),
                "components": {"api": {"type": "service", "domain": "API.EXAMPLE.",
                                         "path": "/v1/"}},
            },
            "a-app": {
                "project": "a-project", "principals": ["alice@example"],
                "deployment_root": "/srv/apps/a", "platform": dict(DEBIAN_PLATFORM),
                "limits": dict(TARGET_LIMITS),
                "components": {"web": {"type": "static", "domain": "WWW.EXAMPLE",
                                         "path": "/"}},
            },
        })

        parsed = parse_target_policy(configured)

        self.assertEqual(list(parsed["applications"]), ["a-app", "z-app"])
        self.assertEqual(parsed["applications"]["z-app"]["principals"],
                         ["alice@example", "zeta"])
        self.assertEqual(parsed["applications"]["z-app"]["components"]["api"], {
            "type": "service", "domain": "api.example", "path": "/v1",
        })
        parsed["applications"]["a-app"]["limits"]["artifact_bytes"] = 1
        self.assertEqual(configured["applications"]["a-app"]["limits"]["artifact_bytes"],
                         TARGET_LIMITS["artifact_bytes"])

    def test_raw_json_rejects_duplicate_keys_and_oversized_documents(self):
        with self.assertRaisesRegex(ValueError, "Invalid build document"):
            parse_target_policy(b'{"schema":1,"schema":1,"applications":{}}')
        with self.assertRaises(ValueError):
            parse_target_policy(b" " * (MAX_POLICY_BYTES + 1))

    def test_policy_schema_unknown_fields_and_malformed_types_are_rejected(self):
        for value in (True, 2, "1"):
            with self.subTest(schema=value):
                configured = policy()
                configured["schema"] = value
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

        changes = (
            lambda value: value.update(extra=True),
            lambda value: value["applications"]["shop"].update(extra=True),
            lambda value: value["applications"]["shop"]["components"]["web"].update(
                port=8080),
            lambda value: value["applications"]["shop"].update(principals="alice@example"),
            lambda value: value["applications"]["shop"]["limits"].update(artifact_bytes=True),
        )
        for change in changes:
            with self.subTest(change=change):
                configured = policy()
                change(configured)
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

    def test_policy_requires_explicit_bounded_principals_and_components(self):
        for principals in ([], ["alice@example", "alice@example"], ["*"]):
            with self.subTest(principals=principals):
                configured = policy()
                configured["applications"]["shop"]["principals"] = principals
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

        configured = policy()
        configured["applications"]["shop"]["components"] = {}
        with self.assertRaises(ValueError):
            parse_target_policy(configured)

    def test_policy_rejects_bad_roots_routes_platform_and_limits(self):
        for root in ("/", "relative/path", "/srv/apps/../etc", "/srv//apps/shop",
                     "/srv/apps/shop app", "/srv/apps/$shop",
                     "/srv/apps/.basaltwater_shared/shop"):
            with self.subTest(root=root):
                configured = policy()
                configured["applications"]["shop"]["deployment_root"] = root
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

        for platform in (
            {"os": "linux", "architecture": "aarch64", "baseline": "debian-13"},
            {"os": "linux", "architecture": "x86_64", "baseline": "debian-12"},
        ):
            with self.subTest(platform=platform):
                configured = policy()
                configured["applications"]["shop"]["platform"] = platform
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

        invalid_limits = (dict(TARGET_LIMITS, artifact_bytes=True),
                          dict(TARGET_LIMITS, artifact_entries=0),
                          dict(TARGET_LIMITS, extra=1))
        for limits in invalid_limits:
            with self.subTest(limits=limits):
                configured = policy()
                configured["applications"]["shop"]["limits"] = limits
                with self.assertRaises(ValueError):
                    parse_target_policy(configured)

        configured = policy()
        configured["applications"]["shop"]["components"]["web"]["domain"] = "{{domain}}"
        with self.assertRaises(ValueError):
            parse_target_policy(configured)

    def test_policy_rejects_overlapping_roots_and_conflicting_route_owners(self):
        first = policy()["applications"]["shop"]
        nested = copy.deepcopy(first)
        nested["project"] = "another-project"
        nested["deployment_root"] = "/srv/basaltwater/apps/shop/api"
        nested["components"] = {
            "api": {"type": "service", "domain": "api.example", "path": "/"},
        }
        with self.assertRaisesRegex(ValueError, "roots .* overlap"):
            parse_target_policy(policy(applications={"shop": first, "api": nested}))

        conflicting = copy.deepcopy(first)
        conflicting["project"] = "another-project"
        conflicting["deployment_root"] = "/srv/basaltwater/apps/api"
        with self.assertRaisesRegex(ValueError, "route .* shared"):
            parse_target_policy(policy(applications={"shop": first, "api": conflicting}))

        identity_collision = copy.deepcopy(first)
        identity_collision["project"] = "another-project"
        identity_collision["deployment_root"] = "/var/www/shop-one"
        identity_collision["components"] = {
            "api": {"type": "service", "domain": "api.example", "path": "/"},
        }
        first["deployment_root"] = "/srv/apps/shop.one"
        with self.assertRaisesRegex(ValueError, "generated service identity"):
            parse_target_policy(policy(applications={"shop": first, "api": identity_collision}))

    def test_deployment_matches_request_exports_and_normalizes_detached_output(self):
        build = request(artifacts=["dist"])
        target = deployment(artifacts=["dist"])
        target["components"][0]["domain"] = "SHOP.EXAMPLE."
        target["components"][0]["path"] = "/"

        parsed = parse_target_deployment(target, request=build)

        self.assertEqual(parsed["components"][0]["domain"], "shop.example")
        self.assertEqual(parsed["components"][0]["artifacts"], ["dist"])
        parsed["components"][0]["artifacts"].append("other")
        self.assertEqual(target["components"][0]["artifacts"], ["dist"])
        self.assertEqual(build["components"][0]["artifacts"], ["dist"])

    def test_deployment_rejects_request_project_platform_and_export_mismatches(self):
        cases = (
            (deployment(project="other"), request(), "project"),
            (deployment(platform={"os": "linux", "architecture": "x86_64",
                                  "baseline": "debian-12"}), request(), "platform"),
            (deployment(artifacts=["public"]), request(), "exports"),
            (deployment(artifacts=["second", "dist"], component={"output": "dist"}),
             request(artifacts=["dist", "second"]), "export order"),
        )
        for target, build, label in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    parse_target_deployment(target, request=build)

        build = request(project="other")
        with self.assertRaises(ValueError):
            parse_target_deployment(deployment(), request=build)

    def test_deployment_rejects_uncovered_outputs_binaries_and_malicious_paths(self):
        for output in ("../etc", "/etc", "{{release_dir}}", "dist/../etc"):
            with self.subTest(output=output):
                target = deployment(component={"output": output})
                with self.assertRaises(ValueError):
                    parse_target_deployment(target, request=request())

        service_request = request(artifacts=["bin"])
        service_request["components"][0].update(
            name="api", type="service", artifacts=["bin"])
        for binary in ("../etc/passwd", "/usr/bin/sh", "bin/{{name}}", "bin/%n", "bin/a b"):
            with self.subTest(binary=binary):
                target = {
                    "schema": 1, "application": "shop", "project": "shop-source",
                    "platform": dict(DEBIAN_PLATFORM),
                    "components": [{"name": "api", "type": "service",
                                    "domain": "api.example", "path": "/",
                                    "artifacts": ["bin"], "binary": binary}],
                }
                with self.assertRaises(ValueError):
                    parse_target_deployment(target, request=service_request)

        with self.assertRaisesRegex(ValueError, "not covered"):
            target = deployment(component={"output": "dist"}, artifacts=["dist/site/index.html"])
            parse_target_deployment(target, request=request(artifacts=["dist/site/index.html"]))

    def test_static_output_matches_manifest_nginx_character_contract(self):
        invalid = deployment(artifacts=["dist@v1"], component={"output": "dist@v1"})
        with self.assertRaisesRegex(ValueError, "safe Nginx path characters"):
            parse_target_deployment(invalid, request=request(artifacts=["dist@v1"]))

        nested = deployment(component={"output": "dist/site"})
        parsed = parse_target_deployment(nested, request=request(artifacts=["dist"]))
        self.assertEqual(parsed["components"][0]["output"], "dist/site")

    def test_deployment_rejects_commands_secrets_identity_and_runtime_overrides(self):
        forbidden = (
            {"exec": "{{release_dir}}/bin/app"},
            {"env_file": "/run/secrets/app.env"},
            {"runtime_env": {"TOKEN": "secret"}},
            {"env": {"TOKEN": "secret"}},
            {"commands": ["unsafe command"]},
            {"service_user": "root"},
            {"username": "root"},
            {"writable_dirs": ["/etc"]},
            {"sqlite_backup": "/srv/private.db"},
            {"public_udp": [{"port": 9000}]},
            {"port": 80},
        )
        for extra in forbidden:
            with self.subTest(extra=extra):
                target = deployment(component=extra)
                with self.assertRaises(ValueError):
                    parse_target_deployment(target, request=request())

    def test_authorization_rejects_principal_application_route_and_limit_mismatches(self):
        base_policy = policy()
        base_request = request()
        base_deployment = deployment()
        for owner, app_id in (("bob@example", "shop"), ("alice@example", "other")):
            with self.subTest(owner=owner, application=app_id):
                with self.assertRaises(PermissionError):
                    authorize_target_deployment(
                        base_policy, principal=owner, application=app_id,
                        request=base_request,
                        deployment=deployment(application=app_id),
                    )

        with self.assertRaises(PermissionError):
            authorize_target_deployment(
                base_policy, principal="alice@example", application="shop",
                request=request(project="other"), deployment=deployment(project="other"),
            )

        with self.assertRaisesRegex(PermissionError, "approved route"):
            moved = deployment(component={"path": "/admin"})
            authorize_target_deployment(
                base_policy, principal="alice@example", application="shop",
                request=base_request, deployment=moved,
            )

        low_ceilings = dict(TARGET_LIMITS, artifact_bytes=100, artifact_entries=100)
        limited_policy = policy()
        limited_policy["applications"]["shop"]["limits"] = low_ceilings
        for key, value in (("artifact_bytes", 101), ("artifact_entries", 101)):
            with self.subTest(limit=key):
                requested_limits = dict(DEFAULT_LIMITS)
                requested_limits[key] = value
                with self.assertRaises(PermissionError):
                    authorize_target_deployment(
                        limited_policy, principal="alice@example", application="shop",
                        request=request(limits=requested_limits), deployment=base_deployment,
                    )

    def test_service_projection_uses_auto_port_and_generated_identity_defaults(self):
        service_request = request(artifacts=["bin/app"])
        service_request["components"] = [{
            "name": "api", "type": "service", "artifacts": ["bin/app"],
            "commands": ["go build -o bin/app ."], "env": {"BUILD_TAG": "release"},
        }]
        configured = policy()
        configured["applications"]["shop"]["components"] = {
            "api": {"type": "service", "domain": "api.example", "path": "/"},
        }
        target = {
            "schema": 1, "application": "shop", "project": "shop-source",
            "platform": dict(DEBIAN_PLATFORM),
            "components": [{"name": "api", "type": "service",
                            "domain": "api.example", "path": "/",
                            "artifacts": ["bin/app"], "binary": "bin/app",
                            "health": "/health"}],
        }

        result = authorize_target_deployment(
            configured, principal="alice@example", application="shop",
            request=service_request, deployment=target,
        )

        component = result["bindings"]["components"][0]
        self.assertEqual(component["binary"], "bin/app")
        self.assertEqual(component["health"], "/health")
        self.assertNotIn("port", component)
        self.assertNotIn("service_user", component)
        self.assertNotIn("commands", str(result))
        self.assertNotIn("BUILD_TAG", str(result))

    def test_private_bindings_digest_is_stable_canonical_and_detached(self):
        build = request()
        build["components"].append({
            "name": "docs", "type": "static", "artifacts": ["manual"],
            "commands": ["make docs"], "env": {},
        })
        configured = policy()
        configured["applications"]["shop"]["components"]["docs"] = {
            "type": "static", "domain": "docs.example", "path": "/manual",
        }
        target = deployment()
        target["components"].append({
            "name": "docs", "type": "static", "domain": "docs.example",
            "path": "/manual", "artifacts": ["manual"],
            "output": "manual", "spa": False,
        })
        first = authorize_target_deployment(
            configured, principal="alice@example", application="shop",
            request=build, deployment=target,
        )
        reordered = {**target, "components": list(reversed(target["components"]))}
        second = authorize_target_deployment(
            configured, principal="alice@example", application="shop",
            request=build, deployment=reordered,
        )

        self.assertEqual(first["bindings_sha256"], second["bindings_sha256"])
        self.assertEqual(len(first["bindings_sha256"]), 64)
        self.assertEqual(first["bindings"]["deployment_root"], "/srv/basaltwater/apps/shop")
        first["bindings"]["components"][0]["artifacts"].append("other")
        self.assertEqual(target["components"][0]["artifacts"], ["dist"])
        self.assertEqual(target["components"][1]["artifacts"], ["manual"])
        self.assertEqual(policy()["applications"]["shop"]["deployment_root"],
                         "/srv/basaltwater/apps/shop")

    def test_capabilities_are_principal_scoped_and_never_include_filesystem_paths(self):
        configured = policy(applications={
            "shop": policy()["applications"]["shop"],
            "internal": {
                "project": "internal-source", "principals": ["bob@example"],
                "deployment_root": "/srv/basaltwater/apps/internal",
                "platform": dict(DEBIAN_PLATFORM), "limits": dict(TARGET_LIMITS),
                "components": {"api": {"type": "service", "domain": "api.example",
                                         "path": "/"}},
            },
        })

        capabilities = capabilities_for_principal(configured, principal="alice@example")

        self.assertEqual(set(capabilities), {"schema", "applications"})
        self.assertEqual(set(capabilities["applications"]), {"shop"})
        app = capabilities["applications"]["shop"]
        self.assertEqual(set(app), {"project", "platform", "limits", "components"})
        self.assertEqual(app["components"], [{"name": "web", "type": "static",
                                              "domain": "shop.example", "path": "/"}])
        self.assertNotIn("deployment_root", str(capabilities))
        self.assertNotIn("dist", str(capabilities))
        self.assertNotIn("/srv/basaltwater", str(capabilities))
        self.assertEqual(capabilities_for_principal(configured, principal="unknown"),
                         {"schema": 1, "applications": {}})


if __name__ == "__main__":
    unittest.main()
