"""Role collectors exercise actual retention, hashes and temporary files."""

from __future__ import annotations

from contextlib import nullcontext
import os
import unittest
from unittest.mock import patch

from lib import build_storage_maintenance as maintenance
from lib.build_capacity import inspect_build_capacity
from tests import test_build_retention as retention_tests
from tests import test_build_target_artifacts as artifact_tests


class BuilderCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = retention_tests.BuildRetentionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = dict(roots={key: str(path) for key, path in self.fixture.roots.items()},
                           max_records=128, max_bytes=self.fixture.inputs.max_bytes)
        self.enterContext(patch("common.service_tools.build_host.load_config", return_value=self.config))
        self.enterContext(patch("lib.build_capacity._require_root"))

    def collect(self, **options):
        return maintenance.maintain_builder(apply=options.pop("apply", False), budget=0, target=0,
                                            min_age=10, now=2000, **options)

    def test_preview_apply_replay_and_explicit_bundle_release(self):
        f = self.fixture
        job = f.terminal("succeeded")
        self.assertEqual(self.collect()["candidates"][0]["status"], "eligible")
        self.assertTrue(f.path("inputs", ".source").exists())
        result = self.collect(apply=True)
        self.assertEqual(result["candidates"][0]["status"], "maintained")
        self.assertFalse(f.path("inputs", ".source").exists())
        self.assertFalse(f.path("logs", ".log").exists())
        self.assertTrue(f.path("artifacts", ".bundle").exists())
        self.assertEqual(f.jobs.status(f.request["request_id"], owner="controller"), job)
        self.assertEqual(self.collect(apply=True)["candidates"][0]["reason"], "no_eligible_payloads")
        self.collect(apply=True, release_bundles=True)
        self.assertFalse(f.path("artifacts", ".bundle").exists())
        self.assertEqual(inspect_build_capacity(self.config)["inputs"]["retained_slots"], 1)

    def test_running_and_recent_work_remain_protected(self):
        f = self.fixture
        f.admit()
        with f.jobs.worker() as lease:
            lease.claim()
            self.assertEqual(self.collect(apply=True)["candidates"][0]["status"], "skipped")
        self.assertTrue(f.path("inputs", ".source").exists())

    def test_new_worker_excludes_cleanup_of_an_older_terminal_job(self):
        f = self.fixture
        f.terminal("succeeded")
        # Raise only the temporary fixture's logical ceiling for a second job.
        self.config["max_bytes"] = 2 * f.reservation + 65536
        f.inputs.max_bytes = self.config["max_bytes"]
        f.admit(dict(f.request, request_id="b" * 32))
        with f.jobs.worker() as lease:
            lease.claim()
            result = self.collect(apply=True)
        self.assertEqual(result["candidates"][0]["reason"], "busy_or_unsafe_retention")
        self.assertTrue(f.path("inputs", ".source").exists())
        self.assertTrue(f.path("artifacts", ".bundle").exists())


class TargetCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = artifact_tests.BuildTargetArtifactStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.records = []
        for _ in range(2):
            record = f.reserve()
            f.stage(record)
            f.admissions.activate(record["operation_id"], record["generation"], f.request,
                artifact_sha256=f.artifact_sha256, owner="controller", bindings_sha256=artifact_tests.BINDINGS,
                action=lambda: {"status": "completed"})
            self.records.append(record)
        for name in ("releases", "verify"):
            (f.parent / name).mkdir(mode=0o700)
        self.config = dict(roots=dict(artifacts=str(f.staging_root), admission=str(f.parent),
                                     releases=str(f.parent / "releases"), verify=str(f.parent / "verify")),
                           max_records=128, max_bytes=f.store.max_bytes)
        # Match the installed service's fixed admission filename.
        os.rename(f.parent / "admission.json", f.parent / "admissions.json")
        os.rename(f.parent / "admission.json.lock", f.parent / "admissions.json.lock")
        f.admissions = f.admissions.__class__(f.parent / "admissions.json", clock=f.clock)
        current = self.records[-1]
        installed = {"web": dict(operation_id=current["operation_id"], generation=current["generation"],
                                artifact_sha256=f.artifact_sha256)}
        self.enterContext(patch("common.service_tools.build_target.load_config", return_value=self.config))
        self.enterContext(patch("lib.build_target_maintenance.target_maintenance", side_effect=nullcontext))
        self.enterContext(patch("lib.build_target_activation.TargetReleaseActivator._load",
                                return_value={"applications": installed}))
        self.enterContext(patch.object(maintenance.OperationStateStore, "load", return_value=None))
        self.enterContext(patch("lib.build_conversion._load", return_value=None))
        self.enterContext(patch.object(maintenance, "storage_lock", side_effect=lambda **_: nullcontext()))

    def collect(self, **options):
        return maintenance.maintain_target(apply=options.pop("apply", False), budget=0, target=0,
                                           min_age=10, now=2000, **options)

    def test_current_bundle_pinned_and_retired_metadata_reopens(self):
        f = self.fixture
        old, current = [row["operation_id"] for row in self.records]
        plan = self.collect()
        self.assertTrue((f.staging_root / (old + ".bundle")).exists())
        self.assertEqual(plan["candidates"][0]["status"], "eligible")
        self.collect(apply=True)
        self.assertFalse((f.staging_root / (old + ".bundle")).exists())
        self.assertEqual((f.staging_root / (current + ".bundle")).read_bytes(), f.bundle)
        with f.store._locked(initialize=False) as root:
            records, charge, _ = f.store._scan(root)
        self.assertEqual(len(records), 2)
        self.assertIn(".retired", records[old][2])
        self.assertLess(charge, 2 * records[current][0]["reservation_bytes"])
        with self.assertRaisesRegex(ValueError, "Incomplete|ambiguous"):
            f.stage(self.records[0])
        self.assertFalse((f.staging_root / (old + ".bundle")).exists())
        self.collect(apply=True)

    def test_lease_contention_and_unfinished_conversion_defer(self):
        with patch("lib.build_conversion._load", return_value=dict(state="captured")), self.assertRaises(RuntimeError):
            self.collect(apply=True)
        with patch("lib.build_target_maintenance.target_maintenance", side_effect=RuntimeError("active")), self.assertRaises(RuntimeError):
            self.collect(apply=True)
        self.assertTrue((self.fixture.staging_root / (self.records[0]["operation_id"] + ".bundle")).exists())

    def test_missing_current_payload_or_active_maintenance_refuses(self):
        f = self.fixture
        current = self.records[-1]["operation_id"]
        (f.staging_root / (current + ".bundle")).unlink()
        with self.assertRaises(ValueError):
            self.collect(apply=True)
        self.assertTrue((f.staging_root / (self.records[0]["operation_id"] + ".bundle")).exists())

    def test_interrupted_unlink_needs_explicit_exact_retry(self):
        f = self.fixture
        old = self.records[0]["operation_id"]
        plan = self.collect()["candidates"][0]
        unlink = os.unlink

        def crash(path, *args, **kwargs):
            if path == old + ".bundle":
                unlink(path, *args, **kwargs)
                raise OSError("lost completion")
            return unlink(path, *args, **kwargs)

        with patch("lib.build_target_artifacts.os.unlink", side_effect=crash), self.assertRaises(OSError):
            self.collect(apply=True)
        self.assertEqual(self.collect(apply=True)["candidates"][0]["reason"], "retirement_requires_exact_retry")
        with self.assertRaisesRegex(ValueError, "revision"):
            self.collect(apply=True, retry_operation=old, expected_revision="f" * 64)
        self.collect(apply=True, retry_operation=old, expected_revision=plan["revision"])
        self.assertTrue((f.staging_root / (old + ".retired")).exists())
