"""Durable delivery, bounded storage and cooperative terminal wake behavior."""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from lib import agent_inbox as mail


class AgentInboxTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = os.path.realpath(temporary.name)
        self.inbox = mail.AgentInbox(self.home)

    def test_feedback_cli_persists_framed_operator_mail_and_restore_round_trip(self) -> None:
        parser = argparse.ArgumentParser()
        mail.add_inbox_parser(parser.add_subparsers())
        with patch.object(mail, "AgentInbox", return_value=self.inbox), patch("sys.stdout", new_callable=io.StringIO) as output:
            args = parser.parse_args(["inbox", "feedback", "--sender", "t3-agent", "Missing tool\nCheck failed", "--json"])
            self.assertEqual(mail.run_inbox_command(args), 0)
            record = json.loads(output.getvalue())
            self.assertEqual(record["recipient"], "operator")
            self.assertEqual(record["sender"], "t3-agent")
            self.assertEqual(record["message"], mail.FEEDBACK_PREFIX + "Missing tool\nCheck failed")
            self.assertEqual(mail.AgentInbox(self.home).list("operator"), [record])
            for action in ("ack", "restore"):
                self.assertEqual(mail.run_inbox_command(parser.parse_args(["inbox", action, "operator", record["id"], "--json"])), 0)
                retained = self.inbox.list("operator", include_acknowledged=True)
                self.assertEqual(len(retained), 1)
                self.assertEqual(retained[0]["acknowledged"], action == "ack")
        self.assertEqual(self.inbox.list("operator"), [record])

    def test_cli_feedback_errors_preserve_state_and_generic_send_keeps_original_text(self) -> None:
        parser = argparse.ArgumentParser()
        mail.add_inbox_parser(parser.add_subparsers())
        with patch.object(mail, "AgentInbox", return_value=self.inbox), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(mail.run_inbox_command(parser.parse_args([
                "inbox", "feedback", "--sender", "worker", " \t", "--json",
            ])), 1)
            self.assertIn("Error:", output.getvalue())
            self.assertFalse(self.inbox.parent.exists())
            self.assertEqual(mail.run_inbox_command(parser.parse_args([
                "inbox", "send", "parent", "--sender", "worker", "Original message",
            ])), 0)
        self.assertEqual(self.inbox.list("parent")[0]["message"], "Original message")
        self.assertEqual(self.inbox.list("operator"), [])

    def test_feedback_bounds_include_framing_and_invalid_input_does_not_write(self) -> None:
        for sender, message in (("bad sender", "hello"), ("worker", " \n\t"), ("worker", "\0"),
                                ("worker", "a" * (mail.MAX_MESSAGE_BYTES - len(mail.FEEDBACK_PREFIX) + 1)),
                                ("worker", "é" * 2000), ("worker", None)):
            with self.subTest(sender=sender, message=str(message)[:20]), self.assertRaises(ValueError):
                self.inbox.feedback(sender, message)
        self.assertFalse(self.inbox.parent.exists())
        record = self.inbox.feedback("worker", "a" * (mail.MAX_MESSAGE_BYTES - len(mail.FEEDBACK_PREFIX)))
        self.assertEqual(len(record["message"].encode("utf-8")), mail.MAX_MESSAGE_BYTES)

    def test_restore_validates_mailbox_and_protects_restored_mail_from_reclamation(self) -> None:
        with patch.object(mail, "MAX_MESSAGES", 1):
            record = self.inbox.feedback("worker", "issue")
            self.inbox.acknowledge("operator", record["id"])
            for recipient, identifier in (("other", record["id"]), ("../operator", record["id"]), ("operator", "bad")):
                with self.subTest(recipient=recipient, identifier=identifier), self.assertRaises(ValueError):
                    self.inbox.restore(recipient, identifier)
            self.inbox.restore("operator", record["id"])
            self.inbox.restore("operator", record["id"])
            with self.assertRaisesRegex(RuntimeError, "full"):
                self.inbox.feedback("worker", "next issue")
            self.assertEqual(self.inbox.list("operator"), [record])
            self.inbox.acknowledge("operator", record["id"])
            replacement = self.inbox.feedback("worker", "next issue")
            self.assertEqual(self.inbox.list("operator", include_acknowledged=True), [replacement])
            with self.assertRaisesRegex(ValueError, "not found"):
                self.inbox.restore("operator", record["id"])

    def test_restart_preserves_unread_mail_until_explicit_recipient_ack(self) -> None:
        record = self.inbox.send("worker", "parent", "Validation passed\nReady for review")
        restored = mail.AgentInbox(self.home)
        self.assertEqual(restored.list("parent"), [record])
        self.assertEqual(restored.list("other"), [])
        with self.assertRaises(ValueError):
            restored.acknowledge("other", record["id"])
        restored.acknowledge("parent", record["id"])
        restored.acknowledge("parent", record["id"])
        self.assertEqual(self.inbox.list("parent"), [])
        self.assertTrue(self.inbox.list("parent", include_acknowledged=True)[0]["acknowledged"])
        self.assertEqual(self.inbox.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.inbox.parent.stat().st_mode & 0o777, 0o700)

    def test_concurrent_writers_do_not_lose_messages(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda number: mail.AgentInbox(self.home).send("worker", "parent", str(number)), range(32)))
        self.assertEqual({message["message"] for message in self.inbox.list("parent")}, {str(number) for number in range(32)})

    def test_full_inbox_preserves_pending_messages_and_reclaims_only_acknowledged(self) -> None:
        with patch.object(mail, "MAX_MESSAGES", 2):
            first = self.inbox.send("a", "parent", "one")
            self.inbox.send("b", "parent", "two")
            before = self.inbox.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "full"):
                self.inbox.send("c", "parent", "three")
            self.assertEqual(self.inbox.path.read_bytes(), before)
            self.inbox.acknowledge("parent", first["id"])
            self.inbox.send("c", "parent", "three")
            self.assertEqual([record["message"] for record in self.inbox.list("parent")], ["two", "three"])

    def test_byte_limit_reclaims_acknowledged_mail_before_message_count_limit(self) -> None:
        with patch.object(mail, "MAX_STORAGE_BYTES", 2000):
            first = self.inbox.send("worker", "parent", "é" * 120)
            self.inbox.send("worker", "parent", "pending")
            before = self.inbox.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "full"):
                self.inbox.send("worker", "parent", "é" * 120)
            self.assertEqual(self.inbox.path.read_bytes(), before)
            self.inbox.acknowledge("parent", first["id"])
            self.inbox.send("worker", "parent", "é" * 120)
            self.assertEqual([message["message"] for message in self.inbox.list("parent")], ["pending", "é" * 120])
            self.assertLessEqual(self.inbox.path.stat().st_size, 2000)

    def test_wait_wakes_on_mail_without_consuming_it(self) -> None:
        ready = threading.Event()
        result = []
        def worker() -> None:
            ready.set()
            result.extend(mail.AgentInbox(self.home).wait("parent", timeout=2))
        thread = threading.Thread(target=worker)
        thread.start()
        ready.wait(timeout=1)
        record = self.inbox.send("worker", "parent", "done")
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [record])
        self.assertEqual(self.inbox.list("parent"), [record])

    def test_empty_read_and_timeout_do_not_write_state(self) -> None:
        with patch.object(mail.time, "monotonic", side_effect=(0, 2)):
            self.assertEqual(self.inbox.wait("parent", timeout=1), [])
        self.assertFalse(self.inbox.parent.exists())

    def test_rejects_bad_labels_text_and_symlinks_without_touching_targets(self) -> None:
        for label in ("../parent", "a/b", "a\n", "", "x" * 65):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.inbox.send("worker", label, "done")
        for message in ("", "\0", "é" * 2001, "text\x7f"):
            with self.subTest(message=message[:20]), self.assertRaises(ValueError):
                self.inbox.send("worker", "parent", message)
        self.inbox.send("worker", "parent", "one")
        outside = Path(self.home) / "outside"
        outside.write_text("preserve")
        self.inbox.path.unlink()
        self.inbox.path.symlink_to(outside)
        with self.assertRaises(OSError):
            self.inbox.send("worker", "parent", "two")
        self.assertEqual(outside.read_text(), "preserve")

    def test_corrupt_state_is_preserved_with_an_actionable_error(self) -> None:
        self.inbox.send("worker", "parent", "one")
        for content in ('{"version":2,"messages":[]}', '{"version":1,"messages":[{}]}'):
            self.inbox.path.write_text(content)
            with self.assertRaisesRegex(RuntimeError, "preserve and inspect"):
                self.inbox.send("worker", "parent", "two")
            self.assertEqual(self.inbox.path.read_text(), content)

    def test_private_storage_and_directory_chain_are_enforced(self) -> None:
        self.inbox.send("worker", "parent", "one")
        self.inbox.path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            self.inbox.list("parent")
        self.inbox.path.chmod(0o600)
        self.inbox.parent.chmod(0o755)
        with self.assertRaises(RuntimeError):
            self.inbox.list("parent")

    def test_group_writable_home_is_refused_without_creating_storage(self) -> None:
        Path(self.home).chmod(0o770)
        with self.assertRaisesRegex(RuntimeError, "write access"):
            self.inbox.send("worker", "parent", "hello")
        self.assertFalse(self.inbox.parent.exists())


if __name__ == "__main__":
    unittest.main()
