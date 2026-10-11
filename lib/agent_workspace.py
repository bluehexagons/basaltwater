"""Safe Git worktree lifecycle helpers for concurrent coding-agent tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
from typing import Optional

from lib.types import JSONDict
from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path


_TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~:+-]{0,255}$")
_DEFAULT_WORKTREE_RELATIVE = os.path.join(
    ".local",
    "share",
    "basaltwater",
    "worktrees",
)


class WorkspaceIssue(ValueError):
    """An actionable workspace refusal with a stable automation code."""

    def __init__(self, issue: str, message: str, remediation: str) -> None:
        super().__init__(message)
        self.issue = issue
        self.remediation = remediation


def _ownership_path(worktree: str) -> str:
    return os.path.join(os.path.dirname(worktree), f".{os.path.basename(worktree)}.basaltwater-workspace.json")


def _read_ownership(worktree: str) -> JSONDict | None:
    path = _ownership_path(worktree)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise WorkspaceIssue("workspace_ownership_invalid", "Unsafe workspace ownership record",
                             "Inspect the private ownership record; restore verified metadata before cleanup.")
    value = read_json_file(path, max_bytes=64 * 1024)
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or type(value.get("uid")) is not int or value["uid"] != os.geteuid()
            or value.get("path") != worktree or not isinstance(value.get("repository"), str)
            or not isinstance(value.get("root"), str) or not isinstance(value.get("branch"), str)
            or not isinstance(value.get("task"), str) or not _TASK_NAME_RE.fullmatch(value["task"])):
        raise WorkspaceIssue("workspace_ownership_invalid", "Invalid workspace ownership record",
                             "Inspect the private ownership record; restore verified metadata before cleanup.")
    return value


def _record_ownership(worktree: str, primary: str, root: str, task: str, branch: str) -> None:
    _read_ownership(worktree)  # Preserve malformed or unsafe existing records.
    write_json_atomic(_ownership_path(worktree), {
        "version": 1, "uid": os.geteuid(), "path": worktree,
        "repository": primary, "root": root, "task": task, "branch": branch,
    }, mode=0o600)


def _ownership_status(record: JSONDict, primary: str) -> JSONDict:
    ownership = _read_ownership(str(record["path"]))
    return {
        "managed": bool(ownership and ownership["branch"] == record["branch"]
                        and ownership["repository"] == primary
                        and _within(str(record["path"]), ownership["root"])),
        "task": ownership["task"] if ownership else None,
        "ownership_branch": ownership["branch"] if ownership else None,
    }


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


def primary_agent_repository(path: str) -> str:
    """Resolve the primary checkout even when called from a linked worktree."""
    worktree = _repository_root(path)
    value = _git(worktree, ["rev-parse", "--git-common-dir"]).stdout.strip()
    common_dir = os.path.realpath(
        value if os.path.isabs(value) else os.path.join(worktree, value)
    )
    return _repository_root(os.path.dirname(common_dir))


def inspect_agent_worktree(path: str) -> JSONDict:
    """Report branch ancestry against the primary checkout's current commit."""
    primary = primary_agent_repository(path)
    record = _worktree_record(path, main_path=primary)
    destination = _worktree_record(primary, main_path=primary)
    ancestry = _git(
        primary,
        ["merge-base", "--is-ancestor", str(record["head"]), str(destination["head"])],
        check=False,
    )
    if ancestry.returncode not in (0, 1):
        raise RuntimeError("Could not determine task ancestry against the primary checkout")
    record.update({
        "repository": primary,
        "primary_branch": destination["branch"],
        "primary_head": destination["head"],
        "merged_into_primary": ancestry.returncode == 0,
        **_ownership_status(record, primary),
    })
    return record


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
    info = os.stat(candidate)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("Agent worktree root must be owned by the current user without shared write access")
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
    branch: Optional[str] = None,
) -> JSONDict:
    """Create one isolated, explicitly owned worktree and a new task branch."""
    user_home = os.path.abspath(home or _effective_home())
    repository_root = primary_agent_repository(repository)
    default_branch = _validate_task(task)
    branch = _validate_branch(repository_root, branch) if branch is not None else default_branch
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
    _record_ownership(destination, repository_root, managed_root, task, branch)
    record.update(
        {
            "repository": repository_root,
            "base": base,
            "base_commit": base_commit,
            "task": task,
            "managed": True,
        }
    )
    return record


def list_agent_worktrees(repository: str) -> list[JSONDict]:
    """List worktrees without exposing changed file names or file contents."""
    repository_root = primary_agent_repository(repository)
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
        record.update(_ownership_status(record, repository_root))
        records.append(record)
    return records


def _validate_branch(repository: str, branch: str) -> str:
    if not isinstance(branch, str) or not 1 <= len(branch) <= 255 or branch.startswith("-"):
        raise ValueError("Invalid workspace branch name")
    result = _git(repository, ["check-ref-format", "--branch", branch], check=False)
    if result.returncode or result.stdout.strip() != branch:
        raise ValueError("Invalid workspace branch name")
    return branch


