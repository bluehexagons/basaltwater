"""Preserve public shared-host policy on same-profile setup reruns."""

from __future__ import annotations

import argparse
import copy
import json

from lib.config import SetupConfig
from lib.github_runner_config import PROFILE, runner_repositories


BUILD_ROLE_PROFILES = {"server_lite", "server_web", PROFILE}


def setup_config_with_saved_build_roles(
    args: argparse.Namespace, cached: SetupConfig | None,
) -> SetupConfig:
    """Fill omitted role options before normal configuration validation.

    Keep the caller's explicit options separate from inherited policy: generated
    project/resource updates must merge the saved enrollment rather than treat
    it as a newly supplied policy file. Never inherit transient credentials.
    """
    if cached is None or cached.system_type != args.system_type:
        config = SetupConfig.from_args(args, args.system_type)
        config.validate_agent_provider_selection()
        return config
    selected = argparse.Namespace(**vars(args))

    def inherit(field: str, value: object) -> None:
        if getattr(selected, field, None) is None:
            setattr(selected, field, copy.deepcopy(value))

    inherit("is_build_server", True if cached.is_build_server else (
        False if cached.disable_build_host else None))
    inherit("enable_github_runner", True if cached.enable_github_runner else (
        False if cached.disable_github_runner else None))
    if getattr(selected, "enable_webhook_builds", None) is None:
        selected.enable_webhook_builds = (
            cached.enable_webhook_builds
            and getattr(selected, "is_build_server", None) is not False
            and not getattr(selected, "disable_build_host", False)
        )

    if (getattr(selected, "is_build_server", None) is True
            and getattr(selected, "build_host_policy", None) is None
            and getattr(selected, "build_host_enrollment_json", None) is None
            and not getattr(selected, "disable_build_host", False)
            and cached.build_host_enrollment is not None):
        selected.build_host_enrollment_json = json.dumps(cached.build_host_enrollment)

    if getattr(selected, "enable_github_runner", None) is True or args.system_type == PROFILE:
        if (getattr(selected, "ci_repository", None) is None
                and getattr(selected, "ci_organization", None) is None):
            selected.ci_repository = runner_repositories(cached) or None
            selected.ci_organization = cached.ci_organization or None
        for field in ("ci_allowed_users", "ci_labels"):
            inherit(field, getattr(cached, field))
        if getattr(selected, "ci_organization", None) == cached.ci_organization:
            inherit("ci_runner_group", cached.ci_runner_group)

    if selected.enable_webhook_builds:
        for field in ("webhook_repositories", "webhook_branches", "webhook_domain",
                      "enable_ssl", "ssl_email", "enable_cloudflare", "cloudflare_allow_direct"):
            inherit(field, getattr(cached, field))

    return SetupConfig.from_args(selected, args.system_type)
