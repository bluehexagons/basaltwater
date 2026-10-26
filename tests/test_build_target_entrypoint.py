"""Root-controlled target SSH entrypoint and configuration boundary."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

from common.service_tools import build_target
from lib import build_target_maintenance
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import MAX_ARTIFACT_BYTES


def target_policy(
    *, deployment_root: str = "/srv/apps/shop", principal: str = "alice@example",
) -> dict:
    return {
        "schema": 1,
        "applications": {
            "shop": {
                "project": "shop-source",
                "principals": [principal],
                "deployment_root": deployment_root,
                "platform": dict(DEBIAN_PLATFORM),
                "limits": {"artifact_bytes": MAX_ARTIFACT_BYTES, "artifact_entries": 100_000},
                "components": {
                    "web": {"type": "static", "domain": "shop.example", "path": "/"},
                },
            },
        },
    }


def config(*, root_base: str = "/var/lib/basaltwater/target") -> dict:
    return {
        "schema": 1,
        "roots": {name: root_base + "/" + name for name in build_target._ROOTS},
        "policy": target_policy(),
        "max_records": 128,
        "max_bytes": 64 * 1024**3,
    }


class BinaryStreams:
    def __init__(self, buffer: BytesIO) -> None:
        self.buffer = buffer


@contextmanager
def stdio_channels():
    """Give fd1/fd2 disposable destinations without changing the test runner."""
    with tempfile.TemporaryFile() as wire, tempfile.TemporaryFile() as diagnostics:
        saved = [(fd, os.dup(fd), os.get_inheritable(fd)) for fd in (1, 2)]
        try:
            os.dup2(wire.fileno(), 1)
            os.dup2(diagnostics.fileno(), 2)
            with os.fdopen(1, "w", closefd=False) as stdout, \
                 os.fdopen(2, "w", buffering=1, closefd=False) as stderr, \
                 patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
                yield SimpleNamespace(stdout=stdout, stderr=stderr, wire=wire,
                                      diagnostics=diagnostics)
        finally:
            for fd, duplicate, inheritable in saved:
                try:
                    os.dup2(duplicate, fd, inheritable=inheritable)
                finally:
                    os.close(duplicate)


class BuildTargetEntrypointTests(unittest.TestCase):
    def test_config_is_detached_strict_bounded_and_rejects_overlap(self) -> None:
        original = config()
        parsed = build_target.parse_config(original)
        parsed["policy"]["applications"]["shop"]["principals"].clear()
        self.assertEqual(original["policy"]["applications"]["shop"]["principals"], ["alice@example"])

        cases = []
        for key, value in (("schema", True), ("max_records", True), ("max_records", 4097),
                           ("max_bytes", 256 * 1024**2 - 1), ("extra", 1)):
            item = config()
            item[key] = value
            cases.append(item)
        for path in ("relative", "/", "//var/lib/target", "/var/../tmp/target",
                     "/var/private path", "/var/%n"):
            item = config()
            item["roots"]["admission"] = path
            cases.append(item)
        for name, path in (("verify", "/var/lib/basaltwater/target/admission"),
                           ("verify", "/var/lib/basaltwater/target/admission/nested")):
            item = config()
            item["roots"][name] = path
            cases.append(item)
        item = config()
        item["roots"]["verify"] = "/srv/apps/shop/private"
        cases.append(item)
        for item in cases:
            with self.subTest(config=item), self.assertRaises(ValueError):
                build_target.parse_config(item)

    def test_config_file_is_root_private_bounded_regular_nofollow_and_single_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            os.chmod(base, 0o700)
            config_dir = base / "config"
            config_dir.mkdir(mode=0o700)
            roots = {}
            for name in build_target._ROOTS:
                root = base / name
                root.mkdir(mode=0o700)
                roots[name] = str(root)
            configured = config(root_base=str(base / "target"))
            configured["roots"] = roots
            path = config_dir / "config.json"
            path.write_text(json.dumps(configured), encoding="utf-8")
            path.chmod(0o600)

            real_fstat = os.fstat

            def root_owned(descriptor):
                observed = real_fstat(descriptor)
                values = {name: getattr(observed, name) for name in (
                    "st_mode", "st_uid", "st_nlink", "st_size", "st_dev", "st_ino",
                    "st_mtime_ns", "st_ctime_ns",
                )}
                values["st_uid"] = 0
                return SimpleNamespace(**values)

            with patch.object(build_target.os, "fstat", side_effect=root_owned):
                self.assertEqual(build_target.load_config(str(path))["max_records"], 128)
                oversized = build_target.CONFIG_OVERHEAD_BYTES + 4 * 1024**2 + 1
                path.write_bytes(b" " * oversized)
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    build_target.load_config(str(path))
                path.write_text(json.dumps(configured), encoding="utf-8")
                path.chmod(0o600)

                real_open = os.open
                opened_config = []
                config_fstats = 0

                def tracked_open(file, *args, **kwargs):
                    descriptor = real_open(file, *args, **kwargs)
                    if file == path.name:
                        opened_config.append(descriptor)
                    return descriptor

                def changed_identity(descriptor):
                    nonlocal config_fstats
                    values = root_owned(descriptor).__dict__.copy()
                    if opened_config and descriptor == opened_config[0]:
                        config_fstats += 1
                        if config_fstats == 2:
                            values["st_mtime_ns"] += 1
                    return SimpleNamespace(**values)

                with patch.object(build_target.os, "open", side_effect=tracked_open), \
                     patch.object(build_target.os, "fstat", side_effect=changed_identity), \
                     self.assertRaises(ValueError):
                    build_target.load_config(str(path))

                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    build_target.load_config(str(path))
                path.chmod(0o600)
                os.link(path, config_dir / "alias.json")
                with self.assertRaises(ValueError):
                    build_target.load_config(str(path))
                (config_dir / "alias.json").unlink()
                (config_dir / "link.json").symlink_to(path)
                with self.assertRaises(OSError):
                    build_target.load_config(str(config_dir / "link.json"))

    def test_installed_code_rejects_untrusted_ownership_write_access_and_links(self) -> None:
        target = Path("/opt/basaltwater/common/service_tools/build_target.py")

        def metadata(path):
            mode = stat.S_IFREG | 0o644 if path == target else stat.S_IFDIR | 0o755
            return SimpleNamespace(st_uid=0, st_mode=mode)

        with patch.object(Path, "lstat", autospec=True, side_effect=metadata):
            build_target._trusted_code(target)
        def sticky_ancestor(path):
            if path == Path("/opt/basaltwater"):
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777)
            return metadata(path)
        with patch.object(Path, "lstat", autospec=True, side_effect=sticky_ancestor):
            build_target._trusted_code(target)
        for bad_uid, bad_mode in ((1000, stat.S_IFREG | 0o644),
                                  (0, stat.S_IFREG | 0o666),
                                  (0, stat.S_IFLNK | 0o777)):
            def unsafe(path):
                return (SimpleNamespace(st_uid=bad_uid, st_mode=bad_mode)
                        if path == target else metadata(path))
            with self.subTest(uid=bad_uid, mode=bad_mode), \
                 patch.object(Path, "lstat", autospec=True, side_effect=unsafe), \
                 self.assertRaises(ValueError):
                build_target._trusted_code(target)

    def test_installed_library_coverage_matches_builder_package_set(self) -> None:
        root = Path("/opt/basaltwater")
        with patch.object(build_target, "_trusted_code") as trusted, \
             patch.object(build_target.os, "walk", return_value=[]):
            build_target._trusted_libraries(root)
        self.assertEqual(
            {call.args[0] for call in trusted.call_args_list},
            {root / package for package in build_target._LIBRARY_PACKAGES},
        )

    def test_platform_is_fixed_to_debian_13_x86_64(self) -> None:
        with patch.object(build_target.platform, "system", return_value="Linux"), \
             patch.object(build_target.platform, "machine", return_value="x86_64"), \
             patch.object(build_target.platform, "freedesktop_os_release",
                          return_value={"ID": "debian", "VERSION_ID": "13"}) as release:
            build_target.require_platform()
            for value in ({"ID": "debian", "VERSION_ID": "12"},
                          {"ID": "ubuntu", "VERSION_ID": "13"}):
                release.return_value = value
                with self.assertRaises(ValueError):
                    build_target.require_platform()
        with patch.object(build_target.platform, "machine", return_value="aarch64"), \
             patch.object(build_target.platform, "system", return_value="Linux"), \
             patch.object(build_target.platform, "freedesktop_os_release",
                          return_value={"ID": "debian", "VERSION_ID": "13"}), \
             self.assertRaises(ValueError):
            build_target.require_platform()

    def test_policy_loader_reloads_policy_and_refuses_structural_changes(self) -> None:
        configured = build_target.parse_config(config())
        load = build_target.current_policy_loader(configured, config_path="/private/config.json")
        changed_policy = config()
        changed_policy["policy"] = target_policy(principal="bob@example")
        structural_change = config()
        structural_change["max_records"] += 1
        with patch.object(build_target, "load_config", side_effect=[
            build_target.parse_config(changed_policy), build_target.parse_config(structural_change),
        ]) as loader:
            self.assertEqual(load()["applications"]["shop"]["principals"], ["bob@example"])
            with self.assertRaises(PermissionError):
                load()
        self.assertEqual([call.args for call in loader.call_args_list], [
            ("/private/config.json",), ("/private/config.json",),
        ])

        mutable = build_target.parse_config(config())
        mutable_loader = build_target.current_policy_loader(mutable, config_path="/private/config.json")
        mutable["roots"]["verify"] = "/var/lib/basaltwater/target/other-verify"
        with patch.object(build_target, "load_config", return_value=mutable), \
             self.assertRaises(PermissionError):
            mutable_loader()

    def test_invocation_rejects_nonroot_nonisolated_or_wrong_original_command_early(self) -> None:
        stderr = StringIO()
        with patch.object(build_target.os, "geteuid", return_value=0), \
             patch.object(build_target.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_target, "_trusted_code") as trusted, \
             patch.object(build_target, "load_config") as loader, \
             patch.object(build_target.sys, "stderr", stderr), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "bash"}):
            self.assertEqual(build_target.main(["--serve", "--principal", "alice@example"]), 1)
            trusted.assert_not_called()
            loader.assert_not_called()
            with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": build_target.SSH_COMMAND}), \
                 patch.object(build_target.sys, "flags", SimpleNamespace(isolated=0)):
                self.assertEqual(build_target.main(["--serve", "--principal", "alice@example"]), 1)
            with patch.object(build_target.os, "geteuid", return_value=1000):
                self.assertEqual(build_target.main(["--serve", "--principal", "alice@example"]), 1)
        self.assertNotIn("bash", stderr.getvalue())

    def test_exact_invocation_checks_trust_before_platform_config_and_serve(self) -> None:
        configured = build_target.parse_config(config())
        stderr = StringIO()
        events = []

        @contextmanager
        def lease():
            events.append("lock")
            yield
            events.append("unlock")

        def load(_path):
            events.append("config")
            return configured

        with patch.object(build_target.os, "geteuid", return_value=0), \
             patch.object(build_target.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_target, "_trusted_code") as trusted_code, \
             patch.object(build_target, "_trusted_libraries") as trusted_libraries, \
             patch.object(build_target, "require_platform") as require_platform, \
             patch.object(build_target_maintenance, "target_lease", side_effect=lease), \
             patch.object(build_target, "load_config", side_effect=load) as load_config, \
             patch.object(build_target, "serve", side_effect=lambda *_args, **_kwargs: events.append("response")) as serve, \
             patch.object(build_target.sys, "stderr", stderr), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": build_target.SSH_COMMAND}):
            result = build_target.main(["--serve", "--principal", "alice@example",
                                        "--config", "/etc/private/target.json"])
        self.assertEqual(result, 0)
        self.assertEqual(events, ["lock", "config", "response", "unlock"])
        trusted_code.assert_called_once()
        trusted_libraries.assert_called_once_with(trusted_code.call_args.args[0].parents[2])
        require_platform.assert_called_once()
        load_config.assert_called_once_with("/etc/private/target.json")
        serve.assert_called_once_with(configured, principal="alice@example",
                                      config_path="/etc/private/target.json")

    def test_serve_composes_exact_stores_under_deadline_and_preserves_binary_stdout(self) -> None:
        configured = build_target.parse_config(config())
        input_bytes = BytesIO(b"header\n")
        admissions, artifact_store, activator, service = Mock(), Mock(), Mock(), Mock()
        artifact_module = ModuleType("lib.build_target_artifacts")
        artifact_module.BuildTargetArtifactStore = Mock(return_value=artifact_store)
        protocol_streams = []

        def service_once(**kwargs):
            self.assertIs(sys.stdout, sys.stderr)
            sys.stdout.write("deployment and nginx diagnostic\n")
            os.write(1, b"native diagnostic\n")
            subprocess.run(["id", "app-go-api"], check=False)
            # A cached Python stdout reference can still hold buffered text.
            channels.stdout.write("buffered deployment diagnostic")
            protocol_streams.append(kwargs["output_stream"])
            self.assertFalse(os.get_inheritable(kwargs["output_stream"].fileno()))
            self.assertNotEqual(kwargs["output_stream"].fileno(), 1)
            kwargs["output_stream"].write(b'{"ok":true}\n')
            kwargs["output_stream"].flush()

        service.serve_once.side_effect = service_once
        service_factory = Mock(return_value=service)
        activation_factory = Mock(return_value=activator)
        stdin = BinaryStreams(input_bytes)
        events = []
        with stdio_channels() as channels, \
             patch.dict(sys.modules, {"lib.build_target_artifacts": artifact_module}), \
             patch("lib.build_admission.DeploymentAdmissionStore", return_value=admissions) as admission_factory, \
             patch("lib.build_target_activation.TargetReleaseActivator", activation_factory), \
             patch("lib.build_target_service.BuildTargetService", service_factory), \
             patch.object(build_target, "connection_deadline", wraps=build_target.connection_deadline) as deadline, \
             patch.object(build_target.signal, "signal", return_value="old"), \
             patch.object(build_target.signal, "alarm", side_effect=lambda seconds: events.append(seconds)), \
             patch.object(build_target.sys, "stdin", stdin), \
             patch.object(subprocess, "run", side_effect=lambda *_args, **_kwargs:
                          os.write(1, b"uid=995(app-go-api) inherited child diagnostic\n")) as child:
            channels.stdout.write("queued before serving")
            build_target.serve(configured, principal="alice@example", config_path="/etc/target.json")
            self.assertIs(sys.stdout, channels.stdout)
            channels.wire.seek(0)
            self.assertEqual(channels.wire.read(), b'{"ok":true}\n')
            channels.diagnostics.seek(0)
            diagnostic_bytes = channels.diagnostics.read()

        self.assertEqual(events, [900, 0])
        deadline.assert_called_once_with()
        roots = configured["roots"]
        admission_factory.assert_called_once_with(os.path.join(roots["admission"], "admissions.json"))
        artifact_module.BuildTargetArtifactStore.assert_called_once_with(
            roots["artifacts"], max_records=128, max_bytes=64 * 1024**3,
        )
        activation_factory.assert_called_once_with(
            roots["releases"], roots["verify"], open_artifact=artifact_store.open_verified_bundle,
        )
        service_factory.assert_called_once_with(
            admissions=admissions,
            load_policy=ANY,
            stage_artifact=artifact_store.stage,
            activate_release=activator,
        )
        service.serve_once.assert_called_once_with(
            principal="alice@example", input_stream=input_bytes, output_stream=protocol_streams[0],
        )
        child.assert_called_once_with(["id", "app-go-api"], check=False)
        self.assertTrue(protocol_streams[0].closed)
        for diagnostic in (b"deployment and nginx diagnostic", b"native diagnostic",
                           b"uid=995(app-go-api)", b"buffered deployment diagnostic",
                           b"queued before serving"):
            self.assertIn(diagnostic, diagnostic_bytes)

    def test_serve_failure_restores_stdout_and_closes_protocol_under_deadline(self) -> None:
        artifact_module = ModuleType("lib.build_target_artifacts")
        artifact_module.BuildTargetArtifactStore = Mock()
        for phase in ("composition", "response", "interruption"):
            with self.subTest(phase=phase), stdio_channels() as channels:
                # Also preserve a caller's deliberately non-inheritable fd1.
                os.set_inheritable(1, phase != "interruption")
                original = os.fstat(1)
                inheritable = os.get_inheritable(1)
                protocol_streams = []
                events = []

                def fail(**kwargs):
                    os.write(1, b"failure diagnostic\n")
                    channels.stdout.write("buffered failure diagnostic")
                    protocol_streams.append(kwargs["output_stream"])
                    if phase == "interruption":
                        kwargs["output_stream"].write(b'{"ok":true}\n')
                        raise KeyboardInterrupt
                    with patch.object(kwargs["output_stream"], "write",
                                      side_effect=BrokenPipeError("response disconnected")):
                        kwargs["output_stream"].write(b'{"ok":true}\n')

                failure = (OSError if phase == "composition" else
                           KeyboardInterrupt if phase == "interruption" else BrokenPipeError)
                real_fdopen = os.fdopen
                with patch.dict(sys.modules, {"lib.build_target_artifacts": artifact_module}), \
                     patch("lib.build_admission.DeploymentAdmissionStore",
                           side_effect=OSError("setup failed") if phase == "composition" else None), \
                     patch("lib.build_target_activation.TargetReleaseActivator"), \
                     patch("lib.build_target_service.BuildTargetService") as service, \
                     patch.object(build_target.os, "fdopen", wraps=real_fdopen) as fdopen, \
                     patch.object(build_target.signal, "signal", return_value="old") as handler, \
                     patch.object(build_target.signal, "alarm", side_effect=events.append), \
                     patch.object(build_target.sys, "stdin", BinaryStreams(BytesIO())), \
                     self.assertRaises(failure):
                    service.return_value.serve_once.side_effect = fail
                    build_target.serve(config(), principal="alice@example", config_path="/unused")

                self.assertIs(sys.stdout, channels.stdout)
                restored = os.fstat(1)
                self.assertEqual((restored.st_dev, restored.st_ino),
                                 (original.st_dev, original.st_ino))
                self.assertEqual(os.get_inheritable(1), inheritable)
                with self.assertRaises(OSError):
                    os.fstat(fdopen.call_args.args[0])
                self.assertTrue(all(stream.closed for stream in protocol_streams))
                self.assertEqual(events, [900, 0])
                self.assertEqual(handler.call_args_list[-1].args,
                                 (build_target.signal.SIGALRM, "old"))
                channels.wire.seek(0)
                self.assertEqual(channels.wire.read(),
                                 b'{"ok":true}\n' if phase == "interruption" else b"")
                if phase != "composition":
                    channels.diagnostics.seek(0)
                    self.assertEqual(channels.diagnostics.read(),
                                     b"failure diagnostic\nbuffered failure diagnostic")

    def test_protocol_stdout_setup_and_flush_failures_close_duplicate_and_restore_fd(self) -> None:
        for phase in ("fdopen", "redirect", "initial_flush", "flush"):
            with self.subTest(phase=phase), stdio_channels() as channels:
                original = os.fstat(1)
                inheritable = os.get_inheritable(1)
                real_dup2, real_fdopen = os.dup2, os.fdopen
                flush_failures = {
                    "initial_flush": [OSError("initial flush failed"), None],
                    "flush": [None, OSError("flush failed")],
                }
                with patch.object(build_target.os, "fdopen", wraps=real_fdopen) as fdopen, \
                     patch.object(build_target.os, "dup2", wraps=real_dup2) as redirect, \
                     patch.object(channels.stdout, "flush", side_effect=flush_failures.get(phase)), \
                     self.assertRaises(OSError):
                    if phase == "fdopen":
                        fdopen.side_effect = OSError("fdopen failed")
                    elif phase == "redirect":
                        redirect.side_effect = [OSError("dup2 failed"), None]
                    with build_target._protocol_stdout():
                        pass
                self.assertIs(sys.stdout, channels.stdout)
                self.assertEqual(os.fstat(1), original)
                self.assertEqual(os.get_inheritable(1), inheritable)
                with self.assertRaises(OSError):
                    os.fstat(fdopen.call_args.args[0])

    def test_connection_deadline_restores_alarm_and_handler_after_failure(self) -> None:
        with patch.object(build_target.signal, "signal", return_value="old") as handler, \
             patch.object(build_target.signal, "alarm") as alarm:
            with self.assertRaises(KeyboardInterrupt):
                with build_target.connection_deadline():
                    raise KeyboardInterrupt
        self.assertEqual(alarm.call_args_list[0].args, (900,))
        self.assertEqual(alarm.call_args_list[-1].args, (0,))
        self.assertEqual(handler.call_args_list[-1].args,
                         (build_target.signal.SIGALRM, "old"))

    def test_unexpected_failure_emits_only_fixed_stderr_text(self) -> None:
        stderr = StringIO()
        with patch.object(build_target.os, "geteuid", return_value=0), \
             patch.object(build_target.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_target, "_trusted_code"), \
             patch.object(build_target, "_trusted_libraries"), \
             patch.object(build_target, "require_platform"), \
             patch.object(build_target_maintenance, "target_lease", return_value=nullcontext()), \
             patch.object(build_target, "load_config", side_effect=RuntimeError("/private token=secret")), \
             patch.object(build_target.sys, "stderr", stderr), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": build_target.SSH_COMMAND}):
            self.assertEqual(build_target.main(["--serve", "--principal", "alice@example"]), 1)
        self.assertEqual(stderr.getvalue(),
                         "Target operation is unavailable; inspect protected host evidence\n")

    def test_busy_or_held_enrollment_refuses_before_configuration_load(self) -> None:
        with patch.object(build_target.os, "geteuid", return_value=0), \
             patch.object(build_target.sys, "flags", SimpleNamespace(isolated=1)), \
             patch.object(build_target, "_trusted_code"), \
             patch.object(build_target, "_trusted_libraries"), \
             patch.object(build_target, "require_platform"), \
             patch.object(build_target_maintenance, "target_lease", side_effect=RuntimeError("busy")), \
             patch.object(build_target, "load_config") as loader, \
             patch.object(build_target, "serve") as serve, \
             patch.object(build_target.sys, "stderr", StringIO()), \
             patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": build_target.SSH_COMMAND}):
            self.assertEqual(build_target.main(["--serve", "--principal", "alice"]), 1)
        loader.assert_not_called()
        serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
