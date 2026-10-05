"""CachyOS T3 activation tests with temporary files and mocked host commands."""

from __future__ import annotations

from contextlib import ExitStack
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common import cachyos_t3 as t3
from common import cachyos_aur as aur
from lib.config import SetupConfig


class T3InstallTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"XDG_CACHE_HOME": ""}))
        self.prefix = self.home / ".local/share/basaltwater/cachyos-t3"
        self.binary = self.prefix / "bin/t3"
        self.unit = self.home / ".config/systemd/user" / t3.T3_SERVICE
        self.config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos",
                                  agent_tools=["codex"], web_interfaces=["t3code"])
        self.events = []
        self.active = False
        self.enabled = False
        self.failure = None
        self.desktop_version = "0.0.42-1"
        stack.enter_context(patch.object(t3, "_home", return_value=self.home))
        self.user_run = stack.enter_context(patch.object(t3, "_user_run", side_effect=self.user_command))
        self.system_run = stack.enter_context(patch.object(t3, "run", side_effect=self.system_command))
        stack.enter_context(patch.object(aur, "run", self.system_run))
        self.ui = stack.enter_context(patch.object(t3, "_wait_for_ui"))
        self.which = stack.enter_context(patch.object(t3.shutil, "which", side_effect=lambda name, **kw: "/usr/bin/" + name))

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
        if argv[:3] == ["systemctl", "--user", "show"]:
            present = argv[3] == t3.T3_SERVICE and self.unit.exists()
            output = ("LoadState=loaded\nFragmentPath=" + str(self.unit) + "\n" if present else
                      "LoadState=not-found\nFragmentPath=\n")
            output += "DropInPaths=\nActiveState=" + ("active" if present and self.active else "inactive") + "\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        if argv[:2] == ["pacman", "-Q"]:
            return subprocess.CompletedProcess(argv, 0 if self.desktop_version else 1,
                                               f"t3code-bin {self.desktop_version}\n" if self.desktop_version else "", "")
        if argv[:2] == ["pacman", "-Qqo"]:
            package = Path(argv[-1]).name if Path(argv[-1]).name in {"shelly", "paru", "yay"} else "t3code-bin"
            return subprocess.CompletedProcess(argv, 0, package + "\n", "")
        if argv[0] in {"/usr/bin/shelly", "/usr/bin/paru", "/usr/bin/yay"}:
            if argv[0] == "/usr/bin/shelly":
                cache = self.home / ".cache/Shelly"
                self.assertTrue(cache.is_dir(), "Cache must exist before Shelly elevates")
                self.assertEqual(cache.stat().st_uid, os.getuid())
            if self.failure == "aur":
                raise aur.CommandExecutionError(" ".join(argv), 1, "AUR fixture failure")
            if self.failure != "aur-cancel":
                self.desktop_version = "0.0.42-1"
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
            if "--now" in argv:
                self.active = False
        output = ("active\n" if self.active else "inactive\n") if "is-active" in argv else ""
        if "is-enabled" in argv:
            output = "enabled\n" if self.enabled else "disabled\n"
        return subprocess.CompletedProcess(argv, code, output, "")

    def test_initial_install_validates_before_activation_and_keeps_cli_path(self):
        t3.install(self.config)
        self.assertTrue(self.binary.resolve().is_relative_to(self.prefix / "releases"))
        content = self.unit.read_text()
        self.assertIn("UMask=0077", content)
        self.assertIn("WorkingDirectory=" + str(self.home / "repos"), content)
        self.assertNotIn('WorkingDirectory="', content)
        self.assertIn("--host 127.0.0.1 --port 3773", content)
        self.assertIn('--base-dir "' + str(self.prefix / "data") + '"', content)
        self.assertIn("UnsetEnvironment=T3CODE_STATE_DIR", content)
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

    def desktop_config(self):
        return SetupConfig(host="localhost", username="human", system_type="agent_cachyos",
                           agent_tools=["codex"], t3code_desktop=True)

    def test_desktop_retains_installed_package_without_launching_or_downloading(self):
        t3.install_desktop(self.desktop_config())
        self.assertTrue((self.prefix / "desktop-mode").is_file())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.binary.exists())
        self.assertFalse(any(cmd[0] in {"npm", "/usr/bin/shelly", "/usr/bin/paru", "/usr/bin/yay",
                                       "/usr/bin/t3code"} for _, cmd in self.events))

    def test_desktop_state_is_private_before_first_launch(self):
        t3.install_desktop(self.desktop_config())
        for path in (self.home / ".t3", self.home / ".t3/userdata"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.home / ".t3/userdata/clerk-tokens.json").exists())

    def test_existing_credentials_are_protected_without_reading_or_replacing_data(self):
        userdata = self.home / ".t3/userdata"
        userdata.mkdir(parents=True)
        token = userdata / "clerk-tokens.json"
        token.write_text("credential fixture")
        token.chmod(0o666)
        database = userdata / "state.sqlite"
        database.write_text("history fixture")
        database.chmod(0o644)
        inode = token.stat().st_ino
        actual_read = Path.read_text

        def read(path, *args, **kwargs):
            self.assertNotIn(path, (token, database))
            return actual_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            t3.install_desktop(self.desktop_config())
        self.assertEqual(token.stat().st_ino, inode)
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        self.assertEqual(token.read_text(), "credential fixture")
        self.assertEqual(database.read_text(), "history fixture")
        self.assertEqual(database.stat().st_mode & 0o777, 0o644)

    def test_unsafe_desktop_state_stops_before_package_install_or_permission_changes(self):
        self.desktop_version = None
        root = self.home / ".t3"
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        outside = self.home / "personal"
        outside.mkdir(mode=0o755)
        userdata = root / "userdata"
        userdata.symlink_to(outside)
        for operation in (t3.preflight, t3.install_desktop):
            with self.assertRaisesRegex(ValueError, "owned directories"):
                operation(self.desktop_config())
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)
        self.assertTrue(userdata.is_symlink())
        self.assertFalse(any(cmd[0] == "/usr/bin/shelly" for _, cmd in self.events))
        userdata.unlink()
        userdata.mkdir()
        token = userdata / "clerk-tokens.json"
        token.symlink_to(outside / "missing-token")
        with self.assertRaises(ValueError):
            t3._protect_desktop_data(self.home)
        self.assertTrue(token.is_symlink())

    def test_foreign_owned_desktop_state_is_refused(self):
        with patch.object(t3.os, "getuid", return_value=os.getuid() + 1), self.assertRaises(ValueError):
            t3._protect_desktop_data(self.home)
        self.assertFalse((self.home / ".t3").exists())

    def test_missing_desktop_prefers_shelly_with_review_prompts(self):
        self.desktop_version = None
        t3.install_desktop(self.desktop_config())
        calls = [call for call in self.system_run.call_args_list
                 if call.args[0][0] in {"/usr/bin/shelly", "/usr/bin/paru", "/usr/bin/yay"}]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], ["/usr/bin/shelly", "install", "aur", "t3code-bin"])
        self.assertTrue(calls[0].kwargs["interactive"])

    def test_current_cachyos_with_only_shelly_passes_preflight_without_mutation(self):
        self.desktop_version = None
        self.which.side_effect = lambda name, **kw: "/usr/bin/shelly" if name == "shelly" else None
        t3.preflight(self.desktop_config())
        self.assertFalse(self.prefix.exists())
        self.assertFalse((self.home / ".cache").exists())
        self.assertTrue(all(cmd[:2] in (["pacman", "-Q"], ["pacman", "-Qqo"])
                            or cmd[:3] == ["systemctl", "--user", "show"]
                            for _, cmd in self.events))

    def test_shelly_cache_preserves_existing_files_and_permissions(self):
        cache = self.home / ".cache/Shelly"
        cache.mkdir(parents=True, mode=0o700)
        keep = cache / "existing-build"
        keep.write_text("keep")
        self.desktop_version = None
        t3.install_desktop(self.desktop_config())
        self.assertEqual(keep.read_text(), "keep")
        self.assertEqual(cache.stat().st_mode & 0o777, 0o700)

    def test_shelly_checks_and_prepares_xdg_and_default_cache(self):
        configured = self.home / "custom-cache"
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(configured)}):
            aur.prepare_cache(self.home)
            self.assertFalse(configured.exists())
            aur.prepare_cache(self.home, create=True)
        self.assertTrue((configured / "Shelly").is_dir())
        self.assertTrue((self.home / ".cache/Shelly").is_dir())

    def test_shelly_ignores_relative_xdg_cache_like_upstream(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": "relative-cache"}):
            aur.prepare_cache(self.home, create=True)
        self.assertTrue((self.home / ".cache/Shelly").is_dir())
        self.assertFalse((self.home / "relative-cache").exists())

    def test_bad_shelly_cache_stops_preflight_before_install_or_web_changes(self):
        self.legacy()
        self.desktop_version = None
        cache = self.home / ".cache/Shelly"
        cache.mkdir(parents=True)
        # Model a cache owned by another UID without changing real ownership.
        actual_stat = Path.stat

        def cache_stat(path, *args, **kwargs):
            info = actual_stat(path, *args, **kwargs)
            if path == cache:
                fields = list(info)
                fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        with patch.object(Path, "stat", cache_stat), \
                self.assertRaisesRegex(RuntimeError, "root-owned Shelly cache"):
            t3.preflight(self.desktop_config())
        with patch.object(t3.os, "access", return_value=False), \
                self.assertRaisesRegex(RuntimeError, "ls -ld"):
            t3.install_desktop(self.desktop_config())
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertFalse(any(cmd[0] == "/usr/bin/shelly" for _, cmd in self.events))

    def test_shelly_cache_rejects_symlinks_and_files_without_replacing_them(self):
        self.desktop_version = None
        root = self.home / ".cache"
        root.mkdir()
        cache = root / "Shelly"
        outside = self.home / "personal"
        outside.mkdir()
        cache.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Unsafe Shelly"):
            t3.preflight(self.desktop_config())
        self.assertTrue(cache.is_symlink())
        self.assertEqual(list(outside.iterdir()), [])
        cache.unlink()
        cache.write_text("keep")
        with self.assertRaisesRegex(ValueError, "Unsafe Shelly"):
            t3.install_desktop(self.desktop_config())
        self.assertEqual(cache.read_text(), "keep")

    def test_shelly_reports_unsearchable_parent_before_inspecting_children(self):
        root = self.home / ".cache"
        root.mkdir()
        with patch.object(t3.os, "access", side_effect=lambda path, mode: path != root), \
                self.assertRaisesRegex(RuntimeError, str(root)):
            aur.prepare_cache(self.home, create=True)
        self.assertFalse((root / "Shelly").exists())

    def test_missing_shelly_uses_available_legacy_helper(self):
        for available, expected in (({"paru", "yay"}, "paru"), ({"yay"}, "yay")):
            with self.subTest(available=available):
                self.desktop_version = None
                self.events.clear()
                self.which.side_effect = lambda name, **kw: (
                    "/usr/bin/" + name if name in available | {"t3code", "codex"} else None
                )
                t3.install_desktop(self.desktop_config())
                self.assertIn(("system", ["/usr/bin/" + expected, "-S", "--aur", "--needed", "--", "t3code-bin"]),
                              self.events)
                self.assertFalse((self.home / ".cache/Shelly").exists())

    def test_installed_desktop_needs_no_aur_helper(self):
        self.which.side_effect = lambda name, **kw: "/usr/bin/" + name if name in {"t3code", "codex"} else None
        t3.preflight(self.desktop_config())
        t3.install_desktop(self.desktop_config())
        self.assertTrue((self.prefix / "desktop-mode").is_file())
        self.assertFalse((self.home / ".cache/Shelly").exists())

    def test_missing_helper_fails_preflight_without_mutation(self):
        self.desktop_version = None
        self.which.return_value = None
        self.which.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "sudo pacman -S --needed shelly"):
            t3.preflight(self.desktop_config())
        self.assertFalse(self.prefix.exists())
        self.assertTrue(all(cmd[:2] == ["pacman", "-Q"] or cmd[:3] == ["systemctl", "--user", "show"]
                            for _, cmd in self.events))

    def test_failed_desktop_install_leaves_web_service_and_data_untouched(self):
        target, content = self.legacy()
        self.desktop_version = None
        self.failure = "aur"
        with self.assertRaisesRegex(RuntimeError, "AUR fixture") as raised:
            t3.install_desktop(self.desktop_config())
        self.assertIn("exit code 1", str(raised.exception))
        self.assertIn("shelly config get AurUrl", str(raised.exception))
        self.assertIn("shelly.log", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, aur.CommandExecutionError)
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertEqual(self.unit.read_text(), content)
        self.assertEqual(self.binary.resolve(), target)
        self.assertFalse((self.prefix / "desktop-mode").exists())
        self.assertFalse(any(cmd[0] in {"/usr/bin/paru", "/usr/bin/yay"} for _, cmd in self.events))

    def test_cancelled_desktop_install_does_not_disable_web_service(self):
        target, content = self.legacy()
        self.desktop_version = None
        self.failure = "aur-cancel"
        with self.assertRaisesRegex(RuntimeError, "without t3code-bin"):
            t3.install_desktop(self.desktop_config())
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertEqual(self.unit.read_text(), content)
        self.assertEqual(self.binary.resolve(), target)
        self.assertFalse((self.prefix / "desktop-mode").exists())

    def test_web_desktop_web_switches_preserve_both_data_directories(self):
        desktop_data = self.home / ".t3/userdata/state.sqlite"
        desktop_data.parent.mkdir(parents=True)
        desktop_data.write_text("desktop data")
        t3.install(self.config)
        web_data = self.prefix / "data/userdata/state.sqlite"
        web_data.parent.mkdir(parents=True)
        web_data.write_text("web data")
        t3.install_desktop(self.desktop_config())
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        t3.install_desktop(self.desktop_config())
        self.assertFalse(self.active)
        t3.install(self.config)
        self.assertTrue(self.active)
        self.assertTrue(self.enabled)
        self.assertFalse((self.prefix / "desktop-mode").exists())
        self.assertEqual(desktop_data.read_text(), "desktop data")
        self.assertEqual(web_data.read_text(), "web data")

    def test_failed_web_switch_restores_desktop_selection(self):
        t3.install(self.config)
        t3.install_desktop(self.desktop_config())
        self.ui.side_effect = RuntimeError("startup failure")
        with self.assertRaisesRegex(RuntimeError, "startup failure"):
            t3.install(self.config)
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        self.assertTrue((self.prefix / "desktop-mode").is_file())

    def test_desktop_recovers_interrupted_web_activation_without_restarting_it(self):
        target, content = self.legacy()
        self.ui.side_effect = RuntimeError("UI fixture failure")
        actual_recover = t3._recover_activation

        def interrupt_recovery(prefix, unit):
            if (prefix / ".activation").exists():
                raise RuntimeError("interrupted recovery")
            return actual_recover(prefix, unit)

        with patch.object(t3, "_recover_activation", side_effect=interrupt_recovery), \
                self.assertRaisesRegex(RuntimeError, "recovery is incomplete"):
            t3.install(self.config)
        self.events.clear()
        t3.install_desktop(self.desktop_config())
        self.assertEqual(self.binary.resolve(), target)
        self.assertEqual(self.unit.read_text(), content)
        self.assertFalse(self.active)
        self.assertFalse(self.enabled)
        self.assertFalse(any("start" in cmd for _, cmd in self.events))
        self.assertFalse((self.prefix / ".activation").exists())
        self.assertTrue((self.prefix / "desktop-mode").is_file())

    def test_desktop_preserves_unmanaged_unit_and_marker(self):
        for path in (self.unit, self.prefix / "desktop-mode"):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("personal content")
                with self.assertRaisesRegex(ValueError, "unmanaged"):
                    t3.install_desktop(self.desktop_config())
                self.assertEqual(path.read_text(), "personal content")
                path.unlink()
        self.system_run.assert_not_called()

    def test_effective_upstream_service_outside_home_is_never_adopted(self):
        self.system_run.return_value = subprocess.CompletedProcess([], 0,
            "LoadState=loaded\nFragmentPath=/usr/lib/systemd/user/t3code.service\nDropInPaths=\nActiveState=inactive\n", "")
        self.system_run.side_effect = None
        for config in (self.config, self.desktop_config()):
            with self.assertRaisesRegex(RuntimeError, "original installer"):
                t3.preflight(config)
        self.assertFalse(self.prefix.exists())
        self.assertTrue(all(call.args[0][:3] == ["systemctl", "--user", "show"]
                            for call in self.system_run.call_args_list))

    def test_unmanaged_overrides_fail_before_runtime_or_package_install(self):
        self.legacy()
        actual = self.system_command
        for replacement in (
            "LoadState=masked\nFragmentPath=/dev/null\nDropInPaths=\nActiveState=inactive\n",
            f"LoadState=loaded\nFragmentPath={self.unit}\nDropInPaths=/etc/systemd/user/service.d/override.conf\nActiveState=active\n",
        ):
            def command(argv, **kwargs):
                if argv[:4] == ["systemctl", "--user", "show", t3.T3_SERVICE]:
                    return subprocess.CompletedProcess(argv, 0, replacement, "")
                return actual(argv, **kwargs)
            self.system_run.side_effect = command
            for install, config in ((t3.install, self.config), (t3.install_desktop, self.desktop_config())):
                with self.assertRaisesRegex(RuntimeError, "overridden"):
                    install(config)
            self.assertTrue(self.active)
        self.user_run.assert_not_called()

    def test_unloaded_local_dropin_is_preserved_and_rejected(self):
        dropin = self.unit.with_name(t3.T3_SERVICE + ".d") / "override.conf"
        dropin.parent.mkdir(parents=True)
        dropin.write_text("[Service]\nEnvironment=T3CODE_STATE_DIR=/personal\n")
        with self.assertRaisesRegex(RuntimeError, "drop-ins"):
            t3.install(self.config)
        self.assertTrue(dropin.is_file())
        self.system_run.assert_not_called()

    def test_desktop_does_not_record_success_if_service_did_not_stop(self):
        self.legacy()
        def command(argv, **kwargs):
            if "disable" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return self.system_command(argv, **kwargs)
        self.system_run.side_effect = command
        with self.assertRaisesRegex(RuntimeError, "service stopped"):
            t3.install_desktop(self.desktop_config())
        self.assertFalse((self.prefix / "desktop-mode").exists())

    def test_exec_arguments_escape_systemd_environment_expansion(self):
        self.assertEqual(t3._unit_exec_quote("/home/a$USER/%/t3"), '"/home/a$$USER/%%/t3"')


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
