"""Signed webhook adapters use the managed input store and never execute code."""

from __future__ import annotations

import base64
import copy
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import http.client
from http.server import HTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from lib import build_webhook_service as service
from lib.build_enrollment import generate_general_builder_enrollment
from lib.build_jobs import BuildJobStore
from lib.build_setup_sources import PreparedSource
from lib.config import SetupConfig
from lib.project_manifest import parse_manifest
from lib.webhook_builds import WEBHOOK_PATH
from tests.test_build_policy_drafts import KEY
from tests.test_build_worker import REVISION, source_snapshot


SECRET = "fixture-signing-secret-" * 3
SETTINGS = dict(schema=1, secret=SECRET, repositories=["example/project"],
                branches=["main"], principal="deploy", domain="builds.example.com")


def envelope(**changes):
    payload = dict(repository={"full_name": "example/project"}, ref="refs/heads/main",
                   after=REVISION, deleted=False)
    payload.update(changes)
    body = json.dumps(payload, separators=(",", ":")).encode()
    return dict(body=base64.b64encode(body).decode(), event="push", delivery="fixture-delivery",
                signature="sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest())


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o700)
        self.inbox = self.private("incoming")
        self.receipts = self.private("receipts")
        self.preparing = self.private("preparing")
        self.host = generate_general_builder_enrollment(KEY)
        self.host["roots"] = {name: str(self.private(name)) for name in ("state", "inputs")}
        self.host.pop("keys")
        self.manifest = {"version": 1, "components": [], "build_runtimes": [],
                         "ci": {"test": [{"argv": ["true"]}]}}
        self.prepare = Mock(side_effect=self.prepared)

    def private(self, name):
        directory = self.root / name
        directory.mkdir(mode=0o700)
        return directory

    def prepared(self, specs, destination, *, expected_revision, credential_runner):
        self.assertEqual(expected_revision, REVISION)
        self.assertEqual(specs, [["build.invalid", "https://github.com/example/project.git#branch=main"]])
        self.assertIs(credential_runner, service.credential_runner)
        raw, source = source_snapshot(json.dumps(self.manifest).encode())
        snapshot = Path(destination, "source.snapshot")
        snapshot.write_bytes(raw)
        snapshot.chmod(0o400)
        return [PreparedSource("build.invalid", parse_manifest(self.manifest), source, str(snapshot), ())]

    def admit(self, event=None, **kwargs):
        return service.admit_event(envelope() if event is None else event, SETTINGS, host=self.host,
                                   receipts=str(self.receipts), preparing=str(self.preparing), prepare=self.prepare, **kwargs)

    def test_signed_push_uses_core_queue_and_unsigned_headers_cannot_replay(self):
        result = self.admit()
        self.assertEqual(result["state"], "submitted")
        jobs = BuildJobStore(str(Path(self.host["roots"]["state"], "jobs.json")))
        status = jobs.status(result["request_id"], owner="deploy")
        self.assertEqual(status["state"], "queued")
        altered = envelope()
        altered["delivery"] = "changed-unsigned-header"
        self.assertEqual(self.admit(altered), result)
        self.prepare.assert_called_once()
        self.assertEqual(list(self.preparing.iterdir()), [])

    def test_signature_repository_branch_and_revision_fail_before_fetch_or_retention(self):
        bad_signature = envelope()
        bad_signature["signature"] = "sha256=" + "0" * 64
        for event in (bad_signature, envelope(repository={"full_name": "other/project"}),
                      envelope(ref="refs/heads/untrusted"), envelope(after="HEAD"), envelope(deleted=True)):
            with self.subTest(event=event), self.assertRaises((ValueError, PermissionError)):
                self.admit(event)
        self.prepare.assert_not_called()
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_uncertain_preparation_retains_receipt_and_does_not_replay(self):
        self.prepare.side_effect = ValueError("bounded source failure")
        with self.assertRaises(ValueError):
            self.admit()
        result = self.admit()
        self.assertEqual(result["state"], "recovery_required")
        self.prepare.assert_called_once()
        self.assertEqual(list(self.preparing.iterdir()), [])

    def test_current_builder_policy_is_enforced_before_queue_submission(self):
        self.host["policy"]["principals"]["deploy"]["projects"] = {
            "other-project": self.host["policy"]["principals"]["deploy"]["projects"]["*"]}
        with self.assertRaises(PermissionError):
            self.admit()
        self.assertFalse(Path(self.host["roots"]["state"], "jobs.json").exists())

    def test_inbox_is_bounded_and_ping_does_not_retain(self):
        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        with patch.object(service, "MAX_EVENTS", 1), self.assertRaises(RuntimeError):
            service.retain_event(envelope(after="2" * 40), SETTINGS, inbox=str(self.inbox))
        ping = envelope()
        ping["event"] = "ping"
        self.assertEqual(service.retain_event(ping, SETTINGS, inbox=str(self.inbox)), {"event": "ping"})
        self.assertEqual(len(list(self.inbox.iterdir())), 1)

    def process(self, slot, admit):
        with patch.object(service, "INBOX", str(self.inbox)), \
             patch.object(service, "RECEIPTS", str(self.receipts)), \
             patch.object(service, "load_settings", return_value=SETTINGS), \
             patch("common.service_tools.build_host.load_config", return_value=self.host), \
             patch("lib.build_coordinator.host_slot", side_effect=slot), \
             patch.object(service, "admit_event", side_effect=admit):
            service.process_once()

    def test_capacity_recovery_preserves_accepted_push_without_false_receipt(self):
        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        admit = Mock()
        self.process(Mock(side_effect=ValueError("capacity requires recovery")), admit)
        admit.assert_not_called()
        self.assertEqual(len(list(self.inbox.iterdir())), 1)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_uncertain_source_cleanup_propagates_through_capacity_and_retains_push(self):
        from lib.build_setup_sources import SourceCleanupUncertain

        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        uncertain = []

        @contextmanager
        def slot():
            try:
                yield
            except SourceCleanupUncertain:
                uncertain.append(True)
                raise

        self.process(slot, Mock(side_effect=SourceCleanupUncertain("inspect processes")))
        self.assertEqual(uncertain, [True])
        self.assertEqual(len(list(self.inbox.iterdir())), 1)

    def test_source_refusal_retains_push_and_receipt_for_inspection(self):
        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        self.prepare.side_effect = ValueError("confirmed source refusal")
        admit = service.admit_event
        self.process(nullcontext, lambda event, settings, **kwargs: admit(
            event, settings, host=self.host, receipts=str(self.receipts),
            preparing=str(self.preparing), prepare=self.prepare))
        self.assertEqual(len(list(self.inbox.iterdir())), 1)
        receipt, = self.receipts.iterdir()
        self.assertEqual(json.loads(receipt.read_text())["state"], "recovery_required")

    def test_capacity_refusals_retry_exact_prepared_input_without_fetch(self):
        for refusal in ("input", "queue"):
            with self.subTest(refusal=refusal):
                event = envelope(extra=refusal)
                if refusal == "input":
                    self.host["max_bytes"] = 1
                    first = self.admit(event)
                    self.host["max_bytes"] = 64 * 1024**3
                else:
                    with patch("lib.build_jobs.BuildJobStore", side_effect=lambda *args:
                               BuildJobStore(*args, max_queue_length=1)):
                        first = self.admit(event)
                self.assertEqual(first["state"], "retry_pending")
                retained = Path(self.preparing, first["request_id"] + ".source").read_bytes()
                # Moving policy/branch data cannot regenerate the prepared request.
                self.manifest["ci"]["test"] = [{"argv": ["false"]}]
                result = self.admit(event)
                self.assertEqual(result["state"], "submitted")
                self.assertEqual(result["request"], first["request"])
                self.assertEqual(Path(self.host["roots"]["inputs"], result["request_id"] + ".source").read_bytes(), retained)
        self.assertEqual(self.prepare.call_count, 2)

    def test_response_loss_requires_explicit_exact_idempotent_recovery(self):
        original = BuildJobStore.submit

        def lost_response(store, *args, **kwargs):
            original(store, *args, **kwargs)
            raise OSError("response lost after durable core submission")

        with patch.object(BuildJobStore, "submit", lost_response), self.assertRaises(OSError):
            self.admit()
        for _ in range(2):
            self.assertEqual(self.admit()["state"], "recovery_required")
        result = self.admit(recover=True)
        self.assertEqual(result["state"], "submitted")
        ledger = json.loads(Path(self.host["roots"]["state"], "jobs.json").read_text())
        self.assertEqual(len(ledger["jobs"]), 1)
        self.prepare.assert_called_once()

    def test_malformed_and_abandoned_entries_do_not_block_valid_push(self):
        from lib.atomic_io import write_json_atomic

        for index, field in enumerate(("body", "signature", "delivery", "event")):
            value = envelope()
            value[field] = None
            write_json_atomic(str(self.inbox / (f"{index:032x}.json")), value)
        (self.inbox / "unexpected").write_text("junk")
        (self.inbox / ".unfinished.json-temporary").write_text("partial")
        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        admit = service.admit_event
        self.process(nullcontext, lambda event, settings, **kwargs: admit(
            event, settings, host=self.host, receipts=str(self.receipts),
            preparing=str(self.preparing), prepare=self.prepare))
        self.assertEqual(list(self.inbox.iterdir()), [])
        self.prepare.assert_called_once()

    def test_pending_capacity_push_survives_until_exact_retry_succeeds(self):
        service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))
        admit = service.admit_event
        callback = lambda event, settings, **kwargs: admit(
            event, settings, host=self.host, receipts=str(self.receipts),
            preparing=str(self.preparing), prepare=self.prepare)
        self.host["max_bytes"] = 1
        self.process(nullcontext, callback)
        self.assertEqual(len(list(self.inbox.iterdir())), 1)
        receipt, = self.receipts.iterdir()
        self.assertEqual(json.loads(receipt.read_text())["state"], "retry_pending")
        self.host["max_bytes"] = 64 * 1024**3
        self.process(nullcontext, callback)
        self.assertEqual(list(self.inbox.iterdir()), [])
        self.prepare.assert_called_once()

    def test_cleanup_waits_for_receiver_atomic_publication(self):
        original = service.write_json_atomic
        staged = threading.Event()
        release = threading.Event()
        processing = threading.Event()
        errors = []

        def checked(callback):
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)

        def writing(path, value, **kwargs):
            temporary = Path(self.inbox, ".active-write")
            temporary.write_text("partial")
            staged.set()
            self.assertTrue(release.wait(5))
            self.assertTrue(temporary.exists())
            original(path, value, **kwargs)
            temporary.unlink()

        def process():
            processing.set()
            self.process(nullcontext, Mock(return_value={"state": "submitted"}))

        with patch.object(service, "write_json_atomic", side_effect=writing):
            writer = threading.Thread(target=lambda: checked(
                lambda: service.retain_event(envelope(), SETTINGS, inbox=str(self.inbox))))
            reader = threading.Thread(target=lambda: checked(process))
            writer.start()
            try:
                self.assertTrue(staged.wait(5))
                reader.start()
                self.assertTrue(processing.wait(5))
            finally:
                release.set()
                writer.join(5)
                if reader.ident is not None:
                    reader.join(5)
        self.assertFalse(writer.is_alive() or reader.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_http_authentication_and_bounded_framing(self):
        server = HTTPServer(("127.0.0.1", 0), service.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        retain = service.retain_event
        with patch.object(service, "load_settings", return_value=SETTINGS), patch.object(
                service, "retain_event", side_effect=lambda event, settings:
                retain(event, settings, inbox=str(self.inbox))):
            thread.start()
            try:
                for signature, expected in ((envelope()["signature"], 202), ("sha256=" + "0" * 64, 403)):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                    connection.request("POST", WEBHOOK_PATH, base64.b64decode(envelope()["body"]), {
                        "X-Hub-Signature-256": signature, "X-GitHub-Delivery": "http-fixture",
                        "X-GitHub-Event": "push", "Content-Type": "application/json"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                    connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_public_flags_and_secrets_use_private_transport_without_saved_credentials(self):
        config = SetupConfig(host="builds.example.com", username="operator", system_type="server_lite",
                             machine_type="vm", is_build_server=True, enable_webhook_builds=True,
                             enable_ssl=True, webhook_domain="builds.example.com",
                             webhook_repositories=["example/project"], webhook_secret=SECRET,
                             webhook_github_token="fixture-token")
        self.assertTrue(config.install_node and config.install_go and config.install_python)
        self.assertEqual(config.effective_web_ports(), [80, 443])
        self.assertNotIn(SECRET, str(config.to_dict()))
        self.assertNotIn("fixture-token", str(config.to_dict()))
        self.assertNotIn(SECRET, config.to_setup_command())
        self.assertIn(SECRET, " ".join(config.to_remote_args()))
        for branch in (".hidden", "main/.hidden", "main.lock", "main..x", "--option"):
            bad = copy.copy(config)
            bad.webhook_branches = [branch]
            from lib.webhook_builds import validate_webhook_settings

            with self.assertRaises(ValueError):
                validate_webhook_settings(bad)


if __name__ == "__main__":
    unittest.main()
