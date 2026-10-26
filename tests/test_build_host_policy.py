"""Pure policy validation and project-scoped build authorization contracts."""

from __future__ import annotations

import unittest

from lib.build_host_policy import (
    MAX_PRINCIPALS,
    MAX_PROJECTS_PER_PRINCIPAL,
    authorize_build_request,
    capabilities_for_principal,
    parse_build_host_policy,
)
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS


def grant(*, runtimes: list[str] | None = None,
          limits: dict[str, int] | None = None,
          platform: dict[str, str] | None = None) -> dict:
    return {
        "platform": dict(DEBIAN_PLATFORM if platform is None else platform),
        "runtimes": list([] if runtimes is None else runtimes),
        "limits": dict(DEFAULT_LIMITS if limits is None else limits),
    }


def policy(*, principals: dict | None = None) -> dict:
    return {
        "schema": 1,
        "principals": principals or {
            "alice@example": {"projects": {"site": grant(runtimes=["node", "go"])}},
        },
    }


def request(*, project: str = "site", component_type: str = "service",
            limits: dict[str, int] | None = None,
            platform: dict[str, str] | None = None) -> dict:
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
            "name": "app",
            "type": component_type,
            "artifacts": ["bin/app"],
            "commands": ["go build -o bin/app ."],
            "env": {"BUILD_MODE": "release"},
        }],
        "limits": dict(DEFAULT_LIMITS if limits is None else limits),
    }


