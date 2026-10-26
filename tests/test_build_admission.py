from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import lib.build_admission as build_admission
from lib.build_admission import DeploymentAdmissionStore
from lib.build_protocol import DEFAULT_LIMITS


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
            "commands": ["build"],
            "env": {"MODE": "production"},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


class MutableClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class BuildAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.clock = MutableClock()
        self.path = self.root / "admission.json"
        self.store = DeploymentAdmissionStore(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reserve(self, *, request: dict | None = None, owner: str = "controller",
                key: str = "b" * 32, bindings: str = "c" * 64,
                ttl: int = 3600, application: str = "web") -> dict:
        return self.store.reserve(
            application, request or build_request(), owner=owner,
            idempotency_key=key, bindings_sha256=bindings, ttl_seconds=ttl,
        )

    def activate(self, record: dict, *, action=None, **overrides):
        arguments = {
            "operation_id": record["operation_id"],
            "generation": record["generation"],
            "request": build_request(),
            "artifact_sha256": "d" * 64,
            "owner": "controller",
            "bindings_sha256": "c" * 64,
            "action": action or Mock(return_value={"release": "r1"}),
        }
        arguments.update(overrides)
        return self.store.activate(**arguments)

    def test_reservation_replays_after_reopen_without_extending_expiry(self) -> None:
        first = self.reserve(ttl=30)
        persisted = self.path.read_text()
        self.assertNotIn('"env"', persisted)
        self.assertNotIn("production", persisted)
        first["application"] = "detached-change"
        self.assertEqual(self.store.status(first["operation_id"], owner="controller")["application"], "web")
        self.clock.value += 100
        reopened = DeploymentAdmissionStore(self.path, clock=self.clock)

        replay = reopened.reserve(
            "web", build_request(), owner="controller", idempotency_key="b" * 32,
            bindings_sha256="c" * 64, ttl_seconds=600,
        )

        self.assertEqual(replay["operation_id"], first["operation_id"])
        self.assertEqual(replay["generation"], first["generation"])
        self.assertEqual(replay["expires_at"], first["expires_at"])
        self.assertTrue(replay["expired"])
        self.assertEqual(replay["effective_status"], "expired")
        self.assertNotEqual(replay["operation_id"], replay["request_id"])

    def test_same_key_mismatch_rejects_and_owner_scope_is_enforced(self) -> None:
        first = self.reserve()
        changed_source = build_request()
        changed_source["source"]["snapshot_sha256"] = "e" * 64
        changed_project = build_request(project="other")
        for request, bindings in (
            (changed_source, "c" * 64),
            (changed_project, "c" * 64),
            (build_request(), "f" * 64),
        ):
            with self.subTest(request=request["project"], bindings=bindings):
                with self.assertRaises(ValueError):
                    self.reserve(request=request, bindings=bindings)
        with self.assertRaises(PermissionError):
            self.store.status(first["operation_id"], owner="other-controller")
        with self.assertRaises(PermissionError):
            self.store.cancel(first["operation_id"], owner="other-controller")

        separate_owner = self.reserve(owner="different-controller")
        self.assertEqual(separate_owner["generation"], 2)
        self.assertNotEqual(separate_owner["operation_id"], first["operation_id"])

    def test_newer_cancelled_expired_or_uncertain_intent_never_revives_older(self) -> None:
        older = self.reserve(key="1" * 32)
        cancelled = self.reserve(key="2" * 32)
        self.store.cancel(cancelled["operation_id"], owner="controller")
        self.assertEqual(self.store.cancel(cancelled["operation_id"], owner="controller")["state"], "cancelled")
        self.assertEqual(self.store.status(older["operation_id"], owner="controller")["effective_status"], "superseded")
        delayed_action = Mock(return_value={"release": "stale"})
        with self.assertRaisesRegex(ValueError, "superseded"):
            self.activate(older, action=delayed_action)
        delayed_action.assert_not_called()

        expired = self.reserve(key="3" * 32, ttl=1)
        self.clock.value += 2
        self.assertEqual(self.store.status(expired["operation_id"], owner="controller")["effective_status"], "expired")
        self.assertEqual(self.store.status(older["operation_id"], owner="controller")["effective_status"], "superseded")

        failed = self.reserve(key="4" * 32)
        with self.assertRaisesRegex(RuntimeError, "failed action"):
            self.activate(failed, action=Mock(side_effect=RuntimeError("failed action")))
        self.assertEqual(self.store.status(older["operation_id"], owner="controller")["effective_status"], "superseded")
        self.assertEqual(self.store.status(failed["operation_id"], owner="controller")["state"], "uncertain")

    def test_activation_rejects_wrong_request_generation_expiry_owner_or_bindings(self) -> None:
        cases = ("request", "generation", "expired", "owner", "bindings")
        keys = {"request": "1", "generation": "2", "expired": "3", "owner": "4", "bindings": "5"}
        for case in cases:
            with self.subTest(case=case):
                path = self.root / f"{case}.json"
                clock = MutableClock()
                store = DeploymentAdmissionStore(path, clock=clock)
                record = store.reserve(
                    "web", build_request(), owner="controller", idempotency_key=(keys[case] * 32),
                    bindings_sha256="c" * 64, ttl_seconds=1,
                )
                callback = Mock(return_value={"ok": True})
                arguments = {
                    "operation_id": record["operation_id"],
                    "generation": record["generation"],
                    "request": build_request(),
                    "artifact_sha256": "d" * 64,
                    "owner": "controller",
                    "bindings_sha256": "c" * 64,
                    "action": callback,
                }
                if case == "request":
                    arguments["request"] = build_request(request_id="f" * 32)
                elif case == "generation":
                    arguments["generation"] += 1
                elif case == "expired":
                    clock.value += 2
                elif case == "owner":
                    arguments["owner"] = "someone-else"
                elif case == "bindings":
                    arguments["bindings_sha256"] = "e" * 64
                with self.assertRaises((ValueError, PermissionError)):
                    store.activate(**arguments)
                callback.assert_not_called()

    def test_success_is_cached_and_artifact_digest_cannot_change(self) -> None:
        record = self.reserve()
        action = Mock(return_value={"release": "r1", "details": [1, 2]})
        result = self.activate(record, action=action)
        result["details"].append(3)
        action.assert_called_once_with()

        reopened = DeploymentAdmissionStore(self.path, clock=self.clock)
        cached = reopened.activate(
            record["operation_id"], record["generation"], build_request(), "d" * 64,
            owner="controller", bindings_sha256="c" * 64, action=Mock(),
        )
        self.assertEqual(cached, {"release": "r1", "details": [1, 2]})
        with self.assertRaisesRegex(ValueError, "different artifact"):
            reopened.activate(
                record["operation_id"], record["generation"], build_request(), "e" * 64,
                owner="controller", bindings_sha256="c" * 64, action=Mock(),
            )
        with self.assertRaisesRegex(ValueError, "cannot be represented as undone"):
            reopened.cancel(record["operation_id"], owner="controller")

    def test_action_exception_and_final_save_failure_block_replay(self) -> None:
        crashed = self.reserve(key="1" * 32)
        callback = Mock(side_effect=RuntimeError("action crashed"))
        with self.assertRaisesRegex(RuntimeError, "action crashed"):
            self.activate(crashed, action=callback)
        reopened = DeploymentAdmissionStore(self.path, clock=self.clock)
        self.assertEqual(reopened.status(crashed["operation_id"], owner="controller")["state"], "uncertain")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            reopened.activate(
                crashed["operation_id"], crashed["generation"], build_request(), "d" * 64,
                owner="controller", bindings_sha256="c" * 64, action=Mock(),
            )
        callback.assert_called_once_with()

        final_save = self.reserve(key="2" * 32)
        action = Mock(return_value={"release": "r2"})
        real_write = build_admission.write_bytes_atomic
        writes = 0

        def fail_completion(path, content, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("final state sync failed")
            return real_write(path, content, **kwargs)

        with patch.object(build_admission, "write_bytes_atomic", side_effect=fail_completion):
            with self.assertRaisesRegex(OSError, "final state sync failed"):
                self.activate(final_save, action=action)
        self.assertEqual(writes, 2)
        retry_store = DeploymentAdmissionStore(self.path, clock=self.clock)
        self.assertEqual(retry_store.status(final_save["operation_id"], owner="controller")["effective_status"], "uncertain")
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            retry_store.activate(
                final_save["operation_id"], final_save["generation"], build_request(), "d" * 64,
                owner="controller", bindings_sha256="c" * 64, action=Mock(),
            )
        action.assert_called_once_with()

    def test_corrupt_or_unreadable_persistent_state_is_not_treated_as_missing(self) -> None:
        record = self.reserve()
        original = self.path.read_bytes()
        self.path.write_bytes(b'{"schema":')
        corrupt = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.status(record["operation_id"], owner="controller")
        self.assertEqual(self.path.read_bytes(), corrupt)

        self.path.write_bytes(original)
        self.path.write_text('{"schema":2,"desired_generations":{},"operations":{},"replays":{}}')
        unsupported = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.status(record["operation_id"], owner="controller")
        self.assertEqual(self.path.read_bytes(), unsupported)

        self.path.write_bytes(original)
        with patch("lib.build_admission.read_json_file", side_effect=OSError("state lookup failed")):
            with self.assertRaisesRegex(OSError, "state lookup failed"):
                self.store.status(record["operation_id"], owner="controller")
        self.assertEqual(self.path.read_bytes(), original)

    def test_private_parent_and_nofollow_file_checks(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            DeploymentAdmissionStore(public / "admission.json", clock=self.clock)

        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            DeploymentAdmissionStore(linked / "admission.json", clock=self.clock)

        target = self.root / "target.lock"
        target.write_text("not a lock")
        os.chmod(target, 0o600)
        (self.root / "linked-lock.json.lock").symlink_to(target)
        linked_lock = DeploymentAdmissionStore(self.root / "linked-lock.json", clock=self.clock)
        with self.assertRaises(ValueError):
            linked_lock.status("f" * 32, owner="controller")

        (self.root / "linked-state.json").symlink_to(target)
        linked_state = DeploymentAdmissionStore(self.root / "linked-state.json", clock=self.clock)
        with self.assertRaises(ValueError):
            linked_state.status("f" * 32, owner="controller")

    def test_lock_is_private_stable_and_competing_call_fails_busy(self) -> None:
        record = self.reserve()
        lock_path = self.path.with_name(self.path.name + ".lock")
        before = lock_path.stat()
        self.assertTrue(stat.S_ISREG(before.st_mode))
        self.assertEqual(stat.S_IMODE(before.st_mode), 0o600)

        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []
        second_store = DeploymentAdmissionStore(self.path, clock=self.clock)

        def action():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test action timed out")
            return {"ok": True}

        def activate_in_thread() -> None:
            try:
                self.activate(record, action=action)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=activate_in_thread)
        worker.start()
        self.assertTrue(entered.wait(5))
        try:
            with self.assertRaisesRegex(RuntimeError, "busy"):
                second_store.status(record["operation_id"], owner="controller")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        after = lock_path.stat()
        self.assertEqual((before.st_ino, before.st_dev), (after.st_ino, after.st_dev))

    def test_invalid_clock_does_not_allocate_or_reset_generation(self) -> None:
        first = self.reserve()
        invalid = DeploymentAdmissionStore(self.path, clock=lambda: float("nan"))
        with self.assertRaisesRegex(ValueError, "clock"):
            invalid.reserve(
                "web", build_request(request_id="f" * 32), owner="controller",
                idempotency_key="d" * 32, bindings_sha256="c" * 64,
            )
        second = self.reserve(key="e" * 32)
        self.assertEqual(second["generation"], first["generation"] + 1)

    def test_full_store_fails_closed_without_pruning_replay_identity(self) -> None:
        first = self.reserve()
        with patch.object(build_admission, "MAX_OPERATIONS", 1):
            replay = self.reserve()
            self.assertEqual(replay["operation_id"], first["operation_id"])
            with self.assertRaisesRegex(ValueError, "full"):
                self.reserve(key="d" * 32)
        state = json.loads(self.path.read_text())
        self.assertEqual(len(state["operations"]), 1)
        self.assertEqual(len(state["replays"]), 1)

    def test_byte_limit_failure_does_not_acknowledge_or_consume_generation(self) -> None:
        with patch.object(build_admission, "MAX_STORE_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.reserve()
        self.assertFalse(self.path.exists())
        record = self.reserve(key="d" * 32)
        self.assertEqual(record["generation"], 1)


if __name__ == "__main__":
    unittest.main()
