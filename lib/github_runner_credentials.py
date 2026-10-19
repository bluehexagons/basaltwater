"""Controller-only registration tokens from a private file or authenticated gh."""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from lib.atomic_io import write_bytes_atomic
from lib.config import SetupConfig
from lib.validation import (
    validate_filesystem_path, validate_github_runner_settings, validate_runner_registration_token,
)


def _token_from_gh(config: SetupConfig) -> bytes:
    executable = shutil.which("gh")
    if executable is None:
        raise ValueError("Install GitHub CLI (gh) on the controller, or use --ci-registration-token-file PATH")
    scope = f"orgs/{config.ci_organization}" if config.ci_organization else f"repos/{config.ci_repository}"
    environment = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_DEBUG": ""}
    try:
        result = subprocess.run(
            [executable, "api", "--hostname", "github.com", "--method", "POST",
             f"{scope}/actions/runners/registration-token", "--jq", ".token"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("GitHub CLI could not request a runner token; retry or use --ci-registration-token-file PATH") from None
    if result.returncode != 0:
        # Show only a recognized status code, never gh's response or diagnostics.
        match = re.search(rb"\(HTTP ([1-5][0-9]{2})\)", result.stderr or b"")
        status = f" (HTTP {match[1].decode('ascii')})" if match else ""
        if config.ci_organization:
            target = f"organization {config.ci_organization}"
            access = "Organization runner-management access is required; OAuth/classic tokens need admin:org."
            scope_hint = "admin:org"
        else:
            target = f"repository {config.ci_repository}"
            access = "Repository admin access is required; OAuth/classic tokens need repo."
            scope_hint = "repo"
        token_variable = next((name for name in ("GH_TOKEN", "GITHUB_TOKEN") if environment.get(name)), None)
        if token_variable:
            remedy = (
                f"{token_variable} overrides gh's saved login. Update that credential's runner permissions "
                "or unset GH_TOKEN and GITHUB_TOKEN to use the saved login."
            )
        else:
            remedy = f"For a saved gh login, run: gh auth refresh --hostname github.com --scopes {scope_hint}"
        raise ValueError(
            f"GitHub CLI could not create a registration token for {target}{status}.\n"
            "  Check the active account with: gh auth status --hostname github.com\n"
            f"  {access}\n"
            f"  {remedy}\n"
            "  Alternatively use --ci-registration-token-file PATH."
        )
    try:
        return validate_runner_registration_token(result.stdout.strip())
    except ValueError:
        raise ValueError("GitHub CLI returned an invalid runner registration token") from None


def prepare_registration_payload(config: SetupConfig, destination: str) -> bool:
    """Stage only the one-hour token; never transfer gh's API credentials."""
    validate_github_runner_settings(config)
    if not config.ci_registration_token_file and not config.ci_register_with_gh:
        return False
    validate_filesystem_path(destination)
    if config.dry_run:
        source = "request a runner token using local gh" if config.ci_register_with_gh else "stage the runner token file"
        print(f"  [DRY RUN] Would {source}")
        return False
    if config.ci_register_with_gh:
        token = _token_from_gh(config)
        os.makedirs(destination, mode=0o700)
        write_bytes_atomic(os.path.join(destination, "token"), token, mode=0o600)
    else:
        from web.github_runner_steps import stage_registration_token

        stage_registration_token(config.ci_registration_token_file, destination)
    return True
