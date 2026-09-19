"""Shared policy helpers for automatic update services."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any


ECOSYSTEM_AUTO_UPGRADE_ENV = "BASALTWATER_ECOSYSTEM_AUTO_UPGRADE"
DEPENDENCY_MIN_AGE_DAYS_ENV = "BASALTWATER_DEPENDENCY_MIN_AGE_DAYS"
DEFAULT_DEPENDENCY_MIN_AGE_DAYS = 7

_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag_enabled(
    name: str,
    *,
    default: bool = False,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return whether an environment flag is enabled."""
    source = os.environ if env is None else env
    value = source.get(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def ecosystem_auto_upgrade_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether global npm/gem/uv-tool upgrades are allowed."""
    return env_flag_enabled(ECOSYSTEM_AUTO_UPGRADE_ENV, env=env)


def dependency_min_age_days(env: Mapping[str, str] | None = None) -> int:
    """Return the minimum dependency age for resolving ecosystem packages."""
    source = os.environ if env is None else env
    value = source.get(DEPENDENCY_MIN_AGE_DAYS_ENV)
    if value is None or value.strip() == "":
        return DEFAULT_DEPENDENCY_MIN_AGE_DAYS

    try:
        days = int(value)
    except ValueError as exc:
        raise ValueError(f"{DEPENDENCY_MIN_AGE_DAYS_ENV} must be an integer number of days") from exc
    if days < 0:
        raise ValueError(f"{DEPENDENCY_MIN_AGE_DAYS_ENV} must be 0 or greater")
    return days


def dependency_exclude_newer_cutoff(
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return an ISO-8601 cutoff for package-manager freshness gates."""
    days = dependency_min_age_days(env=env)
    if days == 0:
        return None

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def npm_freshness_args(env: Mapping[str, str] | None = None) -> list[str]:
    """Return npm arguments that avoid resolving very new package versions."""
    cutoff = dependency_exclude_newer_cutoff(env=env)
    return [f"--before={cutoff}"] if cutoff else []


def uv_exclude_newer_args(env: Mapping[str, str] | None = None) -> list[str]:
    """Return uv arguments that avoid resolving very new package versions."""
    cutoff = dependency_exclude_newer_cutoff(env=env)
    return ["--exclude-newer", cutoff] if cutoff else []


def _parse_github_release_timestamp(value: str) -> datetime | None:
    """Parse a GitHub release timestamp into an aware UTC datetime."""
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def order_preferred_github_releases(
    releases: list[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Mapping[str, Any]]:
    """Order GitHub releases by preference, avoiding very new stable releases when possible."""
    stable_releases = [
        release
        for release in releases
        if not bool(release.get("draft")) and not bool(release.get("prerelease"))
    ]
    if not stable_releases:
        raise ValueError("No stable GitHub releases available")

    cutoff = dependency_exclude_newer_cutoff(now=now, env=env)
    if cutoff is None:
        return stable_releases

    cutoff_dt = _parse_github_release_timestamp(cutoff)
    if cutoff_dt is None:
        return stable_releases

    aged_releases: list[Mapping[str, Any]] = []
    fresh_releases: list[Mapping[str, Any]] = []
    for release in stable_releases:
        published_at = release.get("published_at")
        if not isinstance(published_at, str):
            fresh_releases.append(release)
            continue
        published_dt = _parse_github_release_timestamp(published_at)
        if published_dt is None:
            fresh_releases.append(release)
            continue
        if published_dt <= cutoff_dt:
            aged_releases.append(release)
        else:
            fresh_releases.append(release)
    return aged_releases + fresh_releases


def select_preferred_github_release(
    releases: list[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Return the preferred GitHub release after applying freshness policy."""
    ordered = order_preferred_github_releases(releases, now=now, env=env)
    return ordered[0]
