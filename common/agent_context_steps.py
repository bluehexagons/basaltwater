"""Overridable context budgets for managed coding tools.

Pricing and native controls were checked on 2026-10-15; see
docs/AGENT_CONTEXT.md before changing these budgets or adding models.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import pwd
import stat
import tomllib

from lib.atomic_io import parse_json, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run
from lib.types import JSONDict
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


CODEX_CONTEXT_DEFAULTS = {
    "model_auto_compact_token_limit": 240_000,
    "model_auto_compact_token_limit_scope": "total",
}
CLAUDE_CONTEXT_DEFAULTS = {
    "autoCompactWindow": 200_000,
    "modelSettings": {"claude-haiku-5-5": {"autoCompactWindow": 100_000}},
    "env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80"},
}
OPENCODE_CONTEXT_DEFAULTS = {
    "compaction": {"auto": True},
    "provider": {
        "openai": {"models": {
            model: {"limit": {"context": 1_050_000, "input": 240_000, "output": 128_000}}
            for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna")
        }},
        "anthropic": {"models": {
            "claude-haiku-5-5": {
                "limit": {"context": 1_000_000, "input": 80_000, "output": 128_000},
            },
        }},
    },
}
_MAX_BYTES = 1024 * 1024


def codex_context_defaults_toml() -> str:
    """Render top-level defaults without declaring a model's native capacity."""
    return "".join(f"{key} = {json.dumps(value)}\n" for key, value in CODEX_CONTEXT_DEFAULTS.items())


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


def _fill_missing(value: JSONDict, defaults: JSONDict) -> bool:
    changed = False
    for key, default in defaults.items():
        if key not in value:
            value[key] = copy.deepcopy(default)
            changed = True
        elif isinstance(default, dict) and key != "limit":
            if not isinstance(value[key], dict):
                raise ValueError(f"Agent configuration '{key}' must contain an object")
            changed = _fill_missing(value[key], default) or changed
        # A configured limit is a single explicit user choice. Never complete
        # it with defaults that could increase a smaller model's capacity.
    return changed


def _claude_context_configured(value: JSONDict) -> bool:
    """An existing global or per-model budget is already a user's policy."""
    for key in ("env", "modelSettings"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f"Claude configuration '{key}' must contain an object")
    models = value.get("modelSettings", {})
    if any(not isinstance(model, dict) for model in models.values()):
        raise ValueError("Claude per-model settings must contain objects")
    return (
        "autoCompactWindow" in value
        or any("autoCompactWindow" in model for model in models.values())
        or bool({
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            "DISABLE_COMPACT", "DISABLE_AUTO_COMPACT",
        }.intersection(value.get("env", {})))
    )


def _seed(path: Path, tool: str, account: pwd.struct_passwd) -> None:
    defaults = {
        "codex": CODEX_CONTEXT_DEFAULTS,
        "claude": CLAUDE_CONTEXT_DEFAULTS,
        "opencode": OPENCODE_CONTEXT_DEFAULTS,
    }[tool]
    _prepare_path(path, Path(account.pw_dir), account.pw_uid, account.pw_gid)
    previous = _read(path, account.pw_uid)
    if tool == "codex":
        value = tomllib.loads(previous or "")
        missing = {key: default for key, default in defaults.items() if key not in value}
        if not missing:
            print(f"  ✓ Preserved existing context settings: {path}")
            return
        # Prepending keeps these keys at the root even if the file ends inside
        # a table, and preserves user comments and profile/MCP configuration.
        content = (
            "# Basaltwater context defaults; user and launch overrides are supported.\n"
            + "".join(f"{key} = {json.dumps(default)}\n" for key, default in missing.items())
            + (previous or "")
        )
        tomllib.loads(content)
    else:
        from common.browser_automation_steps import _strip_jsonc_comments, _strip_jsonc_trailing_commas

        normalized = previous or ""
        if path.suffix == ".jsonc":
            normalized = _strip_jsonc_trailing_commas(_strip_jsonc_comments(normalized))
        value = parse_json(normalized) if previous is not None else {}
        if not isinstance(value, dict):
            raise ValueError(f"Agent configuration must contain a JSON object: {path}")
        # A new per-model window could override an explicit global window, and
        # a new percentage could shorten an explicit per-model window. Keep an
        # existing Claude context policy as a whole, including its formatting.
        if tool == "claude" and _claude_context_configured(value):
            print(f"  ✓ Preserved existing context policy: {path}")
            return
        if not _fill_missing(value, defaults):
            print(f"  ✓ Preserved existing context settings: {path}")
            return
        content = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > _MAX_BYTES:
        raise ValueError(f"Updated agent configuration exceeds {_MAX_BYTES} bytes: {path}")
    write_text_atomic(str(path), content, mode=0o600, uid=account.pw_uid, gid=account.pw_gid)
    print(f"  ✓ Filled missing context defaults: {path} (existing values retained)")


def configure_agent_context_defaults(config: SetupConfig) -> None:
    """Seed selected tools after payload copying, before starting any client."""
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would fill missing agent context budgets; retain explicit overrides")
        return
    if not validate_username(config.username):
        raise ValueError("Invalid agent username")
    if not any((config.install_codex, config.install_claude, config.install_opencode)):
        return
    account = pwd.getpwnam(config.username)
    home = Path(account.pw_dir)
    # Debian VM setup puts Codex defaults in /etc/codex/config.toml so personal
    # configuration remains higher precedence. Local profiles do not own /etc.
    if config.install_codex and config.system_type in {"agent_cachyos", "server_wsl"}:
        _seed(home / ".codex/config.toml", "codex", account)
    if config.install_claude:
        _seed(home / ".claude/settings.json", "claude", account)
    if config.install_opencode:
        directory = home / ".config/opencode"
        path = directory / "opencode.json"
        if not os.path.lexists(path) and os.path.lexists(directory / "opencode.jsonc"):
            path = directory / "opencode.jsonc"
        _seed(path, "opencode", account)
