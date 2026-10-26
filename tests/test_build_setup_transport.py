"""Public setup selection and transient expected-release transport."""

from __future__ import annotations

from contextlib import contextmanager
import io
import json
import sys
import unittest
from unittest.mock import patch

from lib import setup_common
from lib.config import SetupConfig
import remote_setup


EXPECTED = [{"application": "site", "operation_id": "a" * 32,
             "generation": 1, "artifact_sha256": "b" * 64}]


class BuildSetupTransportTests(unittest.TestCase):
    def config(self, **kwargs):
        return SetupConfig(host="target.example", username="agent", system_type="server_web",
                           machine_type="vm", **kwargs)

    def test_selected_builder_runs_inside_existing_target_lock_without_recursive_hook(self):
        owned = False

        @contextmanager
        def lock(kind, target):
            nonlocal owned
            self.assertEqual((kind, target), ("setup-target", "target.example"))
            owned = True
            try:
                yield
            finally:
                owned = False

        def build(config, *, apply_setup):
            self.assertTrue(owned)
            self.assertIs(apply_setup, primitive)
            self.assertEqual(config.build_on, "builder")
            return 0

        with patch.object(setup_common, "resource_lock", side_effect=lock), \
             patch.object(setup_common, "_run_remote_setup_locked") as primitive, \
             patch("lib.build_setup.run_build_setup", side_effect=build) as build_call:
            self.assertEqual(setup_common.run_remote_setup(self.config(build_on="builder")), 0)
        primitive.assert_not_called()
        build_call.assert_called_once()
        self.assertFalse(owned)

    def test_standard_setup_and_dry_run_selection_keep_their_boundaries(self):
        with patch.object(setup_common, "resource_lock") as lock, \
             patch.object(setup_common, "_run_remote_setup_locked", return_value=0) as primitive, \
             patch("lib.build_setup.run_build_setup", return_value=0) as build:
            ordinary = self.config()
            self.assertEqual(setup_common.run_remote_setup(ordinary), 0)
            primitive.assert_called_once_with(ordinary)
            build.assert_not_called()
            lock.reset_mock()
            dry = self.config(build_on="builder", dry_run=True)
            self.assertEqual(setup_common.run_remote_setup(dry), 0)
            lock.assert_not_called()
            build.assert_called_once_with(dry, apply_setup=primitive)

    def test_selected_builder_failure_never_enters_standard_setup(self):
        with patch.object(setup_common, "resource_lock"), \
             patch("lib.build_setup.run_build_setup", side_effect=ValueError("missing builder")), \
             patch.object(setup_common, "_run_remote_setup_locked") as primitive:
            with self.assertRaisesRegex(ValueError, "missing builder"):
                setup_common.run_remote_setup(self.config(build_on="missing"))
        primitive.assert_not_called()

    def test_expectations_use_private_args_payload_and_are_not_saved_configuration(self):
        config = self.config(artifact_deployments=True, dry_run=True)
        with patch.object(setup_common, "get_ssh_control_path", return_value="/unused"), \
             patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "prepare_registration_payload", return_value=False), \
             patch.object(setup_common, "_write_remote_args_file",
                          wraps=setup_common._write_remote_args_file) as write, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(setup_common._run_remote_setup_locked(config, expected_releases=EXPECTED), 0)
        tokens = write.call_args.args[1]
        index = tokens.index("--artifact-releases-json")
        self.assertEqual(json.loads(tokens[index + 1]), EXPECTED)
        self.assertNotIn("artifact_releases_json", config.to_dict())
        self.assertNotIn("--artifact-releases-json", config.to_remote_args())
        with self.assertRaisesRegex(ValueError, "artifact profile replay"):
            setup_common._run_remote_setup_locked(self.config(), expected_releases=EXPECTED)

    def tokens(self, *extra):
        args = setup_common._expand_remote_args(self.config(artifact_deployments=True).to_remote_args())
        return ["remote_setup.py", *args, "--artifact-releases-json", json.dumps(EXPECTED), *extra]

    def test_remote_guard_covers_the_entire_profile_before_any_step_or_setup_marker(self):
        owned = False

        @contextmanager
        def guard(config, expected):
            nonlocal owned
            self.assertTrue(config.artifact_deployments)
            self.assertEqual(expected, EXPECTED)
            self.assertIsNone(config.build_target_enrollment)
            owned = True
            try:
                yield
            finally:
                owned = False

        def run(config, _args):
            self.assertTrue(owned)
            self.assertNotIn("artifact_releases_json", config.to_dict())
            return 0

        with patch.object(sys, "argv", self.tokens()), \
             patch("lib.cachyos.is_cachyos", return_value=False), \
             patch("common.build_target_refresh.expected_artifact_setup", side_effect=guard), \
             patch.object(remote_setup, "_run_configured_setup", side_effect=run) as apply:
            self.assertEqual(remote_setup._run_main(), 0)
        apply.assert_called_once()
        self.assertFalse(owned)

    def test_stale_expectation_or_malformed_json_refuses_before_profile(self):
        with patch.object(sys, "argv", self.tokens()), \
             patch("lib.cachyos.is_cachyos", return_value=False), \
             patch("common.build_target_refresh.expected_artifact_setup",
                   side_effect=RuntimeError("stale generation")), \
             patch.object(remote_setup, "_run_configured_setup") as apply:
            with self.assertRaisesRegex(RuntimeError, "stale generation"):
                remote_setup._run_main()
        apply.assert_not_called()
        tokens = self.tokens()
        tokens[-1] = '[{"application":"site","application":"other"}]'
        with patch.object(sys, "argv", tokens), \
             patch("lib.cachyos.is_cachyos", return_value=False), \
             patch.object(remote_setup, "_run_configured_setup") as apply:
            with self.assertRaisesRegex(ValueError, "duplicate"):
                remote_setup._run_main()
        apply.assert_not_called()

    def test_hidden_expected_input_cannot_change_enrollment_or_run_ordinary_profile(self):
        for mode in ("disable", "ordinary"):
            tokens = self.tokens(*(["--disable-build-target"] if mode == "disable" else []))
            if mode == "ordinary":
                tokens.remove("--artifact-deployments")
            with self.subTest(mode=mode), patch.object(sys, "argv", tokens), \
                 patch("lib.cachyos.is_cachyos", return_value=False), \
                 patch.object(remote_setup, "_run_configured_setup") as apply, \
                 self.assertRaises(ValueError):
                remote_setup._run_main()
            apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
