"""Terminal settlement preserves bytes and replay while releasing unused charge."""

from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import lib.build_inputs as build_inputs
import lib.build_retention as build_retention
from lib.build_artifacts import MAGIC
from lib.build_inputs import BuildInputStore
from lib.build_jobs import BuildJobStore
from lib.build_protocol import MAX_DESCRIPTOR_BYTES, encode_document, request_digest
from lib.build_retention import BuildRetentionStore, FILE_CHARGE_FLOOR
from tests.test_build_inputs import build_request


class BuildRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.roots = {name: self.root / name for name in
                      ("inputs", "state", "scratch", "artifacts", "logs")}
        for path in self.roots.values():
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        # Mock only privilege authorization; real temporary-file ownership,
        # mode, no-follow, locks and atomic persistence checks remain active.
        self.root_guard = patch.object(build_retention, "_require_root")
        self.root_guard.start()
        self.addCleanup(self.root_guard.stop)
        self.payload = b"immutable-source"
        self.request = build_request("a" * 32, self.payload)
        self.request["limits"].update(scratch_bytes=256 * 1024**2, artifact_bytes=8192, log_bytes=8192)
        self.reservation = BuildInputStore._reservation(len(self.payload), self.request)
        self.inputs = BuildInputStore(self.roots["inputs"], max_bytes=self.reservation + 65536)
        self.jobs = BuildJobStore(self.roots["state"] / "jobs.json", clock=lambda: 1000.0)
        self.retention = self.reopen()

    def reopen(self):
        return BuildRetentionStore(inputs=self.inputs, jobs=self.jobs,
                                   scratch_root=str(self.roots["scratch"]),
                                   artifacts_root=str(self.roots["artifacts"]),
                                   logs_root=str(self.roots["logs"]))

    def admit(self, request=None):
        return self.inputs.admit(request or self.request, owner="controller", source=BytesIO(self.payload),
                                 source_size=len(self.payload), jobs=self.jobs)

    def path(self, root, suffix):
        return self.roots[root] / (self.request["request_id"] + suffix)

    def intent(self):
        return json.loads(self.path("inputs", ".upload.json").read_bytes())

    def write(self, root, suffix, payload, mode=0o600):
        path = self.path(root, suffix)
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def bundle(self, payload=b"output"):
        descriptor = {
            "schema": 1, "request_id": self.request["request_id"], "project": self.request["project"],
            "request_sha256": request_digest(self.request), "source": self.request["source"],
            "platform": self.request["platform"], "components": [
                {key: item[key] for key in ("name", "type", "artifacts")}
                for item in self.request["components"]], "toolchains": {},
            "entries": [{"path": "dist", "kind": "directory", "size": 0,
                         "sha256": "", "executable": False},
                        {"path": "dist/index.html", "kind": "file", "size": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest(), "executable": False}],
        }
        raw = encode_document(descriptor, maximum=MAX_DESCRIPTOR_BYTES)
        content = MAGIC + len(raw).to_bytes(8, "big") + raw + payload
        self.write("artifacts", ".bundle", content)
        return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
                "descriptor": descriptor}

    def terminal(self, outcome="cancelled", *, orphan=False):
        self.admit()
        if outcome == "cancelled" and not orphan:
            return self.jobs.cancel(self.request["request_id"], owner="controller")
        with self.jobs.worker() as lease:
            lease.claim()
            artifact = self.bundle() if outcome == "succeeded" or orphan else None
            self.write("logs", ".log", b"private build log")
            if outcome == "cancelled":
                self.jobs.cancel(self.request["request_id"], owner="controller")
            return lease.finish(self.request["request_id"], outcome=outcome, work_stopped=True,
                                artifact=artifact if outcome == "succeeded" else None)

    def inspect(self):
        return self.retention.inspect_retention(self.request["request_id"])

    def settle(self, plan=None):
        return self.retention.settle_terminal(self.request["request_id"],
                                             expected_revision=(plan or self.inspect())["revision"])

    def test_explicit_settlement_frees_capacity_preserves_all_bytes_and_replay_after_reopen(self):
        terminal = self.terminal("succeeded")
        original = self.intent()
        before = {path: (path.read_bytes(), path.stat().st_ino) for root in ("inputs", "artifacts", "logs")
                  for path in self.roots[root].iterdir() if path.suffix != ".json"}
        second = copy.deepcopy(self.request)
        second["request_id"] = "b" * 32
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "lacks capacity"):
                self.admit(second)
            self.inspect()
            self.assertEqual(self.intent(), original)
        plan = self.inspect()
        self.assertLess(plan["charge_bytes"], self.reservation)
        self.assertFalse(plan["settled"])
        self.assertNotIn("private", json.dumps(plan))
        self.assertNotIn("commands", json.dumps(plan))
        receipt = self.settle(plan)
        self.assertTrue(receipt["settled"])
        self.assertEqual(self.intent()["reservation_bytes"], self.reservation)
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])
        self.assertEqual(self.intent()["settlement_revision"], plan["revision"])
        for key, value in original.items():
            if key != "schema":
                self.assertEqual(self.intent()[key], value)
        for path, identity in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino), identity)
        self.inputs = BuildInputStore(self.roots["inputs"], max_bytes=self.reservation + 65536)
        self.jobs = BuildJobStore(self.roots["state"] / "jobs.json", clock=lambda: 1000.0)
        self.retention = self.reopen()
        self.assertEqual(self.settle(plan), receipt)
        self.assertEqual(self.inputs.admit(self.request, owner="controller", source=None,
                                          source_size=len(self.payload), jobs=self.jobs), terminal)
        self.assertEqual(self.admit(), terminal)
        self.assertEqual(self.admit(second)["sequence"], 2)
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            third = {**second, "request_id": "c" * 32}
            self.admit(third)
        changed = copy.deepcopy(self.request)
        changed["components"][0]["commands"] = ["other"]
        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.admit(changed)
        with self.assertRaises(PermissionError):
            self.inputs.admit(self.request, owner="other", source=None,
                              source_size=len(self.payload), jobs=self.jobs)

    def test_cancelled_and_failed_orphan_bundle_descriptor_and_logs_are_charged(self):
        for outcome in ("cancelled", "failed"):
            with self.subTest(outcome=outcome):
                if outcome == "failed":
                    self.request = {**self.request, "request_id": "b" * 32}
                self.terminal(outcome, orphan=True)
                plan = self.inspect()
                self.assertIsNone(self.jobs.status(self.request["request_id"], owner="controller")["artifact"])
                self.assertGreater(plan["payloads"]["bundle"]["descriptor_bytes"], 0)
                self.assertEqual(plan["charge_bytes"], 9 * FILE_CHARGE_FLOOR)
                self.settle(plan)
                self.assertTrue(self.path("artifacts", ".bundle").exists())

    def test_zero_byte_log_and_metadata_have_conservative_nonzero_charge(self):
        self.terminal()
        self.write("logs", ".log", b"")
        plan = self.inspect()
        self.assertEqual(plan["metadata_charge_bytes"], 6 * FILE_CHARGE_FLOOR)
        self.assertEqual(plan["charge_bytes"], 8 * FILE_CHARGE_FLOOR)
        self.assertEqual(plan["payloads"]["log"]["size"], 0)
        self.settle(plan)

    def test_eight_terminal_reservations_fill_logical_quota_without_large_payloads(self):
        self.inputs.max_bytes = 8 * self.reservation + 65536
        for number in range(1, 9):
            self.request = {**self.request, "request_id": f"{number:032x}"}
            self.terminal()
        ninth = {**self.request, "request_id": f"{9:032x}"}
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.admit(ninth)
        self.assertLess(sum(path.stat().st_size for root in self.roots.values()
                            for path in root.iterdir()), 65536)
        plan = self.inspect()
        self.settle(plan)
        self.assertEqual(self.admit(ninth)["sequence"], 9)
        self.settle(plan)
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.admit({**ninth, "request_id": f"{10:032x}"})

    def test_expired_job_can_settle_without_expiring_or_mutating_jobs_during_inspect(self):
        self.admit()
        self.jobs._clock = lambda: 2000.0
        status = self.jobs.status(self.request["request_id"], owner="controller")
        self.assertEqual(status["state"], "expired")
        ledger = self.roots["state"] / "jobs.json"
        before = ledger.read_bytes()
        with patch.object(self.jobs, "_clock", side_effect=AssertionError("maintenance changed clock")):
            plan = self.inspect()
            self.settle(plan)
        self.assertEqual(ledger.read_bytes(), before)
        self.assertEqual(self.inputs.admit(self.request, owner="controller", source=None,
                                          source_size=len(self.payload), jobs=self.jobs), status)

    def test_private_job_reader_requires_its_own_live_lease_and_detaches_evidence(self):
        self.terminal()
        other = BuildJobStore(self.roots["state"] / "jobs.json")
        with self.jobs.worker() as lease:
            record = self.jobs.read_for_retention(self.request["request_id"], lease=lease)
            record["request"]["components"].clear()
            self.assertTrue(self.jobs.read_for_retention(self.request["request_id"], lease=lease)
                            ["request"]["components"])
            with self.assertRaises(PermissionError):
                other.read_for_retention(self.request["request_id"], lease=lease)
        with self.assertRaises(PermissionError):
            self.jobs.read_for_retention(self.request["request_id"], lease=lease)

    def test_queued_running_uncertain_are_refused_and_worker_slot_is_nonblocking(self):
        self.admit()
        with self.assertRaisesRegex(ValueError, "certain terminal"):
            self.inspect()
        with self.jobs.worker() as lease:
            with self.assertRaisesRegex(RuntimeError, "already leased"):
                self.inspect()
            lease.claim()
        with self.assertRaisesRegex(ValueError, "certain terminal"):
            self.inspect()
        with self.jobs.worker() as lease:
            lease.recover_interrupted(self.request["request_id"], cleanup=lambda *_: True)
        with self.assertRaisesRegex(ValueError, "certain terminal"):
            self.inspect()
        self.assertEqual(self.intent()["schema"], 1)

    def test_partial_unsubmitted_and_scratch_evidence_are_refused(self):
        with patch.object(self.jobs, "submit", side_effect=OSError("lost submit")):
            with self.assertRaises(OSError):
                self.admit()
        with self.assertRaisesRegex(ValueError, "not found"):
            self.inspect()
        self.admit()
        self.jobs.cancel(self.request["request_id"], owner="controller")
        scratch = self.path("scratch", "")
        scratch.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "scratch evidence"):
            self.inspect()
        scratch.rmdir()
        source = self.path("inputs", ".source")
        source.rename(self.path("inputs", ".part"))
        with self.assertRaisesRegex(ValueError, "complete published"):
            self.inspect()
        self.assertEqual(self.intent()["schema"], 1)

    def test_stale_plan_on_added_log_inode_or_digest_change_never_releases_charge(self):
        self.terminal()
        plan = self.inspect()
        log = self.write("logs", ".log", b"one")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.settle(plan)
        plan = self.inspect()
        replacement = self.roots["logs"] / "replacement"
        replacement.write_bytes(b"one")
        replacement.chmod(0o600)
        replacement.replace(log)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.settle(plan)
        plan = self.inspect()
        log.write_bytes(b"two")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.settle(plan)
        self.assertEqual(self.intent()["schema"], 1)

    def test_source_and_successful_artifact_digest_mismatches_are_refused(self):
        self.terminal("succeeded")
        plan = self.inspect()
        bundle = self.path("artifacts", ".bundle")
        raw = bundle.read_bytes()
        bundle.write_bytes(raw[:-1] + b"X")
        with self.assertRaisesRegex(ValueError, "inventory digest"):
            self.settle(plan)
        bundle.write_bytes(raw)
        source = self.path("inputs", ".source")
        source.chmod(0o600)
        source.write_bytes(b"X" * len(self.payload))
        source.chmod(0o400)
        with self.assertRaisesRegex(ValueError, "immutable input"):
            self.inspect()
        self.assertEqual(self.intent()["schema"], 1)

    def test_wrong_succeeded_bundle_and_descriptor_identity_are_refused(self):
        self.terminal("succeeded")
        self.bundle(b"other-valid-output")
        with self.assertRaisesRegex(ValueError, "succeeded artifact identity"):
            self.inspect()
        original = self.request
        self.request = copy.deepcopy(original)
        self.request["project"] = "other"
        self.bundle()
        self.request = original
        with self.assertRaisesRegex(ValueError, "identity does not match"):
            self.inspect()

    def test_ambiguous_publication_evidence_is_not_traversed_or_ignored(self):
        self.terminal()
        unowned = self.roots["artifacts"] / ".artifact-ambiguous"
        unowned.mkdir(mode=0o700)
        (unowned / "do-not-touch").write_bytes(b"evidence")
        with self.assertRaisesRegex(ValueError, "ambiguous evidence"):
            self.inspect()
        self.assertEqual((unowned / "do-not-touch").read_bytes(), b"evidence")
        unowned.rename(self.root / "protected-evidence")
        unmatched = self.roots["artifacts"] / ("f" * 32 + ".bundle")
        unmatched.write_bytes(b"not attributed to an input")
        unmatched.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "ambiguous evidence"):
            self.inspect()

    def test_missing_success_payloads_oversized_logs_and_malformed_bundles_are_refused(self):
        self.terminal("succeeded")
        bundle = self.path("artifacts", ".bundle")
        log = self.path("logs", ".log")
        for path in (bundle, log):
            hidden = self.root / path.name
            path.rename(hidden)
            with self.subTest(missing=path.name), self.assertRaisesRegex(ValueError, "succeeded artifact"):
                self.inspect()
            hidden.rename(path)
        log.write_bytes(b"x" * (self.request["limits"]["log_bytes"] + 1))
        with self.assertRaisesRegex(ValueError, "reserved size"):
            self.inspect()
        log.write_bytes(b"")
        raw = bundle.read_bytes()
        for content, message in ((b"bad", "truncated"),
                                 (b"x" * len(MAGIC), "format"),
                                 (MAGIC + (MAX_DESCRIPTOR_BYTES + 1).to_bytes(8, "big"), "bound"),
                                 (raw + b"extra", "trailing")):
            bundle.write_bytes(content)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.inspect()


    def test_owner_and_original_reservation_must_match_job(self):
        self.terminal()
        path = self.path("inputs", ".upload.json")
        original = self.intent()
        for key, value in (("owner", "different-owner"), ("request_sha256", "f" * 64),
                           ("source_sha256", "e" * 64), ("reservation_bytes", self.reservation - 1)):
            path.write_bytes(encode_document({**original, key: value}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identities do not match"):
                self.inspect()
        path.write_bytes(encode_document(original))
        real_stat = build_retention.os.stat

        def wrong_owner(name, *args, **kwargs):
            info = real_stat(name, *args, **kwargs)
            if name == self.request["request_id"] + ".source":
                values = list(info)
                values[4] = info.st_uid + 1
                return os.stat_result(values)
            return info

        with patch.object(build_retention.os, "stat", side_effect=wrong_owner):
            with self.assertRaisesRegex(ValueError, "owned, unlinked regular"):
                self.inspect()

    def test_link_modes_special_files_and_path_inputs_fail_closed(self):
        self.terminal()
        log = self.write("logs", ".log", b"log")
        for mode in (0o644, 0o400):
            log.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.inspect()
        log.chmod(0o600)
        alias = self.root / "alias"
        os.link(log, alias)
        with self.assertRaises(ValueError):
            self.inspect()
        alias.unlink()
        log.unlink()
        log.symlink_to(self.path("inputs", ".source"))
        with self.assertRaises(ValueError):
            self.inspect()
        log.unlink()
        os.mkfifo(log, 0o600)
        with self.assertRaises(ValueError):
            self.inspect()
        for request_id in ("../outside", "A" * 32, "a" * 31, "a" * 32 + "\n"):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                self.retention.inspect_retention(request_id)
        for revision in (None, "x" * 64, "a" * 64 + "\n"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.retention.settle_terminal(self.request["request_id"], expected_revision=revision)

    def test_failed_save_does_not_release_or_double_credit_and_ambiguous_reply_reconciles(self):
        self.terminal()
        plan = self.inspect()
        original = self.path("inputs", ".upload.json").read_bytes()
        with patch.object(build_inputs.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.settle(plan)
        self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
        self.assertFalse(self.path("inputs", ".settlement.tmp").exists())
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.admit({**self.request, "request_id": "b" * 32})
        real_save = self.inputs._save_settlement

        def lose_reply(*args):
            real_save(*args)
            raise OSError("reply lost")

        with patch.object(self.inputs, "_save_settlement", side_effect=lose_reply):
            with self.assertRaisesRegex(OSError, "reply lost"):
                self.settle(plan)
        self.retention = self.reopen()
        saved = self.intent()
        receipt = self.settle(plan)
        self.assertEqual(receipt["charge_bytes"], saved["charge_bytes"])
        self.assertEqual(self.settle(plan), receipt)
        self.assertEqual(self.intent(), saved)

    def test_directory_sync_ambiguity_requires_sync_before_charge_can_be_used(self):
        self.terminal()
        plan = self.inspect()
        real_save = self.inputs._save_settlement
        real_fsync = os.fsync

        def save_with_failed_directory_sync(root_fd, record):
            def sync(descriptor):
                if descriptor == root_fd:
                    raise OSError("directory sync lost")
                real_fsync(descriptor)
            with patch.object(build_inputs.os, "fsync", side_effect=sync):
                real_save(root_fd, record)

        with patch.object(self.inputs, "_save_settlement", side_effect=save_with_failed_directory_sync):
            with self.assertRaisesRegex(OSError, "directory sync lost"):
                self.settle(plan)
        self.assertEqual(self.intent()["schema"], 2)
        with patch.object(build_inputs.os, "fsync", side_effect=OSError("still unavailable")):
            with self.assertRaises(OSError):
                self.admit({**self.request, "request_id": "b" * 32})
        self.assertTrue(self.settle(plan)["settled"])
        self.assertEqual(self.admit({**self.request, "request_id": "b" * 32})["sequence"], 2)

    def test_exact_intent_and_aggregate_metadata_caps_are_enforced_before_save(self):
        self.terminal()
        plan = self.inspect()
        original = self.path("inputs", ".upload.json").read_bytes()
        settled = {**self.intent(), "schema": 2, "charge_bytes": plan["charge_bytes"],
                   "settlement_revision": plan["revision"]}
        size = len(encode_document(settled))
        with patch.object(build_retention, "MAX_INTENT_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.settle(plan)
        with patch.object(build_retention, "MAX_RETAINED_METADATA_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "metadata bound"):
                self.settle(plan)
        self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
        with patch.object(build_retention, "MAX_INTENT_BYTES", size), \
             patch.object(build_retention, "MAX_RETAINED_METADATA_BYTES", size):
            self.settle(plan)
        self.assertEqual(self.path("inputs", ".upload.json").stat().st_size, size)
        self.assertLessEqual(size, build_inputs.MAX_INTENT_BYTES)

    def test_settled_schema_rejects_invalid_charges_revisions_and_unknown_fields(self):
        self.terminal()
        self.settle()
        original = self.intent()
        for key, value in (("charge_bytes", 0), ("charge_bytes", True),
                           ("charge_bytes", self.reservation + 1),
                           ("charge_bytes", len(self.payload)),
                           ("settlement_revision", "f" * 63), ("extra", 0)):
            self.path("inputs", ".upload.json").write_bytes(encode_document({**original, key: value}))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.inspect()

    def test_record_and_job_history_limits_still_refuse_after_settlement(self):
        self.terminal()
        self.settle()
        second = {**self.request, "request_id": "b" * 32}
        self.inputs.max_records = 1
        with self.assertRaisesRegex(ValueError, "cannot be pruned"):
            self.admit(second)
        self.inputs.max_records = 128
        with patch("lib.build_jobs.MAX_OPERATIONS", 1):
            with self.assertRaisesRegex(ValueError, "cannot be pruned"):
                self.admit(second)
        self.assertTrue((self.roots["inputs"] / ("b" * 32 + ".source")).exists())
        self.assertEqual(len(json.loads((self.roots["state"] / "jobs.json").read_bytes())["jobs"]), 1)

    def test_drain_and_status_remain_available_during_payload_verification(self):
        terminal = self.terminal()
        entered = threading.Event()
        resume = threading.Event()
        errors = []
        real_payload = build_retention._payload

        def pause(*args, **kwargs):
            entered.set()
            if not resume.wait(5):
                raise RuntimeError("test synchronization timed out")
            return real_payload(*args, **kwargs)

        def inspect():
            try:
                self.inspect()
            except BaseException as error:
                errors.append(error)

        with patch.object(build_retention, "_payload", side_effect=pause):
            thread = threading.Thread(target=inspect)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.jobs.set_draining(True))
                self.assertEqual(self.jobs.status(self.request["request_id"], owner="controller"), terminal)
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.settle()
        self.assertTrue(json.loads((self.roots["state"] / "jobs.json").read_bytes())["draining"])

    def test_mid_verification_file_changes_and_post_settlement_changes_are_refused(self):
        self.terminal()
        log = self.write("logs", ".log", b"before")
        real_recheck = self.retention._recheck

        def change_then_check(*args):
            log.write_bytes(b"after")
            return real_recheck(*args)

        with patch.object(self.retention, "_recheck", side_effect=change_then_check):
            with self.assertRaisesRegex(ValueError, "payload identity changed"):
                self.settle()
        self.assertEqual(self.intent()["schema"], 1)
        self.settle()
        log.write_bytes(b"root changed retained evidence")
        with self.assertRaisesRegex(ValueError, "settled evidence changed"):
            self.inspect()

    def test_root_guard_precedes_any_store_access_and_roots_cannot_overlap(self):
        self.root_guard.stop()
        with patch.object(build_retention.os, "geteuid", return_value=1001), \
             patch.object(build_retention, "_private_root") as opened:
            with self.assertRaises(PermissionError):
                self.reopen()
            with self.assertRaises(PermissionError):
                self.inspect()
            opened.assert_not_called()
        self.root_guard.start()
        with self.assertRaisesRegex(ValueError, "distinct"):
            BuildRetentionStore(inputs=self.inputs, jobs=self.jobs,
                                scratch_root=str(self.roots["inputs"]),
                                artifacts_root=str(self.roots["artifacts"]),
                                logs_root=str(self.roots["logs"]))


if __name__ == "__main__":
    unittest.main()
