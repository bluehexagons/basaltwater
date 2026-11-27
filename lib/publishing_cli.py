"""VM-local automation and recovery surface; human reviews are panel-only."""

from __future__ import annotations

import argparse
import http.client
import os
import subprocess
import sys
import time

from lib.atomic_io import read_json_file
from lib.publishing import PROVIDERS, Publishing
from lib.publishing_artifacts import complete_record
from lib.publishing_auth import PublishingAuth, credential_paths, environment, executable
from lib.publishing_store import file_lock, private_directory
from lib.publishing_worker import work
from lib.tool_results import failure, from_report, success, write_result
from lib.validators import validate_steam_account_name


def add_publishing_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("publish", help="Manage VM game uploads and reviewed release writing", allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="Emit JSON (also accepted after the subcommand)")
    commands = parser.add_subparsers(dest="publish_command", required=True)
    for name in ("status", "projects", "jobs"):
        commands.add_parser(name)
    runs = commands.add_parser("runs")
    runs.add_argument("--id", metavar="JOB_ID", help="Read one retained run by its job ID")
    command = commands.add_parser("project", help="Configure a destination without credentials")
    command.add_argument("id")
    command.add_argument("repository")
    command.add_argument("provider", choices=PROVIDERS)
    command.add_argument("target")
    command.add_argument("--depot", default="")
    command.add_argument("--username", default="")
    command.add_argument("--record", default=".basaltwater/publishing-complete.json")
    command = commands.add_parser("complete", help="Atomically declare a completed build")
    command.add_argument("repository")
    command.add_argument("path")
    command.add_argument("build_id")
    command.add_argument("--record", default=".basaltwater/publishing-complete.json")
    command = commands.add_parser("prepare")
    command.add_argument("project")
    command = commands.add_parser("upload")
    command.add_argument("artifact")
    command.add_argument("--wait", action="store_true")
    command.add_argument("--timeout", type=int, help="Caller wait limit in seconds (1–86400, default 600)")
    command = commands.add_parser("cancel")
    command.add_argument("run")
    commands.add_parser("worker", help="Process the durable queue (no schedule ownership)")
    command = commands.add_parser("schedule")
    command.add_argument("project")
    command.add_argument("--interval", type=int, default=60, help="Polling minutes; the running panel owns schedules")
    command = commands.add_parser("job")
    command.add_argument("id")
    command.add_argument("action", choices=("pause", "resume", "remove", "edit"))
    command.add_argument("--interval", type=int, help="New polling minutes for edit only; does not resume or accept a new destination")
    command = commands.add_parser("draft", help="Import an unreviewed draft; imported approvals are rejected")
    command.add_argument("project")
    command.add_argument("file")
    command = commands.add_parser("export", help="Export exact human-reviewed text")
    command.add_argument("draft")
    command.add_argument("--handoff", action="store_true")
    command = commands.add_parser("artifact-text", help="Declare and attach an exact reviewed public text file")
    command.add_argument("artifact")
    command.add_argument("path")
    command.add_argument("draft")
    command = commands.add_parser("remove-artifact")
    command.add_argument("artifact")
    command = commands.add_parser("promote-itch", help="Re-upload retained bytes to another configured channel")
    command.add_argument("artifact")
    command.add_argument("destination")
    command = commands.add_parser("release", help="Prepare a manual Steamworks BuildID handoff")
    command.add_argument("run")
    command.add_argument("--branch", default="default")
    command = commands.add_parser("beta-prepare", help="Observe a non-default Steam beta promotion")
    command.add_argument("run")
    command.add_argument("branch")
    command = commands.add_parser("beta-promote", help="Queue an explicitly prepared beta promotion")
    command.add_argument("release")
    command = commands.add_parser("auth", help="Native terminal recovery; no passwords or tokens in arguments")
    command.add_argument("action", choices=("login", "logout"))
    command.add_argument("provider", choices=PROVIDERS)
    command.add_argument("--username", default="")
    for command in commands.choices.values():
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)


def _run_view(run: dict) -> dict:
    view = dict(run)
    native = view["state"]
    states = {"queued": "queued", "running": "running", "uploaded": "succeeded", "promoted": "succeeded",
              "operator-confirmed": "succeeded", "preflight-failed": "failed", "not-submitted": "failed",
              "cancelled": "cancelled"}
    view["schedule_id"] = view.get("job") or None
    view["job"] = {"id": view["id"], "state": states.get(native, "unknown"), "native_state": native}
    if "exit_code" in view:
        view["child_exit_code"] = view["exit_code"]
    return view


