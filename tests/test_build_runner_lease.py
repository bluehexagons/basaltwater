"""Exact nested admission, kernel lease transfer and cancellation boundaries."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import build_runner_lease as runner
from lib import build_nested
from lib.build_client import BuildHostClient
from lib.build_host_service import BuildHostService
from lib.build_jobs import BuildJobStore
from lib.build_protocol import encode_document, request_digest
from tests.test_build_host_service import build_policy, build_request, header
from tests.test_build_client import build_request as client_request


def proof_for(request: dict, *, pid: int = 12345) -> dict:
    return {"worker_pid": pid, "request_id": request["request_id"],
            "request_sha256": request_digest(request), "token": "d" * 64}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        key, hosts = self.root / "identity", self.root / "known_hosts"
        key.write_bytes(b"fixture key")
        key.chmod(0o600)
        hosts.write_text("builder.example.net ssh-ed25519 AAAAFIXTURE\n")
        hosts.chmod(0o600)
        self.client = self.enterContext(BuildHostClient(
            host="builder.example.net", username="controller", identity_file=key,
            known_hosts_file=hosts, port=22, timeout=5))
        self.source = self.root / "source"
        self.source.write_bytes(b"verified fixture snapshot")
        self.request = client_request(snapshot_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_local_proof_is_optional_and_does_not_change_the_ssh_endpoint(self):
        for proof in (None, proof_for(self.request)):
            with self.subTest(proof=proof), \
                 patch.object(runner, "submission_proof", return_value=proof) as discover, \
                 patch.object(self.client, "_call", return_value=({"receipt": True}, None)) as call:
                self.assertEqual(self.client.submit(self.request, self.source), {"receipt": True})
            discover.assert_called_once_with("builder.example.net", self.request)
            fields = {"request": self.request, "source_size": self.source.stat().st_size}
            if proof is not None:
                fields["runner_lease"] = proof
            self.assertEqual(call.call_args.args, ("submit", fields))
            self.assertEqual(self.client._remote_command, "basaltwater-build-v1")

    def test_failed_local_handoff_never_enqueues_and_target_transport_never_borrows(self):
        with patch.object(runner, "submission_proof", side_effect=ValueError("missing guardian")), \
             patch.object(self.client, "_call") as call, self.assertRaises(ValueError):
            self.client.submit(self.request, self.source)
        call.assert_not_called()
        with patch.object(self.client, "_remote_command", "basaltwater-target-v1"), \
             patch.object(runner, "submission_proof") as discover, \
             patch.object(self.client, "_call", return_value=({}, None)):
            self.client.submit(self.request, self.source)
        discover.assert_not_called()

    def test_workflow_environment_cannot_replace_real_runner_ancestry(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TRACKING_ID": "fixture",
                                     "BASALTWATER_RUNNER_PID": "12345"}), \
             patch.object(runner.os, "getpid", return_value=2), \
             patch.object(runner.os, "readlink", return_value="/usr/bin/python3"), \
             patch.object(runner.Path, "read_text", return_value="2 (python3) S 1 0 0"), \
             patch.object(runner, "_connect") as connect:
            self.assertIsNone(runner.submission_proof("builder.example.net", self.request))
        connect.assert_not_called()


class ExactClaimTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o700)
        self.jobs = BuildJobStore(self.root / "jobs.json", clock=lambda: 100)
        self.first, self.nested = build_request("a" * 32), build_request("b" * 32)
        for request in (self.first, self.nested):
            self.jobs.submit(request, owner="controller")

    def test_exact_claim_cannot_run_an_older_job_or_a_different_owner_or_digest(self):
        with self.jobs.worker() as lease:
            for fields in ({"owner": "other"}, {"request_sha256": "0" * 64}, {"request_id": None}):
                selection = {"request_id": self.nested["request_id"], "owner": "controller",
                             "request_sha256": request_digest(self.nested), **fields}
                with self.subTest(fields=fields), self.assertRaises((ValueError, PermissionError)):
                    lease.claim(**selection)
                self.assertEqual(self.jobs.status(self.nested["request_id"], owner="controller")["state"], "queued")
            claim = lease.claim(request_id=self.nested["request_id"], owner="controller",
                                request_sha256=request_digest(self.nested))
            self.assertEqual(claim["request_id"], self.nested["request_id"])
            self.assertEqual(self.jobs.status(self.first["request_id"], owner="controller")["state"], "queued")

    def test_fifo_excludes_all_retained_nested_intents(self):
        with self.jobs.worker() as lease:
            claim = lease.claim(excluded_requests=frozenset({
                (self.first["request_id"], "controller", request_digest(self.first))}))
        self.assertEqual(claim["request_id"], self.nested["request_id"])

    def test_selected_claim_still_respects_drain_and_interrupted_work(self):
        selection = {"request_id": self.nested["request_id"], "owner": "controller",
                     "request_sha256": request_digest(self.nested)}
        self.jobs.set_draining(True)
        with self.jobs.worker() as lease:
            self.assertIsNone(lease.claim(**selection))
        self.jobs.set_draining(False)
        with self.jobs.worker() as lease:
            lease.claim()
        with self.jobs.worker() as lease, self.assertRaisesRegex(RuntimeError, "interrupted"):
            lease.claim(**selection)


class NestedAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o700)
        roots = {}
        for name in ("state", "inputs", "scratch", "artifacts", "logs"):
            path = self.root / name
            path.mkdir(mode=0o700)
            roots[name] = str(path)
        self.config = {"roots": roots, "max_records": 2}
        self.requests = build_nested.NestedRequests(self.config)
        self.jobs = BuildJobStore(Path(roots["state"]) / "jobs.json")
        self.request = build_request()
        self.proof = proof_for(self.request)
        self.enterContext(patch.object(build_nested, "verify_proof", side_effect=lambda proof, request: runner.validate_proof(proof)))
        self.enterContext(patch.object(build_nested, "drained", return_value=False))

    def register(self):
        self.requests.register(self.request, self.proof, owner="controller")
        self.jobs.submit(self.request, owner="controller")

    def test_registration_is_exact_private_bounded_and_does_not_pollute_job_accounting(self):
        self.register()
        before = (Path(self.requests.path) / (self.request["request_id"] + ".json")).read_bytes()
        self.requests.register(self.request, self.proof, owner="controller")
        with self.assertRaises(ValueError):
            self.requests.register(self.request, {**self.proof, "token": "e" * 64}, owner="controller")
        self.assertEqual((Path(self.requests.path) / (self.request["request_id"] + ".json")).read_bytes(), before)
        self.assertEqual(self.jobs.capacity_snapshot()[0]["jobs"][self.request["request_id"]]["state"], "queued")
        with self.requests.fifo_guard() as excluded:
            self.assertEqual(excluded, frozenset({
                (self.request["request_id"], "controller", request_digest(self.request))}))
        self.assertEqual(Path(self.requests.path).stat().st_mode & 0o777, 0o700)
        (Path(self.requests.path) / ("f" * 32 + ".json")).write_bytes(b"{}")
        third = build_request("c" * 32)
        with self.assertRaises(ValueError):
            self.requests.register(third, proof_for(third), owner="controller")

    def test_parent_loss_and_drain_cancel_queued_intent_before_any_execution(self):
        for reason in ("lost", "drain"):
            with self.subTest(reason=reason):
                request = build_request(("b" if reason == "drain" else "a") * 32)
                self.request, self.proof = request, proof_for(request)
                self.register()
                execute = Mock()
                with patch.object(build_nested, "borrow", side_effect=ConnectionRefusedError), \
                     patch.object(build_nested, "drained", return_value=reason == "drain"):
                    self.requests.run_pending(self.jobs, execute)
                self.assertEqual(self.jobs.status(request["request_id"], owner="controller")["state"], "cancelled")
                execute.assert_not_called()

    def test_rejected_owner_or_digest_collision_cannot_cancel_or_exclude_the_original_job(self):
        for kind in ("owner", "digest"):
            with self.subTest(kind=kind):
                request = build_request(("b" if kind == "owner" else "a") * 32)
                self.jobs.submit(request, owner="original")
                attempted = dict(request)
                if kind == "digest":
                    attempted = {**request, "project": "different"}
                self.requests.register(attempted, proof_for(attempted),
                                       owner="other" if kind == "owner" else "original")
                with self.assertRaises((PermissionError, ValueError)):
                    self.jobs.submit(attempted, owner="other" if kind == "owner" else "original")
                execute = Mock()
                self.assertFalse(self.requests.run_pending(self.jobs, execute))
                execute.assert_not_called()
                with self.requests.fifo_guard() as excluded, self.jobs.worker() as lease:
                    claim = lease.claim(excluded_requests=excluded)
                    self.assertEqual(claim["owner"], "original")
                    self.assertEqual(claim["request"], request)
                    lease.finish(request["request_id"], outcome="failed", work_stopped=True)

    def test_exact_execution_and_store_drain_confirm_cleanup_without_hanging_parent(self):
        self.register()
        parent_cancelled = Mock(return_value=False)
        @contextmanager
        def borrowed(_proof):
            yield SimpleNamespace(cancelled=parent_cancelled)
        execute = Mock(return_value=None)
        with patch.object(build_nested, "borrow", side_effect=borrowed):
            self.assertTrue(self.requests.run_pending(self.jobs, execute))
        execute.assert_called_once_with(request_id=self.request["request_id"], owner="controller",
                                         request_sha256=self.proof["request_sha256"], cancelled=parent_cancelled)
        self.assertEqual(self.jobs.status(self.request["request_id"], owner="controller")["state"], "cancelled")

    def test_unsafe_or_incomplete_evidence_stops_instead_of_starting_jobs(self):
        self.register()
        target = Path(self.requests.path) / (self.request["request_id"] + ".json")
        for kind in ("public", "hardlink", "symlink", "partial"):
            with self.subTest(kind=kind):
                saved = target.read_bytes()
                if kind == "public":
                    target.chmod(0o644)
                elif kind == "hardlink":
                    os.link(target, self.root / "alias")
                elif kind == "symlink":
                    target.unlink()
                    target.symlink_to(self.root / "alias")
                else:
                    (Path(self.requests.path) / ".uncertain.tmp").touch(mode=0o600)
                with self.assertRaises((ValueError, OSError)):
                    self.requests.run_pending(self.jobs, Mock())
                if kind == "symlink":
                    target.unlink()
                    target.write_bytes(saved)
                target.chmod(0o600)
                (self.root / "alias").unlink(missing_ok=True)
        self.assertEqual(self.jobs.status(self.request["request_id"], owner="controller")["state"], "queued")

    def test_ssh_policy_precedes_proof_and_proof_precedes_source_admission(self):
        admit, nested = Mock(), Mock()
        service = BuildHostService(jobs=self.jobs, load_policy=build_policy, admit_source=admit,
                                   tool_versions=dict, logs_root=self.config["roots"]["logs"],
                                   artifacts_root=self.config["roots"]["artifacts"], nested_admission=nested)
        nested.side_effect = ValueError("untrusted proof")
        for owner in ("unknown", "alice@example"):
            output = BytesIO()
            service.handle(header("submit", request=self.request, source_size=1, runner_lease=self.proof),
                           principal=owner, input_stream=BytesIO(b"x"), output_stream=output)
            self.assertFalse(json.loads(output.getvalue())["ok"])
            admit.assert_not_called()
        nested.assert_called_once_with(self.request, self.proof, owner="alice@example")

    def test_scanning_holds_the_publication_lock_but_execution_does_not(self):
        self.register()
        checked = []
        original = self.requests._names
        def names(directory):
            lock = os.open(Path(self.requests.path) / "requests.lock", os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked.append(True)
                return original(directory)
            finally:
                os.close(lock)
        @contextmanager
        def borrowed(_proof):
            yield SimpleNamespace(cancelled=lambda: False)
        def execute(**_selection):
            lock = os.open(Path(self.requests.path) / "requests.lock", os.O_RDWR)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(lock)
        with patch.object(self.requests, "_names", side_effect=names), \
             patch.object(build_nested, "borrow", side_effect=borrowed):
            self.assertTrue(self.requests.run_pending(self.jobs, execute))
        self.assertEqual(checked, [True])

    def test_missing_published_record_is_uncertain_not_an_empty_store(self):
        self.register()
        with patch.object(self.requests, "_read", side_effect=FileNotFoundError), \
             self.assertRaisesRegex(ValueError, "disappeared"):
            self.requests.run_pending(self.jobs, Mock())


class KernelHandoffTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o700)
        self.path = self.root / "slot.lock"
        self.path.write_bytes(b"parent lease retained\n")
        self.path.chmod(0o660)
        self.lock = os.open(self.path, os.O_RDWR)
        self.addCleanup(os.close, self.lock)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.worker = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE)
        self.addCleanup(self.stop_worker)
        self.parent = os.pidfd_open(self.worker.pid)
        self.addCleanup(os.close, self.parent)
        self.enterContext(patch.object(runner, "_runner_group", return_value="managed-fixture"))
        self.enterContext(patch.object(runner, "_descends_from", return_value=True))
        self.enterContext(patch.object(runner, "_peer", return_value=(os.getpid(), 0, 0)))
        self.enterContext(patch.object(runner.os, "geteuid", return_value=0))
        self.enterContext(patch.object(runner.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0)))
        self.enterContext(patch("lib.build_coordinator.open_slot", side_effect=lambda: os.open(self.path, os.O_RDWR)))
        self.broker = runner.GuardianBroker(self.lock, self.parent, self.worker.pid)
        self.stop = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self.serve)
        self.thread.start()
        self.addCleanup(self.close_broker)

    def stop_worker(self):
        if self.worker.poll() is None:
            self.worker.kill()
        self.worker.communicate(timeout=5)

    def serve(self):
        try:
            while not self.stop.is_set():
                watched = [self.broker.server]
                if self.broker.borrower is not None:
                    watched.append(self.broker.borrower)
                available = select.select(watched, [], [], .05)[0]
                if self.broker.server in available:
                    self.broker.accept()
                if self.broker.borrower is not None and self.broker.borrower in available:
                    self.broker.finish_borrow()
        except BaseException as error:
            self.errors.append(error)

    def close_broker(self):
        self.stop.set()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        self.broker.close()
        self.assertEqual(self.errors, [])

    def registered(self):
        proof = proof_for(build_request(), pid=self.worker.pid)
        with runner._connect(proof) as connection:
            connection.sendall(encode_document({"op": "register", "proof": proof}, maximum=runner.MAX_MESSAGE))
            return runner.validate_proof(runner._message(connection))

    def test_only_real_locked_descriptor_is_accepted_and_closing_borrow_keeps_parent_lock(self):
        proof = self.registered()
        with runner.borrow(proof) as lease:
            self.assertFalse(lease.cancelled())
            self.assertFalse(os.get_inheritable(lease.descriptor))
            fresh = os.open(self.path, os.O_RDWR)
            try:
                with self.assertRaises(ValueError):
                    runner._locked_descriptor(fresh)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fresh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fresh)
        self.assertFalse(self.broker.uncertain)
        fresh = os.open(self.path, os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fresh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fresh)
        self.assertTrue(self.path.read_bytes())

    def test_forged_proof_and_other_job_cannot_borrow(self):
        proof = self.registered()
        with self.assertRaises(ValueError):
            runner.verify_proof({**proof, "token": "0" * 64}, build_request())
        with patch.object(runner, "_descends_from", return_value=False):
            with self.assertRaises(ValueError):
                self.registered()
        self.assertIsNone(self.broker.borrower)

    def test_unrelated_uid_is_rejected_before_reading_a_message(self):
        proof = proof_for(build_request(), pid=self.worker.pid)
        with patch.object(runner, "_peer", return_value=(os.getpid(), 1234, 1234)), \
             patch.object(runner, "_message") as message:
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                connection.connect(runner._address(self.worker.pid))
                connection.settimeout(1)
                self.assertEqual(connection.recv(32), b"")
        message.assert_not_called()

    def test_direct_parent_pidfd_cancels_even_before_guardian_notification(self):
        with runner.borrow(self.registered()) as lease:
            self.stop_worker()
            self.assertTrue(lease.cancelled())

    def test_uncertain_borrow_does_not_acknowledge_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            with runner.borrow(self.registered()):
                raise RuntimeError("uncertain")
        # Synchronize with the guardian without a timing guess.
        self.stop.set()
        self.thread.join(timeout=5)
        if self.broker.borrower is not None:
            self.broker.finish_borrow()
        self.assertTrue(self.broker.uncertain)
        self.assertTrue(self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
