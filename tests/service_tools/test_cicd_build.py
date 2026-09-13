"""Credential isolation and the untrusted artifact handoff."""

from __future__ import annotations

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import cicd_build as build
from web.service_tools import cicd_executor as executor


class TestBuildIdentity(unittest.TestCase):
    def test_receipt_operations_use_receiver_identity_and_restore_after_failure(self):
        changes = []
        with patch.object(build.os, "geteuid", return_value=0), patch.object(
            build.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1101, pw_gid=1102),
        ), patch.object(build.os, "getegid", return_value=0), patch.object(
            build.os, "seteuid", side_effect=lambda uid: changes.append(("uid", uid)),
        ), patch.object(build.os, "setegid", side_effect=lambda gid: changes.append(("gid", gid))):
            with self.assertRaisesRegex(OSError, "ledger failure"):
                with build.receiver_state():
                    self.assertEqual(changes[-1], ("uid", 1101))
                    raise OSError("ledger failure")
        self.assertEqual(changes, [("gid", 1102), ("uid", 1101), ("uid", 0), ("gid", 0)])

    def test_build_has_no_broker_environment_groups_or_capabilities(self):
        accounts = {
            build.BUILD_USER: SimpleNamespace(pw_uid=1100, pw_gid=1100, pw_dir=build.BUILD_HOME),
            "webhook": SimpleNamespace(pw_uid=1101, pw_gid=1101),
        }
        with patch.object(build.os, "geteuid", return_value=0), patch.object(
            build.pwd, "getpwnam", side_effect=accounts.__getitem__,
        ), patch.object(build.grp, "getgrgid", return_value=SimpleNamespace(gr_name=build.BUILD_USER)), patch.object(build, "run_command") as run, patch.dict(os.environ, {
            "WEBHOOK_SECRET": "private", "SSH_AUTH_SOCK": "/run/agent", "LD_PRELOAD": "evil",
        }):
            build.run_build_command(["git", "clone", "https://example/repo"], timeout=300)
        command = run.call_args.args[0]
        for flag in ("--reuid=1100", "--regid=1100", "--clear-groups", "--inh-caps=-all",
                     "--ambient-caps=-all", "--bounding-set=-all", "--no-new-privs"):
            self.assertIn(flag, command)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["HOME"], build.BUILD_HOME)
        self.assertFalse({"WEBHOOK_SECRET", "SSH_AUTH_SOCK", "LD_PRELOAD"} & environment.keys())
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_missing_or_unsafe_identity_never_executes(self):
        with patch.object(build, "run_command") as run:
            with patch.object(build.os, "geteuid", return_value=1000):
                with self.assertRaisesRegex(RuntimeError, "root"):
                    build.run_build_command(["git"], timeout=30)
            with patch.object(build.os, "geteuid", return_value=0), patch.object(
                build.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=0, pw_gid=0, pw_dir=build.BUILD_HOME),
            ):
                with self.assertRaisesRegex(RuntimeError, "separate"):
                    build.run_build_command(["git"], timeout=30)
            run.assert_not_called()


