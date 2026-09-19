#!/usr/bin/env python3
"""Local privilege broker. Only the separate approval identity can decide requests."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import secrets
import socket
import socketserver
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import unicodedata

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.privilege_policy import (
    APPROVAL_SOCKET, DATABASE_PATH, ID_PATTERN, MAX_MESSAGE,
    REQUEST_SOCKET, WEB_USER, canonical, digest, load_policy, operation_plan, protected_path,
)

ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/"}


def execute(plan: dict) -> str:
    """No arbitrary argv, environment, stdin, output, or cwd crosses this boundary."""
    try:
        result = subprocess.run(
            plan["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd="/", env=ENVIRONMENT, close_fds=True,
            timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        # systemd may still have a job after its client is killed.
        return "uncertain"
    except OSError:
        return "failed"
    if result.returncode:
        return "failed"
    return "dispatched" if plan["operation"] == "system.reboot" else "succeeded"


class Broker:
    """A durable one-shot authorization state machine with serialized execution."""

    def __init__(self, database: str, policy_loader=load_policy, runner=execute):
        self.policy_loader = policy_loader
        self.runner = runner
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA max_page_count=16384")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, uid INTEGER NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, plan TEXT NOT NULL, digest TEXT NOT NULL,
                reason TEXT NOT NULL, state TEXT NOT NULL, actor TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, at REAL NOT NULL,
                request_id TEXT NOT NULL, state TEXT NOT NULL, actor TEXT
            );
        """)
        with self.db:
            for row in self.db.execute(
                "SELECT id,state FROM requests WHERE state IN ('pending','approved','executing')"
            ).fetchall():
                self._transition(row["id"], "uncertain" if row["state"] == "executing" else "expired", "restart")

    def _transition(self, request_id: str, state: str, actor: str) -> None:
        self.db.execute("UPDATE requests SET state=?,actor=? WHERE id=?", (state, actor, request_id))
        self.db.execute("INSERT INTO events(at,request_id,state,actor) VALUES(?,?,?,?)",
                        (time.time(), request_id, state, actor))

    def _expire(self) -> None:
        for row in self.db.execute(
            "SELECT id FROM requests WHERE state IN ('pending','approved') AND expires<=?", (time.time(),)
        ).fetchall():
            self._transition(row["id"], "expired", "expiry")

    def _get(self, request_id: str) -> dict:
        if not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id):
            raise ValueError("Invalid request ID")
        row = self.db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("Request unavailable")
        result = dict(row)
        result["plan"] = json.loads(result["plan"])
        return result

    def request(self, uid: int, operation: str, parameters: dict, reason: str) -> dict:
        if not isinstance(reason, str) or len(reason) > 1000 or any(unicodedata.category(c).startswith("C") for c in reason):
            raise ValueError("Reason must be a single line of at most 1000 characters")
        with self.lock, self.db:
            self._expire()
            policy = self.policy_loader()
            plan = operation_plan(policy, uid, operation, parameters)
            # Persistent quotas bound pending requests and repeated automatic actions.
            count = self.db.execute("SELECT count(*) FROM requests WHERE uid=? AND created>?",
                                    (uid, time.time() - 3600)).fetchone()[0]
            pending = self.db.execute("SELECT count(*) FROM requests WHERE state IN ('pending','approved')").fetchone()[0]
            if count >= 30 or pending >= 8:
                raise ValueError("Request quota reached; wait before submitting more requests")
            request_id = secrets.token_hex(16)
            now = time.time()
            fingerprint = digest({"id": request_id, "created": now, "expires": now + policy["ttl_seconds"], "plan": plan})
            self.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)",
                            (request_id, uid, now, now + policy["ttl_seconds"], canonical(plan), fingerprint, reason, "pending", "requester"))
            self._transition(request_id, "pending", f"uid:{uid}")
            if plan["mode"] == "allow":
                self._transition(request_id, "approved", "administrator-policy")
            result = self._get(request_id)
            result["review_url"] = policy["origin"] + "/requests/" + request_id
            return result

    def status(self, request_id: str, uid: int | None = None) -> dict:
        with self.lock, self.db:
            self._expire()
            result = self._get(request_id)
            if uid is not None and result["uid"] != uid:
                raise PermissionError("Request unavailable")
            return result

    def decide(self, request_id: str, fingerprint: str, approve: bool, actor: str) -> dict:
        if not isinstance(actor, str) or not actor or len(actor) > 128 or any(ord(c) < 32 for c in actor):
            raise ValueError("Invalid approver identity")
        with self.lock, self.db:
            self._expire()
            row = self._get(request_id)
            if row["state"] != "pending" or fingerprint != row["digest"]:
                raise ValueError("Request is no longer pending or the reviewed plan changed")
            if row["plan"]["policy_digest"] != digest(self.policy_loader()):
                self._transition(request_id, "invalidated", "policy-change")
            else:
                self._transition(request_id, "approved" if approve else "denied", actor)
            return self._get(request_id)

    def execute_next(self) -> bool:
        with self.lock, self.db:
            self._expire()
            # Also protects against accidental multiple worker threads.
            if self.db.execute("SELECT 1 FROM requests WHERE state='executing'").fetchone():
                return False
            row = self.db.execute("SELECT id FROM requests WHERE state='approved' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return False
            request = self._get(row["id"])
            try:
                policy = self.policy_loader()
                plan = operation_plan(policy, request["uid"], request["plan"]["operation"], request["plan"]["parameters"])
                valid = plan == request["plan"]
            except (OSError, ValueError, PermissionError):
                valid = False
            if not valid:
                self._transition(request["id"], "invalidated", "policy-change")
                return True
            self._transition(request["id"], "executing", request["actor"])
        # The execution claim must be durably committed BEFORE any side effect.
        try:
            result = self.runner(request["plan"])
            if result not in {"succeeded", "failed", "uncertain", "dispatched"}:
                result = "uncertain"
        except Exception:
            result = "uncertain"
        with self.lock, self.db:
            self._transition(request["id"], result, request["actor"])
        return True

    def dispatch(self, message: object, uid: int, *, approval: bool = False) -> dict:
        if not isinstance(message, dict):
            raise ValueError("Expected an object")
        action = message.get("action")
        if approval and action == "list" and set(message) == {"action"}:
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,uid,created,state,plan FROM requests ORDER BY created DESC LIMIT 20").fetchall()
                return {"requests": [{"id": row["id"], "uid": row["uid"], "created": row["created"],
                                      "state": row["state"], "operation": json.loads(row["plan"])["operation"]} for row in rows]}
        if action == "status" and set(message) == {"action", "id"}:
            return self.status(message["id"], None if approval else uid)
        if not approval and action == "request" and set(message) == {"action", "operation", "parameters", "reason"}:
            return self.request(uid, message["operation"], message["parameters"], message["reason"])
        if approval and action == "decide" and set(message) == {"action", "id", "digest", "approve", "actor"}:
            if type(message["approve"]) is not bool:
                raise ValueError("approve must be a boolean")
            return self.decide(message["id"], message["digest"], message["approve"], message["actor"])
        raise PermissionError("Action is not available on this interface")


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(3)
        try:
            _pid, uid, _gid = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.server.allowed_uid:
                raise PermissionError("Unauthorized peer")
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                raise ValueError("Invalid message size or framing")
            result = self.server.broker.dispatch(json.loads(raw), uid, approval=self.server.approval)
            response = {"ok": True, "result": result}
        except (OSError, ValueError, PermissionError, RecursionError, sqlite3.Error):
            response = {"ok": False, "error": "Broker rejected the request or could not persist it; inspect request status before retrying"}
        self.wfile.write((canonical(response) + "\n").encode())


class Server(socketserver.UnixStreamServer):
    def handle_error(self, request, client_address) -> None:
        # Do not log request content or tracebacks from disconnected clients.
        print("Broker connection failed", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Broker requires root")
    os.umask(0o077)
    policy = load_policy()
    protected_path(os.path.dirname(DATABASE_PATH), directory=True)
    protected_path(os.path.dirname(REQUEST_SOCKET), directory=True)
    if os.path.lexists(DATABASE_PATH):
        protected_path(DATABASE_PATH)
    # Refuse two brokers, including manual invocations outside systemd.
    lock = open(DATABASE_PATH + ".lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    broker = Broker(DATABASE_PATH)
    approver = pwd.getpwnam(WEB_USER)
    requester = pwd.getpwuid(policy["requester_uid"])
    servers = []
    for path, account, approval in ((REQUEST_SOCKET, requester, False), (APPROVAL_SOCKET, approver, True)):
        if os.path.lexists(path):
            os.unlink(path)
        server = Server(path, Handler)
        server.broker, server.allowed_uid, server.approval = broker, account.pw_uid, approval
        os.chown(path, 0, account.pw_gid)
        os.chmod(path, 0o660)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        if not broker.execute_next():
            time.sleep(0.25)


if __name__ == "__main__":
    main()
