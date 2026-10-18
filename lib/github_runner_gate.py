"""Fail-closed identity evaluation and bounded local runner telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import stat

from lib.atomic_io import read_json_file, write_json_atomic
from lib.github_runner_config import APP, EVENTS, GATE, HOOK, MAX_OUTCOMES, OUTCOMES, POLICY, validate_policy
from lib.validators import validate_github_login


def _login(value: object) -> str:
    if not isinstance(value, str) or not validate_github_login(value):
        raise ValueError("missing-identity")
    return value.lower()


def evaluate_job(policy: dict, event: object, environment: dict[str, str]) -> tuple[bool, str, str]:
    """Use GitHub context plus webhook identities, never commit author text."""
    policy = validate_policy(policy)
    try:
        name = environment.get("GITHUB_EVENT_NAME", "")
        if name not in EVENTS:
            return False, "unsupported-event", ""
        if not isinstance(event, dict):
            raise ValueError("malformed-event")
        repository = event.get("repository")
        if (not isinstance(repository, dict) or repository.get("private") is not True
                or repository.get("full_name", "").lower() != policy["repository"].lower()
                or environment.get("GITHUB_REPOSITORY", "").lower() != policy["repository"].lower()
                or environment.get("GITHUB_SERVER_URL") != "https://github.com"):
            return False, "repository-mismatch-or-public", ""
        # Reruns can execute with another user's original privileges. Phase 1
        # admits only the original delivery, with matching server identities.
        if environment.get("GITHUB_RUN_ATTEMPT") != "1":
            return False, "rerun-unsupported", ""
        actor = _login(environment.get("GITHUB_ACTOR"))
        sender = event.get("sender")
        if not isinstance(sender, dict) or sender.get("type") != "User" or _login(sender.get("login")) != actor:
            return False, "identity-mismatch", ""
        triggering = environment.get("GITHUB_TRIGGERING_ACTOR")
        if triggering is not None and _login(triggering) != actor:
            return False, "identity-mismatch", ""
        allowed = {user.lower() for user in policy["allowed_users"]}
        if actor not in allowed:
            return False, "actor-denied", actor
        identity = actor
        if name == "pull_request":
            if event.get("action") not in {"opened", "reopened", "synchronize"}:
                return False, "pr-action-unsupported", actor
            pr = event.get("pull_request")
            if not isinstance(pr, dict):
                raise ValueError("malformed-event")
            for side in ("base", "head"):
                repo = pr.get(side, {}).get("repo", {})
                if repo.get("full_name", "").lower() != policy["repository"].lower() or repo.get("private") is not True:
                    return False, "fork-denied", actor
            author = pr.get("user")
            if not isinstance(author, dict) or author.get("type") != "User":
                raise ValueError("missing-identity")
            identity = _login(author.get("login"))
        if name == "push" and event.get("deleted") is not False:
            return False, "deleted-or-malformed-push", actor
        return (True, "allowed", identity) if identity in allowed else (False, "author-denied", identity)
    except (ValueError, TypeError, AttributeError):
        return False, "malformed-identity-or-event", ""


def check_installation() -> dict:
    """Require root-controlled policy, hook, interpreter inputs and app files."""
    for path in (POLICY, HOOK, GATE, APP, APP + "/.env", APP + "/.path", APP + "/.runner",
                 APP + "/.credentials", APP + "/.credentials_rsaparams",
                 APP + "/bin/Runner.Listener", APP + "/bin/Runner.Worker"):
        current = path
        while current != "/":
            info = os.lstat(current)
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise ValueError("Runner installation is writable or contains a symlink")
            current = os.path.dirname(current)
    return validate_policy(read_json_file(POLICY, max_bytes=16 * 1024))


def record_outcome(allowed: bool, reason: str, identity: str, environment: dict[str, str]) -> None:
    """Retain only the last 100 decisions, never webhook bodies or credentials.

    This file is runner-writable operational telemetry, not an audit trail.
    Failure to record a decision also denies execution.
    """
    try:
        previous = read_json_file(OUTCOMES, max_bytes=64 * 1024)
        if not isinstance(previous, list) or len(previous) > MAX_OUTCOMES:
            raise ValueError("Invalid runner outcome history")
    except FileNotFoundError:
        previous = []
    run_id = environment.get("GITHUB_RUN_ID", "")
    event_name = environment.get("GITHUB_EVENT_NAME", "")
    previous.append({
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "allowed": allowed, "reason": reason, "identity": identity,
        "event": event_name if event_name in EVENTS else "unsupported",
        "run_id": run_id if run_id.isascii() and run_id.isdigit() and len(run_id) <= 20 else "",
    })
    write_json_atomic(OUTCOMES, previous[-MAX_OUTCOMES:], mode=0o644)


def main(*, check: bool = False) -> int:
    try:
        policy = check_installation()
        if check:
            return 0
        environment = dict(os.environ)
        try:
            event = read_json_file(environment.get("GITHUB_EVENT_PATH", ""), max_bytes=2 * 1024 * 1024)
            allowed, reason, identity = evaluate_job(policy, event, environment)
        except (OSError, ValueError):
            allowed, reason, identity = False, "event-unavailable", ""
        record_outcome(allowed, reason, identity, environment)
        print("GitHub runner gate: " + reason)
        return 0 if allowed else 1
    except (OSError, ValueError, TypeError):
        print("GitHub runner gate: configuration or telemetry unavailable")
        return 1
