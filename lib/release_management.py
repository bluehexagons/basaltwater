"""Shared helpers for GitHub release-backed service installs."""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from lib.atomic_io import write_json_atomic
from lib.remote_utils import run
from lib.update_policy import order_preferred_github_releases
from lib.validation import validate_filesystem_path, validate_no_control_characters


_RELEASE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SHA256_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


def validate_release_tag(value: str) -> str:
    """Return a release tag that is safe as metadata and a path component."""
    if not isinstance(value, str) or not _RELEASE_TAG_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid release tag: {value}")
    validate_no_control_characters(value, "release tag")
    return value


def validate_release_download_url(value: str) -> str:
    """Require an HTTPS release asset URL without embedded credentials."""
    if not isinstance(value, str) or not value:
        raise ValueError("Release download URL must be a non-empty string")
    validate_no_control_characters(value, "release download URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Release download URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Release download URL must not embed credentials")
    return value


def validate_release_sha256_digest(value: str) -> str:
    """Return the normalized hexadecimal value of a GitHub asset SHA-256."""
    if not isinstance(value, str):
        raise ValueError("Release asset digest must be a sha256 value")
    match = _SHA256_DIGEST_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(
            "Release asset digest must be sha256 followed by 64 hexadecimal characters"
        )
    return match.group(1).lower()


def detect_release_arch() -> str:
    """Return the GitHub release architecture suffix for this machine."""
    result = run("uname -m", capture_output=True)
    arch = (result.stdout or "").strip()
    if not arch:
        raise RuntimeError("Could not detect release architecture")
    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    try:
        return arch_map[arch]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported release architecture: {arch}") from exc


def fetch_github_releases(repo: str, *, per_page: int = 20) -> list[Mapping[str, Any]]:
    """Fetch recent GitHub releases for a repository."""
    api_url = f"https://api.github.com/repos/{repo}/releases?per_page={per_page}"
    result = run(
        f"curl -sf {shlex.quote(api_url)}",
        check=False,
        capture_output=True,
        display_cmd=f"curl -sf https://api.github.com/repos/{repo}/releases?per_page={per_page}",
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"Failed to fetch {repo} release data from GitHub")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse {repo} release data: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected {repo} release response payload")
    return payload


def select_preferred_github_release_asset(
    releases: list[Mapping[str, Any]],
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
) -> tuple[str, str]:
    """Return the preferred release tag and asset URL matching the selector."""
    return _select_github_release_asset(
        order_preferred_github_releases(releases),
        asset_matches=asset_matches,
        missing_asset_description=missing_asset_description,
    )


def select_latest_github_release_asset(
    releases: list[Mapping[str, Any]],
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
) -> tuple[str, str]:
    """Return the latest stable release tag and asset URL matching the selector."""
    stable_releases = [
        release
        for release in releases
        if not bool(release.get("draft")) and not bool(release.get("prerelease"))
    ]
    return _select_github_release_asset(
        stable_releases,
        asset_matches=asset_matches,
        missing_asset_description=missing_asset_description,
    )


def _select_github_release_asset(
    releases: list[Mapping[str, Any]],
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
) -> tuple[str, str]:
    """Return the first matching release asset from an ordered release list."""
    if not releases:
        raise RuntimeError(missing_asset_description)
    for release in releases:
        tag_name = release.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_name = asset.get("name")
            download_url = asset.get("browser_download_url")
            if (
                isinstance(asset_name, str)
                and isinstance(download_url, str)
                and asset_matches(tag_name, asset_name)
            ):
                try:
                    return (
                        validate_release_tag(tag_name),
                        validate_release_download_url(download_url),
                    )
                except ValueError:
                    continue
    raise RuntimeError(missing_asset_description)


def fetch_preferred_github_release_asset(
    repo: str,
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
    per_page: int = 20,
) -> tuple[str, str]:
    """Fetch and select the preferred GitHub release asset for a repository."""
    releases = fetch_github_releases(repo, per_page=per_page)
    return select_preferred_github_release_asset(
        releases,
        asset_matches=asset_matches,
        missing_asset_description=missing_asset_description,
    )


def fetch_preferred_verified_github_release_asset(
    repo: str,
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
    per_page: int = 20,
) -> tuple[str, str, str]:
    """Fetch a preferred release asset with its publisher-provided SHA-256."""
    releases = order_preferred_github_releases(
        fetch_github_releases(repo, per_page=per_page)
    )
    for release in releases:
        tag_name = release.get("tag_name")
        assets = release.get("assets")
        if not isinstance(tag_name, str) or not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_name = asset.get("name")
            download_url = asset.get("browser_download_url")
            digest = asset.get("digest")
            if not (
                isinstance(asset_name, str)
                and isinstance(download_url, str)
                and isinstance(digest, str)
                and asset_matches(tag_name, asset_name)
            ):
                continue
            try:
                return (
                    validate_release_tag(tag_name),
                    validate_release_download_url(download_url),
                    validate_release_sha256_digest(digest),
                )
            except ValueError:
                continue
    raise RuntimeError(
        f"{missing_asset_description}; a publisher-provided SHA-256 is required"
    )