def adopt_agent_worktree(path: str, *, task: Optional[str] = None, root: Optional[str] = None,
                        home: Optional[str] = None) -> JSONDict:
    """Explicitly register an existing owned worktree without moving or cleaning it."""
    managed_root = _managed_root(os.path.abspath(home or _effective_home()), root, create=False)
    worktree = _repository_root(path)
    primary = primary_agent_repository(worktree)
    if worktree == managed_root or not _within(worktree, managed_root) or os.path.samefile(worktree, primary):
        raise ValueError("Only secondary worktrees below the managed agent root can be adopted")
    if os.stat(worktree).st_uid != os.geteuid():
        raise ValueError("Workspace must be owned by the current user")
    registered = list_agent_worktrees(primary)
    if not any(os.path.samefile(worktree, str(item["path"])) for item in registered):
        raise ValueError("Workspace is not registered with its primary repository")
    record = _worktree_record(worktree, main_path=primary)
    branch = _validate_branch(primary, record["branch"])
    task = task or os.path.basename(worktree)
    _validate_task(task)
    _record_ownership(worktree, primary, managed_root, task, branch)
    return {**record, "repository": primary, "task": task, "managed": True, "status": "adopted"}


def inspect_workspace_group(task: str, repositories: list[str]) -> JSONDict:
    """Find matching owned task branches and paths across explicitly named repositories."""
    _validate_task(task)
    workspaces, missing = [], []
    roots = list(dict.fromkeys(primary_agent_repository(path) for path in repositories))
    for primary in roots:
        matches = [record for record in list_agent_worktrees(primary) if record.get("managed") and record.get("task") == task]
        workspaces.extend({**record, "repository": primary} for record in matches)
        if not matches:
            missing.append(primary)
    return {"task": task, "workspaces": workspaces, "missing_repositories": missing}


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

    primary = primary_agent_repository(worktree)
    worktrees = list_agent_worktrees(primary)
    if not any(os.path.samefile(worktree, str(item["path"])) for item in worktrees):
        raise ValueError(f"Path is not a registered Git worktree: {worktree}")
    if os.path.samefile(worktree, primary):
        raise ValueError("The primary repository checkout cannot be removed")

    record = _worktree_record(worktree, main_path=primary)
    branch = record.get("branch")
    ownership = _read_ownership(worktree)
    if not ownership:
        raise WorkspaceIssue("workspace_unowned", "Workspace has no explicit Basaltwater ownership record",
                             "Inspect the worktree, then use workspace adopt with its managed root before cleanup.")
    if ownership["repository"] != primary or ownership["root"] != managed_root or ownership["branch"] != branch:
        raise WorkspaceIssue("workspace_ownership_mismatch", "Workspace no longer matches its ownership record",
                             "Inspect the repository, root and branch; explicitly adopt the intended worktree again.")
    if record["dirty"]:
        raise WorkspaceIssue("workspace_dirty", "Agent worktree has uncommitted or untracked changes",
                             "Review and commit or preserve the worktree changes before cleanup.")
    target = _git(primary, ["rev-parse", "HEAD"]).stdout.strip()
    ancestry = _git(
        primary,
        ["merge-base", "--is-ancestor", str(record["head"]), target],
        check=False,
    )
    if ancestry.returncode not in (0, 1):
        raise RuntimeError("Could not determine task ancestry against the primary checkout")
    if ancestry.returncode == 1:
        raise WorkspaceIssue("workspace_unmerged",
            "Agent task branch is not merged into the primary checkout's current HEAD; "
            "cherry-pick and squash do not preserve task ancestry. Review and merge the "
            "task branch before cleanup, or retain it for later integration.",
            "Review and merge the task branch into the primary checkout, then retry cleanup.",
        )

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
    os.unlink(_ownership_path(worktree))
    return result


def _print_result(value: JSONDict | list[JSONDict], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    records = value if isinstance(value, list) else [value]
    if isinstance(value, dict) and "workspaces" in value:
        records = value["workspaces"]
    for record in records:
        marker = "main" if record.get("main") else str(record.get("status") or "worktree")
        dirty = " dirty" if record.get("dirty") else ""
        print(f"{record['path']}: {record.get('branch') or 'detached'} ({marker}{dirty})")
        if "merged_into_primary" in record:
            state = "merged" if record["merged_into_primary"] else "not merged"
            print(f"  {state} into {record.get('primary_branch') or 'detached primary HEAD'}")


def run_agent_workspace_command(args: argparse.Namespace) -> int:
    """Run a parsed local worktree lifecycle command."""
    try:
        if args.agent_workspace_command == "create":
            value: JSONDict | list[JSONDict] = create_agent_worktree(
                args.repository,
                args.task,
                base=args.base,
                root=args.workspace_root,
                branch=getattr(args, "branch", None),
            )
        elif args.agent_workspace_command == "list":
            value = list_agent_worktrees(args.repository)
        elif args.agent_workspace_command == "status":
            value = inspect_agent_worktree(args.path)
        elif args.agent_workspace_command == "adopt":
            value = adopt_agent_worktree(args.path, task=args.task, root=args.workspace_root)
        elif args.agent_workspace_command == "group":
            value = inspect_workspace_group(args.task, args.repositories)
        elif args.agent_workspace_command == "remove":
            value = remove_agent_worktree(
                args.path,
                root=args.workspace_root,
                dry_run=args.dry_run,
            )
        else:
            raise WorkspaceIssue("workspace_command_required", "Agent workspace command required",
                                 "Choose create, adopt, list, group, status, or remove.")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": False, "error": str(exc),
                "issue": getattr(exc, "issue", "invalid_workspace" if isinstance(exc, ValueError) else "workspace_operation_failed"),
                "remediation": getattr(exc, "remediation", "Check the path, Git state and permissions; inspect the worktree before retrying."),
            }, indent=2))
        else:
            print(f"Error: {exc}")
        return 1
    _print_result(value, as_json=bool(getattr(args, "json", False)))
    return 0
