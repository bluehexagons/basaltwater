"""Opt-in development package bundles retain the normal setup persistence path."""

from __future__ import annotations

import shlex
import unittest

from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig


class DevelopmentToolBundleTests(unittest.TestCase):
    def test_bundles_preserve_explicit_packages_and_deduplicate(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args([
            "vm.example", "agent", "--apt-install", "podman",
            "--container-tools", "--debug-tools", "--container-tools",
            "--apt-install", "linux-perf",
        ])
        self.assertEqual(args.apt_packages, [
            "podman", "uidmap", "slirp4netns", "fuse-overlayfs",
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
