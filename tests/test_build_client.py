from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from lib import build_client
from lib.build_client import (
    BuildHostClient,
    BuildHostProtocolError,
    BuildHostTransportError,
    BuildLogChunk,
    BuildSubmissionUnconfirmed,
    REMOTE_COMMAND,
)
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import DEFAULT_LIMITS
from lib.build_source import MAX_SNAPSHOT_BYTES
from lib.build_host_service import MAX_HEADER_LINE_BYTES, MAX_LOG_CHUNK_BYTES, MAX_RESPONSE_LINE_BYTES


def build_request(request_id: str = "a" * 32, *, snapshot_sha256: str = "2" * 64) -> dict:
    return {
        "schema": 1,
        "request_id": request_id,
        "project": "site",
        "source": {
            "revision": "1" * 40,
            "snapshot_sha256": snapshot_sha256,
            "manifest_sha256": "3" * 64,
        },
        "platform": dict(DEBIAN_PLATFORM),
        "components": [{
            "name": "site",
            "type": "static",
            "artifacts": ["dist"],
            "commands": [],
            "env": {},
        }],
        "limits": dict(DEFAULT_LIMITS),
    }


def job_result(request_id: str, request: dict, *, state: str = "queued") -> dict:
    terminal = state in {"failed", "cancelled", "expired", "uncertain", "succeeded"}
    started = None if state in {"queued", "expired", "cancelled"} else 2.0
    artifact = None
    if state == "succeeded":
        artifact = {
            "sha256": "4" * 64,
            "size": 5,
            "descriptor_sha256": "5" * 64,
            "request_id": request_id,
            "request_sha256": "6" * 64,
            "project": request["project"],
            "source": request["source"],
            "platform": request["platform"],
        }
    return {
        "request_id": request_id,
        "sequence": 1,
        "project": request["project"],
        "source": request["source"],
        "platform": request["platform"],
        "limits": request["limits"],
        "state": state,
        "cancel_requested": state == "cancelled",
        "created_at": 1.0,
        "expires_at": 901.0,
        "started_at": started,
        "finished_at": 3.0 if terminal else None,
        "artifact": artifact,
        "reason": {
            "failed": "build_failed", "cancelled": "cancelled",
            "expired": "queue_expired", "uncertain": "worker_interrupted",
        }.get(state),
    }


