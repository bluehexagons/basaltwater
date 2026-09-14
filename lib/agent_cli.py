"""Local agentic coding tool diagnostics and lifecycle management."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from lib.atomic_io import write_json_atomic
from lib.local_http import open_loopback
from lib.vendor_installer import POLICIES, download_installer
from lib.agent_maintenance import (
    DEFAULT_HOLD_HOURS,
    MAX_HOLD_HOURS,
    inspect_agent_maintenance,
)
from lib.agent_auth import AGENT_AUTH_TOOLS
from lib.agent_credentials import (
    codex_auth_is_healthy,
    codex_auth_warning,
    inspect_codex_auth_file,
)
from lib.ssh_utils import build_ssh_command, shell_join, ssh_batch_mode
from lib.types import BYTES_PER_GB, BYTES_PER_MB, JSONDict, StrList
from lib.validation import validate_filesystem_path, validate_package_name
from lib.validators import validate_host, validate_username
from lib.remote_utils import CommandTimeoutError, run as run_command


AGENT_DOCTOR_TOOLS = ("gh", "codex", "claude", "opencode")
AGENT_DOCTOR_CAPABILITIES = ("browser", "development", "host", "t3code")
DEFAULT_DOCTOR_TOOLS = ("gh", "codex", "claude", "opencode")
AGENT_UPDATE_TOOLS = ("codex", "claude", "opencode")
DEFAULT_UPDATE_TOOLS = AGENT_UPDATE_TOOLS

_AGENT_STATE_RELATIVE = os.path.join(
    ".local",
    "state",
    "infra_tools",
    "agent-tools.json",
)
_CODEX_INSTALLER_URL = POLICIES["codex"][0]
_UPDATE_TIMEOUT_SECONDS = 600
_BROWSER_MCP_WRAPPER = "/usr/local/bin/infra-tools-playwright-mcp"
_BROWSER_DOCTOR_WRAPPER = "/usr/local/bin/infra-tools-playwright-doctor"
_BROWSER_MCP_CLI = (
    "/opt/infra-tools-playwright/node_modules/@playwright/mcp/cli.js"
)
_BROWSER_OUTPUT_MAX_BYTES = 256 * BYTES_PER_MB
_BROWSER_MCP_SERVER_NAME = "infra-tools-playwright"
_BROWSER_DOCTOR_TIMEOUT_SECONDS = 210
_REMOTE_DOCTOR_TIMEOUT_SECONDS = _BROWSER_DOCTOR_TIMEOUT_SECONDS + 90
_MAX_AGENT_SKILL_BYTES = 256 * 1024
_PLAYWRIGHT_BROWSER_AGENT_SKILL_NAMES = frozenset(
    (
        "infra-tools-browser-testing",
        "infra-tools-playwright-testing",
    )
)
_T3_SERVICE_NAME = "t3code.service"
_T3_RUNTIME_RELATIVE = os.path.join(
    ".t3", "runtime"
)
_T3_SEMVER_NUMBER = r"(?:0|[1-9][0-9]*)"
_T3_SEMVER_PRERELEASE = (
    rf"(?:{_T3_SEMVER_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
_T3_VERSION_RE = re.compile(
    rf"^{_T3_SEMVER_NUMBER}\.{_T3_SEMVER_NUMBER}\.{_T3_SEMVER_NUMBER}"
    rf"(?:-{_T3_SEMVER_PRERELEASE}(?:\.{_T3_SEMVER_PRERELEASE})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_T3_DEFAULT_PORT = 3773
_T3_NATIVE_PACKAGES = ("node-pty", "msgpackr-extract")
_REMOTE_INFRA_TOOLS_PATH = "/opt/infra_tools/infra_tools.py"
_AGENT_HOST_MAINTENANCE_UNITS = (
    "security-monitor",
    "auto-update-apt",
    "cleanup-maintenance",
    "user-cache-maintenance",
    "auto-restart-if-needed",
)
_AGENT_HOST_OPTIONAL_MAINTENANCE_UNITS = (
    "auto-update-godot",
    "auto-update-node",
    "codex-auth-maintenance",
)
_AGENT_HOST_MIN_RECOMMENDED_MEMORY = 4 * BYTES_PER_GB
_AGENT_HOST_LOW_AVAILABLE_MEMORY = 512 * BYTES_PER_MB
_AGENT_HOST_DISK_WARNING_PERCENT = 80
_AGENT_HOST_DISK_CRITICAL_PERCENT = 90
_AGENT_HOST_DISK_WARNING_FREE = 4 * BYTES_PER_GB
_AGENT_HOST_DISK_CRITICAL_FREE = BYTES_PER_GB
_AGENT_STORAGE_WARNING_BYTES = {
    "npm_cache": 2 * BYTES_PER_GB,
    "browser_cache": 2 * BYTES_PER_GB,
    "t3_logs": 512 * BYTES_PER_MB,
}

_CREDENTIAL_PATHS = {
    "gh": ".config/gh/hosts.yml",
    "codex": ".codex/auth.json",
    "claude": ".claude/.credentials.json",
    "opencode": ".local/share/opencode/auth.json",
}

_UPDATE_ENVIRONMENT_REDIRECTS = {
    "bash_env",
    "cdpath",
    "codex_home",
    "env",
    "git_config_global",
    "nvm_dir",
    "oldpwd",
    "pwd",
    "tmpdir",
    "xdg_cache_home",
    "xdg_config_home",
    "xdg_data_home",
    "xdg_state_home",
}
_UPDATE_SYSTEM_PATH = os.pathsep.join(
    ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
)


def add_agent_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add local agent-tool management commands."""
    parser = subparsers.add_parser(
        "agent",
        help="Inspect local agentic coding tools",
    )
    commands = parser.add_subparsers(dest="agent_command", help="Agent commands")
    doctor = commands.add_parser(
        "doctor",
        help="Check installed agent tools and local credential files",
    )
    doctor.add_argument(
        "agent_doctor_host",
        nargs="?",
        metavar="HOST",
        help="Remote agent VM to inspect; omit HOST and USER for a local check",
    )
    doctor.add_argument(
        "agent_doctor_username",
        nargs="?",
        metavar="USER",
        help="Remote agent VM user",
    )
    doctor.add_argument(
        "--tool",
        dest="agent_doctor_tools",
        action="append",
        choices=AGENT_DOCTOR_TOOLS,
        help="Tool to require; repeat as needed",
    )
    capability_selection = doctor.add_mutually_exclusive_group()
    capability_selection.add_argument(
        "--capability",
        dest="agent_doctor_capabilities",
        action="append",
        choices=AGENT_DOCTOR_CAPABILITIES,
        help="Provisioned agent capability to verify; repeat as needed",
    )
    capability_selection.add_argument(
        "--all-capabilities",
        action="store_true",
        help=(
            "Check every provisioned capability and inventory default tools; "
            "use --tool to require specific tools"
        ),
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    doctor.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe T3 Code/Git repairs while checking readiness",
    )
    doctor_history = doctor.add_mutually_exclusive_group()
    doctor_history.add_argument(
        "--record",
        action="store_true",
        help="Privately record the redacted result for later comparison",
    )
    doctor_history.add_argument(
        "--last-record",
        action="store_true",
        help="Show the last recorded readiness result without running checks",
    )
    doctor.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    update = commands.add_parser(
        "update",
        help="Deliberately update user-installed terminal agents",
    )
    update.add_argument(
        "agent_update_host",
        nargs="?",
        metavar="HOST",
        help="Remote agent VM to update; omit HOST and USER for a local update",
    )
    update.add_argument(
        "agent_update_username",
        nargs="?",
        metavar="USER",
        help="Remote agent VM user",
    )
    update.add_argument(
        "--tool",
        dest="agent_update_tools",
        action="append",
        choices=AGENT_UPDATE_TOOLS,
        help="Tool to update; repeat as needed (default: all terminal agents)",
    )
    update.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the selected update mechanisms without changing tools",
    )
    update.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    update.add_argument(
        "--tools-only-readiness",
        action="store_true",
        help=(
            "After updating, verify only selected terminal tools; omit the full "
            "host and T3 readiness audit"
        ),
    )
    update.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    auth = commands.add_parser(
        "auth",
        help="Rotate or inspect credentials on an agent VM",
    )
    auth_commands = auth.add_subparsers(
        dest="agent_auth_command",
        help="Credential operations",
    )
    auth_set = auth_commands.add_parser(
        "set",
        help="Atomically replace one target credential",
    )
    auth_set.add_argument("agent_auth_host", metavar="HOST")
    auth_set.add_argument("agent_auth_username", metavar="USER")
    auth_set.add_argument(
        "--tool",
        dest="agent_auth_tool",
        required=True,
        choices=AGENT_AUTH_TOOLS,
        help="Credential to replace",
    )
    auth_source = auth_set.add_mutually_exclusive_group()
    auth_source.add_argument(
        "--file",
        dest="agent_auth_file",
        metavar="PATH",
        help="Controller-local credential file",
    )
    auth_source.add_argument(
        "--active",
        dest="agent_auth_active",
        action="store_true",
        help="Use the active controller user's credential file",
    )
    auth_source.add_argument(
        "--interactive",
        dest="agent_auth_interactive",
        action="store_true",
        help="Choose the source interactively",
    )
    auth_set.add_argument(
        "--git-host",
        default="github.com",
        metavar="HOST",
        help="GitHub host for gh credentials (default: github.com)",
    )
    auth_set.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")

    auth_status = auth_commands.add_parser(
        "status",
        help="Show non-secret credential and authentication status",
    )
    auth_status.add_argument("agent_auth_host", metavar="HOST")
    auth_status.add_argument("agent_auth_username", metavar="USER")
    auth_status.add_argument(
        "--tool",
        dest="agent_auth_tools",
        action="append",
        choices=AGENT_AUTH_TOOLS,
        help="Tool to inspect; repeat as needed",
    )
    auth_status.add_argument(
        "--git-host",
        default="github.com",
        metavar="HOST",
        help="GitHub host to check with gh (default: github.com)",
    )
    auth_status.add_argument("--json", action="store_true", help="Output JSON")
    auth_status.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")

    auth_pull = auth_commands.add_parser(
        "pull",
        help="Copy file-backed credentials from an existing agent VM",
    )
    auth_pull.add_argument("agent_auth_host", metavar="HOST")
    auth_pull.add_argument("agent_auth_username", metavar="USER")
    auth_pull.add_argument(
        "--tool",
        dest="agent_auth_tools",
        action="append",
        choices=AGENT_AUTH_TOOLS,
        help="Credential to pull; repeat as needed (default: every present file)",
    )
    auth_pull.add_argument(
        "--output-dir",
        metavar="PATH",
        help="Private directory for renamed outputs (default: active-user paths)",
    )
    auth_pull.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    auth_pull.add_argument("-p", "--port", type=int, default=22, help="SSH port")
    auth_pull.add_argument(
        "--overwrite",
        action="store_true",
        help="Deliberately replace other existing regular files",
    )
    web = commands.add_parser(
        "web",
        help="Pair with a remote agent web interface",
    )
    web_commands = web.add_subparsers(
        dest="agent_web_command",
        help="Web interface operations",
    )
    web_pair = web_commands.add_parser(
        "pair",
        help="Print a fresh T3 Code pairing URL from a remote VM",
    )
    web_pair.add_argument("agent_web_host", metavar="HOST")
    web_pair.add_argument("agent_web_username", metavar="USER")
    web_pair.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    workspace = commands.add_parser(
        "workspace",
        help="Manage isolated Git worktrees for concurrent agent tasks",
    )
    workspace_commands = workspace.add_subparsers(
        dest="agent_workspace_command",
        help="Workspace operations",
    )
    workspace_create = workspace_commands.add_parser(
        "create",
        help="Create an isolated worktree and agent task branch",
    )
    workspace_create.add_argument("repository", metavar="REPOSITORY")
    workspace_create.add_argument("task", metavar="TASK")
    workspace_create.add_argument("--base", default="HEAD", metavar="REVISION")
    workspace_create.add_argument("--root", dest="workspace_root", metavar="PATH")
    workspace_create.add_argument("--json", action="store_true")
    workspace_list = workspace_commands.add_parser(
        "list",
        help="List a repository's registered worktrees",
    )
    workspace_list.add_argument("repository", metavar="REPOSITORY")
    workspace_list.add_argument("--json", action="store_true")
    workspace_status = workspace_commands.add_parser(
        "status",
        help="Show branch and dirty state for one worktree",
    )
    workspace_status.add_argument("path", metavar="WORKTREE")
    workspace_status.add_argument("--json", action="store_true")
    workspace_remove = workspace_commands.add_parser(
        "remove",
        help="Remove a clean task worktree after its branch is merged",
    )
    workspace_remove.add_argument("path", metavar="WORKTREE")
    workspace_remove.add_argument("--root", dest="workspace_root", metavar="PATH")
    workspace_remove.add_argument("--dry-run", action="store_true")
    workspace_remove.add_argument("--json", action="store_true")
    maintenance = commands.add_parser(
        "maintenance",
        help="Hold or release disruptive host maintenance during agent work",
    )
    maintenance_commands = maintenance.add_subparsers(
        dest="agent_maintenance_command",
        help="Maintenance hold operations",
    )
    for action in ("hold", "status", "release"):
        action_parser = maintenance_commands.add_parser(
            action,
            help={
                "hold": "Create or renew a bounded automatic-restart hold",
                "status": "Show the current automatic-restart hold",
                "release": "Release the automatic-restart hold",
            }[action],
        )
        action_parser.add_argument(
            "agent_maintenance_host",
            nargs="?",
            metavar="HOST",
            help="Remote agent VM; omit HOST and USER for a local operation",
        )
        action_parser.add_argument(
            "agent_maintenance_username",
            nargs="?",
            metavar="USER",
            help="Remote agent VM user",
        )
        if action == "hold":
            action_parser.add_argument(
                "--hours",
                type=int,
                default=DEFAULT_HOLD_HOURS,
                metavar="N",
                help=(
                    f"Hold duration from 1 to {MAX_HOLD_HOURS} hours "
                    f"(default: {DEFAULT_HOLD_HOURS})"
                ),
            )
        action_parser.add_argument("--json", action="store_true")
        action_parser.add_argument(
            "-k",
            "--key",
            dest="ssh_key",
            help="SSH private key path",
        )
    support = commands.add_parser(
        "support-bundle",
        help="Collect a redacted local agent-host support snapshot",
    )
    support.add_argument(
        "--output",
        metavar="PATH",
        help="Write a new private JSON file below the current user's home",
    )
    support.add_argument(
        "--browser-smoke",
        action="store_true",
        help="Also launch the optional Playwright browser smoke test",
    )


