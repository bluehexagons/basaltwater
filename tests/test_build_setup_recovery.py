"""Offline recovery inspection; only disposable evidence and mocked clients."""

from __future__ import annotations

from contextlib import ExitStack
import copy
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib import build_setup_recovery as recovery
from lib.build_delivery import DeliveryRecoveryRequired
from lib.build_protocol import encode_document
from lib.operation_state import OperationStateStore
from tests.test_build_delivery import BuildDeliveryFixture


_POPEN = subprocess.Popen


def snapshot(root: Path) -> dict:
    """Exclude atime: reading a file may update it on the local filesystem."""
    result = {}
    for path in [root, *root.rglob("*")]:
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return result


class BuildSetupRecoveryTests(BuildDeliveryFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        host = "target.example"
        self.host_root = self.root / "build-setups" / hashlib.sha256(host.encode()).hexdigest()
        self.host_root.mkdir(parents=True, mode=0o700)
        self.host_root.parent.chmod(0o700)
        self.attempt = self.host_root / ("a" * 32)
        self.attempt.mkdir(mode=0o700)
        self.prepared = {"schema": 1, "plans": [dict(asdict(plan), source_path=str(plan.source_path)) for plan in self.plans]}
        self.write(self.attempt / "prepared.json", self.prepared)
        self.journal = self.host_root / "setup.json"
        self.context = {"schema": 1, "host": host, "builder": "builder.example", "attempt": str(self.attempt),
                        "prepared_sha256": hashlib.sha256(encode_document(self.prepared)).hexdigest(),
                        "profile_sha256": "e" * 64}
        with OperationStateStore(str(self.journal)).ownership() as store:
            self.setup_operation = store.begin("build_setup", host, "bootstrapping", context=self.context)
        self.workspace = self.attempt / "delivery"
        self.controller.workspace = str(self.workspace)
        self.controller.journal_path = str(self.workspace / "delivery.json")

    @staticmethod
    def write(path: Path, value: object) -> None:
        path.write_bytes(json.dumps(value).encode())
        path.chmod(0o600)

    def transition(self, phase: str, **kwargs) -> None:
        with OperationStateStore(str(self.journal)).ownership() as store:
            store.transition(self.setup_operation.operation_id, phase, **kwargs)

    def start_delivery(self) -> None:
        self.transition("delivering")
        self.workspace.mkdir(mode=0o700)

    def failed_delivery(self, action: str = "activate", index: int = 2) -> None:
        self.start_delivery()
        client = self.target if action != "submit" else self.builder
        original = getattr(client, action).side_effect

        def lost(*args, **fields):
            result = original(*args, **fields)
            application = args[0]["request_id"] if action == "submit" else fields.get("application", fields.get("deployment", {}).get("application"))
            selected = self.plans[index - 1].request["request_id"] if action == "submit" else f"app{index}"
            if application == selected:
                raise RuntimeError("lost reply: private diagnostic must never appear")
            return result

        getattr(client, action).side_effect = lost
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.transition("recovery_required", status="recovery_required")

    def completed_delivery(self) -> None:
        self.start_delivery()
        delivery = self.controller.deliver(self.plans)
        self.context.update(delivery_receipt=delivery.receipt_path, expected_releases=[
            {key: getattr(entry, key) for key in ("application", "operation_id", "generation", "artifact_sha256")}
            for entry in delivery.applications
        ])
        self.transition("profile_applying", context=self.context)
        self.transition("recovery_required", status="recovery_required", context=self.context)

    def inspect(self) -> dict:
        return recovery.inspect_build_setup_recovery(self.journal)

    def assert_refused(self) -> None:
        with self.assertRaises(recovery.BuildSetupInspectionError) as caught:
            self.inspect()
        self.assertIn(caught.exception.code, {"invalid_evidence", "unavailable", "busy"})
        self.assertLess(len(str(caught.exception)), 100)
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertNotIn("private", str(caught.exception))

    def test_interrupted_bootstrap_preserves_unknowns_and_explicitly_unobserved_state(self) -> None:
        for phase in ("bootstrapping", "recovery_required"):
            if phase == "recovery_required":
                self.transition(phase, status=phase)
            report = self.inspect()
            self.assertEqual(report["setup"]["phase"], phase)
            self.assertTrue(report["setup"]["unfinished_guard"])
            self.assertEqual(report["remote_state"], "unobserved")
            self.assertEqual(report["payload_contents"], "unverified")
            self.assertEqual(report["delivery"]["phase"], "unknown")
            for entry in report["applications"]:
                self.assertEqual(entry["local_status"], "unknown")
                self.assertIsNone(entry["operation_id"])
                self.assertIsNone(entry["generation"])
                self.assertIsNone(entry["activation_acknowledgement"])

    def test_lost_reserve_reply_never_invents_operation_or_generation(self) -> None:
        self.failed_delivery("reserve")
        first, second = self.inspect()["applications"]
        self.assertEqual(first["local_status"], "reserved")
        self.assertIsNotNone(first["operation_id"])
        self.assertEqual(second["local_status"], "reserving")
        self.assertEqual(second["unresolved_intent"], "reserve")
        self.assertIsNone(second["operation_id"])
        self.assertIsNone(second["generation"])
        self.assertEqual(second["request_id"], self.plans[1].request["request_id"])
        self.assertEqual(second["idempotency_key"], self.plans[1].idempotency_key)

    def test_completed_delivery_and_final_profile_failure_remain_unfinished(self) -> None:
        self.completed_delivery()
        report = self.inspect()
        self.assertTrue(report["setup"]["unfinished_guard"])
        self.assertEqual(report["setup"]["status"], "recovery_required")
        self.assertTrue(report["delivery"]["successful_last_result"])
        self.assertTrue(report["delivery"]["completed_receipt"])
        self.assertFalse(report["delivery"]["unfinished_guard"])
        self.assertTrue(all(entry["activation_acknowledgement"]["status"] == "completed"
                            for entry in report["applications"]))
        self.assertEqual(report["remote_state"], "unobserved")
        # Saving the profile_applying transition itself can fail: the exception
        # handler may then persist the receipt in recovery_required directly.
        marker = json.loads(self.journal.read_bytes())
        marker["phases"].remove("profile_applying")
        self.write(self.journal, marker)
        self.assertEqual(self.inspect(), report)

    def test_lost_stage_reply_retains_exact_artifact_without_stage_acknowledgement(self) -> None:
        self.failed_delivery("stage")
        entry = self.inspect()["applications"][1]
        self.assertEqual(entry["local_status"], "staging")
        self.assertEqual(entry["unresolved_intent"], "stage")
        self.assertIsNone(entry["stage_acknowledgement"])
        self.assertIsNone(entry["activation_acknowledgement"])
        self.assertEqual(entry["artifact_sha256"], self.artifacts[entry["request_id"]][1]["sha256"])

    def test_lost_submit_reply_retains_request_without_inventing_build_state(self) -> None:
        self.failed_delivery("submit")
        entry = self.inspect()["applications"][1]
        self.assertEqual(entry["local_status"], "submitting")
        self.assertEqual(entry["unresolved_intent"], "submit")
        self.assertIsNone(entry["build_state"])
        self.assertIsNone(entry["artifact_sha256"])
        self.assertEqual(entry["request_id"], self.plans[1].request["request_id"])

    def test_unfinished_delivery_marker_takes_precedence_over_successful_result(self) -> None:
        self.completed_delivery()
        last = json.loads(Path(self.controller.journal_path + ".last.json").read_bytes())
        self.write(Path(self.controller.journal_path), last["operation"])
        report = self.inspect()
        self.assertTrue(report["delivery"]["unfinished_guard"])
        self.assertTrue(report["delivery"]["successful_last_result"])
        self.assertEqual(report["remote_state"], "unobserved")
        last["operation"]["operation_id"] = "11111111-1111-4111-8111-111111111111"
        self.write(Path(self.controller.journal_path + ".last.json"), last)
        self.assert_refused()

    def test_every_saved_mutation_intent_remains_local_evidence(self) -> None:
        self.failed_delivery("activate")
        report = self.inspect()
        first, second = report["applications"]
        self.assertEqual(first["local_status"], "completed")
        self.assertEqual(first["activation_acknowledgement"]["status"], "completed")
        self.assertEqual(second["local_status"], "activating")
        self.assertEqual(second["unresolved_intent"], "activate")
        self.assertIsNone(second["activation_acknowledgement"])
        self.assertEqual(second["stage_acknowledgement"]["status"], "staged")
        encoded = encode_document(report)
        for secret in (b"private-build-input", b"go build", str(self.root).encode(), b"source_path", b"commands", b"env"):
            self.assertNotIn(secret, encoded)
        self.assertLessEqual(len(encoded) + 1, recovery.MAX_REPORT_BYTES)

    def test_repeated_inspection_cannot_write_create_sync_spawn_connect_or_open_payloads(self) -> None:
        self.failed_delivery()
        sibling = self.host_root / ("f" * 32)
        sibling.mkdir(mode=0o700)
        self.write(sibling / "prepared.json", {"private": "unrelated attempt"})
        before = snapshot(self.root)
        real_open = os.open
        opened = []
        allowed = {"setup.json", "setup.json.lock", "prepared.json", "delivery.json", "delivery.json.lock",
                   "delivery.json.last.json", "receipt-" + self.plans[0].idempotency_key + ".json"}

        def readonly(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.assertTrue(flags & os.O_NOFOLLOW)
            if not flags & os.O_DIRECTORY:
                self.assertIn(path, allowed)
                self.assertIn("dir_fd", kwargs)
            opened.append(path)
            return real_open(path, flags, *args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(recovery.os, "open", side_effect=readonly))
            for name in ("write", "mkdir", "makedirs", "fsync", "fdatasync", "sync", "rename", "replace",
                         "unlink", "remove", "chmod", "chown", "link", "symlink", "truncate", "ftruncate"):
                stack.enter_context(patch.object(os, name, side_effect=AssertionError("Mutation forbidden")))
            for name in ("builtins.open", "io.open", "os.scandir", "os.listdir",
                         "subprocess.Popen", "subprocess.run", "socket.socket", "socket.getaddrinfo",
                         "lib.build_client.BuildHostClient.__init__", "lib.build_target_client.BuildTargetClient.__init__",
                         "lib.operation_state.OperationStateStore.__init__", "lib.operation_state.OperationStateStore.ownership",
                         "lib.operation_state.OperationStateStore._acquire"):
                stack.enter_context(patch(name, side_effect=AssertionError("Effect forbidden")))
            first = self.inspect()
            self.assertEqual(self.inspect(), first)
        self.assertEqual(snapshot(self.root), before)
        self.assertNotIn(sibling.name, opened)
        self.assertEqual([name for name in opened if name.endswith(".lock")],
                         ["setup.json.lock", "delivery.json.lock"] * 2)

    def test_busy_and_absent_setup_and_delivery_locks_refuse_without_replacement(self) -> None:
        self.failed_delivery()
        for lock in (Path(str(self.journal) + ".lock"), self.workspace / "delivery.json.lock"):
            with self.subTest(lock=lock.name):
                with lock.open("rb") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaisesRegex(recovery.BuildSetupInspectionError, "busy"):
                        self.inspect()
                original = lock.read_bytes()
                lock.unlink()
                self.assert_refused()
                self.assertFalse(lock.exists())
                lock.write_bytes(original)
                lock.chmod(0o600)

    def test_missing_phase_evidence_refuses_and_does_not_repair(self) -> None:
        self.completed_delivery()
        paths = [self.attempt / "prepared.json", self.workspace / "delivery.json.last.json",
                 Path(self.context["delivery_receipt"]), self.journal]
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                self.assert_refused()
                self.assertFalse(path.exists())
                path.write_bytes(original)
                path.chmod(0o600)
        self.workspace.rename(self.attempt / "retained")
        self.assert_refused()
        self.assertFalse(self.workspace.exists())

    def test_interrupted_delivery_initialization_refuses_instead_of_guessing(self) -> None:
        self.transition("delivering")
        self.assert_refused()
        self.assertFalse(self.workspace.exists())
        self.workspace.mkdir(mode=0o700)
        self.assert_refused()
        self.assertEqual(list(self.workspace.iterdir()), [])
        (self.workspace / "delivery.json.lock").touch(mode=0o600)
        self.assert_refused()
        self.assertEqual([item.name for item in self.workspace.iterdir()], ["delivery.json.lock"])

    def test_unsafe_directory_file_symlink_hardlink_and_ownership_refuse(self) -> None:
        self.failed_delivery()
        for directory in (self.root, self.host_root, self.attempt, self.workspace):
            mode = directory.stat().st_mode & 0o777
            directory.chmod(0o777)
            self.assert_refused()
            directory.chmod(mode)
        for path in (self.journal, Path(str(self.journal) + ".lock"), self.attempt / "prepared.json",
                     self.workspace / "delivery.json", self.workspace / "delivery.json.lock"):
            with self.subTest(path=path.name):
                path.chmod(0o640)
                self.assert_refused()
                path.chmod(0o600)
                link = path.with_name(path.name + ".alias")
                os.link(path, link)
                self.assert_refused()
                link.unlink()
                path.rename(link)
                path.symlink_to(link)
                self.assert_refused()
                path.unlink()
                link.rename(path)
        self.workspace.rename(self.attempt / "original-delivery")
        self.workspace.symlink_to(self.attempt / "original-delivery", target_is_directory=True)
        self.assert_refused()
        self.workspace.unlink()
        (self.attempt / "original-delivery").rename(self.workspace)
        real_fstat = os.fstat

        def wrong_owner(descriptor):
            info = real_fstat(descriptor)
            fields = list(info)
            fields[4] = os.geteuid() + 10000
            return os.stat_result(fields)

        with patch.object(recovery.os, "fstat", side_effect=wrong_owner):
            self.assert_refused()

    def test_malformed_oversized_duplicate_and_unsupported_evidence_is_safe(self) -> None:
        self.failed_delivery()
        for path, maximum in ((self.journal, recovery.MAX_STATE_BYTES),
                              (self.attempt / "prepared.json", recovery.MAX_PREPARED_BYTES),
                              (self.workspace / "delivery.json", recovery.MAX_STATE_BYTES)):
            original = path.read_bytes()
            for raw in (b'{"private-secret":', b'{"schema":1,"schema":1}', b'NaN', b'null', b'[]', b'"private-secret"',
                        b'[' * 1100, b'\xff'):
                with self.subTest(path=path.name, raw=raw[:20]):
                    path.write_bytes(raw)
                    self.assert_refused()
            with path.open("wb") as stream:
                stream.truncate(maximum + 1)
            real_read = os.read
            oversized_inode = path.stat().st_ino

            def bounded_read(descriptor, size):
                self.assertNotEqual(os.fstat(descriptor).st_ino, oversized_inode,
                                    "Oversized evidence must not be read")
                self.assertLessEqual(size, 64 * 1024)
                return real_read(descriptor, size)

            with patch.object(recovery.os, "read", side_effect=bounded_read):
                self.assert_refused()
            path.write_bytes(original)
        original = json.loads(self.journal.read_bytes())
        for key, value in (("schema_version", 2), ("schema_version", True), ("phase", "private-secret"),
                           ("operation_type", "other"), ("started_at", "private-secret"),
                           ("extra", "private-secret")):
            self.write(self.journal, original | {key: value})
            self.assert_refused()

    def test_identity_digest_acknowledgement_and_receipt_conflicts_refuse(self) -> None:
        self.completed_delivery()
        prepared_path = self.attempt / "prepared.json"
        original = json.loads(prepared_path.read_bytes())
        changed = copy.deepcopy(original)
        changed["plans"][0]["request"]["components"][0]["env"]["BUILD_VALUE"] = "changed-private-input"
        self.write(prepared_path, changed)
        self.assert_refused()  # Canonical prepared digest binds even omitted private fields.
        self.write(prepared_path, original)
        last_path = self.workspace / "delivery.json.last.json"
        last = json.loads(last_path.read_bytes())
        for key, value in (("request_sha256", "f" * 64), ("deployment_sha256", "f" * 64),
                           ("idempotency_key", "f" * 32), ("generation", True), ("source", {}),
                           ("operation_id", self.plans[0].request["request_id"]), ("extra", "private-secret"),
                           ("stage_receipt", {}), ("activation_receipt", {})):
            changed = copy.deepcopy(last)
            changed["operation"]["context"]["applications"][0][key] = value
            self.write(last_path, changed)
            self.assert_refused()
        self.write(last_path, last)
        receipt_path = Path(self.context["delivery_receipt"])
        receipt = json.loads(receipt_path.read_bytes())
        receipt["applications"][1]["artifact_sha256"] = "f" * 64
        self.write(receipt_path, receipt)
        self.assert_refused()

    def test_untrusted_attempt_paths_and_expected_release_identities_refuse(self) -> None:
        original = json.loads(self.journal.read_bytes())
        for attempt in (str(self.root), str(self.attempt) + "/../" + self.attempt.name,
                        str(self.host_root / ("f" * 32)), "/private/unrelated"):
            changed = copy.deepcopy(original)
            changed["context"]["attempt"] = attempt
            self.write(self.journal, changed)
            self.assert_refused()
        self.write(self.journal, original)
        self.completed_delivery()
        marker = json.loads(self.journal.read_bytes())
        for key, value in (("delivery_receipt", "/private/credential"), ("expected_releases", []),
                           ("host", "other.example"), ("prepared_sha256", "0" * 64)):
            changed = copy.deepcopy(marker)
            changed["context"][key] = value
            self.write(self.journal, changed)
            self.assert_refused()

    def test_changed_file_identity_and_replacement_during_read_refuse(self) -> None:
        real_read = os.read
        changed = False

        def read(descriptor, size):
            nonlocal changed
            data = real_read(descriptor, size)
            if data and not changed:
                changed = True
                # Same bytes, different inode: descriptors alone are insufficient.
                old = self.journal.with_name("old.json")
                self.journal.rename(old)
                self.journal.write_bytes(old.read_bytes())
                self.journal.chmod(0o600)
            return data

        with patch.object(recovery.os, "read", side_effect=read):
            self.assert_refused()

    def test_application_count_and_projection_are_bounded_and_ids_unique(self) -> None:
        prepared = json.loads((self.attempt / "prepared.json").read_bytes())
        base = prepared["plans"][0]
        plans = []
        for index in range(1, 18):
            plan = copy.deepcopy(base)
            plan["request"]["request_id"] = f"{index:032x}"
            plan["idempotency_key"] = f"{index + 100:032x}"
            plan["deployment"]["application"] = f"app{index}"
            plan["deployment"]["components"][0]["domain"] = f"app{index}.example"
            plans.append(plan)

        def save(selected):
            prepared["plans"] = selected
            self.write(self.attempt / "prepared.json", prepared)
            marker = json.loads(self.journal.read_bytes())
            marker["context"]["prepared_sha256"] = hashlib.sha256(encode_document(prepared)).hexdigest()
            self.write(self.journal, marker)

        save(plans[:16])
        report = self.inspect()
        self.assertEqual(len(report["applications"]), 16)
        self.assertLessEqual(len(encode_document(report)) + 1, recovery.MAX_REPORT_BYTES)
        save(plans)
        self.assert_refused()
        for key in ("application", "request_id", "idempotency_key"):
            selected = copy.deepcopy(plans[:2])
            parent = "deployment" if key == "application" else "request" if key == "request_id" else None
            if parent:
                selected[1][parent][key] = selected[0][parent][key]
            else:
                selected[1][key] = selected[0][key]
            save(selected)
            self.assert_refused()

    def test_cold_copy_cli_has_bounded_output_no_bytecode_and_safe_errors(self) -> None:
        self.failed_delivery()
        with tempfile.TemporaryDirectory() as temporary:
            cold = Path(temporary)
            project = Path(__file__).resolve().parents[1]
            for package in ("lib", "common"):
                shutil.copytree(project / package, cold / package,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            before = snapshot(cold)
            evidence_before = snapshot(self.root)
            # Deliberate isolated interpreter proof; all producers above used
            # mocked transports and the copied package contains no private state.
            environment = {**os.environ, "PYTHONPATH": str(cold)}
            environment.pop("PYTHONPYCACHEPREFIX", None)
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            with patch("subprocess.Popen", _POPEN):
                for args, code in ((["--journal", str(self.journal)], 0),
                                   (["--journal", str(self.root / "private-secret" / "missing" / "setup.json")], 2),
                                   (["--private-secret-argument"], 2)):
                    result = subprocess.run([sys.executable, "-B", "-m", "lib.build_setup_recovery", *args],
                                            cwd=cold, env=environment, capture_output=True, timeout=20, check=False)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertLessEqual(len(result.stdout) + len(result.stderr), recovery.MAX_REPORT_BYTES)
                    self.assertNotIn(b"private-secret", result.stderr)
                    self.assertNotIn(b"/private/", result.stderr)
                    if code == 0:
                        self.assertEqual(json.loads(result.stdout), self.inspect())
                    else:
                        self.assertEqual(json.loads(result.stderr)["schema_version"], 1)
                self.assertEqual(snapshot(cold), before)
                result = subprocess.run([sys.executable, "-m", "lib.build_setup_recovery", "--journal", str(self.journal)],
                                        cwd=cold, env={**environment, "PYTHONDONTWRITEBYTECODE": "1"},
                                        capture_output=True, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), self.inspect())
                self.assertEqual(snapshot(cold), before)
                # Ordinary package import honestly uses Python's cache behavior.
                imported = subprocess.run([sys.executable, "-c", "import lib.build_setup_recovery"], cwd=cold,
                                          env=environment, capture_output=True, timeout=20, check=False)
                self.assertEqual(imported.returncode, 0, imported.stderr)
                self.assertTrue(list(cold.rglob("*.pyc")))
            self.assertEqual(snapshot(self.root), evidence_before)


if __name__ == "__main__":
    unittest.main()
