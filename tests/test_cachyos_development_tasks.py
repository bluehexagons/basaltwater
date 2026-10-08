"""Remote development contracts with temporary projects and mocked system calls."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop import development, development_worker
from lib import desktop_cli
from lib import agent_environment, agent_workspace
from lib.atomic_io import write_json_atomic


class TestDevelopmentTasks(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.home / "project with %n $HOME `ticks`"
        self.project.mkdir()
        (self.project / "project.godot").write_text('[application]\nconfig/features=PackedStringArray("4.7", "Forward Plus")\n')
        self.engine = self.home / "godot"
        self.engine.write_text("fixture; never executed")
        self.engine.chmod(0o700)
        self.uid = os.getuid()
        self.enterContext(patch.object(development, "_guard", return_value=self.uid))
        self.enterContext(patch.object(development.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.enterContext(patch.object(development, "_honor_human_pause"))
        self.which = self.enterContext(patch.object(development.shutil, "which", side_effect=lambda name: str(self.engine) if name in ("godot", str(self.engine)) else None))
        self.enterContext(patch.object(development, "is_dry_run", return_value=False))
        self.environment = self.enterContext(patch.object(development, "session_environment", return_value=({"WAYLAND_DISPLAY": "wayland-0"}, "available")))
        self.probe = self.enterContext(patch.object(development, "_probe", side_effect=self.respond))
        self.state, self.substate, self.invocation = "active", "running", "a" * 32
        self.transient, self.description = "yes", None
        self.launch_state = "ok"
        self.enterContext(patch.dict(os.environ, {"NVM_DIR": ""}))

    def respond(self, command, uid):
        self.assertEqual(uid, self.uid)
        if command[0] == "/usr/bin/systemd-run":
            return self.launch_state, ""
        if command[2] == "stop":
            self.state, self.substate, self.invocation = "inactive", "dead", ""
            return "ok", ""
        unit = command[3]
        task = unit.removeprefix(development.UNIT_PREFIX).removesuffix(".service")
        return "ok", "\n".join((
            "LoadState=loaded", f"Transient={self.transient}",
            f"Description={self.description or 'Basaltwater development task ' + task}",
            f"InvocationID={self.invocation}", f"ActiveState={self.state}",
            f"SubState={self.substate}",
        ))

    def launch(self, **kwargs):
        return development.launch(str(self.project), kwargs.pop("kind", "run"), **kwargs)

    def test_doctor_reads_only_prerequisites_without_state_or_engine_execution(self):
        result = development.doctor(str(self.project))
        self.assertTrue(result["ok"])
        self.assertTrue(result["prerequisites_only"])
        self.assertEqual(result["project"]["minimum_engine_version"], "4.7")
        self.assertEqual(result["project"]["engine_version"], "unverified")
        self.assertFalse((self.home / ".local").exists())
        self.probe.assert_not_called()

    def test_csharp_reports_dotnet_engine_and_sdk_without_first_run_initialization(self):
        (self.project / "project.godot").write_text('[application]\nconfig/features=PackedStringArray("4.7", "C#")\n')
        result = development.doctor(str(self.project))
        self.assertFalse(result["ok"])
        self.assertEqual(result["project"]["missing_tools"], ["Godot .NET engine", ".NET SDK"])
        self.probe.assert_not_called()

    def test_features_come_only_from_the_application_section(self):
        (self.project / "project.godot").write_text(
            '[rendering]\nconfig/features=PackedStringArray("3.0", "C#")\n'
            '[application]\nconfig/features=PackedStringArray("4.7", "Forward Plus")\n'
        )
        result = development.doctor(str(self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(result["project"]["minimum_engine_version"], "4.7")
        self.assertFalse(result["project"]["requires_dotnet"])

    def test_multiline_csharp_features_and_escaped_strings_are_read_without_execution(self):
        (self.project / "project.godot").write_text(
            '[application] ; application metadata\nconfig/features=PackedStringArray(\n'
            '  "4.7", "C\\u0023", "custom ) feature",\n) ; declaration\n'
        )
        result = development.doctor(str(self.project))
        self.assertEqual(result["project"]["minimum_engine_version"], "4.7")
        self.assertTrue(result["project"]["requires_dotnet"])
        self.assertIn("Godot .NET engine", result["project"]["missing_tools"])
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_ambiguous_or_malformed_features_do_not_silently_choose_an_engine(self):
        bodies = (
            '[application]\nconfig/features=PackedStringArray("C#")\nconfig/features=PackedStringArray("4.7")',
            '[application]\nconfig/features=PackedStringArray("C#")\n[application]\n',
            '[application]\nconfig/features=["C#"]',
            '[application]\nconfig/features=PackedStringArray("C#", 7)',
            '[application]\nconfig/features=PackedStringArray("C#"',
            '[application]\nconfig/features=PackedStringArray(' + '[' * 2000 + ']' * 2000 + ')',
        )
        for body in bodies:
            with self.subTest(body=body):
                (self.project / "project.godot").write_text(body)
                with self.assertRaisesRegex(ValueError, "features|application"):
                    self.launch()
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_native_and_node_projects_remain_usable_without_godot(self):
        (self.project / "project.godot").unlink()
        self.assertEqual(development.doctor(str(self.project))["project"]["kind"], "native")
        (self.project / "package.json").write_text("{}")
        self.assertEqual(development.doctor(str(self.project))["project"]["kind"], "node")
        with self.assertRaisesRegex(ValueError, "project.godot"):
            self.launch()

    def test_metadata_symlinks_fifos_and_oversized_content_are_rejected(self):
        metadata = self.project / "project.godot"
        for kind in ("symlink", "fifo", "oversized"):
            with self.subTest(kind=kind):
                metadata.unlink()
                if kind == "symlink":
                    metadata.symlink_to(self.engine)
                elif kind == "fifo":
                    os.mkfifo(metadata)
                else:
                    metadata.write_bytes(b" " * (1024 * 1024 + 1))
                with self.assertRaises((OSError, ValueError)):
                    development.doctor(str(self.project))

    def test_missing_session_fails_before_task_creation(self):
        self.environment.return_value = {}, "Log into KDE"
        result = development.doctor(str(self.project))
        self.assertFalse(result["ok"])
        self.assertEqual(result["session"]["state"], "deferred")
        with self.assertRaisesRegex(RuntimeError, "Log into KDE"):
            self.launch()
        self.assertFalse((self.home / ".local").exists())

    def test_dry_run_has_no_task_files_or_service_mutation(self):
        result = self.launch(dry_run=True, argv=["--", "$HOME", "%n", "`uname`", "two words"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["argv"][-5:], ["--", "$HOME", "%n", "`uname`", "two words"])
        self.probe.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_runtime_path_is_forwarded_once_without_importing_provider_credentials(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin:/tmp/literal%npath", "PROVIDER_TOKEN": "secret"}):
            self.launch()
        command = self.probe.call_args_list[0].args[0]
        self.assertIn("--setenv=PATH=/usr/bin:/tmp/literal%npath", command)
        self.assertFalse(any("PROVIDER_TOKEN" in argument for argument in command))

    def test_unvalidated_display_authority_is_removed_from_task_environment(self):
        self.launch()
        command = self.probe.call_args_list[0].args[0]
        unset = next(value.removeprefix("--property=UnsetEnvironment=").split() for value in command if value.startswith("--property=UnsetEnvironment="))
        self.assertIn("DISPLAY", unset)
        self.assertIn("XAUTHORITY", unset)
        self.assertNotIn("WAYLAND_DISPLAY", unset)

    def test_argument_count_controls_and_serialized_size_are_bounded(self):
        for argv in ([], ["arg"] * 101, ["arg", "bad\nvalue"], ["arg", "x" * 4097], ["x" * 4096] * 20):
            with self.subTest(argv_length=len(argv)), self.assertRaises(ValueError):
                development.validate_argv(argv)

    def test_list_is_read_only_filters_worktrees_and_does_not_expose_arguments(self):
        self.assertEqual(development.list_tasks()["tasks"], [])
        self.assertFalse((self.home / ".local").exists())
        first = self.launch()
        self.probe.reset_mock()
        tasks = development.list_tasks(str(self.project))["tasks"]
        self.assertEqual(tasks[0]["task"], first["task"])
        self.assertEqual(tasks[0]["state"], "recorded")
        self.assertNotIn("argv", tasks[0])
        self.assertEqual(development.list_tasks(str(self.home))["tasks"], [])
        self.probe.assert_not_called()

    def test_scene_cannot_escape_project_by_parent_absolute_path_or_symlink(self):
        outside = self.home / "outside.tscn"
        outside.write_text("fixture")
        (self.project / "linked.tscn").symlink_to(outside)
        for scene in ("../outside.tscn", str(outside), "linked.tscn", "missing.tscn", "project.godot", "bad\nscene"):
            with self.subTest(scene=scene), self.assertRaises(ValueError):
                self.launch(scene=scene)
        self.probe.assert_not_called()

    def test_task_records_literal_arguments_private_storage_and_invocation_identity(self):
        scene = self.project / "scene with spaces.tscn"
        scene.write_text("fixture")
        result = self.launch(scene="res://scene with spaces.tscn", argv=["--", "%n", "$HOME", "`touch nope`", "two words", ""])
        directory = Path(result["directory"])
        record = development.load_task(directory)
        self.assertEqual(record["argv"], [str(self.engine), "--path", str(self.project), str(scene), "--", "%n", "$HOME", "`touch nope`", "two words", ""])
        self.assertEqual(record["invocation_id"], "a" * 32)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / "task.json").stat().st_mode & 0o777, 0o600)
        command = self.probe.call_args_list[0].args[0]
        self.assertIn("--expand-environment=no", command)
        self.assertIn("--property=KillMode=control-group", command)
        self.assertIn("--property=ExitType=cgroup", command)
        self.assertNotIn("--remain-after-exit", command)
        self.assertNotIn(str(scene), command)  # Project argv is passed through JSON, not systemd expansion.
        self.assertEqual(result["state"], "running")

    def test_editor_and_explicit_native_command(self):
        editor = development.load_task(Path(self.launch(kind="editor")["directory"]))
        self.assertEqual(editor["argv"][-1], "--editor")
        native = development.load_task(Path(self.launch(kind="exec", argv=["--", str(self.engine), "literal argument"])["directory"]))
        self.assertEqual(native["argv"], [str(self.engine), "literal argument"])

    def test_stop_targets_only_owned_transient_invocation_and_survives_cleared_identity(self):
        launched = self.launch()
        result = development.task_status(launched["task"], stop=True)
        self.assertEqual(result["state"], "stopped")
        stop_calls = [call.args[0] for call in self.probe.call_args_list if call.args[0][2] == "stop"]
        self.assertEqual(stop_calls, [["/usr/bin/systemctl", "--user", "stop", "--no-block", launched["unit"]]])
        self.assertEqual(development.task_status(launched["task"])["state"], "stopped")

    def test_stop_acknowledges_queueing_without_claiming_a_running_task_has_stopped(self):
        launched = self.launch()
        active = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running"}
        with patch.object(development, "_unit", return_value=active):
            result = development.task_status(launched["task"], stop=True)
        self.assertEqual(result["state"], "stopping")
        self.assertIn("stop_requested_at", result)
        self.assertTrue((Path(result["directory"]) / "stop-requested.json").exists())
        self.assertEqual(development.task_status(launched["task"])["state"], "stopped")
        self.probe.side_effect = None
        self.probe.return_value = "ok", "LoadState=not-found"
        self.assertEqual(development.task_status(launched["task"])["state"], "stopped")

    def test_unacknowledged_stop_does_not_record_a_successful_request(self):
        launched = self.launch()
        with patch.object(development, "_unit", return_value={"LoadState": "loaded"}), patch.object(development, "_probe", return_value=("error", "")):
            with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
                development.task_status(launched["task"], stop=True)
        self.assertFalse((Path(launched["directory"]) / "stop-requested.json").exists())

    def test_changed_or_unmanaged_unit_is_never_stopped(self):
        launched = self.launch()
        for field, value in (("invocation", "b" * 32), ("transient", "no"), ("description", "User's service")):
            with self.subTest(field=field):
                saved = getattr(self, field)
                setattr(self, field, value)
                self.probe.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    development.task_status(launched["task"], stop=True)
                self.assertFalse(any(call.args[0][2] == "stop" for call in self.probe.call_args_list))
                setattr(self, field, saved)

    def test_explicit_and_global_stop_previews_never_mutate_the_task(self):
        launched = self.launch()
        for global_preview in (False, True):
            with self.subTest(global_preview=global_preview), patch.object(development, "is_dry_run", return_value=global_preview):
                self.probe.reset_mock()
                result = development.task_status(launched["task"], stop=True, dry_run=not global_preview)
                self.assertTrue(result["dry_run"])
                self.assertFalse(any(call.args[0][2] == "stop" for call in self.probe.call_args_list))
                self.assertFalse((Path(launched["directory"]) / "stopped.json").exists())
                self.assertFalse((Path(launched["directory"]) / "stop-requested.json").exists())

    def test_completion_survives_disappearance_of_service_and_project(self):
        launched = self.launch()
        directory = Path(launched["directory"])
        write_json_atomic(str(directory / "result.json"), {"returncode": 0, "log_truncated": True})
        self.probe.side_effect = None
        self.probe.return_value = "ok", "LoadState=not-found"
        (self.project / "project.godot").unlink()
        self.project.rmdir()
        result = development.task_status(launched["task"])
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["log_truncated"])

    def test_live_descendants_take_precedence_over_wrapper_completion(self):
        launched = self.launch()
        write_json_atomic(str(Path(launched["directory"]) / "result.json"), {"returncode": 0, "log_truncated": False})
        self.assertEqual(development.task_status(launched["task"])["state"], "running")
        self.substate = "exited"
        self.assertEqual(development.task_status(launched["task"])["state"], "completed")

    def test_evidence_collection_failure_does_not_claim_completed_playtest(self):
        launched = self.launch()
        self.substate = "exited"
        write_json_atomic(str(Path(launched["directory"]) / "result.json"), {
            "returncode": 0, "log_truncated": False, "error": "Output collection failed",
        })
        result = development.task_status(launched["task"])
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["ok"])

    def test_failed_and_missing_tasks_do_not_claim_success(self):
        launched = self.launch()
        self.state, self.substate = "failed", "failed"
        self.assertFalse(development.task_status(launched["task"])["ok"])
        self.probe.side_effect = None
        self.probe.return_value = "ok", "LoadState=not-found"
        self.assertEqual(development.task_status(launched["task"])["state"], "unavailable")

    def test_unavailable_service_keeps_recorded_evidence_without_claiming_live_completion(self):
        launched = self.launch()
        directory = Path(launched["directory"])
        write_json_atomic(str(directory / "result.json"), {"returncode": 0, "log_truncated": False})
        self.probe.side_effect = None
        self.probe.return_value = "error", ""
        result = development.task_status(launched["task"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["recorded_state"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["log"], str(directory / "output.log"))
        with self.assertRaises(RuntimeError):
            development.task_status(launched["task"], stop=True)
        self.assertFalse((directory / "stop-requested.json").exists())
        self.assertTrue(all(call.args[0][2] != "stop" for call in self.probe.call_args_list))

    def test_unacknowledged_launch_retains_identifier_for_recovery(self):
        self.launch_state = "error"
        result = self.launch()
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "launch-unverified")
        self.assertTrue((Path(result["directory"]) / "task.json").exists())

    def test_post_queue_inspection_failure_retains_identifier_without_relaunching(self):
        for inspections in (0, 1):
            with self.subTest(inspections=inspections), patch.object(development, "_unit", side_effect=[
                *[{"InvocationID": self.invocation}] * inspections, RuntimeError("Inspection unavailable"),
            ]):
                self.probe.reset_mock()
                result = self.launch()
            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "unverified" if inspections else "launch-unverified")
            self.assertIn("Inspection unavailable", result["service_error"] if inspections else result["error"])
            self.assertEqual(sum(call.args[0][0] == "/usr/bin/systemd-run" for call in self.probe.call_args_list), 1)
            self.assertEqual(development.task_status(result["task"])["state"], "running")

    def test_task_collision_never_replaces_original_record(self):
        with patch.object(development.secrets, "token_hex", return_value="c" * 32):
            first = self.launch(kind="editor")
            with self.assertRaises(FileExistsError):
                self.launch(kind="run")
        self.assertEqual(development.load_task(Path(first["directory"]))["kind"], "editor")

    def test_unsafe_storage_task_ids_and_records_are_rejected(self):
        for task in ("../other", "x" * 32, "a" * 31, "a" * 33, "a" * 32 + "\n"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                development.task_status(task)
        launched = self.launch()
        record = Path(launched["directory"]) / "task.json"
        record.chmod(0o644)
        with self.assertRaises(ValueError):
            development.task_status(launched["task"])
        record.chmod(0o600)
        data = json.loads(record.read_text())
        data["unit"] = "sunshine.service"
        write_json_atomic(str(record), data)
        with self.assertRaisesRegex(ValueError, "identity"):
            development.task_status(launched["task"], stop=True)

    def test_symlink_storage_ancestor_is_rejected_before_mutation(self):
        (self.home / ".local").symlink_to(self.project)
        with self.assertRaisesRegex(ValueError, "storage ancestor"):
            self.launch()
        self.probe.assert_not_called()

    def test_cli_routes_without_starting_portal_and_requires_native(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers(dest="command"))
        for native, expected in ((True, 0), (False, 1)):
            args = parser.parse_args(["desktop", *(["--native"] if native else []), "develop", "doctor", "--project", str(self.project), "--json"])
            with redirect_stdout(output := StringIO()), patch("desktop.native_session.start") as portal:
                self.assertEqual(desktop_cli.run_desktop_command(args), expected)
            self.assertEqual(json.loads(output.getvalue())["ok"], native)
            portal.assert_not_called()


class TestDevelopmentSession(unittest.TestCase):
    def test_ssh_environment_uses_only_owned_manager_sockets_and_drops_other_variables(self):
        with patch.object(development, "_owned_socket", return_value=True), patch.object(development, "_probe", side_effect=[
            ("ok", "ActiveState=active"),
            ("ok", "WAYLAND_DISPLAY=wayland-0\nDISPLAY=:1\nTOKEN=secret\nDBUS_SESSION_BUS_ADDRESS=tcp:host=remote\n"),
        ]) as probe, patch.dict(os.environ, {"SSH_CONNECTION": "remote", "WAYLAND_DISPLAY": "evil", "DBUS_SESSION_BUS_ADDRESS": "evil"}):
            environment, _ = development.session_environment(1234)
        self.assertEqual(environment["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1234/bus")
        self.assertNotIn("TOKEN", environment)
        self.assertEqual(environment["DISPLAY"], ":1")
        self.assertTrue(all(call.args[1] == 1234 for call in probe.call_args_list))

    def test_stale_or_unsafe_xauthority_does_not_break_an_owned_wayland_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = root / "authority"
            authority.write_text("synthetic authority")
            authority.chmod(0o600)
            linked = root / "linked"
            linked.symlink_to(authority)
            public = root / "public"
            public.write_text("synthetic authority")
            public.chmod(0o666)
            for selected in (authority, linked, public, root / "missing"):
                with self.subTest(selected=selected), patch.object(development, "_owned_socket", return_value=True), patch.object(development, "_probe", side_effect=[
                    ("ok", "ActiveState=active"), ("ok", f"WAYLAND_DISPLAY=wayland-0\nDISPLAY=:1\nXAUTHORITY={selected}\n"),
                ]):
                    environment, _ = development.session_environment(os.getuid())
                self.assertEqual(environment["WAYLAND_DISPLAY"], "wayland-0")
                self.assertEqual(environment["DISPLAY"], ":1")
                if selected == authority:
                    self.assertEqual(environment["XAUTHORITY"], str(authority))
                else:
                    self.assertNotIn("XAUTHORITY", environment)

    def test_missing_bus_or_graphical_session_never_starts_services(self):
        with patch.object(development, "_owned_socket", return_value=False), patch.object(development, "_probe") as probe:
            self.assertFalse(development.session_environment(1234)[0])
            probe.assert_not_called()
        with patch.object(development, "_owned_socket", return_value=True), patch.object(development, "_probe", return_value=("ok", "ActiveState=inactive")) as probe:
            self.assertFalse(development.session_environment(1234)[0])
            self.assertEqual(probe.call_count, 1)

    def test_unsafe_stale_or_ambiguous_display_is_deferred(self):
        for text in ("WAYLAND_DISPLAY=/tmp/other", "WAYLAND_DISPLAY=../other", "WAYLAND_DISPLAY=wayland-0\nWAYLAND_DISPLAY=wayland-1", "WAYLAND_DISPLAY='two words'", "TOKEN=secret"):
            with self.subTest(text=text), patch.object(development, "_owned_socket", return_value=True), patch.object(development, "_probe", side_effect=[("ok", "ActiveState=active"), ("ok", text)]):
                self.assertFalse(development.session_environment(1234)[0])

    def test_root_and_other_distributions_are_rejected(self):
        with patch.object(development.os, "getuid", return_value=0), self.assertRaises(RuntimeError):
            development._guard()
        with patch.object(development.os, "getuid", return_value=1234), patch.object(development.os, "geteuid", return_value=1234), patch.object(development, "is_cachyos", return_value=False), self.assertRaises(RuntimeError):
            development._guard()

    def test_persisted_and_live_human_pause_are_honored(self):
        with patch("desktop.native_grants.metadata", return_value={"paused": True}), patch("desktop.native_session.request") as request, self.assertRaisesRegex(RuntimeError, "Human paused"):
            development._honor_human_pause()
        request.assert_not_called()
        with patch("desktop.native_grants.metadata", return_value={"paused": False}), patch("desktop.native_session.request", return_value={"paused": True}), self.assertRaisesRegex(RuntimeError, "Human paused"):
            development._honor_human_pause()


class TestDevelopmentWorker(unittest.TestCase):
    def test_small_output_is_visible_while_application_is_still_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            read_fd, write_fd = os.pipe()
            process = Mock(stdout=os.fdopen(read_fd, "rb"), pid=123)
            process.wait.return_value = 0
            process.poll.return_value = None
            with patch.object(development_worker, "load_task", return_value={"argv": ["fixture"], "project": temporary}), patch.object(development_worker.subprocess, "Popen", return_value=process), patch.object(development_worker.signal, "signal"), patch.object(development_worker, "_honor_human_pause"):
                worker = threading.Thread(target=development_worker.run_task, args=(directory,))
                worker.start()
                try:
                    os.write(write_fd, b"small log")
                    until = time.monotonic() + 2
                    log = directory / "output.log"
                    while time.monotonic() < until and (not log.exists() or log.stat().st_size != 9):
                        time.sleep(.01)
                    self.assertTrue(worker.is_alive())
                    self.assertEqual(log.read_bytes(), b"small log")
                finally:
                    os.close(write_fd)
                    process.poll.return_value = 0
                    worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_shutdown_remains_responsive_after_application_closes_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            finished, eof, terminated = threading.Event(), threading.Event(), threading.Event()
            handlers = {}
            process = Mock(stdout=os.fdopen(read_fd, "rb"), pid=123)
            process.poll.side_effect = lambda: -15 if finished.is_set() else None

            def wait(timeout=None):
                if not finished.wait(2 if timeout is None else timeout):
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return -15

            def terminate():
                terminated.set()
                finished.set()

            read = os.read

            def read_output(descriptor, count):
                body = read(descriptor, count)
                if not body:
                    eof.set()
                return body

            process.wait.side_effect = wait
            process.terminate.side_effect = terminate
            process.kill.side_effect = finished.set
            with patch.object(development_worker, "load_task", return_value={"argv": ["fixture"], "project": temporary}), patch.object(development_worker.subprocess, "Popen", return_value=process), patch.object(development_worker.signal, "signal", side_effect=lambda number, handler: handlers.update({number: handler})), patch.object(development_worker.os, "read", side_effect=read_output), patch.object(development_worker, "_honor_human_pause"):
                worker = threading.Thread(target=development_worker.run_task, args=(directory,))
                worker.start()
                try:
                    self.assertTrue(eof.wait(2))
                    handlers[development_worker.signal.SIGTERM]()
                    self.assertTrue(terminated.wait(1), "Supervisor ignored shutdown after stdout closed")
                finally:
                    finished.set()
                    worker.join(2)
                self.assertFalse(worker.is_alive())
            process.terminate.assert_called_once()
            process.kill.assert_not_called()
            self.assertEqual(json.loads((directory / "result.json").read_text())["returncode"], -15)

    def test_log_quota_drains_output_and_preserves_child_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            read_fd, write_fd = os.pipe()
            os.write(write_fd, b"0123456789abcdef")
            os.close(write_fd)
            stream = os.fdopen(read_fd, "rb")
            process = Mock(stdout=stream, pid=123)
            process.wait.return_value = 7
            process.poll.return_value = 7
            with patch.object(development_worker, "load_task", return_value={"argv": ["fixture", "literal $HOME"], "project": temporary}), patch.object(development_worker.subprocess, "Popen", return_value=process) as popen, patch.object(development_worker.signal, "signal"), patch.object(development_worker, "_honor_human_pause"), patch.object(development_worker, "LOG_LIMIT", 8):
                self.assertEqual(development_worker.run_task(directory), 0)
            self.assertEqual((directory / "output.log").read_bytes(), b"01234567")
            result = json.loads((directory / "result.json").read_text())
            self.assertEqual(result["returncode"], 7)
            self.assertTrue(result["log_truncated"])
            self.assertEqual((directory / "output.log").stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "result.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(popen.call_args.args[0], ["fixture", "literal $HOME"])
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(json.loads((directory / "started.json").read_text())["launch_pid"], 123)

    def test_spawn_failure_retains_completion_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(development_worker, "load_task", return_value={"argv": ["missing"], "project": temporary}), patch.object(development_worker.subprocess, "Popen", side_effect=OSError("missing")), patch.object(development_worker.signal, "signal"), patch.object(development_worker, "_honor_human_pause"):
                self.assertEqual(development_worker.run_task(directory), 0)
            self.assertEqual(json.loads((directory / "result.json").read_text())["returncode"], 1)

    def test_pause_after_service_queueing_prevents_child_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(development_worker, "load_task", return_value={"argv": ["fixture"], "project": temporary}), patch.object(development_worker.subprocess, "Popen") as popen, patch.object(development_worker.signal, "signal"), patch.object(development_worker, "_honor_human_pause", side_effect=RuntimeError("Human paused")):
                self.assertEqual(development_worker.run_task(directory), 0)
            popen.assert_not_called()
            self.assertEqual(json.loads((directory / "result.json").read_text())["returncode"], 1)


class TestDevelopmentManifest(unittest.TestCase):
    def test_native_task_commands_are_discovered_without_project_execution(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(agent_workspace, "_repository_root", return_value=temporary), patch.object(agent_workspace, "_effective_home", return_value=temporary), patch.object(agent_workspace, "_worktree_record", return_value={"branch": "main", "head": "a" * 40, "dirty": False}), patch.object(agent_environment, "is_cachyos", return_value=True), patch.object(agent_environment.shutil, "which", return_value=None), patch.object(agent_environment.subprocess, "run") as execute:
            result = agent_environment.inspect_environment(temporary)
        commands = result["development"]
        self.assertEqual(commands["role"], "remote-godot-development")
        self.assertEqual(commands["readiness"], "unverified")
        self.assertEqual(commands["doctor_argv"], ["basaltw", "desktop", "--native", "develop", "doctor", "--project", temporary, "--json"])
        self.assertEqual(commands["godot_import_argv"][4], "import")
        self.assertEqual(commands["node_script_prefix_argv"][4], "node")
        self.assertEqual(commands["recipe_check_prefix_argv"][4], "check")
        self.assertIn("electron", commands["secondary_workflows"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
