"""Public setup selection and transient expected-release transport."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from lib import build_selection, build_setup, setup_common
from lib.build_delivery import ApplicationDeliveryReceipt, DeliveryReceipt
from lib.build_selection import SSHEndpoint
from lib.build_setup_sources import PreparedSource, _plans
from lib.cache import load_setup_command, save_setup_command
from lib.config import SetupConfig
from lib.operation_state import OperationStateStore
from lib.project_manifest import parse_manifest
from tests.test_build_selection import enrollment as builder_enrollment
from tests.test_build_setup_plan import capabilities, enrollment, manifest_data
import remote_setup


EXPECTED = [{"application": "site", "operation_id": "a" * 32,
             "generation": 1, "artifact_sha256": "b" * 64}]


class SavedBuilderPatchTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = "https://example.com/site.git#tag=v1"
        self.updated = "https://example.com/site.git#tag=v2"
        self.sibling = "https://example.com/sibling.git#tag=v1"
        policy = enrollment()
        sibling = copy.deepcopy(policy["policy"]["applications"]["site"])
        sibling["deployment_root"] = "/srv/apps/sibling"
        for component in sibling["components"].values():
            component["domain"] = "sibling.example"
        policy["policy"]["applications"]["sibling"] = sibling
        self.config = SetupConfig(
            host="target.example", username="agent", system_type="server_web", machine_type="vm",
            build_on="BUILDS", build_target_enrollment=policy,
            deploy_specs=[["EXAMPLE.COM./", self.old], ["sibling.example", self.sibling]],
        )
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {"BASALTWATER_WORKSPACE": str(self.root)}))
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        stack.enter_context(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
        # Keep real CLI validation while isolating credential reads and writes.
        stack.enter_context(patch.object(basaltwater, "prepare_runtime_config", side_effect=copy.deepcopy))
        stack.enter_context(patch.object(basaltwater, "store_cli_credentials"))
        stack.enter_context(patch.object(basaltwater, "print_service_access_summary"))
        stack.enter_context(patch.object(build_selection, "_endpoint", side_effect=lambda config, _keys, prefix, _workspace:
            SSHEndpoint(config.host, prefix + "fixture", "/unused/key", "/unused/known_hosts", "alice")))
        stack.enter_context(patch.object(build_setup, "_preflight_target"))
        stack.enter_context(patch.object(build_setup, "strict_ssh_transport"))
        stack.enter_context(patch.object(build_setup, "_check_workspace_capacity"))
        self.sources = stack.enter_context(patch.object(build_setup, "_prepare_sources", side_effect=self.prepare_sources))
        self.builder = stack.enter_context(patch.object(build_setup, "BuildHostClient"))
        self.builder.return_value.__enter__.return_value.capabilities.return_value = capabilities()
        self.target = stack.enter_context(patch.object(build_setup, "BuildTargetClient"))
        self.delivery = stack.enter_context(patch.object(build_setup, "BuildDeliveryController"))
        self.delivery.return_value.deliver.side_effect = self.deliver
        self.apply = stack.enter_context(patch.object(setup_common, "_run_remote_setup_locked", return_value=0))
        for name in ("subprocess.Popen", "subprocess.run", "socket.socket", "socket.getaddrinfo"):
            stack.enter_context(patch(name, side_effect=AssertionError("Unexpected system/remote action: " + name)))
        for host, name in (("builder.example", "builds"), ("next-builder.example", "next-builds")):
            save_setup_command(SetupConfig(
                host=host, username="agent", system_type="server_lite", machine_type="vm",
                friendly_name=name, is_build_server=True, build_host_enrollment=builder_enrollment(),
            ))
        save_setup_command(self.config)

    def prepare_sources(self, specs, destination):
        # Simulated pinned source results at the Git boundary; retain the real
        # destination/source parser and delivery planner on either side.
        prepared = []
        for index, (target, _url, ref) in enumerate(_plans(specs)):
            path = Path(destination) / f"{index}.snapshot"
            path.write_bytes(b"fixture snapshot")
            path.chmod(0o600)
            prepared.append(PreparedSource(
                target, parse_manifest(manifest_data()),
                {"revision": ("2" if ref == "refs/tags/v2" else "1") * 40,
                 "snapshot_sha256": "b" * 64, "manifest_sha256": "c" * 64}, str(path),
            ))
        return prepared

    def deliver(self, plans):
        return DeliveryReceipt(tuple(ApplicationDeliveryReceipt(
            application=plan.deployment["application"], request_id=plan.request["request_id"],
            operation_id=str(index + 1) * 32, generation=1, artifact_sha256="d" * 64,
            artifact_size=128,
        ) for index, plan in enumerate(plans)), "/retained/receipt.json")

    def patch_args(self, *options):
        return self.parser.parse_args(["patch", "target.example", "agent", *options])

    def test_public_patch_then_reloaded_saved_deploy_delivers_only_updated_source(self):
        self.assertEqual(basaltwater.run_patch_command(self.patch_args(
            "--deploy", "example.com, EXAMPLE.COM./", self.updated,
            "--deploy", "example.com/", self.updated,
        )), 0)
        saved = load_setup_command("target.example")
        self.assertEqual(saved.build_on, "BUILDS")
        self.assertEqual(saved.deploy_specs, [
            ["sibling.example", self.sibling], ["example.com", self.updated],
        ])
        self.assertEqual(self.builder.call_args.kwargs["host"], "builder.example")
        self.sources.assert_called_once()
        self.apply.reset_mock()
        self.sources.reset_mock()
        self.delivery.return_value.deliver.reset_mock()
        args = self.parser.parse_args(["deploy", "target.example", "--yes"])
        self.assertEqual(basaltwater.run_deploy_command(args), 0)
        self.assertEqual(self.sources.call_args.args[0], saved.deploy_specs)
        plans = self.delivery.return_value.deliver.call_args.args[0]
        self.assertEqual({plan.deployment["application"]: plan.request["source"]["revision"]
                          for plan in plans}, {"site": "2" * 40, "sibling": "1" * 40})
        self.assertEqual(self.apply.call_count, 2)
        bootstrap, profile = self.apply.call_args_list
        self.assertEqual(bootstrap.args[0].custom_steps, "bootstrap_build_target")
        self.assertIsNone(bootstrap.args[0].deploy_specs)
        self.assertIsNone(profile.args[0].build_on)
        self.assertTrue(profile.args[0].artifact_deployments)
        self.assertIsNone(profile.args[0].build_target_enrollment)
        self.assertEqual(profile.kwargs["expected_releases"], [
            {"application": plan.deployment["application"], "operation_id": str(index + 1) * 32,
             "generation": 1, "artifact_sha256": "d" * 64}
            for index, plan in enumerate(plans)
        ])
        journal = next(self.root.glob("build-setups/*/setup.json.last.json"))
        self.assertEqual(json.loads(journal.read_text())["outcome"], "succeeded")
        self.assertIsNone(OperationStateStore(str(journal).removesuffix(".last.json")).load())

    def test_public_explicit_builder_switch_replaces_source_using_new_selection(self):
        self.assertEqual(basaltwater.run_patch_command(self.patch_args(
            "--build-on", "NEXT-BUILDS", "--deploy", "EXAMPLE.COM/", self.updated,
        )), 0)
        saved = load_setup_command("target.example")
        self.assertEqual(saved.build_on, "NEXT-BUILDS")
        self.assertEqual(saved.deploy_specs, [["sibling.example", self.sibling], ["example.com", self.updated]])
        self.assertEqual(self.builder.call_args.kwargs["host"], "next-builder.example")

    def test_public_conflicting_replacements_refuse_before_source_or_transport(self):
        self.assertEqual(basaltwater.run_patch_command(self.patch_args(
            "--deploy", "EXAMPLE.COM/, sibling.example", self.updated,
            "--deploy", "example.com.", self.old,
        )), 1)
        self.sources.assert_not_called()
        self.builder.assert_not_called()
        self.target.assert_not_called()
        self.delivery.assert_not_called()
        self.apply.assert_not_called()
        self.assertEqual(load_setup_command("target.example").deploy_specs, self.config.deploy_specs)
        self.assertFalse((self.root / "build-setups").exists())

    def test_public_no_build_on_retains_standard_pair_merge_and_transport(self):
        self.assertEqual(basaltwater.run_patch_command(self.patch_args(
            "--no-build-on", "--deploy", "EXAMPLE.COM./", self.updated,
        )), 0)
        saved = load_setup_command("target.example")
        self.assertIsNone(saved.build_on)
        self.assertFalse(saved.artifact_deployments)
        self.assertEqual(saved.deploy_specs, [*self.config.deploy_specs, ["EXAMPLE.COM./", self.updated]])
        self.builder.assert_not_called()
        self.sources.assert_not_called()
        self.apply.assert_called_once()
        self.assertFalse(self.apply.call_args.args[0].artifact_deployments)

    def test_unsupported_patch_preserves_saved_setup_and_credentials(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        for options in (("--node",), ("--app-server",), ("--go",)):
            for dry_run in (False, True):
                with self.subTest(options=options, dry_run=dry_run), \
                     patch.object(basaltwater, "store_cli_credentials") as credentials, \
                     patch.object(basaltwater, "save_setup_command") as save:
                    self.assertEqual(basaltwater.run_patch_command(self.patch_args(
                        *options, *(["--dry-run"] if dry_run else []),
                    )), 1)
                    credentials.assert_not_called()
                    save.assert_not_called()
                self.assertEqual(
                    {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, before,
                )
        self.sources.assert_not_called()
        self.builder.assert_not_called()
        self.target.assert_not_called()
        self.apply.assert_not_called()

    def test_unsupported_saved_deploy_preserves_setup_and_credentials(self):
        self.config.is_app_server = True
        save_setup_command(self.config)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.object(basaltwater, "store_cli_credentials") as credentials, \
             patch.object(basaltwater, "save_setup_command") as save:
            args = self.parser.parse_args(["deploy", "target.example", "--yes"])
            self.assertEqual(basaltwater.run_deploy_command(args), 1)
        credentials.assert_not_called()
        save.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, before)
        self.sources.assert_not_called()
        self.builder.assert_not_called()
        self.apply.assert_not_called()


class BuildSetupTransportTests(unittest.TestCase):
    def config(self, **kwargs):
        return SetupConfig(host="target.example", username="agent", system_type="server_web",
                           machine_type="vm", **kwargs)

    def test_unsupported_setup_refuses_before_provisioning_or_saving(self):
        parser, _, _ = basaltwater.create_basaltwater_parser()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"BASALTWATER_WORKSPACE": temporary}))
            stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
            stack.enter_context(patch("lib.system_utils.get_local_timezone", return_value="UTC"))
            provision = stack.enter_context(patch("lib.proxmox_vm.provision_vm"))
            start = stack.enter_context(patch("lib.proxmox_vm.ensure_vm_running"))
            credentials = stack.enter_context(patch.object(basaltwater, "store_cli_credentials"))
            save = stack.enter_context(patch.object(basaltwater, "save_setup_command"))
            apply = stack.enter_context(patch.object(basaltwater, "run_remote_setup"))
            policy = Path(temporary) / "target.json"
            policy.write_text(json.dumps(enrollment()))
            for options in (("--node",), ("--app-server",), ("--go",), ()):
                for dry_run in (False, True):
                    with self.subTest(options=options, dry_run=dry_run):
                        args = parser.parse_args([
                            "setup", "server_web", "192.0.2.52", "agent",
                            "--provision-on", "192.0.2.10", "--build-on", "builder",
                            "--deploy", "example.com", "https://example.com/source.git",
                            *(["--build-target-policy", str(policy)] if options else []),
                            *options, *(["--dry-run"] if dry_run else []),
                        ])
                        self.assertEqual(basaltwater.run_setup_command(args), 1)
            provision.assert_not_called()
            start.assert_not_called()
            credentials.assert_not_called()
            save.assert_not_called()
            apply.assert_not_called()

    def test_legacy_runtime_preparation_checks_selection_before_credentials(self):
        config = self.config(build_on="builder", is_app_server=True,
                             build_target_enrollment=enrollment(),
                             deploy_specs=[["example.com", "https://example.com/source.git"]])
        with patch.object(setup_common, "prepare_runtime_config") as credentials, \
             self.assertRaisesRegex(ValueError, "legacy app-server"):
            setup_common.prepare_validated_runtime_config(config, None)
        credentials.assert_not_called()

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
