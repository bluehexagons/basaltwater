"""Signed HTTP intake and source admission into the ordinary managed job store.

The HTTP account can only retain signed events. A separate privileged intake
process authenticates them again, prepares bounded pinned source inputs, and
uses the same policy, input store, queue and worker as SSH controllers.
"""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.build_protocol import decode_document
from lib.webhook_builds import MAX_EVENT_BYTES, MAX_EVENTS, WEBHOOK_PATH, authenticate_event, project_name

ACCOUNT = "basaltwater-webhook"
CONFIG = "/etc/basaltwater/build-webhooks/config.json"
TOKEN = "/etc/basaltwater/build-webhooks/github-token"
DATA = "/var/lib/basaltwater-build-webhooks"
INBOX = DATA + "/incoming"
RECEIPTS = DATA + "/receipts"
PREPARING = DATA + "/preparing"
PORT = 8765
ENTRY = "/opt/basaltwater/common/service_tools/build_webhooks.py"
MAX_ENVELOPE = 2 * MAX_EVENT_BYTES
_NAME = re.compile(r"[0-9a-f]{32}\.json")


def load_settings() -> dict:
    descriptor = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o640 or info.st_size > 32768):
            raise ValueError("Unsafe webhook settings")
        value = decode_document(stream.read(32769), maximum=32768)
    if (not isinstance(value, dict) or set(value) != {"schema", "secret", "repositories", "branches", "principal", "domain"}
            or type(value["schema"]) is not int or value["schema"] != 1 or not isinstance(value["secret"], str)
            or not 32 <= len(value["secret"]) <= 512):
        raise ValueError("Invalid webhook settings")
    # Apply the public validators to retained policy too, without constructing
    # a setup or importing target-side setup defaults.
    from types import SimpleNamespace
    from lib.webhook_builds import validate_webhook_settings

    validate_webhook_settings(SimpleNamespace(
        enable_webhook_builds=True, enable_github_runner=False, is_build_server=True,
        disable_build_host=False, system_type="server_lite", enable_ssl=True,
        enable_cloudflare=False, effective_access_sources=lambda: [],
        webhook_secret=value["secret"], webhook_repositories=value["repositories"],
        webhook_branches=value["branches"], webhook_domain=value["domain"],
    ))
    return value


def authenticated(envelope: dict, settings: dict) -> tuple[dict, bytes]:
    if not isinstance(envelope, dict) or set(envelope) != {"body", "signature", "delivery", "event"}:
        raise ValueError("Invalid signed envelope")
    body = base64.b64decode(envelope["body"], validate=True)
    return authenticate_event(body, signature=envelope["signature"], delivery=envelope["delivery"],
                              event=envelope["event"], secret=settings["secret"],
                              repositories=settings["repositories"], branches=settings["branches"]), body


def retain_event(envelope: dict, settings: dict, *, inbox: str = INBOX) -> dict:
    event, _body = authenticated(envelope, settings)
    if event["event"] == "ping":
        return {"event": "ping"}
    path = Path(inbox, event["request_id"] + ".json")
    if path.exists():
        return {"request_id": event["request_id"]}
    if len(list(Path(inbox).iterdir())) >= MAX_EVENTS:
        raise RuntimeError("Webhook intake is full")
    write_json_atomic(str(path), envelope, mode=0o600)
    return {"request_id": event["request_id"]}


class Handler(BaseHTTPRequestHandler):
    """One bounded request at a time, behind the shared nginx HTTPS gateway."""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, _format, *args) -> None:
        pass  # Never log request headers, bodies or signing material.

    def do_POST(self) -> None:
        try:
            if self.path != WEBHOOK_PATH or self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("Unsupported request framing")
            for name in ("X-Hub-Signature-256", "X-GitHub-Delivery", "X-GitHub-Event"):
                if len(self.headers.get_all(name) or []) != 1:
                    raise ValueError("A single signed event header is required")
            sizes = self.headers.get_all("Content-Length") or []
            if len(sizes) != 1 or re.fullmatch(r"[0-9]{1,8}", sizes[0]) is None:
                raise ValueError("A bounded content length is required")
            size = int(sizes[0])
            if not 1 <= size <= MAX_EVENT_BYTES:
                raise ValueError("Event size is outside the limit")
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete event")
            envelope = {"body": base64.b64encode(body).decode("ascii"),
                        "signature": self.headers.get("X-Hub-Signature-256", ""),
                        "delivery": self.headers.get("X-GitHub-Delivery", ""),
                        "event": self.headers.get("X-GitHub-Event", "")}
            result = retain_event(envelope, load_settings())
            code = 200 if result.get("event") == "ping" else 202
        except PermissionError:
            code = 403
        except (OSError, ValueError, TypeError):
            code = 400
        except RuntimeError:
            code = 503
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.close_connection = True


