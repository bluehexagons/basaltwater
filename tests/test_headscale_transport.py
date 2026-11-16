"""Fixed SSH argv, descriptor trust, bounded output and private body transport."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from lib import headscale_transport as transport


class PipeProcess:
    """Simulate SSH's streams without executing any system command."""

    def __init__(self, response: bytes, stderr: bytes = b""):
        self.pid, self.returncode, self.received = 999999, None, b""
        child_in, parent_in = os.pipe()
        parent_out, child_out = os.pipe()
        parent_err, child_err = os.pipe()
        self.stdin, self.stdout, self.stderr = (io.FileIO(fd, mode) for fd, mode in
                                               ((parent_in, "w"), (parent_out, "r"), (parent_err, "r")))
        def child():
            try:
                while block := os.read(child_in, 4096):
                    self.received += block
                os.write(child_out, response)
                if stderr:
                    os.write(child_err, stderr)
            finally:
                for fd in (child_in, child_out, child_err):
                    os.close(fd)
                self.returncode = 0
        self.thread = threading.Thread(target=child, daemon=True)
        self.thread.start()

    def wait(self, timeout=None):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise RuntimeError("Fixture did not finish")
        return self.returncode

    def poll(self):
        return self.returncode


class TransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.key, self.hosts = root / "key", root / "known_hosts"
        self.key.write_bytes(b"fixture-private-key")
        self.key.chmod(0o600)
        self.hosts.write_bytes(b"fixture-host-key")
        self.hosts.chmod(0o600)
        self.binding = transport.SSHBinding("control.example.com", str(self.key), str(self.hosts))

    def test_fixed_argv_private_stdin_and_descriptor_pinned_credentials(self):
        process = PipeProcess(b'{"ok":true,"result":{"id":"9"}}')
        secret = "fixture_private_enrollment_secret"
        with patch.object(transport.subprocess, "Popen", return_value=process) as popen:
            result = transport.exchange(self.binding, "client", {"key": secret})
        argv = popen.call_args.args[0]
        self.assertNotIn(secret, " ".join(argv))
        self.assertEqual(json.loads(process.received), {"key": secret})
        self.assertEqual(result, {"id": "9"})
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("IdentityAgent=none", argv)
        self.assertIn("ControlMaster=no", argv)
        self.assertIn("ProxyCommand=none", argv)
        self.assertEqual(argv[-1], "/usr/bin/python3 -I " + transport.SERVICES["client"])
        for fd in popen.call_args.kwargs["pass_fds"]:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_untrusted_credentials_rejected_before_process(self):
        self.key.chmod(0o644)
        with patch.object(transport.subprocess, "Popen") as popen:
            with self.assertRaises(transport.TransportError):
                transport.exchange(self.binding, "management", {})
            popen.assert_not_called()
        self.key.chmod(0o600)
        self.hosts.unlink()
        self.hosts.symlink_to(self.key)
        with patch.object(transport.subprocess, "Popen") as popen:
            with self.assertRaises(transport.TransportError):
                transport.exchange(self.binding, "management", {})
            popen.assert_not_called()

    def test_both_streams_are_bounded_and_raw_errors_suppressed(self):
        for output, error in ((b'{"ok":true,"result":{}}', b"secret" * 100),
                              (b'{"ok":false,"error":"secret"}', b"")):
            process = PipeProcess(output, error)
            with patch.object(transport, "MAX_BYTES", 256), \
                    patch.object(transport.subprocess, "Popen", return_value=process), \
                    patch.object(transport.os, "killpg"):
                with self.assertRaises(transport.TransportError) as raised:
                    transport.exchange(self.binding, "management", {})
            self.assertNotIn("secret", str(raised.exception))

    def test_arbitrary_commands_and_unsafe_bindings_are_rejected(self):
        with patch.object(transport.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                transport.exchange(self.binding, "sh -c malicious", {})
            popen.assert_not_called()
        for values in (("-oProxyCommand=anything", str(self.key), str(self.hosts)),
                       ("control.example.com", "relative", str(self.hosts))):
            with self.assertRaises(ValueError):
                transport.SSHBinding(*values)


if __name__ == "__main__":
    unittest.main()
