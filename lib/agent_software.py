"""Agent software proposals and the owning controller's SSH pull workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from lib.privilege_client import PrivilegeBrokerUnavailable, PrivilegeRequestRejected, duration, exchange
from lib.privilege_policy import ID_PATTERN, MAX_MESSAGE
from lib.software_requests import SOFTWARE_FLAGS, WORKER, selection
from lib.tool_results import add_json_option, failure, success, write_result


def add_software_parser(commands: argparse._SubParsersAction) -> None:
    root = commands.add_parser("software", help="Request an approved software addition or synchronize a saved host")
    actions = root.add_subparsers(dest="software_action", required=True)
    for name in ("plan", "request"):
        action = actions.add_parser(name)
        choice = action.add_mutually_exclusive_group(required=True)
        choice.add_argument("--flag", choices=sorted(SOFTWARE_FLAGS))
        choice.add_argument("--apt", metavar="PACKAGE")
        action.add_argument("--temporary", action="store_true", help="Install on this host without changing its rebuild setup")
        add_json_option(action, default=True)
        if name == "request":
            action.add_argument("--reason", required=True)
            action.add_argument("--review-window", type=duration)
            action.add_argument("--runtime", type=duration, help="Installation time budget, e.g. 30m or 2h")
    sync = actions.add_parser("sync", help="On the controller, save approved changes for one enrolled host")
    sync.add_argument("host")
    sync.add_argument("--ssh-key")
    sync.add_argument("--watch", action="store_true", help="Stream JSON lines for newly saved requests and retry notices")
    sync.add_argument("--json", action="store_true", help="Emit JSON (the default); --watch emits JSON lines")


def synchronize(host: str, key: str | None, *, rpc=None) -> list[str]:
    from lib.cache import load_setup_command, record_software_selection
    from lib.concurrency import resource_lock
    from lib.ssh_utils import build_ssh_command, workspace_pinned_ssh

    config = load_setup_command(host)
    if config is None:
        raise ValueError("The controller needs an existing saved configuration for this host")
    if rpc is None:
        with workspace_pinned_ssh(config.host if config.hosted_node else None):
            command = build_ssh_command(config.host, "root", key or config.ssh_key, batch_mode=True)

        def rpc(message):
            result = subprocess.run([*command, f"/usr/bin/python3 -I {WORKER} controller"],
                                    input=json.dumps(message), text=True, capture_output=True, timeout=60, check=True)
            if len(result.stdout) > MAX_MESSAGE:
                raise ValueError("Oversize software controller response")
            return json.loads(result.stdout)

    completed = []
    target = "local" if config.host in {"localhost", "127.0.0.1", "::1"} else config.host.lower().rstrip(".")
    with resource_lock("setup-target", target):
        for entry in rpc({"action": "software-queue"})["requests"]:
            rid = entry["id"]
            if (not ID_PATTERN.fullmatch(rid) or entry["profile"] != config.system_type
                    or entry["username"] != config.username):
                raise ValueError("Software approval does not match the selected host setup")
            identity = {"id": rid, "digest": entry["digest"]}
            item = rpc({"action": "software-claim", **identity})
            if item["id"] != rid or item["digest"] != entry["digest"] or item["plan"]["operation"] != "software.install":
                raise ValueError("Software request changed before the controller could save it")
            if item.get("saved_for_rebuild"):
                completed.append(rid)
                continue
            if item["state"] in {"cancelled", "expired", "invalidated"}:
                continue
            if item["state"] != "saving":
                raise ValueError("Software request could not be claimed")
            record_software_selection(config.host, config.system_type, config.username, item["plan"]["parameters"])
            acknowledged = rpc({"action": "software-saved", **identity})
            if acknowledged["id"] != rid or not acknowledged["saved_for_rebuild"]:
                raise ValueError("Software selection saved locally. Reconcile the same request on the target.")
            completed.append(rid)
    return completed


def run_software(args: argparse.Namespace) -> int:
    stage = "validate"
    try:
        if args.software_action == "sync":
            from lib.concurrency import ResourceBusyError
            stage = "sync"
            delay = 15
            first = True
            while True:
                try:
                    saved = synchronize(args.host, args.ssh_key)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ResourceBusyError) as exc:
                    if not args.watch:
                        raise
                    write_result(failure("sync_unavailable", str(exc), event="retry", retry_seconds=delay), flush=True)
                    time.sleep(delay)
                    delay = min(300, delay * 2)
                    continue
                delay = 15
                if not args.watch:
                    return write_result(success(saved=saved), flush=True)
                if first or saved:
                    write_result(success(event="sync", saved=saved), flush=True)
                    first = False
                time.sleep(15)
        parameters = {"kind": "flag" if args.flag else "apt", "name": args.flag or args.apt,
                      "scope": "temporary" if args.temporary else "saved"}
        if args.software_action == "request" and args.runtime is not None:
            parameters["runtime_seconds"] = args.runtime
        selection(parameters)
        if args.software_action == "plan" or args.temporary:
            stage = "plan"
            worker = Path(__file__).resolve().parents[1] / "common/service_tools/software_install.py"
            # The Debian interpreter supplies the distribution's python-apt bindings.
            output = subprocess.run(["/usr/bin/python3", "-I", str(worker), "details", parameters["scope"],
                                     parameters["kind"], parameters["name"]], capture_output=True,
                                    text=True, timeout=45, check=True)
            plan = json.loads(output.stdout)
            if args.software_action == "plan":
                return write_result(success(**plan))
            if plan["changes"] == 0:
                return write_result(success(state="already_installed", requested=plan["requested"], saved_for_rebuild=False))
        stage = "request"
        message = {"action": "request", "operation": "software.install", "parameters": parameters, "reason": args.reason}
        if args.review_window is not None:
            message["review_seconds"] = args.review_window
        return write_result(success(**exchange(message)))
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        effect = "none" if stage in {"plan", "validate"} or isinstance(
            exc, (PrivilegeRequestRejected, PrivilegeBrokerUnavailable)
        ) else "unknown"
        error = str(exc)
        if stage == "plan" and isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error = exc.stderr.strip()[:500]
        issue = "invalid_input" if stage == "validate" else "software_request_failed"
        event = {"event": "failed"} if args.software_action == "sync" and args.watch else {}
        return write_result(failure(issue, error, stage=stage, effect=effect, **event), flush=True)
