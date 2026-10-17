"""Secret uploads and expired payload recovery stay outside the runtime tree."""

from __future__ import annotations

import io
import os
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.atomic_io import write_json_atomic
from lib import setup_payloads as payloads
from lib.setup_common import _create_payload_archive, create_tar_from_dir


class TestSetupPayloads(unittest.TestCase):
    def test_main_exposes_payloads_only_while_setup_is_running(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                source = os.path.join(directory, "source")
                runtime = os.path.join(directory, "runtime")
                root = os.path.join(directory, "private")
                os.makedirs(os.path.join(source, "agent_payload"))
                os.mkdir(runtime)
                with open(os.path.join(source, ".remote_setup_args.json"), "w") as handle:
                    handle.write("[]")
                archive = _create_payload_archive(source)
                alias = os.path.join(runtime, "agent_payload")

                def execute(*_args, **_kwargs):
                    self.assertTrue(os.path.isdir(alias))
                    if interrupted:
                        raise KeyboardInterrupt
                    return SimpleNamespace(returncode=17)

                with (
                    patch.object(payloads, "PAYLOAD_ROOT", root),
                    patch.object(payloads, "__file__", os.path.join(runtime, "lib", "setup_payloads.py")),
                    patch.object(payloads, "run", side_effect=execute),
                    patch.object(sys, "argv", ["payloads", "--timeout", "60", "--payload-bytes", str(len(archive))]),
                    patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(archive))),
                    patch.dict(os.environ, {payloads.PROMPT_RELAY_ENV: "0"}),
                ):
                    if interrupted:
                        with self.assertRaises(KeyboardInterrupt):
                            payloads.main()
                    else:
                        self.assertEqual(payloads.main(), 17)
                self.assertEqual(os.listdir(runtime), [])
                self.assertEqual(os.listdir(root), [])

    def test_partial_publication_failure_removes_all_created_links(self):
        for after_link in (False, True):
            for failure in (OSError, KeyboardInterrupt):
                with self.subTest(after_link=after_link, failure=failure.__name__), \
                     tempfile.TemporaryDirectory() as directory:
                    source = os.path.join(directory, "source")
                    runtime = os.path.join(directory, "runtime")
                    os.mkdir(runtime)
                    for name in ("agent_payload", "device_pairing_payload"):
                        os.makedirs(os.path.join(source, name))
                    symlink = os.symlink

                    def publish(target, destination):
                        selected = os.path.basename(destination) == "device_pairing_payload"
                        if selected and not after_link:
                            raise failure("publication failed")
                        symlink(target, destination)
                        if selected:
                            raise failure("publication failed")

                    with patch.object(payloads.os, "symlink", side_effect=publish):
                        with self.assertRaisesRegex(failure, "publication failed"):
                            with payloads.link_payloads(source, runtime):
                                self.fail("Incomplete payloads must not run setup")
                    self.assertEqual(os.listdir(runtime), [])
                    self.assertTrue(os.path.isdir(os.path.join(source, "agent_payload")))

    def test_collision_preserves_existing_link_and_removes_partial_publication(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as runtime:
            for name in ("agent_payload", "device_pairing_payload"):
                os.mkdir(os.path.join(source, name))
            existing = os.path.join(runtime, "device_pairing_payload")
            target = os.path.join(source, "device_pairing_payload")
            os.symlink(target, existing)
            with self.assertRaisesRegex(FileExistsError, "existing setup payload"):
                with payloads.link_payloads(source, runtime):
                    self.fail("Existing payload entries must not be replaced")
            self.assertEqual(os.listdir(runtime), ["device_pairing_payload"])
            self.assertEqual(os.readlink(existing), target)

    def test_execution_interrupt_preserves_unowned_and_replaced_entries(self):
        for replacement in ("file", "symlink"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as source, \
                 tempfile.TemporaryDirectory() as runtime:
                os.mkdir(os.path.join(source, "agent_payload"))
                alias = os.path.join(runtime, "agent_payload")
                unowned = os.path.join(runtime, "web_panel_payload")
                os.symlink("unrelated", unowned)
                with self.assertRaises(KeyboardInterrupt):
                    with payloads.link_payloads(source, runtime):
                        self.assertEqual(os.readlink(alias), os.path.join(source, "agent_payload"))
                        os.unlink(alias)
                        if replacement == "symlink":
                            os.symlink("replacement", alias)
                        else:
                            with open(alias, "w", encoding="utf-8") as handle:
                                handle.write("replacement")
                        raise KeyboardInterrupt
                self.assertEqual(os.readlink(unowned), "unrelated")
                if replacement == "symlink":
                    self.assertEqual(os.readlink(alias), "replacement")
                else:
                    with open(alias, encoding="utf-8") as handle:
                        self.assertEqual(handle.read(), "replacement")

    def test_cleanup_failure_does_not_leave_other_owned_links(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as runtime:
            for name in ("agent_payload", "device_pairing_payload"):
                os.mkdir(os.path.join(source, name))
            failed = os.path.join(runtime, "device_pairing_payload")
            unlink = os.unlink

            def remove(path):
                if path == failed:
                    raise OSError("cleanup denied")
                unlink(path)

            with patch.object(payloads.os, "unlink", side_effect=remove):
                with self.assertRaisesRegex(RuntimeError, "device_pairing_payload.*cleanup denied"):
                    with payloads.link_payloads(source, runtime):
                        pass
            self.assertEqual(os.listdir(runtime), ["device_pairing_payload"])

    def test_framed_archive_does_not_consume_later_prompt_answers(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as destination:
            with open(os.path.join(source, ".remote_setup_args.json"), "w") as handle:
                handle.write("[]")
            archive = _create_payload_archive(source)
            answer = b'"tunnel-name"\n'
            stream = io.BytesIO(archive + answer)
            framed = payloads._BoundedPayloadReader(stream, len(archive))
            payloads.receive_payloads(framed, destination)
            while framed.remaining:
                framed.read(65536)
            self.assertEqual(stream.read(), answer)
            self.assertTrue(os.path.isfile(os.path.join(destination, ".remote_setup_args.json")))

    def test_truncated_frame_is_rejected(self):
        framed = payloads._BoundedPayloadReader(io.BytesIO(b"abc"), 4)
        self.assertEqual(framed.read(4), b"abc")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            framed.read(1)

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
