"""Refresh overridable context budgets without freezing native model capacities."""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import tomllib

from lib.agent_context_state import (
    context_digest, context_lock, context_paths, context_projection,
    read_context_state, remove_context_paths, save_context_state,
)
from lib.atomic_io import parse_json, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run
from lib.types import JSONDict
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


# Review the linked prices in docs/agents/agent-context.md before advancing this date.
POLICY_VERIFIED_ON = date(2026, 10, 15)
POLICY_REVIEW_DAYS = 7
CODEX_CONTEXT_DEFAULTS = {
    "model_auto_compact_token_limit": 240_000,
    "model_auto_compact_token_limit_scope": "total",
}
# Unknown/renamed Claude models get the conservative window too. Only reviewed
# flat-price models receive the larger ordinary default.
CLAUDE_CONTEXT_DEFAULTS = {
    "autoCompactWindow": 100_000,
    "modelSettings": {
        model: {"autoCompactWindow": 200_000}
        for model in ("claude-sonnet-5-5", "claude-opus-5-5")
    },
    "env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80"},
}
_REVIEWED_OPENAI = {
    "gpt-6.1-sol": (2.0, 10.0),
    "gpt-6-astra": (10.0, 50.0),
    "gpt-6-luna": (0.1, 0.5),
}
_FALLBACK_INPUT = 80_000
_MAX_BYTES = 1024 * 1024
_CODEX_BEGIN = "# Begin Basaltwater context defaults\n"
_CODEX_END = "# End Basaltwater context defaults\n"


def _policy_fresh() -> bool:
    age = (datetime.now(timezone.utc).date() - POLICY_VERIFIED_ON).days
    return 0 <= age <= POLICY_REVIEW_DAYS


def _codex_defaults() -> JSONDict:
    value = dict(CODEX_CONTEXT_DEFAULTS)
    if not _policy_fresh():
        value["model_auto_compact_token_limit"] = _FALLBACK_INPUT
    return value


def codex_context_defaults_toml() -> str:
    """Render top-level defaults without declaring a model's native capacity."""
    return "".join(f"{key} = {json.dumps(value)}\n" for key, value in _codex_defaults().items())


def _read(path: Path, uid: int) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid:
            raise ValueError(f"Expected an owned, singly linked configuration file: {path}")
        content = stream.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise ValueError(f"Agent configuration exceeds {_MAX_BYTES} bytes: {path}")
    return content.decode("utf-8")


def _prepare_path(path: Path, home: Path, uid: int, gid: int) -> None:
    validate_filesystem_path(str(path))
    if not home.is_absolute() or home == Path("/") or not path.is_relative_to(home):
        raise ValueError("Expected agent configuration inside an absolute user home")
    for parent in reversed(path.parents):
        if parent.is_symlink() or parent.exists() and not parent.is_dir():
            raise ValueError(f"Unsafe agent configuration directory: {parent}")
        if parent == home or home in parent.parents:
            if not parent.exists():
                parent.mkdir(mode=0o700)
                os.chown(parent, uid, gid)
            if parent.stat().st_uid != uid:
                raise ValueError(f"Agent configuration directory belongs to another user: {parent}")


def _defaults(tool: str) -> JSONDict:
    if tool == "codex":
        return _codex_defaults()
    defaults = copy.deepcopy(CLAUDE_CONTEXT_DEFAULTS)
    if not _policy_fresh():
        defaults.pop("modelSettings", None)
    return defaults


def _merge(value: JSONDict, defaults: JSONDict) -> None:
    for key, item in defaults.items():
        if isinstance(item, dict) and key in value:
            _merge(value[key], item)
        else:
            value[key] = copy.deepcopy(item)


def _codex_block(value: JSONDict) -> str:
    return _CODEX_BEGIN + "".join(f"{key} = {json.dumps(value[key])}\n" for key in sorted(value)) + _CODEX_END


