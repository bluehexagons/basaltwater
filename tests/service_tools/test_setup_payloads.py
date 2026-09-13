"""Secret uploads and expired payload recovery stay outside the runtime tree."""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from lib.atomic_io import write_json_atomic
from lib import setup_payloads as payloads
from lib.setup_common import _create_payload_archive, create_tar_from_dir


class TestSetupPayloads(unittest.TestCase):
    def test_runtime_archive_excludes_credentials_and_payload_extract_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source")
            os.makedirs(os.path.join(source, "agent_payload"))
            with open(os.path.join(source, "agent_payload", "auth.json"), "w") as file:
                file.write("private-value")
            with open(os.path.join(source, ".remote_setup_args.json"), "w") as file:
                file.write('["--password", "private-value"]')
            with open(os.path.join(source, "remote_setup.py"), "w") as file:
                file.write("# runtime")
            archive = create_tar_from_dir(source, exclude_payloads=True)
            with tarfile.open(fileobj=io.BytesIO(archive)) as runtime:
                self.assertEqual(runtime.getnames(), [".", "./remote_setup.py"])
            root = os.path.join(directory, "run")
            with patch.object(payloads, "PAYLOAD_ROOT", root):
                with payloads.payload_workspace(60) as destination:
                    payloads.receive_payloads(io.BytesIO(_create_payload_archive(source)), destination)
                    secret = os.path.join(destination, "agent_payload", "auth.json")
                    self.assertEqual(os.stat(secret).st_mode & 0o777, 0o600)
                    self.assertEqual(os.stat(destination).st_mode & 0o777, 0o700)
                    with open(secret) as file:
                        self.assertEqual(file.read(), "private-value")
                self.assertFalse(os.path.exists(destination))

    def test_expired_abandoned_payload_is_removed_but_live_lease_is_preserved(self):
        with tempfile.TemporaryDirectory() as root, patch.object(payloads, "PAYLOAD_ROOT", root):
            abandoned = os.path.join(root, "payload-abandoned")
            os.mkdir(abandoned, 0o700)
            write_json_atomic(os.path.join(abandoned, "lease.json"), {
                "version": 1, "uid": os.getuid(), "pid": 12345, "expires_at": 1,
            })
            with payloads.payload_workspace(60) as active:
                with patch.object(payloads.time, "time", return_value=10**12):
                    payloads.scrub_expired_payloads()
                self.assertTrue(os.path.isdir(active))
                self.assertFalse(os.path.exists(abandoned))

    def test_interruption_and_invalid_archive_clean_private_directory(self):
        for member_name, kind in (("../escape", tarfile.REGTYPE), ("agent_payload/link", tarfile.SYMTYPE)):
            with self.subTest(member=member_name), tempfile.TemporaryDirectory() as root:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                    member = tarfile.TarInfo(member_name)
                    member.type = kind
                    member.linkname = "/etc/passwd"
                    archive.addfile(member)
                buffer.seek(0)
                with patch.object(payloads, "PAYLOAD_ROOT", root):
                    with self.assertRaises(ValueError):
                        with payloads.payload_workspace(60) as destination:
                            payloads.receive_payloads(buffer, destination)
                    self.assertEqual(os.listdir(root), [])
                    with self.assertRaises(KeyboardInterrupt):
                        with payloads.payload_workspace(60):
                            raise KeyboardInterrupt
                    self.assertEqual(os.listdir(root), [])
