#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path



WORKSPACE_ENV_VAR = "BASALTWATER_WORKSPACE"
DEFAULT_WORKSPACE_DIR = "~/.config/basaltwater"


def normalize_workspace_dir(path: str | None = None) -> str:
    """Resolve the workspace, migrating recent default client data on access."""
    raw_path = path if path is not None else os.environ.get(WORKSPACE_ENV_VAR, DEFAULT_WORKSPACE_DIR)
    expanded_path = os.path.expanduser(raw_path)
    workspace = os.path.abspath(expanded_path)
    if workspace == os.path.abspath(os.path.expanduser(DEFAULT_WORKSPACE_DIR)):
        from lib.client_migration import migrate_client_workspace

        migrate_client_workspace(Path(workspace))
    return workspace


def set_workspace_dir(path: str | None) -> str:
    """Persist the active workspace root in the process environment."""
    workspace_dir = normalize_workspace_dir(path)
    os.environ[WORKSPACE_ENV_VAR] = workspace_dir
    return workspace_dir


def get_workspace_dir() -> str:
    """Return the active workspace root."""
    return normalize_workspace_dir()


def get_setup_cache_dir(path: str | None = None) -> str:
    """Return the setup cache directory inside the workspace."""
    return os.path.join(normalize_workspace_dir(path), "setups")


def get_history_dir(path: str | None = None) -> str:
    """Return the history directory inside the workspace."""
    return os.path.join(normalize_workspace_dir(path), "history")


def get_known_hosts_path(path: str | None = None) -> str:
    """Return the workspace-managed known_hosts path."""
    return os.path.join(normalize_workspace_dir(path), "known_hosts")


def get_credentials_path(path: str | None = None) -> str:
    """Return the workspace credential store path."""
    return os.path.join(normalize_workspace_dir(path), "credentials.json")


def ensure_workspace_dir(path: str | None = None) -> str:
    """Create the workspace root and standard directories when missing."""
    workspace_dir = normalize_workspace_dir(path)
    os.makedirs(workspace_dir, mode=0o700, exist_ok=True)
    os.makedirs(get_setup_cache_dir(workspace_dir), mode=0o700, exist_ok=True)
    os.makedirs(get_history_dir(workspace_dir), mode=0o700, exist_ok=True)
    return workspace_dir
