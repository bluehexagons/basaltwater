"""Root build-worker lifecycle tests with mocked host execution boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

from lib import build_jobs, build_source, build_worker
from lib.build_execution import BuildExecutionResult
from lib.build_jobs import BuildJobStore
from lib.build_manifest import DEBIAN_PLATFORM, request_from_manifest
from lib.build_protocol import DEFAULT_LIMITS
from lib.project_manifest import parse_manifest


REQUEST_ID = "a" * 32
OWNER = "controller:project"
REVISION = "1" * 40
HISTORY = b"filtered-test-history-bundle"


def manifest_data(*, command: str = "build", component_type: str = "static") -> dict:
    component = {"name": "site", "type": component_type, "domain": "example.com",
                 "build": [command]}
    if component_type in {"static", "godot-web"}:
        component["output"] = "dist"
    return {"version": 1, "components": [component], "build_runtimes": ["node"]}


def source_snapshot(content: bytes) -> tuple[bytes, dict[str, str]]:
    path = b"basaltwater.json"
    frame = bytearray(build_source.MAGIC)
    frame.extend(struct.pack("!H", len(REVISION)))
    frame.extend(REVISION.encode("ascii"))
    frame.extend(struct.pack("!I", 1))
    frame.extend(b"F\x00")
    frame.extend(struct.pack("!I", len(path)))
    frame.extend(path)
    frame.extend(struct.pack("!Q", len(content)))
    frame.extend(content)
    frame.extend(hashlib.sha256(content).digest())
    frame.extend(b"H")
    frame.extend(struct.pack("!Q", len(HISTORY)))
    frame.extend(HISTORY)
    frame.extend(hashlib.sha256(HISTORY).digest())
    frame.extend(b"E")
    raw = bytes(frame)
    return raw, {
        "revision": REVISION,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": hashlib.sha256(content).hexdigest(),
    }


class FakeScratch:
    def __init__(self, request_id: str, mount_path: str, size_bytes: int, events: list[str]):
        self.request_id = request_id
        self.mount_path = mount_path
        self.size_bytes = size_bytes
        self.capacity_bytes = size_bytes - 4096
        self._root_path = str(Path(mount_path).parent.parent)
        self._mount_id = 47
        self.events = events
        self.fail_release = False
        self.release_calls = 0

    def release(self, *, work_stopped: bool = False) -> None:
        self.events.append("release")
        self.release_calls += 1
        if work_stopped is not True:
            raise AssertionError("scratch release must confirm stopped work")
        if self.fail_release:
            raise OSError("private release detail")


class BuildWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.inputs = self.private_dir("inputs")
        self.scratch = self.private_dir("scratch")
        self.artifacts = self.private_dir("artifacts")
        self.logs = self.private_dir("logs")
        self.store = BuildJobStore(self.private_dir("jobs") / "state.json")
        self.events: list[str] = []
        self.handle: FakeScratch | None = None
        self.request_id = REQUEST_ID
        self.request, self.snapshot = self.make_request_and_snapshot()
        self.stage_source(self.request_id, self.snapshot)
        self.store.submit(self.request, owner=OWNER)

    def private_dir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        return path

    def make_request_and_snapshot(
        self, *, stored_manifest: bytes | None = None, request_manifest: dict | None = None,
        request_id: str | None = None, platform: dict | None = None,
    ) -> tuple[dict, bytes]:
        stored = stored_manifest or json.dumps(manifest_data(), separators=(",", ":")).encode()
        source_data, source = source_snapshot(stored)
        manifest = parse_manifest(manifest_data() if request_manifest is None else request_manifest)
        limits = dict(DEFAULT_LIMITS)
        limits["scratch_bytes"] = 256 * 1024**2
        request = request_from_manifest(
            manifest,
            request_id=request_id or self.request_id,
            project="sample",
            source=source,
            platform=DEBIAN_PLATFORM if platform is None else platform,
            limits=limits,
        )
        return request, source_data

    def stage_source(self, request_id: str, raw: bytes) -> Path:
        path = self.inputs / f"{request_id}.source"
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(raw)
        path.chmod(0o400)
        return path

    def create_scratch(self, root: str, request_id: str, *, size_bytes: int) -> FakeScratch:
        self.events.append("scratch")
        request_root = Path(root) / request_id
        request_root.mkdir(mode=0o700)
        mount_path = request_root / "fs"
        mount_path.mkdir(mode=0o700)
        self.handle = FakeScratch(request_id, str(mount_path), size_bytes, self.events)
        return self.handle

    def record_chown(self, descriptor: int, uid: int, gid: int) -> None:
        self.events.append("chown")
        self.chowns.append((os.readlink(f"/proc/self/fd/{descriptor}"), uid, gid))

    def _prepare_workspace(self, checkout: str) -> None:
        output = Path(checkout) / "dist"
        output.mkdir()
        (output / "index.html").write_text("<main>built</main>")

    def _run(
        self,
        *,
        authorize=None,
        execute=None,
        after_package=None,
        fail_release: bool = False,
    ) -> dict | None:
        self.chowns: list[tuple[str, int, int]] = []
        callback_count = 0
        real_unpack = build_source.unpack_source_snapshot
        real_read_manifest = build_worker._read_committed_manifest
        real_readiness = build_worker.check_tool_readiness
        real_package = build_worker.create_artifact
        real_finish = BuildJobStore._finish
        self.published_toolchains: dict[str, str] | None = None

        def authorize_default(owner: str, request: dict) -> dict[str, str]:
            nonlocal callback_count
            callback_count += 1
            self.events.append(f"authorize{callback_count}")
            self.assertEqual(owner, OWNER)
            self.assertNotIn("owner", request)
            self.assertEqual(self.store.status(request["request_id"], owner=OWNER)["state"], "running")
            return {"node": f"node-v{callback_count}"}

        def create(root: str, request_id: str, *, size_bytes: int) -> FakeScratch:
            result = self.create_scratch(root, request_id, size_bytes=size_bytes)
            result.fail_release = fail_release
            return result

        def unpack(*args, **kwargs):
            self.events.append("unpack")
            return real_unpack(*args, **kwargs)

        def read_manifest(*args, **kwargs):
            self.events.append("manifest")
            return real_read_manifest(*args, **kwargs)

        def readiness(*args, **kwargs):
            self.events.append("readiness")
            return real_readiness(*args, **kwargs)

        def package(*args, **kwargs):
            self.events.append("artifact")
            self.published_toolchains = dict(kwargs.get("toolchains", {}))
            result = real_package(*args, **kwargs)
            if after_package is not None:
                after_package()
            return result

        def finish(instance, lease, request_id: str, *, outcome: str, **kwargs):
            self.events.append("finish:" + outcome)
            return real_finish(instance, lease, request_id, outcome=outcome, **kwargs)

        def execute_default(request, **kwargs):
            self.events.append("run")
            self.assertEqual(kwargs["scratch_path"], self.handle.mount_path)
            self.assertEqual(kwargs["log_path"], str(self.logs / f"{self.request_id}.log"))
            self.assertTrue(kwargs["cancelled"]() is False)
            self._prepare_workspace(str(Path(self.handle.mount_path) / "source" / "checkout"))
            return BuildExecutionResult("succeeded", 0, "completed")

        self.events.clear()
        with patch.object(build_worker, "_require_root"), \
             patch.object(build_worker, "create_scratch", side_effect=create), \
             patch.object(build_worker, "unpack_source_snapshot", side_effect=unpack), \
             patch.object(build_worker, "_read_committed_manifest", side_effect=read_manifest), \
             patch.object(build_worker, "check_tool_readiness", side_effect=readiness), \
             patch.object(build_worker, "_chown_descriptor", side_effect=self.record_chown), \
             patch.object(build_worker, "run_systemd_build", side_effect=execute or execute_default), \
             patch.object(build_worker, "create_artifact", side_effect=package), \
             patch.object(BuildJobStore, "_finish", new=finish), \
             patch.object(build_source, "_publish_directory",
                          side_effect=lambda parent, source, destination: os.rename(
                              source, destination, src_dir_fd=parent, dst_dir_fd=parent)):
            result = build_worker.run_next(
                self.store, uid=12345, gid=12345, inputs_root=self.inputs,
                scratch_root=self.scratch, artifacts_root=self.artifacts,
                logs_root=self.logs, authorize=authorize or authorize_default,
            )
        return result

    def test_order_fresh_policy_toolchain_and_private_artifact_layout(self) -> None:
        status = self._run()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual([event for event in self.events if event.startswith("authorize")],
                         ["authorize1", "authorize2"])
        self.assertLess(self.events.index("authorize1"), self.events.index("scratch"))
        self.assertLess(self.events.index("scratch"), self.events.index("unpack"))
        self.assertLess(self.events.index("unpack"), self.events.index("manifest"))
        self.assertLess(self.events.index("manifest"), self.events.index("readiness"))
        self.assertLess(self.events.index("readiness"), self.events.index("authorize2"))
        self.assertLess(self.events.index("authorize2"), self.events.index("run"))
        self.assertLess(self.events.index("run"), self.events.index("artifact"))
        self.assertLess(self.events.index("artifact"), self.events.index("release"))
        self.assertLess(self.events.index("release"), self.events.index("finish:succeeded"))

        bundle = self.artifacts / f"{self.request_id}.bundle"
        self.assertTrue(bundle.is_file())
        self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)
        self.assertEqual(status["artifact"]["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        descriptor = status["artifact"]
        self.assertNotIn("commands", json.dumps(descriptor))
        self.assertNotIn("env", json.dumps(descriptor))

        request_path = Path(self.handle.mount_path) / "request.json"
        history_path = Path(self.handle.mount_path) / "source" / "history.bundle"
        self.assertEqual(stat.S_IMODE(request_path.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(history_path.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE((Path(self.handle.mount_path) / "source").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(Path(self.handle.mount_path).stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.logs.joinpath(f"{self.request_id}.log").stat().st_mode), 0o600)
        self.assertEqual(self.published_toolchains, {"node": "node-v2"})
        self.assertTrue(self.chowns)
        build_handoffs = [(path, uid, gid) for path, uid, gid in self.chowns
                          if "/checkout" in path or "/home" in path]
        self.assertTrue(any("/checkout" in path for path, _, _ in build_handoffs))
        self.assertTrue(any("/home" in path for path, _, _ in build_handoffs))
        self.assertTrue(all(uid == 12345 and gid == 12345 for _, uid, gid in build_handoffs))
        self.assertFalse(any("/source" in path and "/checkout" not in path for path, _, _ in self.chowns))

    def test_tampered_input_and_manifest_request_mismatch_fail_before_execution(self) -> None:
        raw = bytearray(self.snapshot)
        marker = b"H" + struct.pack("!Q", len(HISTORY)) + HISTORY
        position = raw.index(marker) + 9
        raw[position] ^= 1
        raw[position + len(HISTORY):position + len(HISTORY) + 32] = hashlib.sha256(
            raw[position:position + len(HISTORY)]
        ).digest()
        self.stage_source(self.request_id, bytes(raw))
        with patch.object(build_worker, "run_systemd_build") as execute:
            status = self._run(execute=lambda *_args, **_kwargs: self.fail("tampered source launched"))
        self.assertEqual(status["state"], "failed")
        self.assertEqual(self.handle.release_calls, 1)
        self.assertNotIn("run", self.events)
        execute.assert_not_called()

        mismatch_id = "b" * 32
        different = json.dumps(manifest_data(command="different build"), separators=(",", ":")).encode()
        self.request_id = mismatch_id
        self.request, self.snapshot = self.make_request_and_snapshot(
            stored_manifest=different, request_manifest=manifest_data(), request_id=mismatch_id,
        )
        self.stage_source(mismatch_id, self.snapshot)
        self.store.submit(self.request, owner=OWNER)
        status = self._run(execute=lambda *_args, **_kwargs: self.fail("manifest mismatch launched"))
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("run", self.events)

    def test_private_input_mode_platform_and_component_policy_fail_before_execution(self) -> None:
        self.stage_source(self.request_id, self.snapshot).chmod(0o644)
        status = self._run(execute=lambda *_args, **_kwargs: self.fail("unsafe input launched"))
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("run", self.events)

        for index, (platform, data) in enumerate((
            ({"os": "linux", "architecture": "x86_64", "baseline": "other"}, manifest_data()),
            (DEBIAN_PLATFORM, manifest_data(component_type="godot-web")),
        ), start=1):
            request_id = f"{index:032x}"
            self.request_id = request_id
            self.request, self.snapshot = self.make_request_and_snapshot(
                stored_manifest=json.dumps(data, separators=(",", ":")).encode(),
                request_manifest=data, request_id=request_id, platform=platform,
            )
            self.stage_source(request_id, self.snapshot)
            self.store.submit(self.request, owner=OWNER)
            status = self._run(execute=lambda *_args, **_kwargs: self.fail("unsupported job launched"))
            self.assertEqual(status["state"], "failed")
            self.assertNotIn("run", self.events)

    def test_cancellation_before_and_during_execution_skips_artifact(self) -> None:
        def cancel_on_second_policy(owner: str, request: dict) -> dict[str, str]:
            count = getattr(self, "auth_count", 0) + 1
            self.auth_count = count
            self.events.append(f"authorize{count}")
            self.assertEqual(owner, OWNER)
            if count == 2:
                self.store.cancel(request["request_id"], owner=owner)
            return {"node": "node-v20"}

        def cancelled_before(request, **kwargs):
            self.events.append("run")
            self.assertTrue(kwargs["cancelled"]())
            return BuildExecutionResult("cancelled", None, "cancelled-before-launch")

        status = self._run(authorize=cancel_on_second_policy, execute=cancelled_before)
        self.assertEqual(status["state"], "cancelled")
        self.assertNotIn("run", self.events)
        self.assertNotIn("artifact", self.events)
        self.assertEqual(self.handle.release_calls, 1)

        request_id = "c" * 32
        self.request_id = request_id
        self.request, self.snapshot = self.make_request_and_snapshot(request_id=request_id)
        self.stage_source(request_id, self.snapshot)
        self.store.submit(self.request, owner=OWNER)

        def cancel_during(request, **kwargs):
            self.events.append("run")
            self.store.cancel(request["request_id"], owner=OWNER)
            self.assertTrue(kwargs["cancelled"]())
            return BuildExecutionResult("cancelled", None, "cancelled")

        status = self._run(execute=cancel_during)
        self.assertEqual(status["state"], "cancelled")
        self.assertNotIn("artifact", self.events)
        self.assertEqual(self.handle.release_calls, 1)

    def test_fresh_policy_denial_cleans_scratch_before_failed_terminal(self) -> None:
        def deny_on_second_check(_owner: str, _request: dict) -> dict[str, str]:
            if self.events.count("authorize"):
                raise PermissionError("private policy detail")
            self.events.append("authorize")
            return {"node": "node-v20"}

        status = self._run(authorize=deny_on_second_check)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(self.handle.release_calls, 1)
        self.assertNotIn("run", self.events)
        self.assertLess(self.events.index("release"), self.events.index("finish:failed"))

    def test_cancellation_immediately_after_claim_skips_scratch_creation(self) -> None:
        def cancel_after_claim(_owner: str, request: dict) -> dict[str, str]:
            self.store.cancel(request["request_id"], owner=OWNER)
            return {"node": "node-v20"}

        status = self._run(authorize=cancel_after_claim)
        self.assertEqual(status["state"], "cancelled")
        self.assertIsNone(self.handle)
        self.assertNotIn("scratch", self.events)
        self.assertNotIn("run", self.events)

    def test_cancel_after_publication_keeps_bundle_as_orphan_and_finishes_cancelled(self) -> None:
        def cancel_after_package() -> None:
            self.store.cancel(self.request_id, owner=OWNER)

        status = self._run(after_package=cancel_after_package)
        self.assertEqual(status["state"], "cancelled")
        self.assertIsNone(status["artifact"])
        self.assertTrue((self.artifacts / f"{self.request_id}.bundle").is_file())
        self.assertLess(self.events.index("artifact"), self.events.index("release"))
        self.assertLess(self.events.index("release"), self.events.index("finish:cancelled"))

    def test_success_finish_racing_cancel_never_reports_success(self) -> None:
        original_finish = BuildJobStore._finish

        def race_cancel(instance, lease, request_id: str, *, outcome: str, **kwargs):
            if outcome == "succeeded":
                self.store.cancel(request_id, owner=OWNER)
            kwargs.setdefault("artifact", None)
            return original_finish(instance, lease, request_id, outcome=outcome, **kwargs)

        self.chowns = []
        with patch.object(build_worker, "_require_root"), \
             patch.object(build_worker, "create_scratch", side_effect=lambda root, request_id, **kwargs:
                          self.create_scratch(root, request_id, size_bytes=kwargs["size_bytes"])), \
             patch.object(build_worker, "_chown_descriptor", side_effect=self.record_chown), \
             patch.object(build_worker, "run_systemd_build", side_effect=lambda request, **kwargs: (
                 self._prepare_workspace(str(Path(self.handle.mount_path) / "source" / "checkout"))
                 or BuildExecutionResult("succeeded", 0, "completed"))), \
             patch.object(build_jobs.WorkerLease, "finish", new=lambda lease, request_id, **kwargs:
                          race_cancel(lease._store, lease, request_id, **kwargs)), \
             patch.object(build_source, "_publish_directory",
                          side_effect=lambda parent, source, destination: os.rename(
                              source, destination, src_dir_fd=parent, dst_dir_fd=parent)):
            status = build_worker.run_next(
                self.store, uid=12345, gid=12345, inputs_root=self.inputs,
                scratch_root=self.scratch, artifacts_root=self.artifacts, logs_root=self.logs,
                authorize=lambda _owner, _request: {"node": "node-v20"},
            )
        self.assertEqual(status["state"], "cancelled")
        self.assertIsNone(status["artifact"])
        self.assertTrue((self.artifacts / f"{self.request_id}.bundle").exists())

    def test_confirmed_helper_failure_cleans_before_terminal_but_cleanup_error_retains_claim(self) -> None:
        status = self._run(execute=lambda *_args, **_kwargs: BuildExecutionResult("failed", 1, "failed"))
        self.assertEqual(status["state"], "failed")
        self.assertLess(self.events.index("release"), self.events.index("finish:failed"))

        request_id = "d" * 32
        self.request_id = request_id
        self.request, self.snapshot = self.make_request_and_snapshot(request_id=request_id)
        self.stage_source(request_id, self.snapshot)
        self.store.submit(self.request, owner=OWNER)
        with self.assertRaisesRegex(RuntimeError, "running claim and evidence were retained"):
            self._run(execute=lambda *_args, **_kwargs: BuildExecutionResult("failed", 1, "failed"),
                      fail_release=True)
        self.assertEqual(self.store.status(request_id, owner=OWNER)["state"], "running")
        with self.assertRaisesRegex(RuntimeError, "interrupted running build"):
            with self.store.worker() as lease:
                lease.claim()

    def test_unknown_execution_and_failed_terminal_write_retain_running_claim(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "running claim and evidence were retained"):
            self._run(execute=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private systemd detail")))
        self.assertEqual(self.store.status(self.request_id, owner=OWNER)["state"], "running")
        self.assertEqual(self.handle.release_calls, 0)

        with self.store.worker() as lease:
            recovered = lease.recover_interrupted(
                self.request_id, cleanup=lambda _request_id, _request: True,
            )
        self.assertEqual(recovered["state"], "uncertain")

        request_id = "e" * 32
        self.request_id = request_id
        self.request, self.snapshot = self.make_request_and_snapshot(request_id=request_id)
        self.stage_source(request_id, self.snapshot)
        self.store.submit(self.request, owner=OWNER)
        original_save = self.store._save

        def fail_terminal(parent_fd, state):
            if state["jobs"].get(request_id, {}).get("state") in {
                    "succeeded", "failed", "cancelled"}:
                raise OSError("private state storage detail")
            original_save(parent_fd, state)

        with patch.object(self.store, "_save", side_effect=fail_terminal):
            with self.assertRaisesRegex(RuntimeError, "running claim and evidence were retained"):
                self._run()
        self.assertEqual(self.store.status(request_id, owner=OWNER)["state"], "running")
        self.assertTrue((self.artifacts / f"{request_id}.bundle").is_file())
        with self.assertRaisesRegex(RuntimeError, "interrupted running build"):
            with self.store.worker() as lease:
                lease.claim()

    def test_no_queue_and_drain_skip_all_job_effects(self) -> None:
        empty_store = BuildJobStore(self.private_dir("empty-jobs") / "state.json")
        with patch.object(build_worker, "_require_root"), \
             patch.object(build_worker, "create_scratch") as scratch, \
             patch.object(build_worker, "run_systemd_build") as execute:
            result = build_worker.run_next(
                empty_store, uid=12345, gid=12345, inputs_root=self.inputs,
                scratch_root=self.scratch, artifacts_root=self.artifacts, logs_root=self.logs,
                authorize=lambda *_args: self.fail("authorization called without a claim"),
            )
            self.assertIsNone(result)
            empty_store.submit(self.request, owner=OWNER)
            empty_store.set_draining(True)
            self.assertIsNone(build_worker.run_next(
                empty_store, uid=12345, gid=12345, inputs_root=self.inputs,
                scratch_root=self.scratch, artifacts_root=self.artifacts, logs_root=self.logs,
                authorize=lambda *_args: self.fail("authorization called while draining"),
            ))
        scratch.assert_not_called()
        execute.assert_not_called()

    def test_root_gate_and_build_identity_reject_root(self) -> None:
        with patch.object(build_worker.os, "geteuid", return_value=1000):
            with self.assertRaises(PermissionError):
                build_worker.run_next(
                    self.store, uid=12345, gid=12345, inputs_root=self.inputs,
                    scratch_root=self.scratch, artifacts_root=self.artifacts, logs_root=self.logs,
                    authorize=lambda *_args: {},
                )
        with patch.object(build_worker, "_require_root"):
            with self.assertRaises(ValueError):
                build_worker.run_next(
                    self.store, uid=0, gid=0, inputs_root=self.inputs,
                    scratch_root=self.scratch, artifacts_root=self.artifacts, logs_root=self.logs,
                    authorize=lambda *_args: {},
                )
        self.assertEqual(self.store.status(self.request_id, owner=OWNER)["state"], "queued")

    def test_base_exception_is_sanitized_and_retains_scratch_and_running_claim(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "running claim and evidence were retained") as caught:
            self._run(execute=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("private command detail"),
            ))
        self.assertNotIn("private command detail", str(caught.exception))
        self.assertEqual(self.store.status(self.request_id, owner=OWNER)["state"], "running")
        self.assertEqual(self.handle.release_calls, 0)
        self.assertTrue(Path(self.handle.mount_path).exists())


if __name__ == "__main__":
    unittest.main()
