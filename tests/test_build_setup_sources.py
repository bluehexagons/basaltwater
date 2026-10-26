"""Controller preparation contracts; every Git process is replaced by a fixture."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import build_setup_sources as sources
from lib.build_source import SourceLimits, unpack_source_snapshot
from tests.test_build_source import FakeGit


URL = "https://example.com/project.git"
MANIFEST = (b'{"version":1,"components":[{"name":"site","type":"static",'
            b'"domain":"{{domain}}","output":"public"}]}\n')


class SetupGit:
    """Model clone/ref selection/checkout, then use the snapshot Git fixture."""

    def __init__(self):
        self.calls = []
        self.repositories = {}
        self.refs = {"HEAD": "a" * 40, "refs/heads/release/v1": "b" * 40,
                     "refs/tags/v1": "b" * 40, "refs/heads/HEAD": "c" * 40}
        self.manifest = MANIFEST
        self.filter_value = b"unspecified"

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        repository = Path(kwargs["cwd"])
        if "clone" in command:
            (repository / ".git/objects/info").mkdir(parents=True, mode=0o700)
            (repository / ".git/config").write_text("[core]\n bare = true\n")
            self.repositories[str(repository)] = None
            return b""
        if "config" in command and "--local" in command:
            return b""
        if "checkout" in command:
            revision = command[command.index("--force") + 1]
            entries = [("public/index.html", "100644", "blob", b"hello\n")]
            if self.manifest is not None:
                entries.append(("basaltwater.json", "100644", "blob", self.manifest))
            if self.filter_value != b"unspecified":
                entries.append((".gitattributes", "100644", "blob", b"* filter=hostile\n"))
            for path, _mode, _kind, content in entries:
                target = repository / path
                target.parent.mkdir(exist_ok=True, parents=True, mode=0o700)
                target.write_bytes(content)
            git = FakeGit(repository, entries=entries, filter_value=self.filter_value)
            git.revision = git.head_revision = revision
            self.repositories[str(repository)] = git
            return b""
        if ("rev-parse" in command and "--verify" in command
                and not any(arg.startswith("--git-dir=") for arg in command)):
            selected = command[-1].removesuffix("^{commit}")
            if self.repositories.get(str(repository)) is None:
                if selected not in self.refs:
                    raise ValueError("Requested ref does not exist")
                return (self.refs[selected] + "\n").encode("ascii")
        return self.repositories[str(repository)](command, **kwargs)


class BuildSetupSourcesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.destination = self.root / "prepared"
        self.destination.mkdir(mode=0o700)
        self.git = SetupGit()
        # Accidental process creation is a test failure, including from snapshot code.
        native = patch("subprocess.Popen", side_effect=AssertionError("Native commands forbidden"))
        native.start()
        self.addCleanup(native.stop)
        disk = patch.object(sources.shutil, "disk_usage", return_value=Mock(free=1024**4))
        disk.start()
        self.addCleanup(disk.stop)

    def prepare(self, specs=None, **kwargs):
        return sources.prepare_setup_sources(
            specs if specs is not None else [["example.com", URL]],
            str(self.destination), runner=kwargs.pop("runner", self.git), **kwargs,
        )

    def test_real_snapshot_is_shared_across_destinations_and_inputs_are_detached(self):
        result = self.prepare([["one.example/releases, two.example", URL], ["three.example", URL]])
        self.assertEqual([item.destination for item in result],
                         ["one.example/releases", "two.example", "three.example"])
        self.assertEqual(len({item.source_path for item in result}), 1)
        self.assertEqual(sum("clone" in command for command, _ in self.git.calls), 1)
        self.assertEqual(sum("create" in command and "bundle" in command
                             for command, _ in self.git.calls), 1)
        path = Path(result[0].source_path)
        self.assertTrue(path.is_absolute())
        self.assertTrue(path.is_relative_to(self.destination))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(result[0].source["manifest_sha256"], hashlib.sha256(MANIFEST).hexdigest())
        checkout, _history = unpack_source_snapshot(str(path), str(self.root / "unpacked"),
                                                    expected_source=result[0].source)
        self.assertEqual((Path(checkout) / "basaltwater.json").read_bytes(), MANIFEST)
        result[0].manifest.components[0].domain = "changed.example"
        result[0].source["revision"] = "changed"
        self.assertEqual(result[1].manifest.components[0].domain, "{{domain}}")
        self.assertEqual(result[1].source["revision"], "a" * 40)

    def test_distinct_refs_clone_once_and_same_commit_snapshots_once(self):
        result = self.prepare([["a.example", URL + "#branch=release/v1"],
                               ["b.example", URL + "#tag=v1"],
                               ["c.example", URL + "#branch=HEAD"], ["d.example", URL]])
        self.assertEqual([item.source["revision"] for item in result],
                         ["b" * 40, "b" * 40, "c" * 40, "a" * 40])
        self.assertEqual(result[0].source_path, result[1].source_path)
        self.assertEqual(sum("clone" in command for command, _ in self.git.calls), 1)
        checkouts = [command for command, _ in self.git.calls if "checkout" in command]
        self.assertEqual(len(checkouts), 3)
        for command in checkouts:
            self.assertIn("--detach", command)
            self.assertIn("--no-recurse-submodules", command)
            self.assertRegex(command[-1], r"^[a-f0-9]{40}$")

    def test_different_urls_with_the_same_basename_have_separate_clones(self):
        result = self.prepare([["a.example", URL], ["b.example", "https://other.example/project.git"]])
        self.assertEqual(sum("clone" in command for command, _ in self.git.calls), 2)
        self.assertNotEqual(result[0].source_path, result[1].source_path)

    def test_all_commands_isolate_ambient_git_configuration_and_credentials(self):
        hostile = {"HOME": "/hostile", "SSH_AUTH_SOCK": "/agent.sock", "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "filter.hostile.process", "GIT_CONFIG_VALUE_0": "execute-me",
                   "GIT_CONFIG_PARAMETERS": "execute-me", "GIT_EXEC_PATH": "/hostile",
                   "HTTPS_PROXY": "https://secret@proxy.example", "GIT_TEMPLATE_DIR": "/hostile"}
        with patch.dict(os.environ, hostile):
            self.prepare()
        for command, kwargs in self.git.calls:
            env = kwargs["env"]
            self.assertEqual(env["HOME"], str(self.destination / "home"))
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(env["GIT_CONFIG_SYSTEM"], os.devnull)
            self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(env["GIT_SSH_COMMAND"], "/bin/false")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertFalse(set(hostile).difference({"HOME"}) & set(env))
            for setting in ("credential.helper=", "core.fsmonitor=false", "protocol.allow=never",
                            "protocol.https.allow=always", "http.followRedirects=false",
                            f"core.hooksPath={self.destination / 'empty-template'}"):
                self.assertIn(setting, command)
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 120)
            self.assertGreaterEqual(kwargs["max_output_bytes"], 0)
        clone = next(command for command, _ in self.git.calls if "clone" in command)
        self.assertIn("--no-checkout", clone)
        self.assertIn("--bare", clone)
        self.assertIn(f"--template={self.destination / 'empty-template'}", clone)
        self.assertFalse(any(arg.startswith(("--depth", "--shallow", "--filter")) for arg in clone))

    def test_validation_happens_before_git_or_directory_writes(self):
        cases = [[], [["example.com", URL]] * 17, [["example.com"]], ["bad"],
                 [["example.com", 3]], [["/var/www/site", URL]], [["bad host", URL]],
                 [[",".join(f"d{i}.example" for i in range(17)), URL]],
                 [["example.com", URL + "#main"]], [["example.com", URL + "#branch=x..y"]],
                 [["example.com", URL + "#tag=x.lock"]], [["example.com", URL + "#branch=x~1"]],
                 [["example.com", URL + "x" * 6000]],
                 [["example.com/../private", URL]], [["example.com,", URL]],
                 [["example.com", "ssh://git@example.com/project.git"]],
                 [["example.com", "file:///tmp/repository"]],
                 [["example.com", "git@example.com:project.git"]],
                 [["example.com", "http://example.com/project.git"]],
                 [["example.com", "https://user:secret@example.com/project.git"]],
                 [["example.com", URL + "?token=secret"]]]
        for specs in cases:
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                self.prepare(specs)
            self.assertEqual(list(self.destination.iterdir()), [])
            self.assertEqual(self.git.calls, [])

    def test_sixteen_expanded_plans_are_supported(self):
        result = self.prepare([[",".join(f"d{i}.example" for i in range(16)), URL]])
        self.assertEqual(len(result), 16)
        self.assertEqual(len({item.source_path for item in result}), 1)

    def test_destination_must_be_existing_empty_owned_private_and_no_symlinks(self):
        self.destination.chmod(0o750)
        with self.assertRaisesRegex(ValueError, "0700"):
            self.prepare()
        self.destination.chmod(0o700)
        (self.destination / "keep").write_text("untouched")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.prepare()
        self.assertEqual((self.destination / "keep").read_text(), "untouched")
        link = self.root / "link"
        link.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sources.prepare_setup_sources([["example.com", URL]], str(link), runner=self.git)
        with patch.object(sources.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(ValueError, "caller-owned"):
                self.prepare()
        self.assertEqual(self.git.calls, [])

    def test_no_manifest_inference_and_no_replay_of_partial_preparation(self):
        self.git.manifest = None
        with self.assertRaisesRegex(ValueError, "explicit tracked basaltwater.json"):
            self.prepare()
        self.assertTrue((self.destination / "repository-0").exists())
        calls = len(self.git.calls)
        with self.assertRaisesRegex(ValueError, "empty"):
            self.prepare()
        self.assertEqual(len(self.git.calls), calls)

    def test_manifest_must_be_bounded_and_strict_json(self):
        for manifest in (b"{" * (sources.MAX_MANIFEST_BYTES + 1),
                         b'{"version":1,"version":1,"components":[]}'):
            with self.subTest(manifest=manifest[:20]):
                self.git.manifest = manifest
                with self.assertRaises(ValueError):
                    self.prepare()
                # The caller owns cleanup, including incomplete output.
                for child in self.destination.iterdir():
                    if child.is_dir():
                        sources.shutil.rmtree(child)
                    else:
                        child.unlink()

    def test_manifest_is_parsed_and_hashed_from_the_same_bytes(self):
        original = sources._manifest

        def swap_after_read(repository):
            parsed, digest = original(repository)
            changed = MANIFEST.replace(b'"public"', b'"other"')
            path = Path(repository) / "basaltwater.json"
            path.write_bytes(changed)
            fake = FakeGit(Path(repository), entries=[
                ("basaltwater.json", "100644", "blob", changed),
                ("public/index.html", "100644", "blob", b"hello\n"),
            ])
            self.git.repositories[repository] = fake
            return parsed, digest

        with patch.object(sources, "_manifest", side_effect=swap_after_read):
            with self.assertRaisesRegex(ValueError, "identity differ"):
                self.prepare()

    def test_snapshot_revision_must_match_the_pinned_checkout(self):
        with patch.object(sources, "create_source_snapshot", return_value={
            "revision": "b" * 40, "manifest_sha256": hashlib.sha256(MANIFEST).hexdigest(),
            "snapshot_sha256": "c" * 64,
        }):
            with self.assertRaisesRegex(ValueError, "identity differ"):
                self.prepare()

    def test_external_filter_attributes_are_refused_without_running_a_driver(self):
        self.git.filter_value = b"hostile"
        with self.assertRaisesRegex(ValueError, "filter"):
            self.prepare()

    def test_low_controller_disk_is_rejected_before_clone(self):
        with patch.object(sources.shutil, "disk_usage", return_value=Mock(free=1)):
            with self.assertRaisesRegex(ValueError, "disk headroom"):
                self.prepare()
        self.assertEqual(self.git.calls, [])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_snapshot_headroom_is_checked_before_snapshot_creation(self):
        original = self.git

        def runner(command, **kwargs):
            result = original(command, **kwargs)
            if "checkout" in command:
                sources.shutil.disk_usage.return_value.free = 2 * 1024**3
            return result

        with patch.object(sources, "create_source_snapshot") as snapshot:
            with self.assertRaisesRegex(ValueError, "disk headroom"):
                self.prepare(runner=runner)
            snapshot.assert_not_called()

    def test_aggregate_storage_counts_all_clones_instead_of_resetting_per_source(self):
        limits = SourceLimits(max_source_bytes=1024**2, max_history_bytes=1024**2,
                              max_snapshot_bytes=1024**2, max_metadata_bytes=1024**2)
        original = self.git

        def runner(command, **kwargs):
            result = original(command, **kwargs)
            if "clone" in command:
                (Path(kwargs["cwd"]) / ".git/pack").write_bytes(b"x" * (2 * 1024**2))
            return result

        specs = [[f"d{i}.example", f"https://example.com/p{i}.git"] for i in range(3)]
        with self.assertRaisesRegex(ValueError, "Aggregate.*headroom"):
            self.prepare(specs, runner=runner, limits=limits)
        self.assertEqual(sum("clone" in command for command, _ in self.git.calls), 2)

    def test_runner_rejects_unbounded_or_nonbyte_output(self):
        def oversized(command, **kwargs):
            if "rev-parse" in command:
                return b"a" * 81
            return self.git(command, **kwargs)

        with self.assertRaisesRegex(ValueError, "oversized"):
            self.prepare(runner=oversized)

    def test_storage_monitor_latches_a_periodic_failure(self):
        storage = sources._Storage(str(self.destination), SourceLimits())
        storage.stopped = Mock()
        storage.stopped.wait.return_value = False
        with patch.object(storage, "check", side_effect=ValueError("periodic limit")):
            storage._watch()
        with self.assertRaisesRegex(ValueError, "periodic limit"):
            storage.check()

    def test_final_monitor_failure_cannot_race_successful_return(self):
        storage = sources._Storage(str(self.destination), SourceLimits())
        watcher = Mock()

        def finish_scan():
            storage.failure = ValueError("late storage limit")

        watcher.join.side_effect = finish_scan
        with patch.object(sources.threading, "Thread", return_value=watcher):
            with self.assertRaisesRegex(ValueError, "late storage limit"):
                with storage.monitor():
                    pass

    def test_zero_output_limit_discards_stdout_without_spooling_or_reading(self):
        storage = sources._Storage(str(self.destination), SourceLimits())
        process = Mock(pid=123, returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired("git", 0.1), (None, None)]
        with patch.object(sources.subprocess, "Popen", return_value=process) as popen, \
                patch.object(sources.tempfile, "NamedTemporaryFile") as spool, \
                patch.object(storage, "check") as check:
            spool.return_value.__enter__.return_value.tell.side_effect = AssertionError(
                "Discarded stdout must not be inspected"
            )
            result = sources._run(
                ["git", "update-ref", "--stdin"], cwd=str(self.destination), env={},
                input_data=b"start\nprepare\ncommit\n", timeout=1, max_output_bytes=0,
                file_size_limit=100, storage=storage,
            )
        self.assertEqual(result, b"")
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        spool.assert_not_called()
        self.assertEqual(check.call_count, 2)
        self.assertEqual(process.communicate.call_count, 2)
        launch = popen.call_args.args[0]
        self.assertEqual(launch[launch.index(sources._LIMIT_EXEC) + 1], "100")

    def test_native_runner_kills_process_group_on_periodic_storage_failure(self):
        storage = sources._Storage(str(self.destination), SourceLimits())
        process = Mock(pid=123, returncode=0)
        process.communicate.side_effect = subprocess.TimeoutExpired("git", 0.1)
        with patch.object(storage, "check", side_effect=[None, ValueError("aggregate limit")]), \
                patch.object(sources.subprocess, "Popen", return_value=process), \
                patch.object(sources.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "aggregate limit"):
                sources._run(["git"], cwd=str(self.destination), env={}, input_data=b"input",
                             timeout=1, max_output_bytes=10, file_size_limit=100, storage=storage)
        self.assertEqual(kill.call_args_list[0].args, (123, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (123, signal.SIGKILL))
        process.communicate.assert_called_once()

    def test_native_runner_enforces_timeout_and_output_without_exposing_diagnostics(self):
        storage = sources._Storage(str(self.destination), SourceLimits())
        process = Mock(pid=123, returncode=0)
        with patch.object(sources.subprocess, "Popen", return_value=process), \
                patch.object(sources.os, "killpg"), \
                patch.object(sources.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                sources._run(["git"], cwd=str(self.destination), env={}, input_data=None,
                             timeout=1, max_output_bytes=10, file_size_limit=100, storage=storage)

        def spawn(*args, **kwargs):
            kwargs["stdout"].write(b"output exceeds ten bytes")
            return process

        with patch.object(sources.subprocess, "Popen", side_effect=spawn) as popen, \
                patch.object(sources.os, "killpg"):
            with self.assertRaisesRegex(ValueError, "output exceeds"):
                sources._run(["git"], cwd=str(self.destination), env={}, input_data=None,
                             timeout=1, max_output_bytes=10, file_size_limit=100, storage=storage)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["umask"], 0o077)
        self.assertNotIn("preexec_fn", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
