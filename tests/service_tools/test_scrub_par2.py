"""Tests for par2 scrub operations."""

from __future__ import annotations

import os
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from lib.runtime_config import RuntimeConfig
from sync.service_tools.scrub_par2 import _par2_base_from_parity_file
from sync.service_tools import scrub_runner


class TestPar2BaseFromParityFile(unittest.TestCase):
    """Tests for _par2_base_from_parity_file function.
    
    This function extracts the base par2 filename from various par2 file formats.
    Critical for orphan detection to avoid false positives that delete valid par2 files.
    """
    
    def test_volume_only_file(self):
        """Test extraction of base path from volume-only par2 files (created with -n1).
        
        Volume-only files have pattern: filename.vol000+100.par2
        This was the bug: the old code didn't recognize this pattern and treated
        these as orphans, causing a delete/recreate cycle.
        """
        self.assertEqual(
            _par2_base_from_parity_file('/database/Blender/lampshade_1b.blend.vol000+100.par2'),
            '/database/Blender/lampshade_1b.blend.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.vol00+01.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/test.vol999+999.par2'),
            '/database/test.par2'
        )
    
    def test_base_plus_volume_file(self):
        """Test extraction of base path from base+volume par2 files (created with -n2+).
        
        Base+volume files have pattern: filename.par2.vol00+01.par2
        This format was already working correctly.
        """
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.par2.vol00+01.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/test.par2.vol000+100.par2'),
            '/database/test.par2'
        )
    
    def test_base_file(self):
        """Test that base par2 files are returned unchanged."""
        self.assertEqual(
            _par2_base_from_parity_file('/database/file.par2'),
            '/database/file.par2'
        )
        self.assertEqual(
            _par2_base_from_parity_file('/database/Blender/project.blend.par2'),
            '/database/Blender/project.blend.par2'
        )
    
    def test_edge_case_vol_in_filename(self):
        """Test that files with .vol in their actual name are handled correctly.
        
        A file named "data.vol.txt" should have its base par2 as "data.vol.txt.par2"
        and should NOT be confused with a volume file.
        """
        # Base file with .vol in the data filename
        self.assertEqual(
            _par2_base_from_parity_file('/database/data.vol.txt.par2'),
            '/database/data.vol.txt.par2'
        )
        # Volume file for a data file that has .vol in its name
        self.assertEqual(
            _par2_base_from_parity_file('/database/data.vol.txt.vol001+050.par2'),
            '/database/data.vol.txt.par2'
        )
    
    def test_unusual_patterns(self):
        """Test edge cases with unusual but valid patterns."""
        # Multiple dots in filename
        self.assertEqual(
            _par2_base_from_parity_file('/db/archive.tar.gz.vol001+010.par2'),
            '/db/archive.tar.gz.par2'
        )
        # Very long volume numbers
        self.assertEqual(
            _par2_base_from_parity_file('/db/file.vol12345+67890.par2'),
            '/db/file.par2'
        )
        # Nested directories
        self.assertEqual(
            _par2_base_from_parity_file('/db/a/b/c/d/file.vol00+01.par2'),
            '/db/a/b/c/d/file.par2'
        )