def _tool_path(tool: str, home: str) -> Optional[str]:
    search_path = os.pathsep.join(
        (
            os.path.join(home, ".local", "bin"),
            os.path.join(home, ".opencode", "bin"),
            _UPDATE_SYSTEM_PATH,
        )
    )
    return shutil.which(tool, path=search_path)


def _tool_version(tool: str, path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else None


def _tool_smoke_test(path: str) -> bool:
    try:
        result = subprocess.run(
            [path, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool((result.stdout or result.stderr).strip())


def _validate_update_tools(tools: StrList) -> None:
    for tool in dict.fromkeys(tools):
        validate_package_name(tool, name="agent tool")
        if tool not in AGENT_UPDATE_TOOLS:
            raise ValueError(f"Unsupported agent update tool: {tool}")


def _state_path(home: str) -> str:
    path = os.path.join(home, _AGENT_STATE_RELATIVE)
    validate_filesystem_path(path, must_exist=False)
    return path


def _load_update_state(path: str) -> JSONDict:
    if not os.path.exists(path):
        return {"schema_version": 1, "tools": {}}
    try:
        with open(path, encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read agent update state: {path}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("tools"), dict)
    ):
        raise RuntimeError(f"Unsupported agent update state: {path}")
    return value


def _save_update_state(path: str, state: JSONDict) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    os.chmod(parent, 0o700)
    write_json_atomic(path, state, mode=0o600, sort_keys=True)


def _within_home(path: str, home: str) -> bool:
    try:
        resolved_home = os.path.realpath(home)
        return os.path.commonpath((os.path.realpath(path), resolved_home)) == resolved_home
    except ValueError:
        return False


def _validate_update_identity(home: str) -> pwd.struct_passwd:
    """Require updates to run as the owner of the user-scoped installation."""

    try:
        owner = pwd.getpwuid(os.stat(home).st_uid)
    except (OSError, KeyError) as exc:
        raise RuntimeError(f"Could not determine the owner of agent home: {home}") from exc

    if os.geteuid() != owner.pw_uid:
        try:
            current_user = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            current_user = str(os.geteuid())
        raise RuntimeError(
            f"Agent updates for {home} must run as {owner.pw_name}; "
            f"current user is {current_user}. Use 'sudo -u {owner.pw_name} -H'."
        )
    return owner


def _agent_update_environment(home: str, owner: pwd.struct_passwd) -> dict[str, str]:
    """Build an environment rooted in the account being updated.

    ``sudo -u`` normally preserves the caller's working directory and some
    environment variables. Vendor installers may use those values while
    changing directories, causing an unprivileged account to touch the
    caller's home. Keep useful inherited values such as proxy and agent
    sockets, but remove path/home redirects and replace them with the target
    account's locations.
    """

    environment = os.environ.copy()
    for key in list(environment):
        normalized = key.lower()
        if normalized.startswith("sudo_"):
            environment.pop(key, None)
        elif normalized in _UPDATE_ENVIRONMENT_REDIRECTS:
            environment.pop(key, None)
        elif normalized in {
            "npm_config_userconfig",
            "npm_config_prefix",
            "npm_config_cache",
        }:
            environment.pop(key, None)

    environment.update(
        {
            "HOME": home,
            "LOGNAME": owner.pw_name,
            "PATH": os.pathsep.join(
                (
                    os.path.join(home, ".local", "bin"),
                    os.path.join(home, ".opencode", "bin"),
                    _UPDATE_SYSTEM_PATH,
                )
            ),
            "PWD": home,
            "TMPDIR": "/tmp",
            "USER": owner.pw_name,
            "XDG_CACHE_HOME": os.path.join(home, ".cache"),
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
            "XDG_STATE_HOME": os.path.join(home, ".local", "state"),
            "CODEX_HOME": os.path.join(home, ".codex"),
            "NVM_DIR": os.path.join(home, ".nvm"),
        }
    )
    return environment


def _backup_executable(tool: str, path: str, home: str) -> str:
    backup_dir = os.path.join(
        home,
        ".local",
        "state",
        "infra_tools",
        "agent-backups",
    )
    validate_filesystem_path(backup_dir, must_exist=False)
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    backup_path = os.path.join(backup_dir, f"{tool}.previous")
    descriptor, temporary_path = tempfile.mkstemp(
        dir=backup_dir,
        prefix=f".{tool}-",
    )
    os.close(descriptor)
    try:
        shutil.copy2(os.path.realpath(path), temporary_path)
        with open(temporary_path, "rb") as file_obj:
            os.fsync(file_obj.fileno())
        os.replace(temporary_path, backup_path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    return backup_path


def _restore_executable(path: str, backup_path: str) -> bool:
    parent = os.path.dirname(path)
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=parent,
            prefix=".infra-tools-rollback-",
        )
        os.close(descriptor)
        os.unlink(temporary_path)
        try:
            os.symlink(backup_path, temporary_path)
            os.replace(temporary_path, path)
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
    except OSError:
        return False
    return True


def _download_codex_installer(directory: str) -> tuple[str, str]:
    path, _state_path, record = download_installer("codex", directory)
    return path, record["observed_sha256"]


def _invoke_agent_update(tool: str, path: str, home: str) -> JSONDict:
    owner = _validate_update_identity(home)
    environment = _agent_update_environment(home, owner)
    installer_path: Optional[str] = None
    installer_sha256: Optional[str] = None
    if tool == "codex":
        installer_path, installer_sha256 = _download_codex_installer(
            os.path.dirname(_state_path(home)),
        )
        command = ["/bin/sh", installer_path]
        method = _CODEX_INSTALLER_URL
    elif tool == "claude":
        command = [path, "update"]
        method = "claude update"
    else:
        command = [path, "upgrade"]
        method = "opencode upgrade"

    try:
        result = run_command(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_UPDATE_TIMEOUT_SECONDS,
            env=environment,
            cwd=home,
        )
        return {
            "returncode": result.returncode,
            "method": method,
            "installer_sha256": installer_sha256,
        }
    except CommandTimeoutError:
        return {
            "returncode": None,
            "method": method,
            "installer_sha256": installer_sha256,
            "failure": "timeout",
        }
    except OSError:
        return {
            "returncode": None,
            "method": method,
            "installer_sha256": installer_sha256,
            "failure": "execution_error",
        }
    finally:
        if installer_path:
            try:
                os.unlink(installer_path)
            except FileNotFoundError:
                pass


def _update_method(tool: str) -> str:
    if tool == "codex":
        return _CODEX_INSTALLER_URL
    return f"{tool} {'update' if tool == 'claude' else 'upgrade'}"


def update_agent_tools(
    tools: StrList,
    *,
    home: Optional[str] = None,
    dry_run: bool = False,
) -> list[JSONDict]:
    """Update user-installed agents and persist non-secret verification records."""
    _validate_update_tools(tools)
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    validate_filesystem_path(user_home, must_exist=True)
    _validate_update_identity(user_home)
    state_path = _state_path(user_home)
    state = _load_update_state(state_path) if not dry_run else None
    results: list[JSONDict] = []

    for tool in dict.fromkeys(tools):
        path = _tool_path(tool, user_home)
        before_version = _tool_version(tool, path) if path else None
        record: JSONDict = {
            "tool": tool,
            "path": path,
            "method": _update_method(tool),
            "channel_policy": POLICIES[tool][2],
            "before_version": before_version,
            "after_version": before_version,
            "status": "planned" if dry_run else "failed",
            "rollback": False,
        }
        if not path:
            record["failure"] = "not_installed"
            record["status"] = "failed"
            results.append(record)
            continue
        if not _within_home(path, user_home):
            record["failure"] = "not_user_managed"
            record["status"] = "failed"
            results.append(record)
            continue
        if not before_version or not _tool_smoke_test(path):
            record["failure"] = "pre_update_verification"
            record["status"] = "failed"
            results.append(record)
            continue
        if dry_run:
            results.append(record)
            continue

        assert state is not None
        attempted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record["attempted_at"] = attempted_at
        try:
            backup_path = _backup_executable(tool, path, user_home)
        except OSError:
            record["failure"] = "backup_error"
            state["tools"][tool] = record
            _save_update_state(state_path, state)
            results.append(record)
            continue
        record["backup_path"] = backup_path
        state["tools"][tool] = {**record, "status": "in_progress"}
        _save_update_state(state_path, state)

        try:
            invocation = _invoke_agent_update(tool, path, user_home)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            invocation = {
                "returncode": None,
                "method": _update_method(tool),
                "installer_sha256": None,
                "failure": "download_error",
            }
        record.update(invocation)
        current_path = _tool_path(tool, user_home)
        after_version = _tool_version(tool, current_path) if current_path else None
        smoke_ok = bool(current_path and _tool_smoke_test(current_path))
        record["path"] = current_path
        record["after_version"] = after_version

        if invocation.get("returncode") == 0 and after_version and smoke_ok:
            record["status"] = "updated" if after_version != before_version else "current"
        else:
            record["status"] = "failed"
            if "failure" not in record:
                record["failure"] = (
                    "updater_exit" if invocation.get("returncode") != 0 else "post_update_verification"
                )
            if after_version != before_version or not smoke_ok:
                restored = _restore_executable(path, backup_path)
                restored_version = _tool_version(tool, path) if restored else None
                restored_smoke = restored and _tool_smoke_test(path)
                record["rollback"] = bool(
                    restored
                    and restored_version == before_version
                    and restored_smoke
                )
                if restored:
                    record["path"] = path
                    record["after_version"] = restored_version
                if restored and not record["rollback"]:
                    record["rollback_failure"] = "verification"

        state["tools"][tool] = record
        _save_update_state(state_path, state)
        results.append(record)

    return results


def inspect_agent_tools(tools: StrList, home: Optional[str] = None) -> list[JSONDict]:
    """Return non-secret installation and credential status for selected tools."""
    user_home = home or os.path.expanduser("~")
    results: list[JSONDict] = []
    for tool in tools:
        path = _tool_path(tool, user_home)
        credential_relative = _CREDENTIAL_PATHS.get(tool)
        credential_path = (
            os.path.join(user_home, credential_relative)
            if credential_relative
            else None
        )
        credential_present = bool(credential_path and os.path.exists(credential_path))
        result: JSONDict = {
            "tool": tool,
            "installed": path is not None,
            "path": path,
            "version": _tool_version(tool, path) if path else None,
            "credential": credential_present if credential_relative else None,
        }
        if tool == "codex" and credential_present and credential_path:
            credential_status = inspect_codex_auth_file(credential_path)
            result["credential_status"] = credential_status
            result["credential_healthy"] = codex_auth_is_healthy(
                credential_status
            )
        results.append(result)
    return results


def _codex_browser_registration(home: str) -> bool:
    config_path = os.path.join(home, ".codex", "config.toml")
    try:
        with open(config_path, encoding="utf-8") as file_obj:
            content = file_obj.read()
    except OSError:
        return False
    escaped_name = re.escape(_BROWSER_MCP_SERVER_NAME)
    section = re.search(
        rf'^\[mcp_servers\.(?:{escaped_name}|"{escaped_name}")\]\s*$',
        content,
        re.MULTILINE,
    )
    if section is None:
        return False

    following_section = re.search(r'^\[', content[section.end():], re.MULTILINE)
    section_content = content[section.end():]
    if following_section is not None:
        section_content = section_content[:following_section.start()]
    command = re.search(
        r'^\s*command\s*=\s*(?:"([^"]*)"|\'([^\']*)\')\s*(?:#.*)?$',
        section_content,
        re.MULTILINE,
    )
    return bool(command and (command.group(1) or command.group(2)) == _BROWSER_MCP_WRAPPER)


def _opencode_browser_registration(home: str) -> bool:
    from common.browser_automation_steps import _load_opencode_config, _opencode_config_path

    config_dir = os.path.join(home, ".config", "opencode")
    config_path = _opencode_config_path(config_dir)
    try:
        value = _load_opencode_config(config_path)
    except (OSError, ValueError):
        return False
    if not isinstance(value.get("mcp"), dict):
        return False
    registration = value["mcp"].get(_BROWSER_MCP_SERVER_NAME)
    return bool(
        isinstance(registration, dict)
        and registration.get("type") == "local"
        and registration.get("enabled") is True
        and registration.get("command") == [_BROWSER_MCP_WRAPPER]
        and registration.get("timeout") == 30000
    )


def _browser_launcher_features(path: Optional[str] = None) -> JSONDict:
    """Report whether the launcher has the current safe browser defaults."""
    launcher_path = path or _BROWSER_MCP_WRAPPER
    try:
        with open(launcher_path, encoding="utf-8") as file_obj:
            content = file_obj.read()
    except OSError:
        content = ""
    return {
        "browser_selection": (
            'browser_path="$(' in content
            and "chromium.executablePath()" in content
            and '--executable-path "$browser_path"' in content
        ),
        "private_evidence": (
            'output_dir="$HOME/.local/state/infra_tools/playwright-mcp"' in content
            and '--output-dir "$output_dir"' in content
            and "umask 077" in content
        ),
        "bounded_evidence": (
            f"--output-max-size {_BROWSER_OUTPUT_MAX_BYTES}" in content
        ),
        "coordinate_input": "--caps vision" in content,
        "webgl_settle_delay": "--timeout-settle 1000" in content,
    }


def _browser_launchers_secure(paths: Optional[tuple[str, ...]] = None) -> bool:
    """Return whether managed browser launchers are root-owned safe files."""
    launcher_paths = paths or (_BROWSER_MCP_WRAPPER, _BROWSER_DOCTOR_WRAPPER)
    for path in launcher_paths:
        try:
            metadata = os.lstat(path)
        except OSError:
            return False
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            return False
    return True


def _browser_argument_value(arguments: list[str], option: str) -> Optional[str]:
    """Return one command-line option value without exposing the full process."""
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(f"{option}="):
            return argument.split("=", 1)[1]
    return None


def _browser_process_has_managed_defaults(
    arguments: list[str],
    home: str,
) -> bool:
    """Return whether one active MCP process has the current safe arguments."""
    capabilities = (_browser_argument_value(arguments, "--caps") or "").split(",")
    executable_path = _browser_argument_value(arguments, "--executable-path")
    browser_cache = os.path.realpath(
        os.path.join(home, ".cache", "ms-playwright")
    )
    try:
        managed_executable = bool(
            executable_path
            and os.path.isabs(executable_path)
            and os.path.commonpath((os.path.realpath(executable_path), browser_cache))
            == browser_cache
            and os.path.isfile(executable_path)
            and os.access(executable_path, os.X_OK)
        )
    except ValueError:
        managed_executable = False
    return bool(
        "--headless" in arguments
        and "--isolated" in arguments
        and managed_executable
        and "vision" in capabilities
        and _browser_argument_value(arguments, "--output-dir")
        == os.path.join(home, ".local", "state", "infra_tools", "playwright-mcp")
        and _browser_argument_value(arguments, "--timeout-settle") == "1000"
        and _browser_argument_value(arguments, "--output-max-size")
        == str(_BROWSER_OUTPUT_MAX_BYTES)
        and "--ignore-https-errors" not in arguments
        and "--no-sandbox" not in arguments
    )


def _browser_running_processes(
    home: str,
    proc_root: str = "/proc",
) -> JSONDict:
    """Count active managed MCP servers that predate the current launcher."""
    try:
        owner_uid = os.stat(home).st_uid
        entries = list(os.scandir(proc_root))
    except OSError:
        return {"total": 0, "stale": 0, "inspected": False}

    total = 0
    stale = 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat(follow_symlinks=False).st_uid != owner_uid:
                continue
            with open(os.path.join(entry.path, "cmdline"), "rb") as file_obj:
                payload = file_obj.read(16 * 1024)
        except OSError:
            continue
        arguments = [
            os.fsdecode(value)
            for value in payload.split(b"\0")
            if value
        ]
        if _BROWSER_MCP_CLI not in arguments or "--isolated" not in arguments:
            continue
        total += 1
        if not _browser_process_has_managed_defaults(arguments, home):
            stale += 1
    return {"total": total, "stale": stale, "inspected": True}


def _managed_agent_skill_ready(path: str, owner_uid: int) -> bool:
    """Return whether one safe skill entrypoint has the infra-tools marker."""

    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        return False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size > _MAX_AGENT_SKILL_BYTES
        ):
            return False
        with os.fdopen(descriptor, "rb") as file_obj:
            descriptor = -1
            content = file_obj.read(_MAX_AGENT_SKILL_BYTES + 1)
        return (
            len(content) <= _MAX_AGENT_SKILL_BYTES
            and b"managed-by: infra_tools" in content
        )
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _browser_workflow_skills(
    home: str,
    owner_uid: int | None = None,
) -> tuple[str, ...]:
    """Return installed infra-tools-managed browser workflow variants."""

    from common.agent_steps import BROWSER_AGENT_SKILL_NAMES

    if owner_uid is None:
        try:
            owner_uid = os.stat(home).st_uid
        except OSError:
            return ()
    return tuple(
        skill_name
        for skill_name in BROWSER_AGENT_SKILL_NAMES
        if _managed_agent_skill_ready(
            os.path.join(home, ".agents", "skills", skill_name, "SKILL.md"),
            owner_uid,
        )
    )


def inspect_browser_automation(
    home: Optional[str] = None,
    *,
    run_smoke: bool = True,
) -> JSONDict:
    """Verify local launchers, selected-agent registration, and browser startup."""
    user_home = home or os.path.expanduser("~")
    launchers_installed = all(
        os.path.isfile(path) and os.access(path, os.X_OK)
        for path in (_BROWSER_MCP_WRAPPER, _BROWSER_DOCTOR_WRAPPER)
    )
    launchers_secure = _browser_launchers_secure()
    launcher_features = _browser_launcher_features()
    managed_defaults = all(bool(value) for value in launcher_features.values())
    running_processes = _browser_running_processes(user_home)
    processes_current = bool(
        running_processes.get("inspected") is not True
        or int(running_processes.get("stale", 0)) == 0
    )
    workflow_skills = _browser_workflow_skills(user_home)
    workflow_skill_ready = bool(
        len(workflow_skills) == 1
        and workflow_skills[0] in _PLAYWRIGHT_BROWSER_AGENT_SKILL_NAMES
    )

    registrations: JSONDict = {}
    if _tool_path("codex", user_home):
        registrations["codex"] = _codex_browser_registration(user_home)
    if _tool_path("opencode", user_home):
        registrations["opencode"] = _opencode_browser_registration(user_home)

    smoke_test: bool | None = None
    if launchers_installed and run_smoke:
        environment = os.environ.copy()
        environment["HOME"] = user_home
        try:
            result = subprocess.run(
                [_BROWSER_DOCTOR_WRAPPER],
                check=False,
                capture_output=True,
                text=True,
                timeout=_BROWSER_DOCTOR_TIMEOUT_SECONDS,
                env=environment,
                cwd=user_home,
            )
            smoke_test = result.returncode == 0 and result.stdout.strip() == "browser-ready"
        except (OSError, subprocess.TimeoutExpired):
            smoke_test = False

    registrations_ready = bool(
        registrations and all(bool(value) for value in registrations.values())
    )
    configured = bool(
        launchers_installed
        and launchers_secure
        and managed_defaults
        and registrations_ready
        and workflow_skill_ready
    )
    healthy = configured and processes_current and smoke_test if run_smoke else None
    issues: list[str] = []
    if not launchers_installed:
        issues.append("launchers_missing")
    else:
        if not launchers_secure:
            issues.append("launchers_unsafe")
        if launcher_features.get("browser_selection") is not True:
            issues.append("mcp_browser_selection_missing")
        if not all(
            bool(value)
            for feature, value in launcher_features.items()
            if feature != "browser_selection"
        ):
            issues.append("managed_defaults_stale")
    if not registrations_ready:
        issues.append("registration_missing")
    if not workflow_skill_ready:
        issues.append("workflow_skill_missing_or_stale")
    if run_smoke and launchers_installed and smoke_test is not True:
        issues.append("smoke_test_failed")
    if not processes_current:
        issues.append("stale_processes")

    remediation: str | None = None
    if "launchers_unsafe" in issues:
        remediation = "inspect_launcher_security_then_rerun_saved_setup"
    elif "launchers_missing" in issues or "registration_missing" in issues:
        remediation = "rerun_setup_with_browser_automation"
    elif (
        "mcp_browser_selection_missing" in issues
        or "managed_defaults_stale" in issues
        or "workflow_skill_missing_or_stale" in issues
    ):
        remediation = "rerun_saved_setup"
    elif "stale_processes" in issues:
        remediation = "restart_agent_sessions"
    elif "smoke_test_failed" in issues:
        remediation = "inspect_browser_runtime"
    return {
        "capability": "browser",
        "installed": launchers_installed,
        "path": _BROWSER_MCP_WRAPPER if launchers_installed else None,
        "launchers_secure": launchers_secure,
        "launcher_features": launcher_features,
        "managed_defaults": managed_defaults,
        "running_processes": running_processes,
        "registrations": registrations,
        "workflow_skills": list(workflow_skills),
        "workflow_skill_ready": workflow_skill_ready,
        "configured": configured,
        "smoke_test": smoke_test,
        "healthy": healthy,
        "issues": issues,
        "remediation": remediation,
    }


def _read_meminfo(path: str = "/proc/meminfo") -> dict[str, int]:
    """Read byte values from Linux meminfo without relying on locale output."""
    values: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as file_obj:
            for line in file_obj:
                key, separator, raw_value = line.partition(":")
                if not separator:
                    continue
                fields = raw_value.split()
                if not fields:
                    continue
                try:
                    value = int(fields[0])
                except ValueError:
                    continue
                if len(fields) > 1 and fields[1].lower() == "kb":
                    value *= 1024
                values[key] = value
    except OSError:
        return {}
    return values


def _read_memory_capacity_bytes(
    path: str = "/sys/devices/system/memory",
) -> int:
    """Return guest-visible memory capacity, including ballooned-out pages.

    Linux ``MemTotal`` can reflect only the memory currently available to a
    dynamically sized guest.  The sysfs memory-block inventory continues to
    describe the guest's configured capacity, which is the value relevant to
    sizing recommendations.
    """

    block_size_path = os.path.join(path, "block_size_bytes")
    try:
        with open(block_size_path, encoding="utf-8") as file_obj:
            block_size = int(file_obj.read().strip(), 16)
        if block_size <= 0:
            return 0
        block_count = sum(
            1
            for entry in os.scandir(path)
            if re.fullmatch(r"memory[0-9]+", entry.name)
            and entry.is_dir(follow_symlinks=False)
        )
    except (OSError, ValueError):
        return 0
    return block_size * block_count


def _directory_size_bytes(path: str) -> int:
    """Return regular-file bytes below one user path without following links."""
    if os.path.islink(path) or not os.path.isdir(path):
        return 0
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(root, name))
        ]
        for filename in files:
            candidate = os.path.join(root, filename)
            try:
                if os.path.islink(candidate):
                    continue
                total += os.stat(candidate, follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _systemd_properties(
    unit: str,
    properties: tuple[str, ...],
    *,
    user: bool = False,
) -> dict[str, str]:
    command = ["systemctl"]
    if user:
        command.append("--user")
    command.extend(["show", unit])
    command.extend(f"--property={property_name}" for property_name in properties)
    result = _run_check(command)
    if result.returncode != 0:
        return {}
    observed: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            observed[key] = value
    return observed


def _optional_systemd_integer(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "", "[not set]") else None
    except ValueError:
        return None


def _maintenance_status() -> JSONDict:
    units: JSONDict = {}
    warnings: list[str] = []
    errors: list[str] = []
    service_names = (
        *_AGENT_HOST_MAINTENANCE_UNITS,
        *_AGENT_HOST_OPTIONAL_MAINTENANCE_UNITS,
    )
    for service_name in service_names:
        timer_name = f"{service_name}.timer"
        timer = _systemd_properties(
            timer_name,
            ("LoadState", "ActiveState", "UnitFileState", "NextElapseUSecRealtime"),
        )
        service = _systemd_properties(
            f"{service_name}.service",
            ("LoadState", "ActiveState", "Result", "ExecMainStatus"),
        )
        if (
            service_name in _AGENT_HOST_OPTIONAL_MAINTENANCE_UNITS
            and timer.get("LoadState") != "loaded"
            and service.get("LoadState") != "loaded"
        ):
            continue
        loaded = timer.get("LoadState") == "loaded"
        enabled = timer.get("UnitFileState") in {"enabled", "enabled-runtime"}
        active = timer.get("ActiveState") == "active"
        result = service.get("Result")
        units[service_name] = {
            "loaded": loaded,
            "enabled": enabled,
            "active": active,
            "last_result": result or None,
            "last_exit_status": _optional_systemd_integer(
                service.get("ExecMainStatus")
            ),
            "next_run": timer.get("NextElapseUSecRealtime") or None,
        }
        if result == "failed" or service.get("ActiveState") == "failed":
            errors.append(f"{service_name} maintenance last failed")
        elif not loaded or not enabled or not active:
            warnings.append(f"{timer_name} is not enabled and active")
    return {
        "units": units,
        "warnings": warnings,
        "errors": errors,
    }


def _agent_storage_inventory(home: str) -> JSONDict:
    paths = {
        "npm_cache": os.path.join(home, ".npm"),
        "browser_cache": os.path.join(home, ".cache", "ms-playwright"),
        "t3_logs": os.path.join(home, ".t3", "userdata", "logs"),
        "codex_packages": os.path.join(home, ".codex", "packages"),
        "workspace": os.path.join(home, "repos"),
    }
    sizes = {name: _directory_size_bytes(path) for name, path in paths.items()}
    releases = os.path.join(home, ".codex", "packages", "standalone", "releases")
    release_count = 0
    if not os.path.islink(releases) and os.path.isdir(releases):
        try:
            release_count = sum(
                1
                for entry in os.scandir(releases)
                if entry.is_dir(follow_symlinks=False)
            )
        except OSError:
            release_count = 0
    return {
        "paths": paths,
        "size_bytes": sizes,
        "codex_release_count": release_count,
    }


def inspect_host_readiness(
    home: Optional[str] = None,
    *,
    meminfo_path: str = "/proc/meminfo",
    memory_sysfs_path: str = "/sys/devices/system/memory",
) -> JSONDict:
    """Report resource headroom and recurring-maintenance health for an agent VM."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    warnings: list[str] = []
    errors: list[str] = []
    meminfo = _read_meminfo(meminfo_path)
    memory_total = meminfo.get("MemTotal", 0)
    memory_capacity = max(
        memory_total,
        _read_memory_capacity_bytes(memory_sysfs_path),
    )
    memory_ballooned = bool(
        memory_total
        and memory_capacity
        and memory_total * 10 < memory_capacity * 9
    )
    memory_available = meminfo.get("MemAvailable", 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    if (
        memory_capacity
        and memory_capacity < _AGENT_HOST_MIN_RECOMMENDED_MEMORY
    ):
        warnings.append(
            "memory is below the 4 GiB recommendation for T3 Code with browser or build workloads"
        )
    if memory_available and memory_available < _AGENT_HOST_LOW_AVAILABLE_MEMORY:
        warnings.append("available memory is below 512 MiB")
    if swap_total and swap_used * 4 >= swap_total:
        warnings.append("at least 25% of swap is in use")

    try:
        disk = shutil.disk_usage(user_home)
        disk_usage_percent = int((disk.used * 100) / disk.total) if disk.total else 0
        disk_details: JSONDict = {
            "path": user_home,
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "usage_percent": disk_usage_percent,
        }
        if (
            disk_usage_percent >= _AGENT_HOST_DISK_CRITICAL_PERCENT
            or disk.free < _AGENT_HOST_DISK_CRITICAL_FREE
        ):
            errors.append("agent filesystem has critical free-space pressure")
        elif (
            disk_usage_percent >= _AGENT_HOST_DISK_WARNING_PERCENT
            or disk.free < _AGENT_HOST_DISK_WARNING_FREE
        ):
            warnings.append("agent filesystem has low free-space headroom")
    except OSError:
        disk_details = {"path": user_home, "available": False}
        warnings.append("agent filesystem usage could not be inspected")

    storage = _agent_storage_inventory(user_home)
    for name, threshold in _AGENT_STORAGE_WARNING_BYTES.items():
        size = int(storage["size_bytes"].get(name, 0))
        if size > threshold:
            warnings.append(f"{name} exceeds its diagnostic size threshold")
    if int(storage["codex_release_count"]) > 2:
        warnings.append("more than two Codex standalone releases are retained")

    t3_properties = _systemd_properties(
        _T3_SERVICE_NAME,
        ("MemoryCurrent", "MemoryPeak", "TasksCurrent"),
        user=True,
    )
    t3_resources = {
        "memory_current_bytes": _optional_systemd_integer(
            t3_properties.get("MemoryCurrent")
        ),
        "memory_peak_bytes": _optional_systemd_integer(t3_properties.get("MemoryPeak")),
        "tasks_current": _optional_systemd_integer(t3_properties.get("TasksCurrent")),
    }
    if (
        memory_total
        and t3_resources["memory_current_bytes"] is not None
        and int(t3_resources["memory_current_bytes"]) * 5 >= memory_total * 4
    ):
        warnings.append("T3 Code currently accounts for at least 80% of guest memory")
    if (
        memory_total
        and t3_resources["memory_peak_bytes"] is not None
        and int(t3_resources["memory_peak_bytes"]) > memory_total
    ):
        warnings.append("T3 Code's recorded memory peak exceeded guest memory")

    maintenance = _maintenance_status()
    warnings.extend(str(item) for item in maintenance["warnings"])
    errors.extend(str(item) for item in maintenance["errors"])
    maintenance_hold = inspect_agent_maintenance(user_home)
    if maintenance_hold["status"] == "active":
        warnings.append(
            "automatic restarts are held until "
            f"{maintenance_hold['expires_at']}"
        )
    elif maintenance_hold["status"] == "invalid":
        warnings.append(
            "agent maintenance hold state is invalid; release and recreate it"
        )
    reboot_pending = os.path.exists("/var/run/reboot-required")
    if reboot_pending:
        warnings.append("a host reboot is pending")

    return {
        "capability": "host",
        "healthy": not errors,
        "status": "error" if errors else "warning" if warnings else "ok",
        "memory": {
            "total_bytes": memory_total or None,
            "capacity_bytes": memory_capacity or None,
            "ballooned": memory_ballooned,
            "available_bytes": memory_available or None,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
        },
        "disk": disk_details,
        "agent_storage": storage,
        "t3_service": t3_resources,
        "maintenance": maintenance["units"],
        "maintenance_hold": maintenance_hold,
        "reboot_pending": reboot_pending,
        "warnings": warnings,
        "errors": errors,
    }


def _t3_environment(home: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": home,
            "GH_CONFIG_DIR": os.path.join(home, ".config", "gh"),
            "PATH": os.pathsep.join(
                (
                    os.path.join(home, ".opencode", "bin"),
                    os.path.join(home, ".local", "bin"),
                    _UPDATE_SYSTEM_PATH,
                )
            ),
        }
    )
    return environment


def _run_check(
    command: list[str],
    *,
    environment: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 1, "", "")


def _command_version(path: str, *arguments: str) -> Optional[str]:
    result = _run_check([path, *(arguments or ("--version",))])
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if result.returncode == 0 and output else None


def _system_development_tool(name: str) -> Optional[str]:
    search_path = os.pathsep.join(("/usr/local/go/bin", _UPDATE_SYSTEM_PATH))
    return shutil.which(name, path=search_path)


def _nvm_node_bin(home: str) -> Optional[str]:
    """Resolve the active/default nvm Node bin without evaluating shell files."""
    versions_root = os.path.join(home, ".nvm", "versions", "node")
    active = shutil.which("node")
    if active:
        try:
            if os.path.commonpath((os.path.realpath(active), versions_root)) == versions_root:
                return os.path.dirname(active)
        except ValueError:
            pass

    alias_path = os.path.join(home, ".nvm", "alias", "default")
    try:
        with open(alias_path, encoding="utf-8") as file_obj:
            alias = file_obj.read(128).strip()
        if alias == "lts/*":
            with open(
                os.path.join(home, ".nvm", "alias", "lts", "*"),
                encoding="utf-8",
            ) as file_obj:
                alias = file_obj.read(128).strip()
    except OSError:
        alias = ""
    if re.fullmatch(r"v\d+\.\d+\.\d+", alias):
        candidate = os.path.join(versions_root, alias, "bin")
        if os.path.isfile(os.path.join(candidate, "node")):
            return candidate

    versions: list[tuple[tuple[int, int, int], str]] = []
    try:
        entries = os.scandir(versions_root)
    except OSError:
        return None
    with entries:
        for entry in entries:
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", entry.name)
            if match and entry.is_dir(follow_symlinks=False):
                versions.append(
                    (tuple(int(part) for part in match.groups()), entry.path)
                )
    if not versions:
        return None
    _version, version_root = max(versions)
    candidate = os.path.join(version_root, "bin")
    return candidate if os.path.isfile(os.path.join(candidate, "node")) else None


def _inspect_node_development(home: str) -> JSONDict:
    nvm_script = os.path.join(home, ".nvm", "nvm.sh")
    installed = os.path.isfile(nvm_script)
    if not installed:
        return {"installed": False, "healthy": True, "issues": []}

    issues: list[str] = []
    node_bin = _nvm_node_bin(home)
    node_path = os.path.join(node_bin, "node") if node_bin else ""
    npm_path = os.path.join(node_bin, "npm") if node_bin else ""
    pnpm_path = os.path.join(node_bin, "pnpm") if node_bin else ""
    corepack_path = os.path.join(node_bin, "corepack") if node_bin else ""
    node_version = _command_version(node_path) if node_path else None
    npm_version = _command_version(npm_path) if os.path.isfile(npm_path) else None
    pnpm_version = _command_version(pnpm_path) if os.path.isfile(pnpm_path) else None
    corepack_version = (
        _command_version(corepack_path) if os.path.isfile(corepack_path) else None
    )
    if node_version is None:
        issues.append("node_default_missing")
    if npm_version is None:
        issues.append("node_npm_missing")
    if pnpm_version is None:
        issues.append("node_pnpm_missing")
    return {
        "installed": True,
        "healthy": not issues,
        "version": node_version,
        "npm": npm_version,
        "pnpm": pnpm_version,
        "corepack": corepack_version,
        "issues": issues,
    }


def _inspect_go_development() -> JSONDict:
    go_path = _system_development_tool("go")
    if go_path is None:
        return {"installed": False, "healthy": True, "issues": []}

    issues: list[str] = []
    version = _command_version(go_path, "version")
    gofmt_path = os.path.join(os.path.dirname(go_path), "gofmt")
    gofmt = os.path.isfile(gofmt_path) and os.access(gofmt_path, os.X_OK)
    cgo_result = _run_check([go_path, "env", "CGO_ENABLED"])
    cgo_enabled = (
        cgo_result.stdout.strip()
        if cgo_result.returncode == 0 and cgo_result.stdout.strip() in {"0", "1"}
        else None
    )
    c_compiler = _system_development_tool("gcc") is not None
    if version is None:
        issues.append("go_unusable")
    if not gofmt:
        issues.append("gofmt_missing")
    if cgo_enabled == "1" and not c_compiler:
        issues.append("go_c_compiler_missing")
    return {
        "installed": True,
        "healthy": not issues,
        "version": version,
        "gofmt": gofmt,
        "cgo_enabled": cgo_enabled,
        "c_compiler": c_compiler,
        "issues": issues,
    }


def _inspect_godot_development(home: str) -> JSONDict:
    godot_path = _system_development_tool("godot")
    if godot_path is None:
        return {"installed": False, "healthy": True, "issues": []}

    issues: list[str] = []
    version = _command_version(godot_path)
    if version is None:
        issues.append("godot_unusable")
    template_version = version.split(".official", 1)[0] if version else ""
    template_dir = os.path.join(
        home,
        ".local",
        "share",
        "godot",
        "export_templates",
        template_version,
    )
    export_templates = bool(
        template_version
        and os.path.isfile(os.path.join(template_dir, "version.txt"))
    )
    web_templates = bool(
        export_templates
        and os.path.isfile(os.path.join(template_dir, "web_debug.zip"))
        and os.path.isfile(os.path.join(template_dir, "web_release.zip"))
    )
    return {
        "installed": True,
        "healthy": not issues,
        "version": version,
        "export_templates": export_templates,
        "web_templates": web_templates,
        "issues": issues,
    }


def inspect_development_readiness(home: Optional[str] = None) -> JSONDict:
    """Inventory managed Godot, Go, and Node development toolchains."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    toolchains = {
        "godot": _inspect_godot_development(user_home),
        "go": _inspect_go_development(),
        "node": _inspect_node_development(user_home),
    }
    installed = any(bool(result["installed"]) for result in toolchains.values())
    issues = [
        str(issue)
        for result in toolchains.values()
        for issue in result.get("issues", [])
    ]
    return {
        "capability": "development",
        "installed": installed,
        "healthy": not issues,
        "toolchains": toolchains,
        "issues": issues,
    }


