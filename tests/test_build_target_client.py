from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from lib import build_client, build_target_client
from lib.build_admission import MAX_OPERATIONS, DeploymentAdmissionStore
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS, request_digest
from lib.build_target_client import (
    BuildTargetClient,
    REMOTE_COMMAND,
    TargetOperationUnconfirmed,
    TargetProtocolError,
    TargetRemoteError,
    TargetTransportError,
)
from lib.build_target_service import BuildTargetService, MAX_HEADER_LINE_BYTES, MAX_RESPONSE_LINE_BYTES
from tests.test_build_client import FakePopen, build_request, error, success, write_all


OPERATION = "b" * 32
KEY = "c" * 32


def deployment() -> dict:
    return {
        "schema": 1, "application": "app", "project": "site",
        "platform": dict(DEBIAN_PLATFORM),
        "components": [{
            "name": "site", "type": "static", "domain": "site.example",
            "path": "/", "artifacts": ["dist"], "output": "dist", "spa": False,
        }],
    }


def preflight() -> dict:
    return {
        "application": "app", "project": "site", "platform": dict(DEBIAN_PLATFORM),
        "limits": {key: DEFAULT_LIMITS[key] for key in ("artifact_bytes", "artifact_entries")},
        "routes": [{"name": "site", "type": "static", "domain": "site.example", "path": "/"}],
    }


def admission() -> dict:
    request = build_request()
    return {
        "operation_id": OPERATION, "application": "app", "project": "site", "generation": 1,
        "input": {"request_id": request["request_id"], "request_sha256": request_digest(request)},
        "bindings_sha256": "d" * 64, "source": request["source"],
        "created_at": 100, "expires_at": 200, "state": "reserved",
        "effective_status": "reserved", "superseded": False, "expired": False,
    }


def acknowledgement(action: str, fields: dict) -> dict:
    result = {key: fields[key] for key in ("operation_id", "generation", "artifact_sha256")}
    if action == "stage":
        result.update(status="staged", artifact_size=fields["artifact_size"])
    else:
        result.update(status="completed", application=fields["deployment"]["application"])
    return result


def server(handler):
    def run(input_stream, output, stderr, stop):
        header = input_stream.readline(MAX_HEADER_LINE_BYTES + 1)
        if not header.endswith(b"\n"):
            raise ValueError("Missing target header")
        fields = json.loads(header)
        body = bytearray()
        remaining = fields.get("artifact_size", 0) if fields["action"] == "stage" else 0
        while remaining:
            block = input_stream.read(min(65536, remaining))
            if not block:
                raise ValueError("Truncated target upload")
            remaining -= len(block)
            body.extend(block)
        if input_stream.read(1):
            raise ValueError("Trailing target upload")
        return handler(fields, bytes(body), output, stderr, stop)
    return run


class BuildTargetClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identity = self.root / "key"
        self.identity.write_bytes(b"private fixture")
        self.identity.chmod(0o600)
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_bytes(b"target.example ssh-ed25519 fixture\n")
        self.known_hosts.chmod(0o600)
        self.artifact = self.root / "artifact"
        self.body = bytes(range(251)) * 800
        self.artifact.write_bytes(self.body)
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.client = self.make_client()
        self.addCleanup(self.client.close)

    def make_client(self, **overrides) -> BuildTargetClient:
        fields = dict(host="target.example", username="controller", port=2222,
                      identity_file=self.identity, known_hosts_file=self.known_hosts, timeout=2)
        return BuildTargetClient(**(fields | overrides))

    def fields(self) -> dict:
        return dict(operation_id=OPERATION, generation=1, request=build_request(),
                    deployment=deployment(), artifact_sha256=self.digest)

    def call(self, action: str):
        if action == "reserve":
            return self.client.reserve(idempotency_key=KEY, application="app", request=build_request(),
                                       deployment=deployment(), ttl_seconds=100)
        if action == "stage":
            return self.client.stage(**self.fields(), artifact_path=self.artifact,
                                     artifact_size=len(self.body))
        if action == "activate":
            return self.client.activate(**self.fields())
        return getattr(self.client, action)("app" if action == "preflight" else OPERATION)

    def run_fake(self, fake, call):
        processes = []
        def spawn(arguments, **options):
            process = fake(arguments, **options)
            processes.append(process)
            return process
        def stop_fake(_pid, _signal):
            processes[0].terminate()
            raise ProcessLookupError
        with patch.object(build_client.subprocess, "Popen", side_effect=spawn), \
                patch.object(build_client.os, "killpg", side_effect=stop_fake):
            return call()

    def invoke(self, raw: bytes, action="status", *, returncode=0, stderr=b""):
        def handler(_fields, _body, output, error_stream, _stop):
            write_all(error_stream, stderr)
            write_all(output, raw)
            return returncode
        fake = FakePopen(server(handler))
        return self.run_fake(fake, lambda: self.call(action)), fake

    def test_constructor_validates_explicit_trust_and_deadline(self) -> None:
        for override in ({"host": "bad host"}, {"username": "bad user"}, {"port": True},
                         {"port": 0}, {"timeout": True}, {"timeout": 0}, {"timeout": 901},
                         {"timeout": float("inf")}, {"timeout": float("nan")},
                         {"identity_file": "relative"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.make_client(**override)
        for name in ("identity_file", "known_hosts_file"):
            path = self.identity if name == "identity_file" else self.known_hosts
            link = self.root / (name + "-link")
            link.symlink_to(path)
            with self.subTest(name=name), self.assertRaises(OSError):
                self.make_client(**{name: link})
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                self.make_client(**{name: path})
            path.chmod(0o600)

    def test_exact_command_restrictions_and_fresh_pinned_fds(self) -> None:
        calls = []
        paths = []
        fake = FakePopen(server(lambda _fields, _body, output, _error, _stop:
                               write_all(output, success(preflight())) or 0))

        def spawn(arguments, **options):
            current = [arguments[arguments.index("-i") + 1], next(
                arg.split("=", 1)[1] for arg in arguments if arg.startswith("UserKnownHostsFile="))]
            for descriptor_path, configured in zip(current, (self.identity, self.known_hosts)):
                self.assertRegex(descriptor_path, rf"^/proc/{os.getpid()}/fd/\d+$")
                self.assertEqual(os.stat(descriptor_path).st_ino, configured.stat().st_ino)
            paths.extend(current)
            calls.append(list(arguments))
            self.assertNotIn("pass_fds", options)
            self.assertTrue(options["close_fds"])
            self.assertTrue(options["start_new_session"])
            self.assertEqual(options["env"], {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                                               "LANG": "C", "LC_ALL": "C"})
            return fake(arguments, **options)

        with patch.object(build_client.os, "dup", wraps=os.dup) as duplicate:
            for _ in range(2):
                self.run_fake(spawn, lambda: self.client.preflight("app"))
                for path in paths:
                    with self.assertRaises(FileNotFoundError):
                        os.stat(path)
            self.assertEqual(duplicate.call_count, 4)
        for arguments in calls:
            self.assertEqual(arguments[-2:], ["controller@target.example", REMOTE_COMMAND])
            self.assertEqual(arguments[:3], ["/usr/bin/ssh", "-F", "/dev/null"])
            for option in ("StrictHostKeyChecking=yes", "GlobalKnownHostsFile=/dev/null",
                           "IdentityFile=none", "IdentityAgent=none", "IdentitiesOnly=yes",
                           "BatchMode=yes", "ForwardAgent=no", "CertificateFile=none",
                           "PasswordAuthentication=no", "KbdInteractiveAuthentication=no",
                           "RequestTTY=no", "ClearAllForwardings=yes", "ProxyCommand=none",
                           "ProxyJump=none", "ControlMaster=no", "ControlPath=none",
                           "ControlPersist=no", "PermitLocalCommand=no", "CanonicalizeHostname=no"):
                self.assertIn(option, arguments)
            self.assertNotIn(str(self.identity), arguments)
            self.assertNotIn(str(self.known_hosts), arguments)

    def test_all_six_actions_have_exact_wire_fields_and_public_results(self) -> None:
        seen = []
        def handler(fields, body, output, _stderr, _stop):
            action = fields["action"]
            seen.append((fields, body))
            if action == "preflight":
                result = preflight()
            elif action in {"reserve", "status", "cancel"}:
                result = admission()
                if action == "cancel":
                    result.update(state="cancelled", effective_status="cancelled")
            else:
                result = acknowledgement(action, fields)
            write_all(output, success(result))
            return 0
        for action in ("preflight", "reserve", "status", "cancel", "stage", "activate"):
            fake = FakePopen(server(handler))
            result = self.run_fake(fake, lambda: self.call(action))
            self.assertIsNone(fake.process.error)
            self.assertEqual(fake.calls, 1)
            self.assertNotIn("owner", result)
        expected = [
            {"application": "app"},
            {"idempotency_key": KEY, "application": "app", "request": build_request(),
             "deployment": deployment(), "ttl_seconds": 100},
            {"operation_id": OPERATION}, {"operation_id": OPERATION},
            self.fields() | {"artifact_size": len(self.body)}, self.fields(),
        ]
        for (fields, body), expected_fields in zip(seen, expected):
            self.assertEqual(fields, {"schema": 1, "action": fields["action"], **expected_fields})
            self.assertEqual(body, self.body if fields["action"] == "stage" else b"")
            self.assertNotIn(str(self.artifact), json.dumps(fields))

    def test_strict_response_framing_schema_and_no_payload(self) -> None:
        valid = success(admission())
        malformed = [
            valid + b"x", valid + b"\n", valid[:-1], b"", b"x" * MAX_RESPONSE_LINE_BYTES + b"\n",
            valid.replace(b'"schema":1', b'"schema":true'),
            valid.replace(b'"schema":1', b'"schema":1,"schema":1'),
            valid.replace(b'"ok":true', b'"ok":1'),
            valid.replace(b'"payload_bytes":0', b'"payload_bytes":true'),
            valid.replace(b'"payload_bytes":0', b'"payload_bytes":1') + b"x",
            valid.replace(b'"created_at":100', b'"created_at":NaN'),
            valid.replace(b"\n", b"\r\n"), b"\xff\n", success(None),
            error("private server exception"),
            error("rejected").replace(b'"error":"rejected"', b'"error":[]'),
            valid.replace(b'"schema":1', b'"secret":"do-not-leak","schema":1'),
        ]
        for index, raw in enumerate(malformed):
            with self.subTest(index=index), self.assertRaises(TargetProtocolError) as caught:
                self.invoke(raw)
            self.assertNotIn("do-not-leak", str(caught.exception))

    def test_maximum_response_line_accepts_fragments_and_requires_eof(self) -> None:
        line = success(admission())
        line = line[:-1] + b" " * (MAX_RESPONSE_LINE_BYTES - len(line)) + b"\n"
        self.assertEqual(len(line), MAX_RESPONSE_LINE_BYTES)
        observed = threading.Event()
        def handler(_fields, _body, output, _stderr, _stop):
            write_all(output, line[:17])
            if not observed.wait(1):
                raise TimeoutError("Missing client read")
            write_all(output, line[17:])
            return 0
        fake = FakePopen(server(handler))
        actual_read = os.read
        def read(descriptor, size):
            block = actual_read(descriptor, size)
            if fake.process is not None and descriptor == fake.process.stdout.fileno() and block:
                observed.set()
            return block
        with patch.object(build_client.os, "read", side_effect=read):
            self.assertEqual(self.run_fake(fake, lambda: self.call("status")), admission())
        self.assertIsNone(fake.process.error)
        with self.assertRaises(TargetProtocolError):
            self.invoke(error("rejected") + b"trailing private data")

    def test_admission_rejects_wrong_types_keys_identity_and_cross_fields(self) -> None:
        variants = [
            {"operation_id": "e" * 32}, {"operation_id": "a" * 32},
            {"owner": "private"}, {"generation": True}, {"generation": MAX_OPERATIONS + 1},
            {"created_at": True}, {"created_at": -1}, {"expires_at": 100},
            {"expires_at": float("inf")}, {"bindings_sha256": "not-a-digest"},
            {"input": {"request_id": "a" * 32, "request_sha256": "e" * 64, "env": {}}},
            {"source": {"revision": "1" * 40}},
            {"source": build_request()["source"] | {"revision": "bad"}},
            {"state": []}, {"state": "expired"}, {"superseded": 0}, {"expired": 1},
            {"state": "activating"}, {"superseded": True}, {"expired": True},
            {"effective_status": []},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(TargetProtocolError):
                self.invoke(success(admission() | variant))
        for missing in admission():
            value = admission()
            del value[missing]
            with self.subTest(missing=missing), self.assertRaises(TargetProtocolError):
                self.invoke(success(value))

    def test_admission_state_contract_accepts_expiry_supersession_and_completion(self) -> None:
        for state in ("reserved", "cancelled", "activating", "uncertain", "completed"):
            for superseded, expired in ((False, False), (False, True), (True, False), (True, True)):
                effective = "uncertain" if state == "activating" else state
                if state == "reserved":
                    effective = "superseded" if superseded else "expired" if expired else "reserved"
                value = admission() | dict(state=state, effective_status=effective,
                                           superseded=superseded, expired=expired)
                with self.subTest(state=state, superseded=superseded, expired=expired):
                    result, _ = self.invoke(success(value))
                    self.assertEqual(result, value)

    def test_reserve_binds_application_project_input_and_source(self) -> None:
        for variant in ({"application": "other"}, {"project": "other"},
                        {"input": {"request_id": "e" * 32, "request_sha256": "e" * 64}},
                        {"input": admission()["input"] | {"request_sha256": "e" * 64}},
                        {"source": build_request()["source"] | {"snapshot_sha256": "e" * 64}}):
            with self.subTest(variant=variant), self.assertRaises(TargetOperationUnconfirmed) as caught:
                self.invoke(success(admission() | variant), "reserve")
            self.assertEqual(caught.exception.idempotency_key, KEY)
            self.assertIsNone(caught.exception.operation_id)
            self.assertIn("same idempotency key", str(caught.exception))
        # A recovered reservation need not still be live.
        value = admission() | dict(state="completed", effective_status="completed", expired=True)
        self.assertEqual(self.invoke(success(value), "reserve")[0], value)

    def test_preflight_rejects_wrong_application_routes_limits_platform_and_private_fields(self) -> None:
        for variant in ({"application": "other"}, {"deployment_root": "/private"},
                        {"routes": []}, {"routes": preflight()["routes"] * 2},
                        {"routes": [preflight()["routes"][0] | {"owner": "private"}]},
                        {"routes": [preflight()["routes"][0] | {"domain": "bad host"}]},
                        {"routes": [preflight()["routes"][0] | {"type": "unknown"}]},
                        {"limits": {"artifact_bytes": True, "artifact_entries": 1}},
                        {"platform": DEBIAN_PLATFORM | {"os": "other"}}):
            with self.subTest(variant=variant), self.assertRaises(TargetProtocolError):
                self.invoke(success(preflight() | variant), "preflight")

    def test_stage_and_activate_require_exact_ack_echoes(self) -> None:
        for action in ("stage", "activate"):
            fields = self.fields() | {"artifact_size": len(self.body)}
            expected = acknowledgement(action, fields)
            variants = [{"operation_id": "e" * 32}, {"generation": 2}, {"generation": True},
                        {"artifact_sha256": "e" * 64}, {"status": "reserved"}, {"private": "leak"}]
            variants += ([{"artifact_size": len(self.body) + 1}, {"artifact_size": True}]
                         if action == "stage" else [{"application": "other"}])
            for variant in variants:
                with self.subTest(action=action, variant=variant), \
                        self.assertRaises(TargetOperationUnconfirmed) as caught:
                    self.invoke(success(expected | variant), action)
                self.assertEqual(caught.exception.operation_id, OPERATION)
                self.assertIn("same operation ID", str(caught.exception))

    def test_every_mutating_remote_rejection_disconnect_and_bad_exit_is_unconfirmed(self) -> None:
        for action in ("reserve", "cancel", "stage", "activate"):
            for raw, returncode, code in ((error("rejected"), 0, "rejected"),
                                          (error("unavailable"), 0, "unavailable"),
                                          (b"", 0, None), (error("rejected"), 255, None)):
                with self.subTest(action=action, code=code, returncode=returncode), \
                        self.assertRaises(TargetOperationUnconfirmed) as caught:
                    self.invoke(raw, action, returncode=returncode)
                self.assertEqual(caught.exception.action, action)
                self.assertEqual(caught.exception.error_code, code)
                self.assertEqual(caught.exception.idempotency_key, KEY if action == "reserve" else None)
                self.assertEqual(caught.exception.operation_id, None if action == "reserve" else OPERATION)
        with self.assertRaises(TargetRemoteError) as caught:
            self.invoke(error("rejected"))
        self.assertEqual(caught.exception.code, "rejected")
        with self.assertRaises(TargetTransportError):
            self.invoke(success(admission()), returncode=255)

    def test_cancel_requires_cancelled_state(self) -> None:
        with self.assertRaises(TargetOperationUnconfirmed):
            self.invoke(success(admission()), "cancel")

    def test_upload_reads_and_writes_bounded_chunks_and_checks_full_hash_twice(self) -> None:
        seen = []
        actual_read = os.read
        actual_write = os.write
        def read(descriptor, size):
            seen.append(size)
            return actual_read(descriptor, size)
        def write(descriptor, data):
            self.assertLessEqual(len(data), 65536)
            return actual_write(descriptor, data)
        fields = self.fields() | {"artifact_size": len(self.body)}
        with patch.object(build_client.os, "read", side_effect=read), \
                patch.object(build_client.os, "write", side_effect=write):
            result, fake = self.invoke(success(acknowledgement("stage", fields)), "stage")
        self.assertEqual(result["artifact_size"], len(self.body))
        self.assertLessEqual(max(seen), 65536)
        self.assertGreaterEqual(seen.count(65536), 6)
        self.assertIsNone(fake.process.error)

    def test_upload_prestart_hash_size_type_symlink_and_input_failures(self) -> None:
        with patch.object(build_client.subprocess, "Popen") as spawn:
            for fields in (self.fields() | {"artifact_sha256": "f" * 64}, self.fields()):
                size = len(self.body) if fields["artifact_sha256"] != self.digest else len(self.body) + 1
                with self.assertRaises(ValueError):
                    self.client.stage(**fields, artifact_path=self.artifact, artifact_size=size)
            link = self.root / "artifact-link"
            link.symlink_to(self.artifact)
            for path in (link, self.root):
                with self.assertRaises((OSError, ValueError)):
                    self.client.stage(**self.fields(), artifact_path=path, artifact_size=len(self.body))
            for key, bad in (("generation", True), ("generation", 0), ("operation_id", "bad"),
                             ("artifact_sha256", "bad")):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.client.activate(**(self.fields() | {key: bad}))
            with self.assertRaises(ValueError):
                self.client.reserve(idempotency_key=KEY, application="other", request=build_request(),
                                    deployment=deployment(), ttl_seconds=100)
            with self.assertRaises(ValueError):
                self.client.reserve(idempotency_key=KEY, application="app", request=build_request(),
                                    deployment=deployment() | {"owner": "private"}, ttl_seconds=100)
            spawn.assert_not_called()

    def test_upload_prehash_timeout_and_interrupt_never_start_ssh(self) -> None:
        with patch.object(build_client.subprocess, "Popen") as spawn:
            with patch.object(build_target_client, "time", SimpleNamespace(monotonic=lambda: 0)), \
                    patch.object(build_client, "time", SimpleNamespace(monotonic=lambda: 3)):
                with self.assertRaises(TargetTransportError) as caught:
                    self.call("stage")
                self.assertFalse(caught.exception.transport_started)
            with patch.object(build_client.os, "read", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.call("stage")
            spawn.assert_not_called()

    def test_upload_prehash_short_read_growth_and_path_mutation_close_source_before_ssh(self) -> None:
        actual_read = os.read
        for change in ("short", "growth", "replace"):
            self.artifact.write_bytes(self.body)
            inode = self.artifact.stat().st_ino
            source_fds = set()
            total = 0
            def read(descriptor, size):
                nonlocal total
                if os.fstat(descriptor).st_ino != inode:
                    return actual_read(descriptor, size)
                source_fds.add(descriptor)
                if change == "short" and total:
                    return b""
                block = actual_read(descriptor, size)
                total += len(block)
                if total == len(self.body) and block:
                    if change == "growth":
                        with self.artifact.open("ab") as output:
                            output.write(b"extra")
                    elif change == "replace":
                        other = self.root / "replacement"
                        other.write_bytes(self.body)
                        other.replace(self.artifact)
                return block
            with self.subTest(change=change), \
                    patch.object(build_client.subprocess, "Popen") as spawn, \
                    patch.object(build_client.os, "read", side_effect=read):
                with self.assertRaises(ValueError):
                    self.call("stage")
                spawn.assert_not_called()
            for descriptor in source_fds:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_stream_digest_change_with_unchanged_file_identity_is_unconfirmed(self) -> None:
        actual_read = os.read
        active = False
        altered = False
        inode = self.artifact.stat().st_ino
        fields = self.fields() | {"artifact_size": len(self.body)}
        fake = FakePopen(server(lambda _fields, _body, output, _stderr, _stop:
                               write_all(output, success(acknowledgement("stage", fields))) or 0))
        def spawn(arguments, **options):
            nonlocal active
            active = True
            return fake(arguments, **options)
        def read(descriptor, size):
            nonlocal altered
            block = actual_read(descriptor, size)
            if active and not altered and block and os.fstat(descriptor).st_ino == inode:
                altered = True
                return bytes([block[0] ^ 1]) + block[1:]
            return block
        with patch.object(build_client.os, "read", side_effect=read):
            with self.assertRaises(TargetOperationUnconfirmed):
                self.run_fake(spawn, lambda: self.call("stage"))
        self.assertTrue(altered)
        self.assertEqual(self.artifact.read_bytes(), self.body)

    def test_upload_replacement_and_same_size_mutation_after_prehash_are_unconfirmed(self) -> None:
        for replace in (False, True):
            self.artifact.write_bytes(self.body)
            fake = FakePopen(server(lambda fields, _body, output, _error, _stop:
                                   write_all(output, success(acknowledgement("stage", fields))) or 0))
            def spawn(arguments, **options):
                if replace:
                    other = self.root / "other"
                    other.write_bytes(self.body)
                    other.replace(self.artifact)
                else:
                    self.artifact.write_bytes(b"x" * len(self.body))
                return fake(arguments, **options)
            # Mutation causes a local input failure after EOF; the peer's
            # plausible successful acknowledgement must never mask it.
            with self.subTest(replace=replace), self.assertRaises(TargetOperationUnconfirmed):
                self.run_fake(spawn, lambda: self.call("stage"))
            self.assertEqual(fake.calls, 1)

    def test_credentials_changed_or_closed_client_fail_before_ssh(self) -> None:
        with patch.object(build_client.subprocess, "Popen") as spawn:
            self.identity.write_bytes(b"changed")
            with self.assertRaises(TargetTransportError) as caught:
                self.call("reserve")
            self.assertFalse(caught.exception.transport_started)
            self.client.close()
            self.client.close()
            with self.assertRaises(ValueError):
                self.call("activate")
            spawn.assert_not_called()

    def test_source_changed_before_spawn_is_plain_and_never_starts_ssh(self) -> None:
        duplicate = os.dup
        def mutate(descriptor):
            self.artifact.write_bytes(b"changed before spawn")
            return duplicate(descriptor)
        with patch.object(build_client.os, "dup", side_effect=mutate), \
                patch.object(build_client.subprocess, "Popen") as spawn:
            with self.assertRaises(TargetTransportError) as caught:
                self.call("stage")
            self.assertFalse(caught.exception.transport_started)
            spawn.assert_not_called()

    def test_cleanup_interrupt_is_unconfirmed_and_closes_every_credential_and_pipe(self) -> None:
        selector_type = build_client.selectors.DefaultSelector
        class CleanupInterruptSelector:
            def __init__(self):
                self.inner = selector_type()
            def register(self, *args):
                return self.inner.register(*args)
            def unregister(self, *args):
                return self.inner.unregister(*args)
            def select(self, timeout):
                return self.inner.select(timeout)
            def close(self):
                self.inner.close()
                raise KeyboardInterrupt
        for action in ("reserve", "cancel", "stage", "activate"):
            def handler(fields, _body, output, _stderr, _stop):
                if action in {"stage", "activate"}:
                    result = acknowledgement(action, fields)
                else:
                    result = admission()
                    if action == "cancel":
                        result.update(state="cancelled", effective_status="cancelled")
                write_all(output, success(result))
                return 0
            fake = FakePopen(server(handler))
            with self.subTest(action=action), \
                    patch.object(build_client.selectors, "DefaultSelector", CleanupInterruptSelector):
                with self.assertRaises(TargetOperationUnconfirmed):
                    self.run_fake(fake, lambda: self.call(action))
            for stream in (fake.process.stdin, fake.process.stdout, fake.process.stderr):
                self.assertTrue(stream.closed)
            paths = [fake.arguments[fake.arguments.index("-i") + 1], next(
                arg.split("=", 1)[1] for arg in fake.arguments if arg.startswith("UserKnownHostsFile="))]
            for path in paths:
                with self.assertRaises(FileNotFoundError):
                    os.stat(path)

    def test_poststart_interrupt_and_timeout_keep_identity_and_fds_through_cleanup(self) -> None:
        for action in ("reserve", "cancel", "stage", "activate"):
            for interrupt in (False, True):
                client = self.make_client(timeout=0.04 if not interrupt else 2)
                self.addCleanup(client.close)
                self.client = client
                admitted = threading.Event()
                paths = []
                def handler(_fields, _body, _output, _error, stop):
                    admitted.set()
                    stop.wait(2)
                    return 0
                fake = FakePopen(server(handler))
                def spawn(arguments, **options):
                    paths.extend([arguments[arguments.index("-i") + 1], next(
                        arg.split("=", 1)[1] for arg in arguments
                        if arg.startswith("UserKnownHostsFile="))])
                    # Closing the client cannot close this call's duplicate fds.
                    client.close()
                    return fake(arguments, **options)
                selector_type = build_client.selectors.DefaultSelector
                class InterruptSelector:
                    def __init__(self):
                        self.inner = selector_type()
                    def register(self, *args):
                        return self.inner.register(*args)
                    def unregister(self, *args):
                        return self.inner.unregister(*args)
                    def close(self):
                        self.inner.close()
                    def select(self, timeout):
                        events = self.inner.select(timeout)
                        if admitted.is_set():
                            raise KeyboardInterrupt
                        return events
                def stop_fake(_pid, _signal):
                    for path in paths:
                        os.stat(path)
                    fake.process.terminate()
                    raise ProcessLookupError
                with self.subTest(action=action, interrupt=interrupt), \
                        patch.object(build_client.subprocess, "Popen", side_effect=spawn), \
                        patch.object(build_client.os, "killpg", side_effect=stop_fake), \
                        patch.object(build_client.selectors, "DefaultSelector",
                                     InterruptSelector if interrupt else selector_type):
                    with self.assertRaises(TargetOperationUnconfirmed) as caught:
                        self.call(action)
                    self.assertEqual(caught.exception.operation_id, None if action == "reserve" else OPERATION)
                    self.assertEqual(caught.exception.idempotency_key, KEY if action == "reserve" else None)
                self.assertIsNotNone(fake.process.wait(timeout=1))
                for path in paths:
                    with self.assertRaises(FileNotFoundError):
                        os.stat(path)

    def test_prestart_spawn_interrupt_and_failure_do_not_claim_submission(self) -> None:
        for action in ("reserve", "cancel", "stage", "activate"):
            for failure in (KeyboardInterrupt(), OSError("private details")):
                with self.subTest(action=action, failure=type(failure)), \
                        patch.object(build_client.subprocess, "Popen", side_effect=failure):
                    with self.assertRaises(KeyboardInterrupt if isinstance(failure, KeyboardInterrupt)
                                           else TargetTransportError) as caught:
                        self.call(action)
                    self.assertNotIsInstance(caught.exception, TargetOperationUnconfirmed)
                    self.assertNotIn("private details", str(caught.exception))

    def test_stderr_is_drained_bounded_and_private_even_on_error(self) -> None:
        captured = []
        capture = build_client._capture_stderr
        def observe(buffer, block):
            capture(buffer, block)
            captured.append(len(buffer))
        with patch.object(build_client, "_capture_stderr", side_effect=observe):
            with self.assertRaises(TargetRemoteError) as caught:
                self.invoke(error("unavailable"), stderr=b"private diagnostic" * 10000)
        self.assertLessEqual(max(captured), 65536)
        self.assertNotIn("private diagnostic", str(caught.exception))

    def test_real_service_round_trip_and_cached_completed_activation(self) -> None:
        state = self.root / "state"
        state.mkdir(mode=0o700)
        clock = [100.0]
        admissions = DeploymentAdmissionStore(state / "admission.json", clock=lambda: clock[0])
        grant = preflight()
        policy = {"schema": 1, "applications": {"app": {
            "project": "site", "principals": ["controller"], "deployment_root": "/srv/app",
            "platform": grant["platform"], "limits": grant["limits"],
            "components": {"site": {key: grant["routes"][0][key] for key in ("type", "domain", "path")}},
        }}}
        activate = Mock(return_value={"private": "not public"})
        stage = Mock(side_effect=lambda *_args, **kwargs: self.assertEqual(kwargs["source"].read(), self.body))
        service = BuildTargetService(admissions=admissions, load_policy=lambda: policy,
                                     stage_artifact=stage, activate_release=activate)
        def remote(input_stream, output, _stderr, _stop):
            service.serve_once(principal="controller", input_stream=input_stream, output_stream=output)
            return 0
        def call(method):
            return self.run_fake(FakePopen(remote), method)
        self.assertEqual(call(lambda: self.client.preflight("app")), preflight())
        reserved = call(lambda: self.call("reserve"))
        fields = self.fields() | {"operation_id": reserved["operation_id"], "generation": reserved["generation"]}
        call(lambda: self.client.stage(**fields, artifact_path=self.artifact, artifact_size=len(self.body)))
        expected = acknowledgement("activate", fields)
        self.assertEqual(call(lambda: self.client.activate(**fields)), expected)
        clock[0] = 1000  # The completed acknowledgement survives expiry.
        self.assertEqual(call(lambda: self.client.activate(**fields)), expected)
        self.assertEqual(activate.call_count, 1)
        self.assertEqual(stage.call_count, 1)
        self.assertTrue(call(lambda: self.client.status(fields["operation_id"]))["expired"])


if __name__ == "__main__":
    unittest.main()
