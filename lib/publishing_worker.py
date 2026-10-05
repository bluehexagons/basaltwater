"""Detached, bounded publishing supervisor; output is parsed then discarded."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

from lib.atomic_io import write_text_atomic
from lib.publishing import Publishing
from lib.publishing_auth import credential_paths, environment, executable
from lib.publishing_store import file_lock, now, private_directory


def upload_command(publishing: Publishing, run: dict, artifact: dict) -> list[str]:
    project = run["project_config"]
    service = project["provider"]
    binary = executable(service, publishing.home)
    native = credential_paths(service, publishing.home)
    if service == "butler":
        if not native.is_file():
            raise RuntimeError("Sign in to itch.io first")
        return [binary, "-i", str(native), "--json", "push", artifact["snapshot"], project["target"]]
    directory = private_directory(publishing.store.root / "run-files" / run["id"])
    # All paths are generated local paths; VDF escaping is stricter than shell
    # quoting. No user-supplied VDF or SetLive is ever accepted.
    def quote(value: str) -> str:
        if any(c in value for c in '\\"\r\n'):
            raise ValueError("Unsafe VDF path")
        return '"' + value + '"'
    depot = directory / "depot.vdf"
    output = private_directory(directory / "output")
    write_text_atomic(str(depot), '"DepotBuildConfig" { "DepotID" ' + quote(project["depot"]) +
                      ' "ContentRoot" ' + quote(artifact["snapshot"]) +
                      ' "FileMapping" { "LocalPath" "*" "DepotPath" "." "recursive" "1" } }\n', mode=0o600)
    app = directory / "app.vdf"
    write_text_atomic(str(app), '"AppBuild" { "AppID" ' + quote(project["target"]) + ' "Desc" ' + quote(run["id"]) +
                      ' "BuildOutput" ' + quote(str(output)) + ' "ContentRoot" ' + quote(artifact["snapshot"]) +
                      ' "Depots" { ' + quote(project["depot"]) + ' ' + quote(str(depot)) + ' } }\n', mode=0o600)
    return [binary, "+login", project["username"], "+run_app_build", str(app), "+quit"]


def receipt(output: str, service: str) -> str:
    if service == "butler":
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            result = value.get("value", {}) if isinstance(value, dict) and value.get("type") == "result" else {}
            if isinstance(result, dict) and type(result.get("buildId")) is int and result["buildId"] > 0 and not result.get("dryRun"):
                return str(result["buildId"])
    else:
        match = re.search(r"Successfully finished AppID [0-9]+ build \(BuildID ([0-9]+)\)", output, re.IGNORECASE)
        if match:
            return match[1]
    return ""


def execute(publishing: Publishing, run: dict, command: list[str], *, timeout: float = 21600, lease_fd: int | None = None) -> dict:
    """Never store raw output; failed post-dispatch outcomes are ambiguous."""
    output = bytearray()
    started = time.monotonic()
    received = 0
    stopped = False
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=environment(publishing.home), cwd=str(publishing.home), umask=0o077, start_new_session=True,
                          pass_fds=() if lease_fd is None else (lease_fd,)) as process:
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while process.poll() is None or selector.get_map():
                    with publishing.store.transaction() as db:
                        current = publishing.store.get(db, "runs", run["id"])
                    if current.get("cancel_requested") or time.monotonic() - started >= timeout or received > 16 * 1024 * 1024:
                        stopped = True
                        break
                    for key, _ in selector.select(timeout=0.5):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        received += len(chunk)
                        output.extend(chunk)
                        del output[:-65536]
            if stopped:
                os.killpg(process.pid, signal.SIGKILL)
            code = process.wait(timeout=5)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    text = output.decode("utf-8", errors="replace")
    build_id = receipt(text, run["project_config"]["provider"]) if code == 0 and not stopped else ""
    auth_failed = any(phrase in text.lower() for phrase in ("invalid password", "no credentials", "not logged", "login failure", "steam guard", "access denied"))
    return {"state": "uploaded" if build_id else "uploaded-unverified" if code == 0 and not stopped else "unknown",
            "receipt": build_id, "exit_code": code, "finished": now(), "needs_login": auth_failed,
            "message": "Upload completed" if build_id else "Inspect the provider before retrying; the outcome needs reconciliation"}


def work(publishing: Publishing) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Uploads require a non-root account")
    root = private_directory(publishing.store.root)
    with file_lock(root / "worker.lock"):
        # Holding the supervisor lease proves prior supervisors have stopped.
        # A running record is never reset to queued after a crash.
        with publishing.store.transaction() as db:
            for run in publishing.store.records(db, "runs"):
                if run["state"] == "running":
                    run.update(state="unknown", message="Supervisor interrupted; reconcile before retrying", finished=now())
                    publishing.store.put(db, "runs", run)
        while True:
            with publishing.store.transaction() as db:
                queued = [run for run in publishing.store.records(db, "runs") if run["state"] == "queued"]
            if not queued:
                return
            progress = False
            for saved in reversed(queued):
                try:
                    with file_lock(root / (saved["project_config"]["provider"] + ".lock")) as lease_fd:
                        with publishing.store.transaction() as db:
                            run = publishing.store.get(db, "runs", saved["id"])
                            if run["state"] != "queued":
                                continue
                            if run["job"] and publishing.store.get(db, "jobs", run["job"])["state"] != "enabled":
                                continue
                            artifact = publishing.store.get(db, "artifacts", run["artifact"])
                            try:
                                publishing._valid_artifact(db, artifact)
                                command = upload_command(publishing, run, artifact)
                            except (OSError, RuntimeError, ValueError):
                                run.update(state="preflight-failed", message="Check authentication, tool installation, configuration and artifact reviews", finished=now())
                                publishing.store.put(db, "runs", run)
                                db.execute("DELETE FROM dispatches WHERE identity=?", (run["identity"],))
                                if run["job"]:
                                    job = publishing.store.get(db, "jobs", run["job"])
                                    job["state"] = "paused"
                                    publishing.store.put(db, "jobs", job)
                                progress = True
                                continue
                            run.update(state="running", started=now())
                            publishing.store.put(db, "runs", run)
                        try:
                            outcome = execute(publishing, run, command, lease_fd=lease_fd)
                        except Exception:
                            outcome = {"state": "unknown", "message": "Worker interrupted; inspect provider state before retrying", "finished": now()}
                        with publishing.store.transaction() as db:
                            run.update(outcome)
                            publishing.store.put(db, "runs", run)
                            for job in publishing.store.records(db, "jobs"):
                                project = publishing.store.get(db, "projects", job["project"])
                                if (outcome.get("needs_login") and project["provider"] == run["project_config"]["provider"]
                                        or job["id"] == run["job"] and outcome["state"] in {"unknown", "uploaded-unverified"}):
                                    job["state"] = "paused"
                                    publishing.store.put(db, "jobs", job)
                        progress = True
                except RuntimeError:
                    continue
            if not progress:
                return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home")
    args = parser.parse_args()
    try:
        work(Publishing(args.home))
        return 0
    except (OSError, RuntimeError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
