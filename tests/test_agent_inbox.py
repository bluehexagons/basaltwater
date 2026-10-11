"""Durable delivery, bounded storage and cooperative terminal wake behavior."""

from __future__ import annotations

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
