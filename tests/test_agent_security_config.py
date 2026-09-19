"""Tests for coding-agent security options and propagation."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from basaltwater import _patch_preserve_keys
from lib.arg_parser import create_setup_argument_parser
from lib.cache import merge_setup_configs
from lib.config import SetupConfig


class TestAgentSecurityConfig(unittest.TestCase):
    def test_security_flags_propagate_to_remote_setup(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
            nopasswd=True,
        )

        self.assertIn("--nopasswd", config.to_remote_args())
        self.assertIn("--nopasswd", config.to_setup_command())

    def test_harden_agent_propagates_to_remote_setup(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            harden_agent=True,
        )

        self.assertIn("--harden-agent", config.to_remote_args())
        self.assertIn("--harden-agent", config.to_setup_command())

    def test_harden_user_implies_agent_hardening_and_propagates(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            harden_user=True,
        )

        self.assertTrue(config.harden_agent)
        self.assertIn("--harden-user", config.to_remote_args())
        self.assertNotIn("--harden-agent", config.to_remote_args())
        self.assertIn("--harden-user", config.to_setup_command())

    def test_nopasswd_and_hardening_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                nopasswd=True,
                harden_agent=True,
            )

    def test_hardening_rejects_root_identity(self):
        with self.assertRaisesRegex(ValueError, "non-root setup user"):
            SetupConfig(
                host="testhost",
                username="root",
                system_type="agent_vm",
                harden_agent=True,
            )

    def test_harden_user_rejects_rdp(self):
        with self.assertRaisesRegex(ValueError, "cannot be combined with --rdp"):
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                harden_user=True,
                enable_rdp=True,
            )

    def test_parser_accepts_security_flags(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["testhost", "agent", "--nopasswd"])

        self.assertTrue(args.nopasswd)
        self.assertIsNone(args.harden_agent)
        self.assertIsNone(args.harden_user)

    def test_parser_distinguishes_omitted_and_disabled_nopasswd(self):
        parser = create_setup_argument_parser("test")

        omitted = parser.parse_args(["testhost", "agent"])
        disabled = parser.parse_args(["testhost", "agent", "--no-nopasswd"])

        self.assertIsNone(omitted.nopasswd)
        self.assertFalse(disabled.nopasswd)

    def test_parser_accepts_harden_user(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["testhost", "agent", "--harden-user"])

        self.assertTrue(args.harden_user)

    def test_parser_can_explicitly_remove_hardening(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            ["testhost", "agent", "--no-harden-user", "--no-harden-agent"]
        )

        self.assertFalse(args.harden_user)
        self.assertFalse(args.harden_agent)

    def test_patch_merge_preserves_omitted_hardening_and_allows_removal(self):
        cached = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            harden_user=True,
        )
        omitted = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            harden_agent=None,
            harden_user=None,
        )

        preserved = merge_setup_configs(cached, omitted)

        self.assertTrue(preserved.harden_user)
        self.assertTrue(preserved.harden_agent)

        removed = merge_setup_configs(
            preserved,
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                harden_agent=False,
                harden_user=False,
            ),
        )
        self.assertFalse(removed.harden_user)
        self.assertFalse(removed.harden_agent)

    def test_patch_merge_preserves_omitted_nopasswd_and_allows_removal(self):
        parser = create_setup_argument_parser("test")
        cached = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            nopasswd=True,
        )

        omitted_args = parser.parse_args(["testhost", "agent"])
        omitted = SetupConfig.from_args(omitted_args, "agent_vm")
        preserved = merge_setup_configs(
            cached,
            omitted,
            preserve_keys=_patch_preserve_keys(omitted_args),
        )

        self.assertTrue(preserved.nopasswd)

        disabled_args = parser.parse_args(
            ["testhost", "agent", "--no-nopasswd"]
        )
        disabled = SetupConfig.from_args(disabled_args, "agent_vm")
        removed = merge_setup_configs(
            preserved,
            disabled,
            preserve_keys=_patch_preserve_keys(disabled_args),
        )

        self.assertFalse(removed.nopasswd)

    def test_selecting_nopasswd_exits_cached_hardened_posture(self):
        cached = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            harden_user=True,
        )
        selected = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            nopasswd=True,
            harden_agent=None,
            harden_user=None,
        )

        merged = merge_setup_configs(cached, selected)

        self.assertTrue(merged.nopasswd)
        self.assertFalse(merged.harden_agent)
        self.assertFalse(merged.harden_user)


if __name__ == "__main__":
    unittest.main()
