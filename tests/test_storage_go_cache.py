"""Go cache cleanup tests use synthetic processes and mocked native commands."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import go_cache_cleanup as go
from lib.agent_storage_inventory import add_storage_parser, run_storage_command


class TestGoActivity(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.tool = self.root / "toolchain/bin/go"
        self.tool.parent.mkdir(parents=True)
        self.tool.write_bytes(b"\x7fELFgo")
        self.tool.chmod(0o755)
        (self.tool.parent.parent / "VERSION").write_text("go1.27.3\n")
        self.program = self.root / "go-build123/b001/exe/server"
        self.program.parent.mkdir(parents=True)
        self.program.write_bytes(b"\x7fELFserver")
        self.program.chmod(0o755)
        for directory in self.root.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
        pattern = re.compile(re.escape(str(self.root)) + r"/go-build[0-9]+/b[0-9]+/exe/[^/]+$")
        self.enterContext(patch.object(go, "_TEMP_PROGRAM", pattern))
        self.process(10, "go", self.tool, [str(self.tool), "run", "."])
        self.process(20, "server", self.program, [str(self.program)], parent=10)
        self.children("10", "10", "")
        self.children("10", "11", "20")

    def process(self, pid, name, executable, args, parent=1, start=100):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / "comm").write_text(name + "\n")
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start)]
        (directory / "stat").write_text(f"{pid} ({name}) " + " ".join(fields))
        (directory / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in args) + b"\0")
        if not (directory / "exe").is_symlink():
            (directory / "exe").symlink_to(executable)
        return directory

    def children(self, pid, tid, children):
        directory = self.proc / pid / "task" / tid
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "children").write_text(children)

    def activity(self):
        return go.go_build_activity(proc_root=str(self.proc))

    def test_completed_run_child_on_nonleader_thread_is_exempt(self):
        self.assertEqual(self.activity(), {"state": "eligible", "blocking_pids": [],
                                          "waiting_run_pids": [10], "reason": None})

    def test_builds_before_compiler_start_and_other_tools_block(self):
        for name in ("go", "compile", "link", "asm", "cgo", "gopls"):
            with self.subTest(name=name):
                directory = self.process(30, name, self.tool, [str(self.tool), "build"])
                self.assertEqual(self.activity()["blocking_pids"], [30])
                for item in directory.iterdir():
                    item.unlink()
                directory.rmdir()

    def test_flags_program_arguments_and_execution_hooks_are_not_exempt(self):
        for args in ([str(self.tool), "run", "-exec=helper", "."],
                     [str(self.tool), "run", ".", "--serve"],
                     [str(self.tool), "run", "-toolexec=helper", "."]):
            with self.subTest(args=args):
                (self.proc / "10/cmdline").write_bytes(b"\0".join(os.fsencode(a) for a in args) + b"\0")
                self.assertEqual(self.activity()["state"], "deferred_active")

    def test_inherited_hooks_have_additional_child_arguments(self):
        for args in ([str(self.program), "/tool/compile", "-V=full"],
                     [str(self.program), "/tmp/actual-program"]):
            (self.proc / "20/cmdline").write_bytes(b"\0".join(os.fsencode(a) for a in args) + b"\0")
            self.assertEqual(self.activity()["state"], "deferred_active")

    def test_indirect_or_missing_child_and_extra_children_block(self):
        self.children("10", "11", "")
        self.assertEqual(self.activity()["state"], "deferred_active")
        self.children("10", "11", "20 30")
        self.assertEqual(self.activity()["state"], "deferred_active")
        self.children("10", "11", "20")
        self.process(20, "server", self.program, [str(self.program)], parent=30)
        self.assertEqual(self.activity()["state"], "deferred_active")

    def test_cache_resident_or_spoofed_programs_block(self):
        for name in ("cache/program", "go-build123/b001/exe/other/program"):
            program = self.root / name
            program.parent.mkdir(parents=True, exist_ok=True)
            program.write_bytes(b"\x7fELF")
            (self.proc / "20/exe").unlink()
            (self.proc / "20/exe").symlink_to(program)
            (self.proc / "20/cmdline").write_bytes(os.fsencode(program) + b"\0")
            self.assertEqual(self.activity()["state"], "deferred_active")

    def test_fake_go_name_is_not_identity_evidence(self):
        (self.tool.parent.parent / "VERSION").unlink()
        self.assertEqual(self.activity()["state"], "deferred_unknown")

    def test_unrelated_protected_fd_directory_is_never_inspected(self):
        self.process(30, "sd-pam", "/unreadable/executable", ["sd-pam"])
        real_read = go._read
        def read(path, *args):
            if "/fd/" in path or path.endswith("environ") or "/30/" in path and not path.endswith("comm"):
                raise AssertionError("unrelated private metadata read")
            return real_read(path, *args)
        with patch.object(go, "_read", side_effect=read):
            self.assertEqual(self.activity()["state"], "eligible")

    def test_metadata_failures_and_truncation_defer(self):
        for content in (b"", b"go\0run\0." , b"x" * 65537):
            (self.proc / "10/cmdline").write_bytes(content)
            self.assertEqual(self.activity()["state"], "deferred_unknown")
        with patch.object(go, "_read", side_effect=PermissionError):
            self.assertEqual(self.activity()["state"], "deferred_unknown")

    def test_pid_reuse_invalidates_exemption(self):
        real = go._process
        seen = 0
        def state(directory):
            nonlocal seen
            value = real(directory)
            if directory.endswith("/10"):
                seen += 1
                if seen > 2:
                    return value[0], value[1] + 1, value[2]
            return value
        with patch.object(go, "_process", side_effect=state):
            self.assertNotEqual(self.activity()["state"], "eligible")

    def test_inventory_limits_and_iteration_failure_defer(self):
        with patch.object(go.time, "monotonic", side_effect=[0, 4]):
            self.assertEqual(self.activity()["state"], "deferred_unknown")
        self.children("10", "11", " ".join(str(n) for n in range(257)))
        self.assertEqual(self.activity()["state"], "deferred_unknown")
        with patch.object(go.os, "scandir") as scan:
            scan.return_value.__enter__.return_value.__iter__.side_effect = PermissionError
            self.assertEqual(self.activity()["state"], "deferred_unknown")


class TestGoCleanup(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = self.home / ".cache/go-build"
        self.cache.mkdir(parents=True)
        self.tool = self.home / "toolchain/bin/go"
        self.tool.parent.mkdir(parents=True)
        self.tool.write_bytes(b"\x7fELF")
        self.tool.chmod(0o755)
        (self.tool.parent.parent / "VERSION").write_text("go1.27.3\n")
        self.enterContext(patch.object(go.os, "getuid", return_value=1000))
        self.enterContext(patch.object(go.os, "geteuid", return_value=1000))
        self.enterContext(patch.object(go.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.enterContext(patch.object(go.shutil, "which", return_value=str(self.tool)))
        self.identity = self.enterContext(patch.object(go, "_cache_identity", return_value=(1, 2)))
        self.measure = self.enterContext(patch.object(go, "_measure", return_value={"allocated_bytes": 200, "apparent_bytes": 190, "files": 1}))
        self.activity = self.enterContext(patch.object(go, "go_build_activity", return_value={"state": "eligible", "waiting_run_pids": [10]}))
        self.run = self.enterContext(patch.object(go, "run_captured"))
        self.run.side_effect = [subprocess.CompletedProcess([], 0, str(self.cache) + "\n", ""),
                                subprocess.CompletedProcess([], 0, "", "")]

    def test_preview_queries_but_never_cleans(self):
        self.assertEqual(go.clean_go_build_cache()["status"], "preview")
        self.assertEqual(self.run.call_count, 1)

    def test_managed_go_path_survives_the_minimal_launcher_environment(self):
        with patch.dict(os.environ, {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}):
            self.assertEqual(go.clean_go_build_cache(search_path="/usr/local/go/bin:/usr/bin")["status"], "preview")
        self.assertEqual(go.shutil.which.call_args.kwargs["path"], "/usr/local/go/bin:/usr/bin")
        self.assertEqual(self.run.call_args.kwargs["env"]["PATH"], "/usr/local/go/bin:/usr/bin")

    def test_mismatched_maintenance_account_fails_before_any_native_command(self):
        result = go.clean_go_build_cache(apply=True, expected_home=str(self.home / "other"))
        self.assertEqual(result["status"], "failed")
        self.run.assert_not_called()
        self.activity.assert_not_called()

    def test_apply_pins_native_command_and_cache_without_modules_or_hooks(self):
        result = go.clean_go_build_cache(apply=True)
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(self.run.call_args.args[0], [str(self.tool), "clean", "-cache"])
        environment = self.run.call_args.kwargs["env"]
        self.assertEqual(environment["GOCACHE"], str(self.cache))
        self.assertEqual(environment["GOENV"], "off")
        self.assertEqual(environment["GOTOOLCHAIN"], "local")
        self.assertEqual(environment["GOFLAGS"], "")
        self.assertEqual(environment["GOCACHEPROG"], "")

    def test_build_appearing_during_measurement_prevents_cleanup(self):
        events = []
        self.measure.side_effect = lambda *a: events.append("measure") or {"apparent_bytes": 10}
        self.activity.side_effect = lambda: events.append("guard") or {"state": "deferred_active"}
        self.assertEqual(go.clean_go_build_cache(apply=True)["status"], "deferred_active")
        self.assertEqual(events, ["measure", "guard"])
        self.assertEqual(self.run.call_count, 1)

    def test_unknown_activity_and_changed_cache_fail_closed(self):
        self.activity.return_value = {"state": "deferred_unknown"}
        self.assertEqual(go.clean_go_build_cache(apply=True)["status"], "deferred_unknown")
        self.activity.return_value = {"state": "eligible"}
        self.run.side_effect = [subprocess.CompletedProcess([], 0, str(self.cache), "")]
        self.identity.side_effect = [(1, 2), (1, 3)]
        self.assertEqual(go.clean_go_build_cache(apply=True)["status"], "failed")
        self.assertEqual(self.run.call_count, 2)

    def test_below_threshold_and_missing_cache_are_noops(self):
        self.assertEqual(go.clean_go_build_cache(needs_cleanup=lambda *a: False)["status"], "below_limits")
        self.activity.assert_not_called()
        self.run.side_effect = [subprocess.CompletedProcess([], 0, str(self.cache), "")]
        self.identity.side_effect = FileNotFoundError
        self.assertEqual(go.clean_go_build_cache(apply=True)["status"], "absent")

    def test_native_failure_is_not_success_or_a_private_output_dump(self):
        self.run.side_effect = [subprocess.CompletedProcess([], 0, str(self.cache), ""),
                               subprocess.CompletedProcess([], 1, "private-output", "private-error")]
        result = go.clean_go_build_cache(apply=True)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private-", json.dumps(result))

    def test_cli_defaults_to_preview_and_deferral_exits_nonzero(self):
        parser = argparse.ArgumentParser()
        add_storage_parser(parser.add_subparsers())
        for status, code in (("preview", 0), ("cleaned", 0), ("deferred_active", 1), ("deferred_unknown", 1), ("failed", 1)):
            with self.subTest(status=status), patch.object(go, "clean_go_build_cache", return_value={"status": status}) as clean, patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(run_storage_command(parser.parse_args(["storage", "clean-go-build", "--json"])), code)
                clean.assert_called_once_with(apply=False)


class TestGoCacheValidation(unittest.TestCase):
    def test_target_validation_preserves_outside_and_linked_data(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as outside:
            cache = Path(home) / "cache"
            cache.mkdir(mode=0o700)
            self.assertEqual(go._cache_identity(str(cache), home), (cache.stat().st_dev, cache.stat().st_ino))
            for target in (home, outside, str(cache / "..")):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    go._cache_identity(target, home)
            linked = Path(home) / "linked"
            linked.symlink_to(cache)
            with self.assertRaises(ValueError):
                go._cache_identity(str(linked), home)


if __name__ == "__main__":
    unittest.main()
