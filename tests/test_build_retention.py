"""Terminal settlement preserves bytes and replay while releasing unused charge."""

from __future__ import annotations

import copy
import fcntl
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
import lib.build_jobs as build_jobs
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

    def service(self):
        policy = {"schema": 1, "principals": {"controller": {"projects": {
            self.request["project"]: {"platform": self.request["platform"],
                                      "limits": self.request["limits"], "runtimes": []},
        }}}}
        return BuildHostService(jobs=self.jobs, load_policy=lambda: policy,
                                admit_source=self.inputs.admit, tool_versions=lambda: {},
                                logs_root=str(self.roots["logs"]),
                                artifacts_root=str(self.roots["artifacts"]))

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

    def terminal(self, outcome="cancelled", *, orphan=False, bundle_payload=b"output"):
        self.admit()
        if outcome == "cancelled" and not orphan:
            return self.jobs.cancel(self.request["request_id"], owner="controller")
        with self.jobs.worker() as lease:
            lease.claim()
            artifact = self.bundle(bundle_payload) if outcome == "succeeded" or orphan else None
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

    def release(self, plan):
        return self.retention.release_bundle(self.request["request_id"],
                                             expected_revision=plan["revision"])

    def stage_settlement(self, plan):
        """Retain the real synced pre-replace output, as an uncatchable kill does.

        Suppress only the save's finally cleanup; payloads and the version-1
        intent still come from the ordinary admission/worker/save paths.
        """
        real_unlink = os.unlink

        def retain_temporary(name, *args, **kwargs):
            if name != self.request["request_id"] + ".settlement.tmp":
                return real_unlink(name, *args, **kwargs)

        with patch.object(build_inputs.os, "replace", side_effect=OSError("pre-replace crash")), \
             patch.object(build_inputs.os, "unlink", side_effect=retain_temporary):
            with self.assertRaisesRegex(OSError, "pre-replace crash"):
                self.settle(plan)
        self.assertEqual(self.intent()["schema"], 1)
        return self.path("inputs", ".settlement.tmp")

    def stage_release(self, plan, state="pending"):
        """Leave the ordinary release save's complete pre-replace temporary."""
        real_save, real_unlink = self.inputs._save_settlement, os.unlink

        def preserve(name, *args, **kwargs):
            if not name.endswith(".settlement.tmp"):
                return real_unlink(name, *args, **kwargs)

        def crash(directory, record):
            if record["pruning"]["operations"][-1]["state"] == state:
                with patch.object(build_inputs.os, "replace", side_effect=KeyboardInterrupt), \
                     patch.object(build_inputs.os, "unlink", side_effect=preserve):
                    real_save(directory, record)
            else:
                real_save(directory, record)

        with patch.object(self.inputs, "_save_settlement", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.release(plan)
        return self.path("inputs", ".settlement.tmp")

    def test_staged_initial_settlement_exact_retry_preserves_payloads_jobs_and_sibling(self):
        self.inputs.max_bytes = 2 * self.reservation + 65536
        terminal = self.terminal("succeeded")
        sibling = {**self.request, "request_id": "b" * 32}
        self.admit(sibling)
        plan = self.inspect()
        original = self.intent()
        retained = [path for root in self.roots.values() for path in root.iterdir()
                    if path != self.path("inputs", ".upload.json")]
        before = {path: (path.read_bytes(), path.stat().st_ino) for path in retained}
        temporary = self.stage_settlement(plan)
        staged = temporary.read_bytes()
        self.assertEqual(json.loads(staged), {
            **original, "schema": 2, "charge_bytes": plan["charge_bytes"],
            "settlement_revision": plan["revision"],
        })
        for operation in (self.admit, self.inspect, self.inputs.capacity_snapshot):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "unrecognized retained"):
                    if operation == self.inputs.capacity_snapshot:
                        with operation():
                            self.fail("staged settlement accepted for capacity")
                    else:
                        operation()
                self.assertEqual(temporary.read_bytes(), staged)
                self.assertEqual(self.intent(), original)
        self.inputs = BuildInputStore(self.roots["inputs"], max_bytes=self.inputs.max_bytes)
        self.jobs = BuildJobStore(self.roots["state"] / "jobs.json", clock=lambda: 1000.0)
        self.retention = self.reopen()
        receipt = self.settle(plan)
        self.assertEqual(self.settle(plan), receipt)
        self.assertTrue(receipt["settled"])
        self.assertFalse(temporary.exists())
        self.assertEqual(self.intent()["reservation_bytes"], original["reservation_bytes"])
        for path, value in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino), value)
        self.assertEqual(self.admit(), terminal)

    def test_staged_settlement_refuses_stale_or_changed_evidence_before_discard(self):
        self.inputs.max_bytes = 12 * self.reservation
        for number, change in enumerate(("revision", "job", "source", "log", "bundle", "root"), 1):
            with self.subTest(change=change):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                self.terminal("succeeded")
                plan = self.inspect()
                original = self.path("inputs", ".upload.json").read_bytes()
                temporary = self.stage_settlement(plan)
                staged = temporary.read_bytes()
                if change == "revision":
                    plan = {**plan, "revision": "f" * 64}
                elif change == "job":
                    with self.jobs._locked() as (state, directory):
                        state["jobs"][self.request["request_id"]]["finished_at"] += 1
                        state["last_time"] += 1
                        self.jobs._save(directory, state)
                elif change == "source":
                    source = self.path("inputs", ".source")
                    source.chmod(0o600)
                    source.write_bytes(b"X" * len(self.payload))
                    source.chmod(0o400)
                elif change == "log":
                    self.path("logs", ".log").write_bytes(b"changed log")
                elif change == "bundle":
                    self.bundle(b"changed output")
                else:
                    old = self.root / "old-scratch"
                    self.roots["scratch"].rename(old)
                    self.roots["scratch"].mkdir(mode=0o700)
                with self.assertRaises(ValueError):
                    self.settle(plan)
                self.assertEqual(temporary.read_bytes(), staged)
                self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
                temporary.unlink()  # fixture cleanup only
                if change == "root":
                    self.roots["scratch"].rmdir()
                    old.rename(self.roots["scratch"])

    def test_staged_settlement_requires_exact_canonical_safe_bounded_transition(self):
        self.terminal("succeeded")
        plan = self.inspect()
        temporary = self.stage_settlement(plan)
        original = self.path("inputs", ".upload.json").read_bytes()
        raw = temporary.read_bytes()
        record = json.loads(raw)
        alias = self.root / "temporary-alias"
        for change in ("partial", "malformed", "noncanonical", "wrong charge", "wrong revision",
                       "version1", "other id", "oversized", "mode", "symlink", "hardlink", "owner"):
            with self.subTest(change=change):
                temporary.unlink()
                temporary.write_bytes(raw)
                temporary.chmod(0o600)
                if change == "partial":
                    temporary.write_bytes(raw[:-1])
                elif change == "malformed":
                    temporary.write_bytes(b"not json")
                elif change == "noncanonical":
                    temporary.write_bytes(raw + b"\n")
                elif change == "wrong charge":
                    temporary.write_bytes(encode_document({**record, "charge_bytes": record["charge_bytes"] + 1}))
                elif change == "wrong revision":
                    temporary.write_bytes(encode_document({**record, "settlement_revision": "f" * 64}))
                elif change == "version1":
                    temporary.write_bytes(original)
                elif change == "other id":
                    temporary.write_bytes(encode_document({**record, "request_id": "b" * 32}))
                elif change == "oversized":
                    temporary.write_bytes(b"x" * (build_inputs.MAX_INTENT_BYTES + 1))
                elif change == "mode":
                    temporary.chmod(0o644)
                elif change == "symlink":
                    temporary.unlink()
                    temporary.symlink_to(self.path("inputs", ".source"))
                elif change == "hardlink":
                    os.link(temporary, alias)
                before = temporary.lstat()
                real_check = build_retention._check_regular

                def check_owner(info, label, **kwargs):
                    if change == "owner" and label == "staged retention intent":
                        values = list(info)
                        values[4] = info.st_uid + 1
                        info = os.stat_result(values)
                    return real_check(info, label, **kwargs)

                with patch.object(build_retention, "_check_regular", side_effect=check_owner):
                    with self.assertRaises((ValueError, OSError)):
                        self.settle(plan)
                self.assertEqual(build_retention._identity(temporary.lstat()), build_retention._identity(before))
                self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
                if alias.exists():
                    alias.unlink()
        temporary.unlink()
        temporary.write_bytes(raw)
        temporary.chmod(0o600)
        other = temporary.with_name("b" * 32 + ".settlement.tmp")
        temporary.rename(other)
        with self.assertRaisesRegex(ValueError, "unrecognized retained"):
            self.settle(plan)
        self.assertEqual(other.read_bytes(), raw)

    def test_staged_settlement_checks_quota_and_metadata_bounds_before_discard(self):
        self.terminal()
        plan = self.inspect()
        temporary = self.stage_settlement(plan)
        staged = temporary.read_bytes()
        for module, name, bound in ((build_retention, "MAX_INTENT_BYTES", len(staged) - 1),
                                     (build_retention, "MAX_RETAINED_METADATA_BYTES", len(staged) - 1),
                                     (self.inputs, "max_bytes", self.reservation - 1),
                                     (self.inputs, "max_records", 0)):
            with self.subTest(name=name), patch.object(module, name, bound):
                with self.assertRaises(ValueError):
                    self.settle(plan)
            self.assertEqual(temporary.read_bytes(), staged)
            self.assertEqual(self.intent()["schema"], 1)
        self.assertTrue(self.settle(plan)["settled"])

    def test_staged_settlement_discard_sync_and_new_save_interruptions_keep_original_charge(self):
        self.inputs.max_bytes = 5 * self.reservation
        for number, stage in enumerate(("discard", "sync", "save", "new temporary"), 1):
            with self.subTest(stage=stage):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                self.terminal("succeeded")
                plan = self.inspect()
                temporary = self.stage_settlement(plan)
                original = self.path("inputs", ".upload.json").read_bytes()
                real_unlink, real_sync = os.unlink, os.fsync
                real_save = self.inputs._save_settlement
                discarded = []
                synced = []

                def unlink(name, *args, **kwargs):
                    if name == temporary.name:
                        if stage == "discard":
                            raise KeyboardInterrupt
                        result = real_unlink(name, *args, **kwargs)
                        discarded.append(kwargs["dir_fd"])
                        return result
                    return real_unlink(name, *args, **kwargs)

                def sync(descriptor):
                    if descriptor in discarded:
                        if stage == "sync":
                            raise KeyboardInterrupt
                        synced.append(True)
                    return real_sync(descriptor)

                def fail_save(*args, **kwargs):
                    self.assertTrue(synced)
                    if stage == "new temporary":
                        # Another pre-replace kill can leave a fresh complete
                        # temporary after the prior one was durably discarded.
                        with patch.object(build_inputs.os, "replace", side_effect=KeyboardInterrupt), \
                             patch.object(build_inputs.os, "unlink", return_value=None):
                            return real_save(*args, **kwargs)
                    raise KeyboardInterrupt

                with patch.object(build_retention.os, "unlink", side_effect=unlink), \
                     patch.object(build_retention.os, "fsync", side_effect=sync), \
                     patch.object(self.inputs, "_save_settlement", side_effect=fail_save):
                    with self.assertRaises(KeyboardInterrupt):
                        self.settle(plan)
                self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
                self.assertEqual(temporary.exists(), stage in {"discard", "new temporary"})
                self.retention = self.reopen()
                self.inputs.max_bytes = sum(
                    record.get("charge_bytes", record["reservation_bytes"])
                    for record in (json.loads(path.read_bytes())
                                   for path in self.roots["inputs"].glob("*.upload.json")))
                with self.assertRaises(ValueError):
                    self.admit({**self.request, "request_id": "f" * 32})
                receipt = self.settle(plan)
                self.assertEqual(self.settle(plan), receipt)
                self.inputs.max_bytes = 5 * self.reservation

    def test_staged_settlement_is_not_accepted_for_changed_revision_or_settled_intent(self):
        self.terminal()
        plan = self.inspect()
        temporary = self.stage_settlement(plan)
        staged = temporary.read_bytes()
        original = self.path("inputs", ".upload.json").read_bytes()
        self.write("logs", ".log", b"new evidence")
        # Recompute a fresh revision without asking inspect to ignore staged
        # metadata. Even that revision cannot authorize the old temporary.
        temporary.rename(self.root / "held-temporary")
        changed = self.inspect()
        (self.root / "held-temporary").rename(temporary)
        with self.assertRaisesRegex(ValueError, "staged intent is ambiguous"):
            self.settle(changed)
        self.assertEqual(temporary.read_bytes(), staged)
        self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
        temporary.unlink()
        receipt = self.settle(changed)
        self.write("inputs", ".settlement.tmp", self.path("inputs", ".upload.json").read_bytes())
        with self.assertRaises(ValueError):
            self.settle(changed)
        self.assertTrue(temporary.exists())
        self.assertEqual(self.intent()["charge_bytes"], receipt["charge_bytes"])

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

    def test_private_job_reader_refuses_unestablished_evidence_without_repair(self):
        self.terminal()
        ledger = self.roots["state"] / "jobs.json"
        witness = self.roots["state"] / "jobs.json.lock"
        ledger_bytes = ledger.read_bytes()
        witness_bytes = witness.read_bytes()

        def evidence(path):
            if not path.exists():
                return None
            info = path.stat()
            return (path.read_bytes(), info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)

        with self.jobs.worker() as lease:
            for case in ("missing lock", "empty witness", "invalid witness", "missing ledger", "both missing"):
                with self.subTest(case=case):
                    if ledger.exists():
                        ledger.unlink()
                    if witness.exists():
                        witness.unlink()
                    if case != "both missing":
                        ledger.write_bytes(ledger_bytes)
                        ledger.chmod(0o600)
                    if case not in {"missing lock", "both missing"}:
                        witness.write_bytes(witness_bytes)
                        witness.chmod(0o600)
                    if case == "empty witness":
                        witness.write_bytes(b"")
                    elif case == "invalid witness":
                        witness.write_bytes(b"invalid\n")
                    elif case == "missing ledger":
                        ledger.unlink()
                    before = (evidence(ledger), evidence(witness))
                    with patch.object(self.jobs, "_save", side_effect=AssertionError("must not save job state")), \
                         patch.object(build_jobs.os, "pwrite", side_effect=AssertionError("must not write witness")), \
                         patch.object(build_jobs.os, "fsync", side_effect=AssertionError("must not sync job state")):
                        with self.assertRaises((OSError, ValueError)):
                            self.jobs.read_for_retention(self.request["request_id"], lease=lease)
                    self.assertEqual((evidence(ledger), evidence(witness)), before)

    def test_inspect_settle_and_prune_refuse_missing_job_lock_before_input_changes(self):
        ledger = self.roots["state"] / "jobs.json"
        witness = self.roots["state"] / "jobs.json.lock"

        self.inputs.max_bytes = 4 * self.reservation + 65536
        for index, operation in enumerate(("inspect", "settle", "prune", "release"), 1):
            with self.subTest(operation=operation):
                self.request = {**self.request, "request_id": f"{index:032x}"}
                self.terminal("succeeded")
                plan = self.inspect()
                if operation in {"prune", "release"}:
                    plan = self.settle(plan)
                witness_bytes = witness.read_bytes()
                input_path = self.path("inputs", ".upload.json")
                intent_before = input_path.read_bytes()
                ledger_before = ledger.read_bytes()
                ledger_info = ledger.stat()
                source = self.path("inputs", ".source")
                log = self.path("logs", ".log")
                source_info, log_info = source.stat(), log.stat()
                witness.unlink()

                with self.assertRaises((OSError, ValueError)):
                    if operation == "inspect":
                        self.inspect()
                    elif operation == "settle":
                        self.settle(plan)
                    elif operation == "prune":
                        self.prune(plan)
                    else:
                        self.release(plan)

                self.assertFalse(witness.exists())
                self.assertEqual(ledger.read_bytes(), ledger_before)
                self.assertEqual((ledger.stat().st_ino, ledger.stat().st_mtime_ns, ledger.stat().st_ctime_ns),
                                 (ledger_info.st_ino, ledger_info.st_mtime_ns, ledger_info.st_ctime_ns))
                self.assertEqual(input_path.read_bytes(), intent_before)
                self.assertFalse(self.path("inputs", ".settlement.tmp").exists())
                self.assertEqual(source.stat().st_ino, source_info.st_ino)
                self.assertEqual(log.stat().st_ino, log_info.st_ino)
                self.assertTrue(source.exists())
                self.assertTrue(log.exists())
                witness.write_bytes(witness_bytes)
                witness.chmod(0o600)

    def test_busy_job_state_lock_refuses_promptly_and_releases_maintenance_locks(self):
        self.check_busy_job_state_lock(release=False)

    def test_release_busy_job_state_lock_refuses_promptly_and_releases_maintenance_locks(self):
        self.check_busy_job_state_lock(release=True)

    def check_busy_job_state_lock(self, *, release):
        self.terminal("succeeded")
        plan = self.settle() if release else None
        lock_path = self.roots["state"] / "jobs.json.lock"
        holder = os.open(lock_path, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held_identity = os.fstat(holder).st_dev, os.fstat(holder).st_ino
        attempted = threading.Event()
        finished = threading.Event()
        errors = []
        real_flock = fcntl.flock

        def observed_flock(descriptor, operation):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == held_identity:
                attempted.set()
            return real_flock(descriptor, operation)

        def inspect():
            try:
                self.release(plan) if release else self.inspect()
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        try:
            with patch.object(build_jobs.fcntl, "flock", side_effect=observed_flock):
                thread = threading.Thread(target=inspect)
                thread.start()
                try:
                    self.assertTrue(attempted.wait(5), "maintenance did not attempt the job-state lock")
                    prompt_refusal = finished.wait(5)
                finally:
                    # Release and join even if synchronization fails or a
                    # regression waits on flock instead of refusing it.
                    os.close(holder)
                    holder = -1
                    thread.join(5)
                self.assertFalse(thread.is_alive(), "maintenance remained stuck after lock release")
            self.assertTrue(prompt_refusal, "busy job-state lock must be refused without waiting")
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertIn("busy", str(errors[0]))
            # A fresh operation proves worker, input and payload protection
            # unwound when the job-state snapshot refused.
            self.assertEqual(self.inspect()["request_id"], self.request["request_id"])
        finally:
            if holder >= 0:
                os.close(holder)

    def test_queued_running_uncertain_are_refused_and_worker_slot_is_nonblocking(self):
        self.admit()
        with self.assertRaisesRegex(ValueError, "certain terminal"):
            self.inspect()
        with self.jobs.worker() as lease:
            with self.assertRaisesRegex(RuntimeError, "already leased"):
                self.inspect()
            with self.assertRaisesRegex(RuntimeError, "already leased"):
                self.release({"revision": "f" * 64})
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
        with self.assertRaisesRegex(ValueError, "state lock is missing|not found"):
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
            with self.subTest(release_revision=revision), self.assertRaises(ValueError):
                self.retention.release_bundle(self.request["request_id"], expected_revision=revision)

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
            with self.assertRaises(PermissionError):
                self.release({"revision": "f" * 64})
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
        self.check_deleted_replay(release=False)

    def test_released_replay_checks_owner_input_exact_stream_and_existing_terminal_before_read(self):
        self.check_deleted_replay(release=True)

    def check_deleted_replay(self, *, release):
        terminal = self.terminal("succeeded")
        (self.release if release else self.prune)(self.settle())
        self.jobs = BuildJobStore(self.roots["state"] / "jobs.json", clock=lambda: 1000.0)
        self.assertEqual(self.admit(), terminal)
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
        stream = CountingStream(self.payload)
        with patch.object(self.jobs, "submit", side_effect=AssertionError("must never queue")):
            with self.assertRaisesRegex(ValueError, "not found"):
                self.inputs.admit(self.request, owner="controller", source=stream,
                                  source_size=len(self.payload), jobs=self.jobs)
        self.assertEqual(stream.read_calls, 0)

    def test_pruned_replay_does_not_initialize_or_rewrite_missing_job_evidence(self):
        self.check_deleted_replay_missing_evidence(release=False)

    def test_released_replay_does_not_initialize_or_rewrite_missing_job_evidence(self):
        self.check_deleted_replay_missing_evidence(release=True)

    def check_deleted_replay_missing_evidence(self, *, release):
        self.terminal("succeeded")
        (self.release if release else self.prune)(self.settle())
        ledger = self.roots["state"] / "jobs.json"
        witness = self.roots["state"] / "jobs.json.lock"
        ledger_bytes = ledger.read_bytes()
        witness_bytes = witness.read_bytes()

        def restore():
            ledger.write_bytes(ledger_bytes)
            ledger.chmod(0o600)
            witness.write_bytes(witness_bytes)
            witness.chmod(0o600)

        for case in ("missing lock", "empty witness", "invalid witness", "missing ledger", "both missing"):
            with self.subTest(case=case):
                restore()
                if case == "missing lock":
                    witness.unlink()
                elif case == "empty witness":
                    witness.write_bytes(b"")
                elif case == "invalid witness":
                    witness.write_bytes(b"invalid\n")
                elif case == "missing ledger":
                    ledger.unlink()
                else:
                    ledger.unlink()
                    witness.unlink()
                before = {
                    "ledger_exists": ledger.exists(),
                    "ledger": ledger.read_bytes() if ledger.exists() else None,
                    "witness_exists": witness.exists(),
                    "witness": witness.read_bytes() if witness.exists() else None,
                }
                stream = CountingStream(self.payload)
                with patch.object(self.jobs, "submit", side_effect=AssertionError("must never queue")):
                    with self.assertRaises((OSError, ValueError)):
                        self.inputs.admit(self.request, owner="controller", source=stream,
                                          source_size=len(self.payload), jobs=self.jobs)
                self.assertEqual(stream.read_calls, 0)
                self.assertEqual(ledger.exists(), before["ledger_exists"])
                self.assertEqual(ledger.read_bytes() if ledger.exists() else None, before["ledger"])
                self.assertEqual(witness.exists(), before["witness_exists"])
                self.assertEqual(witness.read_bytes() if witness.exists() else None, before["witness"])
    def test_shared_payload_reader_makes_pruning_busy_without_changing_intent(self):
        self.terminal("succeeded")
        plan = self.settle()
        before = self.intent()
        with payload_lease(self.jobs, self.request["request_id"], exclusive=False):
            with self.assertRaisesRegex(BlockingIOError, "busy"):
                self.prune(plan)
            with self.assertRaisesRegex(BlockingIOError, "busy"):
                self.release(plan)
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
        self.check_completed_replace_sync_failure(release=False)

    def test_release_completed_replace_sync_failure_reconciles_before_lower_charge(self):
        self.check_completed_replace_sync_failure(release=True)

    def check_completed_replace_sync_failure(self, *, release):
        self.terminal("succeeded")
        plan = self.settle()
        operation = self.release if release else self.prune
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
                operation(plan)
        self.assertEqual(self.intent()["pruning"]["operations"][-1]["state"], "completed")
        self.retention = self.reopen()
        with patch.object(build_inputs.os, "fsync", side_effect=OSError("still unavailable")):
            with self.assertRaises(OSError):
                self.admit({**self.request, "request_id": "b" * 32})
        self.assertEqual(operation(plan), self.inspect())

    def test_prune_refuses_active_uncertain_and_scratch_partial_evidence(self):
        self.admit()
        for outcome in ("queued", "running", "uncertain"):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, "certain terminal"):
                self.prune({"revision": "f" * 64})
            with self.subTest(release_outcome=outcome), self.assertRaisesRegex(ValueError, "certain terminal"):
                self.release({"revision": "f" * 64})
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
        service = self.service()
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
                with self.assertRaisesRegex(BlockingIOError, "busy"):
                    self.release(plan)
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

    def test_release_orders_preserve_exact_historical_receipts_and_all_job_evidence(self):
        orders = (("bundle", "source", "log"), ("source", "bundle", "log"),
                  ("source", "log", "bundle"), (("source", "log"), "bundle"),
                  ("bundle", ("source", "log")))
        self.inputs.max_bytes = 6 * self.reservation
        for number, order in enumerate(orders, 1):
            with self.subTest(order=order):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                terminal = self.terminal("succeeded")
                ledger = (self.roots["state"] / "jobs.json").read_bytes()
                original = self.intent()
                plan = self.settle()
                retries = []
                for selection in order:
                    previous = plan
                    if selection == "bundle":
                        plan = self.release(previous)
                        kinds = ("bundle",)
                    else:
                        kinds = (selection,) if isinstance(selection, str) else selection
                        plan = self.prune(previous, kinds)
                    retries.append((previous, kinds, plan))
                    self.assertEqual(plan["current_charge_bytes"], previous["current_charge_bytes"]
                                     - len(kinds) * FILE_CHARGE_FLOOR)
                self.assertEqual(self.intent()["schema"], 4)
                self.assertLessEqual(len(encode_document(self.intent())), build_inputs.MAX_PRUNING_INTENT_BYTES)
                self.assertEqual(plan["current_charge_bytes"], plan["metadata_charge_bytes"])
                for key, value in original.items():
                    if key != "schema":
                        self.assertEqual(self.intent()[key], value)
                self.retention = self.reopen()
                self.assertEqual(self.inspect(), plan)
                for previous, kinds, receipt in retries:
                    result = self.release(previous) if kinds == ("bundle",) else self.prune(previous, kinds)
                    self.assertEqual(result, receipt)
                with patch.object(self.jobs, "submit", side_effect=AssertionError("must not submit")):
                    self.assertEqual(self.admit(), terminal)
                    self.assertEqual(self.inputs.admit(self.request, owner="controller", source=None,
                                                      source_size=len(self.payload), jobs=self.jobs), terminal)
                self.assertEqual((self.roots["state"] / "jobs.json").read_bytes(), ledger)

    def test_release_requires_settlement_success_and_exact_revision_keeps_orphans_charged(self):
        self.inputs.max_bytes = 6 * self.reservation
        for number, outcome in enumerate(("failed", "cancelled", "expired", "uncertain", "succeeded"), 1):
            with self.subTest(outcome=outcome):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                if outcome == "expired":
                    self.admit()
                    self.jobs._clock = lambda: 2000.0
                    self.jobs.status(self.request["request_id"], owner="controller")
                    self.jobs._clock = lambda: 1000.0
                    self.bundle()
                    self.write("logs", ".log", b"orphan log")
                else:
                    self.terminal(outcome, orphan=True)
                if outcome == "uncertain":
                    with self.assertRaisesRegex(ValueError, "certain terminal"):
                        self.release({"revision": "f" * 64})
                    self.assertEqual(self.intent()["schema"], 1)
                    continue
                plan = self.inspect()
                with self.assertRaisesRegex(ValueError, "settlement before"):
                    self.release(plan)
                plan = self.settle(plan)
                original = self.intent()
                if outcome != "succeeded":
                    with self.assertRaisesRegex(ValueError, "succeeded job"):
                        self.release(plan)
                else:
                    with self.assertRaisesRegex(ValueError, "stale"):
                        self.release({"revision": "f" * 64})
                self.assertEqual(self.intent(), original)
                self.assertTrue(self.path("artifacts", ".bundle").exists())

    def test_release_keeps_shared_service_protection_through_flush_and_baseexception_cleanup(self):
        self.request["platform"] = dict(DEBIAN_PLATFORM)
        self.terminal("succeeded")
        plan = self.settle()
        service = self.service()
        original = self.intent()
        for interrupted in (False, True):
            entered, resume = threading.Event(), threading.Event()
            errors = []

            class Output(BytesIO):
                def flush(self):
                    entered.set()
                    if not resume.wait(5):
                        raise RuntimeError("test synchronization timed out")
                    if interrupted:
                        raise KeyboardInterrupt
                    super().flush()

            def stream():
                try:
                    service.handle(encode_document({"schema": 1, "action": "artifact",
                                                    "request_id": self.request["request_id"]}) + b"\n",
                                   principal="controller", input_stream=BytesIO(), output_stream=Output())
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=stream)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(BlockingIOError, "busy"):
                    self.release(plan)
                self.assertEqual(self.intent(), original)
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([type(error) for error in errors], [KeyboardInterrupt] if interrupted else [])
            self.assertEqual(self.inspect()["revision"], plan["revision"])
        self.release(plan)

    def test_release_preserves_service_status_download_does_not_release_and_missing_is_unavailable(self):
        self.request["platform"] = dict(DEBIAN_PLATFORM)
        terminal = self.terminal("succeeded", bundle_payload=b"x" * 5000)
        plan = self.settle()
        service = self.service()

        def call(action):
            output = BytesIO()
            service.handle(encode_document({"schema": 1, "action": action,
                                            "request_id": self.request["request_id"]}) + b"\n",
                           principal="controller", input_stream=BytesIO(), output_stream=output)
            header, payload = output.getvalue().split(b"\n", 1)
            return json.loads(header), payload

        before = self.intent()
        response, payload = call("artifact")
        self.assertTrue(response["ok"])
        self.assertEqual(response["payload_bytes"], terminal["artifact"]["size"])
        self.assertEqual(payload, self.path("artifacts", ".bundle").read_bytes())
        self.assertEqual(self.intent(), before)
        ledger = (self.roots["state"] / "jobs.json").read_bytes()
        released = self.release(plan)
        self.assertEqual(released["charge_bytes"], plan["charge_bytes"] - terminal["artifact"]["size"])
        self.assertEqual(call("status"), ({"ok": True, "schema": 1, "payload_bytes": 0,
                                           "result": terminal}, b""))
        self.assertEqual(call("artifact"), ({"ok": False, "schema": 1, "payload_bytes": 0,
                                             "error": "unavailable"}, b""))
        self.assertEqual((self.roots["state"] / "jobs.json").read_bytes(), ledger)

    def test_release_unlink_sync_save_and_reply_faults_retry_exactly_once(self):
        self.inputs.max_bytes = 6 * self.reservation
        for number, failure in enumerate(("pending save", "unlink", "directory sync", "completion save", "reply"), 1):
            with self.subTest(failure=failure):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                self.terminal("succeeded")
                plan = self.settle()
                real_save, real_unlink = self.inputs._save_settlement, os.unlink
                real_sync = self.retention._sync_deleted
                synced = []

                def save(directory, record):
                    completed = record["pruning"]["operations"][-1]["state"] == "completed"
                    if failure == "pending save" or failure == "completion save" and completed:
                        raise OSError("injected save failure")
                    if completed:
                        self.assertTrue(synced)
                    real_save(directory, record)
                    if failure == "reply" and completed:
                        raise OSError("injected reply failure")

                def unlink(name, *args, **kwargs):
                    if name.endswith(".bundle") and failure == "unlink":
                        self.assertEqual(self.intent()["pruning"]["operations"][-1]["state"], "pending")
                        raise OSError("injected unlink failure")
                    return real_unlink(name, *args, **kwargs)

                def sync(directories, kinds):
                    self.assertEqual(kinds, ["bundle"])
                    if failure == "directory sync":
                        raise OSError("injected sync failure")
                    real_sync(directories, kinds)
                    synced.append(True)

                with patch.object(self.inputs, "_save_settlement", side_effect=save), \
                     patch.object(build_retention.os, "unlink", side_effect=unlink), \
                     patch.object(self.retention, "_sync_deleted", side_effect=sync):
                    with self.assertRaisesRegex(OSError, "injected"):
                        self.release(plan)
                self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"]
                                 - (FILE_CHARGE_FLOOR if failure == "reply" else 0))
                if failure not in {"pending save", "reply"}:
                    stream = CountingStream(self.payload)
                    with self.assertRaisesRegex(ValueError, "pending explicit"):
                        self.inputs.admit(self.request, owner="controller", source=stream,
                                          source_size=len(self.payload), jobs=self.jobs)
                    self.assertEqual(stream.read_calls, 0)
                    with self.assertRaises(ValueError):
                        self.prune(plan)
                    with self.assertRaises(ValueError):
                        self.settle(plan)
                    self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])
                self.retention = self.reopen()
                with patch.object(self.retention, "_sync_deleted", wraps=self.retention._sync_deleted) as sync_again:
                    receipt = self.release(plan)
                    sync_again.assert_called_once()
                self.assertEqual(receipt, self.release(plan))
                self.assertEqual(receipt["current_charge_bytes"], plan["charge_bytes"] - FILE_CHARGE_FLOOR)

    def test_release_recovers_staged_first_pending_from_schema2_and3_and_staged_completion(self):
        self.inputs.max_bytes = 4 * self.reservation
        for number, (prior_prune, stage) in enumerate(((False, "pending"), (True, "pending"),
                                                      (False, "completed")), 1):
            with self.subTest(prior_prune=prior_prune, stage=stage):
                self.request = {**self.request, "request_id": f"{number:032x}"}
                self.terminal("succeeded")
                plan = self.settle()
                if prior_prune:
                    plan = self.prune(plan, ("source",))
                previous = self.intent()
                temporary = self.stage_release(plan, stage)
                self.assertEqual(self.intent()["charge_bytes"], previous["charge_bytes"])
                if stage == "pending":
                    self.assertEqual(self.intent(), previous)
                raw = temporary.read_bytes()
                staged = json.loads(raw)
                self.assertEqual(staged["schema"], 4)
                # No other operation adopts or resumes even a fully synced
                # completed temporary with the tempting lower charge.
                for operation in (self.admit, self.inspect, self.inputs.capacity_snapshot):
                    with self.assertRaises(ValueError):
                        if operation == self.inputs.capacity_snapshot:
                            with operation():
                                self.fail("temporary was accepted")
                        else:
                            operation()
                    self.assertEqual(temporary.read_bytes(), raw)
                self.retention = self.reopen()
                with self.assertRaises(ValueError):
                    self.release({"revision": "f" * 64})
                self.assertEqual(temporary.read_bytes(), raw)
                temporary.write_bytes(raw[:-1])
                with self.assertRaises(ValueError):
                    self.release(plan)
                temporary.write_bytes(raw)
                with patch.object(self.inputs, "_save_settlement", side_effect=OSError("redo save")):
                    with self.assertRaisesRegex(OSError, "redo save"):
                        self.release(plan)
                self.assertFalse(temporary.exists())
                self.assertEqual(self.intent()["charge_bytes"], previous["charge_bytes"])
                receipt = self.release(plan)
                self.assertEqual(receipt, self.release(plan))
                self.assertEqual(receipt["charge_bytes"], previous["charge_bytes"] - FILE_CHARGE_FLOOR)

    def test_release_staged_unsafe_unrelated_and_out_of_bounds_metadata_is_preserved(self):
        self.terminal("succeeded")
        plan = self.settle()
        original = self.path("inputs", ".upload.json").read_bytes()
        temporary = self.stage_release(plan)
        raw = temporary.read_bytes()
        record = json.loads(raw)
        alias = self.root / "staged-alias"
        for change in ("partial", "malformed", "noncanonical", "wrong revision", "old schema",
                       "wrong operation", "oversized", "mode", "symlink", "hardlink"):
            with self.subTest(change=change):
                temporary.unlink()
                temporary.write_bytes(raw)
                temporary.chmod(0o600)
                if change == "partial":
                    temporary.write_bytes(raw[:-1])
                elif change == "malformed":
                    temporary.write_bytes(b"not json")
                elif change == "noncanonical":
                    temporary.write_bytes(raw + b"\n")
                elif change == "wrong revision":
                    temporary.write_bytes(encode_document({**record, "settlement_revision": "f" * 64}))
                elif change == "old schema":
                    temporary.write_bytes(encode_document({**record, "schema": 3}))
                elif change == "wrong operation":
                    changed = copy.deepcopy(record)
                    changed["pruning"]["operations"][0]["kinds"] = ["log"]
                    temporary.write_bytes(encode_document(changed))
                elif change == "oversized":
                    temporary.write_bytes(b"x" * (build_inputs.MAX_PRUNING_INTENT_BYTES + 1))
                elif change == "mode":
                    temporary.chmod(0o644)
                elif change == "symlink":
                    temporary.unlink()
                    temporary.symlink_to(self.path("artifacts", ".bundle"))
                elif change == "hardlink":
                    os.link(temporary, alias)
                before = build_retention._identity(temporary.lstat())
                with self.assertRaises((ValueError, OSError)):
                    self.release(plan)
                self.assertEqual(build_retention._identity(temporary.lstat()), before)
                self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)
                if alias.exists():
                    alias.unlink()
        temporary.unlink()
        temporary.write_bytes(raw)
        temporary.chmod(0o600)
        for name in ("MAX_PRUNING_INTENT_BYTES", "MAX_RETAINED_METADATA_BYTES"):
            # Pending fits exactly, but completed's longer state does not.
            # Both transitions must fit before even discarding the temporary.
            with patch.object(build_retention, name, len(raw)):
                with self.assertRaises(ValueError):
                    self.release(plan)
            self.assertEqual(temporary.read_bytes(), raw)
        # Valid staged metadata cannot authorize changed payload evidence.
        self.path("logs", ".log").write_bytes(b"changed log evidence")
        with self.assertRaisesRegex(ValueError, "settled evidence changed"):
            self.release(plan)
        self.assertEqual(temporary.read_bytes(), raw)
        self.assertEqual(self.path("inputs", ".upload.json").read_bytes(), original)

    def test_release_bounds_and_replaced_payload_fail_before_credit(self):
        self.terminal("succeeded")
        plan = self.settle()
        before = self.intent()
        for module, name, bound in ((build_retention, "MAX_PRUNING_INTENT_BYTES", 1024),
                                    (build_retention, "MAX_PLAN_BYTES", 128),
                                    (build_retention, "MAX_RETAINED_METADATA_BYTES", 1024)):
            with self.subTest(name=name), patch.object(module, name, bound):
                with self.assertRaises(ValueError):
                    self.release(plan)
            self.assertEqual(self.intent(), before)
            self.assertTrue(self.path("artifacts", ".bundle").exists())
        replacement = self.root / "replacement-bundle"
        replacement.write_bytes(self.path("artifacts", ".bundle").read_bytes())
        replacement.chmod(0o600)
        real_save = self.inputs._save_settlement

        def replace_after_save(directory, record):
            real_save(directory, record)
            replacement.replace(self.path("artifacts", ".bundle"))

        with patch.object(self.inputs, "_save_settlement", side_effect=replace_after_save):
            with self.assertRaisesRegex(ValueError, "replaced before unlink"):
                self.release(plan)
        with self.assertRaisesRegex(ValueError, "payload evidence changed"):
            self.release(plan)
        self.assertEqual(self.intent()["charge_bytes"], plan["charge_bytes"])

    def test_schema4_rejects_missing_bundle_mixed_repeated_and_missing_release_operations(self):
        self.terminal("succeeded")
        self.release(self.settle())
        original = self.intent()
        for mutation in ("missing original", "mixed kinds", "duplicate bundle", "missing operation",
                         "pending earlier", "schema3"):
            changed = copy.deepcopy(original)
            if mutation == "missing original":
                changed["pruning"]["payloads"]["bundle"] = None
            elif mutation == "mixed kinds":
                changed["pruning"]["operations"][0]["kinds"] = ["bundle", "source"]
            elif mutation == "duplicate bundle":
                changed["pruning"]["operations"] *= 2
            elif mutation == "missing operation":
                changed["pruning"]["operations"][0]["kinds"] = ["source"]
            elif mutation == "pending earlier":
                changed["pruning"]["operations"] *= 2
                changed["pruning"]["operations"][0]["state"] = "pending"
            else:
                changed["schema"] = 3
            self.path("inputs", ".upload.json").write_bytes(encode_document(changed))
            stream = CountingStream(self.payload)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.inputs.admit(self.request, owner="controller", source=stream,
                                  source_size=len(self.payload), jobs=self.jobs)
            self.assertEqual(stream.read_calls, 0)
        self.path("inputs", ".upload.json").write_bytes(encode_document(original))
        self.inputs.max_records = 1
        with self.assertRaisesRegex(ValueError, "cannot be pruned"):
            self.admit({**self.request, "request_id": "b" * 32})


if __name__ == "__main__":
    unittest.main()
