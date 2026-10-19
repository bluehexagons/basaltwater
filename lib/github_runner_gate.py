"""Fail-closed identity evaluation and bounded local runner telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import stat

from lib.atomic_io import read_json_file, write_json_atomic
from lib.github_runner_config import (
    APP, CREDENTIAL_FILES, DATA, EVENTS, GATE, HOOK, HOOK_DIR, MAX_OUTCOMES, OUTCOMES, POLICY,
    repository_instance, runner_paths, validate_policy,
)
from lib.validation import validate_github_runner_repository
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
        full_name = validate_github_runner_repository(repository.get("full_name")) if isinstance(repository, dict) else ""
        organization = policy.get("organization")
        scope_matches = (full_name.split("/")[0].lower() == organization.lower() if organization
                         else full_name.lower() == policy["repository"].lower())
        if (not isinstance(repository, dict) or repository.get("private") is not True
                or not scope_matches
                or environment.get("GITHUB_REPOSITORY", "").lower() != full_name.lower()
                or environment.get("GITHUB_SERVER_URL") != "https://github.com"):
            return False, "repository-mismatch-or-public", ""
        if organization:
            owner = repository.get("owner")
            if (not isinstance(owner, dict) or owner.get("type") != "Organization"
                    or _login(owner.get("login")) != organization.lower()):
                return False, "organization-mismatch", ""
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
                if repo.get("full_name", "").lower() != full_name.lower() or repo.get("private") is not True:
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


def check_installation(*, require_registration: bool = True, instance: str = "") -> dict:
    """Require root-controlled policy, hook, interpreter inputs and app files."""
    paths = runner_paths(instance, app=APP, data=DATA, policy=POLICY, hook_dir=HOOK_DIR, hook=HOOK)
    application, hook, policy_path = paths.app, paths.hook, paths.policy
    executables = {application + "/bin/Runner.Listener", application + "/bin/Runner.Worker"}
    policy = validate_policy(read_json_file(policy_path, max_bytes=16 * 1024))
    if instance and (not policy["repository"] or repository_instance(policy["repository"]) != instance):
        raise ValueError("Runner instance policy mismatch")
    if require_registration and not policy.get("enabled", True):
        raise ValueError("Runner is disabled")
    if require_registration and policy["registration"] != "registered":
        raise ValueError("Runner is pending registration")
    credentials = tuple(application + "/" + name for name in CREDENTIAL_FILES)
    if policy["registration"] == "pending" and any(os.path.lexists(path) for path in credentials):
        raise ValueError("Partial runner registration requires recovery; see runner guide")
    for path in (policy_path, hook, GATE, application, application + "/.env", application + "/.path",
                 *(credentials if policy["registration"] == "registered" else ()),
                 *sorted(executables)):
        current = path
        while current != "/":
            info = os.lstat(current)
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise ValueError("Runner installation is writable or contains a symlink")
            directory = current != path or path == application
            if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
                raise ValueError("Runner installation contains an unexpected file type")
            current = os.path.dirname(current)
        access = os.R_OK | (os.X_OK if path == application or path in executables else 0)
        if not os.access(path, access):
            raise ValueError("Runner installation is not accessible to its service account")
    return policy


def record_outcome(allowed: bool, reason: str, identity: str, environment: dict[str, str], *, instance: str = "") -> None:
    """Retain only the last 100 decisions, never webhook bodies or credentials.

    This file is runner-writable operational telemetry, not an audit trail.
    Failure to record a decision also denies execution.
    """
    outcomes = runner_paths(instance).outcomes if instance else OUTCOMES
    try:
        previous = read_json_file(outcomes, max_bytes=64 * 1024)
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
    write_json_atomic(outcomes, previous[-MAX_OUTCOMES:], mode=0o644)


def main(*, check: bool = False, instance: str = "", worker: int | None = None) -> int:
    try:
        policy = check_installation(**({"instance": instance} if instance else {}))
        if check:
            return 0
        environment = dict(os.environ)
        try:
            event = read_json_file(environment.get("GITHUB_EVENT_PATH", ""), max_bytes=2 * 1024 * 1024)
            allowed, reason, identity = evaluate_job(policy, event, environment)
        except (OSError, ValueError):
            allowed, reason, identity = False, "event-unavailable", ""
        record_outcome(allowed, reason, identity, environment, **({"instance": instance} if instance else {}))
        print("GitHub runner gate: " + reason)
        if allowed and worker is not None:
            from lib.github_runner_queue import acquire_job_slot

            acquire_job_slot(worker)
        return 0 if allowed else 1
    except (OSError, ValueError, TypeError):
        print("GitHub runner gate: configuration or telemetry unavailable")
        return 1