def _t3_port(drop_in: str) -> int | None:
    try:
        with open(drop_in, encoding="utf-8") as file_obj:
            match = re.search(r"T3CODE_PORT=(\d+)", file_obj.read())
    except OSError:
        match = None
    if match is None:
        return _T3_DEFAULT_PORT
    try:
        port = int(match.group(1))
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def _t3_active_binary(home: str) -> str | None:
    runtime = os.path.join(home, _T3_RUNTIME_RELATIVE)
    state_file = os.path.join(runtime, "service-state.json")
    if os.path.islink(state_file) or not os.path.isfile(state_file):
        return None
    try:
        with open(state_file, encoding="utf-8") as file_obj:
            state = json.load(file_obj)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("protocol") != 2:
        return None
    version = state.get("activeVersion")
    if not isinstance(version, str) or _T3_VERSION_RE.fullmatch(version) is None:
        return None
    binary = os.path.join(
        runtime,
        "versions",
        version,
        "node_modules",
        "t3",
        "dist",
        "bin.mjs",
    )
    return binary if os.path.isfile(binary) and os.access(binary, os.X_OK) else None


def _t3_version_root(binary: str) -> str | None:
    version_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(binary)))
    )
    expected = os.path.join(
        version_root,
        "node_modules",
        "t3",
        "dist",
        "bin.mjs",
    )
    if os.path.normpath(binary) != os.path.normpath(expected):
        return None
    return version_root if os.path.isdir(version_root) else None


