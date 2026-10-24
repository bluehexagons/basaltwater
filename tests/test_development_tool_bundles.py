"""Opt-in development package bundles retain the normal setup persistence path."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import io
import json
import shlex
import subprocess
import unittest
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib import reconstruct


BUNDLES = (
    ("data-analysis", "install_data_analysis_tools", "Python, Data analysis",
     reconstruct.DATA_ANALYSIS_MARKER_PACKAGES),
    ("av-tools", "install_av_tools", "AV tools", reconstruct.AV_TOOL_MARKER_PACKAGES),
    ("gl-tools", "install_gl_tools", "OpenGL tools", reconstruct.GL_TOOL_MARKER_PACKAGES),
)


class OptionalBundleTests(unittest.TestCase):
    def config(self, **options):
        return SetupConfig(host="example.com", username="agent", system_type="agent_vm", **options)

    def test_parser_preserves_omitted_selected_and_disabled_states(self):
        parser = create_setup_argument_parser("test")
        for flag, field, _label, _markers in BUNDLES:
            for options, expected in (([], None), ([f"--{flag}"], True), ([f"--no-{flag}"], False)):
                with self.subTest(flag=flag, options=options):
                    self.assertIs(getattr(parser.parse_args(["example.com", *options]), field), expected)

    def test_saved_selection_roundtrips_and_normalizes_omitted_state(self):
        for _flag, field, _label, _markers in BUNDLES:
            for selected in (None, False, True):
                with self.subTest(field=field, selected=selected):
                    config = self.config(**{field: selected})
                    saved = config.to_dict()
                    self.assertIs(saved[field], bool(selected))
                    restored = SetupConfig.from_dict(config.host, config.system_type, saved)
                    self.assertIs(getattr(restored, field), bool(selected))
                    if field == "install_data_analysis_tools" and selected:
                        self.assertTrue(restored.install_python)

    def test_patch_merge_distinguishes_omitted_and_disabled_selection(self):
        for _flag, field, _label, _markers in BUNDLES:
            cached = self.config(**{field: True})
            for selected, expected in ((None, True), (False, False)):
                with self.subTest(field=field, selected=selected):
                    update = self.config(install_python=None, **{field: selected})
                    self.assertIs(getattr(merge_setup_configs(cached, update), field), expected)

    @patch("basaltwater.get_all_configs")
    def test_info_displays_selected_bundles(self, configs):
        for _flag, field, label, _markers in BUNDLES:
            with self.subTest(field=field), redirect_stdout(io.StringIO()) as output:
                configs.return_value = [{"host": "192.0.2.10", "system_type": "agent_vm",
                                         "args": {"username": "agent", field: True,
                                                  "install_python": field == "install_data_analysis_tools"}}]
                self.assertEqual(basaltwater.show_info(), 0)
                self.assertIn(f"Features: {label}", output.getvalue())

    def test_reconstruction_detects_each_bundles_markers_and_reports_selection(self):
        for _flag, field, _label, markers in BUNDLES:
            with self.subTest(field=field), ExitStack() as stack:
                for name in ("smb_mounts", "scrub_operations", "sync_operations", "deployments"):
                    stack.enter_context(patch.object(reconstruct, f"detect_{name}", return_value=[]))
                for name in ("samba", "python", "node", "go", "data_analysis_tools", "av_tools", "gl_tools"):
                    if f"install_{name}" != field:
                        stack.enter_context(patch.object(reconstruct, f"detect_{name}", return_value=False))
                packages = stack.enter_context(patch.object(reconstruct, "check_package_installed", return_value=True))
                config, _extras = reconstruct.reconstruct_configuration(host="example.com", username="agent")
                self.assertTrue(getattr(config, field))
                if field == "install_data_analysis_tools":
                    self.assertTrue(config.install_python)
                self.assertEqual([call.args[0] for call in packages.call_args_list], [*markers, "musescore3"])
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(reconstruct.run_reconstruct_command(compact=True), 0)
                self.assertTrue(json.loads(output.getvalue())[field])

    @patch("lib.reconstruct.subprocess.run")
    def test_package_detection_uses_validated_dpkg_query(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "install ok installed", "")
        self.assertTrue(reconstruct.check_package_installed("python3-pandas"))
        run.assert_called_once_with(["dpkg-query", "-W", "-f=${Status}", "python3-pandas"],
                                    capture_output=True, text=True, timeout=5)
        run.reset_mock()
        self.assertFalse(reconstruct.check_package_installed("pandas; touch /tmp/unexpected"))
        run.assert_not_called()


class DevelopmentToolBundleTests(unittest.TestCase):
    def test_bundles_preserve_explicit_packages_and_deduplicate(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args([
            "vm.example", "agent", "--apt-install", "podman",
            "--container-tools", "--debug-tools", "--container-tools",
            "--apt-install", "linux-perf",
        ])
        self.assertEqual(args.apt_packages, [
            "podman", "uidmap", "passt", "slirp4netns", "fuse-overlayfs",
            "gdb", "strace", "valgrind", "ccache", "ninja-build", "linux-perf",
        ])
        self.assertIsNone(parser.parse_args(["vm.example", "agent"]).apt_packages)

    def test_remote_arguments_preserve_the_expanded_package_selection(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["vm.example", "agent", "--container-tools"])
        config = SetupConfig(
            system_type="agent_code_vm", host="vm.example", username="agent",
            apt_packages=args.apt_packages,
        )
        remote = create_setup_argument_parser("remote", for_remote=True)
        restored = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
        self.assertEqual(restored.apt_packages, args.apt_packages)
        self.assertTrue(all(f"--apt-install {package}" in config.to_setup_command()
                            for package in args.apt_packages))

    def test_bundles_are_optional_and_available_in_remote_setup(self):
        remote = create_setup_argument_parser("remote", for_remote=True)
        self.assertIsNone(remote.parse_args([]).apt_packages)
        self.assertEqual(remote.parse_args(["--debug-tools"]).apt_packages,
                         ["gdb", "strace", "valgrind", "ccache", "ninja-build"])
