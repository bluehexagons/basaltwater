"""Tests for Go runtime setup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import common.common_steps as common_steps
from lib.config import SetupConfig


def _make_config(*, refresh_packages: bool = False) -> SetupConfig:
    return SetupConfig(
        host="testhost",
        username="testuser",
        system_type="server_lite",
        refresh_packages=refresh_packages,
    )


class TestInstallGo(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"BASALTWATER_GO_VERSION": ""})
        environment.start()
        self.addCleanup(environment.stop)
        machine = patch("common.common_steps.platform.machine", return_value="x86_64")
        machine.start()
        self.addCleanup(machine.stop)

    @staticmethod
    def _release_feed(arch: str = "amd64") -> str:
        return json.dumps([{
            "version": "go1.27.3",
            "stable": True,
            "files": [{
                "filename": f"go1.27.3.linux-{arch}.tar.gz",
                "os": "linux",
                "arch": arch,
                "kind": "archive",
                "sha256": "a" * 64,
            }],
        }])

    def test_go_release_arch_maps_supported_machines(self):
        self.assertEqual(common_steps._go_release_arch("x86_64"), "amd64")
        self.assertEqual(common_steps._go_release_arch("aarch64"), "arm64")
        self.assertEqual(common_steps._go_release_arch("armv7l"), "armv6l")
        self.assertEqual(common_steps._go_release_arch("riscv64"), "riscv64")
        self.assertIsNone(common_steps._go_release_arch("mips64"))

    def test_release_feed_rejects_unsafe_archive_metadata(self):
        base_file = {
            "os": "linux",
            "arch": "amd64",
            "kind": "archive",
            "sha256": "a" * 64,
        }
        for override in (
            {"filename": "../go.tar.gz"},
            {"filename": "go.tar.gz", "sha256": "not-a-digest"},
        ):
            payload = [{
                "version": "go1.27.3",
                "stable": True,
                "files": [{**base_file, **override}],
            }]
            with self.subTest(override=override), self.assertRaisesRegex(
                RuntimeError,
                "No supported Linux/amd64 Go archive found",
            ):
                common_steps._select_go_download(payload, "amd64", None)

    def test_module_minimum_selects_latest_stable_across_minor_lines(self):
        latest = json.loads(self._release_feed())[0]
        older = {**latest, "version": "go1.26.10"}
        preview = {**latest, "version": "go1.28rc1", "stable": False}
        for minimum in (None, "1.25.0", "1.26.10", "1.27.0", "1.27.3"):
            with self.subTest(minimum=minimum):
                self.assertEqual(common_steps._select_go_download(
                    [older, preview, latest], "amd64", minimum,
                ), ("go1.27.3", "go1.27.3.linux-amd64.tar.gz", "a" * 64))
        for minimum in ("1.27.4", "1.28.0"):
            with self.subTest(minimum=minimum), self.assertRaisesRegex(RuntimeError, "No supported"):
                common_steps._select_go_download([older, latest], "amd64", minimum)

    def test_latest_release_without_valid_native_archive_never_falls_back(self):
        latest = json.loads(self._release_feed("arm64"))[0]
        older = json.loads(self._release_feed())[0]
        older["version"] = "go1.26.10"
        with self.assertRaisesRegex(RuntimeError, "No supported"):
            common_steps._select_go_download([older, latest], "amd64", "1.25.0")

    def test_invalid_go_minimum_fails_without_downloading(self):
        payload = json.loads(self._release_feed())
        for minimum in ("1.27", "go1.27.3", "1.27.3rc1", "1.-1.0", "1.27.3\n"):
            with self.subTest(minimum=minimum), self.assertRaisesRegex(RuntimeError, "Invalid requested"):
                common_steps._select_go_download(payload, "amd64", minimum)

    def test_environment_minimum_reuses_latest_instead_of_requesting_old_line(self):
        def fake_run(command, **kwargs):
            if "mode=json" in command:
                return subprocess.CompletedProcess(command, 0, self._release_feed(), "")
            return subprocess.CompletedProcess(command, 0, "go version go1.27.3 linux/amd64", "")

        with patch.dict(os.environ, {"BASALTWATER_GO_VERSION": "1.25.0"}), \
                patch("common.common_steps.os.path.exists", return_value=True), \
                patch("common.common_steps.run", side_effect=fake_run) as run:
            common_steps.install_go(_make_config())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("mode=json" in command for command in commands))
        self.assertFalse(any(command.startswith("wget ") or command.startswith("rm ") for command in commands))

    def test_feed_failure_on_ordinary_rerun_preserves_existing_runtime(self):
        with patch("common.common_steps.os.path.exists", return_value=True), \
                patch("common.common_steps.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            with self.assertRaisesRegex(RuntimeError, "Failed to retrieve"):
                common_steps.install_go(_make_config())
        self.assertFalse(any(call.args[0].startswith(("wget ", "rm ", "tar ")) for call in run.call_args_list))

    def test_skips_reinstall_when_usr_local_go_is_current(self):
        commands: list[str] = []

        def fake_run(command: str, **kwargs):
            commands.append(command)
            if "mode=json" in command:
                return subprocess.CompletedProcess(command, 0, stdout=self._release_feed(), stderr="")
            if command == "/usr/local/go/bin/go version":
                return subprocess.CompletedProcess(command, 0, stdout="go version go1.27.3 linux/amd64\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("common.common_steps.os.path.exists", return_value=True), \
             patch("common.common_steps.run", side_effect=fake_run):
            common_steps.install_go(_make_config())

        self.assertTrue(any("?mode=json'" in command for command in commands))
        self.assertFalse(any("include=all" in command for command in commands))
        self.assertNotIn("rm -rf /usr/local/go", commands)
        self.assertFalse(any(command.startswith("wget ") for command in commands))

    def test_reinstalls_when_usr_local_go_is_outdated(self):
        commands: list[str] = []

        def fake_run(command: str, **kwargs):
            commands.append(command)
            if "mode=json" in command:
                return subprocess.CompletedProcess(command, 0, stdout=self._release_feed(), stderr="")
            if command == "/usr/local/go/bin/go version":
                return subprocess.CompletedProcess(command, 0, stdout="go version go1.21.0 linux/amd64\n", stderr="")
            if command.startswith("sha256sum "):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=f"{'a' * 64}  archive\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("common.common_steps.os.path.exists", return_value=True), \
             patch("common.common_steps.run", side_effect=fake_run), \
             patch("common.common_steps.open", mock_open()), \
             patch("common.common_steps.shutil.which", return_value=None):
            common_steps.install_go(_make_config())

        self.assertIn("rm -rf /usr/local/go", commands)
        self.assertTrue(any("https://go.dev/dl/go1.27.3" in command for command in commands))
        download_command = next(command for command in commands if command.startswith("wget -q "))
        self.assertIn("--https-only", download_command)
        self.assertIn("/basaltwater-go-release-", download_command)
        self.assertNotIn("-O /tmp/go1.27.3", download_command)

    def test_reinstalls_with_native_arm64_archive(self):
        commands: list[str] = []

        def fake_run(command: str, **kwargs):
            commands.append(command)
            if "mode=json" in command:
                return subprocess.CompletedProcess(command, 0, stdout=self._release_feed("arm64"), stderr="")
            if command == "/usr/local/go/bin/go version":
                return subprocess.CompletedProcess(command, 0, stdout="go version go1.21.0 linux/amd64\n", stderr="")
            if command.startswith("sha256sum "):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=f"{'a' * 64}  archive\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("common.common_steps.platform.machine", return_value="aarch64"), \
             patch("common.common_steps.os.path.exists", return_value=True), \
             patch("common.common_steps.run", side_effect=fake_run), \
             patch("common.common_steps.open", mock_open()), \
             patch("common.common_steps.shutil.which", return_value=None):
            common_steps.install_go(_make_config(refresh_packages=True))

        self.assertTrue(any("go1.27.3.linux-arm64.tar.gz" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