def _t3_node_binary(drop_in: str) -> str | None:
    try:
        with open(drop_in, encoding="utf-8") as file_obj:
            match = re.search(r"^Environment=PATH=(.+)$", file_obj.read(), re.MULTILINE)
    except OSError:
        return None
    if match is None:
        return None
    for directory in match.group(1).split(os.pathsep):
        if not os.path.isabs(directory):
            continue
        node = os.path.join(directory, "node")
        if os.path.isfile(node) and os.access(node, os.X_OK):
            return node
    return None


def _t3_native_runtime_healthy(
    node: str | None,
    binary: str | None,
    environment: dict[str, str],
) -> bool:
    if node is None or binary is None:
        return False
    version_root = _t3_version_root(binary)
    if version_root is None:
        return False
    node_pty = os.path.join(version_root, "node_modules", "node-pty")
    return _run_check(
        [node, "-e", "require(process.argv[1])", node_pty],
        environment=environment,
    ).returncode == 0


def _repair_t3_native_runtime(
    node: str,
    binary: str,
    environment: dict[str, str],
) -> bool:
    version_root = _t3_version_root(binary)
    npm = os.path.join(os.path.dirname(node), "npm")
    if (
        version_root is None
        or not os.path.isfile(npm)
        or not os.access(npm, os.X_OK)
    ):
        return False
    repair_environment = environment.copy()
    for key in list(repair_environment):
        if key.lower() in {
            "npm_config_allow_scripts",
            "npm_config_dangerously_allow_all_scripts",
        }:
            repair_environment.pop(key, None)
    repair_environment.update(
        {
            "CC": "gcc",
            "CXX": "g++",
            "npm_config_foreground_scripts": "true",
            "npm_config_strict_allow_scripts": "false",
        }
    )
    with _temporary_t3_npm_config() as npm_config:
        repair_environment["NPM_CONFIG_USERCONFIG"] = npm_config
        result = _run_check(
            [
                npm,
                "rebuild",
                "--foreground-scripts",
                "--prefix",
                version_root,
                *_T3_NATIVE_PACKAGES,
            ],
            environment=repair_environment,
            cwd=version_root,
            timeout=300,
        )
    return result.returncode == 0 and _t3_native_runtime_healthy(
        node,
        binary,
        environment,
    )


