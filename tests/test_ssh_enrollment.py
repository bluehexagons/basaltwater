"""Tests for explicit SSH host-key enrollment."""

from __future__ import annotations

import base64
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from lib.ssh_enrollment import (
    enroll_host_key,
    get_enrolled_host_key_lines,
    is_host_key_enrolled,
    pin_verified_ed25519_key,
)


EXPECTED_FINGERPRINT = "SHA256:" + "a" * 43
OBSERVED_FINGERPRINT = "SHA256:" + "b" * 43


class TestSshEnrollment(unittest.TestCase):
    @patch("lib.ssh_enrollment.subprocess.run")
    def test_reports_enrolled_host_key(self, run):
        run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "example.com ssh-ed25519 AAAA\n", "stderr": ""},
        )()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            path.write_text("example.com ssh-ed25519 AAAA\n", encoding="utf-8")
            self.assertTrue(
                is_host_key_enrolled("example.com", known_hosts_path=str(path))
            )

    def test_reports_missing_known_hosts_file(self):
        self.assertFalse(
            is_host_key_enrolled(
                "example.com",
                known_hosts_path="/tmp/basaltwater-missing-known-hosts",
            )
        )

    @patch("lib.ssh_enrollment.subprocess.run")
    def test_returns_enrolled_host_key_lines(self, run):
        run.return_value = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "# Host example.com found: line 1\n"
                    "example.com ssh-ed25519 AAAA\n"
                ),
                "stderr": "",
            },
        )()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            path.write_text("example.com ssh-ed25519 AAAA\n", encoding="utf-8")

            self.assertEqual(
                get_enrolled_host_key_lines(
                    "example.com",
                    known_hosts_path=str(path),
                ),
                ["example.com ssh-ed25519 AAAA"],
            )

    @patch("lib.ssh_enrollment.get_workspace_known_hosts_path")
    @patch("lib.ssh_enrollment.subprocess.run")
    def test_enrolls_after_noninteractive_confirmation(self, run, known_hosts):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "example.com ssh-ed25519 AAAA",
                    "stderr": "",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": f"256 {EXPECTED_FINGERPRINT} host (ED25519)",
                    "stderr": "",
                },
            )(),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            known_hosts.return_value = str(path)
            self.assertEqual(enroll_host_key("example.com", assume_yes=True), 0)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "example.com ssh-ed25519 AAAA\n",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                run.call_args_list[0].args[0],
                [
                    "ssh-keyscan",
                    "-T",
                    "10",
                    "-t",
                    "ed25519",
                    "-p",
                    "22",
                    "example.com",
                ],
            )

    @patch("lib.ssh_enrollment.subprocess.run")
    def test_decline_does_not_write_key(self, run):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "example.com ssh-ed25519 AAAA",
                    "stderr": "",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": f"256 {EXPECTED_FINGERPRINT} host (ED25519)",
                    "stderr": "",
                },
            )(),
        ]
        with patch("lib.ssh_enrollment.get_workspace_known_hosts_path") as known_hosts:
            known_hosts.return_value = "/tmp/should-not-be-written"
            self.assertEqual(enroll_host_key("example.com", input_fn=lambda _: "n"), 1)

    @patch("lib.ssh_enrollment.get_workspace_known_hosts_path")
    @patch("lib.ssh_enrollment.subprocess.run")
    def test_pinned_fingerprint_enrolls_without_prompt(self, run, known_hosts):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "example.com ssh-ed25519 AAAA",
                    "stderr": "",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": f"256 {EXPECTED_FINGERPRINT} host (ED25519)",
                    "stderr": "",
                },
            )(),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            known_hosts.return_value = str(path)

            self.assertEqual(
                enroll_host_key(
                    "example.com",
                    expected_fingerprint=EXPECTED_FINGERPRINT,
                ),
                0,
            )

    @patch("lib.ssh_enrollment.get_workspace_known_hosts_path")
    @patch("lib.ssh_enrollment.subprocess.run")
    def test_pinned_fingerprint_mismatch_does_not_write(self, run, known_hosts):
        run.side_effect = [
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "example.com ssh-ed25519 AAAA",
                    "stderr": "",
                },
            )(),
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": f"256 {OBSERVED_FINGERPRINT} host (ED25519)",
                    "stderr": "",
                },
            )(),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known_hosts"
            known_hosts.return_value = str(path)

            self.assertEqual(
                enroll_host_key(
                    "example.com",
                    expected_fingerprint=EXPECTED_FINGERPRINT,
                ),
                1,
            )
            self.assertFalse(path.exists())

    def test_rejects_malformed_pinned_fingerprint_before_scanning(self):
        with self.assertRaisesRegex(ValueError, "OpenSSH SHA256"):
            enroll_host_key("example.com", expected_fingerprint="SHA256:short")



