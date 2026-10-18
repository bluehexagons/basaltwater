"""Fixed boundaries for the repository-scoped Debian GitHub runner."""

from __future__ import annotations

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


def validate_policy(value: object) -> dict:
    """Validate the sole nonsecret policy shared by setup, hook and panel."""
    from lib.validation import validate_github_runner_repository, validate_github_runner_labels

    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise ValueError("Invalid GitHub runner policy schema")
    validate_github_runner_repository(value.get("repository"))
    users = value.get("allowed_users")
    if (not isinstance(users, list) or not 1 <= len(users) <= 100
            or any(not isinstance(user, str) or not validate_github_login(user) for user in users)):
        raise ValueError("GitHub runner requires 1–100 valid allowed users")
    validate_github_runner_labels(value.get("labels"))
    if value.get("runner_version") != VERSION:
        raise ValueError("GitHub runner version differs from the managed release")
    return {key: value[key] for key in ("schema", "repository", "allowed_users", "labels", "runner_version")}
