"""Controller bootstrap/delivery/profile ordering and durable recovery boundary."""

from __future__ import annotations

from contextlib import ExitStack
import copy
from dataclasses import asdict, replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from lib import build_setup, setup_common
from lib.build_admission import DeploymentAdmissionStore
from lib.build_delivery import (
    ApplicationDeliveryReceipt, BuildDeliveryController, DeliveryReceipt, DeliveryRecoveryRequired,
)
from lib.build_protocol import encode_document, request_digest
from lib.build_selection import BuilderSelection, SSHEndpoint
from lib.build_setup_recovery import inspect_build_setup_recovery
from lib.config import SetupConfig
from lib.operation_state import OperationStateStore
from lib.project_manifest import parse_manifest
from tests.test_build_delivery import BuildDeliveryFixture
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
            ssh_access_sources=["192.0.2.10/32"],
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
                                        "manifest_sha256": "c" * 64}, source_path=str(path), build_tools=None)]

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
        self.assertEqual(bootstrap.ssh_access_sources, self.config.ssh_access_sources)
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
        completion = next(self.root.glob("build-setups/*/*/profile-success.json"))
        self.assertEqual(completion.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(completion.read_bytes()), {"schema": 1, "operation": result["operation"]})

    def test_profile_success_receipt_failure_retains_guard_and_completed_delivery(self):
        with patch.object(build_setup, "record_setup_success", side_effect=OSError("receipt unavailable")):
            with self.assertRaises(build_setup.BuildSetupRecoveryRequired) as caught:
                build_setup.run_build_setup(self.config, apply_setup=self.apply)
        marker = OperationStateStore(caught.exception.journal_path).load()
        self.assertEqual(marker.status, "recovery_required")
        self.assertEqual(marker.context["delivery_receipt"], "/retained/delivery/receipt.json")
        self.assertEqual(self.apply.call_count, 2)

    def test_archived_history_no_longer_blocks_the_active_attempt_limit(self):
        with patch.object(build_setup, "MAX_RETAINED_ATTEMPTS", 1):
            self.assertEqual(build_setup.run_build_setup(self.config, apply_setup=self.apply), 0)
            self.apply.reset_mock()
            with self.assertRaisesRegex(ValueError, "retention is full"):
                build_setup.run_build_setup(self.config, apply_setup=self.apply)
            self.apply.assert_not_called()
            attempt = next(self.root.glob("build-setups/*/*/profile-success.json")).parent
            attempt.rename(attempt.with_name("archived-" + attempt.name))
            self.assertEqual(build_setup.run_build_setup(self.config, apply_setup=self.apply), 0)
            self.assertEqual(self.apply.call_count, 2)

    def test_bootstrap_freezes_inferred_lan_sources_without_network_mutation_options(self):
        self.config.host = "192.168.0.52"
        self.config.lan_access = True
        self.config.static_ipv4 = "192.168.0.52/20"
        bootstrap = build_setup._bootstrap_config(self.config)
        self.assertEqual(bootstrap.effective_access_sources(), self.config.effective_access_sources())
        self.assertIn("192.168.0.0/20", bootstrap.effective_access_sources())
        self.assertFalse(bootstrap.lan_access)
        self.assertIsNone(bootstrap.static_ipv4)

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

    def test_setup_primitive_uploads_use_resolved_transport_without_reusing_sockets(self):
        # Selection can resolve the default identity even when config has no key.
        self.config.ssh_key = None
        streamed = []

        def upload(command, **_kwargs):
            streamed.append(command)
            return 0

        with patch.object(setup_common, "copy_project_files"), \
             patch.object(setup_common, "prepare_registration_payload", return_value=False), \
             patch.object(setup_common, "create_tar_from_dir", return_value=b"runtime"), \
             patch.object(setup_common, "_create_payload_archive", return_value=b"payload"), \
             patch.object(setup_common, "run_streamed", side_effect=upload), \
             patch("lib.ssh_utils.get_workspace_known_hosts_path", side_effect=AssertionError("ambient trust")), \
             patch("lib.ssh_utils.tempfile.gettempdir", return_value=str(self.root)), \
             patch("lib.ssh_utils.sys.stdin.isatty", return_value=True):
            self.assertEqual(build_setup.run_build_setup(
                self.config, apply_setup=setup_common._run_remote_setup_locked,
            ), 0)

        self.assertEqual(len(streamed), 2)
        for command in streamed:
            self.assertEqual(command[:3], ["/usr/bin/ssh", "-F", "/dev/null"])
            self.assertEqual(command[command.index("-i") + 1], self.endpoint.identity_file)
            self.assertEqual(command[command.index("-p") + 1], str(self.endpoint.port))
            self.assertEqual(command[-2], "root@target.example")
            for option in ("IdentitiesOnly=yes", "IdentityAgent=none", "ForwardAgent=no",
                           "ClearAllForwardings=yes", "ControlMaster=no", "ControlPath=none",
                           "ControlPersist=no", "StrictHostKeyChecking=yes", "BatchMode=yes",
                           "UserKnownHostsFile=/private/known_hosts"):
                self.assertIn(option, command)
            self.assertEqual([part for part in command if part.startswith("ControlPath=")], ["ControlPath=none"])
        self.assertFalse(any(self.root.glob("basaltwater-ssh-*")))

    def test_dry_run_never_mutates_remote_or_creates_persistent_attempt(self):
        self.config.dry_run = True
        self.assertEqual(build_setup.run_build_setup(self.config, apply_setup=self.apply), 0)
        temporary = Path(self.source.call_args.args[1])
        self.assertEqual(temporary.parent, self.root)
        self.assertFalse(temporary.exists())
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.target.preflight.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.builder.capabilities.assert_called_once()
        self.probe.assert_called_once()
        self.assertEqual(self.builder.mock_calls, [call.capabilities()])
        self.assertEqual(self.target.mock_calls, [])

    def test_dry_run_cleans_workspace_temporary_directory_on_failure(self):
        self.config.dry_run = True
        for stage in (self.source, self.capacity):
            with self.subTest(stage=stage):
                stage.side_effect = ValueError("preparation failed")
                with self.assertRaisesRegex(ValueError, "preparation failed"):
                    build_setup.run_build_setup(self.config, apply_setup=self.apply)
                temporary = Path(self.source.call_args.args[1])
                self.assertEqual(temporary.parent, self.root)
                self.assertFalse(temporary.exists())
                self.assertEqual(list(self.root.iterdir()), [])
                stage.side_effect = self.sources if stage is self.source else None
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.assertEqual(self.target.mock_calls, [])

    def test_all_local_requirements_fail_before_bootstrap(self):
        for failure in (ValueError("source"), PermissionError("source")):
            with self.subTest(failure=failure):
                self.source.side_effect = failure
                with self.assertRaises(type(failure)):
                    build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.assertFalse(list(self.root.glob("build-setups/*/setup.json")))

    def test_web_target_requires_trusted_ssh_sources_before_network_or_preparation(self):
        self.config.ssh_access_sources = None
        with self.assertRaisesRegex(ValueError, "--ssh-access-source"):
            build_setup.run_build_setup(self.config, apply_setup=self.apply)
        self.builder.capabilities.assert_not_called()
        self.probe.assert_not_called()
        self.source.assert_not_called()
        self.apply.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.config.system_type = "server_lite"
        build_setup.validate_build_setup_selection(self.config)
        self.config.system_type = "server_web"
        self.config.access_sources = ["192.0.2.0/24"]
        build_setup.validate_build_setup_selection(self.config)

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
        self.assertEqual(synced, [str(self.root)])
        self.source.assert_not_called()
        self.apply.assert_not_called()
        self.deliver.assert_not_called()

    def test_retry_syncs_existing_directory_after_prior_parent_sync_failure(self):
        path = self.root / "retry"
        synced = []

        def fsync(descriptor):
            synced.append(os.readlink(f"/proc/self/fd/{descriptor}"))
            if len(synced) == 2:
                raise OSError("parent sync failed")

        with patch.object(build_setup.os, "fsync", side_effect=fsync):
            with self.assertRaises(OSError):
                build_setup._private_directory(str(path))
            self.assertTrue(path.is_dir())
            build_setup._private_directory(str(path))
        self.assertEqual(synced, [str(path), str(self.root)] * 2)

    def test_unsupported_target_roles_and_explicit_toolchains_refuse_before_network(self):
        for changes in ({"enable_ssl": "yes"}, {"is_build_server": True}, {"install_go": True},
                        {"install_node_versions": True}, {"system_type": "workstation"},
                        {"disable_build_target": True}, {"host": "localhost"}):
            config = copy.deepcopy(self.config)
            for key, value in changes.items():
                setattr(config, key, value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_setup.run_build_setup(config, apply_setup=self.apply)
        self.builder.capabilities.assert_not_called()
        self.apply.assert_not_called()

    def test_legacy_app_server_and_network_activation_refuse_before_bootstrap(self):
        for changes in ({"is_app_server": True},
                        {"activate_network": True, "static_ipv4": "192.0.2.50/24"}):
            config = copy.deepcopy(self.config)
            for key, value in changes.items():
                setattr(config, key, value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_setup.run_build_setup(config, apply_setup=self.apply)
            self.assertEqual(config.host, self.config.host)
        self.builder.capabilities.assert_not_called()
        self.probe.assert_not_called()
        self.source.assert_not_called()
        self.apply.assert_not_called()
        self.deliver.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_real_probe_uses_pinned_identity_no_agent_and_rejects_bad_state(self):
        value = {"uid": 0, "os": "debian", "version": "13", "architecture": "x86_64",
                 "setup_pending": False, "legacy_ssh": False, "enrollment_schema": None,
                 "ssh_peer": ["192.0.2.10"]}
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
                               ("enrollment_schema", 1), ("version", "12"), ("uid", 1000),
                               ("ssh_peer", []), ("ssh_peer", ["198.51.100.2"]),
                               ("ssh_peer", ["invalid"]), ("ssh_peer", "192.0.2.10")):
                run.return_value.stdout = json.dumps(dict(value, **{key: wrong})).encode()
                with self.subTest(key=key), self.assertRaises(ValueError):
                    _REAL_PREFLIGHT_TARGET(self.config, self.endpoint)


@unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "Requires POSIX fork and SIGKILL")
class BuildSetupControllerCrashTests(BuildDeliveryFixture, unittest.TestCase):
    def test_sigkill_after_target_completion_before_controller_acknowledgement(self) -> None:
        # One crash window through real setup, delivery, operation and admission
        # stores. Only source preparation, transport and activation effects are
        # simulated; SIGKILL cannot run the controllers' exception handlers.
        self.plans = [replace(plan, source_path=str(plan.source_path)) for plan in self.plans]
        workspace = self.root / "setup"
        workspace.mkdir(mode=0o700)
        config = SetupConfig(
            host="target.example", username="agent", system_type="server_web", machine_type="vm",
            build_on="builder", build_target_enrollment=enrollment(),
            deploy_specs=[["example.com", "https://example.com/source.git"]], ssh_key="/private/key",
            ssh_access_sources=["192.0.2.10/32"],
        )
        endpoint = SSHEndpoint("target.example", "bw-target-fixed", "/private/key",
                               "/private/known_hosts", "alice")
        selected = BuilderSelection(SetupConfig(
            host="builder.example", username="agent", system_type="server_lite",
        ), endpoint)
        host_root = workspace / "build-setups" / hashlib.sha256(config.host.encode()).hexdigest()
        sibling = host_root / ("f" * 32)
        sibling.mkdir(mode=0o700, parents=True)
        # Explicit modes avoid inheriting an unsafe test runner umask.
        host_root.parent.chmod(0o700)
        host_root.chmod(0o700)
        (sibling / "receipt.json").write_bytes(b"retained sibling evidence\n")
        setup_journal = host_root / "setup.json"
        with OperationStateStore(str(setup_journal)).ownership() as store:
            previous = store.begin("build_setup", config.host, "profile_applying")
            store.complete(previous.operation_id)
        prior_evidence = Path(str(setup_journal) + ".last.json").read_bytes()
        target_root = self.root / "target"
        target_root.mkdir(mode=0o700)
        admission_path = target_root / "admission.json"
        admissions = DeploymentAdmissionStore(admission_path, clock=lambda: 1000)

        def public(record):
            return {key: record[key] for key in (
                "operation_id", "application", "project", "generation", "bindings_sha256", "source",
                "created_at", "expires_at", "state", "effective_status", "expired", "superseded",
            )} | {"input": {key: record[key] for key in ("request_id", "request_sha256")}}

        def reserve(**fields):
            self.before_mutation("reserve", fields["application"])
            record = admissions.reserve(
                fields["application"], fields["request"], owner=endpoint.principal,
                idempotency_key=fields["idempotency_key"], bindings_sha256="c" * 64,
                ttl_seconds=fields["ttl_seconds"],
            )
            self.admissions[record["operation_id"]] = record
            return public(record)

        context = multiprocessing.get_context("fork")
        ready, notify = context.Pipe(duplex=False)
        gate, release = context.Pipe(duplex=False)
        for connection in (ready, notify, gate, release):
            self.addCleanup(connection.close)

        def activate(**fields):
            result = admissions.activate(
                fields["operation_id"], fields["generation"], fields["request"], fields["artifact_sha256"],
                owner=endpoint.principal, bindings_sha256="c" * 64,
                action=lambda: self.activate(**fields),
            )
            if fields["deployment"]["application"] == self.plans[-1].deployment["application"]:
                # activate() returned only after saving completed and releasing
                # the target lock. Hold its response outside the controller.
                notify.send({"journal": self.controller.journal_path, "fields": fields, "result": result,
                             "events": self.events, "setup_calls": apply.call_count})
                gate.recv()
                raise AssertionError("Crash gate must never release the acknowledgement")
            return result

        def controller(**kwargs):
            self.workspace = Path(kwargs["workspace"])
            self.controller = BuildDeliveryController(**kwargs, clock=lambda: 0)
            return self.controller

        def child():
            ready.close()
            release.close()
            try:
                build_setup.run_build_setup(config, apply_setup=apply)
                notify.send({"error": "Controller unexpectedly returned"})
            except BaseException:
                notify.send({"error": traceback.format_exc()})
            finally:
                notify.close()
                gate.close()

        def snapshot(root=self.root):
            return {str(path.relative_to(root)): (
                path.stat().st_ino, path.stat().st_mode,
                path.stat().st_mtime_ns, path.stat().st_ctime_ns,
                path.read_bytes() if path.is_file() else None,
            ) for path in root.rglob("*")}

        retained_sibling = snapshot(sibling)
        apply = Mock(return_value=0)
        self.target.reserve.side_effect = reserve
        self.target.status.side_effect = lambda operation: public(admissions.status(operation, owner=endpoint.principal))
        self.target.activate.side_effect = activate
        with ExitStack() as stack:
            # These guards also apply in the forked controller. Pipe(duplex=False)
            # uses anonymous pipes, so no socket or executable is required.
            for name in ("subprocess.run", "socket.socket", "socket.getaddrinfo"):
                stack.enter_context(patch(name, side_effect=AssertionError("Host/network operation forbidden")))
            stack.enter_context(patch.object(build_setup, "resolve_saved_builder", return_value=selected))
            stack.enter_context(patch.object(build_setup, "resolve_target_endpoint", return_value=endpoint))
            probe = stack.enter_context(patch.object(build_setup, "_preflight_target"))
            stack.enter_context(patch.object(build_setup, "get_workspace_dir", return_value=str(workspace)))
            prepare = stack.enter_context(patch.object(build_setup, "_plans", return_value=self.plans))
            builder_client = stack.enter_context(patch.object(build_setup, "BuildHostClient"))
            target_client = stack.enter_context(patch.object(build_setup, "BuildTargetClient"))
            builder_client.return_value.__enter__.return_value = self.builder
            target_client.return_value.__enter__.return_value = self.target
            stack.enter_context(patch.object(build_setup, "BuildDeliveryController", side_effect=controller))
            process = context.Process(target=child)
            process.start()
            notify.close()
            gate.close()
            try:
                self.assertTrue(ready.poll(15), "Controller did not reach the durable target completion gate")
                message = ready.recv()
                self.assertNotIn("error", message, message.get("error"))
                fields = message["fields"]
                # Independently reopen the target ledger BEFORE killing the
                # controller; the signal follows durable completion, not time.
                fresh_target = DeploymentAdmissionStore(admission_path, clock=lambda: 1000)
                completed = fresh_target.status(fields["operation_id"], owner=endpoint.principal)
                self.assertEqual(completed["state"], "completed")
                self.assertEqual(completed["result"], message["result"])
                delivery_journal = Path(message["journal"])
                attempt = delivery_journal.parent.parent
                prepared_path = attempt / "prepared.json"
                prepared = json.loads(prepared_path.read_bytes())
                self.assertEqual(prepared, json.loads(encode_document({
                    "schema": 1, "plans": [asdict(plan) for plan in self.plans],
                })))
                setup = OperationStateStore(str(setup_journal)).load()
                self.assertEqual((setup.phase, setup.status), ("delivering", "in_progress"))
                self.assertEqual(setup.context["attempt"], str(attempt))
                self.assertEqual(setup.context["prepared_sha256"], hashlib.sha256(encode_document(prepared)).hexdigest())
                delivery = OperationStateStore(str(delivery_journal)).load()
                self.assertEqual((delivery.phase, delivery.status), ("activating", "in_progress"))
                first, last = delivery.context["applications"]
                self.assertEqual(first["status"], "completed")
                self.assertEqual(first["activation_receipt"]["status"], "completed")
                self.assertEqual(last["status"], "activating")
                self.assertNotIn("activation_receipt", last)
                self.assertEqual(last["request_id"], fields["request"]["request_id"])
                self.assertEqual(last["request_sha256"], request_digest(fields["request"]))
                self.assertEqual(last["source"], fields["request"]["source"])
                self.assertEqual(last["idempotency_key"], self.plans[-1].idempotency_key)
                for key in ("operation_id", "generation", "artifact_sha256", "bindings_sha256"):
                    self.assertEqual(last[key], completed[key])
                self.assertEqual(last["artifact_size"], self.artifacts[last["request_id"]][1]["size"])
                bundle = delivery_journal.parent / ("artifact-" + last["request_id"] + ".bundle")
                self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), last["artifact_sha256"])
                self.assertEqual([event for event, _ in message["events"]].count("activate"), 2)
                self.assertEqual(message["setup_calls"], 1)  # Bootstrap only, no final profile.
                retained = snapshot()
                process.kill()
                process.join(10)
                self.assertEqual(process.exitcode, -signal.SIGKILL)
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(10)
                process.close()

            # New controller/store objects must acquire the released locks and
            # refuse from the retained guards before any mutation or new IDs.
            self.builder.reset_mock()
            self.target.reset_mock()
            # Offline inspection preserves the lost acknowledgement window:
            # target completion above is independent evidence, never inferred
            # from the controller's locally saved activating intent.
            inspected = inspect_build_setup_recovery(setup_journal)
            self.assertEqual(inspect_build_setup_recovery(setup_journal), inspected)
            self.assertEqual(inspected["remote_state"], "unobserved")
            self.assertEqual(inspected["payload_contents"], "unverified")
            saved_first, unresolved_last = inspected["applications"]
            self.assertEqual(saved_first["local_status"], "completed")
            self.assertEqual(saved_first["activation_acknowledgement"], first["activation_receipt"])
            self.assertEqual(unresolved_last["local_status"], "activating")
            self.assertEqual(unresolved_last["unresolved_intent"], "activate")
            self.assertIsNone(unresolved_last["activation_acknowledgement"])
            for key in ("request_id", "request_sha256", "source", "idempotency_key",
                        "operation_id", "generation", "artifact_sha256", "artifact_size", "bindings_sha256"):
                self.assertEqual(unresolved_last[key], last[key])
            self.assertEqual(snapshot(), retained)
            with self.assertRaises(build_setup.BuildSetupRecoveryRequired) as caught:
                build_setup.run_build_setup(config, apply_setup=apply)
            self.assertEqual(caught.exception.journal_path, str(setup_journal))
            apply.assert_not_called()
            prepare.assert_not_called()
            self.builder.capabilities.assert_called_once()  # Read-only setup preflight.
            probe.assert_called_once()
            self.assertEqual(self.builder.mock_calls, [call.capabilities()])
            self.assertEqual(self.target.mock_calls, [])
            self.builder.reset_mock()
            fresh_controller = BuildDeliveryController(
                builder=self.builder, target=self.target, workspace=delivery_journal.parent,
            )
            with self.assertRaises(DeliveryRecoveryRequired):
                fresh_controller.deliver(self.plans)
            self.assertEqual(self.builder.mock_calls, [])
            self.assertEqual(self.target.mock_calls, [])
            # Explicit identical replay is a separate target-store operation;
            # it returns the cache without running any activation effect.
            fresh_target = DeploymentAdmissionStore(admission_path, clock=lambda: 1000)
            self.assertEqual(fresh_target.status(fields["operation_id"], owner=endpoint.principal), completed)
            action = Mock(side_effect=AssertionError("Completed activation must use its cached result"))
            replay = fresh_target.activate(
                fields["operation_id"], fields["generation"], fields["request"], fields["artifact_sha256"],
                owner=endpoint.principal, bindings_sha256=completed["bindings_sha256"], action=action,
            )
            self.assertEqual(replay, message["result"])
            action.assert_not_called()
            self.assertEqual(snapshot(), retained)
            self.assertEqual(snapshot(sibling), retained_sibling)
            self.assertEqual(Path(str(setup_journal) + ".last.json").read_bytes(), prior_evidence)


if __name__ == "__main__":
    unittest.main()
