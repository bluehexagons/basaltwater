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
from lib.build_host_service import BuildHostService
from lib.build_jobs import BuildJobStore
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_payloads import payload_lease
from lib.build_protocol import MAX_DESCRIPTOR_BYTES, encode_document, request_digest
from lib.build_retention import BuildRetentionStore, FILE_CHARGE_FLOOR
from tests.test_build_inputs import CountingStream, InterruptingStream, build_request


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

    def prune(self, plan, kinds=("source", "log")):
        return self.retention.prune_terminal(self.request["request_id"],
                                             expected_revision=plan["revision"], kinds=kinds)

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

    def test_prune_preserves_success_and_cancellation_orphan_bundles_and_exact_results(self):
        for number, outcome in enumerate(("succeeded", "cancelled", "failed"), 1):
            with self.subTest(outcome=outcome):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                terminal = self.terminal(outcome, orphan=True)
                plan = self.settle()
                bundle = self.path("artifacts", ".bundle")
                identity = bundle.stat().st_ino, bundle.read_bytes()
                ledger = (self.roots["state"] / "jobs.json").read_bytes()
                result = self.prune(plan)
                self.assertEqual(result["charge_bytes"], plan["charge_bytes"] - 2 * FILE_CHARGE_FLOOR)
                self.assertEqual(result["availability"], {"source": "pruned", "log": "pruned", "bundle": "present"})
                self.assertFalse(self.path("inputs", ".source").exists())
                self.assertFalse(self.path("logs", ".log").exists())
                self.assertEqual((bundle.stat().st_ino, bundle.read_bytes()), identity)
                self.assertEqual((self.roots["state"] / "jobs.json").read_bytes(), ledger)
                self.retention = self.reopen()
                self.assertEqual(self.prune(plan), result)
                self.assertEqual(self.inspect(), result)
                with patch.object(self.jobs, "submit", side_effect=AssertionError("must not queue")):
                    for stream in (None, CountingStream(self.payload)):
                        self.assertEqual(self.inputs.admit(
                            self.request, owner="controller", source=stream,
                            source_size=len(self.payload), jobs=self.jobs), terminal)
                        if stream is not None:
                            self.assertGreater(stream.read_calls, 0)

    def test_prune_requires_settlement_and_only_source_log_selections(self):
        self.terminal()
        plan = self.inspect()
        with self.assertRaisesRegex(ValueError, "settlement before"):
            self.prune(plan)
        plan = self.settle(plan)
        for kinds in ((), ("bundle",), ("source", "source"), "source", (None,)):
            with self.subTest(kinds=kinds), self.assertRaises(ValueError):
                self.prune(plan, kinds)
        self.assertEqual(self.intent()["schema"], 2)
        result = self.prune(plan)
        self.assertEqual(result["charge_bytes"], plan["charge_bytes"] - FILE_CHARGE_FLOOR)
        self.assertEqual(result["metadata_charge_bytes"], plan["metadata_charge_bytes"])
        self.assertEqual(self.intent()["reservation_bytes"], self.reservation)

    def test_source_and_log_can_be_selected_separately_with_bounded_exact_retry_history(self):
        self.terminal("succeeded")
        initial = self.settle()
        first = self.prune(initial, ("source",))
        self.assertTrue(self.path("logs", ".log").exists())
        second = self.prune(first, ("log",))
        self.assertNotEqual(first["revision"], initial["revision"])
        self.assertNotEqual(second["revision"], first["revision"])
        self.assertEqual(self.prune(initial, ("source",)), first)
        self.assertEqual(self.prune(first, ("log",)), second)
        self.assertEqual(len(self.intent()["pruning"]["operations"]), 2)
        self.assertLessEqual(self.path("inputs", ".upload.json").stat().st_size,
                             build_inputs.MAX_PRUNING_INTENT_BYTES)
        with self.assertRaisesRegex(ValueError, "already pruned"):
            self.prune(second, ("source",))

    def test_partial_deletion_preserves_old_charge_and_requires_explicit_same_operation_resume(self):
        self.terminal("succeeded")
        plan = self.settle()
        real_unlink = os.unlink

        def interrupt_log(name, *args, **kwargs):
            if name == self.request["request_id"] + ".log":
                raise KeyboardInterrupt
            return real_unlink(name, *args, **kwargs)

        with patch.object(build_retention.os, "unlink", side_effect=interrupt_log):
            with self.assertRaises(KeyboardInterrupt):
                self.prune(plan)
        self.assertFalse(self.path("inputs", ".source").exists())
        self.assertTrue(self.path("logs", ".log").exists())
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])
        pending = self.inspect()
        self.assertEqual(pending["availability"]["source"], "pending_absent")
        self.assertEqual(pending["availability"]["log"], "pending_present")
        self.assertEqual(pending["current_charge_bytes"], plan["charge_bytes"])
        self.assertLess(pending["charge_bytes"], pending["current_charge_bytes"])
        untouched = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "pending explicit"):
            self.inputs.admit(self.request, owner="controller", source=untouched,
                              source_size=len(self.payload), jobs=self.jobs)
        self.assertEqual(untouched.read_calls, 0)
        with self.assertRaisesRegex(ValueError, "changed selected kinds"):
            self.prune(plan, ("log",))
        with self.assertRaisesRegex(ValueError, "original revision"):
            self.prune({"revision": "f" * 64})
        with self.assertRaisesRegex(ValueError, "explicit completion"):
            self.settle(plan)
        before = self.path("inputs", ".upload.json").read_bytes()
        self.inspect()
        self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), before)
        self.inputs = BuildInputStore(self.roots["inputs"], max_bytes=self.reservation + 65536)
        self.retention = self.reopen()
        receipt = self.prune(plan)
        self.assertEqual(self.prune(plan), receipt)
        self.assertEqual(receipt["charge_bytes"], plan["charge_bytes"] - 8192)

    def test_prune_sync_and_final_save_failures_resume_without_early_credit(self):
        self.terminal("succeeded")
        plan = self.settle()
        with patch.object(self.retention, "_sync_deleted", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(OSError, "sync failed"):
                self.prune(plan)
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])
        self.assertEqual(self.inspect()["availability"]["log"], "pending_absent")
        real_save = self.inputs._save_settlement
        synced = []
        real_sync = self.retention._sync_deleted

        def sync(*args):
            real_sync(*args)
            synced.append(True)

        def fail_final(directory, record):
            if record["pruning"]["operations"][-1]["state"] == "completed":
                self.assertTrue(synced)
                raise OSError("final save failed")
            real_save(directory, record)

        with patch.object(self.retention, "_sync_deleted", side_effect=sync), \
             patch.object(self.inputs, "_save_settlement", side_effect=fail_final):
            with self.assertRaisesRegex(OSError, "final save failed"):
                self.prune(plan)
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])
        self.retention = self.reopen()
        result = self.prune(plan)
        self.assertEqual(result, self.prune(plan))

    def test_prune_intent_replace_failure_and_completed_reply_ambiguity(self):
        self.terminal("succeeded")
        plan = self.settle()
        with patch.object(build_inputs.os, "replace", side_effect=OSError("intent replace failed")):
            with self.assertRaisesRegex(OSError, "intent replace failed"):
                self.prune(plan)
        self.assertTrue(self.path("inputs", ".source").exists())
        self.assertEqual(self.intent()["schema"], 2)
        real_save = self.inputs._save_settlement

        def lose_completed_reply(directory, record):
            real_save(directory, record)
            if record["pruning"]["operations"][-1]["state"] == "completed":
                raise OSError("completed reply lost")

        with patch.object(self.inputs, "_save_settlement", side_effect=lose_completed_reply):
            with self.assertRaisesRegex(OSError, "completed reply lost"):
                self.prune(plan)
        self.retention = self.reopen()
        self.assertEqual(self.intent()["pruning"]["operations"][-1]["state"], "completed")
        self.assertEqual(self.prune(plan), self.prune(plan))

    def test_prune_caps_are_checked_before_unlink_and_records_remain_charged(self):
        self.terminal("succeeded")
        plan = self.settle()
        for module, name, limit in ((build_retention, "MAX_PRUNING_INTENT_BYTES", 1024),
                                    (build_retention, "MAX_RETAINED_METADATA_BYTES", 1024)):
            with patch.object(module, name, limit):
                with self.assertRaises(ValueError):
                    self.prune(plan)
            self.assertTrue(self.path("inputs", ".source").exists())
            self.assertEqual(self.intent()["schema"], 2)
        self.prune(plan)
        self.inputs.max_records = 1
        with self.assertRaisesRegex(ValueError, "cannot be pruned"):
            self.admit({**self.request, "request_id": "b" * 32})

    def test_prune_refuses_replaced_and_missing_unselected_evidence_on_resume(self):
        self.terminal("succeeded")
        plan = self.settle()
        with patch.object(self.retention, "_sync_deleted", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.prune(plan, ("source",))
        real_payload = build_retention._payload

        def missing_log(directory, name, **kwargs):
            return None if name.endswith(".log") else real_payload(directory, name, **kwargs)

        with patch.object(build_retention, "_payload", side_effect=missing_log):
            with self.assertRaisesRegex(ValueError, "payload evidence changed"):
                self.prune(plan, ("source",))
        source = self.path("inputs", ".source")
        source.write_bytes(self.payload)
        source.chmod(0o400)
        with self.assertRaisesRegex(ValueError, "payload evidence changed"):
            self.prune(plan, ("source",))
        source.unlink()
        self.prune(plan, ("source",))
        source.write_bytes(self.payload)
        source.chmod(0o400)
        with self.assertRaisesRegex(ValueError, "conflicts with pruning"):
            self.inspect()

    def test_pruned_replay_checks_owner_input_exact_stream_and_existing_terminal_before_read(self):
        terminal = self.terminal("succeeded")
        self.prune(self.settle())
        for owner, request, size in (("other", self.request, len(self.payload)),
                                     ("controller", {**self.request, "project": "other"}, len(self.payload)),
                                     ("controller", self.request, len(self.payload) + 1)):
            stream = CountingStream(self.payload)
            with self.subTest(owner=owner, size=size), self.assertRaises((PermissionError, ValueError)):
                self.inputs.admit(request, owner=owner, source=stream, source_size=size, jobs=self.jobs)
            self.assertEqual(stream.read_calls, 0)
        for content in (self.payload[:-1], self.payload + b"x", b"X" * len(self.payload)):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.inputs.admit(self.request, owner="controller", source=BytesIO(content),
                                  source_size=len(self.payload), jobs=self.jobs)
        with self.assertRaises(KeyboardInterrupt):
            self.inputs.admit(self.request, owner="controller", source=InterruptingStream(self.payload),
                              source_size=len(self.payload), jobs=self.jobs)
        self.assertEqual(self.jobs.status(self.request["request_id"], owner="controller"), terminal)
        with self.jobs._locked() as (state, directory):
            state["jobs"][self.request["request_id"]]["finished_at"] += 1
            state["last_time"] += 1
            self.jobs._save(directory, state)
        stream = CountingStream(self.payload)
        with self.assertRaisesRegex(ValueError, "terminal job identity changed"):
            self.inputs.admit(self.request, owner="controller", source=stream,
                              source_size=len(self.payload), jobs=self.jobs)
        self.assertEqual(stream.read_calls, 0)
        with self.jobs._locked() as (state, directory):
            state["jobs"].clear()
            state["last_sequence"] = 0
            self.jobs._save(directory, state)
        with patch.object(self.jobs, "submit", side_effect=AssertionError("must never queue")):
            with self.assertRaisesRegex(ValueError, "not found"):
                self.inputs.admit(self.request, owner="controller", source=stream,
                                  source_size=len(self.payload), jobs=self.jobs)
        self.assertEqual(stream.read_calls, 0)

    def test_shared_payload_reader_makes_pruning_busy_without_changing_intent(self):
        self.terminal("succeeded")
        plan = self.settle()
        before = self.intent()
        with payload_lease(self.jobs, self.request["request_id"], exclusive=False):
            with self.assertRaisesRegex(BlockingIOError, "busy"):
                self.prune(plan)
        self.assertEqual(self.intent(), before)
        self.prune(plan)

    def test_pruning_credit_admits_exactly_one_more_reservation_without_freeing_history(self):
        self.terminal("succeeded")
        plan = self.settle()
        self.inputs.max_bytes = self.reservation + plan["charge_bytes"] - 8192
        second = {**self.request, "request_id": "b" * 32}
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.admit(second)
        self.prune(plan)
        self.assertEqual(self.admit(second)["sequence"], 2)
        with self.assertRaisesRegex(ValueError, "lacks capacity"):
            self.admit({**self.request, "request_id": "c" * 32})

    def test_stale_revision_and_replacement_between_intent_save_and_unlink_are_refused(self):
        self.terminal("succeeded")
        plan = self.settle()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.prune({"revision": "f" * 64})
        replacement = self.root / "replacement-source"
        replacement.write_bytes(self.payload)
        replacement.chmod(0o400)
        real_save = self.inputs._save_settlement

        def replace_after_intent(directory, record):
            real_save(directory, record)
            replacement.replace(self.path("inputs", ".source"))

        with patch.object(self.inputs, "_save_settlement", side_effect=replace_after_intent):
            with self.assertRaisesRegex(ValueError, "replaced before unlink"):
                self.prune(plan)
        self.assertEqual(self.path("inputs", ".source").read_bytes(), self.payload)
        self.assertTrue(self.path("logs", ".log").exists())
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])

    def test_explicit_resume_reconciles_only_exact_complete_staged_metadata(self):
        for number, stage in enumerate(("pending", "completed"), 1):
            self.request = {**self.request, "request_id": f"{number:032x}"}
            self.terminal("succeeded")
            plan = self.settle()
            real_save = self.inputs._save_settlement

            def crash_with_staged_record(directory, record):
                if record["pruning"]["operations"][-1]["state"] == stage:
                    self.write("inputs", ".settlement.tmp", encode_document(record))
                    raise OSError("simulated crash before replace")
                real_save(directory, record)

            with patch.object(self.inputs, "_save_settlement", side_effect=crash_with_staged_record):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    self.prune(plan)
            with self.assertRaisesRegex(ValueError, "unrecognized retained"):
                self.admit()
            temporary = self.path("inputs", ".settlement.tmp")
            self.retention = self.reopen()
            original = temporary.read_bytes()
            temporary.write_bytes(original[:-1])
            with self.assertRaises(ValueError):
                self.prune(plan)
            self.assertTrue(temporary.exists())
            temporary.write_bytes(original)
            self.assertTrue(self.prune(plan)["pruning"][-1]["state"] == "completed")
            self.assertFalse(temporary.exists())

    def test_completed_replace_sync_failure_reconciles_on_reopen_before_lower_charge(self):
        self.terminal("succeeded")
        plan = self.settle()
        real_save = self.inputs._save_settlement
        real_sync = os.fsync

        def save(directory, record):
            if record["pruning"]["operations"][-1]["state"] != "completed":
                return real_save(directory, record)

            def sync(descriptor):
                if descriptor == directory:
                    raise OSError("completed directory sync failed")
                real_sync(descriptor)

            with patch.object(build_inputs.os, "fsync", side_effect=sync):
                return real_save(directory, record)

        with patch.object(self.inputs, "_save_settlement", side_effect=save):
            with self.assertRaisesRegex(OSError, "completed directory sync failed"):
                self.prune(plan)
        self.assertEqual(self.intent()["pruning"]["operations"][-1]["state"], "completed")
        self.retention = self.reopen()
        with patch.object(build_inputs.os, "fsync", side_effect=OSError("still unavailable")):
            with self.assertRaises(OSError):
                self.admit({**self.request, "request_id": "b" * 32})
        self.assertEqual(self.prune(plan), self.inspect())

    def test_prune_refuses_active_uncertain_and_scratch_partial_evidence(self):
        self.admit()
        for outcome in ("queued", "running", "uncertain"):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, "certain terminal"):
                self.prune({"revision": "f" * 64})
            if outcome == "queued":
                with self.jobs.worker() as lease:
                    lease.claim()
            elif outcome == "running":
                with self.jobs.worker() as lease:
                    lease.recover_interrupted(self.request["request_id"], cleanup=lambda *_: True)
        self.inputs.max_bytes += self.reservation
        self.request = {**self.request, "request_id": "b" * 32}
        self.terminal("succeeded")
        plan = self.settle()
        scratch = self.path("scratch", "")
        scratch.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, "scratch evidence"):
            self.prune(plan)
        scratch.rmdir()
        partial = self.path("inputs", ".part")
        partial.write_bytes(b"")
        partial.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "both published and partial"):
            self.prune(plan)

    def test_busy_service_stream_keeps_pruning_out_until_interrupted_response_cleanup(self):
        self.request["platform"] = dict(DEBIAN_PLATFORM)
        terminal = self.terminal("succeeded")
        plan = self.settle()
        policy = {"schema": 1, "principals": {"controller": {"projects": {
            self.request["project"]: {"platform": self.request["platform"],
                                      "limits": self.request["limits"], "runtimes": []},
        }}}}
        service = BuildHostService(jobs=self.jobs, load_policy=lambda: policy,
                                   admit_source=self.inputs.admit, tool_versions=lambda: {},
                                   logs_root=str(self.roots["logs"]),
                                   artifacts_root=str(self.roots["artifacts"]))
        for action in ("artifact", "log"):
            entered = threading.Event()
            resume = threading.Event()
            errors = []

            class Output(BytesIO):
                def write(self, value):
                    entered.set()
                    if not resume.wait(5):
                        raise RuntimeError("test synchronization timed out")
                    raise KeyboardInterrupt

            fields = {"offset": 0, "max_bytes": 64} if action == "log" else {}
            header = encode_document({"schema": 1, "action": action,
                                      "request_id": self.request["request_id"], **fields}) + b"\n"

            def stream():
                try:
                    service.handle(header, principal="controller", input_stream=BytesIO(),
                                   output_stream=Output())
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=stream)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(BlockingIOError, "busy"):
                    self.prune(plan)
                self.assertEqual(self.jobs.status(self.request["request_id"], owner="controller"), terminal)
                self.assertTrue(self.jobs.set_draining(True))
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([type(error) for error in errors], [KeyboardInterrupt])
        self.prune(plan)

    def test_log_only_prune_replay_never_submits_and_remaining_source_can_be_pruned_later(self):
        terminal = self.terminal("succeeded")
        first = self.prune(self.settle(), ("log",))
        self.assertTrue(self.path("inputs", ".source").exists())
        with patch.object(self.jobs, "submit", side_effect=AssertionError("must not submit")):
            self.assertEqual(self.admit(), terminal)
        self.prune(first, ("source",))
        self.inputs.max_records = 128
        with patch("lib.build_jobs.MAX_OPERATIONS", 1):
            with self.assertRaisesRegex(ValueError, "cannot be pruned"):
                self.admit({**self.request, "request_id": "b" * 32})

    def test_corrupt_pruning_tombstones_and_byte_metadata_caps_fail_closed(self):
        self.terminal("succeeded")
        self.prune(self.settle())
        original = self.intent()
        path = self.path("inputs", ".upload.json")
        variants = []
        for mutation in ("charge", "operations", "kinds", "job", "source"):
            changed = copy.deepcopy(original)
            if mutation == "charge":
                changed["charge_bytes"] -= 1
            elif mutation == "operations":
                changed["pruning"]["operations"] *= 3
            elif mutation == "kinds":
                changed["pruning"]["operations"][0]["kinds"] = ["bundle"]
            elif mutation == "job":
                changed["pruning"]["job_sha256"] = "x" * 64
            else:
                changed["pruning"]["payloads"]["source"]["sha256"] = "f" * 64
            variants.append(changed)
        for value in variants:
            path.write_bytes(encode_document(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.inspect()
        raw = encode_document(original)
        path.write_bytes(raw)
        for name, bound in (("MAX_PRUNING_INTENT_BYTES", len(raw) - 1),
                            ("MAX_RETAINED_METADATA_BYTES", len(raw) - 1)):
            with patch.object(build_inputs, name, bound):
                with self.assertRaises(ValueError):
                    self.admit()
        self.assertEqual(self.inspect()["current_charge_bytes"], original["charge_bytes"])


if __name__ == "__main__":
    unittest.main()
