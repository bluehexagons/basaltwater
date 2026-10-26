from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from io import BytesIO
from unittest.mock import Mock

from lib.build_host_service import (
    BuildHostService,
    MAX_HEADER_LINE_BYTES,
    MAX_LOG_CHUNK_BYTES,
    MAX_RESPONSE_LINE_BYTES,
)
from lib.build_jobs import BuildJobStore
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, request_digest


def build_request(request_id: str = "a" * 32, *, project: str = "site") -> dict:
    return {
        "schema": 1,
        "request_id": request_id,
        "project": project,
        "source": {
            "revision": "1" * 40,
            "snapshot_sha256": "2" * 64,
            "manifest_sha256": "3" * 64,
        },
        "platform": dict(DEBIAN_PLATFORM),
        "components": [{
            "name": "site",
            "type": "static",
            "artifacts": ["dist"],
            "commands": [],
            "env": {},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


def build_policy(*, project: str = "site", other_principal: bool = False) -> dict:
    projects = {
        project: {
            "platform": dict(DEBIAN_PLATFORM),
            "runtimes": ["go", "node", "python"],
            "limits": dict(DEFAULT_LIMITS),
        },
    }
    principals = {"alice@example": {"projects": projects}}
    if other_principal:
        principals["bob@example"] = {"projects": {
            "private": {
                "platform": dict(DEBIAN_PLATFORM),
                "runtimes": [],
                "limits": dict(DEFAULT_LIMITS),
            },
        }}
    return {"schema": 1, "principals": principals}


def build_artifact(request: dict, *, digest: str, size: int) -> dict:
    return {
        "sha256": digest,
        "size": size,
        "descriptor": {
            "schema": 1,
            "request_id": request["request_id"],
            "project": request["project"],
            "request_sha256": request_digest(request),
            "source": dict(request["source"]),
            "platform": dict(request["platform"]),
            "components": [{"name": "site", "type": "static", "artifacts": ["dist"]}],
            "toolchains": {},
            "entries": [{
                "path": "dist", "kind": "directory", "size": 0,
                "sha256": "", "executable": False,
            }],
        },
    }


def header(action: str, **fields: object) -> bytes:
    return json.dumps({"schema": 1, "action": action, **fields},
                      sort_keys=True, separators=(",", ":")).encode() + b"\n"


class BuildHostServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.logs_root = self.root / "logs"
        self.artifacts_root = self.root / "artifacts"
        self.logs_root.mkdir(mode=0o700)
        self.artifacts_root.mkdir(mode=0o700)
        os.chmod(self.logs_root, 0o700)
        os.chmod(self.artifacts_root, 0o700)
        self.jobs = BuildJobStore(self.root / "jobs.json")
        self.policy = build_policy()
        self.admissions: list[tuple[str, int, bytes]] = []

        def admit_source(request, *, owner, source, source_size, jobs):
            raw = source.read(source_size)
            if not isinstance(raw, bytes) or len(raw) != source_size or source.read(1):
                raise ValueError("invalid source framing")
            self.admissions.append((owner, source_size, raw))
            return jobs.submit(request, owner=owner)

        self.service = BuildHostService(
            jobs=self.jobs,
            load_policy=lambda: self.policy,
            admit_source=admit_source,
            tool_versions=lambda: {"go": "go1.24.0", "node": "v22.1.0",
                                   "python": "/private/bin/python", "other": "hidden"},
            logs_root=str(self.logs_root),
            artifacts_root=str(self.artifacts_root),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def exchange(self, message: bytes, *, payload: bytes = b"",
                 principal: str = "alice@example") -> tuple[dict, bytes, bytes]:
        output = BytesIO()
        self.service.handle(message, principal=principal, input_stream=BytesIO(payload),
                            output_stream=output)
        wire = output.getvalue()
        line, separator, raw_payload = wire.partition(b"\n")
        self.assertTrue(separator)
        self.assertLessEqual(len(line) + 1, MAX_RESPONSE_LINE_BYTES)
        return json.loads(line), raw_payload, line + separator

    def assert_rejected(self, response: dict) -> None:
        self.assertEqual(response, {
            "schema": 1, "ok": False, "error": "rejected", "payload_bytes": 0,
        })

    def test_capabilities_project_only_authenticated_grants_and_safe_versions(self) -> None:
        self.policy = build_policy(other_principal=True)

        response, payload, _line = self.exchange(header("capabilities"))

        self.assertTrue(response["ok"])
        self.assertEqual(payload, b"")
        self.assertEqual(response["payload_bytes"], 0)
        self.assertEqual(set(response["result"]["projects"]), {"site"})
        self.assertEqual(response["result"]["tool_versions"], {
            "go": "go1.24.0", "node": "v22.1.0",
        })
        self.assertNotIn("bob@example", json.dumps(response))
        self.assertNotIn("/private/bin/python", json.dumps(response))

    def test_submit_passes_only_trusted_principal_and_exact_source_to_admission(self) -> None:
        request = build_request()
        source = b"\x00\xffsource"

        response, payload, _line = self.exchange(
            header("submit", request=request, source_size=len(source)), payload=source,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["state"], "queued")
        self.assertEqual(payload, b"")
        self.assertEqual(self.admissions, [("alice@example", len(source), source)])
        self.assertNotIn("owner", response["result"])

    def test_unknown_project_or_owner_is_rejected_before_admission_or_data_access(self) -> None:
        self.policy = build_policy(project="another-project")
        request = build_request()
        response, _payload, _line = self.exchange(
            header("submit", request=request, source_size=1), payload=b"x",
        )
        self.assert_rejected(response)
        self.assertEqual(self.admissions, [])

        self.jobs.submit(request, owner="alice@example")
        response, _payload, _line = self.exchange(
            header("status", request_id=request["request_id"]), principal="mallory@example",
        )
        self.assert_rejected(response)

    def test_same_owner_can_read_status_and_cancel(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")

        status, _payload, _line = self.exchange(header("status", request_id=request["request_id"]))
        cancelled, _payload, _line = self.exchange(
            header("cancel", request_id=request["request_id"]),
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["result"]["state"], "queued")
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["result"]["state"], "cancelled")

    def test_reserved_fields_unknown_actions_and_schemas_are_rejected_before_effect(self) -> None:
        self.service.load_policy = Mock(return_value=self.policy)
        messages = [
            header("capabilities", owner="attacker"),
            b'{"schema":2,"action":"capabilities"}\n',
            b'{"schema":1,"action":"exec"}\n',
            b'{"schema":1,"action":"capabilities","action":"status"}\n',
            b"{}\n" * (MAX_HEADER_LINE_BYTES // 2 + 1),
            header("capabilities").replace(b"\n", b"\r\n"),
        ]
        request_with_owner = build_request()
        request_with_owner["owner"] = "attacker"
        messages.append(header("submit", request=request_with_owner, source_size=1))
        for message in messages:
            with self.subTest(header_length=len(message)):
                response, _payload, _line = self.exchange(message)
                self.assert_rejected(response)
        self.service.load_policy.assert_not_called()
        self.assertEqual(self.admissions, [])

    def test_malformed_submit_types_and_oversized_source_are_rejected(self) -> None:
        request = build_request()
        for size in (True, 1.5, 0, 5 * 1024**3 + 1):
            with self.subTest(source_size=size):
                response, _payload, _line = self.exchange(
                    header("submit", request=request, source_size=size), payload=b"x",
                )
                self.assert_rejected(response)
        malformed = b'{"schema":1,"action":"submit"\n'
        response, _payload, _line = self.exchange(malformed)
        self.assert_rejected(response)
        self.assertEqual(self.admissions, [])

    def test_nonupload_actions_require_eof_before_job_lookup(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")
        original_status = self.jobs.status
        self.jobs.status = Mock(wraps=original_status)

        response, _payload, _line = self.exchange(
            header("status", request_id=request["request_id"]), payload=b"trailing",
        )

        self.assert_rejected(response)
        self.jobs.status.assert_not_called()

        response, _payload, _line = self.exchange(header("status", request_id="invalid"))
        self.assert_rejected(response)
        self.jobs.status.assert_not_called()

    def test_revoked_project_grant_blocks_status_and_cancel(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")
        self.policy = build_policy(project="another-project")

        status, _payload, _line = self.exchange(header("status", request_id=request["request_id"]))
        cancelled, _payload, _line = self.exchange(
            header("cancel", request_id=request["request_id"]),
        )

        self.assert_rejected(status)
        self.assert_rejected(cancelled)
        self.assertEqual(self.jobs.status(request["request_id"], owner="alice@example")["state"],
                         "queued")

    def test_log_reads_bounded_raw_binary_chunks_and_reports_waiting(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")
        content = bytes(range(256)) * 400
        path = self.logs_root / (request["request_id"] + ".log")
        path.write_bytes(content)
        os.chmod(path, 0o600)

        response, payload, line = self.exchange(header(
            "log", request_id=request["request_id"], offset=0,
            max_bytes=MAX_LOG_CHUNK_BYTES,
        ))

        self.assertTrue(response["ok"])
        self.assertEqual(response["payload_bytes"], MAX_LOG_CHUNK_BYTES)
        self.assertEqual(payload, content[:MAX_LOG_CHUNK_BYTES])
        self.assertFalse(response["result"]["waiting"])
        self.assertLessEqual(len(line), MAX_RESPONSE_LINE_BYTES)

    def test_queued_job_without_log_returns_waiting(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")

        response, payload, _line = self.exchange(header(
            "log", request_id=request["request_id"], offset=0, max_bytes=128,
        ))

        self.assertTrue(response["ok"])
        self.assertEqual(payload, b"")
        self.assertEqual(response["payload_bytes"], 0)
        self.assertTrue(response["result"]["waiting"])

    def test_zero_byte_log_chunk_is_bounded_and_returns_no_raw_payload(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")
        path = self.logs_root / (request["request_id"] + ".log")
        path.write_bytes(b"log-data")
        os.chmod(path, 0o600)

        response, payload, _line = self.exchange(header(
            "log", request_id=request["request_id"], offset=0, max_bytes=0,
        ))

        self.assertTrue(response["ok"])
        self.assertEqual(response["payload_bytes"], 0)
        self.assertEqual(payload, b"")
        self.assertFalse(response["result"]["waiting"])

    def _complete_job(self, request: dict, *, digest: str, size: int) -> None:
        self.jobs.submit(request, owner="alice@example")
        with self.jobs.worker() as lease:
            claim = lease.claim()
            self.assertIsNotNone(claim)
            self.assertEqual(claim["request_id"], request["request_id"])
            lease.finish(request["request_id"], outcome="succeeded", work_stopped=True,
                         artifact=build_artifact(request, digest=digest, size=size))

    def test_artifact_returns_verified_raw_bundle_bytes(self) -> None:
        request = build_request()
        bundle = b"BASALTWATER-ARTIFACT\x00\x01\n\x00\xff"
        digest = hashlib.sha256(bundle).hexdigest()
        self._complete_job(request, digest=digest, size=len(bundle))
        path = self.artifacts_root / (request["request_id"] + ".bundle")
        path.write_bytes(bundle)
        os.chmod(path, 0o600)

        response, payload, line = self.exchange(header("artifact", request_id=request["request_id"]))

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"sha256": digest, "size": len(bundle)})
        self.assertEqual(response["payload_bytes"], len(bundle))
        self.assertEqual(payload, bundle)
        self.assertLessEqual(len(line), MAX_RESPONSE_LINE_BYTES)

    def test_wrong_artifact_hash_size_type_and_links_are_rejected(self) -> None:
        content = b"private bundle bytes"
        correct_digest = hashlib.sha256(content).hexdigest()
        cases = ("hash", "size", "directory", "symlink", "hardlink", "mode")
        for index, case in enumerate(cases, start=1):
            with self.subTest(case=case):
                request = build_request(request_id=f"{index:032x}")
                digest = "f" * 64 if case == "hash" else correct_digest
                size = len(content) + 1 if case == "size" else len(content)
                self._complete_job(request, digest=digest, size=size)
                path = self.artifacts_root / (request["request_id"] + ".bundle")
                if case == "directory":
                    path.mkdir(mode=0o700)
                elif case == "symlink":
                    os.symlink("missing-target", path)
                else:
                    path.write_bytes(content)
                    os.chmod(path, 0o644 if case == "mode" else 0o600)
                    if case == "hardlink":
                        os.link(path, self.artifacts_root / "extra-link")

                response, payload, _line = self.exchange(
                    header("artifact", request_id=request["request_id"]),
                )

                self.assert_rejected(response)
                self.assertEqual(payload, b"")

    def test_read_errors_return_only_fixed_unavailable_code(self) -> None:
        request = build_request()
        self.jobs.submit(request, owner="alice@example")
        self.service._read_log = Mock(side_effect=OSError("private path and exception details"))

        response, payload, line = self.exchange(header(
            "log", request_id=request["request_id"], offset=0, max_bytes=32,
        ))

        self.assertEqual(response, {
            "schema": 1, "ok": False, "error": "unavailable", "payload_bytes": 0,
        })
        self.assertEqual(payload, b"")
        self.assertNotIn(b"private", line)

    def test_serve_once_reads_one_header_line(self) -> None:
        input_stream = BytesIO(header("capabilities"))
        output_stream = BytesIO()

        self.service.serve_once(principal="alice@example", input_stream=input_stream,
                                 output_stream=output_stream)

        response = json.loads(output_stream.getvalue().split(b"\n", 1)[0])
        self.assertTrue(response["ok"])
        self.assertEqual(response["payload_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
