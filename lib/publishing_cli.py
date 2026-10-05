"""VM-local automation and recovery surface; human reviews are panel-only."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys

from lib.atomic_io import read_json_file
from lib.publishing import PROVIDERS, Publishing
from lib.publishing_artifacts import complete_record
from lib.publishing_auth import PublishingAuth, credential_paths, environment, executable
from lib.publishing_store import file_lock, private_directory
from lib.publishing_worker import work
from lib.validators import validate_steam_account_name


def add_publishing_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("publish", help="Manage VM game uploads and reviewed release writing", allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="Emit JSON (also accepted after the subcommand)")
    commands = parser.add_subparsers(dest="publish_command", required=True)
    for name in ("status", "projects", "jobs", "runs"):
        commands.add_parser(name)
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
    command = commands.add_parser("cancel")
    command.add_argument("run")
    commands.add_parser("worker", help="Process the durable queue (no schedule ownership)")
    command = commands.add_parser("schedule")
    command.add_argument("project")
    command.add_argument("--interval", type=int, default=60, help="Polling minutes; the running panel owns schedules")
    command = commands.add_parser("job")
    command.add_argument("id")
    command.add_argument("action", choices=("pause", "resume"))
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


def run_publishing_command(args: argparse.Namespace) -> int:
    publisher = Publishing()
    try:
        name = args.publish_command
        result: object = {"ok": True}
        if name in {"status", "projects", "jobs", "runs"}:
            snapshot = publisher.status()
            result = snapshot if name == "status" else snapshot[name]
        elif name == "project":
            result = publisher.save_project(args.id, args.repository, args.provider, args.target, depot=args.depot, username=args.username, record=args.record)
        elif name == "complete":
            result = complete_record(args.repository, args.path, args.build_id, args.record)
        elif name == "prepare":
            result = publisher.prepare(args.project)
        elif name == "upload":
            result = publisher.upload(args.artifact)
            if args.wait:
                work(publisher)
                with publisher.store.transaction() as db:
                    result = publisher.store.get(db, "runs", result["id"])
            else:
                publisher.start_worker()
        elif name == "cancel":
            publisher.cancel(args.run)
        elif name == "worker":
            work(publisher)
        elif name == "schedule":
            result = publisher.schedule(args.project, args.interval)
        elif name == "job":
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
            result = publisher.promote_itch(args.artifact, args.destination)
            publisher.start_worker()
        elif name == "release":
            result = publisher.create_release(args.run, args.branch)
        elif name == "beta-prepare":
            from lib.publishing_steam import prepare_beta

            result = prepare_beta(publisher, args.run, args.branch)
        elif name == "beta-promote":
            from lib.publishing_steam import queue_beta

            result = queue_beta(publisher, args.release)
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
                result = {"ok": completed.returncode == 0, "provider": args.provider}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not isinstance(result, dict) or result.get("ok", True) and result.get("state") not in {"unknown", "preflight-failed", "uploaded-unverified"} else 1
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError, http.client.HTTPException):
        # Never print provider exceptions, credentials, or imported text here.
        print(json.dumps({"ok": False, "error": "Publishing action failed; inspect the project, artifact, review requirements and VM-local operation history"}))
        return 1
