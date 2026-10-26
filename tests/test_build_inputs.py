from __future__ import annotations

from io import BytesIO
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from lib.build_inputs import BuildInputStore
from lib.build_jobs import BuildJobStore
from lib.build_protocol import DEFAULT_LIMITS, MAX_DESCRIPTOR_BYTES
from lib.build_source import DEFAULT_SOURCE_LIMITS


def build_request(request_id: str, payload: bytes, *, command: str = "build --release") -> dict:
    return {
        "schema": 1,
        "request_id": request_id,
        "project": "sample",
        "source": {
            "revision": "1" * 40,
            "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
            "manifest_sha256": "3" * 64,
        },
        "platform": {"os": "debian", "architecture": "x86_64", "baseline": "trixie"},
        "components": [{
            "name": "site",
            "type": "static",
            "artifacts": ["dist"],
            "commands": [command],
            "env": {"BUILD_MODE": "private-value"},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


class InterruptingStream(BytesIO):
    def read(self, size: int = -1) -> bytes:
        if self.tell():
            raise KeyboardInterrupt
        return super().read(min(size, 2))


class CountingStream(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return super().read(size)


class BuildInputStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        os.chmod(self.inputs, 0o700)
        self.store = BuildInputStore(self.inputs)
        self.jobs = BuildJobStore(self.root / "jobs.json")
        self.payload = b"pinned source snapshot bytes\n"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, request_id: str = "a" * 32, *, command: str = "build --release") -> dict:
        return build_request(request_id, self.payload, command=command)

    def admit(self, request: dict | None = None, *, owner: str = "controller", source=None):
        request = request or self.request()
        stream = BytesIO(self.payload) if source is None else source
        return self.store.admit(
            request,
            owner=owner,
            source=stream,
            source_size=len(self.payload),
            jobs=self.jobs,
        )

    def test_admission_publishes_worker_input_and_replays_only_identical_owner_and_request(self) -> None:
        request = self.request()
        first = self.admit(request)
        source_path = self.inputs / (request["request_id"] + ".source")
        intent_path = self.inputs / (request["request_id"] + ".upload.json")
        self.assertEqual(source_path.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(source_path.stat().st_mode), 0o400)
        self.assertEqual(source_path.stat().st_nlink, 1)
        self.assertEqual(stat.S_IMODE(intent_path.stat().st_mode), 0o600)

        intent = json.loads(intent_path.read_text())
        self.assertEqual(intent["owner"], "controller")
        self.assertEqual(intent["source_size"], len(self.payload))
        self.assertEqual(intent["source_sha256"], request["source"]["snapshot_sha256"])
        self.assertEqual(
            intent["reservation_bytes"],
            len(self.payload) + request["limits"]["scratch_bytes"]
            + request["limits"]["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024
            + request["limits"]["log_bytes"],
        )
        encoded = intent_path.read_text()
        self.assertNotIn("commands", encoded)
        self.assertNotIn("BUILD_MODE", encoded)
        self.assertNotIn("private-value", encoded)

        replay_stream = CountingStream(self.payload)
        replay = self.admit(request, source=replay_stream)
        self.assertEqual(replay, first)
        self.assertGreater(replay_stream.read_calls, 0)
        no_stream_replay = self.store.admit(
            request, owner="controller", source=None,
            source_size=len(self.payload), jobs=self.jobs,
        )
        self.assertEqual(no_stream_replay, first)
        self.assertEqual(self.jobs.status(request["request_id"], owner="controller"), first)

        changed = self.request(command="build --different")
        untouched = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.admit(changed, source=untouched)
        self.assertEqual(untouched.read_calls, 0)

        wrong_owner = CountingStream(self.payload)
        with self.assertRaises(PermissionError):
            self.admit(request, owner="another-controller", source=wrong_owner)
        self.assertEqual(wrong_owner.read_calls, 0)

        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.store.admit(
                request, owner="controller", source=BytesIO(self.payload),
                source_size=len(self.payload) + 1, jobs=self.jobs,
            )

    def test_lost_job_reply_replays_published_source_and_idempotent_queue(self) -> None:
        request = self.request()
        real_submit = self.jobs.submit
        calls = 0

        def submit_then_lose_reply(value, *, owner):
            nonlocal calls
            calls += 1
            result = real_submit(value, owner=owner)
            if calls == 1:
                raise OSError("simulated lost queue reply")
            return result

        with patch.object(self.jobs, "submit", side_effect=submit_then_lose_reply):
            with self.assertRaisesRegex(OSError, "lost queue reply"):
                self.admit(request)
            replay = self.store.admit(
                request,
                owner="controller",
                source=BytesIO(self.payload),
                source_size=len(self.payload),
                jobs=self.jobs,
            )

        self.assertEqual(replay["request_id"], request["request_id"])
        self.assertEqual(replay["sequence"], 1)
        self.assertEqual(len(json.loads((self.root / "jobs.json").read_text())["jobs"]), 1)

    def test_full_reservation_refuses_before_reading_stream(self) -> None:
        store = BuildInputStore(self.inputs, max_bytes=1)
        stream = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            store.admit(
                self.request("b" * 32), owner="controller", source=stream,
                source_size=len(self.payload), jobs=self.jobs,
            )
        self.assertEqual(stream.read_calls, 0)
        self.assertFalse((self.inputs / ("b" * 32 + ".upload.json")).exists())
        self.assertFalse((self.inputs / ("b" * 32 + ".part")).exists())

        oversized = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "Source size"):
            store.admit(
                self.request("c" * 32), owner="controller", source=oversized,
                source_size=DEFAULT_SOURCE_LIMITS.max_snapshot_bytes + 1, jobs=self.jobs,
            )
        self.assertEqual(oversized.read_calls, 0)

    def test_retained_record_count_refuses_before_reading_stream(self) -> None:
        store = BuildInputStore(self.inputs, max_records=1)
        first = self.request("a" * 32)
        store.admit(
            first, owner="controller", source=BytesIO(self.payload),
            source_size=len(self.payload), jobs=self.jobs,
        )
        second = self.request("b" * 32)
        stream = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "retained evidence cannot be pruned"):
            store.admit(
                second, owner="controller", source=stream,
                source_size=len(self.payload), jobs=self.jobs,
            )
        self.assertEqual(stream.read_calls, 0)

    def test_truncated_wrong_digest_and_trailing_streams_retain_fail_closed_intents(self) -> None:
        cases = (
            ("b" * 32, self.request("b" * 32), self.payload[:-1], len(self.payload), "ended"),
            ("d" * 32, self.request("d" * 32), self.payload + b"x", len(self.payload), "trailing"),
        )
        wrong_digest_request = self.request("c" * 32)
        wrong_digest_request["source"]["snapshot_sha256"] = "f" * 64
        cases = (
            cases[0],
            ("c" * 32, wrong_digest_request, self.payload, len(self.payload), "pinned source digest"),
            cases[1],
        )

        for request_id, request, stream_bytes, size, message in cases:
            with self.subTest(request_id=request_id):
                with self.assertRaisesRegex(ValueError, message):
                    self.store.admit(
                        request, owner="controller", source=BytesIO(stream_bytes),
                        source_size=size, jobs=self.jobs,
                    )
                self.assertTrue((self.inputs / (request_id + ".upload.json")).exists())
                self.assertTrue((self.inputs / (request_id + ".part")).exists())
                self.assertFalse((self.inputs / (request_id + ".source")).exists())
                retry = CountingStream(self.payload)
                with self.assertRaisesRegex(ValueError, "Incomplete build input intent"):
                    self.store.admit(
                        request, owner="controller", source=retry,
                        source_size=len(self.payload), jobs=self.jobs,
                    )
                self.assertEqual(retry.read_calls, 0)

    def test_stream_interruption_and_part_fsync_failure_preserve_intent_and_partial_evidence(self) -> None:
        request = self.request()
        with self.assertRaises(KeyboardInterrupt):
            self.store.admit(
                request, owner="controller", source=InterruptingStream(self.payload),
                source_size=len(self.payload), jobs=self.jobs,
            )
        partial = self.inputs / (request["request_id"] + ".part")
        intent = self.inputs / (request["request_id"] + ".upload.json")
        self.assertTrue(intent.exists())
        self.assertTrue(partial.exists())
        self.assertEqual(partial.stat().st_size, 2)
        with self.assertRaisesRegex(ValueError, "Incomplete build input intent"):
            self.admit(request)

        second_id = "e" * 32
        second = self.request(second_id)
        with patch.object(self.store, "_sync_part", side_effect=OSError("simulated fsync failure")):
            with self.assertRaisesRegex(OSError, "fsync failure"):
                self.admit(second)
        self.assertTrue((self.inputs / (second_id + ".upload.json")).exists())
        self.assertTrue((self.inputs / (second_id + ".part")).exists())
        self.assertFalse((self.inputs / (second_id + ".source")).exists())

    def test_published_source_recovers_after_directory_sync_failure_without_overwrite(self) -> None:
        request = self.request()
        source_path = self.inputs / (request["request_id"] + ".source")
        with patch.object(self.store, "_sync_after_rename", side_effect=OSError("directory sync failed")):
            with self.assertRaisesRegex(OSError, "directory sync failed"):
                self.admit(request)
        original = (source_path.stat().st_dev, source_path.stat().st_ino)
        self.assertTrue(source_path.exists())
        self.assertFalse((self.inputs / (request["request_id"] + ".part")).exists())

        replay = self.store.admit(
            request, owner="controller", source=BytesIO(self.payload),
            source_size=len(self.payload), jobs=self.jobs,
        )
        self.assertEqual(replay["state"], "queued")
        current = (source_path.stat().st_dev, source_path.stat().st_ino)
        self.assertEqual(current, original)

    def test_stable_lock_survives_reopen_and_excludes_a_second_open_file_description(self) -> None:
        request = self.request()
        self.admit(request)
        lock_path = self.inputs / ".build-inputs.lock"
        original = (lock_path.stat().st_dev, lock_path.stat().st_ino)
        reopened = BuildInputStore(self.inputs)
        replay = reopened.admit(
            request, owner="controller", source=None,
            source_size=len(self.payload), jobs=self.jobs,
        )
        self.assertEqual(replay["state"], "queued")
        self.assertEqual((lock_path.stat().st_dev, lock_path.stat().st_ino), original)

        first_fd = os.open(lock_path, os.O_RDWR)
        second_fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(first_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(second_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            fcntl.flock(first_fd, fcntl.LOCK_UN)
            os.close(first_fd)
            os.close(second_fd)

    def test_symlink_hardlink_and_renameable_ancestor_are_rejected(self) -> None:
        linked_root = self.root / "inputs-link"
        linked_root.symlink_to(self.inputs, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            BuildInputStore(linked_root)

        request = self.request()
        self.admit(request)
        intent_path = self.inputs / (request["request_id"] + ".upload.json")
        alias = self.inputs / ("f" * 32 + ".upload.json")
        os.link(intent_path, alias)
        with self.assertRaisesRegex(ValueError, "unlinked regular file"):
            self.store.admit(
                self.request("f" * 32), owner="controller", source=BytesIO(self.payload),
                source_size=len(self.payload), jobs=self.jobs,
            )

        unsafe_parent = self.root / "renameable"
        unsafe_parent.mkdir(mode=0o777)
        os.chmod(unsafe_parent, 0o777)
        unsafe_root = unsafe_parent / "inputs"
        unsafe_root.mkdir(mode=0o700)
        os.chmod(unsafe_root, 0o700)
        with self.assertRaisesRegex(ValueError, "renameable ancestor"):
            BuildInputStore(unsafe_root)


if __name__ == "__main__":
    unittest.main()