def _seed_locked(path: Path, tool: str, account: pwd.struct_passwd, state_path: Path) -> None:
    previous = _read(path, account.pw_uid)
    state = read_context_state(_read(state_path, account.pw_uid), tool)
    if tool == "codex":
        value = tomllib.loads(previous or "")
    else:
        value = parse_json(previous) if previous is not None else {}
        if not isinstance(value, dict):
            raise ValueError(f"Agent configuration must contain a JSON object: {path}")
    projection = context_projection(value, tool)
    digest = context_digest(projection)
    state = state or {"version": 1, "tool": tool, "overridden": False, "active": None, "pending": None}
    active = state.get("active")
    if state.get("pending") and state["pending"]["digest"] == digest:
        active = state["pending"]
    elif active is not None and active["digest"] != digest:
        state["overridden"] = True
    if state["overridden"] or active is None and projection:
        state.update(overridden=True, active=None, pending=None)
        save_context_state(state_path, state, account.pw_uid, account.pw_gid)
        print(f"  ✓ Preserved user context policy: {path}; managed refresh is disabled for this file")
        return

    if active is not None and sorted(active["paths"]) != sorted(context_paths(projection)):
        raise ValueError(f"Managed context paths do not match recorded configuration: {state_path}")

    old_block = _codex_block(projection) if active is not None and tool == "codex" else ""
    if old_block and not (previous or "").startswith(old_block):
        state.update(overridden=True, active=None, pending=None)
        save_context_state(state_path, state, account.pw_uid, account.pw_gid)
        print(f"  ✓ Preserved edited Codex context block: {path}")
        return
    if active is not None:
        remove_context_paths(value, active["paths"])
    _merge(value, _defaults(tool))
    projection = context_projection(value, tool)
    record = {"digest": context_digest(projection), "paths": context_paths(projection)}
    if record["digest"] == digest:
        state.update(active=record, pending=None)
        save_context_state(state_path, state, account.pw_uid, account.pw_gid)
        print(f"  ✓ Managed context defaults are current: {path}")
        return
    if tool == "codex":
        content = _codex_block(projection) + (previous or "")[len(old_block):]
        tomllib.loads(content)
    else:
        content = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > _MAX_BYTES:
        raise ValueError(f"Updated agent configuration exceeds {_MAX_BYTES} bytes: {path}")
    # Journal before replacing configuration. Either the old or new digest can
    # be recovered after interruption; any third value is treated as user work.
    state["pending"] = record
    save_context_state(state_path, state, account.pw_uid, account.pw_gid)
    if _read(path, account.pw_uid) != previous:
        raise RuntimeError(f"Agent configuration changed during setup; retry: {path}")
    write_text_atomic(str(path), content, mode=0o600, uid=account.pw_uid, gid=account.pw_gid)
    state.update(active=record, pending=None)
    save_context_state(state_path, state, account.pw_uid, account.pw_gid)
    print(f"  ✓ Refreshed managed context defaults: {path}")


def _seed(path: Path, tool: str, account: pwd.struct_passwd) -> None:
    _prepare_path(path, Path(account.pw_dir), account.pw_uid, account.pw_gid)
    state_path = path.with_name(f".{path.name}.basaltwater-context.json")
    with context_lock(path.with_name(f".{path.name}.basaltwater-context.lock"), account.pw_uid, account.pw_gid):
        _seed_locked(path, tool, account, state_path)


