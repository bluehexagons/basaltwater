"""Shared deployment utilities for both local and remote environments."""

from __future__ import annotations
import hashlib
import os
import re
import stat
import subprocess
from typing import Optional

from lib.atomic_io import write_json_atomic
from lib.state_read import StateReadError, read_state_object
from lib.validation import validate_filesystem_path


def extract_repo_name(git_url: str) -> str:
    """Return the human-readable final repository name from a Git URL."""
    repo_name = git_url.rstrip('/').split('/')[-1]
    return repo_name.removesuffix('.git')


def repository_stage_name(git_url: str) -> str:
    """Return a stable upload directory unique to the full repository URL."""
    repo_name = extract_repo_name(git_url)
    if repo_name in {"", ".", ".."}:
        raise ValueError(f"Unsafe repository name derived from {git_url}")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", repo_name).strip(".-")
    if not safe_name:
        raise ValueError(f"Unsafe repository name derived from {git_url}")
    digest = hashlib.sha256(git_url.encode("utf-8")).hexdigest()[:16]
    return f"{safe_name[:180]}-{digest}"


def validate_repository_source_tree(source_path: str) -> None:
    """Reject links and special files before inspecting or uploading a checkout."""
    validate_filesystem_path(source_path, must_exist=True)
    if os.path.islink(source_path) or not os.path.isdir(source_path):
        raise ValueError(f"Repository source must be a directory: {source_path}")
    pending = [source_path]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    pending.append(entry.path)
                elif not stat.S_ISREG(mode):
                    raise ValueError(
                        f"Repository source contains a link or special file: {entry.path}"
                    )


def parse_deploy_spec(deploy_spec: str) -> tuple[Optional[str], str]:
    """Parse deployment spec into (domain, path). Returns (None, path) for local paths."""
    if deploy_spec.startswith('/'):
        return (None, deploy_spec)
    
    parts = deploy_spec.split('/', 1)
    domain = parts[0]
    path = '/' + parts[1] if len(parts) > 1 else '/'
    
    return (domain, path)


def create_safe_directory_name(domain: Optional[str], path: str) -> str:
    """Create safe directory name from domain and path."""
    if domain is None:
        safe_path = path.strip('/').replace('/', '_')
        return safe_path if safe_path else 'root'
    
    safe_domain = domain.replace('.', '_')
    safe_path = path.strip('/').replace('/', '_')
    
    if safe_path:
        return f"{safe_domain}__{safe_path}"
    else:
        return safe_domain


def detect_project_type(repo_path: str) -> str:
    """Detect a supported legacy project type: node, static, or unknown."""

    if os.path.exists(os.path.join(repo_path, "package.json")):
        return "node"

    if os.path.exists(os.path.join(repo_path, "index.html")) or os.path.exists(os.path.join(repo_path, "public", "index.html")):
        return "static"

    return "unknown"


def is_ruby_project(repo_path: str) -> bool:
    """Return whether a source tree contains Ruby/Rails project markers.

    Ruby support was removed, but a narrow detector remains so v2.0.0 and later
    releases can refuse legacy deployments before modifying the target.
    """
    return any(
        os.path.exists(path)
        for path in (
            os.path.join(repo_path, ".ruby-version"),
            os.path.join(repo_path, "Gemfile"),
            os.path.join(repo_path, "config.ru"),
            os.path.join(repo_path, "config", "environment.rb"),
            os.path.join(repo_path, "bin", "rails"),
        )
    )


def get_project_root(repo_path: str, project_type: str) -> str:
    """Get the root directory for serving the project."""
    if project_type == "node":
        # Check for build output directories first
        for build_dir in ["dist", "build", "out"]:
            full_path = os.path.join(repo_path, build_dir)
            if os.path.exists(full_path):
                return full_path
        # For projects without a build step (e.g., static sites served via http-server)
        for source_dir in ["html", "public"]:
            full_path = os.path.join(repo_path, source_dir)
            if os.path.exists(full_path):
                return full_path
        return repo_path

    for static_dir in ["html", "public", "static"]:
        full_path = os.path.join(repo_path, static_dir)
        if os.path.exists(full_path):
            return full_path

    return repo_path


def get_git_commit_hash(repo_path: str) -> Optional[str]:
    """Get current git commit hash from a repository directory."""
    git_dir = os.path.join(repo_path, '.git')
    
    if not os.path.exists(git_dir):
        return None
    
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    
    return None


def get_deployment_metadata_path(deployment_path: str) -> str:
    """Get path to deployment metadata file."""
    return os.path.join(deployment_path, '.deploy_metadata.json')


def save_deployment_metadata(deployment_path: str, git_url: str, commit_hash: Optional[str]) -> None:
    """Save deployment metadata to track versions."""
    metadata: dict[str, str | None] = {
        'git_url': git_url,
        'commit_hash': commit_hash
    }
    metadata_path = get_deployment_metadata_path(deployment_path)
    
    try:
        write_json_atomic(metadata_path, metadata)
    except OSError as e:
        print(f"  ⚠ Warning: Could not save deployment metadata: {e}")


def load_deployment_metadata(deployment_path: str) -> Optional[dict[str, str | None]]:
    """Read bounded metadata; refuse invalid state instead of rebuilding blindly."""
    metadata_path = get_deployment_metadata_path(deployment_path)
    
    metadata = read_state_object(metadata_path, versioned=False)
    if metadata is None:
        return None
    if (
        not isinstance(metadata.get('git_url'), str)
        or not metadata['git_url']
        or 'commit_hash' not in metadata
        or (metadata['commit_hash'] is not None and not isinstance(metadata['commit_hash'], str))
    ):
        raise StateReadError(metadata_path, "invalid deployment metadata fields")
    return metadata


def should_redeploy(deployment_path: str, git_url: str, new_commit_hash: Optional[str], full_deploy: bool) -> bool:
    """Determine if a deployment should be rebuilt.
    
    Args:
        deployment_path: Path to the existing deployment
        git_url: Git URL being deployed
        new_commit_hash: Commit hash of the new deployment
        full_deploy: If True, always redeploy
    
    Returns:
        True if should redeploy, False to skip
    """
    if full_deploy:
        return True
    
    if not os.path.exists(deployment_path):
        return True
    
    if new_commit_hash is None:
        return True
    
    metadata = load_deployment_metadata(deployment_path)
    if metadata is None:
        return True
    
    if metadata.get('git_url') != git_url:
        return True
    
    if metadata.get('commit_hash') != new_commit_hash:
        return True
    
    return False