class TestScrubRunner(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = RuntimeConfig.from_dict({"username": "agent"})

    def _worker_script(self, name: str, lines: list[str]) -> str:
        path = self.root / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _run_injected_worker(self, *, timeout: float = 2.0):
        return scrub_runner.run_scrub_supervised(
            str(self.root), str(self.root / "parity"), "10%", False, self.config,
            log_dir=str(self.root), preflight_timeout=timeout,
            operation_timeout=timeout,
        )

    def _success_report_source(self) -> str:
        report = {
            "version": 1,
            "ok": True,
            "completed": True,
            "files_processed": 0,
            "files_created": 0,
            "files_verified": 0,
            "files_repaired": 0,
            "files_failed_count": 0,
            "files_unrepairable_count": 0,
            "files_unrepairable_sample": [],
            "directory_disk_usage_percent": 0,
            "database_disk_usage_percent": 0,
            "error": None,
        }
        return json.dumps(report)

    def test_report_contract_distinguishes_completed_damage_from_incomplete_work(self):
        usage = {"usage_percent": 20}

        damaged = scrub_runner._report_from_result({
            "ok": False,
            "completed": True,
            "files_processed": 1,
            "files_created": 0,
            "files_verified": 1,
            "files_repaired": 0,
            "files_failed": [],
            "files_unrepairable": ["damaged.bin"],
        }, usage, usage)
        incomplete = scrub_runner._report_from_result({
            "ok": False,
            "completed": False,
            "files_processed": 1,
            "files_created": 0,
            "files_verified": 1,
            "files_repaired": 0,
            "files_failed": ["unreadable.bin"],
            "files_unrepairable": [],
        }, usage, usage)

        self.assertTrue(damaged["completed"])
        self.assertFalse(damaged["ok"])
        self.assertEqual(damaged["files_unrepairable_count"], 1)
        self.assertFalse(incomplete["completed"])
        self.assertEqual(incomplete["files_failed_count"], 1)

        malformed = scrub_runner._base_report()
        malformed.update({
            "completed": True,
            "directory_disk_usage_percent": 20,
            "database_disk_usage_percent": 20,
        })
        with tempfile.TemporaryFile() as report_stream:
            report_stream.write(json.dumps(malformed).encode("utf-8"))
            report_stream.seek(0)
            with self.assertRaisesRegex(ValueError, "Invalid scrub worker report"):
                scrub_runner._read_worker_report(report_stream)

    def test_worker_request_sends_mount_paths_without_credentials(self):
        config = RuntimeConfig.from_dict({
            "username": "agent",
            "storage_mounts": [["archive", "/srv/archive"]],
            "smb_mounts": [["/mnt/share", "server", "do-not-send", "data"]],
        })
        request = scrub_runner._worker_request(
            str(self.root), str(self.root / "parity"), "10%", False, config,
            log_dir=str(self.root), log_file=None, suppress_notifications=True,
        )

        self.assertEqual(request["mounts"], ["/mnt/share", "/srv/archive"])
        self.assertEqual(request["smb_mounts"], ["/mnt/share"])
        self.assertNotIn("do-not-send", json.dumps(request))
        self.assertNotIn("server", json.dumps(request))

    def test_new_directory_ownership_uses_inline_chown(self):
        path = str(self.root / "created-by-worker")
        with (
            patch.object(scrub_runner.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=123)),
            patch.object(scrub_runner.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=456)),
            patch.object(scrub_runner.os, "chown") as chown,
            patch("lib.task_utils.run") as detached_command,
        ):
            scrub_runner._ensure_directory(path, "agent")

        self.assertTrue(os.path.isdir(path))
        chown.assert_called_once_with(path, 123, 456)
        detached_command.assert_not_called()

    def test_unavailable_endpoint_and_nested_mounts_fail_worker_preflight(self):
        from sync.service_tools import storage_ops

        nested = str(self.root / "nested-mount")
        config = RuntimeConfig.from_dict({
            "username": "agent",
            "storage_mounts": [
                ["source", str(self.root)],
                ["nested", nested],
            ],
        })
        with patch.object(
            storage_ops, "validate_mounts_for_operation",
            side_effect=[(False, "source mount unavailable")],
        ) as validate:
            with self.assertRaisesRegex(RuntimeError, "source mount unavailable"):
                scrub_runner._validate_worker_mounts(
                    str(self.root), str(self.root.parent / "parity"), config,
                )
        validate.assert_called_once()

        with patch.object(
            storage_ops, "validate_mounts_for_operation",
            side_effect=[(True, ""), (True, ""), (False, "nested mount unavailable")],
        ) as validate:
            with self.assertRaisesRegex(RuntimeError, "nested mount unavailable"):
                scrub_runner._validate_worker_mounts(
                    str(self.root), str(self.root.parent / "parity"), config,
                )
        self.assertEqual(validate.call_args.args[0], [nested])

    def test_invalid_lexical_path_never_launches_worker(self):
        with patch.object(scrub_runner.subprocess, "Popen") as start:
            result = scrub_runner.run_scrub_supervised(
                "relative", str(self.root / "parity"), "10%", False, self.config,
                log_dir=str(self.root),
            )
        start.assert_not_called()
        self.assertFalse(result["completed"])
        self.assertIn("absolute path", result["error"])

    def test_blocked_preflight_times_out_and_reaps_its_worker(self):
        pid_file = str(self.root / "worker.pid")
        script = self._worker_script("blocked_worker.py", [
            "import os, time",
            f"open({pid_file!r}, 'w', encoding='ascii').write(str(os.getpid()))",
            "time.sleep(30)",
        ])
        with patch.object(scrub_runner, "WORKER_SCRIPT", script):
            result = self._run_injected_worker(timeout=0.15)

        self.assertFalse(result["completed"])
        self.assertIn("scrub preflight timed out", result["error"])
        worker_pid = int(Path(pid_file).read_text(encoding="ascii"))
        self.assertFalse(scrub_runner._process_group_exists(worker_pid))

    def test_cancellation_reaps_worker_group_before_propagating(self):
        pid_file = str(self.root / "worker.pid")
        script = self._worker_script("cancelled_worker.py", [
            "import os, time",
            f"open({pid_file!r}, 'w', encoding='ascii').write(str(os.getpid()))",
            "time.sleep(30)",
        ])

        def interrupt_after_worker_start(process, phase_fd, **_timeouts):
            deadline = time.monotonic() + 2
            while not os.path.exists(pid_file) and time.monotonic() < deadline:
                time.sleep(0.01)
            raise KeyboardInterrupt

        with patch.object(scrub_runner, "WORKER_SCRIPT", script), \
                patch.object(scrub_runner, "_supervise_worker", side_effect=interrupt_after_worker_start):
            with self.assertRaises(KeyboardInterrupt):
                self._run_injected_worker()

        worker_pid = int(Path(pid_file).read_text(encoding="ascii"))
        self.assertFalse(scrub_runner._process_group_exists(worker_pid))

    def test_clean_leader_exit_with_surviving_child_rejects_report(self):
        worker_pid_file = str(self.root / "worker.pid")
        child_pid_file = str(self.root / "child.pid")
        report_payload = self._success_report_source()
        marker_literal = repr(scrub_runner.WORKER_STARTED_MARKER)
        script = self._worker_script("survivor_worker.py", [
            "import os, subprocess, sys",
            "report_fd, started_fd = int(sys.argv[2]), int(sys.argv[3])",
            f"open({worker_pid_file!r}, 'w', encoding='ascii').write(str(os.getpid()))",
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])",
            f"open({child_pid_file!r}, 'w', encoding='ascii').write(str(child.pid))",
            f"os.write(started_fd, {marker_literal})",
            f"os.write(report_fd, {report_payload!r}.encode('utf-8'))",
        ])
        with patch.object(scrub_runner, "WORKER_SCRIPT", script):
            result = self._run_injected_worker()

        self.assertFalse(result["completed"])
        self.assertIn("descendants remained", result["error"])
        worker_pid = int(Path(worker_pid_file).read_text(encoding="ascii"))
        self.assertFalse(scrub_runner._process_group_exists(worker_pid))

    def test_par2_style_native_child_stays_in_supervised_worker_group(self):
        worker_pid_file = str(self.root / "worker.pid")
        child_pgid_file = str(self.root / "child.pgid")
        report_payload = self._success_report_source()
        marker_literal = repr(scrub_runner.WORKER_STARTED_MARKER)
        repository = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        child_code = f"import os; open({child_pgid_file!r}, 'w').write(str(os.getpgrp()))"
        script = self._worker_script("native_worker.py", [
            "import os, subprocess, sys",
            f"sys.path.insert(0, {repository!r})",
            "from lib.streamed_process import caller_process_group",
            "report_fd, started_fd = int(sys.argv[2]), int(sys.argv[3])",
            f"open({worker_pid_file!r}, 'w', encoding='ascii').write(str(os.getpid()))",
            "with caller_process_group():",
            f"    subprocess.run([sys.executable, '-c', {child_code!r}], check=True)",
            f"    os.write(started_fd, {marker_literal})",
            f"    os.write(report_fd, {report_payload!r}.encode('utf-8'))",
        ])
        with patch.object(scrub_runner, "WORKER_SCRIPT", script):
            result = self._run_injected_worker()

        self.assertTrue(result["ok"])
        self.assertTrue(result["completed"])
        worker_pid = int(Path(worker_pid_file).read_text(encoding="ascii"))
        child_pgid = int(Path(child_pgid_file).read_text(encoding="ascii"))
        self.assertEqual(child_pgid, worker_pid)

    def test_storage_operation_lock_is_held_until_timed_out_group_is_gone(self):
        from sync.service_tools import storage_ops

        pid_file = str(self.root / "worker.pid")
        script = self._worker_script("locked_worker.py", [
            "import os, time",
            f"open({pid_file!r}, 'w', encoding='ascii').write(str(os.getpid()))",
            "time.sleep(30)",
        ])
        config = {
            "username": "agent",
            "sync_specs": [],
            "scrub_specs": [[str(self.root), ".parity", "10%", "weekly"]],
            "notify_specs": [],
        }
        original_runner = storage_ops.run_scrub_supervised
        locks = []

        class ObservedLock(storage_ops.OperationLock):
            def __init__(self, lock_path):
                super().__init__(lock_path)
                self.group_gone_before_release = False
                locks.append(self)

            def __exit__(self, exc_type, exc_value, traceback):
                if os.path.exists(pid_file):
                    worker_pid = int(Path(pid_file).read_text(encoding="ascii"))
                    self.group_gone_before_release = not scrub_runner._process_group_exists(worker_pid)
                return super().__exit__(exc_type, exc_value, traceback)

        def short_preflight(*args, **kwargs):
            kwargs["preflight_timeout"] = 0.15
            return original_runner(*args, **kwargs)

        with (
            patch.object(scrub_runner, "WORKER_SCRIPT", script),
            patch.object(storage_ops, "LOCK_FILE", str(self.root / "storage-ops.lock")),
            patch.object(storage_ops, "STATE_FILE", str(self.root / "last_run.json")),
            patch.object(storage_ops, "LOG_DIR", str(self.root)),
            patch.object(storage_ops, "OperationLock", ObservedLock),
            patch.object(storage_ops, "load_setup_config", return_value=config),
            patch.object(storage_ops, "parse_notification_args", return_value=[]),
            patch.object(storage_ops, "get_service_logger", return_value=SimpleNamespace(
                log=lambda *_: None, debug=lambda *_: None,
            )),
            patch.object(storage_ops, "run_scrub_supervised", side_effect=short_preflight),
        ):
            result = storage_ops.main()

        self.assertEqual(result, 1)
        self.assertEqual(len(locks), 1)
        self.assertTrue(locks[0].group_gone_before_release)