def _check_opencode_context_support(config: SetupConfig, account: pwd.struct_passwd) -> None:
    """Qualify the provider hook without starting a model or reading auth files."""
    search_path = os.pathsep.join((os.path.join(account.pw_dir, ".local/bin"),
                                  os.path.join(account.pw_dir, ".opencode/bin"),
                                  os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")))
    if os.geteuid() == account.pw_uid:
        executable = shutil.which("opencode", path=search_path)
    else:
        from common.agent_steps import _tool_path

        executable = _tool_path(config, "opencode")
    if not executable:
        raise RuntimeError("OpenCode context setup requires an installed OpenCode CLI")
    validate_filesystem_path(executable)
    command = ["env", "-i", f"HOME={account.pw_dir}", f"USER={config.username}",
               f"LOGNAME={config.username}", f"PATH={search_path}", "NO_COLOR=1", executable, "--version"]
    if os.geteuid() != account.pw_uid:
        command = ["runuser", "-u", config.username, "--", *command]
    result = run(command, capture_output=True, check=False, timeout=20)
    match = re.fullmatch(r"1\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?", (result.stdout or "").strip())
    if result.returncode or match is None or (int(match[1]), int(match[2])) < (19, 0):
        raise RuntimeError("Context budgets require qualified OpenCode 1.x >= 1.19.0; run 'basaltw agent update --tool opencode' as the provider user (or use its owning package manager), then rerun setup. Review a new major version's provider-hook API first.")


def _install_opencode_context(config: SetupConfig, account: pwd.struct_passwd) -> None:
    from lib.opencode_context import plugin_content

    path = Path(account.pw_dir) / ".config/opencode/plugins/basaltwater-context.js"
    _prepare_path(path, Path(account.pw_dir), account.pw_uid, account.pw_gid)
    state_path = path.with_name(".basaltwater-context-state.json")
    with context_lock(path.with_name(".basaltwater-context.lock"), account.pw_uid, account.pw_gid):
        previous = _read(path, account.pw_uid)
        recorded = _read(state_path, account.pw_uid)
        state = {"version": 1, "disabled": False, "active": None, "pending": None}
        if recorded is not None:
            state = parse_json(recorded)
            if (not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1
                    or type(state.get("disabled")) is not bool or "active" not in state or "pending" not in state
                    or any(state[key] is not None and (not isinstance(state[key], str)
                           or re.fullmatch(r"[0-9a-f]{64}", state[key]) is None) for key in ("active", "pending"))):
                raise ValueError(f"Invalid OpenCode context plugin ownership: {state_path}")
        digest = context_digest({"plugin": previous}) if previous is not None else None
        if (state["disabled"] or previous is None and state["active"] is not None
                or previous is not None and digest not in (state["active"], state["pending"])):
            state.update(disabled=True, pending=None)
            save_context_state(state_path, state, account.pw_uid, account.pw_gid)
            print(f"  ! Preserved personal or removed OpenCode context plugin: {path}; managed refresh is disabled")
            return
        _check_opencode_context_support(config, account)
        content = plugin_content(POLICY_VERIFIED_ON.isoformat(), POLICY_REVIEW_DAYS, _REVIEWED_OPENAI)
        updated = context_digest({"plugin": content})
        if previous != content:
            state["pending"] = updated
            save_context_state(state_path, state, account.pw_uid, account.pw_gid)
            if _read(path, account.pw_uid) != previous:
                raise RuntimeError(f"OpenCode context plugin changed during setup; retry: {path}")
            write_text_atomic(str(path), content, mode=0o600, uid=account.pw_uid, gid=account.pw_gid)
        state.update(active=updated, pending=None)
        save_context_state(state_path, state, account.pw_uid, account.pw_gid)
    print(f"  ✓ OpenCode context hook configured: {path}; explicit model limits and disabled compaction are preserved")


def configure_agent_context_defaults(config: SetupConfig) -> None:
    """Refresh selected providers after payload copying, before any client starts."""
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would refresh owned agent context defaults; preserve user overrides")
        return
    if not validate_username(config.username):
        raise ValueError("Invalid agent username")
    if not any((config.install_codex, config.install_claude, config.install_opencode)):
        return
    if not _policy_fresh():
        print(f"  ! Context pricing review is stale (verified {POLICY_VERIFIED_ON}); using conservative managed defaults. Review docs/agents/agent-context.md.")
    account = pwd.getpwnam(config.username)
    home = Path(account.pw_dir)
    if config.install_codex and config.system_type in {"agent_cachyos", "server_wsl"}:
        _seed(home / ".codex/config.toml", "codex", account)
    if config.install_claude:
        _seed(home / ".claude/settings.json", "claude", account)
    if config.install_opencode:
        _install_opencode_context(config, account)
