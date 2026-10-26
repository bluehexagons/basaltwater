from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from lib.build_admission import DeploymentAdmissionStore
from lib.build_artifacts import create_artifact
from lib.build_protocol import request_digest
from lib.build_target_artifacts import BuildTargetArtifactStore
from tests.test_build_protocol import build_request


OWNER = "controller"
BINDINGS = "c" * 64
_AUTO = object()


class MutableClock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value


class CountingStream(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return super().read(size)


class FailingStream(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self.read_calls > 1:
            raise OSError("stream interrupted")
        return super().read(min(size, 12))


class BuildTargetArtifactStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.parent.chmod(0o700)
        self.workspace = self.parent / "workspace"
        (self.workspace / "bin").mkdir(parents=True)
        (self.workspace / "bin/api").write_bytes(b"small-target-binary")
        self.request = build_request()
        self.request["limits"]["artifact_bytes"] = 1024 * 1024
        self.bundle_path = self.parent / "source-bundle.bwa"
        artifact = create_artifact(str(self.workspace), self.request, str(self.bundle_path))
        self.bundle = self.bundle_path.read_bytes()
        self.artifact_sha256 = artifact["sha256"]
        self.artifact_size = artifact["size"]

        self.staging_root = self.parent / "target-artifacts"
        self.staging_root.mkdir(mode=0o700)
        self.staging_root.chmod(0o700)
        self.store = BuildTargetArtifactStore(self.staging_root)
        self.clock = MutableClock()
        self.admissions = DeploymentAdmissionStore(self.parent / "admission.json", clock=self.clock)
        self.next_key = 0

    def reserve(
        self,
        request: dict | None = None,
        *,
        owner: str = OWNER,
        application: str = "web",
        bindings_sha256: str = BINDINGS,
        ttl_seconds: int = 3600,
    ) -> dict:
        self.next_key += 1
        return self.admissions.reserve(
            application,
            self.request if request is None else request,
            owner=owner,
            idempotency_key=f"{self.next_key:032x}",
            bindings_sha256=bindings_sha256,
            ttl_seconds=ttl_seconds,
        )

    def stage(
        self,
        record: dict,
        *,
        request: dict | None = None,
        source=_AUTO,
        owner: str = OWNER,
        application: str = "web",
        generation: int | None = None,
        bindings_sha256: str = BINDINGS,
        artifact_sha256: str | None = None,
        artifact_size: int | None = None,
        store: BuildTargetArtifactStore | None = None,
    ) -> dict:
        stream = BytesIO(self.bundle) if source is _AUTO else source
        target = self.store if store is None else store
        return target.stage(
            record["operation_id"],
            record["generation"] if generation is None else generation,
            self.request if request is None else request,
            application=application,
            owner=owner,
            bindings_sha256=bindings_sha256,
            artifact_sha256=self.artifact_sha256 if artifact_sha256 is None else artifact_sha256,
            artifact_size=self.artifact_size if artifact_size is None else artifact_size,
            source=stream,
            admissions=self.admissions,
        )

    def test_stage_publishes_private_receipt_and_exact_replay(self) -> None:
        record = self.reserve()
        first = self.stage(record)
        lock_path = self.staging_root / ".build-target-artifacts.lock"
        lock_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        replay_stream = CountingStream(self.bundle)
        replay = self.stage(record, source=replay_stream)
        without_stream = self.stage(record, source=None)

        self.assertEqual(first, replay)
        self.assertEqual(first, without_stream)
        self.assertGreater(replay_stream.read_calls, 0)
        self.assertEqual(first["operation_id"], record["operation_id"])
        self.assertEqual(first["request_sha256"], request_digest(self.request))
        self.assertEqual(first["artifact_sha256"], self.artifact_sha256)
        self.assertEqual(first["artifact_size"], self.artifact_size)
        self.assertNotIn("path", first)
        self.assertNotIn(str(self.staging_root), repr(first))

        intent_path = self.staging_root / (record["operation_id"] + ".intent.json")
        bundle_path = self.staging_root / (record["operation_id"] + ".bundle")
        ready_path = self.staging_root / (record["operation_id"] + ".ready")
        intent = json.loads(intent_path.read_text())
        self.assertEqual(
            intent["reservation_bytes"],
            self.artifact_size + self.request["limits"]["artifact_bytes"]
            + 64 * 1024**2 + 2048,
        )
        self.assertEqual(stat.S_IMODE(intent_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(bundle_path.stat().st_mode), 0o400)
        self.assertTrue(ready_path.is_file())
        self.assertEqual(
            (lock_path.stat().st_dev, lock_path.stat().st_ino),
            lock_identity,
        )
        retained = intent_path.read_text()
        for private_value in ("go build", "CGO_ENABLED"):
            self.assertNotIn(private_value, retained)
        self.assertEqual(bundle_path.read_bytes(), self.bundle)

    def test_different_digest_or_size_cannot_rebind_or_overwrite(self) -> None:
        record = self.reserve()
        self.stage(record)
        bundle_path = self.staging_root / (record["operation_id"] + ".bundle")
        before = (bundle_path.stat().st_dev, bundle_path.stat().st_ino, bundle_path.read_bytes())
        for digest, size in (("e" * 64, self.artifact_size),
                             (self.artifact_sha256, self.artifact_size + 1)):
            stream = CountingStream(self.bundle)
            with self.subTest(digest=digest[:4], size=size):
                with self.assertRaisesRegex(ValueError, "different inputs"):
                    self.stage(record, source=stream, artifact_sha256=digest, artifact_size=size)
                self.assertEqual(stream.read_calls, 0)
        after = (bundle_path.stat().st_dev, bundle_path.stat().st_ino, bundle_path.read_bytes())
        self.assertEqual(after, before)

    def test_wrong_owner_request_generation_application_or_bindings_reject_before_read(self) -> None:
        record = self.reserve()
        changed_request = build_request()
        changed_request["components"][0]["commands"] = ["different build"]
        cases = (
            {"owner": "another-controller"},
            {"request": changed_request},
            {"generation": record["generation"] + 1},
            {"application": "other-app"},
            {"bindings_sha256": "d" * 64},
        )
        for overrides in cases:
            stream = CountingStream(self.bundle)
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValueError, PermissionError)):
                    self.stage(record, source=stream, **overrides)
                self.assertEqual(stream.read_calls, 0)
        self.assertFalse((self.staging_root / (record["operation_id"] + ".intent.json")).exists())

    def test_cancelled_expired_and_superseded_reservations_cannot_start(self) -> None:
        cancelled = self.reserve()
        self.admissions.cancel(cancelled["operation_id"], owner=OWNER)
        expired = self.reserve(ttl_seconds=1)
        self.clock.value += 2
        superseded = self.reserve()
        current = self.reserve()
        self.assertEqual(self.admissions.status(current["operation_id"], owner=OWNER)["state"], "reserved")
        for record in (cancelled, expired, superseded):
            stream = CountingStream(self.bundle)
            with self.subTest(operation_id=record["operation_id"]):
                with self.assertRaises(ValueError):
                    self.stage(record, source=stream)
                self.assertEqual(stream.read_calls, 0)
                self.assertFalse(
                    (self.staging_root / (record["operation_id"] + ".intent.json")).exists(),
                )

    def test_quota_and_record_limit_reject_before_stream_read(self) -> None:
        record = self.reserve()
        tiny = BuildTargetArtifactStore(self.staging_root, max_bytes=1)
        stream = CountingStream(self.bundle)
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.stage(record, source=stream, store=tiny)
        self.assertEqual(stream.read_calls, 0)
        self.assertFalse((self.staging_root / (record["operation_id"] + ".intent.json")).exists())

        first = self.reserve()
        one_record = BuildTargetArtifactStore(self.staging_root, max_records=1)
        self.stage(first, store=one_record)
        second = self.reserve()
        second_stream = CountingStream(self.bundle)
        with self.assertRaisesRegex(ValueError, "full"):
            self.stage(second, source=second_stream, store=one_record)
        self.assertEqual(second_stream.read_calls, 0)

    def test_size_digest_and_trailing_stream_fail_closed(self) -> None:
        cases = (
            (self.artifact_size - 1, self.bundle, self.artifact_sha256, "trailing"),
            (self.artifact_size + 1, self.bundle, self.artifact_sha256, "ended"),
            (self.artifact_size, self.bundle, "f" * 64, "pinned digest"),
            (self.artifact_size, self.bundle + b"x", self.artifact_sha256, "trailing"),
        )
        for size, content, digest, message in cases:
            record = self.reserve()
            stream = CountingStream(content)
            with self.subTest(size=size, message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self.stage(record, source=stream, artifact_size=size, artifact_sha256=digest)
                operation_id = record["operation_id"]
                self.assertTrue((self.staging_root / (operation_id + ".intent.json")).exists())
                self.assertTrue((self.staging_root / (operation_id + ".part")).exists())
                self.assertFalse((self.staging_root / (operation_id + ".ready")).exists())
                retry = CountingStream(self.bundle)
                with self.assertRaisesRegex(ValueError, "Incomplete or ambiguous"):
                    self.stage(record, source=retry, artifact_size=size, artifact_sha256=digest)
                self.assertEqual(retry.read_calls, 0)

    def test_malformed_bundle_and_interrupted_stream_retain_failure_evidence(self) -> None:
        malformed = self.reserve()
        malformed_bytes = b"not-an-artifact"
        with self.assertRaises(ValueError):
            self.stage(
                malformed, source=BytesIO(malformed_bytes), artifact_sha256=hashlib.sha256(
                    malformed_bytes,
                ).hexdigest(), artifact_size=len(malformed_bytes),
            )
        self.assertTrue((self.staging_root / (malformed["operation_id"] + ".verify")).is_dir())

        interrupted = self.reserve()
        with self.assertRaisesRegex(OSError, "interrupted"):
            self.stage(interrupted, source=FailingStream(self.bundle))
        part = self.staging_root / (interrupted["operation_id"] + ".part")
        self.assertEqual(part.stat().st_size, 12)
        self.assertEqual(stat.S_IMODE(part.stat().st_mode), 0o400)
        with self.assertRaisesRegex(ValueError, "Incomplete or ambiguous"):
            self.stage(interrupted, source=CountingStream(self.bundle))

    def test_descriptor_is_verified_against_the_actual_reserved_request(self) -> None:
        wrong_request = build_request()
        wrong_request["limits"]["artifact_bytes"] = 1024 * 1024
        wrong_request["components"][0]["commands"] = ["different build"]
        wrong_bundle_path = self.parent / "wrong-request.bwa"
        wrong_artifact = create_artifact(
            str(self.workspace), wrong_request, str(wrong_bundle_path),
        )
        wrong_bytes = wrong_bundle_path.read_bytes()
        record = self.reserve()
        with self.assertRaisesRegex(ValueError, "admitted build"):
            self.stage(
                record,
                source=BytesIO(wrong_bytes),
                artifact_sha256=wrong_artifact["sha256"],
                artifact_size=wrong_artifact["size"],
            )
        self.assertFalse((self.staging_root / (record["operation_id"] + ".ready")).exists())

    def test_cancellation_during_upload_prevents_acknowledgement(self) -> None:
        record = self.reserve()
        admissions = self.admissions

        class CancellingStream(BytesIO):
            cancelled = False

            def read(inner_self, size: int = -1) -> bytes:
                content = super(CancellingStream, inner_self).read(size)
                if content and not inner_self.cancelled:
                    inner_self.cancelled = True
                    admissions.cancel(record["operation_id"], owner=OWNER)
                return content

        stream = CancellingStream(self.bundle)
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.stage(record, source=stream)
        operation_id = record["operation_id"]
        self.assertTrue((self.staging_root / (operation_id + ".intent.json")).exists())
        self.assertTrue((self.staging_root / (operation_id + ".part")).exists())
        self.assertFalse((self.staging_root / (operation_id + ".bundle")).exists())
        self.assertFalse((self.staging_root / (operation_id + ".ready")).exists())

    def test_post_rename_directory_sync_failure_is_uncertain_and_not_replayed(self) -> None:
        record = self.reserve()
        with patch.object(self.store, "_sync_after_rename", side_effect=OSError("directory sync failed")):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                self.stage(record)
        operation_id = record["operation_id"]
        bundle_path = self.staging_root / (operation_id + ".bundle")
        original = (bundle_path.stat().st_dev, bundle_path.stat().st_ino, bundle_path.read_bytes())
        self.assertTrue(bundle_path.exists())
        self.assertFalse((self.staging_root / (operation_id + ".ready")).exists())
        with self.assertRaisesRegex(ValueError, "Incomplete or ambiguous"):
            self.stage(record, source=None)
        current = (bundle_path.stat().st_dev, bundle_path.stat().st_ino, bundle_path.read_bytes())
        self.assertEqual(current, original)

    def test_ready_marker_sync_failure_is_poisoned_for_replay(self) -> None:
        record = self.reserve()
        original_create = self.store._create_file

        def fail_after_ready(root_fd, name, content, label):
            original_create(root_fd, name, content, label)
            if label == "ready marker":
                raise OSError("ready marker sync failed")

        with patch.object(self.store, "_create_file", side_effect=fail_after_ready):
            with self.assertRaisesRegex(OSError, "ready marker sync failed"):
                self.stage(record)
        uncertain = self.staging_root / (record["operation_id"] + ".uncertain")
        self.assertTrue(uncertain.is_file())
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            self.stage(record, source=None)

    def test_completed_admission_replay_and_open_verified_activation(self) -> None:
        record = self.reserve()
        receipt = self.stage(record)

        def activate_release() -> dict:
            with self.store.open_verified_bundle(
                record["operation_id"], record["generation"], self.request,
                application="web", owner=OWNER, bindings_sha256=BINDINGS,
                artifact_sha256=receipt["artifact_sha256"],
            ) as bundle_path:
                digest = hashlib.sha256(Path(bundle_path).read_bytes()).hexdigest()
            return {"bundle_sha256": digest}

        result = self.admissions.activate(
            record["operation_id"], record["generation"], self.request,
            receipt["artifact_sha256"], owner=OWNER, bindings_sha256=BINDINGS,
            action=activate_release,
        )
        self.assertEqual(result, {"bundle_sha256": self.artifact_sha256})
        replay = self.stage(record, source=None)
        self.assertEqual(replay, receipt)

    def test_open_verified_accessor_checks_identity_and_does_not_accept_paths(self) -> None:
        record = self.reserve()
        receipt = self.stage(record)
        arguments = {
            "operation_id": record["operation_id"],
            "generation": record["generation"],
            "request": self.request,
            "application": "web",
            "owner": OWNER,
            "bindings_sha256": BINDINGS,
            "artifact_sha256": self.artifact_sha256,
        }
        with self.store.open_verified_bundle(**arguments) as bundle_path:
            self.assertIsInstance(bundle_path, str)
            self.assertEqual(Path(bundle_path).read_bytes(), self.bundle)
        for field, value in (
            ("owner", "another-controller"),
            ("application", "other-app"),
            ("bindings_sha256", "d" * 64),
            ("artifact_sha256", "e" * 64),
            ("generation", record["generation"] + 1),
        ):
            changed = dict(arguments)
            changed[field] = value
            with self.subTest(field=field):
                with self.assertRaises((ValueError, PermissionError)):
                    with self.store.open_verified_bundle(**changed):
                        self.fail("mismatched artifact identity was opened")
        changed_request = build_request()
        changed_request["components"][0]["commands"] = ["other command"]
        with self.assertRaisesRegex(ValueError, "different inputs"):
            with self.store.open_verified_bundle(**{**arguments, "request": changed_request}):
                self.fail("mismatched request was opened")
        self.assertEqual(receipt["artifact_sha256"], self.artifact_sha256)

    def test_missing_intent_or_initialization_witness_with_retained_bytes_fails_closed(self) -> None:
        missing_intent = self.reserve()
        self.stage(missing_intent)
        (self.staging_root / (missing_intent["operation_id"] + ".intent.json")).unlink()
        with self.assertRaisesRegex(ValueError, "without its durable intent"):
            self.stage(missing_intent, source=None)

        missing_witness = self.reserve()
        witness_root = self.parent / "witness-artifacts"
        witness_root.mkdir(mode=0o700)
        witness_store = BuildTargetArtifactStore(witness_root)
        self.stage(missing_witness, store=witness_store)
        (witness_root / ".build-target-artifacts.initialized").unlink()
        with self.assertRaisesRegex(ValueError, "witness is missing"):
            self.stage(missing_witness, source=None, store=witness_store)

    def test_permissions_symlinks_and_renameable_ancestors_are_rejected(self) -> None:
        public_root = self.parent / "public-root"
        public_root.mkdir(mode=0o755)
        public_root.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "mode 0700"):
            BuildTargetArtifactStore(public_root)

        linked_root = self.parent / "linked-root"
        linked_root.symlink_to(self.staging_root, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            BuildTargetArtifactStore(linked_root)

        unsafe_parent = self.parent / "renameable"
        unsafe_parent.mkdir(mode=0o777)
        unsafe_parent.chmod(0o777)
        unsafe_root = unsafe_parent / "root"
        unsafe_root.mkdir(mode=0o700)
        unsafe_root.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "renameable ancestor"):
            BuildTargetArtifactStore(unsafe_root)

        record = self.reserve()
        self.stage(record)
        bundle_path = self.staging_root / (record["operation_id"] + ".bundle")
        bundle_path.unlink()
        bundle_path.symlink_to(self.bundle_path)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.stage(record, source=None)

    def test_hardlinked_intent_is_rejected(self) -> None:
        record = self.reserve()
        self.stage(record)
        intent_path = self.staging_root / (record["operation_id"] + ".intent.json")
        alias = self.staging_root / ("f" * 32 + ".intent.json")
        os.link(intent_path, alias)
        with self.assertRaisesRegex(ValueError, "unlinked regular file"):
            self.stage(record, source=None)


if __name__ == "__main__":
    unittest.main()
