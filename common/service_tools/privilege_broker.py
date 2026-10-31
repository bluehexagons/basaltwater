#!/usr/bin/env python3
"""Local privilege broker. Only the separate approval identity can decide requests."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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
from common.service_tools.admin_job import availability as admin_availability, job_status
from lib.admin_actions import ADMIN_HELPER
from lib.data_actions import DATA_HELPER
from lib.samba_accounts import SAMBA_ACCOUNT_HELPER
from lib.samba_shares import SHARE_HELPER, share_label, share_name
from smb.share_job import job_status as share_job_status, stage_share_job
from smb.password_staging import discard_password, prune_passwords, require_password, stage_password
from common.service_tools.data_job import job_status as data_job_status, stage_data_job
from lib.privilege_execution import (
    COMMAND_HELPER, ENVIRONMENT, EXECUTOR_WAIT_SECONDS, ExecutionResult, command_executor_argv,
)


class BrokerRequestRejected(ValueError):
    """A request failed validation before its durable acceptance began."""


class BrokerPermissionRejected(PermissionError):
    """A request failed authorization before its durable acceptance began."""


def execute(plan: dict) -> ExecutionResult:
    """Execute claimed actions; only approved command argv crosses the helper boundary."""
    try:
        if plan["operation"] == "admin.run":
            protected_path(ADMIN_HELPER)
            if plan["parameters"]["action"] in admin_availability():
                return ExecutionResult("failed", failure="action_unavailable")
        if plan["operation"] == "data.run":
            protected_path(DATA_HELPER)
            status = data_job_status()
            if not status["available"] or status["unit_state"] not in {"inactive", "failed"}:
                return ExecutionResult("failed", failure="action_unavailable")
            stage_data_job(plan["parameters"])
        if plan["operation"] == "samba.account":
            protected_path(SAMBA_ACCOUNT_HELPER)
        if plan["operation"] == "samba.shares":
            protected_path(SHARE_HELPER)
            status = share_job_status()
            if not status["available"] or status["blocked"] or status["unit_state"] not in {"inactive", "failed"}:
                return ExecutionResult("failed", failure="action_unavailable")
            stage_share_job(plan["parameters"])
        command = plan["operation"] == "command.run"
        if command:
            protected_path(COMMAND_HELPER)
        result = subprocess.run(
            command_executor_argv() if command else plan["argv"],
            **({"input": canonical(plan["argv"]).encode()} if command else {"stdin": subprocess.DEVNULL}),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd="/", env=ENVIRONMENT, close_fds=True,
            timeout=EXECUTOR_WAIT_SECONDS if command else 60, check=False,
        )
    except subprocess.TimeoutExpired:
        # systemd may still have a job after its client is killed.
        return ExecutionResult("uncertain", failure="wait_timeout")
    except (OSError, ValueError, RuntimeError):
        return ExecutionResult("failed", failure="executor_unavailable")
    if result.returncode < 0:
        return ExecutionResult("uncertain", exit_code=result.returncode, failure="executor_signal")
    if result.returncode:
        return ExecutionResult("failed", exit_code=result.returncode, failure="nonzero_exit")
    dispatched = plan["operation"] in {"system.reboot", "data.run", "samba.shares"} or (
        plan["operation"] == "admin.run" and plan["parameters"]["action"] != "cancel-shutdown"
    )
    return ExecutionResult("dispatched" if dispatched else "succeeded", exit_code=0)


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
        if "execution" not in {row["name"] for row in self.db.execute("PRAGMA table_info(requests)")}:
            self.db.execute("ALTER TABLE requests ADD COLUMN execution TEXT")
        with self.db:
            for row in self.db.execute(
                "SELECT id,state FROM requests WHERE state IN ('pending','approved','executing')"
            ).fetchall():
                self._transition(row["id"], "uncertain" if row["state"] == "executing" else "expired", "restart")

    def _transition(self, request_id: str, state: str, actor: str) -> None:
        self.db.execute("UPDATE requests SET state=?,actor=? WHERE id=?", (state, actor, request_id))
        self.db.execute("INSERT INTO events(at,request_id,state,actor) VALUES(?,?,?,?)",
                        (time.time(), request_id, state, actor))
        if state in {"cancelled", "denied", "expired", "invalidated", "succeeded", "failed", "uncertain"}:
            row = self.db.execute("SELECT plan FROM requests WHERE id=?", (request_id,)).fetchone()
            plan = json.loads(row["plan"])
            if plan["operation"] == "samba.account" and plan["parameters"]["action"] == "reset-password":
                try:
                    discard_password(plan["parameters"]["job_id"])
                except (OSError, ValueError, subprocess.SubprocessError):
                    # Expiry and runtime-directory removal remain independent
                    # cleanup paths. Never log the payload or obscure the outcome.
                    print("Samba password staging cleanup unavailable", file=sys.stderr)

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
        result["execution"] = json.loads(result["execution"]) if result["execution"] else None
        return result

    def request(self, uid: int, operation: str, parameters: dict, reason: str) -> dict:
        if not isinstance(reason, str) or len(reason) > 1000 or any(unicodedata.category(c).startswith("C") for c in reason):
            raise BrokerRequestRejected("Reason must be a single line of at most 1000 characters")
        with self.lock, self.db:
            self._expire()
            try:
                policy = self.policy_loader()
                plan = operation_plan(policy, uid, operation, parameters)
                if operation == "samba.account" and plan["parameters"]["action"] == "reset-password":
                    if self.db.execute("SELECT 1 FROM requests WHERE json_extract(plan,'$.operation')='samba.account' AND json_extract(plan,'$.parameters.job_id')=? LIMIT 1",
                                       (plan["parameters"]["job_id"],)).fetchone():
                        raise ValueError("This password reset identity was already used")
                    require_password(plan["parameters"], uid)
                if operation in {"admin.run", "data.run", "samba.account", "samba.shares"}:
                    for row in self.db.execute("SELECT plan FROM requests WHERE state IN ('pending','approved','executing')"):
                        existing = json.loads(row["plan"])["operation"]
                        if existing == operation or existing.startswith("samba.") and operation.startswith("samba."):
                            raise ValueError("An administration request is already outstanding; review or cancel it first")
                # Persistent quotas bound pending requests and repeated automatic actions.
                count = self.db.execute("SELECT count(*) FROM requests WHERE uid=? AND created>?",
                                        (uid, time.time() - 3600)).fetchone()[0]
                pending = self.db.execute("SELECT count(*) FROM requests WHERE state IN ('pending','approved')").fetchone()[0]
                if count >= 30 or pending >= 8:
                    raise ValueError("Request quota reached; wait before submitting more requests")
                request_id = secrets.token_hex(16)
                now = time.time()
                fingerprint = digest({"id": request_id, "created": now, "expires": now + policy["ttl_seconds"], "plan": plan})
                preview = {"id": request_id, "uid": uid, "created": now, "expires": now + policy["ttl_seconds"],
                           "plan": plan, "digest": fingerprint, "reason": reason, "state": "pending",
                           "actor": f"uid:{uid}", "execution": None,
                           "review_url": policy["origin"] + "/requests/" + request_id}
                # A valid input can double in size because the review contains both
                # parameters and executable argv. Reserve space for outcome metadata.
                if len(canonical({"ok": True, "result": preview}).encode()) > MAX_MESSAGE - 512:
                    raise ValueError("Reviewed request exceeds the response size limit")
            except PermissionError as exc:
                # Keep the public permission-error contract for direct callers.
                raise BrokerPermissionRejected(str(exc)) from exc
            except ValueError as exc:
                # This scope ends before the first insert. Errors after it must
                # use the generic response because persistence may have happened.
                raise BrokerRequestRejected(str(exc)) from exc
            self.db.execute("INSERT INTO requests(id,uid,created,expires,plan,digest,reason,state,actor) VALUES(?,?,?,?,?,?,?,?,?)",
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

    def cancel(self, request_id: str, uid: int) -> dict:
        """Withdraw an owned request before the worker claims execution."""
        with self.lock, self.db:
            self._expire()
            row = self._get(request_id)
            if row["uid"] != uid:
                raise PermissionError("Request unavailable")
            if row["state"] in {"pending", "approved"}:
                self._transition(request_id, "cancelled", f"uid:{uid}")
            elif row["state"] not in {"cancelled", "expired", "denied", "invalidated"}:
                raise ValueError("Execution was already claimed; cancellation cannot undo the action")
            return self._get(request_id)

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
            if not isinstance(result, ExecutionResult) or result.state not in {"succeeded", "failed", "uncertain", "dispatched"}:
                result = ExecutionResult("uncertain", failure="invalid_executor_result")
        except Exception:
            result = ExecutionResult("uncertain", failure="executor_exception")
        with self.lock, self.db:
            self.db.execute("UPDATE requests SET execution=? WHERE id=?", (canonical(asdict(result)), request["id"]))
            self._transition(request["id"], result.state, request["actor"])
        return True

    def dispatch(self, message: object, uid: int, *, approval: bool = False) -> dict:
        if not isinstance(message, dict):
            raise ValueError("Expected an object")
        action = message.get("action")
        if not approval and action == "samba-discard-password" and set(message) == {"action", "job_id"}:
            with self.lock:
                policy = self.policy_loader()
                if uid != policy["requester_uid"]:
                    raise PermissionError("Account is not authorized")
                if self.db.execute("SELECT 1 FROM requests WHERE json_extract(plan,'$.operation')='samba.account' AND json_extract(plan,'$.parameters.job_id')=? LIMIT 1",
                                   (message["job_id"],)).fetchone():
                    raise ValueError("Inspect or cancel the accepted reset request")
                discard_password(message["job_id"])
                return {"discarded": True}
        if not approval and action == "samba-stage-password" and set(message) == {"action", "parameters", "password"}:
            with self.lock:
                policy = self.policy_loader()
                plan = operation_plan(policy, uid, "samba.account", message["parameters"])
                if (plan["parameters"]["action"] != "reset-password"
                        or self.db.execute("SELECT 1 FROM requests WHERE json_extract(plan,'$.operation')='samba.account' AND json_extract(plan,'$.parameters.job_id')=? LIMIT 1",
                                           (plan["parameters"]["job_id"],)).fetchone()):
                    raise ValueError("Invalid or previously used password staging identity")
                protected_path(SAMBA_ACCOUNT_HELPER)
                return stage_password(plan["parameters"], message["password"], uid)
        if not approval and action == "share-status" and set(message) == {"action"}:
            policy = self.policy_loader()
            if uid != policy["requester_uid"]:
                raise PermissionError("Account is not authorized")
            status = share_job_status()
            try:
                protected_path(SHARE_HELPER)
            except (OSError, ValueError):
                status["available"] = False
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,created,expires,state,plan FROM requests WHERE uid=? AND json_extract(plan,'$.operation')='samba.shares' ORDER BY created DESC LIMIT 10", (uid,)).fetchall()
                records = []
                for row in rows:
                    parameters = json.loads(row["plan"])["parameters"]
                    records.append({"id": row["id"], "created": row["created"], "expires": row["expires"],
                                    "state": row["state"], "name": share_name(parameters), "label": share_label(parameters),
                                    "job_id": parameters["job_id"], "review_url": policy["origin"] + "/requests/" + row["id"]})
                outstanding = self.db.execute("SELECT 1 FROM requests WHERE state IN ('pending','approved','executing') AND json_extract(plan,'$.operation') IN ('samba.account','samba.shares') LIMIT 1").fetchone() is not None
            return {**status, "requests": records, "outstanding": outstanding}
        if not approval and action == "samba-status" and set(message) == {"action"}:
            policy = self.policy_loader()
            if uid != policy["requester_uid"]:
                raise PermissionError("Account is not authorized")
            try:
                protected_path(SAMBA_ACCOUNT_HELPER)
                available = True
            except (OSError, ValueError):
                available = False
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,created,expires,state,plan,execution FROM requests WHERE uid=? AND json_extract(plan,'$.operation')='samba.account' ORDER BY created DESC LIMIT 10", (uid,)).fetchall()
                result = {"available": available, "requests": [{"id": row["id"], "created": row["created"],
                    "expires": row["expires"], "state": row["state"], "parameters": json.loads(row["plan"])["parameters"],
                    "execution": json.loads(row["execution"]) if row["execution"] else None,
                    "review_url": policy["origin"] + "/requests/" + row["id"]} for row in rows]}
                result["outstanding"] = self.db.execute("SELECT 1 FROM requests WHERE state IN ('pending','approved','executing') AND json_extract(plan,'$.operation') IN ('samba.account','samba.shares') LIMIT 1").fetchone() is not None
            return result
        if not approval and action == "data-status" and set(message) == {"action"}:
            policy = self.policy_loader()
            if uid != policy["requester_uid"]:
                raise PermissionError("Account is not authorized")
            result = data_job_status()
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,created,expires,state,plan,execution FROM requests WHERE uid=? AND json_extract(plan,'$.operation')='data.run' ORDER BY created DESC LIMIT 10", (uid,)).fetchall()
                result["requests"] = [{"id": row["id"], "created": row["created"], "expires": row["expires"], "state": row["state"],
                    "parameters": json.loads(row["plan"])["parameters"],
                    "execution": json.loads(row["execution"]) if row["execution"] else None,
                    "review_url": policy["origin"] + "/requests/" + row["id"]}
                    for row in rows]
                while result["requests"] and len(canonical({"ok": True, "result": result}).encode()) > MAX_MESSAGE:
                    result["requests"].pop()
            return result
        if not approval and action == "admin-status" and set(message) == {"action"}:
            policy = self.policy_loader()
            if uid != policy["requester_uid"]:
                raise PermissionError("Account is not authorized")
            result = job_status()
            result["restartable_services"] = sorted(policy["services"])
            if policy["reboot"] == "deny":
                result["blocked"].update({key: "Power control is denied by administrator policy." for key in ("reboot", "shutdown")})
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,created,state,plan FROM requests WHERE uid=? ORDER BY created DESC LIMIT 100", (uid,)).fetchall()
                result["requests"] = [{"id": row["id"], "created": row["created"], "state": row["state"],
                                       "action": json.loads(row["plan"])["parameters"]["action"],
                                       "review_url": policy["origin"] + "/requests/" + row["id"]}
                                      for row in rows if json.loads(row["plan"])["operation"] == "admin.run"][:20]
                # The panel can inspect its account's broker work, but receives
                # neither approval authority nor potentially sensitive argv.
                rows = self.db.execute("SELECT id,created,expires,state,plan,execution FROM requests WHERE uid=? ORDER BY created DESC LIMIT 20", (uid,)).fetchall()
                result["privilege_requests"] = []
                for row in rows:
                    plan = json.loads(row["plan"])
                    target = (plan["parameters"]["unit"] if plan["operation"] == "service.restart" else
                              plan["parameters"]["action"] if plan["operation"] == "admin.run" else "")
                    result["privilege_requests"].append({"id": row["id"], "created": row["created"],
                        "expires": row["expires"], "state": row["state"], "operation": plan["operation"],
                        "target": target, "execution": json.loads(row["execution"]) if row["execution"] else None,
                        "review_url": policy["origin"] + "/requests/" + row["id"]})
                while (result["privilege_requests"] or result["requests"]) and len(canonical({"ok": True, "result": result}).encode()) > MAX_MESSAGE:
                    (result["privilege_requests"] or result["requests"]).pop()
            return result
        if approval and action == "list" and set(message) == {"action"}:
            with self.lock, self.db:
                self._expire()
                rows = self.db.execute("SELECT id,uid,created,state,plan FROM requests ORDER BY created DESC LIMIT 20").fetchall()
                return {"requests": [{"id": row["id"], "uid": row["uid"], "created": row["created"],
                                      "state": row["state"], "operation": json.loads(row["plan"])["operation"]} for row in rows]}
        if action == "status" and set(message) == {"action", "id"}:
            return self.status(message["id"], None if approval else uid)
        if not approval and action == "cancel" and set(message) == {"action", "id"}:
            return self.cancel(message["id"], uid)
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
        except (BrokerRequestRejected, BrokerPermissionRejected) as exc:
            response = {"ok": False, "code": "request_rejected", "error": str(exc)}
        except (OSError, ValueError, PermissionError, RuntimeError, subprocess.SubprocessError, RecursionError, sqlite3.Error):
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
    prune_passwords(clear=True)
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
    next_password_cleanup = time.monotonic() + 30
    while True:
        if time.monotonic() >= next_password_cleanup:
            try:
                prune_passwords()
            except (OSError, ValueError, subprocess.SubprocessError):
                print("Samba password staging cleanup unavailable", file=sys.stderr)
            next_password_cleanup = time.monotonic() + 30
        if not broker.execute_next():
            time.sleep(0.25)


if __name__ == "__main__":
    main()
