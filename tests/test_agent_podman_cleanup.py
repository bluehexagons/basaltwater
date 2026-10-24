"""Offline safety contract for bounded rootless Podman image cleanup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from lib import maintenance_defaults
from lib import podman_cleanup as cleanup


def _image(identifier: str, *, created: int, tags=None, containers=0, labels=None, size=1000) -> dict:
    return {"Id": identifier, "Created": created, "RepoTags": tags, "Containers": containers,
            "Labels": labels, "Size": size}


class PodmanCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.graph = self.home / "podman-store"
        self.graph.mkdir(mode=0o700)
        self.now = 2_000_000_000
        self.cutoff_age = 10 * 86400
        self.images = [
            _image("a" * 64, created=self.now - self.cutoff_age, size=4096),
            _image("b" * 64, created=self.now - self.cutoff_age, tags=["kept:latest"]),
            _image("c" * 64, created=self.now - self.cutoff_age, containers=1),
            _image("d" * 64, created=self.now - 3600),
            _image("e" * 64, created=self.now - self.cutoff_age,
                   labels={maintenance_defaults.PODMAN_KEEP_LABEL: "true"}),
        ]
        self.commands = []
        self.free = [30 * 1024**3]
        self.disk_reads = 0

        def run(command, **kwargs):
            self.commands.append((command, kwargs))
            if "info" in command:
                payload = {"host": {"serviceIsRemote": False, "security": {"rootless": True}},
                           "store": {"graphRoot": str(self.graph),
                                     "containerStore": {"running": 0}}}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command[2:4] == ["image", "ls"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(self.images), "")
            if command[2:4] == ["image", "prune"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError("unexpected mocked Podman command")

        self.run = run
        patches = [
            patch.object(cleanup, "_home_and_identity", return_value=(str(self.home), os.getuid())),
            patch.object(cleanup.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))),
            patch.object(cleanup, "_mounts", return_value={str(self.home): "ext4"}),
            patch.object(cleanup.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(cleanup, "_process_activity", return_value=("eligible", None)),
            patch.object(cleanup, "run_captured", side_effect=run),
            patch.object(cleanup.time, "time", return_value=self.now),
            patch.object(cleanup.shutil, "disk_usage", side_effect=self.disk_usage),
            patch.dict(os.environ, {"CONTAINER_HOST": "", "CONTAINER_CONNECTION": ""}),
        ]
        self.stack = [item.start() for item in patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])

    def disk_usage(self, _path):
        value = self.free[min(self.disk_reads, len(self.free) - 1)]
        self.disk_reads += 1
        return SimpleNamespace(total=40 * 1024**3, used=40 * 1024**3 - value, free=value)

    def test_preview_is_read_only_and_filters_old_untagged_unreferenced_unkept_images(self):
        result = cleanup.clean_podman_images()
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["cleanup_executed"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidate_ids"], ["a" * 64])
        self.assertEqual(result["image_estimate_bytes"], 4096)
        self.assertEqual(len(self.commands), 2)
        self.assertFalse((self.home / ".local").exists())
        self.assertTrue(all(call[0][:2] == ["/usr/bin/podman", "--remote=false"]
                            for call in self.commands))

    def test_apply_uses_only_native_bounded_safe_prune_flags_and_measures_delta(self):
        self.free.append(29 * 1024**3)
        result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "cleaned")
        self.assertTrue(result["cleanup_executed"])
        self.assertEqual(result["free_bytes_change"], -1024**3)
        command, kwargs = self.commands[-1]
        self.assertEqual(command, [
            "/usr/bin/podman", "--remote=false", "image", "prune", "--force",
            "--filter", f"until={self.now - 7 * 86400}",
            "--filter", f"label!={maintenance_defaults.PODMAN_KEEP_LABEL}",
        ])
        self.assertEqual(kwargs["timeout"], cleanup._PRUNE_TIMEOUT)
        self.assertEqual(kwargs["max_bytes"], cleanup._OUTPUT_LIMIT)
        for unsafe in ("--all", "--external", "--build-cache", "system", "volume", "rmi"):
            self.assertNotIn(unsafe, command)
        self.assertTrue(any("newly dangling" in note for note in result["notes"]))

    def test_low_space_uses_one_day_retention_and_shared_threshold_formula(self):
        self.free[0] = 7 * 1024**3
        result = cleanup.clean_podman_images()
        self.assertTrue(result["pressure"])
        self.assertEqual(result["retention_days"], maintenance_defaults.PODMAN_IMAGE_PRESSURE_RETENTION_DAYS)
        self.assertEqual(result["cutoff_epoch"], self.now - 86400)
        self.assertEqual(result["pressure_threshold_bytes"], min(
            maintenance_defaults.USER_CACHE_FREE_MAX_BYTES,
            max(maintenance_defaults.USER_CACHE_FREE_MIN_BYTES, (40 * 1024**3) // 5),
        ))

    def test_apply_under_pressure_uses_one_day_cutoff(self):
        self.free[0] = 1024**3
        result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "cleaned")
        self.assertIn(f"until={self.now - 86400}", self.commands[-1][0])

    def test_native_store_configuration_is_preserved_for_probes_and_prune(self):
        configuration = {"XDG_RUNTIME_DIR": str(self.home / "runtime"),
                         "XDG_CONFIG_HOME": str(self.home / "config"),
                         "CONTAINERS_STORAGE_CONF": str(self.home / "storage.conf")}
        with patch.dict(os.environ, configuration):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "cleaned")
        for _argv, options in self.commands:
            self.assertEqual({key: options["env"][key] for key in configuration}, configuration)

    def test_nonlocal_or_shared_writable_graph_root_is_refused(self):
        with patch.object(cleanup, "_mounts", return_value={str(self.home): "nfs4"}):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["issue"], "unsafe_graph_root")
        self.assertFalse(result["cleanup_attempted"])
        self.graph.chmod(0o777)
        result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["issue"], "unsafe_graph_root")

    def test_malformed_security_metadata_defers_without_prune(self):
        def malformed(command, **kwargs):
            result = self.run(command, **kwargs)
            if "info" in command:
                payload = json.loads(result.stdout)
                payload["host"]["security"] = []
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return result
        with patch.object(cleanup, "run_captured", side_effect=malformed):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "deferred_unknown")
        self.assertEqual(result["issue"], "invalid_podman_info")
        self.assertFalse(result["cleanup_attempted"])

    def test_candidate_preview_is_bounded_while_count_remains_complete(self):
        self.images = [
            _image(f"{index:064x}", created=self.now - 10 * 86400)
            for index in range(cleanup._PREVIEW_LIMIT + 3)
        ]
        result = cleanup.clean_podman_images()
        self.assertEqual(result["candidate_count"], cleanup._PREVIEW_LIMIT + 3)
        self.assertEqual(len(result["candidate_ids"]), cleanup._PREVIEW_LIMIT)
        self.assertTrue(result["candidate_preview_truncated"])

    def test_lock_busy_and_active_process_defer_before_inventory_or_mutation(self):
        with patch.object(cleanup.fcntl, "flock", side_effect=BlockingIOError):
            locked = cleanup.clean_podman_images(apply=True)
        self.assertEqual(locked["status"], "deferred_active")
        self.assertEqual(locked["issue"], "cleanup_already_running")
        self.assertFalse(self.commands)

        self.commands.clear()
        with patch.object(cleanup, "_process_activity", return_value=("deferred_active", "active_container_workload")):
            active = cleanup.clean_podman_images(apply=True)
        self.assertEqual(active["status"], "deferred_active")
        self.assertEqual(len(self.commands), 1)  # engine info only

    def test_unknown_process_inventory_malformed_image_data_and_running_containers_defer(self):
        with patch.object(cleanup, "_process_activity", side_effect=cleanup._Unknown("process_inventory_incomplete")):
            result = cleanup.clean_podman_images()
        self.assertEqual(result["status"], "deferred_unknown")
        self.commands.clear()
        self.images = {"unexpected": "object"}
        result = cleanup.clean_podman_images()
        self.assertEqual(result["status"], "deferred_unknown")
        self.assertEqual(result["issue"], "invalid_image_inventory")

        self.commands.clear()
        self.images = []
        def running(command, **kwargs):
            result = self.run(command, **kwargs)
            if "info" in command:
                payload = json.loads(result.stdout)
                payload["store"]["containerStore"]["running"] = 1
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return result
        with patch.object(cleanup, "run_captured", side_effect=running):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "deferred_active")
        self.assertEqual(result["issue"], "running_containers")

    def test_remote_rootful_and_unsafe_graph_root_are_refused(self):
        with patch.dict(os.environ, {"CONTAINER_HOST": "tcp://example.invalid"}):
            remote = cleanup.clean_podman_images(apply=True)
        self.assertEqual(remote["issue"], "remote_selection_unsupported")
        self.assertFalse(self.commands)

        def rootful(command, **kwargs):
            result = self.run(command, **kwargs)
            if "info" in command:
                payload = json.loads(result.stdout)
                payload["host"]["security"]["rootless"] = False
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return result
        with patch.object(cleanup, "run_captured", side_effect=rootful):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["issue"], "rootful_engine_unsupported")

        self.commands.clear()
        def outside(command, **kwargs):
            result = self.run(command, **kwargs)
            if "info" in command:
                payload = json.loads(result.stdout)
                payload["store"]["graphRoot"] = "/tmp"
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return result
        with patch.object(cleanup, "run_captured", side_effect=outside):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "deferred_unknown")
        self.assertEqual(result["issue"], "unsafe_graph_root")

    def test_prune_timeout_or_failure_is_uncertain_and_never_retried(self):
        attempts = 0

        def timeout(command, **kwargs):
            nonlocal attempts
            if command[2:4] == ["image", "prune"]:
                attempts += 1
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return self.run(command, **kwargs)
        with patch.object(cleanup, "run_captured", side_effect=timeout):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["cleanup_attempted"])
        self.assertFalse(result["cleanup_executed"])
        self.assertEqual(attempts, 1)

        self.commands.clear()
        def failure(command, **kwargs):
            if command[2:4] == ["image", "prune"]:
                self.commands.append((command, kwargs))
                return subprocess.CompletedProcess(command, 125, "", "private provider output")
            return self.run(command, **kwargs)
        with patch.object(cleanup, "run_captured", side_effect=failure):
            result = cleanup.clean_podman_images(apply=True)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["issue"], "prune_result_uncertain")
        self.assertNotIn("private provider output", json.dumps(result))
        self.assertEqual(sum(command[2:4] == ["image", "prune"] for command, _ in self.commands), 1)

    def test_missing_engine_is_absent_and_invalid_arguments_fail_fast(self):
        with patch.object(cleanup.shutil, "which", return_value=None):
            result = cleanup.clean_podman_images()
        self.assertEqual(result["status"], "absent")
        with self.assertRaises(TypeError):
            cleanup.clean_podman_images(apply=1)


class PodmanProcessGuardTests(unittest.TestCase):
    def test_build_tool_defers_but_unrelated_unicode_name_does_not(self):
        entry = SimpleNamespace(name="123", path="/proc/123",
                                stat=Mock(return_value=SimpleNamespace(st_uid=42)))
        for name, expected in ((b"buildah\n", "deferred_active"), ("模型工具\n".encode(), "eligible")):
            with self.subTest(name=name), patch.object(cleanup.os, "scandir") as scan, \
                    patch.object(cleanup.time, "monotonic", return_value=1), \
                    patch("builtins.open", mock_open(read_data=name)):
                scan.return_value.__enter__.return_value = [entry]
                self.assertEqual(cleanup._process_activity(42)[0], expected)

    def test_exited_process_is_ignored_and_unreadable_process_defers(self):
        entry = SimpleNamespace(name="123", path="/proc/123", stat=Mock())
        with patch.object(cleanup.os, "scandir") as scan, \
                patch.object(cleanup.time, "monotonic", return_value=1):
            scan.return_value.__enter__.return_value = [entry]
            entry.stat.side_effect = FileNotFoundError()
            self.assertEqual(cleanup._process_activity(42), ("eligible", None))
            entry.stat.side_effect = PermissionError()
            with self.assertRaisesRegex(cleanup._Unknown, "process_inventory_incomplete"):
                cleanup._process_activity(42)

    def test_root_or_mismatched_privileges_are_refused_before_probing(self):
        for real, effective in ((0, 0), (42, 43)):
            with self.subTest(real=real, effective=effective), \
                    patch.object(cleanup.os, "getuid", return_value=real), \
                    patch.object(cleanup.os, "geteuid", return_value=effective), \
                    patch.object(cleanup, "run_captured") as run:
                result = cleanup.clean_podman_images(apply=True)
                self.assertEqual(result["issue"], "unsafe_account")
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
