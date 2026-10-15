"""Per-user managed browser MCP registrations, separate from runtime health."""

from __future__ import annotations

import os
import shutil
import stat

from common.browser_automation_steps import (
    PLAYWRIGHT_MCP_SERVER_NAME,
    PLAYWRIGHT_MCP_WRAPPER,
    _load_opencode_config,
    _opencode_config_path,
)
from lib.atomic_io import write_json_atomic
from lib.remote_utils import run
from lib.validation import validate_filesystem_path


def browser_registration(engine: str) -> tuple[str, list[str]]:
    """Keep the default Chromium server and opt-in Firefox server distinct."""
    if engine not in ("chromium", "firefox"):
        raise ValueError("Browser engine must be chromium or firefox")
    if engine == "chromium":
        return PLAYWRIGHT_MCP_SERVER_NAME, [PLAYWRIGHT_MCP_WRAPPER]
    return PLAYWRIGHT_MCP_SERVER_NAME + "-firefox", [PLAYWRIGHT_MCP_WRAPPER, "--engine", "firefox"]


def registration_status(registrations: dict[str, bool | None]) -> bool | None:
    """Keep an unavailable configuration parser distinct from a missing entry."""
    if any(value is True for value in registrations.values()):
        return True
    return None if any(value is None for value in registrations.values()) else False


def browser_registrations(engine: str, home: str | None = None, *, wrapper: str | None = None) -> dict[str, bool | None]:
    """Inspect configuration only; registration does not prove session exposure."""
    name, command = browser_registration(engine)
    if wrapper is not None:
        command[0] = wrapper
    home = home or os.path.expanduser("~")
    codex: bool | None = False
    try:
        # Older Python hosts cannot parse TOML reliably; report unknown there.
        import tomllib
    except ImportError:
        codex = None
    else:
        try:
            descriptor = os.open(os.path.join(home, ".codex", "config.toml"),
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, encoding="utf-8") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("Codex config must be a regular file")
                content = source.read(256 * 1024 + 1)
                if len(content) > 256 * 1024:
                    raise ValueError("Codex config is too large")
                config = tomllib.loads(content)
            entry = config.get("mcp_servers", {}).get(name, {})
            codex = (entry.get("command") == command[0]
                     and entry.get("args", []) == command[1:]
                     and entry.get("enabled", True) is True)
        except (OSError, ValueError, AttributeError):
            pass
    opencode = False
    try:
        config = _load_opencode_config(_opencode_config_path(os.path.join(home, ".config", "opencode")))
        entry = config.get("mcp", {}).get(name, {})
        opencode = (entry.get("type") == "local" and entry.get("enabled") is True
                    and entry.get("command") == command and entry.get("timeout") == 30000)
    except (OSError, ValueError, AttributeError):
        pass
    return {"codex": codex, "opencode": opencode}


def register_browser_agent(engine: str, agent_tool: str) -> None:
    """Register an already verified runtime for the invoking user's agent."""
    name, command = browser_registration(engine)
    home = os.path.expanduser("~")
    validate_filesystem_path(home, must_exist=True)
    if os.stat(home).st_uid != os.geteuid():
        raise ValueError("Browser registration must run as the owner of the agent home")
    if agent_tool == "codex":
        executable = shutil.which("codex")
        if not executable:
            raise ValueError("Codex is not on PATH")
        environment = {**os.environ, "CODEX_HOME": os.path.join(home, ".codex")}
        run([executable, "mcp", "add", name, "--", *command], capture_output=True, timeout=30, env=environment)
    elif agent_tool == "opencode":
        from common.agent_steps import _ensure_agent_directory, _reject_symlinked_agent_destination

        directory = os.path.join(home, ".config", "opencode")
        _ensure_agent_directory(directory)
        _reject_symlinked_agent_destination(directory)
        path = _opencode_config_path(directory)
        config = _load_opencode_config(path) if os.path.lexists(path) else {}
        servers = config.setdefault("mcp", {})
        if not isinstance(servers, dict):
            raise ValueError("OpenCode config 'mcp' entry must contain a JSON object")
        servers[name] = {"type": "local", "command": command, "enabled": True, "timeout": 30000}
        write_json_atomic(path, config, mode=0o600, sort_keys=True)
    else:
        raise ValueError("Browser registration requires codex or opencode")