def fetch_latest_verified_github_release_asset(
    repo: str,
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
    per_page: int = 20,
) -> tuple[str, str, str]:
    """Fetch the newest stable release asset and its publisher SHA-256."""
    releases = fetch_github_releases(repo, per_page=per_page)
    for release in releases:
        if bool(release.get("draft")) or bool(release.get("prerelease")):
            continue
        tag_name = release.get("tag_name")
        assets = release.get("assets")
        if not isinstance(tag_name, str) or not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_name = asset.get("name")
            download_url = asset.get("browser_download_url")
            digest = asset.get("digest")
            if not (
                isinstance(asset_name, str)
                and isinstance(download_url, str)
                and isinstance(digest, str)
                and asset_matches(tag_name, asset_name)
            ):
                continue
            try:
                return (
                    validate_release_tag(tag_name),
                    validate_release_download_url(download_url),
                    validate_release_sha256_digest(digest),
                )
            except ValueError:
                continue
    raise RuntimeError(
        f"{missing_asset_description}; a publisher-provided SHA-256 is required"
    )


def fetch_latest_github_release_asset(
    repo: str,
    *,
    asset_matches: Callable[[str, str], bool],
    missing_asset_description: str,
    per_page: int = 20,
) -> tuple[str, str]:
    """Fetch and select the latest stable GitHub release asset for a repository."""
    releases = fetch_github_releases(repo, per_page=per_page)
    return select_latest_github_release_asset(
        releases,
        asset_matches=asset_matches,
        missing_asset_description=missing_asset_description,
    )


def load_json_state(
    path: str,
    *,
    read_error_label: str,
    invalid_state_message: str,
) -> dict[str, Any]:
    """Load a JSON object state file, returning an empty mapping when unavailable."""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ⚠ Warning: Failed to read {read_error_label}: {exc}")
        return {}
    if not isinstance(payload, dict):
        print(f"  ⚠ Warning: {invalid_state_message}")
        return {}
    return payload


def write_json_state(path: str, payload: Mapping[str, Any], *, mode: int | None = None) -> None:
    """Persist a JSON state mapping."""
    write_json_atomic(path, payload, mode=mode or 0o600, sort_keys=True)


def install_binary_release(
    *,
    binary_name: str,
    binary_path: str,
    tag_name: str,
    download_url: str,
    installed_tag: str | None,
    persist_installed_tag: Callable[[str], None],
) -> str:
    """Download and replace a release-managed binary when the tag changes."""
    validate_no_control_characters(binary_name, "release binary name")
    if (
        not binary_name
        or os.path.basename(binary_name) != binary_name
        or binary_name in {".", ".."}
    ):
        raise ValueError(f"Invalid release binary name: {binary_name}")
    validate_filesystem_path(binary_path, must_exist=False)
    tag_name = validate_release_tag(tag_name)
    download_url = validate_release_download_url(download_url)

    if installed_tag == tag_name and os.path.exists(binary_path):
        print(f"  ✓ {os.path.basename(binary_path)} already up to date ({tag_name})")
        return tag_name

    print(f"  Downloading {binary_name} ({tag_name})...")
    with tempfile.TemporaryDirectory(prefix="basaltwater-release-") as temporary_dir:
        tmp_path = os.path.join(temporary_dir, binary_name)
        download_result = run(
            "curl -fL --proto '=https' --proto-redir '=https' "
            f"-o {shlex.quote(tmp_path)} {shlex.quote(download_url)}",
            check=True,
            display_cmd=(
                "curl -fL --proto '=https' --proto-redir '=https' "
                f"-o {tmp_path} <release URL>"
            ),
        )
        if download_result.returncode != 0:
            raise RuntimeError(f"Failed to download {binary_name} {tag_name}")
        chmod_result = run(f"chmod +x {shlex.quote(tmp_path)}", check=True)
        if chmod_result.returncode != 0:
            raise RuntimeError(f"Failed to make {binary_name} {tag_name} executable")
        install_result = run(
            f"mv {shlex.quote(tmp_path)} {shlex.quote(binary_path)}",
            check=True,
        )
        if install_result.returncode != 0:
            raise RuntimeError(f"Failed to install {binary_name} {tag_name}")
    persist_installed_tag(tag_name)
    print(f"  ✓ Installed {binary_path} ({tag_name})")
    return tag_name
