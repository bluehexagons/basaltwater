from __future__ import annotations

import errno
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from lib.build_admission import DeploymentAdmissionStore
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import (
    DEFAULT_LIMITS,
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    request_digest,
)
from lib.build_target_service import (
    BuildTargetService,
    MAX_ARTIFACT_BUNDLE_BYTES,
    MAX_HEADER_LINE_BYTES,
    MAX_RESPONSE_LINE_BYTES,
)


def build_request(request_id: str = "a" * 32, *, project: str = "shop-source") -> dict:
    return {
        "schema": 1,
        "request_id": request_id,
        "project": project,
        "source": {
            "revision": "b" * 40,
            "snapshot_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
        },
        "platform": dict(DEBIAN_PLATFORM),
        "components": [{
            "name": "web",
            "type": "static",
            "artifacts": ["dist"],
            "commands": [],
            "env": {"BUILD_MODE": "release"},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


def deployment(*, application: str = "shop", domain: str = "shop.example") -> dict:
    return {
        "schema": 1,
        "application": application,
        "project": "shop-source",
        "platform": dict(DEBIAN_PLATFORM),
        "components": [{
            "name": "web",
            "type": "static",
            "domain": domain,
            "path": "/",
            "artifacts": ["dist"],
            "output": "dist",
            "spa": True,
        }],
    }


def target_policy(*, principals: list[str] | None = None, root: str = "/srv/apps/shop") -> dict:
    return {
        "schema": 1,
        "applications": {
            "shop": {
                "project": "shop-source",
                "principals": ["alice@example"] if principals is None else principals,
                "deployment_root": root,
                "platform": dict(DEBIAN_PLATFORM),
                "limits": {
                    "artifact_bytes": MAX_ARTIFACT_BYTES,
                    "artifact_entries": 100_000,
                },
                "components": {
                    "web": {"type": "static", "domain": "shop.example", "path": "/"},
                },
            },
        },
    }


def header(action: str, **fields: object) -> bytes:
    return json.dumps(
        {"schema": 1, "action": action, **fields},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


class MutableClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class BuildTargetServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.clock = MutableClock()
        self.admissions = DeploymentAdmissionStore(self.root / "admissions.json", clock=self.clock)
        self.policy = target_policy()
        self.staged: dict[tuple[str, int, str], bytes] = {}
        self.activate_release = Mock(return_value={
            "private_path": "/srv/apps/shop/releases/current",
            "environment": {"TOKEN": "private"},
        })

        def stage_artifact(operation_id, generation, request, *, application, owner, bindings_sha256,
                           artifact_sha256, artifact_size, source, admissions):
            raw = source.read(artifact_size)
            if not isinstance(raw, bytes) or len(raw) != artifact_size or source.read(1):
                raise ValueError("invalid artifact body framing")
            if hashlib.sha256(raw).hexdigest() != artifact_sha256:
                raise ValueError("invalid artifact digest")
            record = admissions.status(operation_id, owner=owner)
            if (record["application"] != application or record["generation"] != generation
                    or record["request_sha256"] != request_digest(request)
                    or record["bindings_sha256"] != bindings_sha256):
                raise ValueError("admission changed during artifact staging")
            self.staged[(operation_id, generation, artifact_sha256)] = raw
            return {"private_path": "/srv/private/staged.bundle", "token": "hidden"}

        self.stage_artifact = Mock(side_effect=stage_artifact)
        self.stage_callback_impl = stage_artifact
        self.load_policy = Mock(side_effect=lambda: self.policy)
        self.service = BuildTargetService(
            admissions=self.admissions,
            load_policy=self.load_policy,
            stage_artifact=self.stage_artifact,
            activate_release=self.activate_release,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def exchange(
        self,
        message: bytes,
        *,
        payload: bytes = b"",
        principal: str = "alice@example",
    ) -> tuple[dict, bytes, bytes]:
        output = BytesIO()
        self.service.handle(
            message, principal=principal, input_stream=BytesIO(payload), output_stream=output,
        )
        wire = output.getvalue()
        line, separator, raw_payload = wire.partition(b"\n")
        self.assertTrue(separator)
        self.assertLessEqual(len(line) + 1, MAX_RESPONSE_LINE_BYTES)
        self.assertEqual(raw_payload, b"")
        return json.loads(line), raw_payload, line + separator

    def reserve(self, *, request: dict | None = None, key: str = "e" * 32,
                ttl_seconds: int = 3600) -> dict:
        response, _payload, _line = self.exchange(header(
            "reserve", idempotency_key=key, application="shop",
            request=build_request() if request is None else request,
            deployment=deployment(), ttl_seconds=ttl_seconds,
        ))
        self.assertTrue(response["ok"], response)
        return response["result"]

    def stage_message(self, record: dict, *, request: dict | None = None,
                      target: dict | None = None, generation: int | None = None,
                      body: bytes = b"bundle") -> bytes:
        request_value = build_request() if request is None else request
        digest = hashlib.sha256(body).hexdigest()
        return header(
            "stage", operation_id=record["operation_id"],
            generation=record["generation"] if generation is None else generation,
            request=request_value,
            deployment=deployment() if target is None else target,
            artifact_sha256=digest, artifact_size=len(body),
        )

    def assert_rejected(self, response: dict) -> None:
        self.assertEqual(response, {
            "schema": 1, "ok": False, "error": "rejected", "payload_bytes": 0,
        })

    def test_preflight_returns_only_selected_authorized_projection(self) -> None:
        response, _payload, _line = self.exchange(header("preflight", application="shop"))

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {
            "application": "shop",
            "project": "shop-source",
            "routes": [{"name": "web", "type": "static", "domain": "shop.example",
                        "path": "/"}],
            "platform": dict(DEBIAN_PLATFORM),
            "limits": {"artifact_bytes": MAX_ARTIFACT_BYTES, "artifact_entries": 100_000},
        })
        encoded = json.dumps(response)
        for forbidden in ("deployment_root", "/srv/apps/shop", "dist", "BUILD_MODE"):
            self.assertNotIn(forbidden, encoded)

        denied, _payload, _line = self.exchange(
            header("preflight", application="shop"), principal="mallory@example",
        )
        self.assert_rejected(denied)

    def test_reserve_binds_intent_and_idempotency_replay_does_not_advance_generation(self) -> None:
        first = self.reserve(ttl_seconds=30)
        replay = self.reserve(ttl_seconds=120)

        self.assertEqual(first["operation_id"], replay["operation_id"])
        self.assertEqual(first["generation"], 1)
        self.assertEqual(first["expires_at"], replay["expires_at"])
        self.assertEqual(first["input"], {
            "request_id": "a" * 32,
            "request_sha256": request_digest(build_request()),
        })
        self.assertEqual(first["source"], build_request()["source"])
        self.assertEqual(len(first["bindings_sha256"]), 64)
        encoded = json.dumps(first)
        for forbidden in ("owner", "alice@example", "idempotency_key", "deployment_root",
                          "/srv/apps/shop", "BUILD_MODE"):
            self.assertNotIn(forbidden, encoded)

        self.clock.value += 60
        expired_replay = self.reserve(ttl_seconds=120)
        self.assertEqual(expired_replay["operation_id"], first["operation_id"])
        self.assertEqual(expired_replay["expires_at"], first["expires_at"])
        self.assertTrue(expired_replay["expired"])
        self.assertEqual(self.admissions.status(first["operation_id"], owner="alice@example")["generation"], 1)

    def test_status_and_cancel_are_owner_scoped_and_recheck_current_application_project_grant(self) -> None:
        record = self.reserve()
        self.load_policy.reset_mock()

        denied, _payload, _line = self.exchange(
            header("status", operation_id=record["operation_id"]), principal="mallory@example",
        )

        self.assert_rejected(denied)
        self.load_policy.assert_not_called()

        self.policy = target_policy(principals=["bob@example"])
        for action in ("status", "cancel"):
            response, _payload, _line = self.exchange(
                header(action, operation_id=record["operation_id"]),
            )
            self.assert_rejected(response)
        self.assertEqual(self.admissions.status(record["operation_id"], owner="alice@example")["state"],
                         "reserved")

    def test_status_returns_a_whitelisted_projection_not_the_admission_record(self) -> None:
        record = self.reserve()
        response, _payload, _line = self.exchange(
            header("status", operation_id=record["operation_id"]),
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["operation_id"], record["operation_id"])
        self.assertEqual(response["result"]["input"], record["input"])
        self.assertEqual(response["result"]["source"], record["source"])
        encoded = json.dumps(response)
        for forbidden in ("owner", "alice@example", "idempotency_key", "/srv/apps/shop"):
            self.assertNotIn(forbidden, encoded)

    def test_nonstage_actions_require_exact_eof_before_policy_or_admission_access(self) -> None:
        self.load_policy.reset_mock()
        original_status = self.admissions.status
        self.admissions.status = Mock(wraps=original_status)

        response, _payload, _line = self.exchange(
            header("status", operation_id="a" * 32), payload=b"extra",
        )

        self.assert_rejected(response)
        self.load_policy.assert_not_called()
        self.admissions.status.assert_not_called()

        response, _payload, _line = self.exchange(
            header("reserve", idempotency_key="e" * 32, application="shop",
                   request=build_request(), deployment=deployment(), ttl_seconds=30),
            payload=b"x",
        )
        self.assert_rejected(response)
        self.assertEqual(self.reserve()["generation"], 1)

    def test_duplicate_json_unknown_owner_path_and_projection_fields_fail_closed(self) -> None:
        messages = [
            b'{"schema":1,"action":"preflight","action":"status","application":"shop"}\n',
            b'{"schema":1,"action":"preflight","application":"shop","application":"shop"}\n',
            header("preflight", application="shop", owner="attacker"),
            header("preflight", application="shop", path="/etc/passwd"),
            header("reserve", idempotency_key="e" * 32, application="shop",
                   request=build_request(),
                   deployment={**deployment(), "deployment_root": "/tmp/attacker"},
                   ttl_seconds=30),
            header("reserve", idempotency_key="e" * 32, application="shop",
                   request=build_request(),
                   deployment={**deployment(), "components": [{
                       **deployment()["components"][0], "exec": ["unsafe"],
                   }]}, ttl_seconds=30),
            b'{"schema":1,"action":"preflight","application":"shop"}\r\n',
        ]
        self.load_policy.reset_mock()
        for message in messages:
            with self.subTest(message=message[:80]):
                response, _payload, _line = self.exchange(message)
                self.assert_rejected(response)
        self.load_policy.assert_not_called()
        self.assertEqual(self.reserve()["generation"], 1)

    def test_malformed_principal_schema_types_line_and_field_limits_are_rejected(self) -> None:
        good = header("preflight", application="shop")
        messages = [
            b'{"schema":true,"action":"preflight","application":"shop"}\n',
            b'{"schema":1,"action":"preflight","application":"Shop"}\n',
            b'{"schema":1,"action":"preflight","application":"shop"}',
            good[:-1] + b"\n\n",
            b" " * MAX_HEADER_LINE_BYTES + b"\n",
            header("reserve", idempotency_key="e" * 32, application="shop",
                   request=build_request(), deployment=deployment(), ttl_seconds=True),
        ]
        self.load_policy.reset_mock()
        for message in messages:
            response, _payload, _line = self.exchange(message)
            self.assert_rejected(response)
        response, _payload, _line = self.exchange(good, principal="bad\nowner")
        self.assert_rejected(response)
        self.load_policy.assert_not_called()

    def test_stage_exact_body_digest_is_typed_and_callback_result_stays_private(self) -> None:
        record = self.reserve()
        artifact = b"BASALTWATER-ARTIFACT\x00\x01\n\x00\xff"

        response, _payload, _line = self.exchange(
            self.stage_message(record, body=artifact), payload=artifact,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(self.stage_artifact.call_args.kwargs["application"], "shop")
        self.assertEqual(response["result"], {
            "operation_id": record["operation_id"],
            "generation": 1,
            "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
            "artifact_size": len(artifact),
            "status": "staged",
        })
        self.assertEqual(self.staged[(record["operation_id"], 1,
                                      hashlib.sha256(artifact).hexdigest())], artifact)
        encoded = json.dumps(response)
        for forbidden in ("private_path", "/srv/private", "token", "hidden", "owner"):
            self.assertNotIn(forbidden, encoded)

    def test_wrong_bindings_request_and_generation_block_stage_before_stream(self) -> None:
        record = self.reserve()
        body = b"artifact"
        cases = [
            self.stage_message(record, generation=record["generation"] + 1, body=body),
            self.stage_message(record, request=build_request(request_id="f" * 32), body=body),
            self.stage_message(record, target=deployment(domain="moved.example"), body=body),
        ]
        for message in cases:
            stream = BytesIO(body)
            output = BytesIO()
            self.service.handle(message, principal="alice@example", input_stream=stream,
                                output_stream=output)
            response = json.loads(output.getvalue().split(b"\n", 1)[0])
            with self.subTest(error=response.get("error")):
                self.assert_rejected(response)
                self.assertEqual(stream.tell(), 0)
        self.stage_artifact.assert_not_called()

    def test_cancelled_superseded_and_expired_admissions_cannot_read_stage_body(self) -> None:
        first = self.reserve(key="1" * 32)
        cancelled = self.reserve(key="2" * 32)
        self.admissions.cancel(cancelled["operation_id"], owner="alice@example")
        stale = self.reserve(key="3" * 32)
        expired = self.reserve(key="4" * 32, ttl_seconds=1)
        self.clock.value += 2
        attempts = [first, cancelled, stale, expired]
        for record in attempts:
            body = b"bytes"
            stream = BytesIO(body)
            output = BytesIO()
            self.service.handle(self.stage_message(record, body=body), principal="alice@example",
                                input_stream=stream, output_stream=output)
            response = json.loads(output.getvalue().split(b"\n", 1)[0])
            self.assert_rejected(response)
            self.assertEqual(stream.tell(), 0)
        self.stage_artifact.assert_not_called()

    def test_stage_rejects_wrong_size_body_and_global_or_request_ceiling_before_callback(self) -> None:
        record = self.reserve()
        message = self.stage_message(record, body=b"expected-size")
        short, _payload, _line = self.exchange(message, payload=b"short")
        self.assert_rejected(short)
        self.stage_artifact.assert_called_once()
        self.stage_artifact.reset_mock()

        too_large = header(
            "stage", operation_id=record["operation_id"], generation=record["generation"],
            request=build_request(), deployment=deployment(), artifact_sha256="f" * 64,
            artifact_size=MAX_ARTIFACT_BUNDLE_BYTES + 1,
        )
        response, _payload, _line = self.exchange(too_large)
        self.assert_rejected(response)
        self.stage_artifact.assert_not_called()

        small_request = build_request()
        small_request["limits"]["artifact_bytes"] = 1
        too_large_for_request = header(
            "stage", operation_id=record["operation_id"], generation=record["generation"],
            request=small_request, deployment=deployment(), artifact_sha256="f" * 64,
            artifact_size=MAX_DESCRIPTOR_BYTES + 1026,
        )
        response, _payload, _line = self.exchange(too_large_for_request)
        self.assert_rejected(response)
        self.stage_artifact.assert_not_called()

    def test_current_policy_revocation_blocks_reserve_and_stage_callbacks(self) -> None:
        record = self.reserve()
        self.policy = target_policy(principals=["bob@example"])
        body = b"artifact"
        response, _payload, _line = self.exchange(
            self.stage_message(record, body=body), payload=body,
        )
        self.assert_rejected(response)
        self.stage_artifact.assert_not_called()

        reserve_response, _payload, _line = self.exchange(header(
            "reserve", idempotency_key="9" * 32, application="shop",
            request=build_request(request_id="9" * 32), deployment=deployment(), ttl_seconds=60,
        ))
        self.assert_rejected(reserve_response)
        self.assertEqual(len(self.admissions._load()["operations"]), 1)

    def test_activation_callback_runs_once_cached_replay_is_fixed_and_result_is_discarded(self) -> None:
        record = self.reserve()
        artifact = b"verified bundle"
        digest = hashlib.sha256(artifact).hexdigest()
        staged = self.exchange(self.stage_message(record, body=artifact), payload=artifact)[0]
        self.assertTrue(staged["ok"])
        activation_header = header(
            "activate", operation_id=record["operation_id"], generation=record["generation"],
            request=build_request(), deployment=deployment(), artifact_sha256=digest,
        )

        first, _payload, _line = self.exchange(activation_header)
        second, _payload, _line = self.exchange(activation_header)

        expected = {
            "application": "shop", "operation_id": record["operation_id"],
            "generation": 1, "artifact_sha256": digest, "status": "completed",
        }
        self.assertEqual(first["result"], expected)
        self.assertEqual(second["result"], expected)
        self.assertEqual(self.activate_release.call_count, 1)
        callback_kwargs = self.activate_release.call_args.kwargs
        self.assertEqual(set(callback_kwargs), {
            "bindings", "request", "operation_id", "generation", "artifact_sha256", "principal",
        })
        self.assertEqual(callback_kwargs["principal"], "alice@example")
        self.assertEqual(callback_kwargs["bindings"]["deployment_root"], "/srv/apps/shop")
        persisted = self.admissions._load()["operations"][record["operation_id"]]
        self.assertIsNone(persisted["result"])
        for wire in (json.dumps(first), json.dumps(second)):
            for forbidden in ("private_path", "/srv/apps/shop", "TOKEN", "private", "environment"):
                self.assertNotIn(forbidden, wire)

    def test_activation_rechecks_current_policy_under_lock_and_uncertain_failure_never_replays(self) -> None:
        record = self.reserve()
        artifact = b"verified bundle"
        digest = hashlib.sha256(artifact).hexdigest()
        staged = self.exchange(self.stage_message(record, body=artifact), payload=artifact)[0]
        self.assertTrue(staged["ok"])
        self.activate_release.side_effect = RuntimeError("/srv/private and secret=hidden")
        activation_header = header(
            "activate", operation_id=record["operation_id"], generation=record["generation"],
            request=build_request(), deployment=deployment(), artifact_sha256=digest,
        )

        failed, _payload, line = self.exchange(activation_header)
        replay, _payload, _line = self.exchange(activation_header)

        self.assert_rejected(failed)
        self.assert_rejected(replay)
        self.assertNotIn(b"/srv/private", line)
        self.assertNotIn(b"hidden", line)
        self.assertEqual(self.activate_release.call_count, 1)
        self.assertEqual(self.admissions.status(record["operation_id"], owner="alice@example")["state"],
                         "uncertain")

    def test_activation_policy_revocation_is_seen_inside_admission_lock_before_callback(self) -> None:
        record = self.reserve()
        artifact = b"bundle"
        digest = hashlib.sha256(artifact).hexdigest()
        self.exchange(self.stage_message(record, body=artifact), payload=artifact)

        # Authorization runs once before admissions.activate and again from its
        # action, after the durable activation marker is written.
        policy_values = [target_policy(), target_policy(principals=["bob@example"])]
        self.load_policy.side_effect = lambda: policy_values.pop(0)
        response, _payload, _line = self.exchange(header(
            "activate", operation_id=record["operation_id"], generation=record["generation"],
            request=build_request(), deployment=deployment(), artifact_sha256=digest,
        ))

        self.assert_rejected(response)
        self.activate_release.assert_not_called()
        self.assertEqual(self.admissions.status(record["operation_id"], owner="alice@example")["state"],
                         "uncertain")

    def test_os_permission_failures_are_unavailable_and_callback_details_never_escape(self) -> None:
        self.load_policy.side_effect = PermissionError(errno.EACCES, "private policy path")
        response, _payload, line = self.exchange(header("preflight", application="shop"))
        self.assertEqual(response, {
            "schema": 1, "ok": False, "error": "unavailable", "payload_bytes": 0,
        })
        self.assertNotIn(b"private", line)

        self.load_policy.side_effect = PermissionError("authorization denied")
        response, _payload, _line = self.exchange(header("preflight", application="shop"))
        self.assert_rejected(response)

        self.load_policy.side_effect = None
        self.load_policy.return_value = self.policy
        record = self.reserve()
        original_stage = self.stage_callback_impl
        self.stage_artifact.side_effect = PermissionError(errno.EACCES, "/private/staging")
        body = b"artifact"
        response, _payload, line = self.exchange(
            self.stage_message(record, body=body), payload=body,
        )
        self.assertEqual(response["error"], "unavailable")
        self.assertNotIn(b"/private", line)

        self.stage_artifact.side_effect = original_stage
        self.stage_artifact.reset_mock()
        self.activate_release.side_effect = OSError(errno.EIO, "/private/release")
        digest = hashlib.sha256(body).hexdigest()
        self.exchange(self.stage_message(record, body=body), payload=body)
        response, _payload, line = self.exchange(header(
            "activate", operation_id=record["operation_id"], generation=record["generation"],
            request=build_request(), deployment=deployment(), artifact_sha256=digest,
        ))
        self.assertEqual(response["error"], "unavailable")
        self.assertNotIn(b"/private", line)


if __name__ == "__main__":
    unittest.main()