@contextmanager
def _temporary_t3_npm_config() -> Iterator[str]:
    """Yield a private npm 12 allowlist without changing the user's npmrc."""

    descriptor, path = tempfile.mkstemp(prefix=".infra-tools-t3-npmrc-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(f"allow-scripts={','.join(_T3_NATIVE_PACKAGES)}\n")
        os.chmod(path, 0o600)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _t3_endpoint_reachable(port: int | None) -> bool:
    import urllib.error

    if port is None:
        return False
    try:
        with open_loopback(
            f"http://127.0.0.1:{port}/", timeout=5
        ) as response:
            return 200 <= response.status < 300 or 400 <= response.status < 500
    except urllib.error.HTTPError as exc:
        return 400 <= exc.code < 500
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False


def _t3_service_enabled(environment: dict[str, str]) -> bool:
    """Return whether the managed user unit is persistently enabled."""
    result = _run_check(
        ["systemctl", "--user", "is-enabled", _T3_SERVICE_NAME],
        environment=environment,
    )
    return result.returncode == 0 and (result.stdout or "").strip() == "enabled"


def _t3_agent_skills_ready(home: str) -> bool:
    """Verify every workflow skill selected by the managed T3 setup."""
    from common.t3code_steps import (
        T3_AGENT_SKILL_NAMES,
        T3_BROWSER_AGENT_SKILL_NAMES,
    )

    try:
        owner_uid = os.stat(home).st_uid
    except OSError:
        return False
    core_ready = all(
        _managed_agent_skill_ready(
            os.path.join(home, ".agents", "skills", skill_name, "SKILL.md"),
            owner_uid,
        )
        for skill_name in T3_AGENT_SKILL_NAMES
    )
    browser_skills = _browser_workflow_skills(home, owner_uid)
    return bool(
        core_ready
        and len(browser_skills) == 1
        and browser_skills[0] in T3_BROWSER_AGENT_SKILL_NAMES
    )


def inspect_t3code(home: Optional[str] = None, *, fix: bool = False) -> JSONDict:
    """Verify the managed T3 service, Git integration, and user onboarding."""

    user_home = os.path.abspath(home or os.path.expanduser("~"))
    user_bus_environment = os.environ.copy()
    user_bus_environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    user_bus_environment.setdefault(
        "DBUS_SESSION_BUS_ADDRESS",
        f"unix:path=/run/user/{os.getuid()}/bus",
    )
    t3_binary = _t3_active_binary(user_home)
    wrapper = os.path.join(
        user_home,
        ".local",
        "bin",
        "infra-tools-t3code-pairing-provider",
    )
    drop_in = os.path.join(
        user_home,
        ".config",
        "systemd",
        "user",
        "t3code.service.d",
        "infra-tools.conf",
    )
    pair_wrapper = os.path.join(user_home, ".local", "bin", "t3code-pair")
    environment = _t3_environment(user_home)
    node = _t3_node_binary(drop_in)
    native_runtime = _t3_native_runtime_healthy(node, t3_binary, environment)
    gh_path = _tool_path("gh", user_home)
    git_path = _tool_path("git", user_home) or shutil.which("git")
    skill_required = bool(
        _tool_path("codex", user_home) or _tool_path("opencode", user_home)
    )
    fixes: list[str] = []

    service = _run_check(
        ["systemctl", "--user", "is-active", "--quiet", _T3_SERVICE_NAME],
        environment=user_bus_environment,
    )
    service_enabled = _t3_service_enabled(user_bus_environment)
    gh_auth = (
        _run_check(
            [gh_path, "auth", "status", "--hostname", "github.com"],
            environment=environment,
        ).returncode
        == 0
        if gh_path
        else False
    )
    git_name = ""
    git_email = ""
    credential_helper = False
    if git_path:
        git_name_result = _run_check(
            [git_path, "config", "--global", "--get", "user.name"],
            environment=environment,
        )
        git_email_result = _run_check(
            [git_path, "config", "--global", "--get", "user.email"],
            environment=environment,
        )
        helper_result = _run_check(
            [
                git_path,
                "config",
                "--global",
                "--get-regexp",
                r"^credential(\..+)?\.helper$",
            ],
            environment=environment,
        )
        git_name = (git_name_result.stdout or "").strip()
        git_email = (git_email_result.stdout or "").strip()
        credential_helper = helper_result.returncode == 0 and bool(
            (helper_result.stdout or "").strip()
        )

    if fix and gh_path and gh_auth:
        setup_git = _run_check(
            [gh_path, "auth", "setup-git", "--hostname", "github.com"],
            environment=environment,
        )
        if setup_git.returncode == 0:
            fixes.append("configured GitHub HTTPS credential helper")
            credential_helper = True
    if fix and t3_binary and node and not native_runtime:
        if service.returncode == 0:
            _run_check(
                ["systemctl", "--user", "stop", _T3_SERVICE_NAME],
                environment=user_bus_environment,
            )
        native_runtime = _repair_t3_native_runtime(node, t3_binary, environment)
        if native_runtime:
            fixes.append("rebuilt T3 Code native runtime")
        service = subprocess.CompletedProcess([], 1, "", "")
    if fix and t3_binary and not service_enabled:
        enabled = _run_check(
            ["systemctl", "--user", "enable", _T3_SERVICE_NAME],
            environment=user_bus_environment,
        )
        if enabled.returncode == 0:
            fixes.append("enabled T3 Code service at boot")
    restarted_service = False
    if fix and t3_binary and service.returncode != 0:
        restart_command = ["systemctl", "--user", "restart", _T3_SERVICE_NAME]
        restarted = _run_check(
            restart_command,
            environment=user_bus_environment,
        )
        if restarted.returncode == 0:
            fixes.append("restarted inactive T3 Code service")
            restarted_service = True

    port = _t3_port(drop_in)
    endpoint = False
    attempts = 10 if restarted_service else 1
    for attempt in range(attempts):
        service = _run_check(
            ["systemctl", "--user", "is-active", "--quiet", _T3_SERVICE_NAME],
            environment=user_bus_environment,
        )
        endpoint = _t3_endpoint_reachable(port)
        if service.returncode == 0 and endpoint:
            break
        if attempt + 1 < attempts:
            time.sleep(1)
    service_enabled = _t3_service_enabled(user_bus_environment)

    checks = {
        "service_active": service.returncode == 0,
        "service_enabled": service_enabled,
        "runtime": bool(t3_binary),
        "native_runtime": native_runtime,
        "wrapper": os.path.isfile(wrapper) and os.access(wrapper, os.X_OK),
        "pairing_helper": os.path.isfile(pair_wrapper)
        and os.access(pair_wrapper, os.X_OK),
        "endpoint": endpoint,
        "git_identity": bool(git_name and git_email),
        "t3_agent_skill": not skill_required or _t3_agent_skills_ready(user_home),
    }
    if gh_path:
        checks["gh_authenticated"] = gh_auth
        checks["git_credential_helper"] = credential_helper
    return {
        "capability": "t3code",
        "healthy": all(checks.values()),
        "checks": checks,
        "runtime": t3_binary,
        "version": _tool_version("t3", t3_binary)
        if checks["runtime"] and t3_binary
        else None,
        "git_identity": {
            "name": git_name or None,
            "email": git_email or None,
        },
        "service_log": os.path.join(
            user_home,
            ".t3",
            "userdata",
            "logs",
            "boot-service.log",
        ),
        "fixes": fixes,
    }


def run_agent_web_pair(args: argparse.Namespace) -> int:
    """Ask a remote T3 Code service to mint a one-time pairing URL."""

    host = str(args.agent_web_host)
    username = str(args.agent_web_username)
    if not validate_host(host):
        print(f"Error: Invalid IP address or hostname: {host}")
        return 1
    if not validate_username(username):
        print(f"Error: Invalid username: {username}")
        return 1

    command = build_ssh_command(
        host,
        username,
        args.ssh_key,
        batch_mode=ssh_batch_mode(),
        remote_command="exec ~/.local/bin/t3code-pair",
        connect_timeout=30,
        server_alive_interval=30,
    )
    try:
        result = subprocess.run(command, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Error: could not obtain T3 Code pairing URL: {exc}")
        return 1
    if result.returncode != 0:
        print(f"Error: remote T3 Code pairing command failed (exit {result.returncode})")
        return 1
    return 0


def _remote_agent_target(
    args: argparse.Namespace,
    *,
    host_attribute: str,
    username_attribute: str,
) -> tuple[str, str, Optional[str]] | None:
    host_value = getattr(args, host_attribute, None)
    username_value = getattr(args, username_attribute, None)
    ssh_key_value = getattr(args, "ssh_key", None)
    if host_value is None and username_value is None:
        if ssh_key_value:
            raise ValueError("--key requires a remote HOST and USER")
        return None
    if host_value is None or username_value is None:
        raise ValueError("remote agent operations require both HOST and USER")

    host = str(host_value)
    username = str(username_value)
    if not validate_host(host):
        raise ValueError(f"Invalid IP address or hostname: {host}")
    if not validate_username(username):
        raise ValueError(f"Invalid username: {username}")

    ssh_key: Optional[str] = None
    if ssh_key_value:
        ssh_key = os.path.abspath(os.path.expanduser(str(ssh_key_value)))
        validate_filesystem_path(ssh_key, must_exist=True)
        if not os.path.isfile(ssh_key) or not os.access(ssh_key, os.R_OK):
            raise ValueError(f"SSH private key is not a readable file: {ssh_key}")
    return host, username, ssh_key


def _run_remote_agent_lifecycle(
    target: tuple[str, str, Optional[str]],
    subcommand: str,
    remote_arguments: StrList,
    *,
    timeout: int,
) -> int:
    """Run one target-user agent lifecycle command over managed SSH."""
    host, username, ssh_key = target
    remote_command = shell_join(
        [
            "python3",
            _REMOTE_INFRA_TOOLS_PATH,
            "agent",
            subcommand,
            *remote_arguments,
        ]
    )
    command = build_ssh_command(
        host,
        username,
        ssh_key,
        batch_mode=ssh_batch_mode(),
        remote_command=remote_command,
        connect_timeout=30,
        server_alive_interval=30,
    )
    try:
        result = subprocess.run(command, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Error: remote agent {subcommand} failed: {exc}")
        return 1
    return result.returncode


def _t3_readiness_expected(home: str) -> bool:
    """Return whether this home contains a managed T3 installation."""
    return any(
        os.path.exists(path)
        for path in (
            os.path.join(home, _T3_RUNTIME_RELATIVE),
            os.path.join(home, ".config", "systemd", "user", _T3_SERVICE_NAME),
        )
    )


def _record_post_update_readiness(
    tools: StrList,
    *,
    include_capabilities: bool = True,
) -> JSONDict:
    """Run and persist the selected post-update checks for this agent home."""
    from lib.agent_readiness import record_agent_readiness

    home = os.path.abspath(os.path.expanduser("~"))
    tool_results = inspect_agent_tools(tools, home=home)
    capability_results: list[JSONDict] = []
    if include_capabilities:
        capability_results.append(inspect_host_readiness(home))
        if _t3_readiness_expected(home):
            capability_results.append(inspect_t3code(home))
    return record_agent_readiness(
        tool_results,
        capability_results,
        trigger="agent_update",
        home=home,
    )


def _readiness_failure_details(record: JSONDict) -> list[str]:
    """Return safe, actionable details for an unhealthy readiness record."""

    details: list[str] = []
    tools = record.get("tools")
    if isinstance(tools, list):
        for tool_record in tools:
            if not isinstance(tool_record, dict):
                continue
            tool = tool_record.get("tool")
            if not isinstance(tool, str):
                continue
            if tool_record.get("installed") is not True:
                details.append(f"{tool}: executable is not available")
                continue
            if tool_record.get("credential_healthy") is False:
                credential_status = tool_record.get("credential_status")
                status = (
                    credential_status.get("status")
                    if isinstance(credential_status, dict)
                    else None
                )
                details.append(
                    f"{tool}: credentials need attention"
                    + (f" ({status})" if isinstance(status, str) else "")
                )

    capabilities = record.get("capabilities")
    if isinstance(capabilities, list):
        for capability_record in capabilities:
            if not isinstance(capability_record, dict):
                continue
            if capability_record.get("healthy") is True:
                continue
            capability = capability_record.get("capability")
            if not isinstance(capability, str):
                continue
            checks = capability_record.get("checks")
            failed_checks = (
                [
                    check
                    for check, healthy in checks.items()
                    if isinstance(check, str) and healthy is not True
                ]
                if isinstance(checks, dict)
                else []
            )
            if failed_checks:
                details.append(
                    f"{capability}: failed checks: {', '.join(failed_checks)}"
                )
                continue
            issues = capability_record.get("issues")
            safe_issues = (
                [issue for issue in issues if isinstance(issue, str)]
                if isinstance(issues, list)
                else []
            )
            if safe_issues:
                details.append(f"{capability}: issues: {', '.join(safe_issues)}")
                continue
            errors = capability_record.get("errors")
            safe_errors = (
                [error for error in errors if isinstance(error, str)]
                if isinstance(errors, list)
                else []
            )
            details.append(
                f"{capability}: "
                + (
                    "errors: " + "; ".join(safe_errors)
                    if safe_errors
                    else "unhealthy"
                )
            )
    return details


def run_agent_command(args: argparse.Namespace) -> int:
    """Run a local or remote agent-tool command."""
    if args.agent_command == "maintenance":
        try:
            target = _remote_agent_target(
                args,
                host_attribute="agent_maintenance_host",
                username_attribute="agent_maintenance_username",
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        if target is not None:
            remote_arguments = [str(args.agent_maintenance_command or "")]
            if args.agent_maintenance_command == "hold":
                remote_arguments.extend(("--hours", str(args.hours)))
            if args.json:
                remote_arguments.append("--json")
            return _run_remote_agent_lifecycle(
                target,
                "maintenance",
                remote_arguments,
                timeout=60,
            )

        from lib.agent_maintenance import run_agent_maintenance_command

        return run_agent_maintenance_command(args)

    if args.agent_command == "workspace":
        from lib.agent_workspace import run_agent_workspace_command

        return run_agent_workspace_command(args)

    if args.agent_command == "support-bundle":
        from lib.agent_support import run_agent_support_command

        return run_agent_support_command(args)

    if args.agent_command == "web":
        if args.agent_web_command == "pair":
            return run_agent_web_pair(args)
        print("Error: agent web command required (pair)")
        return 1

    if args.agent_command == "auth":
        from lib.agent_auth import (
            run_agent_auth_pull,
            run_agent_auth_set,
            run_agent_auth_status,
        )

        try:
            if args.agent_auth_command == "set":
                return run_agent_auth_set(args)
            if args.agent_auth_command == "status":
                return run_agent_auth_status(args)
            if args.agent_auth_command == "pull":
                return run_agent_auth_pull(args)
        except (OSError, RuntimeError, ValueError, EOFError, KeyboardInterrupt) as exc:
            print(f"Error: {exc}")
            return 1
        print("Error: agent auth command required (set, status, or pull)")
        return 1

    if args.agent_command == "update":
        selected = list(args.agent_update_tools or DEFAULT_UPDATE_TOOLS)
        tools_only_readiness = bool(getattr(args, "tools_only_readiness", False))
        try:
            target = _remote_agent_target(
                args,
                host_attribute="agent_update_host",
                username_attribute="agent_update_username",
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        if target is not None:
            remote_arguments: StrList = []
            for tool in args.agent_update_tools or []:
                remote_arguments.extend(("--tool", tool))
            if args.dry_run:
                remote_arguments.append("--dry-run")
            if args.json:
                remote_arguments.append("--json")
            if tools_only_readiness:
                remote_arguments.append("--tools-only-readiness")
            return _run_remote_agent_lifecycle(
                target,
                "update",
                remote_arguments,
                timeout=(_UPDATE_TIMEOUT_SECONDS * len(selected)) + 60,
            )
        try:
            results = update_agent_tools(selected, dry_run=args.dry_run)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}")
            return 1
        readiness_record: Optional[JSONDict] = None
        readiness_error: Optional[str] = None
        if not args.dry_run:
            try:
                readiness_record = _record_post_update_readiness(
                    selected,
                    include_capabilities=not tools_only_readiness,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                readiness_error = str(exc)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            heading = "Agent update plan" if args.dry_run else "Agent update result"
            print(heading)
            for result in results:
                tool = str(result["tool"])
                status = str(result["status"])
                if status == "planned":
                    print(
                        f"  • {tool}: {result['method']} ({result['before_version']})"
                        f" at {result.get('path') or 'unknown path'}"
                    )
                elif status in ("updated", "current"):
                    print(
                        f"  ✓ {tool}: {status} "
                        f"({result['before_version']} → {result['after_version']})"
                        f" at {result.get('path') or 'unknown path'}"
                    )
                else:
                    detail = str(result.get("failure", "unknown_error"))
                    if result.get("returncode") is not None:
                        detail += f" (exit {result['returncode']})"
                    rollback = "; rollback restored" if result.get("rollback") else ""
                    if result.get("rollback_failure"):
                        rollback = "; rollback verification failed"
                    print(
                        f"  ✗ {tool}: {detail}{rollback}"
                        f" at {result.get('path') or 'unknown path'}"
                    )
            if readiness_record is not None:
                status = (
                    "healthy"
                    if readiness_record.get("healthy") is True
                    else "UNHEALTHY"
                )
                print(f"  {'✓' if status == 'healthy' else '✗'} post-update readiness: {status}")
                if status != "healthy":
                    for detail in _readiness_failure_details(readiness_record):
                        print(f"      {detail}")
        if readiness_error is not None:
            print(
                f"Error: post-update readiness could not be recorded: {readiness_error}",
                file=sys.stderr,
            )
        elif (
            args.json
            and readiness_record is not None
            and readiness_record.get("healthy") is not True
        ):
            print(
                "Error: post-update readiness is unhealthy; inspect it with "
                "infra-tools agent doctor --last-record",
                file=sys.stderr,
            )
        updates_healthy = all(
            result["status"] in ("planned", "updated", "current")
            for result in results
        )
        readiness_healthy = (
            args.dry_run
            or (
                readiness_error is None
                and readiness_record is not None
                and readiness_record.get("healthy") is True
            )
        )
        return 0 if updates_healthy and readiness_healthy else 1

    if args.agent_command != "doctor":
        print(
            "Error: agent command required "
            "(doctor, update, auth, web, workspace, maintenance, or support-bundle)"
        )
        return 1

    requested_tools = getattr(args, "agent_doctor_tools", None)
    requested_capabilities = list(
        getattr(args, "agent_doctor_capabilities", None) or []
    )
    all_capabilities_requested = bool(
        getattr(args, "all_capabilities", False)
    )
    if all_capabilities_requested:
        requested_capabilities = list(AGENT_DOCTOR_CAPABILITIES)
    capabilities_explicit = bool(requested_capabilities)
    record_requested = bool(getattr(args, "record", False))
    last_record_requested = bool(getattr(args, "last_record", False))
    try:
        target = _remote_agent_target(
            args,
            host_attribute="agent_doctor_host",
            username_attribute="agent_doctor_username",
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    if target is not None:
        remote_arguments = []
        for tool in requested_tools or []:
            remote_arguments.extend(("--tool", tool))
        if all_capabilities_requested:
            remote_arguments.append("--all-capabilities")
        else:
            for capability in requested_capabilities:
                remote_arguments.extend(("--capability", capability))
        if getattr(args, "fix", False):
            remote_arguments.append("--fix")
        if record_requested:
            remote_arguments.append("--record")
        if last_record_requested:
            remote_arguments.append("--last-record")
        if args.json:
            remote_arguments.append("--json")
        return _run_remote_agent_lifecycle(
            target,
            "doctor",
            remote_arguments,
            timeout=_REMOTE_DOCTOR_TIMEOUT_SECONDS,
        )
    if last_record_requested:
        if requested_tools or requested_capabilities or getattr(args, "fix", False):
            print("Error: --last-record cannot be combined with checks or --fix")
            return 1
        from lib.agent_readiness import (
            format_agent_readiness_record,
            load_agent_readiness_record,
        )

        try:
            record = load_agent_readiness_record()
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}")
            return 1
        if record is None:
            print("Error: no agent readiness record is available")
            return 1
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            print(format_agent_readiness_record(record))
        return 0 if record.get("healthy") is True and record.get("current_boot") is True else 1

    if record_requested and not requested_capabilities:
        requested_capabilities.append("host")
        if _t3_readiness_expected(os.path.abspath(os.path.expanduser("~"))):
            requested_capabilities.append("t3code")
    if requested_tools:
        selected = list(requested_tools)
    elif capabilities_explicit and not all_capabilities_requested:
        selected = []
    else:
        selected = list(DEFAULT_DOCTOR_TOOLS)
    results = inspect_agent_tools(selected)
    if all_capabilities_requested:
        inventory_mode = not requested_tools
        for result in results:
            result["required"] = not inventory_mode or bool(result.get("installed"))
    capability_results: list[JSONDict] = []
    for capability in requested_capabilities:
        if capability == "browser":
            capability_results.append(inspect_browser_automation())
        elif capability == "development":
            capability_results.append(inspect_development_readiness())
        elif capability == "host":
            capability_results.append(inspect_host_readiness())
        elif capability == "t3code":
            capability_results.append(
                inspect_t3code(fix=getattr(args, "fix", False))
            )
    record_error: Optional[str] = None
    if record_requested:
        from lib.agent_readiness import record_agent_readiness

        try:
            record_agent_readiness(
                results,
                capability_results,
                trigger="manual",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            record_error = str(exc)
    if args.json:
        print(json.dumps([*results, *capability_results], indent=2))
    else:
        print("Agent tool check")
        for result in results:
            tool = str(result["tool"])
            if not result["installed"]:
                if result.get("required") is False:
                    print(f"  • {tool}: not installed (optional inventory)")
                else:
                    print(f"  ✗ {tool}: not installed")
                continue
            version = result.get("version")
            detail = f" ({version})" if version else ""
            print(f"  ✓ {tool}: {result['path']}{detail}")
            credential = result.get("credential")
            if credential is True:
                print("      credentials: present")
                credential_status = result.get("credential_status")
                if isinstance(credential_status, dict):
                    warning = codex_auth_warning(credential_status)
                    if warning:
                        print(f"      warning: {warning}")
            elif credential is False:
                print("      credentials: not found; run the tool to sign in")

        for result in capability_results:
            if result["capability"] == "t3code":
                if result["healthy"]:
                    print("  ✓ t3code: service, Git, pairing, and skill are ready")
                else:
                    print("  ✗ t3code: readiness checks failed")
                    for check, healthy in result["checks"].items():
                        if not healthy:
                            print(f"      {check}: failed")
                for fix in result.get("fixes", []):
                    print(f"      repaired: {fix}")
            elif result["capability"] == "development":
                if result["healthy"] and result["installed"]:
                    versions = ", ".join(
                        f"{name} {toolchain.get('version') or 'available'}"
                        for name, toolchain in result["toolchains"].items()
                        if toolchain.get("installed")
                    )
                    print(f"  ✓ development: {versions}")
                elif result["healthy"]:
                    print("  • development: no managed toolchains detected")
                else:
                    print("  ✗ development: toolchain baseline checks failed")
                    for issue in result["issues"]:
                        print(f"      {issue}")
            elif result["capability"] == "host":
                status = str(result["status"])
                marker = (
                    "✓" if status == "ok" else "⚠" if status == "warning" else "✗"
                )
                print(f"  {marker} host: {status}")
                for warning in result["warnings"]:
                    print(f"      warning: {warning}")
                for error in result["errors"]:
                    print(f"      error: {error}")
            elif result["healthy"]:
                print(f"  ✓ browser: {result['path']} (smoke test passed)")
            elif not result["installed"]:
                print("  ✗ browser: Playwright launchers are not installed")
            elif not result.get("launchers_secure"):
                print(
                    "  ✗ browser: managed launchers have unsafe ownership, "
                    "type, or permissions; inspect them and rerun the saved setup"
                )
            elif "registration_missing" in result.get("issues", []):
                print(
                    "  ✗ browser: agent MCP registration is missing; rerun setup "
                    "with browser automation"
                )
            elif "mcp_browser_selection_missing" in result.get("issues", []):
                print(
                    "  ✗ browser: MCP does not select managed Chromium; "
                    "rerun the saved setup"
                )
            elif "workflow_skill_missing_or_stale" in result.get("issues", []):
                print(
                    "  ✗ browser: managed workflow guidance is missing or stale; "
                    "rerun the saved setup"
                )
            elif not result.get("managed_defaults"):
                print(
                    "  ✗ browser: managed launcher defaults are stale; "
                    "rerun the saved setup"
                )
            elif "stale_processes" in result.get("issues", []):
                print(
                    "  ✗ browser: active MCP processes use stale defaults; "
                    "restart their agent sessions"
                )
            elif not result["smoke_test"]:
                print("  ✗ browser: local smoke test failed")
            else:
                print("  ✗ browser: capability checks failed")
        if record_requested and record_error is None:
            print("  ✓ readiness: private record saved")

    if record_error is not None:
        print(f"Error: readiness could not be recorded: {record_error}", file=sys.stderr)

    tools_healthy = all(
        result.get("required") is False
        or (
            bool(result["installed"])
            and result.get("credential_healthy") is not False
        )
        for result in results
    )
    capabilities_healthy = all(bool(result["healthy"]) for result in capability_results)
    return 0 if tools_healthy and capabilities_healthy and record_error is None else 1
