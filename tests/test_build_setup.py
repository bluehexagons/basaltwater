"""Controller bootstrap/delivery/profile ordering and durable recovery boundary."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import build_setup
from lib.build_delivery import ApplicationDeliveryReceipt, DeliveryReceipt
from lib.build_selection import BuilderSelection, SSHEndpoint
from lib.config import SetupConfig
from lib.operation_state import OperationStateStore
from lib.project_manifest import parse_manifest
from tests.test_build_setup_plan import capabilities, enrollment, manifest_data


_REAL_PREFLIGHT_TARGET = build_setup._preflight_target


class BuildSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = SetupConfig(
            host="target.example", username="agent", system_type="server_web", machine_type="vm",
            build_on="builder", build_target_enrollment=enrollment(),
            deploy_specs=[["example.com", "https://example.com/source.git"]], ssh_key="/private/key",
        )
        self.endpoint = SSHEndpoint("target.example", "bw-target-fixed", "/private/key",
                                    "/private/known_hosts", "alice")
        self.builder_config = SetupConfig(host="builder.example", username="agent", system_type="server_lite")
        selected = BuilderSelection(self.builder_config, self.endpoint)
        self.events = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(build_setup, "resolve_saved_builder", return_value=selected))
        stack.enter_context(patch.object(build_setup, "resolve_target_endpoint", return_value=self.endpoint))
        self.probe = stack.enter_context(patch.object(build_setup, "_preflight_target"))
        stack.enter_context(patch.object(build_setup, "get_workspace_dir", return_value=str(self.root)))
        self.source = stack.enter_context(patch.object(build_setup, "_prepare_sources", side_effect=self.sources))
        self.capacity = stack.enter_context(patch.object(build_setup, "_check_workspace_capacity"))
        self.builder = stack.enter_context(patch.object(build_setup, "BuildHostClient")).return_value.__enter__.return_value
        self.target = stack.enter_context(patch.object(build_setup, "BuildTargetClient")).return_value.__enter__.return_value
        self.builder.capabilities.return_value = capabilities()
        self.deliver = stack.enter_context(patch.object(build_setup, "BuildDeliveryController")).return_value.deliver
        self.deliver.side_effect = self.delivered
        self.apply = Mock(side_effect=self.applied)

    def sources(self, _specs, destination):
        self.events.append("prepare")
        path = Path(destination) / "pinned.source"
        path.write_bytes(b"pinned bytes")
        path.chmod(0o600)
        return [SimpleNamespace(destination="example.com", manifest=parse_manifest(manifest_data()),
                                source={"revision": "a" * 40, "snapshot_sha256": "b" * 64,
                                        "manifest_sha256": "c" * 64}, source_path=str(path))]

    def applied(self, config, **kwargs):
        self.events.append("profile" if config.artifact_deployments else "bootstrap")
        guards = list(self.root.glob("build-setups/*/setup.json"))
        self.assertEqual(len(guards), 1)
        marker = OperationStateStore(str(guards[0])).load()
        self.assertEqual(marker.phase, "profile_applying" if kwargs else "bootstrapping")
        attempt = Path(marker.context["attempt"])
        prepared = json.loads((attempt / "prepared.json").read_text())
        self.assertEqual(prepared["plans"][0]["request"]["source"]["revision"], "a" * 40)
        self.assertTrue(Path(prepared["plans"][0]["source_path"]).is_file())
        return 0

    def delivered(self, plans):
        self.events.append("delivery")
        self.assertEqual(len(plans), 1)
        return DeliveryReceipt((ApplicationDeliveryReceipt(
            application="site", request_id=plans[0].request["request_id"], operation_id="d" * 32,
            generation=1, artifact_sha256="e" * 64, artifact_size=128,
        ),), "/retained/delivery/receipt.json")

    def test_bootstrap_delivery_and_profile_preserve_controller_selection(self):
        before = copy.deepcopy(self.config)
        self.assertEqual(build_setup.run_build_setup(self.config, apply_setup=self.apply), 0)
        self.assertEqual(self.config, before)
        self.assertEqual(self.events, ["prepare", "bootstrap", "delivery", "profile"])
        bootstrap = self.apply.call_args_list[0].args[0]
        self.assertEqual(bootstrap.custom_steps, "bootstrap_build_target")
        self.assertIsNone(bootstrap.deploy_specs)
        self.assertIsNone(bootstrap.build_on)
        profile = self.apply.call_args_list[1].args[0]
        self.assertTrue(profile.artifact_deployments)
        self.assertIsNone(profile.build_on)
        self.assertIsNone(profile.build_target_enrollment)
        expected = self.apply.call_args_list[1].kwargs["expected_releases"]
        self.assertEqual(expected, [{"application": "site", "operation_id": "d" * 32,
                                     "generation": 1, "artifact_sha256": "e" * 64}])
        self.assertEqual(list(self.root.glob("build-setups/*/setup.json")), [])
        result = json.loads(next(self.root.glob("build-setups/*/setup.json.last.json")).read_text())
        self.assertEqual(result["outcome"], "succeeded")

    def test_uncertain_bootstrap_delivery_or_profile_preserves_exact_inputs_and_blocks_reentry(self):
        self.deliver.side_effect = KeyboardInterrupt
        with self.assertRaises(build_setup.BuildSetupRecoveryRequired) as caught:
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        journal = caught.exception.journal_path
        marker = OperationStateStore(journal).load()
        self.assertEqual(marker.status, "recovery_required")
        attempt = Path(marker.context["attempt"])
        before = (attempt / "prepared.json").read_bytes()
        self.source.reset_mock()
        self.apply.reset_mock()
        with self.assertRaises(build_setup.BuildSetupRecoveryRequired):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.source.assert_not_called()
        self.apply.assert_not_called()
        self.assertEqual((attempt / "prepared.json").read_bytes(), before)

    def test_nonzero_profile_retains_completed_delivery_evidence(self):
        self.apply.side_effect = [0, 1]
        with self.assertRaises(build_setup.BuildSetupRecoveryRequired) as caught:
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        marker = OperationStateStore(caught.exception.journal_path).load()
        self.assertEqual(marker.context["delivery_receipt"], "/retained/delivery/receipt.json")
        self.assertEqual(marker.context["expected_releases"][0]["operation_id"], "d" * 32)

    def test_bootstrap_failure_never_submits_and_retains_guard(self):
        self.apply.side_effect = [1]
        with self.assertRaises(build_setup.BuildSetupRecoveryRequired):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.deliver.assert_not_called()
        self.assertEqual(self.apply.call_count, 1)

    def test_dry_run_never_mutates_remote_or_creates_persistent_attempt(self):
        self.config.dry_run = True
        self.assertEqual(build_setup.run_build_setup(self.config, apply_setup=self.apply), 0)
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.target.preflight.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.builder.capabilities.assert_called_once()
        self.probe.assert_called_once()

    def test_all_local_requirements_fail_before_bootstrap(self):
        for failure in (ValueError("source"), PermissionError("source")):
            with self.subTest(failure=failure):
                self.source.side_effect = failure
                with self.assertRaises(type(failure)):
                    build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.assertFalse(list(self.root.glob("build-setups/*/setup.json")))

    def test_insufficient_storage_and_duplicate_applications_refuse_before_bootstrap(self):
        self.capacity.side_effect = ValueError("insufficient capacity")
        with self.assertRaisesRegex(ValueError, "capacity"):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.capacity.side_effect = None
        make_sources = self.sources
        self.source.side_effect = lambda *args: make_sources(*args) * 2
        with self.assertRaisesRegex(ValueError, "same target application"):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.apply.assert_not_called()
        self.deliver.assert_not_called()

    def test_selected_builder_unavailable_never_falls_back(self):
        self.builder.capabilities.side_effect = RuntimeError("unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.apply.assert_not_called()
        self.source.assert_not_called()

    def test_directory_publication_sync_failure_prevents_remote_mutations(self):
        real_fsync = os.fsync
        synced = []

        def fsync(descriptor):
            path = os.readlink(f"/proc/self/fd/{descriptor}")
            synced.append(path)
            if path == str(self.root):
                raise OSError("directory publication uncertain")
            return real_fsync(descriptor)

        with patch.object(build_setup.os, "fsync", side_effect=fsync), \
             self.assertRaisesRegex(OSError, "publication uncertain"):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.assertEqual(synced, [str(self.root / "build-setups"), str(self.root)])
        self.source.assert_not_called()
        self.apply.assert_not_called()
        self.deliver.assert_not_called()

    def test_unsupported_target_roles_and_explicit_toolchains_refuse_before_network(self):
        for changes in ({"enable_ssl": True}, {"is_build_server": True}, {"install_go": True},
                        {"install_node_versions": True}, {"system_type": "workstation"},
                        {"disable_build_target": True}, {"host": "localhost"}):
            config = copy.deepcopy(self.config)
            for key, value in changes.items():
                setattr(config, key, value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_setup.run_build_setup(config, apply_setup=self.apply)
        self.builder.capabilities.assert_not_called()
        self.apply.assert_not_called()

    def test_real_probe_uses_pinned_identity_no_agent_and_rejects_bad_state(self):
        value = {"uid": 0, "os": "debian", "version": "13", "architecture": "x86_64",
                 "setup_pending": False, "legacy_ssh": False, "enrollment_schema": None}
        # Use the actual function rather than the orchestration fixture's mock.
        with patch.object(build_setup.subprocess, "run", return_value=SimpleNamespace(
                stdout=json.dumps(value).encode())) as run:
            _REAL_PREFLIGHT_TARGET(self.config, self.endpoint)
            argv = run.call_args.args[0]
            self.assertIn("IdentityAgent=none", argv)
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("/private/known_hosts", " ".join(argv))
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            for key, wrong in (("setup_pending", True), ("legacy_ssh", True),
                               ("enrollment_schema", 1), ("version", "12"), ("uid", 1000)):
                run.return_value.stdout = json.dumps(dict(value, **{key: wrong})).encode()
                with self.subTest(key=key), self.assertRaises(ValueError):
                    _REAL_PREFLIGHT_TARGET(self.config, self.endpoint)


if __name__ == "__main__":
    unittest.main()