def admit_event(envelope: dict, settings: dict, *, host: dict,
                receipts: str = RECEIPTS, preparing: str = PREPARING,
                prepare=None) -> dict:
    """Durable signed-event receipt followed by ordinary core source admission."""
    from lib.build_host_policy import authorize_build_request
    from lib.build_inputs import BuildInputStore
    from lib.build_jobs import BuildJobStore
    from lib.build_manifest import DEBIAN_PLATFORM, request_from_manifest
    from lib.build_setup_sources import prepare_setup_sources

    event, _body = authenticated(envelope, settings)
    if event["event"] == "ping":
        return event
    request_id = event["request_id"]
    path = str(Path(receipts, request_id + ".json"))
    try:
        previous = read_json_file(path, max_bytes=32768)
    except FileNotFoundError:
        previous = None
    if previous is not None:
        if (not isinstance(previous, dict) or previous.get("body_sha256") != event["body_sha256"]
                or previous.get("principal") != settings["principal"]):
            raise ValueError("Webhook receipt identity changed")
        # Repeated signed bodies never refetch or execute again. Interrupted
        # preparation requires explicit inspection; changing unsigned headers
        # cannot obtain another core request identity.
        return previous
    if len(list(Path(receipts).iterdir())) >= MAX_EVENTS:
        raise RuntimeError("Webhook receipts are full; archive settled receipts explicitly")
    receipt = dict(event, principal=settings["principal"], state="preparing")
    write_json_atomic(path, receipt, mode=0o600)
    try:
        with tempfile.TemporaryDirectory(prefix=request_id + "-", dir=preparing) as temporary:
            os.chmod(temporary, 0o700)
            url = "https://github.com/" + event["repository"] + ".git#branch=" + event["branch"]
            item, = (prepare or prepare_setup_sources)(
                [["build.invalid", url]], temporary, expected_revision=event["revision"],
                credential_runner=credential_runner,
            )
            project = project_name(event["repository"])
            projects = host["policy"]["principals"][settings["principal"]]["projects"]
            grant = projects.get(project, projects.get("*"))
            if grant is None:
                raise PermissionError("Webhook project is not allowed by current build policy")
            request = request_from_manifest(
                item.manifest, request_id=request_id, project=project, source=item.source,
                platform=DEBIAN_PLATFORM, limits=grant["limits"], workflow=True,
            )
            authorize_build_request(host["policy"], principal=settings["principal"], request=request,
                                    requested_runtimes=list(item.build_tools or ()))
            jobs = BuildJobStore(str(Path(host["roots"]["state"], "jobs.json")))
            inputs = BuildInputStore(host["roots"]["inputs"], max_records=host["max_records"],
                                     max_bytes=host["max_bytes"])
            with open(item.source_path, "rb") as stream:
                inputs.admit(request, owner=settings["principal"], source=stream,
                             source_size=os.fstat(stream.fileno()).st_size, jobs=jobs)
        receipt["state"] = "submitted"
    except BaseException:
        receipt["state"] = "recovery_required"
        write_json_atomic(path, receipt, mode=0o600)
        raise
    write_json_atomic(path, receipt, mode=0o600)
    return receipt


def process_once() -> None:
    from common.service_tools.build_host import load_config
    from lib.build_coordinator import CoordinatorBusy, host_slot
    from lib.build_setup_sources import SourceCleanupUncertain

    settings = load_settings()
    for entry in sorted(Path(INBOX).iterdir()):
        if _NAME.fullmatch(entry.name) is None:
            raise ValueError("Unexpected webhook intake entry")
        try:
            envelope = read_json_file(str(entry), max_bytes=MAX_ENVELOPE)
            event, _ = authenticated(envelope, settings)
            if entry.name != event.get("request_id", "") + ".json":
                raise ValueError("Signed event identity differs from its intake name")
        except (OSError, ValueError, RuntimeError, KeyError):
            print("Invalid webhook intake refused", flush=True)
            remove_file_durable(str(entry))
            continue
        # Capacity or configuration recovery must not discard an accepted push.
        # Only admission with a durable receipt can consume its inbox entry.
        try:
            with host_slot():
                host = load_config()
                try:
                    admit_event(envelope, settings, host=host)
                except SourceCleanupUncertain:
                    raise  # Leave the capacity marker and inbox entry retained.
                except (OSError, ValueError, RuntimeError, KeyError):
                    receipt = read_json_file(str(Path(RECEIPTS, event["request_id"] + ".json")), max_bytes=32768)
                    if (not isinstance(receipt, dict) or receipt.get("body_sha256") != event["body_sha256"]
                            or receipt.get("principal") != settings["principal"]):
                        raise ValueError("Webhook refusal has no durable receipt")
                    print("Webhook admission refused; inspect retained receipt and core job status", flush=True)
        except CoordinatorBusy:
            return
        except (OSError, ValueError, RuntimeError, KeyError):
            print("Webhook admission paused; preserve intake and inspect capacity/configuration", flush=True)
            return
        remove_file_durable(str(entry))


def credential_runner(_command, *, input_data, **_kwargs) -> bytes:
    """Broker-only credential source; never consult an Actions job's Git config."""
    from common.build_host_steps import _read_file
    from lib.validation import validate_github_token

    raw = _read_file(TOKEN, mode=0o600, maximum=16384)
    if raw is None:
        raise ValueError("Private webhook source requires --webhook-github-token")
    token = validate_github_token(raw.decode("utf-8").strip())
    lines = input_data.decode("utf-8").splitlines()
    if "protocol=https" not in lines or "host=github.com" not in lines:
        raise ValueError("Webhook credential scope is invalid")
    return input_data.rstrip(b"\n") + b"\nusername=x-access-token\npassword=" + token.encode() + b"\n\n"


def main(mode: str) -> None:
    if mode == "--receive":
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    elif mode == "--admit" and os.geteuid() == 0:
        while True:
            process_once()
            time.sleep(2)
    else:
        raise ValueError("Use the managed webhook services")
