from __future__ import annotations

from contextlib import ExitStack
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import build_git


class BuildGitTests(unittest.TestCase):
    def prepare(self, replies=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("getuid", 1234), ("getgid", 1234), ("getgroups", [])):
            stack.enter_context(patch.object(build_git.os, name, return_value=value))
        stack.enter_context(patch.object(build_git.os.path, "lexists", return_value=False))
        self.git = stack.enter_context(patch.object(build_git, "_git", side_effect=replies or [
            b"", b"a" * 40 + b" HEAD\n" + b"b" * 40 + b" refs/tags/v1.0\n", b"", b"", b"", b"",
        ]))

    def test_local_unbundle_restores_only_pinned_head_and_tags_without_checkout(self):
        self.prepare()
        build_git.prepare_history("/work/history.bundle", "a" * 40, "/work/source", "/work/home")
        commands = [call.args[0] for call in self.git.call_args_list]
        self.assertEqual(commands[:2], [["init", "--quiet", "--object-format=sha1"],
                                      ["bundle", "unbundle", "/work/history.bundle"]])
        self.assertFalse(any(command[0] in {"fetch", "clone", "checkout"} for command in commands))
        updates = self.git.call_args_list[2].kwargs["input_data"]
        self.assertEqual(updates, ("create refs/heads/source " + "a" * 40 + "\ncreate refs/tags/v1.0 " + "b" * 40 + "\n").encode())
        environment = self.git.call_args.args[2]
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(environment["TMPDIR"], "/work/home/tmp")

    def test_mismatched_malformed_or_unsupported_refs_never_reach_ref_updates(self):
        for raw in (b"a" * 40 + b" refs/heads/other\n", b"b" * 40 + b" HEAD\n",
                    b"a" * 40 + b" HEAD\n" + b"a" * 40 + b" HEAD\n",
                    b"a" * 40 + b" HEAD", b"bad HEAD\n",
                    b"a" * 40 + b" HEAD\n" + b"b" * 40 + b" refs/tags/a bad\n",
                    b"a" * 40 + b" HEAD\n" + b"b" * 40 + b" refs/tags/a\tbad\n"):
            with self.subTest(raw=raw):
                self.prepare([b"", raw])
                with self.assertRaises(ValueError):
                    build_git.prepare_history("/work/history.bundle", "a" * 40, "/work/source", "/work/home")
                self.assertEqual(self.git.call_count, 2)

    def test_dirty_restored_source_is_rejected(self):
        self.prepare([b"", b"a" * 40 + b" HEAD\n", b"", b"", b"", b" M basaltwater.json\0"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_git.prepare_history("/work/history.bundle", "a" * 40, "/work/source", "/work/home")

    def test_privileged_or_shared_group_execution_is_rejected_before_git(self):
        self.prepare()
        for name, value in (("getuid", 0), ("getgid", 0), ("getgroups", [1234])):
            with self.subTest(name=name), patch.object(build_git.os, name, return_value=value):
                with self.assertRaises(RuntimeError):
                    build_git.prepare_history("/work/history.bundle", "a" * 40, "/work/source", "/work/home")
        self.git.assert_not_called()

    def test_git_command_uses_private_spooled_output_and_rejects_failure_or_overflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"TMPDIR": temporary}
            for status, data, expected in ((0, b"refs", b"refs"), (1, b"", None),
                                           (0, b"too much", None)):
                with self.subTest(status=status, data=data):
                    def invoke(argv, **kwargs):
                        self.assertEqual(argv[:3], ["/usr/bin/git", "-c", "core.hooksPath=/dev/null"])
                        self.assertIn("init.templateDir=/dev/null", argv)
                        self.assertEqual(kwargs["cwd"], "/work/source")
                        self.assertIs(kwargs["env"], environment)
                        self.assertEqual(kwargs["input"], b"updates")
                        self.assertIsNone(kwargs["stdin"])
                        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                        self.assertFalse(kwargs["check"])
                        kwargs["stdout"].write(data)
                        return Mock(returncode=status)
                    with patch.object(build_git.subprocess, "run", side_effect=invoke), \
                            patch.object(build_git, "_MAX_OUTPUT", 4):
                        if expected is None:
                            with self.assertRaises(ValueError):
                                build_git._git(["update-ref", "--stdin"], "/work/source", environment,
                                               input_data=b"updates")
                        else:
                            self.assertEqual(build_git._git(["update-ref", "--stdin"], "/work/source",
                                                            environment, input_data=b"updates"), expected)


if __name__ == "__main__":
    unittest.main()
