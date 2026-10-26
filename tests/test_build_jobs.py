from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

import lib.build_jobs as build_jobs
from lib.build_jobs import BuildJobStore
from lib.build_protocol import DEFAULT_LIMITS, request_digest


def build_request(request_id: str = "a" * 32, *, project: str = "sample") -> dict:
    return {
        "schema": 1,
        "request_id": request_id,
        "project": project,
        "source": {
            "revision": "1" * 40,
            "snapshot_sha256": "2" * 64,
            "manifest_sha256": "3" * 64,
        },
        "platform": {"os": "debian", "architecture": "x86_64", "baseline": "trixie"},
        "components": [{
            "name": "site",
            "type": "static",
            "artifacts": ["dist"],
            "commands": ["build --release"],
            "env": {"BUILD_MODE": "production"},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


def artifact_for(request: dict, *, digest: str = "d" * 64) -> dict:
    return {
        "sha256": digest,
        "size": 4096,
        "descriptor": {
            "schema": 1,
            "request_id": request["request_id"],
            "project": request["project"],
            "request_sha256": request_digest(request),
            "source": dict(request["source"]),
            "platform": dict(request["platform"]),
            "components": [{"name": "site", "type": "static", "artifacts": ["dist"]}],
            "toolchains": {"go": "go1.25"},
            "entries": [
                {"path": "dist", "kind": "directory", "size": 0, "sha256": "", "executable": False},
                {"path": "dist/index.html", "kind": "file", "size": 5,
                 "sha256": "e" * 64, "executable": False},
            ],
        },
    }


class MutableClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def _try_worker_process(path: str, result_queue) -> None:
    store = BuildJobStore(path)
    try:
        with store.worker():
            result_queue.put("acquired")
    except RuntimeError:
        result_queue.put("busy")


class BuildJobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.clock = MutableClock()
        self.path = self.root / "jobs.json"
        self.store = BuildJobStore(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def submit(self, request_id: str = "a" * 32, *, owner: str = "controller") -> dict:
        return self.store.submit(build_request(request_id), owner=owner)

    def test_submission_is_private_durable_and_replayed_only_for_same_owner_and_input(self) -> None:
        request = build_request()
        first = self.store.submit(request, owner="controller")
        status = self.store.status(request["request_id"], owner="controller")
        self.assertEqual(first["sequence"], 1)
        self.assertEqual(status["state"], "queued")
        public_json = json.dumps(status)
        self.assertNotIn("commands", public_json)
        self.assertNotIn("env", public_json)
        self.assertNotIn("request", status)
        self.assertNotIn("owner", status)

        persisted = self.path.read_text()
        self.assertIn("build --release", persisted)
        self.assertIn("BUILD_MODE", persisted)
        self.assertIn("production", persisted)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        state_lock = self.path.with_name("jobs.json.lock")
        lock_info = state_lock.stat()
        self.assertTrue(stat.S_ISREG(lock_info.st_mode))
        self.assertEqual(stat.S_IMODE(lock_info.st_mode), 0o600)

        reopened = BuildJobStore(self.path, clock=self.clock)
        replay = reopened.submit(request, owner="controller")
        self.assertEqual(replay, first)
        self.assertEqual((state_lock.stat().st_dev, state_lock.stat().st_ino),
                         (lock_info.st_dev, lock_info.st_ino))

        changed = build_request()
        changed["components"][0]["commands"] = ["build --different"]
        with self.assertRaisesRegex(ValueError, "different inputs"):
            reopened.submit(changed, owner="controller")
        with self.assertRaises(PermissionError):
            reopened.submit(request, owner="another-controller")
        with self.assertRaises(PermissionError):
            reopened.status(request["request_id"], owner="another-controller")
        request_with_owner = build_request()
        request_with_owner["owner"] = "untrusted"
        with self.assertRaises(ValueError):
            reopened.submit(request_with_owner, owner="controller")

    def test_lost_submission_reply_retries_the_durable_job_without_allocating_another(self) -> None:
        request = build_request()
        real_save = self.store._save

        def save_then_lose_reply(parent_fd, state):
            real_save(parent_fd, state)
            raise OSError("simulated reply loss after durable commit")

        with patch.object(self.store, "_save", side_effect=save_then_lose_reply):
            with self.assertRaisesRegex(OSError, "reply loss"):
                self.store.submit(request, owner="controller")
        replay = self.store.submit(request, owner="controller")
        self.assertEqual(replay["request_id"], request["request_id"])
        self.assertEqual(replay["sequence"], 1)
        self.assertEqual(len(json.loads(self.path.read_text())["jobs"]), 1)

    def test_failed_state_write_does_not_acknowledge_or_consume_a_request_id(self) -> None:
        request = build_request()
        with patch.object(self.store, "_save", side_effect=OSError("state sync failed")):
            with self.assertRaisesRegex(OSError, "state sync failed"):
                self.store.submit(request, owner="controller")
        self.assertFalse(self.path.exists())
        accepted = self.store.submit(request, owner="controller")
        self.assertEqual(accepted["sequence"], 1)

    def test_fifo_uses_persisted_sequences_when_wall_clock_moves_backward(self) -> None:
        first = self.submit("a" * 32)
        self.clock.value = 900.0
        second = self.submit("b" * 32)
        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(first["created_at"], second["created_at"])
        with self.store.worker() as lease:
            claim = lease.claim()
            self.assertEqual(claim["request_id"], "a" * 32)
            with self.assertRaisesRegex(RuntimeError, "already has an active claim"):
                lease.claim()

    def test_queue_expiry_is_rechecked_at_claim_and_oldest_live_job_runs(self) -> None:
        store = BuildJobStore(self.path, clock=self.clock, queue_ttl_seconds=10)
        old = store.submit(build_request("a" * 32), owner="controller")
        self.clock.value = 1005.0
        live = store.submit(build_request("b" * 32), owner="controller")
        self.clock.value = 1010.0
        with store.worker() as lease:
            claim = lease.claim()
            self.assertEqual(claim["request_id"], live["request_id"])
        self.assertEqual(store.status(old["request_id"], owner="controller")["state"], "expired")

    def test_draining_refuses_new_admissions_holds_accepted_work_and_allows_cached_operations(self) -> None:
        first = self.submit("a" * 32)
        second = self.submit("b" * 32)
        self.assertTrue(self.store.set_draining(True))
        self.assertEqual(self.submit("a" * 32), first)
        with self.assertRaisesRegex(RuntimeError, "draining"):
            self.submit("c" * 32)
        with self.store.worker() as lease:
            self.assertIsNone(lease.claim())
        self.assertEqual(self.store.status(second["request_id"], owner="controller")["state"], "queued")
        cancelled = self.store.cancel(first["request_id"], owner="controller")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(self.store.cancel(first["request_id"], owner="controller"), cancelled)
        self.assertFalse(self.store.set_draining(False))
        with self.store.worker() as lease:
            self.assertEqual(lease.claim()["request_id"], second["request_id"])

    def test_queue_and_retained_operation_limits_fail_closed_without_pruning(self) -> None:
        store = BuildJobStore(self.path, clock=self.clock, max_queue_length=1)
        first = store.submit(build_request("a" * 32), owner="controller")
        with self.assertRaisesRegex(ValueError, "queue is full"):
            store.submit(build_request("b" * 32), owner="controller")
        store.cancel(first["request_id"], owner="controller")
        with patch.object(build_jobs, "MAX_OPERATIONS", 1):
            self.assertEqual(store.submit(build_request("a" * 32), owner="controller")["state"], "cancelled")
            with self.assertRaisesRegex(ValueError, "cannot be pruned"):
                store.submit(build_request("b" * 32), owner="controller")
        self.assertEqual(len(json.loads(self.path.read_text())["jobs"]), 1)

    def test_encoded_state_byte_limit_does_not_publish_partial_state(self) -> None:
        with patch.object(build_jobs, "MAX_STORE_BYTES", 128):
            with self.assertRaises(ValueError):
                self.submit()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.submit()["sequence"], 1)

    def test_running_cancel_is_cooperative_and_finish_requires_verified_cleanup(self) -> None:
        request = build_request()
        queued = self.store.submit(request, owner="controller")
        with self.store.worker() as lease:
            claim = lease.claim()
            self.assertEqual(claim["request"], request)
            cancelled = self.store.cancel(queued["request_id"], owner="controller")
            self.assertEqual(cancelled["state"], "running")
            self.assertTrue(cancelled["cancel_requested"])
            with self.assertRaisesRegex(ValueError, "cannot finish as succeeded"):
                lease.finish(queued["request_id"], outcome="succeeded", work_stopped=True,
                             artifact=artifact_for(request))
            with self.assertRaisesRegex(ValueError, "verify all supervised"):
                lease.finish(queued["request_id"], outcome="cancelled", work_stopped=False)
            terminal = lease.finish(queued["request_id"], outcome="cancelled", work_stopped=True)
        self.assertEqual(terminal["state"], "cancelled")
        self.assertEqual(terminal["reason"], "cancelled")
        self.assertIsNone(terminal["artifact"])

    def test_success_persists_only_matching_artifact_identity_and_failure_has_fixed_reason(self) -> None:
        success_request = build_request("a" * 32)
        failed_request = build_request("b" * 32)
        success = self.store.submit(success_request, owner="controller")
        failed = self.store.submit(failed_request, owner="controller")
        with self.store.worker() as lease:
            self.assertEqual(lease.claim()["request_id"], success["request_id"])
            result = lease.finish(success["request_id"], outcome="succeeded", work_stopped=True,
                                  artifact=artifact_for(success_request))
            self.assertEqual(lease.claim()["request_id"], failed["request_id"])
            failed_result = lease.finish(failed["request_id"], outcome="failed", work_stopped=True)
        artifact_identity = result["artifact"]
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(artifact_identity["request_id"], success_request["request_id"])
        self.assertEqual(artifact_identity["request_sha256"], request_digest(success_request))
        self.assertNotIn("entries", artifact_identity)
        self.assertEqual(failed_result["state"], "failed")
        self.assertEqual(failed_result["reason"], "build_failed")
        self.assertIsNone(failed_result["artifact"])

    def test_malformed_or_mismatched_artifact_leaves_running_claim_unfinished(self) -> None:
        request = build_request()
        job = self.store.submit(request, owner="controller")
        with self.store.worker() as lease:
            lease.claim()
            malformed = artifact_for(request)
            malformed["descriptor"]["source"]["snapshot_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                lease.finish(job["request_id"], outcome="succeeded", work_stopped=True, artifact=malformed)
            self.assertEqual(self.store.status(job["request_id"], owner="controller")["state"], "running")
            failed = lease.finish(job["request_id"], outcome="failed", work_stopped=True)
        self.assertEqual(failed["state"], "failed")

    def test_cancellation_arriving_during_artifact_validation_prevents_success(self) -> None:
        request = build_request()
        job = self.store.submit(request, owner="controller")
        with self.store.worker() as lease:
            lease.claim()
            validate_artifact = build_jobs._artifact_identity

            def cancel_during_validation(value, admitted_request):
                self.store.cancel(job["request_id"], owner="controller")
                return validate_artifact(value, admitted_request)

            with patch.object(build_jobs, "_artifact_identity", side_effect=cancel_during_validation):
                with self.assertRaisesRegex(ValueError, "cancellation-requested"):
                    lease.finish(job["request_id"], outcome="succeeded", work_stopped=True,
                                 artifact=artifact_for(request))
            cancelled = lease.finish(job["request_id"], outcome="cancelled", work_stopped=True)
        self.assertEqual(cancelled["state"], "cancelled")

    def test_worker_slot_is_exclusive_across_processes_and_inherited_descriptors(self) -> None:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        retained_descriptor = -1
        worker_lock = self.path.with_name("jobs.json.worker.lock")
        with self.store.worker() as lease:
            descriptor = lease.fileno()
            self.assertTrue(os.get_inheritable(descriptor))
            lock_info = worker_lock.stat()
            self.assertTrue(stat.S_ISREG(lock_info.st_mode))
            self.assertEqual(stat.S_IMODE(lock_info.st_mode), 0o600)
            retained_descriptor = os.dup(descriptor)
            process = context.Process(target=_try_worker_process, args=(str(self.path), result_queue))
            process.start()
            self.assertEqual(result_queue.get(timeout=10), "busy")
            process.join(10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        try:
            with self.assertRaisesRegex(RuntimeError, "already leased"):
                with self.store.worker():
                    self.fail("inherited lease descriptor released the worker slot")
        finally:
            os.close(retained_descriptor)
        with self.store.worker():
            pass
        self.assertEqual((worker_lock.stat().st_dev, worker_lock.stat().st_ino),
                         (lock_info.st_dev, lock_info.st_ino))
        result_queue.close()
        result_queue.join_thread()

    def test_interrupted_running_job_requires_verified_recovery_and_is_never_replayed(self) -> None:
        interrupted = self.store.submit(build_request("a" * 32), owner="controller")
        waiting = self.store.submit(build_request("b" * 32), owner="controller")
        with self.store.worker() as lease:
            self.assertEqual(lease.claim()["request_id"], interrupted["request_id"])

        with self.store.worker() as lease:
            with self.assertRaisesRegex(RuntimeError, "recover_interrupted"):
                lease.claim()
            cleanup = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, "did not verify"):
                lease.recover_interrupted(interrupted["request_id"], cleanup=cleanup)
            cleanup.assert_called_once()
            self.assertEqual(cleanup.call_args.args[1]["request_id"], interrupted["request_id"])
            verified_cleanup = Mock(return_value=True)
            uncertain = lease.recover_interrupted(interrupted["request_id"], cleanup=verified_cleanup)
            self.assertEqual(uncertain["state"], "uncertain")
            self.assertEqual(uncertain["reason"], "worker_interrupted")
            self.assertIsNone(uncertain["artifact"])
            self.assertEqual(lease.claim()["request_id"], waiting["request_id"])
        recovered = self.store.status(interrupted["request_id"], owner="controller")
        self.assertEqual(recovered["state"], "uncertain")

    def test_failed_terminal_state_write_keeps_running_claim_for_recovery(self) -> None:
        job = self.submit()
        with self.store.worker() as lease:
            lease.claim()
            with patch.object(self.store, "_save", side_effect=OSError("final sync failed")):
                with self.assertRaisesRegex(OSError, "final sync failed"):
                    lease.finish(job["request_id"], outcome="failed", work_stopped=True)
        self.assertEqual(self.store.status(job["request_id"], owner="controller")["state"], "running")
        with self.store.worker() as lease:
            uncertain = lease.recover_interrupted(job["request_id"], cleanup=lambda _id, _request: True)
        self.assertEqual(uncertain["state"], "uncertain")

    def test_stale_lease_cannot_mutate_after_worker_context_exits(self) -> None:
        job = self.submit()
        with self.store.worker() as lease:
            lease.claim()
        with self.assertRaises(PermissionError):
            lease.finish(job["request_id"], outcome="failed", work_stopped=True)
        self.assertEqual(self.store.status(job["request_id"], owner="controller")["state"], "running")

    def test_corrupt_state_and_unsafe_private_files_fail_closed(self) -> None:
        job = self.submit()
        original = self.path.read_bytes()
        self.path.write_bytes(b'{"schema":')
        corrupt = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.status(job["request_id"], owner="controller")
        self.assertEqual(self.path.read_bytes(), corrupt)

        self.path.write_bytes(original)
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            self.store.status(job["request_id"], owner="controller")
        os.chmod(self.path, 0o600)

        target = self.root / "target"
        target.write_text("not a lock")
        os.chmod(target, 0o600)
        lock_path = self.path.with_name("jobs.json.lock")
        lock_path.unlink()
        lock_path.symlink_to(target)
        with self.assertRaises((OSError, ValueError)):
            self.store.status(job["request_id"], owner="controller")

    def test_state_read_failure_is_not_treated_as_a_missing_store(self) -> None:
        job = self.submit()
        original = self.path.read_bytes()
        real_open = os.open

        def fail_state_open(path, flags, *args, **kwargs):
            if path == self.path.name:
                raise OSError("state lookup failed")
            return real_open(path, flags, *args, **kwargs)

        with patch.object(build_jobs.os, "open", side_effect=fail_state_open):
            with self.assertRaisesRegex(OSError, "state lookup failed"):
                self.store.status(job["request_id"], owner="controller")
        self.assertEqual(self.path.read_bytes(), original)

    def test_private_parent_rejects_public_and_symlink_directories(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o755)
        os.chmod(public, 0o755)
        with self.assertRaisesRegex(ValueError, "private"):
            BuildJobStore(public / "jobs.json", clock=self.clock)

        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            BuildJobStore(linked / "jobs.json", clock=self.clock)

    def test_invalid_clock_and_store_configuration_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            BuildJobStore(self.root / "bad-clock.json", clock=lambda: float("nan")).submit(
                build_request(), owner="controller",
            )
        with self.assertRaises(ValueError):
            BuildJobStore(self.root / "bad-ttl.json", clock=self.clock, queue_ttl_seconds=0)
        with self.assertRaises(ValueError):
            self.store.set_draining(1)


if __name__ == "__main__":
    unittest.main()