class TestScrubStepPreflightBoundary(unittest.TestCase):
    def test_setup_passes_current_mount_declarations_to_runner(self):
        from sync import scrub_steps

        config = SimpleNamespace(
            username="agent",
            storage_mounts=[["archive", "/srv/archive"]],
            smb_mounts=[["/mnt/share", "server", "credential", "data"]],
        )
        logger = SimpleNamespace(
            log_step=lambda *_args: None,
            log_metric=lambda *_args: None,
            log_error=lambda *_args: None,
            complete=lambda *_args: None,
        )
        report = {
            "ok": True, "completed": True,
            "directory_disk_usage_percent": 25,
            "database_disk_usage_percent": 50,
        }
        with patch.object(scrub_steps, "create_operation_logger", return_value=logger), \
                patch("sync.service_tools.scrub_runner.run_scrub_supervised", return_value=report) as runner:
            scrub_steps.create_scrub_service(config, ["/srv/data", ".db", "10%", "daily"])

        runner.assert_called_once()
        self.assertIs(runner.call_args.kwargs["config"], config)
        self.assertEqual(runner.call_args.args[:3], ("/srv/data", "/srv/data/.db", 10))
        options = dict(runner.call_args.kwargs)
        options.pop("config")
        self.assertNotIn("credential", json.dumps(options))


if __name__ == '__main__':
    unittest.main()
