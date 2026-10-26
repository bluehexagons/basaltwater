from __future__ import annotations

import io
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import build_execution as execution
from lib.build_protocol import encode_document, request_digest
from tests.test_build_admission import build_request


def observation(*, load="loaded", active="active", sub="running", code="0", status="0", result="success"):
    return {"LoadState": load, "ActiveState": active, "SubState": sub,
            "ExecMainCode": code, "ExecMainStatus": status, "Result": result}


class FakePipe:
    def fileno(self):
        return 42

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeSelector:
    def __init__(self, readable):
        self.readable = iter(readable)
        self.registered = None
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def register(self, fileobj, events):
        self.registered = (fileobj, events)

    def select(self, *, timeout=None):
        self.timeouts.append(timeout)
        return [self.registered] if next(self.readable) else []


class BuildExecutionTests(unittest.TestCase):
    def arguments(self):
        return {"uid": 1234, "gid": 1234, "scratch_path": "/var/lib/build-scratch/owned/fs",
                "log_path": "/var/lib/build-private/log",
                "cancelled": lambda: False,
                "pause": Mock(), "clock": lambda: 1000.0}

    def test_fixed_execution_boundary_and_resource_limits(self):
        job = build_request()
        job["components"][0]["commands"] = ["echo $PRIVATE_BUILD_INPUT"]
        job["components"][0]["env"] = {"PRIVATE_BUILD_INPUT": "kept-in-private-input"}
        args = self.arguments()
        args.pop("cancelled")
        args.pop("pause")
        args.pop("clock")
        argv = execution.execution_argv(job, **args)
        self.assertNotIn("--uid=root", argv)
        self.assertNotIn("--property=User=root", argv)
        for required in ("--expand-environment=no", "--property=ExitType=cgroup",
                         "--property=RemainAfterExit=yes", "--property=KillMode=control-group",
                         "--property=NoNewPrivileges=yes", "--property=AmbientCapabilities=",
                         "--property=CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP",
                         "--property=MemoryMax=2147483648", "--property=CPUQuota=100%",
                         "--property=MemorySwapMax=0", "--property=ProtectSystem=strict",
                         "--property=TemporaryFileSystem=/tmp:ro /var/tmp:ro",
                         "--property=BindPaths=/var/lib/build-scratch/owned/fs:/run/basaltwater-build-work",
                         "--property=ReadWritePaths=/run/basaltwater-build-work/source/checkout /run/basaltwater-build-work/home",
                         "--property=TasksMax=256", "--clear-groups", "--bounding-set=-all",
                         "--reuid=1234", "--regid=1234", "/usr/bin/env", "-i"):
            self.assertIn(required, argv)
        self.assertEqual(argv[-6:], ["--execute", execution.INPUT_PATH, request_digest(job),
                                    execution.WORKSPACE, execution.BUILD_HOME, execution.HISTORY_PATH])
        self.assertNotIn("kept-in-private-input", " ".join(argv))
        self.assertNotIn("PRIVATE_BUILD_INPUT", " ".join(argv))
        for changed in ({"uid": 0}, {"gid": True}, {"scratch_path": "/var/lib/../etc"},
                        {"log_path": "/var/log/%n"}, {"scratch_path": "/var/lib/with space"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                execution.execution_argv(job, **{**args, **changed})

    def test_success_waits_for_descendants_before_returning(self):
        fields = [observation(load="not-found", active="inactive", sub="dead"),
                  observation(sub="exited", code="1"), observation(sub="exited", code="1")]
        with patch.object(execution, "_observation", side_effect=fields), \
                patch.object(execution, "_empty_cgroup", side_effect=[True, False, True]), \
                patch.object(execution, "_stop") as stop, \
                patch.object(execution, "run_command", return_value=Mock(returncode=0)) as command:
            args = self.arguments()
            result = execution.run_systemd_build(build_request(), **args)
        self.assertEqual(result, execution.BuildExecutionResult("succeeded", 0, "completed"))
        args["pause"].assert_called_once_with(0.2)
        stop.assert_called_once_with(execution.unit_name("a" * 32))
        command.assert_called_once()

    def test_cancel_and_timeout_wait_until_entire_cgroup_is_empty(self):
        for reason in ("cancelled", "timeout"):
            with self.subTest(reason=reason):
                fields = [observation(load="not-found", active="inactive", sub="dead"),
                          observation(), observation(active="failed", sub="failed"),
                          observation(active="failed", sub="failed")]
                args = self.arguments()
                if reason == "cancelled":
                    args["cancelled"] = Mock(side_effect=[False, True])
                else:
                    args["clock"] = Mock(side_effect=[0.0, 3601.0])
                with patch.object(execution, "_observation", side_effect=fields), \
                        patch.object(execution, "_empty_cgroup", side_effect=[True, False, True]), \
                        patch.object(execution, "_stop") as stop, \
                        patch.object(execution, "run_command", return_value=Mock(returncode=0)):
                    result = execution.run_systemd_build(build_request(), **args)
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.state, "cancelled" if reason == "cancelled" else "failed")
                self.assertIsNone(result.exit_code)
                self.assertEqual(args["pause"].call_count, 2)
                stop.assert_called_once()

    def test_failed_helper_stops_live_descendants_before_releasing_capacity(self):
        for code, status in (("1", "1"), ("2", "9")):
            with self.subTest(code=code, status=status):
                fields = [observation(load="not-found", active="inactive", sub="dead"),
                          observation(code=code, status=status),
                          observation(active="inactive", sub="dead", code=code, status=status),
                          observation(active="inactive", sub="dead", code=code, status=status)]
                args = self.arguments()
                with patch.object(execution, "_observation", side_effect=fields), \
                        patch.object(execution, "_empty_cgroup", side_effect=[True, False, True]), \
                        patch.object(execution, "_stop") as stop, \
                        patch.object(execution, "run_command", return_value=Mock(returncode=0)):
                    args["pause"].side_effect = lambda _: self.assertEqual(stop.call_count, 1)
                    result = execution.run_systemd_build(build_request(), **args)
                self.assertEqual(result, execution.BuildExecutionResult("failed", int(status), "command-failed"))
                self.assertEqual(args["pause"].call_count, 2)
                stop.assert_called_once_with(execution.unit_name("a" * 32))

    def test_unconfirmed_launch_probe_or_stop_never_returns_success(self):
        for failure in ("existing", "launch", "status", "stop"):
            with self.subTest(failure=failure):
                fields = [observation(load="not-found", active="inactive", sub="dead"),
                          observation(sub="exited", code="1")]
                if failure == "existing":
                    fields[0] = observation()
                if failure == "status":
                    fields[1] = OSError("status EIO")
                with patch.object(execution, "_observation", side_effect=fields), \
                        patch.object(execution, "_empty_cgroup", return_value=True), \
                        patch.object(execution, "_stop", side_effect=RuntimeError("stop") if failure == "stop" else None), \
                        patch.object(execution, "run_command", return_value=Mock(returncode=1 if failure == "launch" else 0)) as command:
                    with self.assertRaises((RuntimeError, OSError)):
                        execution.run_systemd_build(build_request(), **self.arguments())
                if failure == "existing":
                    command.assert_not_called()

    def test_prelaunch_cancel_does_not_dispatch(self):
        args = {**self.arguments(), "cancelled": lambda: True}
        with patch.object(execution, "_observation", return_value=observation(load="not-found")), \
                patch.object(execution, "_empty_cgroup", return_value=True), \
                patch.object(execution, "run_command") as command:
            result = execution.run_systemd_build(build_request(), **args)
        self.assertEqual(result.state, "cancelled")
        command.assert_not_called()

    def test_status_rejects_missing_duplicate_or_failed_properties(self):
        valid = observation(sub="exited", code="1")
        text = "\n".join(key + "=" + value for key, value in valid.items()) + "\n"
        for output, status in ((text, 0), (text.replace("LoadState=loaded\n", ""), 0),
                               (text + "Result=success\n", 0), (text, 1)):
            with self.subTest(output=output, status=status), \
                    patch.object(execution, "run_command", return_value=subprocess.CompletedProcess([], status, output)):
                if output == text and status == 0:
                    self.assertEqual(execution._observation(execution.unit_name("a" * 32)), valid)
                else:
                    with self.assertRaises(RuntimeError):
                        execution._observation(execution.unit_name("a" * 32))

    def test_unprivileged_helper_preserves_successful_output_until_pipe_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.json"
            job = build_request()
            job["components"][0]["commands"] = ["build"]
            path.write_bytes(encode_document(job))
            output = io.BytesIO()
            readiness = FakeSelector([True, True])
            process = Mock(stdout=FakePipe(), poll=Mock(return_value=0),
                           wait=Mock(return_value=0))
            with patch.object(execution.os, "getuid", return_value=1234), \
                    patch.object(execution.os, "getgid", return_value=1234), \
                    patch.object(execution.os, "getgroups", return_value=[]), \
                    patch.object(execution.sys, "stdout", Mock(buffer=output)), \
                    patch.object(execution, "prepare_history") as history, \
                    patch.object(execution.subprocess, "Popen", return_value=process) as spawn, \
                    patch.object(execution.selectors, "DefaultSelector", return_value=readiness), \
                    patch.object(execution.os, "read", side_effect=[b"build output", b""]):
                result = execution.execute_request(str(path), request_digest(job), temporary, temporary,
                                                   "/work/history.bundle")

        self.assertEqual(result, 0)
        history.assert_called_once_with("/work/history.bundle", job["source"]["revision"],
                                        temporary, temporary)
        self.assertEqual(output.getvalue(), b"build output")
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], ["/bin/bash", "--noprofile", "--norc", "-c", "build"])
        process.wait.assert_called_once_with()
        self.assertEqual(readiness.timeouts, [0.2, 0.2])

    def test_failed_command_is_detected_while_descendant_holds_stdout_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.json"
            job = build_request()
            job["components"][0]["commands"] = ["failed", "must-not-run"]
            path.write_bytes(encode_document(job))
            output = io.BytesIO()
            readiness = FakeSelector([False, False])
            process = Mock(stdout=FakePipe(), poll=Mock(side_effect=[None, 1]),
                           wait=Mock(side_effect=AssertionError("poll already observed command exit")))
            with patch.object(execution.os, "getuid", return_value=1234), \
                    patch.object(execution.os, "getgid", return_value=1234), \
                    patch.object(execution.os, "getgroups", return_value=[]), \
                    patch.object(execution.sys, "stdout", Mock(buffer=output)), \
                    patch.object(execution, "prepare_history"), \
                    patch.object(execution.subprocess, "Popen", return_value=process) as spawn, \
                    patch.object(execution.selectors, "DefaultSelector", return_value=readiness), \
                    patch.object(execution.os, "read") as read:
                result = execution.execute_request(str(path), request_digest(job), temporary, temporary,
                                                   "/work/history.bundle")

        self.assertEqual(result, 1)
        process.wait.assert_not_called()
        self.assertEqual(readiness.timeouts, [0.2, 0.2])
        read.assert_not_called()
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0][-1], "failed")
        self.assertEqual(output.getvalue(), b"")

    def test_unprivileged_helper_bounds_combined_logs_across_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.json"
            job = build_request()
            job["limits"]["log_bytes"] = 3
            job["components"][0]["commands"] = ["first"]
            job["components"][0]["env"] = {"TMPDIR": "/dev/shm", "HOME": "/other-home"}
            second = {**job["components"][0], "name": "worker", "artifacts": ["worker-dist"],
                      "commands": ["second", "must-not-run"], "env": {}}
            job["components"].append(second)
            path.write_bytes(encode_document(job))
            output = io.BytesIO()
            first = Mock(stdout=FakePipe(), poll=Mock(side_effect=[None, 0]),
                          wait=Mock(return_value=0))
            second_process = Mock(stdout=FakePipe(), poll=Mock(return_value=None),
                                  wait=Mock(side_effect=AssertionError("log overflow must fail immediately")))
            readiness = [FakeSelector([True, True]), FakeSelector([True])]
            with patch.object(execution.os, "getuid", return_value=1234), \
                    patch.object(execution.os, "getgid", return_value=1234), \
                    patch.object(execution.os, "getgroups", return_value=[]), \
                    patch.object(execution.sys, "stdout", Mock(buffer=output)), \
                    patch.object(execution, "prepare_history") as history, \
                    patch.object(execution.subprocess, "Popen", side_effect=[first, second_process]) as spawn, \
                    patch.object(execution.selectors, "DefaultSelector", side_effect=readiness), \
                    patch.object(execution.os, "read", side_effect=[b"ab", b"", b"cd"]):
                result = execution.execute_request(str(path), request_digest(job), temporary, temporary,
                                                   "/work/history.bundle")
                with self.assertRaisesRegex(ValueError, "changed"):
                    execution.execute_request(str(path), "f" * 64, temporary, temporary,
                                              "/work/history.bundle")

        self.assertEqual(result, 1)
        history.assert_called_once_with("/work/history.bundle", job["source"]["revision"],
                                        temporary, temporary)
        self.assertEqual(output.getvalue(), b"abc")
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(spawn.call_args_list[0].args[0][-1], "first")
        self.assertEqual(spawn.call_args_list[1].args[0][-1], "second")
        first.wait.assert_called_once_with()
        second_process.wait.assert_not_called()
        environment = spawn.call_args_list[0].kwargs["env"]
        self.assertEqual(environment["HOME"], temporary)
        self.assertEqual(environment["TMPDIR"], str(Path(temporary) / "tmp"))
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_cgroup_lookup_errors_are_not_treated_as_empty(self):
        unit = execution.unit_name("a" * 32)
        for failure in (FileNotFoundError(), OSError("cgroup EIO")):
            with self.subTest(failure=failure), \
                    patch("builtins.open", return_value=io.BytesIO(b"cpu memory pids\n")), \
                    patch.object(execution, "directory_fd", side_effect=failure):
                if isinstance(failure, FileNotFoundError):
                    self.assertTrue(execution._empty_cgroup(unit))
                else:
                    with self.assertRaisesRegex(OSError, "EIO"):
                        execution._empty_cgroup(unit)
        for raw, expected in ((b"populated 0\nfrozen 0\n", True),
                              (b"populated 1\nfrozen 0\n", False), (b"unavailable\n", None)):
            with self.subTest(raw=raw), \
                    patch("builtins.open", return_value=io.BytesIO(b"cpu memory pids\n")), \
                    patch.object(execution, "directory_fd", return_value=nullcontext(11)), \
                    patch.object(execution.os, "open", return_value=12), \
                    patch.object(execution.os, "fdopen", return_value=io.BytesIO(raw)):
                if expected is None:
                    with self.assertRaises(RuntimeError):
                        execution._empty_cgroup(unit)
                else:
                    self.assertEqual(execution._empty_cgroup(unit), expected)


if __name__ == "__main__":
    unittest.main()
