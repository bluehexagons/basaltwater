"""Cheap, account-scoped feature discovery for panel pages and controls."""

from __future__ import annotations

import os
from dataclasses import dataclass

from lib.agent_cli import _tool_path
from lib.guacamole_config import SERVICE as DESKTOP_SERVICE
from lib.maintenance_agent_config import POLICY_FILE, SERVICE_NAME


@dataclass(frozen=True)
class PanelFeatures:
    """Installation evidence, independent of service health and sign-in state."""

    tools: frozenset[str] = frozenset()
    t3: bool = False
    maintenance: bool = False
    admin: bool = False
    desktop: bool = False

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
        if self.maintenance:
            pages.add("maintenance-agent")
        if self.admin:
            pages.add("admin")
        if self.desktop:
            pages.add("desktop")
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


def discover_features(home: str, *, admin: bool) -> PanelFeatures:
    """Check the panel account's managed search paths, never execute tools."""

    tools = frozenset(name for name in ("codex", "claude", "opencode", "git", "gh", "butler", "steamcmd")
                      if _tool_path(name, home))
    return PanelFeatures(
        tools=tools,
        t3=t3_installed(home),
        maintenance=os.path.isfile(POLICY_FILE) and unit_installed(SERVICE_NAME + ".service", home),
        admin=admin,
        desktop=unit_installed(DESKTOP_SERVICE + ".service", home),
    )
