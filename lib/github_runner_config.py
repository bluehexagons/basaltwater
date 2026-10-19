"""Fixed boundaries for the Debian GitHub runner."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from lib.validators import validate_github_login


PROFILE = "server_github_runner"
ACCOUNT = "basaltwater-runner"
SERVICE = "basaltwater-github-runner.service"
APP = "/opt/basaltwater-github-runner"
DATA = "/var/lib/basaltwater-github-runner"
HOME = DATA + "/home"
POLICY = "/etc/basaltwater/github-runner.json"
HOOK_DIR = "/etc/basaltwater/github-runner"
HOOK = HOOK_DIR + "/job-started.sh"
GATE = "/opt/basaltwater/common/service_tools/github_runner_gate.py"
OUTCOMES = DATA + "/telemetry/outcomes.json"
PAYLOAD_NAME = "github_runner_payload"
TOKEN_FILE = "/opt/basaltwater/" + PAYLOAD_NAME + "/token"
# Official linux-x64 release asset and its published SHA-256, 2026-10-18.
VERSION = "2.338.0"
SHA256 = "af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32"
EVENTS = ("push", "workflow_dispatch", "pull_request")
MAX_OUTCOMES = 100
CREDENTIAL_FILES = (".runner", ".credentials", ".credentials_rsaparams")
MAX_RUNNERS = 20
QUEUE_LOCK = DATA + "/queue.lock"


def runner_repositories(config: object) -> list[str]:
    """Keep saved single-repository setups and validate the repeated scope."""
    from lib.validation import validate_github_runner_repository

    single = getattr(config, "ci_repository", "")
    multiple = getattr(config, "ci_repositories", None)
    if not isinstance(single, str) or (multiple is not None and not isinstance(multiple, list)):
        raise ValueError("Invalid GitHub runner repositories")
    if single and multiple:
        raise ValueError("Use ci_repository or ci_repositories")
    repositories = multiple if multiple is not None else ([single] if single else [])
    if len(repositories) > MAX_RUNNERS:
        raise ValueError("A runner host supports at most 20 repositories")
    for repository in repositories:
        validate_github_runner_repository(repository)
    if len({repository.lower() for repository in repositories}) != len(repositories):
        raise ValueError("Duplicate GitHub runner repository")
    return list(repositories)


def repository_instance(repository: str) -> str:
    from lib.validation import validate_github_runner_repository

    validate_github_runner_repository(repository)
    return hashlib.sha256(repository.lower().encode("ascii")).hexdigest()[:16]


def validate_instance(instance: str) -> str:
    if not isinstance(instance, str) or (instance and not re.fullmatch(r"[a-f0-9]{16}", instance)):
        raise ValueError("Invalid runner instance")
    return instance


@dataclass(frozen=True)
class RunnerPaths:
    instance: str
    app: str
    data: str
    home: str
    policy: str
    hook_dir: str
    hook: str
    service: str

    @property
    def outcomes(self) -> str:
        return self.data + "/telemetry/outcomes.json"


def runner_paths(instance: str = "", *, app: str = APP, data: str = DATA, policy: str = POLICY,
                 hook_dir: str = HOOK_DIR, hook: str = HOOK, service: str = SERVICE) -> RunnerPaths:
    validate_instance(instance)
    if instance:
        app += "/instances/" + instance
        data += "/instances/" + instance
        hook_dir += "/instances/" + instance
        policy, hook = hook_dir + "/policy.json", hook_dir + "/job-started.sh"
        service = "basaltwater-github-runner-" + instance + ".service"
    return RunnerPaths(instance, app, data, data + "/home", policy, hook_dir, hook, service)


def installed_instances(hook_dir: str = HOOK_DIR) -> list[str]:
    """Discover only bounded, fixed instance identifiers; never accept paths."""
    directory = Path(hook_dir, "instances")
    if not directory.exists():
        return []
    instances = []
    for entry in directory.iterdir():
        if len(instances) >= 100:
            raise ValueError("Too many installed runner instances")
        instances.append(validate_instance(entry.name))
    return sorted(instances)


def validate_policy(value: object) -> dict:
    """Validate the sole nonsecret policy shared by setup, hook and panel."""
    from lib.validation import validate_github_runner_repository, validate_github_runner_labels, validate_github_runner_group

    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise ValueError("Invalid GitHub runner policy schema")
    repository, organization = value.get("repository", ""), value.get("organization", "")
    group = value.get("runner_group", "")
    if not isinstance(repository, str) or not isinstance(organization, str) or bool(repository) == bool(organization):
        raise ValueError("Specify exactly one of --ci-repository or --ci-organization")
    if repository:
        validate_github_runner_repository(repository)
        if group:
            raise ValueError("--ci-runner-group requires --ci-organization")
    elif not validate_github_login(organization):
        raise ValueError("--ci-organization requires a GitHub organization login")
    if group != "":
        validate_github_runner_group(group)
    users = value.get("allowed_users")
    if (not isinstance(users, list) or not 1 <= len(users) <= 100
            or any(not isinstance(user, str) or not validate_github_login(user) for user in users)):
        raise ValueError("GitHub runner requires 1–100 valid allowed users")
    validate_github_runner_labels(value.get("labels"))
    if value.get("runner_version") != VERSION:
        raise ValueError("GitHub runner version differs from the managed release")
    # Policies installed before deferred registration were always registered.
    registration = value.get("registration", "registered")
    if not isinstance(registration, str) or registration not in {"pending", "registered"}:
        raise ValueError("Invalid GitHub runner registration state")
    result = {key: value[key] for key in ("schema", "allowed_users", "labels", "runner_version")}
    result.update(repository=repository, registration=registration)
    if "enabled" in value:
        if type(value["enabled"]) is not bool:
            raise ValueError("Invalid runner enabled state")
        result["enabled"] = value["enabled"]
    if organization:
        result.update(organization=organization, runner_group=group)
    return result


def runner_target(policy: dict) -> str:
    """Return the validated github.com registration target."""
    return policy.get("organization") or policy["repository"]