def run_publishing_command(args: argparse.Namespace) -> int:
    name = args.publish_command
    admitted = None
    stage = "validate"
    try:
        publisher = Publishing()
        result: object = {"ok": True}
        if name == "runs" and getattr(args, "id", None):
            with publisher.store.transaction() as db:
                result = _run_view(publisher.store.get(db, "runs", args.id))
        elif name in {"status", "projects", "jobs", "runs"}:
            snapshot = publisher.status()
            snapshot["runs"] = [_run_view(run) for run in snapshot["runs"]]
            result = snapshot if name == "status" else {name: snapshot[name]}
        elif name == "project":
            result = publisher.save_project(args.id, args.repository, args.provider, args.target, depot=args.depot, username=args.username, record=args.record)
        elif name == "complete":
            result = complete_record(args.repository, args.path, args.build_id, args.record)
        elif name == "prepare":
            result = publisher.prepare(args.project)
        elif name == "upload":
            timeout = args.timeout if args.timeout is not None else 600
            if args.timeout is not None and not args.wait or not 1 <= timeout <= 86400:
                raise ValueError("--timeout requires --wait and accepts 1–86400 seconds")
            result = _run_view(publisher.upload(args.artifact))
            admitted = result["job"]
            if admitted["state"] in {"queued", "running"}:
                stage = "start_worker"
                publisher.start_worker()
            if args.wait:
                stage = "wait"
                deadline = time.monotonic() + timeout
                while result["job"]["state"] in {"queued", "running"}:
                    with publisher.store.transaction() as db:
                        result = _run_view(publisher.store.get(db, "runs", admitted["id"]))
                    admitted = result["job"]
                    remaining = deadline - time.monotonic()
                    if admitted["state"] not in {"queued", "running"}:
                        break
                    if remaining <= 0:
                        return write_result(failure("wait_timeout", "Inspect the same publishing run after the caller wait expired",
                                                    **result, stage="wait", effect="unknown"))
                    time.sleep(min(0.5, remaining))
        elif name == "cancel":
            publisher.cancel(args.run)
            stage = "read_cancellation"
            with publisher.store.transaction() as db:
                result = _run_view(publisher.store.get(db, "runs", args.run))
            result["cancellation"] = "confirmed" if result["state"] == "cancelled" else "requested"
        elif name == "worker":
            work(publisher)
        elif name == "schedule":
            result = publisher.schedule(args.project, args.interval)
        elif name == "job":
            if args.action == "edit":
                if args.interval is None:
                    raise ValueError("Editing a schedule requires --interval")
                result = publisher.edit_schedule(args.id, args.interval)
            else:
                if args.interval is not None:
                    raise ValueError("--interval requires the edit action")
                publisher.job_action(args.id, args.action)
        elif name == "draft":
            value = read_json_file(args.file, max_bytes=128 * 1024)
            allowed = {"language", "title", "body", "source", "replaces", "release", "publish_at", "late_minutes"}
            if not isinstance(value, dict) or set(value) - allowed or not {"language", "title", "body"} <= set(value):
                raise ValueError("Draft accepts language, title, body, source, replaces, release, publish_at and late_minutes only")
            result = publisher.draft(args.project, **value)
        elif name == "export":
            result = publisher.export(args.draft, dispatch=args.handoff)
        elif name == "artifact-text":
            publisher.authorize_artifact_text(args.artifact, args.path, args.draft)
        elif name == "remove-artifact":
            publisher.remove_artifact(args.artifact)
        elif name == "promote-itch":
            result = _run_view(publisher.promote_itch(args.artifact, args.destination))
            admitted = result["job"]
            stage = "start_worker"
            publisher.start_worker()
        elif name == "release":
            result = publisher.create_release(args.run, args.branch)
        elif name == "beta-prepare":
            from lib.publishing_steam import prepare_beta

            result = prepare_beta(publisher, args.run, args.branch)
        elif name == "beta-promote":
            from lib.publishing_steam import queue_beta

            result = _run_view(queue_beta(publisher, args.release))
            admitted = result["job"]
            stage = "start_worker"
            publisher.start_worker()
        elif name == "auth":
            if os.geteuid() == 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
                raise RuntimeError("Native authentication requires a human non-root terminal; use the HTTPS panel")
            if args.action == "logout":
                PublishingAuth(publisher).logout(args.provider)
            else:
                if args.provider == "steamcmd" and not validate_steam_account_name(args.username):
                    raise ValueError("Supply the Steam account name")
                native = credential_paths(args.provider, publisher.home)
                binary = executable(args.provider, publisher.home)
                command = [binary, "-i", str(native), "login"] if args.provider == "butler" else [binary, "+login", args.username, "+quit"]
                with file_lock(private_directory(publisher.store.root) / (args.provider + ".lock")) as fd:
                    completed = subprocess.run(command, cwd=str(publisher.home), env=environment(publisher.home, login=True), timeout=900, check=False, umask=0o077, pass_fds=(fd,))
                result = {"ok": completed.returncode == 0, "provider": args.provider,
                          "child_exit_code": completed.returncode}
        if name == "upload" and args.wait and result["job"]["state"] != "succeeded":
            effect = "unknown" if result["job"]["state"] == "unknown" else "none"
            return write_result(from_report({**result, "stage": "upload", "effect": effect}, ok=False,
                                            issue="upload_unconfirmed", error="Inspect the retained publication run before retrying"))
        if result.get("ok") is False:
            return write_result(from_report({**result, "stage": "authentication", "effect": "unknown"}, ok=False,
                                            issue="authentication_failed", error="The provider login did not succeed"))
        return write_result(success(**result))
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError, http.client.HTTPException) as exc:
        # Never print provider exceptions, credentials, or imported text here.
        issue = "invalid_input" if isinstance(exc, (ValueError, TypeError)) and stage == "validate" else "publishing_failed"
        fields = {}
        if name not in {"status", "projects", "jobs", "runs"}:
            fields.update(stage=stage if stage != "validate" else name.replace("-", "_"),
                          effect="applied" if admitted and stage == "start_worker" or stage == "read_cancellation"
                          else "none" if issue == "invalid_input" else "unknown")
        if admitted:
            fields["job"] = admitted
        return write_result(failure(issue, "Publishing action failed. Inspect the project, artifact, review requirements and VM-local operation history", **fields))