class TestVerifiedGuestPins(unittest.TestCase):
    host = "192.0.2.40"
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
    public_key = "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.workspace = root / "workspace" / "known_hosts"
        self.user = root / "user" / "known_hosts"
        self.paths = [str(self.workspace), str(self.user)]
        self.enterContext(patch("lib.ssh_enrollment.resource_lock", side_effect=lambda *a, **kw: nullcontext()))
        self.find = self.enterContext(patch("lib.ssh_enrollment.subprocess.run"))

    def pin(self, *alternate_keys):
        return pin_verified_ed25519_key(
            self.host, self.public_key + " guest\n", self.paths,
            additional_public_keys=alternate_keys,
        )

    def test_first_enrollment_and_matching_hashed_pin_fill_only_missing_stores(self):
        self.pin()
        self.find.assert_not_called()
        for path in (self.workspace, self.user):
            self.assertEqual(path.read_text(), f"{self.host} {self.public_key}\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        hashed_entry = f"|1|salt|hash {self.public_key} existing comment\n"
        self.user.write_text(hashed_entry)
        self.workspace.unlink()
        self.find.return_value = subprocess.CompletedProcess([], 0, hashed_entry, "")
        self.pin()
        self.find.assert_called_once()
        self.assertEqual(self.user.read_text(), hashed_entry)
        self.assertEqual(self.workspace.read_text(), f"{self.host} {self.public_key}\n")

        before = [path.read_bytes() for path in (self.workspace, self.user)]
        with patch("lib.ssh_enrollment.write_text_atomic") as write:
            self.pin()
        write.assert_not_called()
        self.assertEqual([path.read_bytes() for path in (self.workspace, self.user)], before)

    def test_conflicts_in_either_store_leave_both_untouched(self):
        self.workspace.parent.mkdir()
        self.user.parent.mkdir()
        different_key = "ssh-ed25519 " + base64.b64encode(self.blob[:-1] + b"Z").decode("ascii")
        for path, conflict in (
            (self.workspace, f"{self.host},other.example {different_key}\n"),
            (self.user, f"|1|salt|hash {different_key}\n"),
            (self.user, f"@revoked {self.host} {self.public_key}\n"),
        ):
            with self.subTest(path=path.name, conflict=conflict):
                self.workspace.write_bytes(b"# workspace\r\n")
                self.user.write_bytes(b"# user\r\n")
                path.write_text(conflict)
                before = [item.read_bytes() for item in (self.workspace, self.user)]

                def search(command, **_):
                    self.assertEqual(command[:4], ["ssh-keygen", "-F", self.host, "-f"])
                    if command[4] == str(path):
                        return subprocess.CompletedProcess(command, 0, conflict, "")
                    return subprocess.CompletedProcess(command, 1, "", "")

                self.find.side_effect = search
                with self.assertRaisesRegex(RuntimeError, "conflicting SSH host key"):
                    self.pin()
                self.assertEqual([item.read_bytes() for item in (self.workspace, self.user)], before)

    @patch("lib.ssh_enrollment._fingerprint", return_value=EXPECTED_FINGERPRINT)
    def test_preserves_verified_rsa_and_ecdsa_entries_and_fills_missing_ed25519(self, fingerprint):
        rsa_key = "ssh-rsa RSA"
        ecdsa_key = "ecdsa-sha2-nistp256 ECDSA"
        self.workspace.parent.mkdir()
        self.user.parent.mkdir()
        ed25519_entry = f"{self.host} {self.public_key}\n"
        alternate_entries = (
            f"{self.host},other.example {ecdsa_key} original comment\r\n"
            f"|1|salt|hash {rsa_key}\r\n"
        )
        self.workspace.write_text(ed25519_entry)
        self.user.write_bytes(alternate_entries.encode())
        self.find.side_effect = [
            subprocess.CompletedProcess([], 0, alternate_entries, ""),
            subprocess.CompletedProcess([], 0, ed25519_entry, ""),
        ]

        self.pin(rsa_key, ecdsa_key)

        self.assertEqual(fingerprint.call_count, 2)
        self.assertEqual(self.workspace.read_text(), ed25519_entry)
        self.assertEqual(self.user.read_bytes(), (alternate_entries + ed25519_entry).encode())

        self.find.side_effect = [
            subprocess.CompletedProcess([], 0, alternate_entries + ed25519_entry, ""),
            subprocess.CompletedProcess([], 0, ed25519_entry, ""),
        ]
        with patch("lib.ssh_enrollment.write_text_atomic") as write:
            self.pin(rsa_key, ecdsa_key)
        write.assert_not_called()

    @patch("lib.ssh_enrollment._fingerprint", return_value=EXPECTED_FINGERPRINT)
    def test_unverified_and_marked_alternate_pins_stop_enrollment(self, _fingerprint):
        self.workspace.parent.mkdir()
        self.user.parent.mkdir()
        for entry in (
            f"{self.host} ssh-rsa DIFFERENT\n",
            f"@revoked {self.host} ssh-rsa VERIFIED\n",
            f"@cert-authority {self.host} ssh-rsa VERIFIED\n",
        ):
            with self.subTest(entry=entry):
                self.user.write_text(entry)
                self.workspace.write_bytes(b"# workspace\r\n")
                self.find.side_effect = [
                    subprocess.CompletedProcess([], 0, entry, ""),
                    subprocess.CompletedProcess([], 1, "", ""),
                ]

                with self.assertRaisesRegex(RuntimeError, "conflicting SSH host key"):
                    self.pin("ssh-rsa VERIFIED")

                self.assertEqual(self.workspace.read_bytes(), b"# workspace\r\n")
                self.assertEqual(self.user.read_text(), entry)

    def test_malformed_management_identity_reaches_neither_store(self):
        for malformed in ("", "ssh-ed25519 " + "x" * 68, self.public_key + "\n" + self.public_key):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    pin_verified_ed25519_key(self.host, malformed, self.paths)
        self.find.assert_not_called()
        self.assertFalse(self.workspace.parent.exists())
        self.assertFalse(self.user.parent.exists())

    def test_known_hosts_inspection_error_leaves_both_stores_untouched(self):
        self.user.parent.mkdir()
        self.user.write_text("# user\n")
        self.find.return_value = subprocess.CompletedProcess([], 1, "", "permission denied")
        with self.assertRaisesRegex(RuntimeError, "inspect existing SSH host keys"):
            self.pin()
        self.assertEqual(self.user.read_text(), "# user\n")
        self.assertFalse(self.workspace.exists())


if __name__ == "__main__":
    unittest.main()