def success(result: object, *, payload_bytes: int = 0) -> bytes:
    return json.dumps({
        "schema": 1, "ok": True, "result": result, "payload_bytes": payload_bytes,
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def error(code: str) -> bytes:
    return json.dumps({
        "schema": 1, "ok": False, "error": code, "payload_bytes": 0,
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def write_all(stream, value: bytes) -> None:
    view = memoryview(value)
    while view:
        count = stream.write(view)
        if not count:
            raise BrokenPipeError
        view = view[count:]


class FakeSSHProcess:
    """Pipe-backed mocked SSH process; it never starts ssh or opens a socket."""

    _next_pid = 2**30 + 11

    def __init__(self, handler) -> None:
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        error_read, error_write = os.pipe()
        self.stdin = os.fdopen(input_write, "wb", buffering=0)
        self.stdout = os.fdopen(output_read, "rb", buffering=0)
        self.stderr = os.fdopen(error_read, "rb", buffering=0)
        self._remote_stdin = os.fdopen(input_read, "rb", buffering=0)
        self._remote_stdout = os.fdopen(output_write, "wb", buffering=0)
        self._remote_stderr = os.fdopen(error_write, "wb", buffering=0)
        self._done = threading.Event()
        self._stop = threading.Event()
        self.returncode: int | None = None
        self.error: BaseException | None = None
        self.pid = FakeSSHProcess._next_pid
        FakeSSHProcess._next_pid += 1

        def run() -> None:
            try:
                self.returncode = handler(
                    self._remote_stdin, self._remote_stdout, self._remote_stderr,
                    self._stop,
                )
            except BaseException as exc:
                self.error = exc
                self.returncode = 99
            finally:
                for stream in (self._remote_stdin, self._remote_stdout, self._remote_stderr):
                    try:
                        stream.close()
                    except OSError:
                        pass
                self._done.set()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("mock-ssh", timeout)
        self._thread.join(timeout=0)
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self._stop.set()

    def kill(self) -> None:
        self._stop.set()


class FakePopen:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.arguments: list[str] | None = None
        self.options: dict | None = None
        self.process: FakeSSHProcess | None = None
        self.calls = 0

    def __call__(self, arguments, **options) -> FakeSSHProcess:
        self.calls += 1
        self.arguments = list(arguments)
        self.options = options
        self.process = FakeSSHProcess(self.handler)
        return self.process


def request_from_pipe(input_stream) -> tuple[dict, bytes]:
    line = input_stream.readline(MAX_HEADER_LINE_BYTES + 1)
    if not line.endswith(b"\n"):
        raise ValueError("client did not send a framed header")
    request = json.loads(line)
    payload = b""
    if request["action"] == "submit":
        remaining = request["source_size"]
        chunks = []
        while remaining:
            block = input_stream.read(min(64 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        payload = b"".join(chunks)
        if len(payload) != request["source_size"]:
            raise ValueError("source payload was truncated")
    if input_stream.read(1):
        raise ValueError("unexpected client payload bytes")
    return request, payload


def server(handler):
    """Adapt a request callback to the fake process pipes."""
    def run(input_stream, output_stream, error_stream, stop_event):
        request, payload = request_from_pipe(input_stream)
        return handler(request, payload, output_stream, error_stream, stop_event)
    return run


class BuildHostClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.identity = self.root / "id_ed25519"
        self.identity.write_bytes(b"test private key material")
        os.chmod(self.identity, 0o600)
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.write_text("builder.example.net ssh-ed25519 AAAATESTKEY\n")
        os.chmod(self.known_hosts, 0o600)
        self.client = self.make_client()
        self.addCleanup(self.client.close)
        self.addCleanup(self.temporary.cleanup)

    def make_client(self, *, timeout: int | float = 2) -> BuildHostClient:
        return BuildHostClient(
            host="builder.example.net", username="build-controller", port=2222,
            identity_file=self.identity, known_hosts_file=self.known_hosts, timeout=timeout,
        )

    def invoke(self, handler, call):
        fake = FakePopen(server(handler))
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            result = call()
        return result, fake

    def test_constructor_requires_explicit_valid_host_identity_and_finite_timeout(self) -> None:
        cases = (
            {"host": "bad host"}, {"username": "bad user"}, {"port": True},
            {"port": 0}, {"timeout": float("inf")}, {"timeout": True},
            {"timeout": 901},
        )
        for override in cases:
            values = {
                "host": "builder.example.net", "username": "build-controller", "port": 2222,
                "identity_file": self.identity, "known_hosts_file": self.known_hosts, "timeout": 2,
            }
            values.update(override)
            with self.subTest(override=override), self.assertRaises(ValueError):
                BuildHostClient(**values)

    def test_ssh_uses_pinned_host_key_and_exact_fixed_remote_command(self) -> None:
        capabilities = {
            "schema": 1,
            "projects": {"site": {
                "platform": dict(DEBIAN_PLATFORM), "runtimes": ["go", "node"],
                "limits": dict(DEFAULT_LIMITS),
            }},
            "tool_versions": {"go": "go version go1.24.4 linux/amd64"},
        }

        result, fake = self.invoke(
            lambda _request, _payload, output, _error, _stop: (
                write_all(output, success(capabilities)) or 0
            ),
            self.client.capabilities,
        )

        self.assertEqual(result, capabilities)
        args = fake.arguments
        self.assertEqual(args[args.index("--") + 1:], [
            "build-controller@builder.example.net", REMOTE_COMMAND,
        ])
        self.assertEqual(args[args.index("-F") + 1], "/dev/null")
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("GlobalKnownHostsFile=/dev/null", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("IdentityAgent=none", args)
        self.assertIn("ForwardAgent=no", args)
        self.assertIn("RequestTTY=no", args)
        self.assertIn("ProxyCommand=none", args)
        self.assertIn("ProxyJump=none", args)
        self.assertIn("ControlMaster=no", args)
        self.assertIn("ControlPath=none", args)
        self.assertIn("CertificateFile=none", args)
        self.assertEqual(fake.options["pass_fds"],
                         (self.client._identity_fd, self.client._known_hosts_fd))
        self.assertEqual(fake.options["start_new_session"], True)
        self.assertEqual(fake.options["cwd"], "/")

    def test_submit_streams_verified_source_in_bounded_chunks(self) -> None:
        source_data = bytes(range(251)) * 700
        source_path = self.root / "source.snapshot"
        source_path.write_bytes(source_data)
        request = build_request(snapshot_sha256=hashlib.sha256(source_data).hexdigest())
        seen = []

        def handler(message, payload, output, _error, _stop):
            seen.append((message, payload))
            write_all(output, success(job_result(request["request_id"], request)))
            return 0

        with patch.object(build_client, "MAX_TRANSFER_CHUNK_BYTES", 4096):
            result, fake = self.invoke(handler, lambda: self.client.submit(request, source_path))

        self.assertEqual(result["request_id"], request["request_id"])
        self.assertEqual(seen, [({
            "schema": 1, "action": "submit", "request": request,
            "source_size": len(source_data),
        }, source_data)])
        self.assertEqual(fake.calls, 1)
        self.assertLessEqual(len(source_data), MAX_SNAPSHOT_BYTES)

    def test_source_symlink_hash_mismatch_and_oversize_fail_before_ssh(self) -> None:
        content = b"snapshot"
        source = self.root / "source.snapshot"
        source.write_bytes(content)
        fake = FakePopen(server(lambda *_args: 0))
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            with self.assertRaises(ValueError):
                self.client.submit(build_request(snapshot_sha256="f" * 64), source)
            link = self.root / "source-link"
            link.symlink_to(source)
            with self.assertRaises(OSError):
                self.client.submit(build_request(snapshot_sha256=hashlib.sha256(content).hexdigest()), link)
            with patch.object(build_client, "MAX_SNAPSHOT_BYTES", 4):
                with self.assertRaises(ValueError):
                    self.client.submit(
                        build_request(snapshot_sha256=hashlib.sha256(content).hexdigest()), source,
                    )
        self.assertEqual(fake.calls, 0)

    def test_source_identity_change_during_transport_never_returns_admission(self) -> None:
        original = b"original"
        changed = b"changed!"
        source = self.root / "source.snapshot"
        source.write_bytes(original)
        request = build_request(snapshot_sha256=hashlib.sha256(original).hexdigest())

        def handler(_message, _payload, output, _error, _stop):
            write_all(output, error("rejected"))
            return 0

        fake = FakePopen(server(handler))

        def mutate_then_spawn(*args, **kwargs):
            source.write_bytes(changed)
            return fake(*args, **kwargs)

        with patch.object(build_client.subprocess, "Popen", side_effect=mutate_then_spawn):
            with self.assertRaises(BuildSubmissionUnconfirmed) as caught:
                self.client.submit(request, source)
        self.assertEqual(caught.exception.request_id, request["request_id"])
        self.assertEqual(caught.exception.error_code, "rejected")
        self.assertEqual(fake.calls, 1)

    def test_submit_remote_rejection_is_unconfirmed_and_keeps_same_id(self) -> None:
        data = b"verified source"
        source = self.root / "source.snapshot"
        source.write_bytes(data)
        request = build_request(snapshot_sha256=hashlib.sha256(data).hexdigest())
        for code in ("rejected", "unavailable"):
            received = []

            def handler(message, payload, output, _error, _stop, *, reply_code=code):
                received.append((message["request"]["request_id"], payload))
                write_all(output, error(reply_code))
                return 0

            fake = FakePopen(server(handler))
            with patch.object(build_client.subprocess, "Popen", side_effect=fake):
                with self.assertRaises(BuildSubmissionUnconfirmed) as caught:
                    self.client.submit(request, source)

            self.assertEqual(caught.exception.request_id, request["request_id"])
            self.assertEqual(caught.exception.error_code, code)
            self.assertIn("same request ID", str(caught.exception))
            self.assertEqual(received, [(request["request_id"], data)])
            self.assertEqual(fake.calls, 1)

    def test_lost_submit_reply_is_unconfirmed_without_retry(self) -> None:
        data = b"lost reply source"
        source = self.root / "source.snapshot"
        source.write_bytes(data)
        request = build_request(snapshot_sha256=hashlib.sha256(data).hexdigest())
        received = []

        def handler(message, payload, _output, _error, _stop):
            received.append((message["request"]["request_id"], payload))
            return 0

        fake = FakePopen(server(handler))
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            with self.assertRaises(BuildSubmissionUnconfirmed) as caught:
                self.client.submit(request, source)

        self.assertEqual(caught.exception.request_id, request["request_id"])
        self.assertIsNone(caught.exception.error_code)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(received, [(request["request_id"], data)])

    def test_status_rejects_wrong_id_and_extra_public_fields(self) -> None:
        request_id = "a" * 32
        receipt = job_result(request_id, build_request(request_id))

        def wrong_id(message, _payload, output, _error, _stop):
            result = dict(receipt, request_id="b" * 32)
            write_all(output, success(result))
            return 0

        with self.assertRaises(BuildHostProtocolError):
            self.invoke(wrong_id, lambda: self.client.status(request_id))

        def extra_field(_message, _payload, output, _error, _stop):
            result = dict(receipt, owner="not-public")
            write_all(output, success(result))
            return 0

        with self.assertRaises(BuildHostProtocolError):
            self.invoke(extra_field, lambda: self.client.status(request_id))

    def test_cancel_returns_matching_durable_job_receipt(self) -> None:
        request_id = "a" * 32
        request = build_request(request_id)
        cancelled = job_result(request_id, request, state="cancelled")

        def handler(message, _payload, output, _error, _stop):
            self.assertEqual(message, {"schema": 1, "action": "cancel", "request_id": request_id})
            write_all(output, success(cancelled))
            return 0

        result, _fake = self.invoke(handler, lambda: self.client.cancel(request_id))
        self.assertEqual(result["request_id"], request_id)
        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["cancel_requested"])

    def test_log_returns_raw_bounded_payload_and_validates_offsets(self) -> None:
        offset = 17
        payload = bytes(range(256)) * 120
        metadata = {
            "offset": offset, "next_offset": offset + len(payload),
            "waiting": False, "complete": False,
        }

        def handler(_message, _request_payload, output, _error, _stop):
            write_all(output, success(metadata, payload_bytes=len(payload)))
            write_all(output, payload)
            return 0

        result, _fake = self.invoke(
            handler, lambda: self.client.log("a" * 32, offset, MAX_LOG_CHUNK_BYTES),
        )
        self.assertIsInstance(result, BuildLogChunk)
        self.assertEqual(result.data, payload)
        self.assertEqual(result.next_offset, offset + len(payload))
        self.assertFalse(result.waiting)

        with self.assertRaises(ValueError):
            self.client.log("a" * 32, 0, MAX_LOG_CHUNK_BYTES + 1)
        with self.assertRaises(ValueError):
            self.client.log("a" * 32, True, 1)

    def test_response_line_payload_and_exit_status_are_strict(self) -> None:
        cases = (
            (b'{"schema":1,"schema":1,"ok":true,"result":{},"payload_bytes":0}\n', 0),
            (b'{"schema":1,"ok":true', 0),
            (b"x" * MAX_RESPONSE_LINE_BYTES + b"\n", 0),
            (success({"schema": 1, "projects": {}, "tool_versions": {}}, payload_bytes=1), 0),
            (success({"schema": 1, "projects": {}, "tool_versions": {}}), 9),
        )
        for raw, returncode in cases:
            def handler(_message, _payload, output, _error, _stop, *, line=raw, code=returncode):
                write_all(output, line)
                return code
            with self.subTest(returncode=returncode, line_length=len(raw)), self.assertRaises(
                (BuildHostProtocolError, BuildHostTransportError),
            ):
                self.invoke(handler, self.client.capabilities)

    def test_response_line_may_arrive_in_bounded_fragments(self) -> None:
        capabilities = {"schema": 1, "projects": {}, "tool_versions": {}}
        line = success(capabilities)
        first_fragment_read = threading.Event()

        def handler(_message, _payload, output, _error, _stop):
            write_all(output, line[:9])
            if not first_fragment_read.wait(2):
                raise TimeoutError("client did not read the first response fragment")
            write_all(output, line[9:])
            return 0

        fake = FakePopen(server(handler))
        original_read = os.read

        def observe_read(descriptor: int, size: int) -> bytes:
            block = original_read(descriptor, size)
            if fake.process is not None and descriptor == fake.process.stdout.fileno() and block:
                first_fragment_read.set()
            return block

        with patch.object(build_client.subprocess, "Popen", side_effect=fake), \
                patch.object(build_client.os, "read", side_effect=observe_read):
            result = self.client.capabilities()
        self.assertEqual(result, capabilities)

    def test_log_rejects_truncated_and_excess_payload(self) -> None:
        requested_id = "a" * 32
        for declared, raw_payload in ((4, b"abc"), (2, b"abc")):
            metadata = {
                "offset": 0, "next_offset": declared,
                "waiting": False, "complete": False,
            }

            def handler(_message, _payload, output, _error, _stop,
                        *, count=declared, raw=raw_payload, result=metadata):
                write_all(output, success(result, payload_bytes=count))
                write_all(output, raw)
                return 0

            with self.subTest(declared=declared, raw=raw_payload), self.assertRaises(BuildHostProtocolError):
                self.invoke(handler, lambda: self.client.log(requested_id, 0, 8))

    def test_artifact_download_streams_hashes_and_publishes_no_replace(self) -> None:
        payload = (b"artifact-bundle-" * 20000)[:250_000]
        expected = hashlib.sha256(payload).hexdigest()
        result = {"sha256": expected, "size": len(payload)}
        destination = self.root / "downloads" / "bundle.bin"
        destination.parent.mkdir(mode=0o700)

        def handler(message, _payload, output, _error, _stop):
            self.assertEqual(message, {"schema": 1, "action": "artifact", "request_id": "a" * 32})
            write_all(output, success(result, payload_bytes=len(payload)))
            for offset in range(0, len(payload), 4096):
                write_all(output, payload[offset:offset + 4096])
            return 0

        with patch.object(build_client, "MAX_TRANSFER_CHUNK_BYTES", 8192):
            path, _fake = self.invoke(
                handler,
                lambda: self.client.download_artifact("a" * 32, destination, expected, len(payload)),
            )

        self.assertEqual(path, str(destination))
        self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(destination.parent.glob("*.part")), [])

    def test_artifact_existing_destination_never_starts_ssh(self) -> None:
        destination = self.root / "downloads" / "bundle.bin"
        destination.parent.mkdir(mode=0o700)
        destination.write_bytes(b"user data")
        fake = FakePopen(server(lambda *_args: 0))
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            with self.assertRaises(FileExistsError):
                self.client.download_artifact("a" * 32, destination, "4" * 64, 8)
        self.assertEqual(destination.read_bytes(), b"user data")
        self.assertEqual(fake.calls, 0)

    def test_artifact_hash_failure_and_publish_race_cleanup_temporary(self) -> None:
        payload = b"complete but wrong"
        response_hash = hashlib.sha256(b"different bytes").hexdigest()
        destination = self.root / "downloads" / "bundle.bin"
        destination.parent.mkdir(mode=0o700)

        def handler(_message, _payload, output, _error, _stop):
            write_all(output, success({"sha256": response_hash, "size": len(payload)},
                                      payload_bytes=len(payload)))
            write_all(output, payload)
            return 0

        with self.assertRaises(BuildHostProtocolError):
            self.invoke(
                handler,
                lambda: self.client.download_artifact("a" * 32, destination, response_hash, len(payload)),
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])

        payload = b"race-safe bytes"
        digest = hashlib.sha256(payload).hexdigest()

        def success_handler(_message, _payload, output, _error, _stop):
            write_all(output, success({"sha256": digest, "size": len(payload)},
                                      payload_bytes=len(payload)))
            write_all(output, payload)
            return 0

        def concurrent_create(parent_fd: int, _temporary: str, leaf: str) -> None:
            target = Path(destination)
            target.write_bytes(b"created concurrently")
            os.chmod(target, 0o600)
            raise FileExistsError(leaf)

        with patch.object(build_client, "_rename_noreplace", side_effect=concurrent_create):
            with self.assertRaises(FileExistsError):
                self.invoke(
                    success_handler,
                    lambda: self.client.download_artifact("a" * 32, destination, digest, len(payload)),
                )
        self.assertEqual(destination.read_bytes(), b"created concurrently")
        self.assertEqual([item.name for item in destination.parent.iterdir()], [destination.name])

    def test_destination_parent_must_be_private_and_no_follow(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o755)
        destination = public / "bundle.bin"
        fake = FakePopen(server(lambda *_args: 0))
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            with self.assertRaises(ValueError):
                self.client.download_artifact("a" * 32, destination, "4" * 64, 8)
        self.assertEqual(fake.calls, 0)

        private = self.root / "private"
        private.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        with patch.object(build_client.subprocess, "Popen", side_effect=fake):
            with self.assertRaises(OSError):
                self.client.download_artifact("a" * 32, alias / "bundle.bin", "4" * 64, 8)
        self.assertEqual(fake.calls, 0)

    def test_stderr_capture_is_bounded_and_large_stderr_is_drained(self) -> None:
        capture = bytearray()
        with patch.object(build_client, "MAX_STDERR_CAPTURE_BYTES", 7):
            build_client._capture_stderr(capture, b"first")
            build_client._capture_stderr(capture, b"second")
        self.assertEqual(capture, b"firstse")
        self.assertEqual(len(capture), 7)

        capabilities = {"schema": 1, "projects": {}, "tool_versions": {}}

        def handler(_message, _payload, output, error_stream, _stop):
            write_all(error_stream, b"x" * 150_000)
            write_all(output, success(capabilities))
            return 0

        result, _fake = self.invoke(handler, self.client.capabilities)
        self.assertEqual(result["projects"], {})

    def test_timeout_cleans_up_only_local_transport(self) -> None:
        client = self.make_client(timeout=0.05)
        self.addCleanup(client.close)
        def handler(_message, _payload, output, _error, stop_event):
            del output
            stop_event.wait(2)
            return 0

        fake = FakePopen(server(handler))
        def terminate_fake(_pid, _signal):
            assert fake.process is not None
            fake.process.terminate()
            raise ProcessLookupError

        with patch.object(build_client.os, "killpg", side_effect=terminate_fake):
            with patch.object(build_client.subprocess, "Popen", side_effect=fake):
                with self.assertRaisesRegex(BuildHostTransportError, "timeout"):
                    client.capabilities()
        self.assertIsNotNone(fake.process)
        self.assertEqual(fake.calls, 1)
        self.assertIsNotNone(fake.process.wait(timeout=0.5))


if __name__ == "__main__":
    unittest.main()
