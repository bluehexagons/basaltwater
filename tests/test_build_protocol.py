"""Behavioral contracts for remote build admission and artifact identity."""

from __future__ import annotations

import copy
import hashlib
import unittest

from lib.build_protocol import (
    DEFAULT_LIMITS,
    decode_document,
    encode_document,
    parse_artifact_descriptor,
    parse_build_request,
    request_digest,
    validate_artifact_for_request,
    validate_artifact_path,
)


def build_request():
    return {
        "schema": 1, "request_id": "a" * 32, "project": "example",
        "source": {"revision": "b" * 40, "snapshot_sha256": "c" * 64,
                   "manifest_sha256": "d" * 64},
        "platform": {"os": "linux", "architecture": "x86_64", "baseline": "debian-13"},
        "components": [{"name": "api", "type": "service", "artifacts": ["bin/api"],
                        "commands": ["go build -o bin/api ."], "env": {"CGO_ENABLED": "0"}}],
        "limits": dict(DEFAULT_LIMITS),
    }


def artifact_descriptor(request=None):
    request = request or build_request()
    return {
        "schema": 1, "request_id": request["request_id"], "project": request["project"],
        "request_sha256": request_digest(request), "source": copy.deepcopy(request["source"]),
        "platform": copy.deepcopy(request["platform"]), "toolchains": {"go": "go1.26.2"},
        "components": [{key: copy.deepcopy(item[key]) for key in ("name", "type", "artifacts")}
                       for item in request["components"]],
        "entries": [
            {"path": "bin", "kind": "directory", "size": 0, "sha256": "", "executable": False},
            {"path": "bin/api", "kind": "file", "size": 4,
             "sha256": hashlib.sha256(b"test").hexdigest(), "executable": True},
        ],
    }


