"""Fixed builder entrypoint, protected configuration and launch-time policy."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import build_host
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS


def config() -> dict:
    return {
        "schema": 1, "uid": 999, "gid": 998,
        "roots": {name: "/var/lib/builder/" + name for name in build_host._ROOTS},
        "policy": {"schema": 1, "principals": {"controller": {"projects": {
            "site": {"platform": DEBIAN_PLATFORM, "runtimes": ["go"],
                     "limits": DEFAULT_LIMITS},
        }}}},
        "max_records": 128, "max_bytes": 64 * 1024**3,
    }


def request() -> dict:
    return {"schema": 1, "request_id": "a" * 32, "project": "site",
            "source": {"revision": "b" * 40, "snapshot_sha256": "c" * 64,
                       "manifest_sha256": "d" * 64},
            "platform": DEBIAN_PLATFORM, "limits": DEFAULT_LIMITS,
            "components": [{"name": "site", "type": "static", "artifacts": ["dist"],
                            "commands": [], "env": {}}]}


class BuildHostEntrypointTest(unittest.TestCase):
    def test_config_is_detached_and_rejects_invalid_privileged_settings(self):
        original = config()
        normalized = build_host.parse_config(original)
        normalized["policy"]["principals"]["controller"]["projects"]["site"]["runtimes"].clear()
        self.assertEqual(original["policy"]["principals"]["controller"]["projects"]["site"]["runtimes"], ["go"])
        cases = []
        for key, value in (("schema", True), ("uid", 0), ("gid", True),
                           ("max_records", 4097), ("max_bytes", 1), ("extra", 1)):
            item = config()
            item[key] = value
            cases.append(item)
        for path in ("relative", "/", "/var/../tmp/state", "/var/bad path", "/var/%n"):
            item = config()
            item["roots"]["state"] = path
            cases.append(item)
        for name, path in (("logs", "/var/lib/builder/state"),
                           ("logs", "/var/lib/builder/state/nested")):
            item = config()
            item["roots"][name] = path
            cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    build_host.parse_config(item)

    def test_config_file_is_bounded_private_regular_and_nofollow(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path = parent / "config.json"
            path.write_text(json.dumps(config()))
            path.chmod(0o600)
            real_fstat = os.fstat

            @contextmanager
            def private_root(_path):
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    yield str(parent), descriptor
                finally:
                    os.close(descriptor)

            def trusted_owner(descriptor):
                observed = real_fstat(descriptor)
                return SimpleNamespace(**{
                    name: (0 if name == "st_uid" else getattr(observed, name))
                    for name in ("st_mode", "st_uid", "st_nlink", "st_size", "st_dev", "st_ino",
                                 "st_mtime_ns", "st_ctime_ns")
                })

            with patch("lib.build_worker._private_root", side_effect=private_root), \
                 patch.object(build_host, "require_build_account"), \
                 patch.object(build_host.os, "fstat", side_effect=trusted_owner):
                self.assertEqual(build_host.load_config(str(path))["uid"], 999)
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    build_host.load_config(str(path))
                path.chmod(0o600)
                os.link(path, parent / "alias")
                with self.assertRaises(ValueError):
                    build_host.load_config(str(path))
                (parent / "alias").unlink()
                (parent / "link").symlink_to(path)
                with self.assertRaises(OSError):
                    build_host.load_config(str(parent / "link"))

    def test_execution_ids_must_match_the_dedicated_nonlogin_identity(self):
        values = {"pw_name": build_host.BUILD_ACCOUNT, "pw_uid": 999, "pw_gid": 998,
                  "pw_dir": "/nonexistent", "pw_shell": "/usr/sbin/nologin"}
        group_values = {"gr_name": build_host.BUILD_ACCOUNT, "gr_gid": 998, "gr_mem": []}
        with patch.object(build_host.pwd, "getpwnam", return_value=SimpleNamespace(**values)) as account, \
             patch.object(build_host.grp, "getgrnam", return_value=SimpleNamespace(**group_values)) as group, \
             patch.object(build_host.pwd, "getpwall", return_value=[SimpleNamespace(**values)]) as accounts, \
             patch.object(build_host.grp, "getgrall", return_value=[SimpleNamespace(**group_values)]) as groups:
            build_host.require_build_account(config())
            account.assert_called_once_with(build_host.BUILD_ACCOUNT)
            group.assert_called_once_with(build_host.BUILD_ACCOUNT)
            for key, value in (("pw_uid", 1000), ("pw_gid", 1000),
                               ("pw_dir", "/home/operator"), ("pw_shell", "/bin/bash")):
                account.return_value = SimpleNamespace(**{**values, key: value})
                with self.subTest(field=key), self.assertRaises(ValueError):
                    build_host.require_build_account(config())
            account.return_value = SimpleNamespace(**values)
            group.return_value = SimpleNamespace(**{**group_values, "gr_mem": ["operator"]})
            with self.assertRaises(ValueError):
                build_host.require_build_account(config())
            group.return_value = SimpleNamespace(**group_values)
            for uid, gid in ((999, 1000), (1000, 998)):
                accounts.return_value = [SimpleNamespace(**values),
                                         SimpleNamespace(pw_name="operator", pw_uid=uid, pw_gid=gid)]
                with self.subTest(uid=uid, gid=gid), self.assertRaises(ValueError):
                    build_host.require_build_account(config())
            accounts.return_value = [SimpleNamespace(**values)]
            groups.return_value = [SimpleNamespace(**group_values),
                                   SimpleNamespace(gr_name="operators", gr_gid=998)]
            with self.assertRaises(ValueError):
                build_host.require_build_account(config())

    def test_policy_is_reloaded_and_only_allowed_tool_versions_reach_worker(self):
        original = config()
        current = copy.deepcopy(original)
        callback = build_host.authorizer(original, config_path="/private/config")
        with patch.object(build_host, "load_config", return_value=current) as loader, \
             patch.object(build_host, "observe_tools", return_value={"go": "go1.25", "node": "v24"}):
            self.assertEqual(callback("controller", request()), {"go": "go1.25"})
            loader.assert_called_with("/private/config")
            current["policy"]["principals"]["controller"]["projects"].clear()
            with self.assertRaises(ValueError):
                callback("controller", request())
            current = copy.deepcopy(original)
            current["uid"] = 1000
            loader.return_value = current
            with self.assertRaises(PermissionError):
                callback("controller", request())

    def test_platform_requires_exact_initial_baseline(self):
        with patch.object(build_host.platform, "system", return_value="Linux"), \
             patch.object(build_host.platform, "machine", return_value="x86_64"), \
             patch.object(build_host.platform, "freedesktop_os_release",
                          return_value={"ID": "debian", "VERSION_ID": "13"}) as release:
            build_host.require_platform()
            for value in ({"ID": "ubuntu", "VERSION_ID": "13"},
                          {"ID": "debian", "VERSION_ID": "12"}):
                release.return_value = value
                with self.assertRaises(ValueError):
                    build_host.require_platform()

    def test_connection_deadline_restores_handler_even_after_interruption(self):
        with patch.object(build_host.signal, "signal", return_value="old") as handler, \
             patch.object(build_host.signal, "alarm") as alarm:
            with self.assertRaises(KeyboardInterrupt):
                with build_host.connection_deadline(10):
                    raise KeyboardInterrupt
            self.assertEqual(alarm.call_args_list[0].args, (10,))
            self.assertEqual(alarm.call_args_list[-1].args, (0,))
            self.assertEqual(handler.call_args_list[-1].args, (build_host.signal.SIGALRM, "old"))

    def test_connection_timeout_interrupts_io_and_restores_alarm(self):
        with patch.object(build_host.signal, "signal", return_value="old") as handler, \
             patch.object(build_host.signal, "alarm") as alarm:
            with self.assertRaises(TimeoutError):
                with build_host.connection_deadline(10):
                    handler.call_args.args[1](None, None)
            self.assertEqual(alarm.call_args.args, (0,))

    def test_nonisolated_or_wrong_ssh_command_fails_before_project_or_config_loading(self):
        with patch.object(build_host.os, "geteuid", return_value=0), \
             patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_host, "_trusted_code") as trusted, \
             patch.object(build_host, "load_config") as loader, \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "bash"}), \
             patch.object(build_host.sys, "stderr"):
            self.assertEqual(build_host.main(["--serve", "--principal", "controller"]), 1)
            trusted.assert_not_called()
            loader.assert_not_called()
            with patch.object(build_host.sys, "flags", SimpleNamespace(isolated=0)):
                self.assertEqual(build_host.main(["--worker"]), 1)
            loader.assert_not_called()

    def test_exact_forced_command_passes_only_its_fixed_principal_to_service(self):
        configured = config()
        with patch.object(build_host.os, "geteuid", return_value=0), \
             patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_host, "_trusted_code"), \
             patch.object(build_host, "_trusted_libraries"), \
             patch.object(build_host, "require_platform"), \
             patch.object(build_host, "load_config", return_value=configured), \
             patch.object(build_host, "serve") as serve, \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": build_host.SSH_COMMAND}):
            self.assertEqual(build_host.main(["--serve", "--principal", "controller"]), 0)
            serve.assert_called_once_with(configured, principal="controller",
                                           config_path=build_host.DEFAULT_CONFIG)

    def test_installed_code_rejects_untrusted_ownership_write_access_and_links(self):
        target = Path("/opt/builder/lib/helper.py")

        def metadata(path):
            return SimpleNamespace(st_uid=0, st_mode=(stat.S_IFREG | 0o644) if path == target
                                   else stat.S_IFDIR | 0o755)

        with patch.object(Path, "lstat", autospec=True, side_effect=metadata):
            build_host._trusted_code(target)
        for bad_uid, bad_mode in ((1000, stat.S_IFREG | 0o644),
                                  (0, stat.S_IFREG | 0o666),
                                  (0, stat.S_IFLNK | 0o777)):
            def unsafe(path):
                return (SimpleNamespace(st_uid=bad_uid, st_mode=bad_mode)
                        if path == target else metadata(path))
            with self.subTest(uid=bad_uid, mode=bad_mode), \
                 patch.object(Path, "lstat", autospec=True, side_effect=unsafe):
                with self.assertRaises(ValueError):
                    build_host._trusted_code(target)

    def test_worker_once_uses_exact_roots_and_no_connection_lifetime(self):
        configured = config()
        with patch("lib.build_jobs.BuildJobStore") as store, \
             patch("lib.build_worker.run_next") as run, \
             patch.object(build_host, "authorizer", return_value=Mock()) as authorization, \
             patch.object(build_host.time, "sleep") as sleep:
            build_host.run_worker(configured, config_path="/private/config", once=True)
            store.assert_called_once_with("/var/lib/builder/state/jobs.json")
            self.assertEqual(run.call_args.kwargs["inputs_root"], configured["roots"]["inputs"])
            self.assertEqual(run.call_args.kwargs["uid"], 999)
            authorization.assert_called_once_with(configured, config_path="/private/config")
            sleep.assert_not_called()

    def test_fixed_tool_probe_has_no_repository_context(self):
        completed = SimpleNamespace(returncode=0, stdout=b"tool-version\n")
        with patch.object(build_host.shutil, "which", side_effect=lambda name, **_: "/usr/bin/" + name), \
             patch("lib.node_toolchain.select_node", return_value=SimpleNamespace(executable="/opt/tools/node")), \
             patch.object(build_host.subprocess, "run", return_value=completed) as run:
            self.assertEqual(set(build_host.observe_tools()), {"go", "node", "python"})
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["cwd"], "/")
                self.assertEqual(call.kwargs["env"]["HOME"], "/nonexistent")
                self.assertEqual(call.kwargs["timeout"], 10)
                self.assertNotIn("SSH_AUTH_SOCK", call.kwargs["env"])

    def test_retention_modes_require_admin_root_and_isolation_before_trust_or_config_reads(self):
        inspect = ["--inspect-retention", "a" * 32]
        settle = ["--settle-terminal", "a" * 32, "--expected-revision", "b" * 64]
        prune = ["--prune-terminal", "a" * 32, "--expected-revision", "b" * 64]
        release = ["--release-bundle", "a" * 32, "--expected-revision", "b" * 64]
        with patch.object(build_host, "_trusted_code") as trusted, \
             patch.object(build_host, "load_config") as loader, \
             patch.object(build_host.sys, "stderr"), \
             patch.dict(os.environ, {}, clear=True):
            for arguments in (inspect, settle, prune, release, ["--inspect-capacity"]):
                with patch.object(build_host.os, "geteuid", return_value=1000), \
                     patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)):
                    self.assertEqual(build_host.main(arguments), 1)
                with patch.object(build_host.os, "geteuid", return_value=0), \
                     patch.object(build_host.sys, "flags", SimpleNamespace(isolated=0)):
                    self.assertEqual(build_host.main(arguments), 1)
                for original in ("", build_host.SSH_COMMAND, "bash"):
                    with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": original}), \
                         patch.object(build_host.os, "geteuid", return_value=0), \
                         patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)):
                        self.assertEqual(build_host.main(arguments), 1)
            trusted.assert_not_called()
            loader.assert_not_called()

    def test_administrator_ssh_can_inspect_and_maintain_without_entering_typed_transport(self):
        configured = config()
        identifier, revision = "a" * 32, "b" * 64
        with patch.object(build_host.os, "geteuid", return_value=0), \
             patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_host, "_trusted_code"), \
             patch.object(build_host, "_trusted_libraries"), \
             patch.object(build_host, "require_platform"), \
             patch.object(build_host, "load_config", return_value=configured), \
             patch("lib.build_capacity.inspect_build_capacity", return_value={"schema": 1}) as inspect, \
             patch.object(build_host, "retain_terminal", return_value={"revision": revision}) as retain, \
             patch.object(build_host, "serve") as serve, \
             patch.object(build_host, "run_worker") as worker, \
             patch.object(build_host.sys, "stdout"), \
             patch.object(build_host.sys, "stderr"), \
             patch.dict(os.environ, {"SSH_CONNECTION": "192.0.2.5 12345 192.0.2.10 22",
                                     "SSH_CLIENT": "192.0.2.5 12345 22"}, clear=True):
            self.assertEqual(build_host.main(["--inspect-capacity"]), 0)
            inspect.assert_called_once_with(configured)
            cases = [(["--inspect-retention", identifier], {"expected_revision": None})]
            for mode, options in (("--settle-terminal", {}),
                                  ("--prune-terminal", {"prune_kinds": ("source", "log")}),
                                  ("--release-bundle", {"release_bundle": True})):
                cases.append(([mode, identifier, "--expected-revision", revision],
                              {"expected_revision": revision, **options}))
            for arguments, options in cases:
                with self.subTest(arguments=arguments):
                    self.assertEqual(build_host.main(arguments), 0)
                    retain.assert_called_once_with(configured, request_id=identifier, **options)
                    retain.reset_mock()
            serve.assert_not_called()
            worker.assert_not_called()

    def test_capacity_mode_is_local_and_passes_only_loaded_configuration(self):
        configured = config()
        with patch.object(build_host.os, "geteuid", return_value=0), \
             patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_host, "_trusted_code"), \
             patch.object(build_host, "_trusted_libraries"), \
             patch.object(build_host, "require_platform"), \
             patch.object(build_host, "load_config", return_value=configured) as loader, \
             patch("lib.build_capacity.inspect_build_capacity", return_value={"schema": 1}) as inspect, \
             patch.object(build_host, "retain_terminal") as retention, \
             patch.object(build_host, "serve") as serve, \
             patch.object(build_host, "run_worker") as worker, \
             patch.object(build_host.sys, "stdout") as output, \
             patch.object(build_host.sys, "stderr") as error, \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build_host.main(["--inspect-capacity", "--config", "/private/config"]), 0)
            inspect.assert_called_once_with(configured)
            loader.assert_called_once_with("/private/config")
            self.assertEqual("".join(call.args[0] for call in output.write.call_args_list), '{"schema":1}\n')
            for arguments in (["--once"], ["--principal", "controller"],
                              ["--expected-revision", "b" * 64], ["--prune-kind", "source"]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(build_host.main(["--inspect-capacity", *arguments]), 1)
            self.assertEqual(inspect.call_count, 1)
            inspect.side_effect = ValueError("private evidence")
            output.reset_mock()
            self.assertEqual(build_host.main(["--inspect-capacity"]), 1)
            output.write.assert_not_called()
            self.assertNotIn("private evidence", str(error.write.call_args_list))
            retention.assert_not_called()
            serve.assert_not_called()
            worker.assert_not_called()

    def test_capacity_suppresses_import_caches_and_restores_flag_on_every_exit(self):
        configured = config()
        for previous in (False, True):
            for failure in (None, "config", "inspect", "interrupted"):
                observed = []

                def load(_path):
                    observed.append("config")
                    self.assertTrue(build_host.sys.dont_write_bytecode)
                    if failure == "config":
                        raise ValueError("configuration unavailable")
                    return configured

                def inspect(config_value):
                    observed.append("inspect")
                    self.assertTrue(build_host.sys.dont_write_bytecode)
                    self.assertIs(config_value, configured)
                    if failure == "inspect":
                        raise ValueError("inspection unavailable")
                    if failure == "interrupted":
                        raise KeyboardInterrupt
                    return {"schema": 1}

                with self.subTest(previous=previous, failure=failure), \
                     patch.object(build_host.sys, "dont_write_bytecode", previous), \
                     patch.object(build_host.os, "geteuid", return_value=0), \
                     patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
                     patch.object(build_host, "_trusted_code"), \
                     patch.object(build_host, "_trusted_libraries"), \
                     patch.object(build_host, "require_platform"), \
                     patch.object(build_host, "load_config", side_effect=load), \
                     patch("lib.build_capacity.inspect_build_capacity", side_effect=inspect), \
                     patch.object(build_host.sys, "stdout"), \
                     patch.object(build_host.sys, "stderr"), \
                     patch.dict(os.environ, {}, clear=True):
                    if failure == "interrupted":
                        with self.assertRaises(KeyboardInterrupt):
                            build_host.main(["--inspect-capacity"])
                    else:
                        self.assertEqual(build_host.main(["--inspect-capacity"]),
                                         0 if failure is None else 1)
                    self.assertIs(build_host.sys.dont_write_bytecode, previous)
                    self.assertEqual(observed, ["config"] if failure == "config"
                                     else ["config", "inspect"])

    def test_local_retention_passes_only_configured_roots_and_exact_id_revision(self):
        configured = config()
        with patch.object(build_host.os, "geteuid", return_value=0), \
             patch.object(build_host.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_host, "_trusted_code"), \
             patch.object(build_host, "_trusted_libraries"), \
             patch.object(build_host, "require_platform"), \
             patch.object(build_host, "load_config", return_value=configured), \
             patch.object(build_host, "retain_terminal", return_value={"revision": "b" * 64}) as retain, \
             patch.object(build_host.sys, "stdout"), \
             patch.object(build_host.sys, "stderr"), \
             patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build_host.main(["--inspect-retention", "a" * 32]), 0)
            retain.assert_called_once_with(configured, request_id="a" * 32, expected_revision=None)
            retain.reset_mock()
            self.assertEqual(build_host.main(["--settle-terminal", "a" * 32,
                                               "--expected-revision", "b" * 64]), 0)
            retain.assert_called_once_with(configured, request_id="a" * 32, expected_revision="b" * 64)
            retain.reset_mock()
            self.assertEqual(build_host.main(["--release-bundle", "a" * 32,
                                               "--expected-revision", "b" * 64]), 0)
            retain.assert_called_once_with(configured, request_id="a" * 32,
                                           expected_revision="b" * 64, release_bundle=True)
            retain.reset_mock()
            for kinds, arguments in ((('source', 'log'), []), (('log',), ["--prune-kind", "log"]),
                                     (('source',), ["--prune-kind", "source"])):
                self.assertEqual(build_host.main(["--prune-terminal", "a" * 32,
                                                   "--expected-revision", "b" * 64, *arguments]), 0)
                retain.assert_called_once_with(configured, request_id="a" * 32,
                                               expected_revision="b" * 64, prune_kinds=kinds)
                retain.reset_mock()
            for arguments in (["--settle-terminal", "a" * 32],
                              ["--prune-terminal", "a" * 32],
                              ["--release-bundle", "a" * 32],
                              ["--release-bundle", "a" * 32, "--expected-revision", "b" * 64,
                               "--prune-kind", "source"],
                              ["--release-bundle", "a" * 32, "--expected-revision", "b" * 64, "--once"],
                              ["--release-bundle", "a" * 32, "--expected-revision", "b" * 64,
                               "--principal", "controller"],
                              ["--inspect-retention", "a" * 32, "--prune-kind", "source"],
                              ["--inspect-retention", "a" * 32, "--expected-revision", "b" * 64],
                              ["--inspect-retention", "a" * 32, "--once"],
                              ["--inspect-retention", "a" * 32, "--principal", "controller"],
                              ["--worker", "--expected-revision", "b" * 64]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(build_host.main(arguments), 1)
            retain.assert_not_called()

    def test_retention_composes_trusted_config_without_worker_launch_or_transport(self):
        configured = config()
        with patch("lib.build_jobs.BuildJobStore") as jobs, \
             patch("lib.build_inputs.BuildInputStore") as inputs, \
             patch("lib.build_retention.BuildRetentionStore") as retention:
            result = build_host.retain_terminal(configured, request_id="a" * 32, expected_revision=None)
            retention.assert_called_once_with(inputs=inputs.return_value, jobs=jobs.return_value,
                                               scratch_root=configured["roots"]["scratch"],
                                               artifacts_root=configured["roots"]["artifacts"],
                                               logs_root=configured["roots"]["logs"])
            self.assertEqual(result, retention.return_value.inspect_retention.return_value)
            retention.return_value.inspect_retention.assert_called_once_with("a" * 32)
            build_host.retain_terminal(configured, request_id="a" * 32, expected_revision="b" * 64)
            retention.return_value.settle_terminal.assert_called_once_with("a" * 32, expected_revision="b" * 64)
            build_host.retain_terminal(configured, request_id="a" * 32, expected_revision="b" * 64,
                                       prune_kinds=("source",))
            retention.return_value.prune_terminal.assert_called_once_with(
                "a" * 32, expected_revision="b" * 64, kinds=("source",))
            build_host.retain_terminal(configured, request_id="a" * 32, expected_revision="b" * 64,
                                       release_bundle=True)
            retention.return_value.release_bundle.assert_called_once_with(
                "a" * 32, expected_revision="b" * 64)


if __name__ == "__main__":
    unittest.main()
