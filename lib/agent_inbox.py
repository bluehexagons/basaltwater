"""Private, durable mailboxes for cooperating terminal agents on one account."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path


MAX_MESSAGES = 256
MAX_MESSAGE_BYTES = 4000
MAX_STORAGE_BYTES = 2 * 1024 * 1024
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")


def mailbox_name(value: object) -> str:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError("Mailbox names require 1–64 letters, digits, dots, underscores or hyphens")
    return value


class AgentInbox:
    """At-least-once delivery with explicit acknowledgements and process locks.

    Names route messages; they are not authenticated identities. All agents on
    the Unix account share this trust boundary. No message executes a command.
    """

    def __init__(self, home: str | None = None) -> None:
        self.home = Path(os.path.abspath(home or os.path.expanduser("~")))
        self.parent = self.home / ".local/state/basaltwater/inbox"
        self.path = self.parent / "messages.json"

    def _directory(self, *, create: bool) -> bool:
        validate_filesystem_path(str(self.home), must_exist=True)
        home_info = self.home.lstat()
        if not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != os.geteuid() or home_info.st_mode & 0o022:
            raise RuntimeError("Inbox home must be owned by this account without group or public write access")
        current = self.home
        for component in self.parent.relative_to(self.home).parts:
            current /= component
            if create and not os.path.lexists(current):
                current.mkdir(mode=0o700, exist_ok=True)
            if not os.path.lexists(current):
                return False
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise RuntimeError(f"Unsafe inbox directory: {current}")
        if self.parent.stat().st_mode & 0o077:
            raise RuntimeError(f"Inbox directory must be private: {self.parent}")
        return True

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._directory(create=True)
        descriptor = os.open(self.parent / "inbox.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise RuntimeError("Unsafe inbox lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _load(self) -> dict[str, Any]:
        try:
            value = read_json_file(str(self.path), max_bytes=MAX_STORAGE_BYTES)
        except FileNotFoundError:
            return {"version": 1, "messages": []}
        info = self.path.lstat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError(f"Inbox state must be private and owned by this account: {self.path}")
        try:
            if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
                raise ValueError("Unsupported schema")
            messages = value["messages"]
            if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
                raise ValueError("Invalid message list")
            ids: set[str] = set()
            for record in messages:
                self._validate_message(record)
                if record["id"] in ids:
                    raise ValueError("Duplicate message ID")
                ids.add(record["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Inbox state is invalid; preserve and inspect {self.path}") from exc
        return value

    @staticmethod
    def _validate_message(record: dict[str, Any]) -> None:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not _ID.fullmatch(record["id"]):
            raise ValueError("Invalid message ID")
        for key in ("sender", "recipient"):
            mailbox_name(record[key])
        message = record["message"]
        if (not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES
                or any(ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in message)):
            raise ValueError(f"Messages require printable text, up to {MAX_MESSAGE_BYTES} UTF-8 bytes")
        if type(record["created_at"]) is not int or record["created_at"] < 0 or type(record["acknowledged"]) is not bool:
            raise ValueError("Invalid message metadata")

    def send(self, sender: str, recipient: str, message: str) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "sender": sender, "recipient": recipient,
                  "message": message, "created_at": int(time.time()), "acknowledged": False}
        self._validate_message(record)
        with self._locked():
            state = self._load()
            state["messages"].append(record)
            while len(state["messages"]) > MAX_MESSAGES or self._storage_size(state) > MAX_STORAGE_BYTES:
                acknowledged = next((item for item in state["messages"] if item["acknowledged"]), None)
                if acknowledged is None:
                    raise RuntimeError("Inbox is full; acknowledge processed messages before sending more")
                state["messages"].remove(acknowledged)
            self._save(state)
        return record

    @staticmethod
    def _storage_size(state: dict[str, Any]) -> int:
        return len((json.dumps(state, indent=2) + "\n").encode("utf-8"))

    def _save(self, state: dict[str, Any]) -> None:
        if self._storage_size(state) > MAX_STORAGE_BYTES:
            raise RuntimeError("Inbox storage limit reached; preserve and inspect its state")
        write_json_atomic(str(self.path), state)

    def list(self, recipient: str, *, include_acknowledged: bool = False) -> list[dict[str, Any]]:
        mailbox_name(recipient)
        if not self._directory(create=False):
            return []
        # Atomic replacements allow genuinely read-only consumers without a lock file.
        return [record for record in self._load()["messages"] if record["recipient"] == recipient
                and (include_acknowledged or not record["acknowledged"])]

    def acknowledge(self, recipient: str, identifier: str) -> None:
        mailbox_name(recipient)
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError("Invalid message ID")
        with self._locked():
            state = self._load()
            record = next((item for item in state["messages"] if item["recipient"] == recipient and item["id"] == identifier), None)
            if record is None:
                raise ValueError("Message was not found in this mailbox")
            record["acknowledged"] = True
            self._save(state)

    def wait(self, recipient: str, *, timeout: int = 60) -> list[dict[str, Any]]:
        """Cooperatively wake on unread mail; timeout never acknowledges it."""
        mailbox_name(recipient)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("Inbox timeout must be 1–3600 seconds")
        deadline = time.monotonic() + timeout
        while True:
            messages = self.list(recipient)
            if messages or time.monotonic() >= deadline:
                return messages
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def add_inbox_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("inbox", help="Durable messages and cooperative wake for local agents")
    actions = parser.add_subparsers(dest="agent_inbox_command", required=True)
    for action in ("send", "list", "wait", "ack"):
        command = actions.add_parser(action)
        command.add_argument("recipient", metavar="MAILBOX")
        command.add_argument("--json", action="store_true")
        if action == "send":
            command.add_argument("--sender", required=True)
            command.add_argument("message", metavar="MESSAGE")
        elif action == "ack":
            command.add_argument("identifier", metavar="MESSAGE_ID")
        elif action == "wait":
            command.add_argument("--timeout", type=int, default=60)
        else:
            command.add_argument("--all", action="store_true")


def run_inbox_command(args: argparse.Namespace) -> int:
    try:
        inbox = AgentInbox()
        if args.agent_inbox_command == "send":
            result: object = inbox.send(args.sender, args.recipient, args.message)
        elif args.agent_inbox_command == "ack":
            inbox.acknowledge(args.recipient, args.identifier)
            result = {"acknowledged": args.identifier}
        elif args.agent_inbox_command == "wait":
            result = inbox.wait(args.recipient, timeout=args.timeout)
            if not result:
                print("[]" if args.json else "No unread messages before timeout")
                return 2
        else:
            result = inbox.list(args.recipient, include_acknowledged=args.all)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