class BuildProtocolTest(unittest.TestCase):
    def test_round_trip_and_canonical_request_identity(self):
        request = build_request()
        parsed = parse_build_request(decode_document(encode_document(request)))
        self.assertEqual(parsed, request)
        reversed_request = dict(reversed(list(request.items())))
        self.assertEqual(request_digest(request), request_digest(reversed_request))
        for field, value in (("commands", ["go build -trimpath -o bin/api ."]),
                             ("env", {"CGO_ENABLED": "1"}), ("artifacts", ["bin/other"])):
            with self.subTest(field=field):
                changed = copy.deepcopy(request)
                changed["components"][0][field] = value
                self.assertNotEqual(request_digest(changed), request_digest(request))

    def test_parser_returns_independent_nested_values(self):
        request = build_request()
        parsed = parse_build_request(request)
        parsed["components"][0]["commands"].append("false")
        parsed["components"][0]["env"]["CGO_ENABLED"] = "1"
        parsed["source"]["revision"] = "e" * 40
        parsed["limits"]["log_bytes"] = 1
        self.assertEqual(request, build_request())

    def test_build_payload_cannot_carry_target_or_credential_fields(self):
        for field in ("target", "runtime_env", "env_file", "credentials", "deploy_command"):
            with self.subTest(field=field):
                request = build_request()
                request[field] = "private-secret-canary"
                with self.assertRaises(ValueError) as caught:
                    parse_build_request(request)
                self.assertNotIn("private-secret-canary", str(caught.exception))

    def test_strict_version_identity_and_integer_limits(self):
        changes = (("schema", True), ("schema", 2), ("request_id", "../job"),
                   ("project", "example\n"), ("project", []))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                request = build_request()
                request[key] = value
                with self.assertRaises(ValueError):
                    parse_build_request(request)
        for value in (True, 0, -1, 14401, "3600"):
            with self.subTest(timeout=value):
                request = build_request()
                request["limits"]["timeout_seconds"] = value
                with self.assertRaises(ValueError):
                    parse_build_request(request)

    def test_scratch_capacity_is_bounded_aligned_and_bound_to_request(self):
        request = build_request()
        original = request_digest(request)
        request["limits"]["scratch_bytes"] = 256 * 1024**2
        self.assertNotEqual(request_digest(request), original)
        for value in (True, 0, 255 * 1024**2, 256 * 1024**2 + 1, 129 * 1024**3):
            with self.subTest(value=value):
                request["limits"]["scratch_bytes"] = value
                with self.assertRaises(ValueError):
                    parse_build_request(request)

    def test_component_names_types_and_environment_are_validated(self):
        for field, value in (("name", "../api"), ("type", []), ("env", {"BAD=KEY": "x"}),
                             ("env", {"X": "bad\0value"}), ("commands", [" "]),
                             ("commands", ["echo bad\0value"])):
            with self.subTest(field=field, value=value):
                request = build_request()
                request["components"][0][field] = value
                with self.assertRaises(ValueError):
                    parse_build_request(request)
        request = build_request()
        request["components"].append(copy.deepcopy(request["components"][0]))
        with self.assertRaises(ValueError):
            parse_build_request(request)

    def test_multiline_project_commands_remain_literal_inputs(self):
        request = build_request()
        request["components"][0]["commands"] = ["set -eu\nprintf '%s\\n' ready"]
        self.assertEqual(parse_build_request(request)["components"], request["components"])

    def test_source_is_a_complete_pinned_identity(self):
        for field, value in (("revision", "main"), ("revision", "b" * 12),
                             ("snapshot_sha256", "c" * 63), ("manifest_sha256", "D" * 64)):
            with self.subTest(field=field):
                request = build_request()
                request["source"][field] = value
                with self.assertRaises(ValueError):
                    parse_build_request(request)

    def test_paths_are_portable_relative_and_not_control_metadata(self):
        for path in ("/etc/passwd", "../secret", "a/../b", "a//b", "a/./b", "a/", "",
                     "C:/secret", "a\\b", "a\nfile", ".git/config", "dist/.GIT/config",
                     ".git-credentials", ".gitmodules", ".gitattributes", ".gitignore", ".", "//server/share"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_artifact_path(path)
        self.assertEqual(validate_artifact_path(".", allow_root=True), ".")
        self.assertEqual(validate_artifact_path(".basaltwater/bin/api"), ".basaltwater/bin/api")

    def test_json_rejects_duplicates_constants_invalid_unicode_and_large_payloads(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"source":{"x":1,"x":2}}',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}', b'\xff', b'{} trailing'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_document(raw)
        with self.assertRaises(ValueError):
            decode_document(b"{}", maximum=1)
        with self.assertRaises(ValueError):
            encode_document({"x": "abc"}, maximum=2)

    def test_artifact_round_trip_omits_build_commands_and_environment(self):
        request = build_request()
        request["components"][0]["env"]["BUILD_INPUT"] = "build-only-canary"
        descriptor = artifact_descriptor(request)
        parsed = validate_artifact_for_request(decode_document(encode_document(descriptor)), request)
        self.assertEqual(parsed, descriptor)
        self.assertNotIn(b"build-only-canary", encode_document(parsed))
        self.assertNotIn(b"commands", encode_document(parsed))

    def test_artifact_must_match_admitted_attempt_project_source_and_platform(self):
        for key, value in (("request_id", "e" * 32), ("project", "other"),
                           ("request_sha256", "e" * 64)):
            with self.subTest(key=key):
                descriptor = artifact_descriptor()
                descriptor[key] = value
                with self.assertRaises(ValueError):
                    validate_artifact_for_request(descriptor, build_request())
        for section, key, value in (("source", "revision", "e" * 40),
                                    ("platform", "baseline", "debian-12")):
            with self.subTest(section=section):
                descriptor = artifact_descriptor()
                descriptor[section][key] = value
                with self.assertRaises(ValueError):
                    validate_artifact_for_request(descriptor, build_request())

    def test_inventory_rejects_duplicate_undeclared_and_missing_exports(self):
        for change in ("duplicate", "undeclared", "missing", "file-parent", "missing-parent", "symlink"):
            with self.subTest(change=change):
                descriptor = artifact_descriptor()
                if change == "duplicate":
                    descriptor["entries"].append(copy.deepcopy(descriptor["entries"][-1]))
                elif change == "undeclared":
                    descriptor["entries"][-1]["path"] = "secret"
                elif change == "missing":
                    descriptor["entries"].pop()
                elif change == "file-parent":
                    descriptor["entries"][0] = dict(descriptor["entries"][-1], path="bin")
                elif change == "missing-parent":
                    descriptor["entries"].pop(0)
                else:
                    descriptor["entries"][-1]["kind"] = "symlink"
                with self.assertRaises(ValueError):
                    parse_artifact_descriptor(descriptor)

    def test_inventory_enforces_admitted_byte_and_entry_limits(self):
        for field, limit in (("artifact_bytes", 3), ("artifact_entries", 1)):
            with self.subTest(field=field):
                request = build_request()
                request["limits"][field] = limit
                with self.assertRaises(ValueError):
                    validate_artifact_for_request(artifact_descriptor(request), request)

    def test_descriptor_is_independent_and_cannot_gain_deploy_fields(self):
        descriptor = artifact_descriptor()
        parsed = parse_artifact_descriptor(descriptor)
        parsed["entries"][0]["path"] = "changed"
        parsed["components"][0]["artifacts"].append("other")
        self.assertEqual(descriptor, artifact_descriptor())
        descriptor["components"][0]["runtime_env"] = {"SECRET": "canary"}
        with self.assertRaises(ValueError):
            parse_artifact_descriptor(descriptor)


if __name__ == "__main__":
    unittest.main()
