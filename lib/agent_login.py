"""CLI coordination of target-owned subscription and API-key authentication."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import webbrowser

from common.service_tools.codex_auth_login import DEVICE_URL, LOGIN_TIMEOUT, LoginError, Protocol, read_private
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


API_BILLING_NOTICE = "API-key authentication uses separately billed OpenAI API usage, not your ChatGPT subscription."


def add_login_parser(commands) -> None:
    parser = commands.add_parser("login", help="Authorize Codex on a target VM (ChatGPT subscription by default)")
    parser.add_argument("agent_auth_host", metavar="HOST")
    parser.add_argument("agent_auth_username", metavar="USER")
    parser.add_argument("--tool", choices=("codex",), default="codex")
    parser.add_argument("--method", choices=("subscription", "api-key"), default="subscription")
    parser.add_argument("--open-browser", action="store_true", help="Open the device authorization page on this controller")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--api-key-file", metavar="PATH", help="Read an API key from a protected file")
    source.add_argument("--api-key-stdin", action="store_true", help="Read an API key from stdin")
    parser.add_argument("-k", "--key", dest="ssh_key")
    parser.add_argument("-p", "--port", type=int, default=22)


def login_agent(*, host: str, username: str, ssh_key: str | None = None,
                port: int = 22, method: str = "subscription", api_key: str | None = None,
                open_browser: bool = False) -> int:
    if not validate_host(host) or not validate_username(username) or not 1 <= port <= 65535:
        raise ValueError("Invalid authentication target or SSH port")
    if method not in {"subscription", "api-key"}:
        raise ValueError("Unsupported authentication method")
    if method == "subscription" and api_key is not None:
        raise ValueError("API-key input requires --method api-key")
    if method == "api-key" and (not api_key or open_browser):
        raise ValueError("API-key login requires a key and does not use --open-browser")
    if ssh_key:
        ssh_key = os.path.abspath(os.path.expanduser(ssh_key))
        validate_filesystem_path(ssh_key, must_exist=True)
    # Ship the current helper, so the controller needs neither Codex nor a
    # matching Basaltwater installation on the target. Secrets use stdin only.
    helper = Path(__file__).resolve().parent.parent / "common/service_tools/codex_auth_login.py"
    command = build_ssh_command(host, username, ssh_key, port=port,
                                batch_mode=ssh_batch_mode(),
                                remote_command="python3 -u -c " + shlex.quote(helper.read_text()))
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None)
    complete = False
    try:
        process.stdin.write(json.dumps({"method": method, "api_key": api_key}).encode() + b"\n")
        process.stdin.flush()
        protocol = Protocol(process, time.monotonic() + LOGIN_TIMEOUT + 30)
        for _ in range(8):
            event = protocol.receive(lambda _message: True)
            if event.get("event") == "device":
                code = event.get("code")
                if event.get("url") != DEVICE_URL or not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,32}", code):
                    raise LoginError("invalid_device_authorization_response")
                print(f"Open {DEVICE_URL} on any device and enter code: {code}", flush=True)
                if open_browser:
                    try:
                        opened = webbrowser.open(DEVICE_URL)
                    except webbrowser.Error:
                        opened = False
                    if not opened:
                        print("Browser could not be opened; use the printed URL on another device.")
            elif event.get("event") == "complete" and event.get("method") == method:
                complete = True
                break
            elif event.get("event") == "error":
                reason = event.get("reason")
                if not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,80}", reason):
                    reason = "remote_login_failed"
                print(f"Authorization required: {reason}", file=sys.stderr)
                return 3
            else:
                raise LoginError("invalid_login_event")
        if not complete:
            raise LoginError("invalid_login_event")
        if process.wait(timeout=10) != 0:
            raise LoginError("remote_login_failed")
        print(f"Codex {method} credentials installed on {username}@{host}.")
        if method == "api-key":
            print("API key stored; provider acceptance has not been verified by a model request.")
        return 0
    except KeyboardInterrupt:
        print("Authorization cancelled; an incomplete login does not replace the target credential.", file=sys.stderr)
        return 3
    except (OSError, LoginError, subprocess.SubprocessError):
        print("Authorization failed or timed out; check SSH access and target Codex availability.", file=sys.stderr)
        return 3
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def run_agent_auth_login(args) -> int:
    source = args.api_key_file
    use_stdin = args.api_key_stdin
    api_key = None
    if args.method != "api-key" and (source or use_stdin):
        raise ValueError("API-key input requires --method api-key")
    if args.method == "api-key":
        print(API_BILLING_NOTICE, file=sys.stderr)
        if source:
            from lib.setup_common import _credential_source_path
            path = _credential_source_path(source, "API key")
            try:
                payload = read_private(path)
                api_key = (payload or b"").decode("utf-8")
            except (OSError, ValueError, LoginError):
                raise ValueError("API-key file must be a private, readable file owned by you") from None
        elif use_stdin:
            api_key = sys.stdin.read(16385)
        elif sys.stdin.isatty():
            api_key = getpass.getpass("OpenAI API key (hidden): ").strip()
        else:
            raise ValueError("API-key login requires --api-key-file or --api-key-stdin when unattended")
        if len(api_key) > 16384:
            raise ValueError("Invalid API-key input")
        api_key = api_key.strip()
        if not api_key or any(c.isspace() for c in api_key):
            raise ValueError("Invalid API-key input")
    return login_agent(host=args.agent_auth_host, username=args.agent_auth_username,
                       ssh_key=args.ssh_key, port=args.port, method=args.method,
                       api_key=api_key, open_browser=args.open_browser)
