"""Capacity observations preserve retained evidence and separate quota from disk."""

from __future__ import annotations

import copy
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

from lib import build_capacity
from lib.build_artifacts import MAGIC
from lib.build_inputs import BuildInputStore
from lib.build_jobs import MAX_OPERATIONS, MAX_STORE_BYTES, BuildJobStore
from lib.build_protocol import encode_document
from lib.build_retention import BuildRetentionStore
from tests.test_build_inputs import build_request
from tests.test_build_host_service import build_artifact


class BuildCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.parent.chmod(0o700)
        self.roots = {name: self.parent / name for name in
                      ("inputs", "state", "scratch", "artifacts", "logs")}
        for path in self.roots.values():
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        self.payload = b"private payload contents"
        self.request = build_request("a" * 32, self.payload, command="private-command")
        self.request["limits"].update(scratch_bytes=256 * 1024**2,
                                      artifact_bytes=8192, log_bytes=8192)
        self.reservation = BuildInputStore._reservation(len(self.payload), self.request)
        self.config = {"roots": {key: str(path) for key, path in self.roots.items()},
                       "max_records": 4, "max_bytes": self.reservation + 26 * 1024**2}
        self.inputs = BuildInputStore(self.roots["inputs"], max_records=4,
                                     max_bytes=self.config["max_bytes"])
        self.jobs = BuildJobStore(self.roots["state"] / "jobs.json", clock=lambda: 1000.0)
        for target in ("lib.build_capacity._require_root", "lib.build_retention._require_root"):
            guard = patch(target)
            guard.start()
            self.addCleanup(guard.stop)
        filesystem = patch.object(build_capacity.os, "fstatvfs", return_value=SimpleNamespace(
            f_bavail=8 * 1024**3 // 4096, f_frsize=4096))
        self.filesystem = filesystem.start()
        self.addCleanup(filesystem.stop)

    def admit(self, request=None, *, stream=None):
        self.inputs.admit(request or self.request, owner="private-owner",
                          source=BytesIO(self.payload) if stream is None else stream,
                          source_size=len(self.payload), jobs=self.jobs)

    def retention(self):
        return BuildRetentionStore(inputs=self.inputs, jobs=self.jobs,
                                   scratch_root=str(self.roots["scratch"]),
                                   artifacts_root=str(self.roots["artifacts"]),
                                   logs_root=str(self.roots["logs"]))

    def inspect(self):
        return build_capacity.inspect_build_capacity(MappingProxyType(self.config))

    def snapshot(self):
        return {str(path.relative_to(self.parent)): (
            path.lstat().st_ino, path.lstat().st_mode, path.lstat().st_uid,
            path.lstat().st_nlink, path.lstat().st_size, path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns, path.read_bytes() if path.is_file() else None,
        ) for path in self.parent.rglob("*")}

    def assert_unavailable_unchanged(self):
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "evidence is unavailable or inconsistent") as error:
                self.inspect()
            self.assertNotIn(str(self.parent), str(error.exception))
            self.assertEqual(before, self.snapshot())

    def test_logical_headroom_record_caps_and_shared_filesystem_are_separate(self):
        self.admit()
        self.config["max_records"] = 1
        before_config = copy.deepcopy(self.config)
        report = self.inspect()
        self.assertEqual(self.config, before_config)
        inputs = report["inputs"]
        self.assertEqual(inputs["original_reservation_bytes"], self.reservation)
        self.assertEqual(inputs["current_charge_bytes"], self.reservation)
        self.assertEqual(inputs["byte_headroom"], 26 * 1024**2)
        self.assertEqual((inputs["retained_slots"], inputs["slot_ceiling"], inputs["slot_headroom"]), (1, 1, 0))
        self.assertEqual(inputs["metadata_byte_ceiling"], 4096)
        job = report["jobs"]
        self.assertEqual((job["replay_records"], job["record_headroom"]), (1, MAX_OPERATIONS - 1))
        self.assertEqual(job["metadata_bytes"], (self.roots["state"] / "jobs.json").stat().st_size)
        self.assertEqual(job["metadata_byte_headroom"], MAX_STORE_BYTES - job["metadata_bytes"])
        self.assertEqual(len(report["filesystems"]), 1)
        filesystem = report["filesystems"][0]
        self.assertEqual(set(filesystem["roots"]), set(self.roots))
        self.assertEqual(filesystem["available_bytes"], 8 * 1024**3)
        self.assertFalse((self.roots["state"] / "jobs.json.worker.lock").exists())
        raw = encode_document(report, maximum=build_capacity.MAX_REPORT_BYTES)
        for private in (b"private-owner", b"private-command", b"private-value", self.payload,
                        str(self.parent).encode(), b'"commands"', b'"env"', b'"ready"'):
            self.assertNotIn(private, raw)

    def test_repeated_reads_never_create_write_sync_open_payload_or_expire(self):
        self.admit()
        for root, suffix in (("artifacts", ".bundle"), ("logs", ".log")):
            path = self.roots[root] / (self.request["request_id"] + suffix)
            path.write_bytes(self.payload)
            path.chmod(0o600)
        before = self.snapshot()
        real_open = os.open

        def readonly_open(path, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_RDWR), 0)
            self.assertFalse(str(path).endswith((".source", ".part", ".bundle", ".log")))
            return real_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", side_effect=readonly_open), \
             patch.object(os, "fsync", side_effect=AssertionError("inspection must not sync")), \
             patch.object(os, "pwrite", side_effect=AssertionError("inspection must not write")), \
             patch.object(BuildJobStore, "_locked", side_effect=AssertionError("mutating lock")), \
             patch.object(BuildInputStore, "_locked", side_effect=AssertionError("mutating lock")), \
             patch.object(BuildJobStore, "_expire_queued", side_effect=AssertionError("expiry")), \
             patch.object(BuildJobStore, "status", side_effect=AssertionError("status")), \
             patch("time.time", return_value=10**10):
            for _ in range(2):
                self.assertEqual(self.inspect()["jobs"]["states"]["queued"], 1)
        self.assertEqual(before, self.snapshot())

    def test_settled_completed_and_pending_pruning_keep_persisted_charge_and_slots(self):
        self.admit()
        self.jobs.cancel(self.request["request_id"], owner="private-owner")
        retention = self.retention()
        plan = retention.inspect_retention(self.request["request_id"])
        retention.settle_terminal(self.request["request_id"], expected_revision=plan["revision"])
        before = self.snapshot()
        with patch.object(os, "fsync", side_effect=AssertionError("no durability repair")):
            report = self.inspect()["inputs"]
            self.assertEqual(report["settled"], 1)
            self.assertEqual(report["current_charge_bytes"], plan["charge_bytes"])
        self.assertEqual(before, self.snapshot())
        # Fail after the unlink: pending intent keeps the entire settled charge.
        with patch.object(retention, "_sync_deleted", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                retention.prune_terminal(self.request["request_id"], expected_revision=plan["revision"],
                                         kinds=("source",))
        before = self.snapshot()
        with patch.object(os, "fsync", side_effect=AssertionError("no pending repair")):
            for _ in range(2):
                report = self.inspect()["inputs"]
                self.assertEqual(report["pending_pruning"], 1)
                self.assertEqual(report["current_charge_bytes"], plan["charge_bytes"])
        self.assertEqual(before, self.snapshot())
        completed = retention.prune_terminal(self.request["request_id"], expected_revision=plan["revision"],
                                             kinds=("source",))
        before = self.snapshot()
        with patch.object(os, "fsync", side_effect=AssertionError("no completed repair")):
            report = self.inspect()["inputs"]
        self.assertEqual(before, self.snapshot())
        self.assertEqual(report["pruned"], 1)
        self.assertEqual(report["current_charge_bytes"], completed["current_charge_bytes"])
        self.assertEqual(report["original_reservation_bytes"], self.reservation)
        self.assertEqual(report["retained_slots"], 1)

    def test_uncertain_retains_full_charge_and_active_worker_does_not_block_snapshot(self):
        self.admit()
        with self.jobs.worker() as lease:
            lease.claim()
            self.assertEqual(self.inspect()["jobs"]["states"]["running"], 1)
            lease.finish(self.request["request_id"], outcome="uncertain", work_stopped=True)
        report = self.inspect()
        self.assertEqual(report["jobs"]["states"]["uncertain"], 1)
        self.assertEqual(report["inputs"]["current_charge_bytes"], self.reservation)

    def test_schema4_release_pending_completed_and_exact_job_checks_without_repair(self):
        self.admit()
        identifier = self.request["request_id"]
        artifact = build_artifact(self.request, digest="0" * 64, size=1)
        descriptor = encode_document(artifact["descriptor"])
        bundle = MAGIC + len(descriptor).to_bytes(8, "big") + descriptor
        artifact.update(sha256=hashlib.sha256(bundle).hexdigest(), size=len(bundle))
        for root, suffix, content in (("artifacts", ".bundle", bundle), ("logs", ".log", b"log")):
            path = self.roots[root] / (identifier + suffix)
            path.write_bytes(content)
            path.chmod(0o600)
        with self.jobs.worker() as lease:
            lease.claim()
            lease.finish(identifier, outcome="succeeded", artifact=artifact, work_stopped=True)
        retention = self.retention()
        plan = retention.inspect_retention(identifier)
        retention.settle_terminal(identifier, expected_revision=plan["revision"])
        with patch.object(retention, "_sync_deleted", side_effect=OSError("interrupted sync")):
            with self.assertRaises(OSError):
                retention.release_bundle(identifier, expected_revision=plan["revision"])
        before = self.snapshot()
        with patch.object(os, "fsync", side_effect=AssertionError("no pending repair")):
            report = self.inspect()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(report["inputs"]["pending_pruning"], 1)
        self.assertEqual(report["inputs"]["current_charge_bytes"], plan["charge_bytes"])
        completed = retention.release_bundle(identifier, expected_revision=plan["revision"])
        before = self.snapshot()
        with patch.object(os, "fsync", side_effect=AssertionError("no completed repair")):
            report = self.inspect()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(report["inputs"]["pruned"], 1)
        self.assertEqual(report["inputs"]["current_charge_bytes"], plan["charge_bytes"] - 4096)
        self.assertEqual(report["inputs"]["original_reservation_bytes"], self.reservation)
        self.assertEqual(report["inputs"]["retained_slots"], 1)
        self.assertEqual(report["jobs"]["replay_records"], 1)
        self.assertEqual(report["jobs"]["states"]["succeeded"], 1)
        retention.prune_terminal(identifier, expected_revision=completed["revision"])
        self.assertEqual(self.inspect()["inputs"]["current_charge_bytes"], plan["metadata_charge_bytes"])
        with self.jobs._locked() as (state, directory):
            state["jobs"][identifier]["finished_at"] += 1
            state["last_time"] += 1
            self.jobs._save(directory, state)
        self.assert_unavailable_unchanged()

    def test_partial_input_without_job_is_counted_without_repair(self):
        self.admit()
        second = {**self.request, "request_id": "b" * 32}
        self.inputs.max_bytes = self.config["max_bytes"] = self.reservation * 2
        with self.assertRaises(ValueError):
            self.admit(second, stream=BytesIO(self.payload[:-1]))
        before = self.snapshot()
        report = self.inspect()["inputs"]
        self.assertEqual(report["current_charge_bytes"], self.reservation * 2)
        self.assertEqual(report["inputs_without_jobs"], 1)
        self.assertEqual(report["incomplete_inputs"], 1)
        self.assertEqual(before, self.snapshot())

    def test_input_and_job_contention_refuse_without_mutation_and_release_locks(self):
        self.admit()
        for root, name in (("inputs", ".build-inputs.lock"), ("state", "jobs.json.lock")):
            descriptor = os.open(self.roots[root] / name, os.O_RDONLY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                before = self.snapshot()
                with self.assertRaisesRegex(RuntimeError, "accounting is busy"):
                    self.inspect()
                self.assertEqual(before, self.snapshot())
            finally:
                os.close(descriptor)
            self.assertEqual(self.inspect()["inputs"]["retained_slots"], 1)

    def test_empty_roots_are_not_initialized(self):
        self.assert_unavailable_unchanged()
        self.assertTrue(all(not list(path.iterdir()) for path in self.roots.values()))

    def test_missing_ledger_witnesses_and_malformed_metadata_refuse(self):
        self.admit()
        paths = [self.roots["state"] / "jobs.json", self.roots["state"] / "jobs.json.lock",
                 self.roots["inputs"] / ".build-inputs.lock",
                 self.roots["inputs"] / ".build-inputs.initialized",
                 self.roots["inputs"] / (self.request["request_id"] + ".upload.json")]
        for path in paths:
            original = path.read_bytes()
            with self.subTest(path=path.name):
                path.unlink()
                self.assert_unavailable_unchanged()
                path.write_bytes(original)
                path.chmod(0o600)
                for contents in (b"", b"malformed", original + b"trailing"):
                    path.write_bytes(contents)
                    self.assert_unavailable_unchanged()
                path.write_bytes(original)

    def test_ambiguous_temporary_and_oversized_intent_refuse(self):
        self.admit()
        for root, name in (("inputs", "a" * 32 + ".settlement.tmp"),
                           ("state", ".jobs.json.abandoned.tmp")):
            path = self.roots[root] / name
            path.write_bytes(b"private evidence")
            path.chmod(0o600)
            self.assert_unavailable_unchanged()
            path.unlink()
        path = self.roots["inputs"] / (self.request["request_id"] + ".upload.json")
        path.write_bytes(b"x" * 4097)
        self.assert_unavailable_unchanged()

    def test_unsafe_roots_and_files_refuse(self):
        self.admit()
        for path in (self.roots["scratch"], self.roots["state"] / "jobs.json",
                     self.roots["inputs"] / ".build-inputs.lock"):
            mode = path.stat().st_mode & 0o777
            path.chmod(0o777)
            self.assert_unavailable_unchanged()
            path.chmod(mode)
        # Protected root ancestry applies even to roots with no lock files.
        self.parent.chmod(0o777)
        self.assert_unavailable_unchanged()
        self.parent.chmod(0o700)
        for key in self.roots:
            original = self.config["roots"][key]
            linked = self.parent / "linked"
            linked.symlink_to(original)
            self.config["roots"][key] = str(linked)
            self.assert_unavailable_unchanged()
            self.config["roots"][key] = original
            linked.unlink()
        ledger = self.roots["state"] / "jobs.json"
        alias = self.parent / "ledger-alias"
        os.link(ledger, alias)
        self.assert_unavailable_unchanged()

    def test_filesystem_failure_is_explicit_and_never_changes_logical_headroom(self):
        self.admit()
        self.filesystem.side_effect = OSError("private filesystem diagnostic")
        report = self.inspect()
        self.assertEqual(report["inputs"]["byte_headroom"], 26 * 1024**2)
        filesystem = report["filesystems"][0]
        self.assertIsNone(filesystem["available_bytes"])
        self.assertEqual(filesystem["observations"], ["available_bytes_unavailable"])
        self.assertNotIn("private filesystem diagnostic", json.dumps(report))

    def test_filesystem_variation_and_invalid_values_are_explicit(self):
        self.admit()
        self.filesystem.side_effect = [SimpleNamespace(f_bavail=value, f_frsize=4096)
                                      for value in (10, 8, 9, 8, 10)]
        filesystem = self.inspect()["filesystems"][0]
        self.assertEqual(filesystem["available_bytes"], 8 * 4096)
        self.assertEqual(filesystem["observations"], ["available_bytes_changed_between_roots"])
        self.filesystem.side_effect = None
        for blocks, size in ((-1, 4096), (True, 4096), (1, 0)):
            self.filesystem.return_value = SimpleNamespace(f_bavail=blocks, f_frsize=size)
            self.assertIsNone(self.inspect()["filesystems"][0]["available_bytes"])

    def test_job_record_and_metadata_caps_are_independent_of_disk(self):
        self.admit()
        # Lower the real validator's record ceiling instead of creating 4096 jobs.
        self.inputs.max_records = self.config["max_records"] = 8
        self.inputs.max_bytes = self.config["max_bytes"] = self.reservation * 8
        for number in range(2, 9):
            self.admit({**self.request, "request_id": f"{number:032x}"})
        with patch("lib.build_jobs.MAX_OPERATIONS", 8), patch.object(build_capacity, "MAX_OPERATIONS", 8):
            jobs = self.inspect()["jobs"]
            self.assertEqual((jobs["replay_records"], jobs["record_ceiling"], jobs["record_headroom"]), (8, 8, 0))
        ledger_bytes = (self.roots["state"] / "jobs.json").stat().st_size
        with patch("lib.build_jobs.MAX_STORE_BYTES", ledger_bytes), \
             patch.object(build_capacity, "MAX_STORE_BYTES", ledger_bytes):
            self.assertEqual(self.inspect()["jobs"]["metadata_byte_headroom"], 0)
        with patch("lib.build_jobs.MAX_STORE_BYTES", ledger_bytes - 1):
            self.assert_unavailable_unchanged()
        self.config["max_bytes"] = self.reservation * 8 - 1
        self.assert_unavailable_unchanged()
        self.config["max_bytes"] = self.reservation * 8
        self.assertEqual(self.inspect()["inputs"]["byte_headroom"], 0)

    def test_missing_submitted_source_or_changed_identity_refuses(self):
        self.admit()
        source = self.roots["inputs"] / (self.request["request_id"] + ".source")
        original = source.read_bytes()
        source.unlink()
        self.assert_unavailable_unchanged()
        source.write_bytes(original)
        source.chmod(0o400)
        intent = self.roots["inputs"] / (self.request["request_id"] + ".upload.json")
        original = intent.read_bytes()
        record = json.loads(original)
        for field, value in (("owner", "changed-owner"), ("request_sha256", "f" * 64),
                             ("reservation_bytes", self.reservation - 1)):
            intent.write_bytes(encode_document({**record, field: value}))
            self.assert_unavailable_unchanged()
        intent.write_bytes(original)
        # Removing a job from an otherwise valid ledger cannot erase established
        # terminal evidence after settlement.
        self.jobs.cancel(self.request["request_id"], owner="private-owner")
        retention = self.retention()
        plan = retention.inspect_retention(self.request["request_id"])
        retention.settle_terminal(self.request["request_id"], expected_revision=plan["revision"])
        ledger = self.roots["state"] / "jobs.json"
        state = json.loads(ledger.read_bytes())
        state.update(jobs={}, last_sequence=0)
        ledger.write_bytes(encode_document(state) + b"\n")
        self.assert_unavailable_unchanged()

    def test_output_bound_and_root_overlap_fail_without_mutation(self):
        self.admit()
        with patch.object(build_capacity, "MAX_REPORT_BYTES", 32):
            self.assert_unavailable_unchanged()
        for value in (self.config["roots"]["inputs"], self.config["roots"]["inputs"] + "/child"):
            self.config["roots"]["scratch"] = value
            self.assert_unavailable_unchanged()

    def test_root_authority_precedes_file_access(self):
        with patch.object(build_capacity, "_require_root", side_effect=PermissionError("root")), \
             patch.object(os, "open") as opened:
            with self.assertRaises(PermissionError):
                self.inspect()
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
