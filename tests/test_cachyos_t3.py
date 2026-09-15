"""CachyOS T3 activation tests with temporary files and mocked host commands."""

from __future__ import annotations

from contextlib import ExitStack
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common import cachyos_t3 as t3
from lib.config import SetupConfig


class T3InstallTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.prefix = self.home / ".local/share/infra-tools/cachyos-t3"
        self.binary = self.prefix / "bin/t3"
        self.unit = self.home / ".config/systemd/user" / t3.T3_SERVICE
        self.config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos",
                                  agent_tools=["codex"], web_interfaces=["t3code"])
        self.events = []
        self.active = False
        self.enabled = False
        self.failure = None
        stack.enter_context(patch.object(t3, "_home", return_value=self.home))
        self.user_run = stack.enter_context(patch.object(t3, "_user_run", side_effect=self.user_command))
        self.system_run = stack.enter_context(patch.object(t3, "run", side_effect=self.system_command))
        self.ui = stack.enter_context(patch.object(t3, "_wait_for_ui"))

    @staticmethod
    def runtime(prefix):
        target = prefix / "lib/node_modules/t3/dist/bin.mjs"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        binary = prefix / "bin/t3"
        binary.parent.mkdir(parents=True)
        binary.symlink_to("../lib/node_modules/t3/dist/bin.mjs")
        return target

    def legacy(self, *, active=True, enabled=True):
        target = self.runtime(self.prefix)
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text(t3._MARKER + "\n[Service]\nWorkingDirectory=/\nExecStart=" + str(self.binary) + "\n")
        self.unit.chmod(0o600)
        self.active, self.enabled = active, enabled
        return target, self.unit.read_text()

    def user_command(self, argv, home, **kwargs):
        self.events.append(("user", argv))
        if argv[:2] == ["npm", "install"]:
            if self.failure == "npm":
                raise RuntimeError("npm fixture failure")
            candidate = Path(argv[argv.index("--prefix") + 1])
            self.assertNotEqual(candidate, self.prefix)
            self.assertTrue(candidate.is_relative_to(self.prefix / "releases"))
            self.runtime(candidate)
        if argv[:2] == ["node", "-e"] and self.failure == "native":
            raise RuntimeError("native fixture failure")
        output = "v24.10.0\n" if argv[:2] == ["node", "--version"] else "t3 v0.0.40\n"
        return subprocess.CompletedProcess(argv, 0, output, "")

    def system_command(self, argv, **kwargs):
        self.events.append(("system", argv))
        if argv[0] == "systemd-analyze" and self.failure == "verify":
            raise RuntimeError("unit fixture failure")
        code = 0
        if "is-active" in argv:
            code = 0 if self.active else 3
        elif "is-enabled" in argv:
            code = 0 if self.enabled else 1
        elif "start" in argv:
            self.active = True
        elif "stop" in argv:
            self.active = False
        elif "enable" in argv:
            self.enabled = True
        elif "disable" in argv:
            self.enabled = False
        return subprocess.CompletedProcess(argv, code, "", "")

    def test_initial_install_validates_before_activation_and_keeps_cli_path(self):
        t3.install(self.config)
        self.assertTrue(self.binary.resolve().is_relative_to(self.prefix / "releases"))
        content = self.unit.read_text()
        self.assertIn("UMask=0077", content)
        self.assertIn("WorkingDirectory=" + str(self.home / "repos"), content)
        self.assertNotIn('WorkingDirectory="', content)
        self.assertIn("--host 127.0.0.1 --port 3773", content)
        self.ui.assert_called_once_with("http://127.0.0.1:3773/")
        verify = next(i for i, (_, cmd) in enumerate(self.events) if cmd[0] == "systemd-analyze")
        start = next(i for i, (_, cmd) in enumerate(self.events) if "start" in cmd)
        native = next(i for i, (_, cmd) in enumerate(self.events) if cmd[:2] == ["node", "-e"])
        self.assertLess(native, verify)
        self.assertLess(verify, start)
        self.assertFalse((self.prefix / ".activation").exists())

    def test_reruns_preserve_previous_release_and_prune_only_older_managed_ones(self):
        target, _ = self.legacy()
        t3.install(self.config)
        first = self.binary.resolve()
        unrelated = self.prefix / "releases/personal"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")
        t3.install(self.config)
        second = self.binary.resolve()
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())
        t3.install(self.config)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(target.exists())  # Legacy npm data is never pruned.
        self.assertTrue((unrelated / "keep.txt").exists())

    def test_staging_failures_never_change_or_stop_the_old_service(self):
        target, content = self.legacy()
        for failure in ("npm", "native", "verify"):
            with self.subTest(failure=failure):
                self.failure = failure
                self.events.clear()
                with self.assertRaises(RuntimeError):
                    t3.install(self.config)
                self.assertEqual(self.binary.resolve(), target)
                self.assertEqual(self.unit.read_text(), content)
                self.assertTrue(self.active)
                self.assertFalse(any("stop" in cmd for _, cmd in self.events))
                self.assertEqual(list((self.prefix / "releases").iterdir()), [])

    def test_startup_failure_restores_legacy_runtime_unit_mode_and_service(self):
        target, content = self.legacy()
        self.ui.side_effect = RuntimeError("UI fixture failure")
        with self.assertRaisesRegex(RuntimeError, "UI fixture failure"):
            t3.install(self.config)
        self.assertEqual(self.binary.resolve(), target)
        self.assertEqual(self.unit.read_text(), content)
        self.assertEqual(self.unit.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertFalse((self.prefix / ".activation").exists())

    def test_failed_initial_install_removes_new_unit_and_does_not_leave_service_enabled(self):
        self.ui.side_effect = RuntimeError("UI fixture failure")
        with self.assertRaises(RuntimeError):
            t3.install(self.config)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.binary.exists())
        self.assertFalse(self.binary.is_symlink())
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)

    def test_rollback_preserves_previously_disabled_inactive_service(self):
        self.legacy(active=False, enabled=False)
        self.ui.side_effect = RuntimeError("UI fixture failure")
        with self.assertRaises(RuntimeError):
            t3.install(self.config)
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)

    def test_keyboard_interrupt_rolls_back_before_propagating(self):
        target, content = self.legacy()
        self.ui.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            t3.install(self.config)
        self.assertEqual(self.binary.resolve(), target)
        self.assertEqual(self.unit.read_text(), content)

    def test_interrupted_recovery_keeps_snapshots_and_retries_on_next_setup(self):
        target, content = self.legacy()
        self.ui.side_effect = RuntimeError("UI fixture failure")
        actual_recover = t3._recover_activation
        def recover(prefix, unit):
            if (prefix / ".activation").exists():
                raise RuntimeError("recovery fixture failure")
            return actual_recover(prefix, unit)
        with patch.object(t3, "_recover_activation", side_effect=recover), \
                self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            t3.install(self.config)
        self.assertTrue((self.prefix / ".activation/state.json").exists())
        self.assertTrue(self.binary.resolve().exists())
        self.assertTrue(target.exists())
        self.failure = "npm"  # Recovery runs before the next candidate is installed.
        with self.assertRaisesRegex(RuntimeError, "npm fixture failure"):
            t3.install(self.config)
        self.assertEqual(self.binary.resolve(), target)
        self.assertEqual(self.unit.read_text(), content)
        self.assertFalse((self.prefix / ".activation").exists())

    def test_outside_binary_link_and_unmanaged_unit_are_preserved(self):
        self.binary.parent.mkdir(parents=True)
        outside = self.home / "personal-t3"
        outside.write_text("keep")
        self.binary.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe T3 runtime"):
            t3.install(self.config)
        self.assertEqual(outside.read_text(), "keep")
        self.unit.write_text("# personal service\n")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            t3.install(self.config)
        self.assertEqual(self.unit.read_text(), "# personal service\n")

    def test_upstream_service_is_refused_without_stopping_it(self):
        upstream = self.unit.with_name("t3code.service")
        upstream.parent.mkdir(parents=True)
        upstream.write_text("# upstream\n")
        with self.assertRaisesRegex(RuntimeError, "original installer"):
            t3.install(self.config)
        self.system_run.assert_not_called()

    def test_failed_recovery_retains_runtime_if_stop_fails(self):
        self.legacy()
        self.ui.side_effect = RuntimeError("UI fixture failure")
        stop_calls = 0
        def command(argv, **kwargs):
            nonlocal stop_calls
            if "stop" in argv:
                stop_calls += 1
                if stop_calls == 2:
                    return subprocess.CompletedProcess(argv, 1, "", "")
            return self.system_command(argv, **kwargs)
        self.system_run.side_effect = command
        with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            t3.install(self.config)
        self.assertTrue(self.binary.resolve().exists())
        self.assertTrue((self.prefix / ".activation/state.json").exists())

    def test_recovery_preserves_external_unit_edits(self):
        self.legacy()
        def fail_and_edit(url):
            self.unit.write_text(t3._MARKER + "\n# human change\n")
            raise RuntimeError("UI fixture failure")
        self.ui.side_effect = fail_and_edit
        with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            t3.install(self.config)
        self.assertIn("human change", self.unit.read_text())
        self.assertTrue(self.binary.resolve().exists())

    def test_recovery_rejects_removed_runtime_or_unit(self):
        for removed in ("unit", "binary"):
            with self.subTest(removed=removed):
                shutil.rmtree(self.prefix, ignore_errors=True)
                shutil.rmtree(self.home / ".config", ignore_errors=True)
                self.unit.unlink(missing_ok=True)
                self.legacy()
                self.ui.side_effect = RuntimeError("UI fixture failure")

                def fail_and_remove(_url):
                    if removed == "unit":
                        self.unit.unlink()
                    else:
                        self.binary.unlink()
                    raise RuntimeError("UI fixture failure")

                self.ui.side_effect = fail_and_remove
                with self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
                    t3.install(self.config)
                self.assertTrue((self.prefix / ".activation/state.json").exists())

    def test_unmanaged_recovery_directory_is_preserved(self):
        transaction = self.prefix / ".activation"
        transaction.mkdir(parents=True)
        (transaction / "personal.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Unmanaged T3 .activation"):
            t3.install(self.config)
        self.assertEqual((transaction / "personal.txt").read_text(), "keep")

    def test_runtime_version_and_native_failure_prevent_activation(self):
        self.prefix.mkdir(parents=True)
        self.runtime(self.prefix)
        with patch.object(t3, "_user_run", return_value=subprocess.CompletedProcess([], 0, "../bad\n", "")), \
                self.assertRaisesRegex(RuntimeError, "valid version"):
            t3._check_runtime(self.prefix, self.home)

    def test_private_lan_url_and_unicode_paths(self):
        self.config.web_interface_host = "192.168.1.50"
        self.config.agent_workspace = str(self.home / "é work%")
        t3.install(self.config)
        self.assertIn("--host 192.168.1.50", self.unit.read_text())
        self.assertIn("é\\x20work%%", self.unit.read_text())
        self.ui.assert_called_once_with("http://192.168.1.50:3773/")
        self.assertEqual(t3._unit_quote("PATH=/home/é/%"), '"PATH=/home/é/%%"')

    def test_setup_lock_refuses_concurrent_install(self):
        self.prefix.mkdir(parents=True)
        with t3._setup_lock(self.prefix), self.assertRaisesRegex(RuntimeError, "Another CachyOS T3"):
            t3.install(self.config)
        self.assertFalse(any(cmd[:2] == ["npm", "install"] for _, cmd in self.events))


class T3UITests(unittest.TestCase):
    def test_ui_check_requires_consecutive_direct_successes_and_labels_its_limit(self):
        responses = []
        for status, url in [(200, "http://127.0.0.1:3773/"),
                            (202, "http://127.0.0.1:3773/"),
                            (200, "http://example.com/"),
                            (200, "http://127.0.0.1:3773/pair"),
                            *[(200, "http://127.0.0.1:3773/")] * 2]:
            item = MagicMock()
            item.__enter__.return_value.status = status
            item.__enter__.return_value.geturl.return_value = url
            responses.append(item)
        with patch.object(t3, "run", return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(t3.urllib.request, "build_opener") as opener, \
                patch.object(t3.time, "sleep"), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            opener.return_value.open.side_effect = responses
            t3._wait_for_ui("http://127.0.0.1:3773/")
        self.assertEqual(opener.call_args.args[0].proxies, {})
        self.assertEqual(opener.return_value.open.call_count, 6)
        self.assertIn("still require a client test", output.getvalue())
        self.assertNotIn("Code ready", output.getvalue())


if __name__ == "__main__":
    unittest.main()