class BuildHostPolicyTests(unittest.TestCase):
    def test_policy_normalizes_multiple_principals_and_projects(self):
        configured = policy(principals={
            "bob/ci": {"projects": {"api": grant(runtimes=["python"])}},
            "alice@example": {"projects": {
                "z-site": grant(runtimes=["node", "go"]),
                "a-site": grant(),
            }},
        })

        parsed = parse_build_host_policy(configured)

        self.assertEqual(list(parsed["principals"]), ["alice@example", "bob/ci"])
        self.assertEqual(list(parsed["principals"]["alice@example"]["projects"]),
                         ["a-site", "z-site"])
        self.assertEqual(parsed["principals"]["alice@example"]["projects"]["z-site"]["runtimes"],
                         ["go", "node"])
        parsed["principals"]["alice@example"]["projects"]["z-site"]["limits"]["cpu_percent"] = 1
        self.assertEqual(configured["principals"]["alice@example"]["projects"]["z-site"]
                         ["limits"]["cpu_percent"], DEFAULT_LIMITS["cpu_percent"])

    def test_raw_json_rejects_duplicate_keys_and_runtime_lists_reject_duplicates(self):
        with self.assertRaisesRegex(ValueError, "Invalid build document"):
            parse_build_host_policy(b'{"schema":1,"schema":1,"principals":{}}')

        configured = policy()
        configured["principals"]["alice@example"]["projects"]["site"]["runtimes"] = ["go", "go"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_build_host_policy(configured)

    def test_schema_and_unknown_fields_are_strict(self):
        for value in (True, 2, "1"):
            with self.subTest(schema=value):
                configured = policy()
                configured["schema"] = value
                with self.assertRaises(ValueError):
                    parse_build_host_policy(configured)

        changes = (
            (lambda value: value.update(extra="ignored"), "build host policy"),
            (lambda value: value["principals"]["alice@example"].update(extra="ignored"),
             "principal"),
            (lambda value: value["principals"]["alice@example"]["projects"]["site"].update(
                commands=["unsafe"]), "project"),
        )
        for change, label in changes:
            with self.subTest(label=label):
                configured = policy()
                change(configured)
                with self.assertRaisesRegex(ValueError, "missing or unsupported fields"):
                    parse_build_host_policy(configured)

    def test_empty_grants_and_excess_principal_or_project_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_build_host_policy({"schema": 1, "principals": {}})
        with self.assertRaises(ValueError):
            parse_build_host_policy(policy(principals={"alice": {"projects": {}}}))

        too_many_principals = {
            f"owner-{index}": {"projects": {"site": grant()}}
            for index in range(MAX_PRINCIPALS + 1)
        }
        with self.assertRaisesRegex(ValueError, "1 to 128 principals"):
            parse_build_host_policy(policy(principals=too_many_principals))

        too_many_projects = {
            f"project-{index}": grant()
            for index in range(MAX_PROJECTS_PER_PRINCIPAL + 1)
        }
        with self.assertRaisesRegex(ValueError, "1 to 128 project grants"):
            parse_build_host_policy(policy(principals={"alice": {"projects": too_many_projects}}))

    def test_principal_and_project_labels_reject_controls_and_invalid_shapes(self):
        for owner in ("bad\nowner", "../owner", "", "équipe"):
            with self.subTest(owner=owner):
                with self.assertRaises(ValueError):
                    parse_build_host_policy(policy(principals={owner: {"projects": {"site": grant()}}}))

        for project in ("../site", "Site", "site/name", "site\x00", ""):
            with self.subTest(project=project):
                with self.assertRaises(ValueError):
                    parse_build_host_policy(policy(principals={"alice": {"projects": {project: grant()}}}))

    def test_only_debian_platform_and_supported_runtime_grants_are_accepted(self):
        bad_platform = {"os": "linux", "architecture": "aarch64", "baseline": "debian-13"}
        with self.assertRaisesRegex(ValueError, "unsupported build platform"):
            parse_build_host_policy(policy(principals={
                "alice": {"projects": {"site": grant(platform=bad_platform)}}
            }))

        for runtimes in (["rust"], ["go", "go"], ["go", "node", "python", "go"]):
            with self.subTest(runtimes=runtimes):
                with self.assertRaises(ValueError):
                    parse_build_host_policy(policy(principals={
                        "alice": {"projects": {"site": grant(runtimes=runtimes)}}
                    }))

    def test_policy_ceilings_use_shared_protocol_validation(self):
        invalid_ceilings = []
        boolean = dict(DEFAULT_LIMITS)
        boolean["cpu_percent"] = True
        invalid_ceilings.append(boolean)
        unaligned_scratch = dict(DEFAULT_LIMITS)
        unaligned_scratch["scratch_bytes"] += 1
        invalid_ceilings.append(unaligned_scratch)
        missing = dict(DEFAULT_LIMITS)
        del missing["log_bytes"]
        invalid_ceilings.append(missing)
        unknown = dict(DEFAULT_LIMITS, credential_bytes=1)
        invalid_ceilings.append(unknown)

        for ceilings in invalid_ceilings:
            with self.subTest(ceilings=ceilings):
                configured = policy()
                configured["principals"]["alice@example"]["projects"]["site"]["limits"] = ceilings
                with self.assertRaises(ValueError):
                    parse_build_host_policy(configured)

    def test_authorization_returns_only_a_detached_grant_for_authenticated_principal(self):
        configured = policy(principals={
            "alice@example": {"projects": {"site": grant(runtimes=["go"])}},
            "bob@example": {"projects": {"private-api": grant(runtimes=["python"])}},
        })
        capabilities = capabilities_for_principal(configured, principal="alice@example")
        self.assertEqual(set(capabilities), {"schema", "projects"})
        self.assertEqual(set(capabilities["projects"]), {"site"})
        self.assertEqual(capabilities["projects"]["site"]["runtimes"], ["go"])
        self.assertEqual(set(capabilities["projects"]["site"]), {"platform", "runtimes", "limits"})

        authorized = authorize_build_request(
            configured, principal="alice@example", request=request(), requested_runtimes=["go"]
        )
        self.assertEqual(authorized["project"], "site")
        self.assertEqual(authorized["runtimes"], ["go"])
        self.assertNotIn("commands", str(authorized))
        self.assertNotIn("BUILD_MODE", str(authorized))
        self.assertNotIn("private-api", str(capabilities))

        authorized["limits"]["cpu_percent"] = 1
        self.assertEqual(configured["principals"]["alice@example"]["projects"]["site"]
                         ["limits"]["cpu_percent"], DEFAULT_LIMITS["cpu_percent"])

    def test_capabilities_for_unknown_principal_are_empty_and_do_not_leak_projects(self):
        configured = policy(principals={
            "alice": {"projects": {"secret-project": grant()}},
        })
        self.assertEqual(
            capabilities_for_principal(configured, principal="unknown"),
            {"schema": 1, "projects": {}},
        )

    def test_authorization_rejects_other_principal_or_ungranted_project(self):
        configured = policy(principals={
            "alice": {"projects": {"site": grant()}},
            "bob": {"projects": {"private-api": grant()}},
        })
        for owner, project in (("bob", "site"), ("alice", "private-api"), ("unknown", "private-api")):
            with self.subTest(owner=owner, project=project):
                with self.assertRaises(PermissionError):
                    authorize_build_request(
                        configured, principal=owner, request=request(project=project),
                        requested_runtimes=[],
                    )

    def test_authorization_rejects_unsupported_platform_component_and_tools(self):
        configured = policy()
        unsupported_platform = {"os": "linux", "architecture": "x86_64", "baseline": "debian-12"}
        cases = (
            request(platform=unsupported_platform),
            request(component_type="godot-web"),
        )
        for job in cases:
            with self.subTest(platform=job["platform"], component=job["components"][0]["type"]):
                with self.assertRaises(PermissionError):
                    authorize_build_request(
                        configured, principal="alice@example", request=job, requested_runtimes=["go"],
                    )

        with self.assertRaises(ValueError):
            authorize_build_request(
                configured, principal="alice@example", request=request(), requested_runtimes=["ruby"]
            )

        with self.assertRaises(PermissionError):
            authorize_build_request(
                configured, principal="alice@example", request=request(), requested_runtimes=["python"]
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            authorize_build_request(
                configured, principal="alice@example", request=request(), requested_runtimes=["go", "go"]
            )

    def test_each_requested_limit_must_fit_the_current_ceiling(self):
        cases = (
            ("timeout_seconds", 3600, 3601),
            ("memory_bytes", 2 * 1024**3, 2 * 1024**3 + 1),
            ("cpu_percent", 100, 101),
            ("scratch_bytes", 8 * 1024**3, 8 * 1024**3 + 1024**2),
            ("artifact_bytes", 2 * 1024**3, 2 * 1024**3 + 1),
            ("artifact_entries", 99_999, 100_000),
            ("log_bytes", 4 * 1024**2, 4 * 1024**2 + 1),
        )
        for name, ceiling, requested in cases:
            with self.subTest(limit=name):
                ceilings = dict(DEFAULT_LIMITS)
                ceilings[name] = ceiling
                configured = policy(principals={
                    "alice@example": {"projects": {"site": grant(runtimes=["go"], limits=ceilings)}}
                })
                requested_limits = dict(DEFAULT_LIMITS)
                requested_limits[name] = ceiling
                self.assertEqual(
                    authorize_build_request(
                        configured, principal="alice@example", request=request(limits=requested_limits),
                        requested_runtimes=["go"],
                    )["project"],
                    "site",
                )
                requested_limits[name] = requested
                with self.assertRaises(PermissionError):
                    authorize_build_request(
                        configured, principal="alice@example", request=request(limits=requested_limits),
                        requested_runtimes=["go"],
                    )

    def test_authorization_validates_request_limits_as_protocol_integers(self):
        invalid_limits = dict(DEFAULT_LIMITS)
        invalid_limits["cpu_percent"] = True
        with self.assertRaises(ValueError):
            authorize_build_request(
                policy(), principal="alice@example", request=request(limits=invalid_limits),
                requested_runtimes=["go"],
            )

        untrusted_owner_field = request()
        untrusted_owner_field["principal"] = "bob"
        with self.assertRaises(ValueError):
            authorize_build_request(
                policy(), principal="alice@example", request=untrusted_owner_field,
                requested_runtimes=["go"],
            )


if __name__ == "__main__":
    unittest.main()
