"""Manage the Git worktree used by an installed infra-tools launcher."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from lib.atomic_io import write_json_atomic
from lib.installation_info import read_installation_metadata
from lib.remote_utils import CommandTimeoutError, run
from lib.types import JSONDict
from lib.validation import validate_channel, validate_filesystem_path


CHANNEL_STATE_DIR = ".infra_tools"
CHANNEL_STATE_FILENAME = "channel.json"
_VERSION_TAG_PATTERN = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)(?P<suffix>[-+][0-9A-Za-z.-]+)?$"
)


class ChannelError(RuntimeError):
    """Raised when a managed channel cannot be inspected or changed."""


@dataclass(frozen=True)
class ChannelTarget:
    """A validated channel and the Git ref it currently resolves to."""

    channel: str
    ref: str
    commit: str


def managed_repository_path(script_path: str | None = None) -> str:
    """Return the Git worktree containing the active Python entry script."""

    candidate = os.path.dirname(os.path.abspath(script_path or os.path.join(os.path.dirname(__file__), "..", "infra_tools.py")))
    validate_filesystem_path(candidate, must_exist=True)
    result = _run_git(candidate, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        if read_installation_metadata(candidate) is not None:
            return os.path.realpath(candidate)
        raise ChannelError(
            "This installation is neither a managed Git worktree nor an "
            "identified setup snapshot."
        )
    return os.path.realpath(result.stdout.strip())


def channel_state_path(repo_path: str) -> str:
    """Return the ignored local state path for a managed repository."""

    return os.path.join(repo_path, CHANNEL_STATE_DIR, CHANNEL_STATE_FILENAME)


def get_channel_info(repo_path: str) -> JSONDict:
    """Return the selected channel and current worktree commit."""

    snapshot = _setup_snapshot(repo_path)
    if snapshot is not None:
        commit = snapshot.get("commit")
        return {
            "channel": "setup-snapshot",
            "branch": snapshot.get("branch"),
            "commit": commit if isinstance(commit, str) else "unknown",
            "version": snapshot["version"],
            "dirty": snapshot.get("dirty"),
            "managed": True,
            "installation_type": "setup-snapshot",
        }

    repo_path = _validate_repository(repo_path)
    commit = _current_commit(repo_path)
    state = _load_state(repo_path)
    if state is None:
        branch = _current_branch(repo_path)
        return {
            "channel": None,
            "branch": branch,
            "commit": commit,
            "managed": False,
        }

    state["commit"] = commit
    state["branch"] = _current_branch(repo_path)
    state["managed"] = True
    return state


def switch_channel(repo_path: str, channel: str) -> JSONDict:
    """Fetch and switch a managed worktree to ``channel``."""

    validate_channel(channel)
    if _setup_snapshot(repo_path) is not None:
        raise ChannelError(
            "Setup snapshots cannot switch channels; rerun setup from the "
            "controller source you want to deploy."
        )
    repo_path = _validate_repository(repo_path)
    _require_clean_worktree(repo_path)
    _fetch_origin(repo_path)
    target = _resolve_channel(repo_path, channel)
    _checkout(repo_path, target.ref)
    state = _save_state(repo_path, target)
    state.update(branch=None, managed=True)
    return state


def upgrade_channel(repo_path: str) -> JSONDict:
    """Install the newest commit currently available on the selected channel."""

    if _setup_snapshot(repo_path) is not None:
        raise ChannelError(
            "Setup snapshots are upgraded by rerunning setup from the controller."
        )
    repo_path = _validate_repository(repo_path)
    state = _load_state(repo_path)
    if state is None:
        raise ChannelError(
            "This checkout has no managed channel. Install it with install.sh "
            "or select a channel first."
        )

    channel = str(state["channel"])
    validate_channel(channel)
    _require_clean_worktree(repo_path)
    _fetch_origin(repo_path)
    target = _resolve_channel(repo_path, channel)
    current_commit = _current_commit(repo_path)
    if current_commit != target.commit:
        _checkout(repo_path, target.ref)
        branch = None
    else:
        branch = _current_branch(repo_path)
    saved_state = _save_state(repo_path, target)
    saved_state["updated"] = current_commit != target.commit
    saved_state.update(branch=branch, managed=True)
    return saved_state


def _validate_repository(repo_path: str) -> str:
    validate_filesystem_path(repo_path, must_exist=True)
    if not os.path.isdir(repo_path):
        raise ChannelError(f"Repository path is not a directory: {repo_path}")
    result = _run_git(repo_path, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise ChannelError(f"Not a Git worktree: {repo_path}")
    return os.path.realpath(result.stdout.strip())


def _setup_snapshot(repo_path: str) -> JSONDict | None:
    """Return metadata only when ``repo_path`` is not itself a Git worktree."""

    validate_filesystem_path(repo_path, must_exist=True)
    if _run_git(repo_path, ["rev-parse", "--show-toplevel"]).returncode == 0:
        return None
    return read_installation_metadata(repo_path)


def _run_git(repo_path: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            ['env', 'GIT_TERMINAL_PROMPT=0', 'git', *arguments],
            cwd=repo_path, capture_output=True, text=True, check=False, timeout=300,
        )
    except CommandTimeoutError as exc:
        raise ChannelError(
            f'Git {arguments[0]} timed out in {repo_path}; saved channel state is unchanged. '
            'Inspect git status and HEAD before retrying.'
        ) from exc


def _git_failure(action: str, result: subprocess.CompletedProcess[str]) -> ChannelError:
    details = result.stderr.strip() or result.stdout.strip() or "no additional details"
    return ChannelError(f"Git {action} failed: {details}")


def _fetch_origin(repo_path: str) -> None:
    remote = _run_git(repo_path, ["remote", "get-url", "origin"])
    if remote.returncode != 0:
        raise ChannelError("Managed repository has no origin remote")
    result = _run_git(repo_path, ["fetch", "--prune", "--tags", "origin"])
    if result.returncode != 0:
        raise _git_failure("fetch", result)


def _resolve_channel(repo_path: str, channel: str) -> ChannelTarget:
    validate_channel(channel)

    if channel == "stable":
        ref = _latest_stable_tag(repo_path)
    elif channel == "dev":
        ref = "refs/remotes/origin/main"
    elif channel.startswith("v"):
        ref = f"refs/tags/{channel}"
    elif channel.startswith("branch-"):
        branch = channel.removeprefix("branch-")
        ref = f"refs/remotes/origin/{branch}"
    else:
        commit = channel.removeprefix("commit-")
        ref = commit
        if not _commit_exists(repo_path, commit):
            fetch_result = _run_git(repo_path, ["fetch", "origin", commit])
            if fetch_result.returncode != 0:
                raise _git_failure("fetch commit", fetch_result)

    commit = _commit_for_ref(repo_path, ref)
    return ChannelTarget(channel=channel, ref=ref, commit=commit)


def _latest_stable_tag(repo_path: str) -> str:
    result = _run_git(repo_path, ["tag", "--list", "v*"])
    if result.returncode != 0:
        raise _git_failure("list tags", result)

    candidates: list[tuple[tuple[int, int, int], str]] = []
    for tag in result.stdout.splitlines():
        match = _VERSION_TAG_PATTERN.fullmatch(tag.strip())
        if match is None or match.group("suffix"):
            continue
        candidates.append(
            (
                (
                    int(match.group("major")),
                    int(match.group("minor")),
                    int(match.group("patch")),
                ),
                tag.strip(),
            )
        )

    if not candidates:
        raise ChannelError("No versioned release tags are available for stable")
    return max(candidates)[1]


def _commit_for_ref(repo_path: str, ref: str) -> str:
    result = _run_git(repo_path, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise ChannelError(f"Channel ref does not exist: {ref}")
    return result.stdout.strip()


def _commit_exists(repo_path: str, commit: str) -> bool:
    result = _run_git(repo_path, ["cat-file", "-e", f"{commit}^{{commit}}"])
    return result.returncode == 0


def _checkout(repo_path: str, ref: str) -> None:
    result = _run_git(repo_path, ["checkout", "--detach", ref])
    if result.returncode != 0:
        raise _git_failure("checkout", result)


def _require_clean_worktree(repo_path: str) -> None:
    result = _run_git(repo_path, ["status", "--porcelain"])
    if result.returncode != 0:
        raise _git_failure("inspect worktree", result)
    if result.stdout.strip():
        raise ChannelError(
            "Refusing to change a worktree with local changes; commit or stash them first"
        )


def _current_commit(repo_path: str) -> str:
    return _commit_for_ref(repo_path, "HEAD")


def _current_branch(repo_path: str) -> str | None:
    result = _run_git(repo_path, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = result.stdout.strip()
    return branch or None


def _load_state(repo_path: str) -> JSONDict | None:
    path = channel_state_path(repo_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file_obj:
            state: Any = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise ChannelError(f"Could not read channel state: {path}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("channel"), str):
        raise ChannelError(f"Invalid channel state: {path}")
    validate_channel(state["channel"])
    return dict(state)


def _save_state(repo_path: str, target: ChannelTarget) -> JSONDict:
    state: JSONDict = {
        "channel": target.channel,
        "commit": target.commit,
        "updated_at": int(time.time()),
    }
    write_json_atomic(channel_state_path(repo_path), state, mode=0o600, sort_keys=True)
    return state
