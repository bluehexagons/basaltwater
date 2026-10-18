"""Cheap, account-scoped feature discovery for panel pages and controls."""

from __future__ import annotations

import os
from dataclasses import dataclass

from lib.agent_cli import _tool_path
from lib.atomic_io import read_json_file
from lib.guacamole_config import SERVICE as DESKTOP_SERVICE, SSH_SERVICE
from lib.maintenance_agent_config import POLICY_FILE, SERVICE_NAME
from lib.github_runner_config import POLICY as RUNNER_POLICY, SERVICE as RUNNER_SERVICE

MANIFEST = "/etc/basaltwater/web-panel/config.json"
AGENT_TOOLS = frozenset({"codex", "claude", "opencode", "gh"})


@dataclass(frozen=True)
class PanelFeatures:
    """Selected, installed features, independent of health and sign-in state."""

    tools: frozenset[str] = frozenset()
    t3: bool = False
    maintenance: bool = False
    admin: bool = False
    desktop: bool = False
    console: bool = False
    github_runner: bool = False

    @property
    def prompts(self) -> bool:
        return "codex" in self.tools

    @property
    def agents(self) -> bool:
        return self.t3 or bool(self.tools & {"codex", "claude", "opencode"})

    @property
    def pages(self) -> frozenset[str]:
        pages = {"dashboard", "services", "jobs", "logs"}
        if self.agents:
            pages.update(("agents", "agent-inbox"))
        if self.prompts:
            pages.add("agent-tools")
        if "git" in self.tools:
            pages.add("credentials")
        if self.tools & {"butler", "steamcmd"}:
            pages.add("publishing")
        if self.maintenance and self.agents:
            pages.add("maintenance-agent")
        if self.admin:
            pages.add("admin")
        if self.desktop:
            pages.add("desktop")
        if self.console:
            pages.add("console")
        if self.github_runner:
            pages.add("github-runner")
        return frozenset(pages)


def unit_installed(unit: str, home: str, *, user: bool = False) -> bool:
    """Inspect fixed unit files without querying or starting a service manager."""

    directories = (
        (os.path.join(home, ".config/systemd/user"), "/etc/systemd/user", "/usr/lib/systemd/user", "/lib/systemd/user")
        if user else ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system")
    )
    for directory in directories:
        path = os.path.join(directory, unit)
        if os.path.islink(path) and os.path.realpath(path) == "/dev/null":
            return True  # A masked installation still needs inspection.
        if os.path.isfile(path):
            return True
    return False


def t3_installed(home: str) -> bool:
    return os.path.isdir(os.path.join(home, ".t3/runtime")) or unit_installed("t3code.service", home, user=True)


def discover_features(
    home: str, *, admin: bool, configured: dict[str, object] | None = None,
) -> PanelFeatures:
    """Intersect setup selections with installation evidence; never run tools.

    The desktop/console gateway reads the same panel manifest. Missing legacy
    selections fail closed until setup republishes them; stale binaries and
    units must not opt a monitoring-only machine into agent features.
    """

    if configured is None:
        try:
            manifest = read_json_file(MANIFEST, max_bytes=256 * 1024)
            configured = manifest.get("features") if isinstance(manifest, dict) else None
        except (OSError, ValueError):
            configured = None
    if not isinstance(configured, dict):
        configured = {}
    selected = configured.get("agent_tools", [])
    allowed = {"butler", "steamcmd"}
    if isinstance(selected, list) and all(isinstance(name, str) for name in selected):
        allowed.update(AGENT_TOOLS.intersection(selected))
    if configured.get("git") is True:
        allowed.add("git")

    tools = frozenset(name for name in ("codex", "claude", "opencode", "git", "gh", "butler", "steamcmd")
                      if name in allowed and _tool_path(name, home))
    t3 = configured.get("t3_update") is True and t3_installed(home)
    agents = t3 or bool(tools & {"codex", "claude", "opencode"})
    return PanelFeatures(
        tools=tools,
        t3=t3,
        maintenance=agents and os.path.isfile(POLICY_FILE) and unit_installed(SERVICE_NAME + ".service", home),
        admin=admin,
        desktop=unit_installed(DESKTOP_SERVICE + ".service", home),
        console=unit_installed(SSH_SERVICE + ".socket", home),
        github_runner=os.path.isfile(RUNNER_POLICY) and unit_installed(RUNNER_SERVICE, home),
    )
