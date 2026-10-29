"""Completed history archival with real local delivery and protected evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from lib import build_setup_history as history
from lib.build_protocol import encode_document
from lib.concurrency import resource_lock, resource_lock_path
from lib.operation_state import OperationStateStore
from tests.test_build_delivery import BuildDeliveryFixture


_POPEN = subprocess.Popen


class BuildSetupHistoryTests(BuildDeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.host = "target.example"
        self.host_root = self.root / "build-setups" / hashlib.sha256(self.host.encode()).hexdigest()
        self.host_root.mkdir(parents=True, mode=0o700)
        self.host_root.parent.chmod(0o700)
        self.attempt = self.host_root / ("a" * 32)
        self.attempt.mkdir(mode=0o700)
        self.journal = self.host_root / "setup.json"
        self.enterContext(patch("lib.concurrency.tempfile.gettempdir", return_value=str(self.root)))
        prepared = {"schema": 1, "plans": [dict(asdict(plan), source_path=str(plan.source_path)) for plan in self.plans]}
        self.write(self.attempt / "prepared.json", prepared)
        context = {"schema": 1, "host": self.host, "builder": "builder.example", "attempt": str(self.attempt),
                   "prepared_sha256": hashlib.sha256(encode_document(prepared)).hexdigest(), "profile_sha256": "e" * 64}
        self.workspace = self.attempt / "delivery"
        self.workspace.mkdir(mode=0o700)
        self.controller.workspace = str(self.workspace)
        self.controller.journal_path = str(self.workspace / "delivery.json")
        with OperationStateStore(str(self.journal)).ownership() as store:
            operation = store.begin("build_setup", self.host, "bootstrapping", context=context)
            store.transition(operation.operation_id, "delivering")
            receipt = self.controller.deliver(self.plans)
            context.update(delivery_receipt=receipt.receipt_path, expected_releases=[
                {key: getattr(entry, key) for key in ("application", "operation_id", "generation", "artifact_sha256")}
                for entry in receipt.applications
            ])
            self.operation = store.transition(operation.operation_id, "profile_applying", context=context)
            history.record_setup_success(str(self.attempt), self.operation)
            store.complete(operation.operation_id)
        self.archived = self.host_root / (history.ARCHIVE_PREFIX + self.attempt.name)

    @staticmethod
    def write(path, value):
        path.write_bytes(encode_document(value, maximum=history.MAX_PREPARED_BYTES))
        path.chmod(0o600)

    @staticmethod
    def snapshot(root):
        return {str(path.relative_to(root)): (path.stat().st_ino, path.stat().st_mode, path.read_bytes())
                for path in root.rglob("*") if path.is_file()}

    def maintain(self, revision=None):
        return history.maintain_completed_setup(self.journal, self.attempt.name, expected_revision=revision)

    def assert_refused(self, code=None, revision=None):
        with self.assertRaises(history.BuildSetupHistoryError) as caught:
            self.maintain(revision)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_inspection_and_archive_preserve_payloads_receipts_and_original_identities(self):
        before = self.snapshot(self.attempt)
        last = Path(str(self.journal) + ".last.json").read_bytes()
        with patch("subprocess.run", side_effect=AssertionError("No transport")), \
             patch("socket.socket", side_effect=AssertionError("No network")):
            report = self.maintain()
            self.assertEqual(self.maintain(), report)
            self.assertFalse(report["archived"])
            self.assertEqual(self.snapshot(self.attempt), before)
            self.assertEqual(history.count_retained_attempts(str(self.host_root)), 1)
            archived = self.maintain(report["revision"])
            self.assertEqual(archived, report | {"archived": True})
            self.assertFalse(self.attempt.exists())
            self.assertEqual(self.snapshot(self.archived), before)
            self.assertEqual(history.count_retained_attempts(str(self.host_root)), 0)
            self.assertEqual(self.maintain(report["revision"]), archived)
            self.assertEqual(self.maintain(), archived)
        self.assertEqual(Path(str(self.journal) + ".last.json").read_bytes(), last)
        self.assertEqual(archived["remote_state"], "unobserved")
        self.assertEqual(archived["payload_contents"], "unverified")
        encoded = encode_document(archived)
        for private in (str(self.root).encode(), b"commands", b"source_path", b"private-build-input"):
            self.assertNotIn(private, encoded)

    def test_archive_refuses_wrong_revision_and_still_running_controller_resource(self):
        report = self.maintain()
        self.assert_refused("revision_changed", "f" * 64)
        with resource_lock("setup-target", self.host):
            self.assert_refused("busy", report["revision"])
        self.assertTrue(self.attempt.is_dir())
        self.assertFalse(self.archived.exists())

    def test_resource_lock_precedes_setup_ownership_and_metadata_is_rechecked(self):
        report = self.maintain()

        @contextmanager
        def resource(namespace, host):
            self.assertEqual((namespace, host), ("setup-target", self.host))
            # Public setup owns this resource before its setup/delivery locks.
            with Path(str(self.journal) + ".lock").open("rb") as descriptor:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.attempt / history.COMPLETION_FILE
            proof = json.loads(path.read_bytes())
            proof["operation"]["context"]["profile_sha256"] = "f" * 64
            self.write(path, proof)
            yield

        with patch.object(history, "resource_lock", side_effect=resource):
            self.assert_refused("revision_changed", report["revision"])
        self.assertTrue(self.attempt.is_dir())
        self.assertFalse(self.archived.exists())

    def test_host_case_and_trailing_dot_use_the_public_setup_lock(self):
        path = self.attempt / history.COMPLETION_FILE
        original = json.loads(path.read_bytes())
        for host in ("TARGET.EXAMPLE", "target.example.", "TARGET.EXAMPLE."):
            with self.subTest(host=host):
                proof = json.loads(json.dumps(original))
                proof["operation"]["context"]["host"] = host
                proof["operation"]["resource"] = host
                self.write(path, proof)
                report = self.maintain()
                with resource_lock("setup-target", self.host):
                    self.assert_refused("busy", report["revision"])
                self.assertFalse(self.archived.exists())

    def test_unsafe_resource_lock_file_and_directory_have_fixed_refusals(self):
        report = self.maintain()
        path = Path(resource_lock_path("setup-target", self.host))
        os.mkfifo(path, 0o600)
        self.assert_refused("invalid_evidence", report["revision"])
        path.unlink()
        path.parent.chmod(0o755)
        self.assert_refused("invalid_evidence", report["revision"])
        path.parent.chmod(0o700)
        self.assertTrue(self.attempt.is_dir())
        self.assertFalse(self.archived.exists())

    def test_unfinished_setup_or_delivery_refuses_even_with_success_receipts(self):
        for path in (self.journal, self.workspace / "delivery.json"):
            with self.subTest(path=path):
                path.write_bytes(b"malformed but authoritative unfinished guard")
                self.assert_refused("unfinished_operation")
                path.unlink()

    def test_setup_and_delivery_ownership_are_respected_without_new_lock_files(self):
        for path in (Path(str(self.journal) + ".lock"), self.workspace / "delivery.json.lock"):
            with self.subTest(path=path), path.open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assert_refused("busy")

    def test_legacy_latest_success_is_preserved_before_host_result_rotates(self):
        (self.attempt / history.COMPLETION_FILE).unlink()
        report = self.maintain()
        self.assertFalse((self.attempt / history.COMPLETION_FILE).exists())
        self.maintain(report["revision"])
        self.assertTrue((self.archived / history.COMPLETION_FILE).is_file())
        self.write(Path(str(self.journal) + ".last.json"), {"a_later_result": True})
        self.assertEqual(self.maintain()["revision"], report["revision"])
        self.assertEqual(self.maintain(report["revision"])["revision"], report["revision"])

    def test_legacy_failed_or_unrelated_result_cannot_establish_completion(self):
        (self.attempt / history.COMPLETION_FILE).unlink()
        path = Path(str(self.journal) + ".last.json")
        result = json.loads(path.read_bytes())
        for change in ("failed", "unrelated"):
            with self.subTest(change=change):
                value = json.loads(json.dumps(result))
                if change == "failed":
                    value["outcome"] = "failed"
                else:
                    value["operation"]["context"]["attempt"] = str(self.host_root / ("b" * 32))
                self.write(path, value)
                self.assert_refused()
        self.assertTrue(self.attempt.exists())

    def test_legacy_success_proof_persistence_failure_prevents_rename(self):
        (self.attempt / history.COMPLETION_FILE).unlink()
        report = self.maintain()
        with patch.object(history, "write_json_atomic", side_effect=OSError("proof unavailable")):
            self.assert_refused("unavailable", report["revision"])
        self.assertTrue(self.attempt.is_dir())
        self.assertFalse(self.archived.exists())

    def test_completion_changes_invalidate_an_inspected_revision(self):
        report = self.maintain()
        path = self.attempt / history.COMPLETION_FILE
        proof = json.loads(path.read_bytes())
        proof["operation"]["context"]["profile_sha256"] = "f" * 64
        self.write(path, proof)
        self.assertNotEqual(self.maintain()["revision"], report["revision"])
        self.assert_refused("revision_changed", report["revision"])

    def test_conflicting_destination_is_never_replaced(self):
        report = self.maintain()
        self.archived.mkdir(mode=0o700)
        (self.archived / "keep").write_text("unrelated evidence")
        self.assert_refused(revision=report["revision"])
        self.assertEqual((self.archived / "keep").read_text(), "unrelated evidence")

    def test_parent_sync_failure_after_rename_recovers_from_original_revision(self):
        report = self.maintain()
        before = self.snapshot(self.attempt)
        with patch("lib.atomic_io._fsync_directory", side_effect=OSError("publication uncertain")):
            self.assert_refused("unavailable", report["revision"])
        self.assertFalse(self.attempt.exists())
        self.assertEqual(self.snapshot(self.archived), before)
        self.assertTrue(self.maintain(report["revision"])["archived"])

    def test_unsafe_modes_symlinks_hardlinks_and_changed_digests_refuse(self):
        path = self.attempt / history.COMPLETION_FILE
        raw = path.read_bytes()
        path.chmod(0o644)
        self.assert_refused()
        path.chmod(0o600)
        link = self.attempt / "extra-link"
        os.link(path, link)
        self.assert_refused()
        link.unlink()
        path.unlink()
        path.symlink_to(self.journal)
        self.assert_refused()
        path.unlink()
        path.write_bytes(raw)
        path.chmod(0o600)
        prepared_path = self.attempt / "prepared.json"
        prepared = json.loads(prepared_path.read_bytes())
        prepared["plans"][0]["request"]["components"][0]["commands"] = ["private changed input"]
        self.write(prepared_path, prepared)
        self.assert_refused()

    def test_cli_inspection_archive_and_refusals(self):
        package = self.root / "cli"
        shutil.copytree(Path(__file__).resolve().parents[1] / "lib", package / "lib",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        command = [sys.executable, "-B", "-m", "lib.build_setup_history", "--journal", str(self.journal),
                   "--attempt", self.attempt.name]
        options = {"capture_output": True, "stdin": subprocess.DEVNULL, "timeout": 10,
                   "env": dict(os.environ, TMPDIR=str(self.root)), "cwd": str(package)}
        # The fixture blocks host processes. Allow only these fixed, offline
        # Python CLI invocations with all writable state in the temporary tree.
        with patch("subprocess.Popen", _POPEN):
            inspected = subprocess.run(command, check=True, **options)
            report = json.loads(inspected.stdout)
            self.assertEqual(self.maintain(), report)
            self.assertEqual(list(package.rglob("__pycache__")), [])
            archived = subprocess.run(command + ["--archive", "--expected-revision", report["revision"]],
                                      check=True, **options)
            self.assertTrue(json.loads(archived.stdout)["archived"])
            self.assertEqual(list(package.rglob("__pycache__")), [])
            for extra in (["--archive"], ["--archive", "--expected-revision", "private"]):
                refused = subprocess.run(command + extra, **options)
                self.assertEqual(refused.returncode, 2)
                self.assertNotIn(b"private", refused.stderr)
                self.assertNotIn(str(self.root).encode(), refused.stderr)
            bytecode_enabled = subprocess.run([command[0], *command[2:]], **options)
            self.assertEqual(bytecode_enabled.returncode, 2)
            self.assertEqual(json.loads(bytecode_enabled.stderr)["error"], "bytecode_enabled_use_python_B")


if __name__ == "__main__":
    unittest.main()
