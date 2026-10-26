"""Controller composition tests: mocked clients and private temporary files only."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.build_artifacts import MAGIC
from lib.build_client import ArtifactPublicationUncertain, BuildHostClient, BuildSubmissionUnconfirmed
from lib.build_delivery import BuildDeliveryController, DeliveryPlan, DeliveryRecoveryRequired, MAX_PLANS
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, encode_document, request_digest
from lib.build_target_client import BuildTargetClient, TargetOperationUnconfirmed
from lib.operation_state import OperationStateStore


class TestBuildDelivery(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.workspace = self.root / "delivery"
        self.workspace.mkdir(mode=0o700)
        self.now = 0.0
        self.sleeps: list[float] = []
        self.events: list[tuple[str, str]] = []
        self.builder = Mock(spec=BuildHostClient)
        self.builder.timeout = 300
        self.target = Mock(spec=BuildTargetClient)
        self.target._transport = SimpleNamespace(timeout=300)
        self.controller = BuildDeliveryController(
            builder=self.builder, target=self.target, workspace=self.workspace,
            timeout_seconds=1000, poll_interval=2, clock=lambda: self.now, sleeper=self.sleep,
        )
        self.plans = [self.plan(1), self.plan(2, service=True)]
        self.by_request = {plan.request["request_id"]: plan for plan in self.plans}
        self.by_app = {plan.deployment["application"]: plan for plan in self.plans}
        self.admissions: dict[str, dict] = {}
        self.artifacts = {plan.request["request_id"]: self.bundle(plan) for plan in self.plans}
        self.builder.capabilities.side_effect = self.capabilities
        self.target.preflight.side_effect = self.preflight
        self.target.reserve.side_effect = self.reserve
        self.builder.submit.side_effect = self.submit
        self.builder.status.side_effect = lambda request_id: self.job(self.by_request[request_id])
        self.builder.download_artifact.side_effect = self.download
        self.target.status.side_effect = self.status
        self.target.stage.side_effect = self.stage
        self.target.activate.side_effect = self.activate
        # Exercise actual descriptor/checksum verification; replace only Linux
        # native no-replace publication with a temporary-filesystem test adapter.
        for patcher in (
            patch("lib.build_artifacts._publish_tree", side_effect=self.publish_tree),
            patch("subprocess.Popen", side_effect=AssertionError("Native process forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def publish_tree(parent: int, source: str, destination: str) -> None:
        try:
            os.stat(destination, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(source, destination, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            raise FileExistsError(destination)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def plan(self, index: int, *, service: bool = False) -> DeliveryPlan:
        source = self.root / f"source-{index}"
        source.write_bytes(f"prepared pinned snapshot {index}".encode())
        source.chmod(0o400)
        artifact = "server" if service else "index.html"
        kind = "service" if service else "static"
        request = {
            "schema": 1, "request_id": f"{index:032x}", "project": "project",
            "source": {"revision": "a" * 40, "snapshot_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                       "manifest_sha256": "b" * 64},
            "platform": dict(DEBIAN_PLATFORM), "limits": dict(DEFAULT_LIMITS),
            "components": [{"name": "main", "type": kind, "artifacts": [artifact],
                            "commands": ["go build -o server ."] if service else [],
                            "env": {"BUILD_VALUE": "private-build-input"}}],
        }
        component = {"name": "main", "type": kind, "artifacts": [artifact],
                     "domain": f"app{index}.example", "path": "/"}
        component.update({"binary": artifact, "health": "/health"} if service else
                         {"output": artifact, "spa": False})
        deployment = {"schema": 1, "application": f"app{index}", "project": "project",
                      "platform": dict(DEBIAN_PLATFORM), "components": [component]}
        return DeliveryPlan(request, source, deployment, f"{index + 100:032x}", ("go",) if service else ())

    def bundle(self, plan: DeliveryPlan) -> tuple[bytes, dict]:
        content = b"verified built bytes"
        component = plan.request["components"][0]
        descriptor = {
            "schema": 1, "request_id": plan.request["request_id"], "project": "project",
            "request_sha256": request_digest(plan.request), "source": plan.request["source"],
            "platform": dict(DEBIAN_PLATFORM), "toolchains": {},
            "components": [{key: component[key] for key in ("name", "type", "artifacts")}],
            "entries": [{"path": component["artifacts"][0], "kind": "file", "size": len(content),
                         "sha256": hashlib.sha256(content).hexdigest(), "executable": component["type"] == "service"}],
        }
        encoded = encode_document(descriptor)
        body = MAGIC + len(encoded).to_bytes(8, "big") + encoded + content
        public = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                  "descriptor_sha256": hashlib.sha256(encoded).hexdigest(),
                  **{key: descriptor[key] for key in ("request_id", "request_sha256", "project", "source", "platform")}}
        return body, public

    def capabilities(self) -> dict:
        self.events.append(("capabilities", "builder"))
        return {"schema": 1, "projects": {"project": {"platform": dict(DEBIAN_PLATFORM),
                "runtimes": ["go", "node", "python"], "limits": dict(DEFAULT_LIMITS)}},
                "tool_versions": {"go": "go1.24"}}

    def preflight(self, application: str) -> dict:
        self.events.append(("preflight", application))
        plan = self.by_app[application]
        return {"application": application, "project": "project", "platform": dict(DEBIAN_PLATFORM),
                "limits": {key: DEFAULT_LIMITS[key] for key in ("artifact_bytes", "artifact_entries")},
                "routes": [{key: item[key] for key in ("name", "type", "domain", "path")}
                           for item in plan.deployment["components"]]}

    def entries(self) -> list[dict]:
        with OperationStateStore(self.controller.journal_path).ownership() as store:
            record = store.load()
            self.assertIsNotNone(record)
            return record.context["applications"]

    def marker(self) -> dict:
        # Read during a remote callback while the controller owns the lock.
        return json.loads(Path(self.controller.journal_path).read_text())

    def before_mutation(self, action: str, application: str) -> dict:
        self.events.append((action, application))
        entry = next(item for item in self.marker()["context"]["applications"]
                     if item["application"] == application)
        expected = {"reserve": "reserving", "submit": "submitting", "stage": "staging", "activate": "activating"}
        self.assertEqual(entry["status"], expected[action])
        self.assertEqual(entry["idempotency_key"], self.by_app[application].idempotency_key)
        self.assertEqual(entry["request_sha256"], request_digest(self.by_app[application].request))
        if action in {"stage", "activate"}:
            self.assertIn("artifact_sha256", entry)
            self.assertIn("artifact_size", entry)
        return entry

    def reserve(self, **fields: object) -> dict:
        application = fields["application"]
        self.before_mutation("reserve", application)
        plan = self.by_app[application]
        record = {"operation_id": f"{len(self.admissions) + 1000:032x}", "application": application,
                  "project": "project", "generation": 1, "bindings_sha256": "c" * 64,
                  "input": {"request_id": plan.request["request_id"], "request_sha256": request_digest(plan.request)},
                  "source": plan.request["source"], "created_at": 1, "expires_at": 999999,
                  "state": "reserved", "effective_status": "reserved", "expired": False, "superseded": False}
        self.admissions[record["operation_id"]] = record
        return deepcopy(record)

    def job(self, plan: DeliveryPlan, state: str = "succeeded") -> dict:
        return {**{key: plan.request[key] for key in ("request_id", "project", "source", "platform", "limits")},
                "sequence": 1, "state": state, "cancel_requested": state == "cancelled",
                "created_at": 1, "expires_at": 100,
                "started_at": None if state in {"queued", "expired"} else 2,
                "finished_at": None if state in {"queued", "running"} else (100 if state == "expired" else 3),
                "artifact": self.artifacts[plan.request["request_id"]][1] if state == "succeeded" else None,
                "reason": {"failed": "build_failed", "expired": "queue_expired", "cancelled": "cancelled",
                           "uncertain": "worker_interrupted"}.get(state)}

    def submit(self, request: dict, source: str) -> dict:
        plan = self.by_request[request["request_id"]]
        self.before_mutation("submit", plan.deployment["application"])
        self.assertEqual(len(self.admissions), len(self.plans))
        self.assertEqual(str(source), str(plan.source_path))
        return self.job(plan)

    def download(self, request_id: str, destination: str, digest: str, size: int) -> str:
        self.events.append(("download", request_id))
        body, identity = self.artifacts[request_id]
        self.assertEqual((digest, size), (identity["sha256"], len(body)))
        with open(destination, "xb") as stream:
            stream.write(body)
        os.chmod(destination, 0o600)
        return destination

    def status(self, operation_id: str) -> dict:
        self.events.append(("status", operation_id))
        return deepcopy(self.admissions[operation_id])

    def stage(self, **fields: object) -> dict:
        application = fields["deployment"]["application"]
        self.before_mutation("stage", application)
        self.assertEqual(hashlib.sha256(Path(fields["artifact_path"]).read_bytes()).hexdigest(), fields["artifact_sha256"])
        self.assertTrue((self.workspace / ("verified-" + fields["request"]["request_id"])).is_dir())
        return {key: fields[key] for key in ("operation_id", "generation", "artifact_sha256", "artifact_size")} | {"status": "staged"}

    def activate(self, **fields: object) -> dict:
        application = fields["deployment"]["application"]
        self.before_mutation("activate", application)
        entries = self.marker()["context"]["applications"]
        self.assertTrue(all("stage_receipt" in entry for entry in entries))
        self.assertEqual(self.target.status.call_count, len(self.plans))
        return {key: fields[key] for key in ("operation_id", "generation", "artifact_sha256")} | {
            "application": application, "status": "completed"}

    def assert_no_activation(self) -> None:
        self.target.activate.assert_not_called()
        self.target.cancel.assert_not_called()
        self.builder.cancel.assert_not_called()

    def test_completed_receipt_and_global_ordering(self) -> None:
        receipt = self.controller.deliver(self.plans)
        names = [name for name, _ in self.events]
        self.assertLess(max(index for index, name in enumerate(names) if name == "reserve"), names.index("submit"))
        self.assertLess(max(index for index, name in enumerate(names) if name == "stage"), names.index("activate"))
        self.assertLess(max(index for index, name in enumerate(names) if name == "preflight"), names.index("reserve"))
        self.assertEqual([item.application for item in receipt.applications], ["app1", "app2"])
        self.assertTrue(all(item.status == "completed" for item in receipt.applications))
        self.assertFalse(Path(self.controller.journal_path).exists())
        evidence = Path(receipt.receipt_path).read_text()
        self.assertNotIn("private-build-input", evidence)
        self.assertNotIn("go build", evidence)
        self.assertNotIn(str(self.root), evidence)
        self.assertEqual(len(json.loads(evidence)["applications"]), 2)
        self.assertEqual(Path(receipt.receipt_path).stat().st_mode & 0o777, 0o600)
        self.assertTrue(Path(self.controller.journal_path + ".last.json").exists())
        before = self.target.reserve.call_count
        with self.assertRaises(FileExistsError):
            self.controller.deliver(self.plans)
        self.assertEqual(self.target.reserve.call_count, before)
        self.assertEqual(Path(receipt.receipt_path).read_text(), evidence)

    def test_second_build_failure_never_activates(self) -> None:
        self.builder.submit.side_effect = lambda request, source: self.job(self.by_request[request["request_id"]],
                                                                           "failed" if request == self.plans[1].request else "succeeded")
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.assertEqual(self.target.stage.call_count, 1)
        self.assert_no_activation()

    def test_second_stage_failure_never_activates(self) -> None:
        def stage(**fields):
            if fields["deployment"]["application"] == "app2":
                raise TargetOperationUnconfirmed("stage", fields, code=None)
            return self.stage(**fields)
        self.target.stage.side_effect = stage
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.assertEqual(self.builder.download_artifact.call_count, 2)
        self.assert_no_activation()
        self.assertTrue(all("artifact_sha256" in item for item in self.entries()))

    def test_local_mismatch_and_duplicates_fail_before_preflight(self) -> None:
        second = self.plans[1]
        changed = deepcopy(second.deployment)
        changed["components"][0]["artifacts"] = ["different"]
        variants = [
            [self.plans[0], replace(second, deployment=changed)],
            [self.plans[0], self.plans[0]], [], [self.plans[0]] * (MAX_PLANS + 1),
            [replace(second, required_tools=())], [replace(second, idempotency_key="unstable")],
            [replace(second, ttl_seconds=True)], [replace(second, required_tools=("Go",))],
        ]
        for plans in variants:
            with self.subTest(plans=len(plans)), self.assertRaises(ValueError):
                self.controller.deliver(plans)
        self.builder.capabilities.assert_not_called()
        self.target.reserve.assert_not_called()

    def test_each_identity_must_be_unique(self) -> None:
        first, second = self.plans
        for name in ("application", "request_id", "key"):
            request, deployment = deepcopy(second.request), deepcopy(second.deployment)
            if name == "application":
                deployment["application"] = first.deployment["application"]
            if name == "request_id":
                request["request_id"] = first.request["request_id"]
            key = first.idempotency_key if name == "key" else second.idempotency_key
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.controller.deliver([first, replace(second, request=request, deployment=deployment, idempotency_key=key)])
        self.target.reserve.assert_not_called()

    def test_preflight_tools_routes_limits_and_projects_before_reservations(self) -> None:
        original = self.capabilities()
        variants = []
        for change in ("tools", "runtimes", "project", "platform", "limits"):
            caps = deepcopy(original)
            if change == "tools":
                caps["tool_versions"] = {"node": "v24"}  # available runtime is not the requested tool
            elif change == "runtimes":
                caps["projects"]["project"]["runtimes"] = ["node"]
            elif change == "project":
                caps["projects"] = {}
            elif change == "platform":
                caps["projects"]["project"]["platform"]["baseline"] = "debian-12"
            else:
                caps["projects"]["project"]["limits"]["artifact_bytes"] = 1
            variants.append(caps)
        for caps in variants:
            self.builder.capabilities.side_effect = None
            self.builder.capabilities.return_value = caps
            with self.assertRaises(ValueError):
                self.controller.deliver(self.plans)
        self.builder.capabilities.side_effect = self.capabilities
        valid = self.preflight("app2")
        for field, value in (("project", "other"), ("routes", []), ("limits", {"artifact_bytes": 1, "artifact_entries": 1})):
            self.target.preflight.side_effect = lambda app, field=field, value=value: self.preflight(app) if app == "app1" else valid | {field: value}
            with self.assertRaises(ValueError):
                self.controller.deliver(self.plans)
        self.target.reserve.assert_not_called()
        self.builder.submit.assert_not_called()

    def test_lost_replies_and_interrupts_keep_ids_and_block_automatic_retry(self) -> None:
        for action in ("reserve", "submit", "stage", "activate"):
            for interrupted in (False, True):
                with self.subTest(action=action, interrupted=interrupted):
                    workspace = self.root / f"{action}-{interrupted}"
                    workspace.mkdir(mode=0o700)
                    self.controller.workspace = str(workspace)
                    self.controller.journal_path = str(workspace / "delivery.json")
                    self.workspace = workspace
                    self.admissions.clear()
                    self.target.status.reset_mock()
                    client = self.builder if action == "submit" else self.target
                    original = getattr(client, action).side_effect
                    def fail(*args, **fields):
                        original(*args, **fields)  # Acceptance can precede the lost response.
                        if interrupted:
                            raise KeyboardInterrupt()
                        if action == "submit":
                            raise BuildSubmissionUnconfirmed(args[0]["request_id"], RuntimeError("lost"))
                        raise TargetOperationUnconfirmed(action, fields, code=None)
                    getattr(client, action).side_effect = fail
                    with self.assertRaisesRegex(DeliveryRecoveryRequired, "SAME recorded idempotency key"):
                        self.controller.deliver(self.plans)
                    entries = self.entries()
                    self.assertEqual([item["request_id"] for item in entries], [plan.request["request_id"] for plan in self.plans])
                    if action != "reserve":
                        self.assertTrue(all("operation_id" in item for item in entries))
                    calls = len(self.builder.mock_calls) + len(self.target.mock_calls)
                    with self.assertRaises(DeliveryRecoveryRequired):
                        self.controller.deliver(self.plans)
                    self.assertEqual(len(self.builder.mock_calls) + len(self.target.mock_calls), calls)
                    getattr(client, action).side_effect = original
        self.target.cancel.assert_not_called()
        self.builder.cancel.assert_not_called()

    def test_polling_survives_many_short_calls_and_restores_client_timeouts(self) -> None:
        self.controller.timeout_seconds = 100
        self.builder.timeout = 1
        self.builder.submit.side_effect = lambda request, source: self.job(self.by_request[request["request_id"]], "queued")
        counts: dict[str, int] = {}
        def status(request_id):
            counts[request_id] = counts.get(request_id, 0) + 1
            return self.job(self.by_request[request_id], "running" if counts[request_id] < 3 else "succeeded")
        self.builder.status.side_effect = status
        self.controller.deliver(self.plans)
        self.assertEqual(self.now, 12)
        self.assertEqual(self.builder.status.call_count, 6)
        self.assertEqual(self.builder.timeout, 1)
        self.assertEqual(self.target._transport.timeout, 300)

    def test_deadline_retains_unresolved_build_without_cancellation(self) -> None:
        self.controller.timeout_seconds = 3
        self.builder.submit.side_effect = lambda request, source: self.job(self.by_request[request["request_id"]], "running")
        def status(request_id):
            self.assertLessEqual(self.builder.timeout, 1)
            return self.job(self.by_request[request_id], "running")
        self.builder.status.side_effect = status
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.assertEqual(self.now, 3)
        self.assertEqual(self.sleeps, [2, 1])
        self.assertEqual(self.entries()[0]["build_state"], "running")
        self.assertTrue(all("operation_id" in item for item in self.entries()))
        self.assert_no_activation()

    def test_terminal_build_states_never_activate(self) -> None:
        for state in ("failed", "expired", "cancelled", "uncertain"):
            with self.subTest(state=state):
                workspace = self.root / state
                workspace.mkdir(mode=0o700)
                self.controller.workspace = str(workspace)
                self.controller.journal_path = str(workspace / "delivery.json")
                self.builder.submit.side_effect = lambda request, source: self.job(self.by_request[request["request_id"]], state)
                with self.assertRaises(DeliveryRecoveryRequired):
                    self.controller.deliver(self.plans)
        self.assert_no_activation()

    def test_rechecks_all_admissions_before_first_activation(self) -> None:
        for state in ("expired", "cancelled", "superseded", "uncertain"):
            with self.subTest(state=state):
                workspace = self.root / state
                workspace.mkdir(mode=0o700)
                self.workspace = workspace
                self.controller.workspace = str(workspace)
                self.controller.journal_path = str(workspace / "delivery.json")
                self.admissions.clear()
                def status(operation_id):
                    record = self.status(operation_id)
                    if record["application"] == "app2":
                        record["effective_status"] = state
                        if state in {"expired", "superseded"}:
                            record[state] = True
                        else:
                            record["state"] = state
                    return record
                self.target.status.side_effect = status
                with self.assertRaises(DeliveryRecoveryRequired):
                    self.controller.deliver(self.plans)
        self.assert_no_activation()

    def test_later_activation_failure_preserves_prior_completion(self) -> None:
        def activate(**fields):
            if fields["deployment"]["application"] == "app2":
                raise TargetOperationUnconfirmed("activate", fields, code="unavailable")
            return self.activate(**fields)
        self.target.activate.side_effect = activate
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        first, second = self.entries()
        self.assertEqual(first["activation_receipt"]["status"], "completed")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "activating")
        self.assertIn("operation_id", second)
        self.assertEqual(self.target.activate.call_count, 2)

    def test_uncertain_artifact_publication_retains_exact_file(self) -> None:
        def download(*args):
            self.download(*args)
            raise ArtifactPublicationUncertain(args[1], args[2], args[3])
        self.builder.download_artifact.side_effect = download
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        entry = self.entries()[0]
        path = self.workspace / ("artifact-" + entry["request_id"] + ".bundle")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["artifact_sha256"])
        self.assertEqual(path.stat().st_size, entry["artifact_size"])
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.builder.download_artifact.assert_called_once()
        self.assert_no_activation()

    def test_corrupt_bundle_cannot_reach_stage_or_activation(self) -> None:
        original = self.download
        def download(*args):
            path = original(*args)
            with open(path, "ab") as stream:
                stream.write(b"trailing bytes")
            return path
        self.builder.download_artifact.side_effect = download
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.target.stage.assert_not_called()
        self.assert_no_activation()

    def test_valid_wire_digest_still_requires_inventory_and_request_verification(self) -> None:
        request_id = self.plans[0].request["request_id"]
        original_body, original_identity = self.artifacts[request_id]
        length_offset = len(MAGIC)
        length = int.from_bytes(original_body[length_offset:length_offset + 8], "big")
        body_offset = length_offset + 8 + length
        original_descriptor = json.loads(original_body[length_offset + 8:body_offset])
        for mismatch in ("request", "checksum"):
            with self.subTest(mismatch=mismatch):
                workspace = self.root / mismatch
                workspace.mkdir(mode=0o700)
                self.controller.workspace = str(workspace)
                self.controller.journal_path = str(workspace / "delivery.json")
                self.admissions.clear()
                descriptor = deepcopy(original_descriptor)
                if mismatch == "request":
                    descriptor["request_sha256"] = "0" * 64
                else:
                    descriptor["entries"][0]["sha256"] = "0" * 64
                encoded = encode_document(descriptor)
                body = MAGIC + len(encoded).to_bytes(8, "big") + encoded + original_body[body_offset:]
                identity = original_identity | {
                    "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                    "descriptor_sha256": hashlib.sha256(encoded).hexdigest(),
                }
                self.artifacts[request_id] = body, identity
                with self.assertRaises(DeliveryRecoveryRequired):
                    self.controller.deliver(self.plans)
                self.assertTrue((workspace / ("artifact-" + request_id + ".bundle")).exists())
        self.target.stage.assert_not_called()
        self.assert_no_activation()

    def test_private_workspace_ancestors_and_inputs_before_network(self) -> None:
        self.workspace.chmod(0o755)
        with self.assertRaises(ValueError):
            self.controller.deliver(self.plans)
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.workspace.chmod(0o700)
        self.root.chmod(0o777)
        try:
            with self.assertRaises(ValueError):
                self.controller.deliver(self.plans)
        finally:
            self.root.chmod(0o700)
        source = Path(self.plans[0].source_path)
        source.chmod(0o644)
        with self.assertRaises(ValueError):
            self.controller.deliver(self.plans)
        self.builder.capabilities.assert_not_called()
        self.target.reserve.assert_not_called()

    def test_workspace_symlink_and_existing_artifact_fail_closed(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.workspace, target_is_directory=True)
        self.controller.workspace = str(alias)
        with self.assertRaises(OSError):
            self.controller.deliver(self.plans)
        self.controller.workspace = str(self.workspace)
        existing = self.workspace / ("artifact-" + self.plans[0].request["request_id"] + ".bundle")
        existing.write_bytes(b"uncertain retained file")
        with self.assertRaises(FileExistsError):
            self.controller.deliver(self.plans)
        self.assertEqual(existing.read_bytes(), b"uncertain retained file")
        self.target.reserve.assert_not_called()

    def test_input_digest_and_target_root_cannot_be_overridden(self) -> None:
        second = self.plans[1]
        request = deepcopy(second.request)
        request["source"]["snapshot_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.controller.deliver([self.plans[0], replace(second, request=request)])
        deployment = deepcopy(second.deployment)
        deployment["deployment_root"] = "/caller/authority"
        with self.assertRaises(ValueError):
            self.controller.deliver([self.plans[0], replace(second, deployment=deployment)])
        self.builder.capabilities.assert_not_called()
        self.target.reserve.assert_not_called()

    def test_guard_precedes_mutation_even_when_later_state_writes_fail(self) -> None:
        transition = OperationStateStore.transition
        def save(store, operation_id, phase, **kwargs):
            if phase == "reserved" or phase == "recovery_required":
                raise OSError("simulated state storage failure")
            return transition(store, operation_id, phase, **kwargs)
        with patch.object(OperationStateStore, "transition", save):
            with self.assertRaises(DeliveryRecoveryRequired):
                self.controller.deliver(self.plans)
        # The last durable intent predates the accepted reserve; its key still
        # recovers the response even when saving returned IDs was impossible.
        self.assertEqual(self.entries()[0]["status"], "reserving")
        self.assertEqual(self.entries()[0]["idempotency_key"], self.plans[0].idempotency_key)
        self.assertEqual(self.target.reserve.call_count, 1)
        self.builder.submit.assert_not_called()
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)

    def test_reservation_failure_stops_before_any_submission(self) -> None:
        first = self.reserve
        def reserve(**fields):
            result = first(**fields)
            if fields["application"] == "app2":
                raise TargetOperationUnconfirmed("reserve", fields, code=None)
            return result
        self.target.reserve.side_effect = reserve
        with self.assertRaises(DeliveryRecoveryRequired):
            self.controller.deliver(self.plans)
        self.builder.submit.assert_not_called()
        self.assertEqual(self.entries()[0]["operation_id"], next(iter(self.admissions)))
        self.assertEqual(self.entries()[1]["status"], "reserving")
        self.assert_no_activation()

    def test_unsafe_journal_lock_and_corrupt_marker_block_before_network(self) -> None:
        lock = self.workspace / "delivery.json.lock"
        outside = self.root / "unrelated"
        outside.write_text("preserve")
        outside.chmod(0o600)
        lock.symlink_to(outside)
        with self.assertRaises(OSError):
            self.controller.deliver(self.plans)
        self.assertEqual(outside.read_text(), "preserve")
        lock.unlink()
        marker = Path(self.controller.journal_path)
        marker.write_text("corrupt JSON")
        marker.chmod(0o600)
        with self.assertRaises(ValueError):
            self.controller.deliver(self.plans)
        self.assertEqual(marker.read_text(), "corrupt JSON")
        self.builder.capabilities.assert_not_called()

    def test_invalid_deadline_or_cadence_is_rejected(self) -> None:
        for value in (0, -1, float("nan"), float("inf"), True):
            for field in ("timeout_seconds", "poll_interval"):
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    BuildDeliveryController(builder=self.builder, target=self.target, workspace=self.workspace,
                                            **{field: value})


if __name__ == "__main__":
    unittest.main()
