"""Safe Git worktree lifecycle helpers for concurrent coding-agent tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import subprocess
from typing import Optional

from lib.types import JSONDict
from lib.validation import validate_filesystem_path


_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~:+-]{0,255}$")
_DEFAULT_WORKTREE_RELATIVE = os.path.join(
    ".local",
    "share",
    "basaltwater",
    "worktrees",
)


def _effective_home() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError as exc:
        raise RuntimeError("Could not resolve the current agent user's home") from exc


def _expand_home_path(path: str, home: str) -> str:
    if path == "~":
        return home
    if path.startswith(f"~{os.path.sep}"):
        return os.path.join(home, path[2:])
    return path


def _git(
    cwd: str,
    arguments: list[str],
    *,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", cwd, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise RuntimeError(detail)
    return result


def _repository_root(repository: str) -> str:
    candidate = os.path.abspath(_expand_home_path(repository, _effective_home()))
    validate_filesystem_path(candidate, must_exist=True)
    if not os.path.isdir(candidate):
        raise ValueError(f"Repository path is not a directory: {candidate}")
    result = _git(candidate, ["rev-parse", "--show-toplevel"])
    root = os.path.realpath(result.stdout.strip())
    validate_filesystem_path(root, must_exist=True)
    if not os.path.isdir(root):
        raise ValueError(f"Git repository root is not a directory: {root}")
    return root


def _within(path: str, parent: str) -> bool:
    try:
        resolved_parent = os.path.realpath(parent)
        return os.path.commonpath(
            (os.path.realpath(path), resolved_parent)
        ) == resolved_parent
    except ValueError:
        return False


def _managed_root(home: str, override: Optional[str], *, create: bool) -> str:
    resolved_home = os.path.realpath(os.path.abspath(home))
    candidate = os.path.abspath(
        _expand_home_path(override, resolved_home)
        if override
        else os.path.join(resolved_home, _DEFAULT_WORKTREE_RELATIVE)
    )
    validate_filesystem_path(candidate, must_exist=False)
    if candidate == resolved_home or not _within(candidate, resolved_home):
        raise ValueError("Agent worktree root must remain below the current user's home")

    relative = os.path.relpath(candidate, resolved_home)
    current = resolved_home
    for component in relative.split(os.path.sep):
        current = os.path.join(current, component)
        if os.path.lexists(current):
            if os.path.islink(current) or not os.path.isdir(current):
                raise ValueError(f"Unsafe agent worktree directory: {current}")
        elif create:
            os.mkdir(current, mode=0o700)
        else:
            raise ValueError(f"Agent worktree root does not exist: {candidate}")
    return candidate


def _validate_task(task: str) -> str:
    if _TASK_NAME_RE.fullmatch(task) is None or ".." in task or task.endswith(".lock"):
        raise ValueError(
            "Task name must use 1-64 letters, digits, '.', '_', or '-' without '..'"
        )
    branch = f"agent/{task}"
    if _git(".", ["check-ref-format", "--branch", branch], check=False).returncode != 0:
        raise ValueError(f"Task name does not form a safe Git branch: {task}")
    return branch


def _validate_base(repository: str, base: str) -> str:
    if _BASE_RE.fullmatch(base) is None or ".." in base:
        raise ValueError(f"Unsafe Git base revision: {base}")
    result = _git(
        repository,
        ["rev-parse", "--verify", f"{base}^{{commit}}"],
    )
    commit = result.stdout.strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None:
        raise RuntimeError("Git returned an invalid base commit")
    return commit


def _repository_namespace(repository: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(repository)).strip("-.")
    if not name:
        name = "repository"
    digest = hashlib.sha256(os.fsencode(repository)).hexdigest()[:10]
    return f"{name}-{digest}"


def _worktree_record(path: str, *, main_path: Optional[str] = None) -> JSONDict:
    root = _repository_root(path)
    head = _git(root, ["rev-parse", "HEAD"]).stdout.strip()
    branch_result = _git(root, ["symbolic-ref", "-q", "--short", "HEAD"], check=False)
    status = _git(root, ["status", "--porcelain", "--untracked-files=normal"])
    return {
        "path": root,
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else None,
        "head": head,
        "dirty": bool(status.stdout),
        "main": bool(main_path and os.path.samefile(root, main_path)),
    }


def create_agent_worktree(
    repository: str,
    task: str,
    *,
    base: str = "HEAD",
    root: Optional[str] = None,
    home: Optional[str] = None,
) -> JSONDict:
    """Create one isolated worktree and a dedicated ``agent/`` branch."""
    user_home = os.path.abspath(home or _effective_home())
    repository_root = _repository_root(repository)
    branch = _validate_task(task)
    base_commit = _validate_base(repository_root, base)
    managed_root = _managed_root(user_home, root, create=True)
    namespace = os.path.join(managed_root, _repository_namespace(repository_root))
    if not os.path.exists(namespace):
        os.mkdir(namespace, mode=0o700)
    elif os.path.islink(namespace) or not os.path.isdir(namespace):
        raise ValueError(f"Unsafe agent worktree namespace: {namespace}")
    destination = os.path.join(namespace, task)
    if os.path.lexists(destination):
        raise ValueError(f"Agent worktree already exists: {destination}")
    if _git(
        repository_root,
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    ).returncode == 0:
        raise ValueError(f"Agent task branch already exists: {branch}")

    try:
        _git(
            repository_root,
            ["worktree", "add", "-b", branch, destination, base_commit],
            timeout=600,
        )
    except RuntimeError:
        try:
            os.rmdir(destination)
        except OSError:
            pass
        raise
    record = _worktree_record(destination, main_path=repository_root)
    record.update(
        {
            "repository": repository_root,
            "base": base,
            "base_commit": base_commit,
            "task": task,
        }
    )
    return record


def list_agent_worktrees(repository: str) -> list[JSONDict]:
    """List worktrees without exposing changed file names or file contents."""
    repository_root = _repository_root(repository)
    result = _git(repository_root, ["worktree", "list", "--porcelain"])
    records: list[JSONDict] = []
    for block in result.stdout.strip().split("\n\n"):
        fields: dict[str, str | bool] = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" ")
            fields[key] = value if separator else True
        path = fields.get("worktree")
        if not isinstance(path, str) or not os.path.isdir(path):
            continue
        record = _worktree_record(path, main_path=repository_root)
        record["locked"] = bool(fields.get("locked"))
        record["prunable"] = bool(fields.get("prunable"))
        records.append(record)
    return records


def remove_agent_worktree(
    path: str,
    *,
    root: Optional[str] = None,
    home: Optional[str] = None,
    dry_run: bool = False,
) -> JSONDict:
    """Remove a clean, merged, basaltwater-managed agent worktree and branch."""
    user_home = os.path.abspath(home or _effective_home())
    managed_root = _managed_root(user_home, root, create=False)
    worktree = _repository_root(path)
    if worktree == managed_root or not _within(worktree, managed_root):
        raise ValueError("Only worktrees below the managed agent root can be removed")

    common_dir_value = _git(worktree, ["rev-parse", "--git-common-dir"]).stdout.strip()
    common_dir = os.path.realpath(
        common_dir_value
        if os.path.isabs(common_dir_value)
        else os.path.join(worktree, common_dir_value)
    )
    primary = _repository_root(os.path.dirname(common_dir))
    worktrees = list_agent_worktrees(primary)
    if not any(os.path.samefile(worktree, str(item["path"])) for item in worktrees):
        raise ValueError(f"Path is not a registered Git worktree: {worktree}")
    if os.path.samefile(worktree, primary):
        raise ValueError("The primary repository checkout cannot be removed")

    record = _worktree_record(worktree, main_path=primary)
    branch = record.get("branch")
    if not isinstance(branch, str) or not branch.startswith("agent/"):
        raise ValueError("Only managed agent/* task branches can be removed")
    if record["dirty"]:
        raise ValueError("Agent worktree has uncommitted or untracked changes")
    target = _git(primary, ["rev-parse", "HEAD"]).stdout.strip()
    merged = _git(
        primary,
        ["merge-base", "--is-ancestor", str(record["head"]), target],
        check=False,
    ).returncode == 0
    if not merged:
        raise ValueError("Agent task branch is not merged into the primary checkout")

    result: JSONDict = {
        "path": worktree,
        "repository": primary,
        "branch": branch,
        "head": record["head"],
        "status": "planned" if dry_run else "removed",
    }
    if dry_run:
        return result
    _git(primary, ["worktree", "remove", worktree])
    _git(primary, ["branch", "-d", branch])
    return result


def _print_result(value: JSONDict | list[JSONDict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    records = value if isinstance(value, list) else [value]
    for record in records:
        marker = "main" if record.get("main") else str(record.get("status") or "worktree")
        dirty = " dirty" if record.get("dirty") else ""
        print(f"{record['path']}: {record.get('branch') or 'detached'} ({marker}{dirty})")


def run_agent_workspace_command(args: argparse.Namespace) -> int:
    """Run a parsed local worktree lifecycle command."""
    try:
        if args.agent_workspace_command == "create":
            value: JSONDict | list[JSONDict] = create_agent_worktree(
                args.repository,
                args.task,
                base=args.base,
                root=args.workspace_root,
            )
        elif args.agent_workspace_command == "list":
            value = list_agent_worktrees(args.repository)
        elif args.agent_workspace_command == "status":
            value = _worktree_record(args.path)
        elif args.agent_workspace_command == "remove":
            value = remove_agent_worktree(
                args.path,
                root=args.workspace_root,
                dry_run=args.dry_run,
            )
        else:
            print("Error: agent workspace command required (create, list, status, or remove)")
            return 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}")
        return 1
    _print_result(value, as_json=args.json)
    return 0