class TestArtifactSnapshot(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.stack.enter_context(patch.object(build, "SNAPSHOT_PARENT", str(self.root)))

    def export(self, command, **kwargs):
        self.assertEqual(command[:2], ["/usr/bin/python3", "-I"])
        build._export_workspace(command[-1], kwargs["stdout"])
        return subprocess.CompletedProcess(command, 0)

    def test_snapshot_is_private_immutable_to_source_and_deploy_readable(self):
        source = self.workspace / "dist"
        source.mkdir()
        (source / "empty").mkdir()
        page = source / "index.html"
        page.write_text("original")
        with patch.object(build, "run_build_command", side_effect=self.export) as run:
            with build.artifact_snapshot(str(self.workspace)) as snapshot:
                path = Path(snapshot)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual((path / "dist").stat().st_mode & 0o777, 0o755)
                self.assertEqual((path / "dist/index.html").stat().st_mode & 0o777, 0o644)
                self.assertTrue((path / "dist/empty").is_dir())
                page.write_text("changed")
                self.assertEqual((path / "dist/index.html").read_text(), "original")
                self.assertEqual(run.call_args.kwargs["timeout"], 300)
            self.assertFalse(path.exists())

    def test_symlinks_and_fifos_cannot_export_broker_files(self):
        for kind in ("file-link", "directory-link", "fifo"):
            path = self.workspace / "unsafe"
            if kind == "fifo":
                os.mkfifo(path)
            else:
                path.symlink_to(self.root if kind == "directory-link" else self.root / "secret")
            try:
                with patch.object(build, "run_build_command", side_effect=self.export):
                    with self.assertRaises((ValueError, OSError)):
                        with build.artifact_snapshot(str(self.workspace)):
                            self.fail("Unsafe artifacts reached deployment")
            finally:
                path.unlink()
            self.assertEqual(list(self.root.iterdir()), [self.workspace])

    def test_failed_export_cleans_snapshot_and_never_yields(self):
        with patch.object(build, "run_build_command", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                with build.artifact_snapshot(str(self.workspace)):
                    self.fail("Failed export reached deployment")
        self.assertEqual(list(self.root.iterdir()), [self.workspace])

    def test_unreadable_tree_or_non_directory_cannot_become_empty_deployment(self):
        def denied(*args, **kwargs):
            kwargs["onerror"](PermissionError("unreadable"))
        with patch.object(build.os, "walk", side_effect=denied):
            with self.assertRaisesRegex(PermissionError, "unreadable"):
                build._export_workspace(str(self.workspace), io.BytesIO())
        file = self.workspace / "file"
        file.touch()
        with self.assertRaisesRegex(ValueError, "real directory"):
            build._export_workspace(str(file), io.BytesIO())

    def test_untrusted_frames_reject_traversal_truncation_duplicates_and_limits(self):
        def frame(path="ok", size=1):
            return json.dumps({"path": path, "size": size, "executable": False, "directory": False}).encode() + b"\n"
        cases = [frame("../secret") + b"x", frame("/secret") + b"x", frame(size=-1),
                 frame(size=100), frame() + b"x" + frame() + b"x", b"{}\n",
                 b"x" * (build.MAX_HEADER_BYTES + 1), frame(size=build.MAX_SNAPSHOT_BYTES + 1)]
        for data in cases:
            with self.subTest(data=data[:50]), tempfile.TemporaryDirectory(dir=self.root) as directory:
                with self.assertRaises((ValueError, OSError)):
                    build._extract_snapshot(io.BytesIO(data), Path(directory))
        self.assertFalse((self.root / "secret").exists())

    def test_executor_deploys_snapshot_and_stops_on_export_failure(self):
        repo = "https://example.com/repo.git"
        for failed in (False, True):
            job = self.root / "job.json"
            job.write_text(json.dumps({"repo_url": repo, "ref": "refs/heads/main",
                                       "commit_sha": "a" * 40, "pusher": "user"}))
            with (
                patch.object(executor, "load_config", return_value={"repositories": [{"url": repo, "deploy_target": "app"}]}),
                patch.object(executor, "LOGS_DIR", str(self.root)),
                patch.object(executor, "clone_or_update_repo", return_value=True),
                patch.object(executor, "artifact_snapshot") as snapshot,
                patch.object(executor, "perform_remote_deployment", return_value=True) as deploy,
                patch.object(executor, "load_notification_configs_from_state", return_value=[]),
                patch.object(executor, "log_event"),
            ):
                snapshot.return_value.__enter__.return_value = "private-snapshot"
                if failed:
                    snapshot.return_value.__enter__.side_effect = RuntimeError("export failed")
                self.assertEqual(executor.process_job(str(job)), not failed)
                if failed:
                    deploy.assert_not_called()
                else:
                    self.assertEqual(deploy.call_args.args[0], "private-snapshot")
