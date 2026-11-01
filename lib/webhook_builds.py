"""Signed repository events admitted into the managed build system."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re

from lib.validation import validate_github_runner_repository, validate_no_control_characters
from lib.validators import validate_host


MAX_EVENT_BYTES = 1024 * 1024
MAX_EVENTS = 128
WEBHOOK_PATH = "/basaltwater-builds/github"


def validate_webhook_settings(config) -> None:
    """Validate public setup selections before provisioning or remote mutation."""
    if type(config.enable_webhook_builds) is not bool or type(config.enable_github_runner) is not bool:
        raise ValueError("Build service selections must be booleans")
    if type(getattr(config, "disable_github_runner", False)) is not bool:
        raise ValueError("Runner disable selection must be boolean")
    if getattr(config, "disable_github_runner", False) and config.enable_github_runner:
        raise ValueError("Choose --github-runner or --no-github-runner")
    if config.enable_github_runner and config.system_type not in {"server_lite", "server_web", "server_github_runner"}:
        raise ValueError("--github-runner requires a Debian server profile")
    repositories = config.webhook_repositories or []
    branches = config.webhook_branches or ["main"]
    if not isinstance(repositories, list) or len(repositories) > 20:
        raise ValueError("Webhook repositories must be a list of at most 20 OWNER/REPO values")
    for repository in repositories:
        validate_github_runner_repository(repository)
    if len({repository.lower() for repository in repositories}) != len(repositories):
        raise ValueError("Duplicate webhook repository")
    if not isinstance(branches, list) or not 1 <= len(branches) <= 20:
        raise ValueError("Webhook branches must be a list of 1..20 literal branch names")
    for branch in branches:
        from lib.validation import validate_git_ref_name

        validate_git_ref_name(branch)
        if (not isinstance(branch, str) or not branch or len(branch) > 200
                or branch.startswith(("-", "/")) or branch.endswith(("/", ".", ".lock"))
                or any(part in branch for part in ("..", "@{", "//"))
                or any(char.isspace() or char in "~^:?*[\\" for char in branch)):
            raise ValueError("Webhook branch must be a literal Git branch name")
        validate_no_control_characters(branch, "Webhook branch")
    if len(set(branches)) != len(branches):
        raise ValueError("Duplicate webhook branch")
    if config.webhook_secret is not None:
        if not isinstance(config.webhook_secret, str) or not 32 <= len(config.webhook_secret) <= 512:
            raise ValueError("Webhook signing secret must contain 32..512 characters")
        validate_no_control_characters(config.webhook_secret, "Webhook secret")
    token = getattr(config, "webhook_github_token", None)
    if token is not None:
        from lib.validation import validate_github_token

        validate_github_token(token)
        if len(token) > 16384:
            raise ValueError("Webhook GitHub token is oversized")
    if config.webhook_domain is not None:
        if not validate_host(config.webhook_domain):
            raise ValueError("Invalid webhook hostname")
        try:
            ipaddress.ip_address(config.webhook_domain)
        except ValueError:
            pass
        else:
            raise ValueError("Webhook HTTPS ingress requires a DNS hostname")
    if config.enable_webhook_builds:
        if not config.is_build_server or config.disable_build_host:
            raise ValueError("--webhook-builds requires an enabled --build-server")
        if config.system_type not in {"server_lite", "server_web"}:
            raise ValueError("Webhook builds require server_lite or server_web")
        if not repositories:
            raise ValueError("--webhook-builds requires --webhook-repository OWNER/REPO")
        if not config.webhook_domain or not config.enable_ssl:
            raise ValueError("--webhook-builds requires --webhook-domain DOMAIN and --ssl")
        from lib.deploy_utils import parse_deploy_spec
        from lib.validation import validate_deploy_specs

        specs = getattr(config, "deploy_specs", None)
        validate_deploy_specs(specs)
        for destinations, _source in specs or []:
            for destination in destinations.split(","):
                domain, _path = parse_deploy_spec(destination.strip())
                if domain and domain.lower().rstrip(".") == config.webhook_domain.lower().rstrip("."):
                    raise ValueError("Webhook ingress requires a dedicated hostname separate from deployed sites")
        if any(getattr(config, field, None) == 8765 for field in (
                "web_panel_port", "privilege_broker_port", "web_interface_port", "device_pairing_port")):
            raise ValueError("Webhook intake reserves loopback TCP port 8765")
        if not config.enable_cloudflare and config.effective_access_sources():
            raise ValueError("Webhook HTTPS ingress requires publicly reachable ports 80 and 443")


def authenticate_event(body: bytes, *, signature: str, delivery: str,
                       event: str, secret: str, repositories: list[str],
                       branches: list[str]) -> dict:
    """Select only an authenticated literal repository/ref/commit, without I/O."""
    from lib.build_protocol import decode_document

    if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_EVENT_BYTES:
        raise ValueError("Webhook event is empty or oversized")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if (not isinstance(signature, str) or re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is None
            or not hmac.compare_digest(signature, expected)):
        raise PermissionError("Invalid webhook signature")
    if not isinstance(delivery, str) or re.fullmatch(r"[A-Za-z0-9-]{1,64}", delivery) is None:
        raise ValueError("Invalid webhook delivery identity")
    if event == "ping":
        return {"event": "ping"}
    if event != "push":
        raise ValueError("Only push events are supported")
    payload = decode_document(body, maximum=MAX_EVENT_BYTES)
    if not isinstance(payload, dict) or not isinstance(payload.get("repository"), dict):
        raise ValueError("Malformed webhook payload")
    repository = validate_github_runner_repository(payload["repository"].get("full_name"))
    if repository.lower() not in {item.lower() for item in repositories}:
        raise PermissionError("Webhook repository is not allowed")
    ref = payload.get("ref")
    if not isinstance(ref, str) or ref not in {"refs/heads/" + branch for branch in branches}:
        raise PermissionError("Webhook branch is not allowed")
    revision = payload.get("after")
    if payload.get("deleted") is not False or not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None or set(revision) == {"0"}:
        raise ValueError("Webhook must select a complete non-deleted commit")
    return {"event": "push", "delivery": delivery, "repository": repository,
            "branch": ref.removeprefix("refs/heads/"), "revision": revision,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            # GitHub's delivery header is not signed. Changing it must never replay a build.
            "request_id": hashlib.sha256(b"github:" + body).hexdigest()[:32]}


def project_name(repository: str) -> str:
    """Stable literal core project identity for a GitHub repository."""
    repository = validate_github_runner_repository(repository).lower()
    readable = re.sub(r"[^a-z0-9]+", "-", repository).strip("-")[:40].rstrip("-")
    return "github-" + readable + "-" + hashlib.sha256(repository.encode()).hexdigest()[:10]
