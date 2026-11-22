# Provision managed Playwright

[Documentation index](browser-automation.md)

## Provisioning

Select at least one compatible agent and request the provider explicitly:

```bash
basaltw setup workstation_dev 10.23.0.50 agent \
  --provision-on pve1 --name agent-1 \
  --image-storage local \
  --memory 4G --balloon-min 1G --storage root local-lvm 32G \
  --agent-tool opencode,gh,codex \
  --browser-automation playwright \
  --node --go --rdp --password "$RDP_PASSWORD" \
  --rdp-source 10.23.0.0/24 --rdp-source 10.24.0.0/24
```

The address, registered Proxmox host and storage names are fictional examples;
replace them for your environment. The `--password` option passes its value in
a command-line argument. A shell variable keeps the literal value out of shell
history, but process inspectors may still see the expanded argument. Run setup from a trusted controller and
never put literal credentials in a saved or committed command.

`--browser-automation playwright` currently requires `--agent-tool codex`
or `--agent-tool opencode`. It registers the managed
`basaltwater-playwright` MCP server
only for the compatible tools selected by that setup. GitHub CLI, Claude Code,
and T3 Code do not receive a browser integration. The browser capability owns
its root-owned Node.js dependency, so `--node` is not required unless projects on
the VM also need the user-managed Node.js development environment.

The interactive setup flow offers the same choice after agent selection. A
saved setup command retains the provider choice, so a later full setup restores
the browser capability without retaining any browser or website credentials.
An agent setup that omits the option also refreshes Node and the launchers for a
complete managed browser installation with current Playwright packages. An
explicit `--no-browser-automation` suppresses that reconciliation. If setup
reports that the Playwright packages need migration, rerun the saved setup with
`--browser-automation playwright` to provision both packages and Node.
For an explicit `--steps` setup, include `install_browser_automation` after
installing the selected compatible agent; it is available as a registered
custom step as well.

Compatible agent setups install `basaltwater-playwright-testing` when this is
the only browser capability. A VM that also provisions T3 Code receives the
combined `basaltwater-browser-testing` skill instead. Setup removes a stale
Basaltwater-managed browser variant when that capability combination changes;
see [Managed agent workflow skills](agent-skills.md).

## Installed components and agent configuration

Setup installs two independently verified packages:

- Stable `@playwright/test` 1.64.1 under `/opt/basaltwater-playwright-stable`
  for engine doctors and Basaltwater's application suite (`--context managed`).
- `@playwright/mcp` 0.0.83 under `/opt/basaltwater-playwright` for MCP clients
  (`--context managed-mcp`). Its exact Playwright dependency is
  `1.64.0-alpha-1790635538000`, an explicit upstream prerelease exception.

Setup verifies package versions and registry integrity metadata, installs native
browser dependencies, and downloads each runtime's matching Chromium build into
the target user's Playwright cache. Browser garbage collection is disabled to
preserve the other runtime's payloads. npm lifecycle scripts are disabled during
the root-owned package installs, which use isolated npm configuration.
Both use Node 24 LTS from a versioned path under `/opt/basaltwater-node`.
The [service runtime definition](../../lib/service_node.py) records the exact
release and official SHA-256 checksums for Linux x86_64 and aarch64.
Setup installs the Debian runtime libraries before executing that interpreter.
Setup verifies root ownership, permissions and the interpreter version before
installing or reusing browser packages. The distro interpreter is left to the
distribution; writable user NVM installations never run these privileged tasks.
The setup marker is written only after the resolved pinned Chromium executable is
present and executable, so a stale marker cannot suppress a required browser
download.

The managed launchers are:

- `/usr/local/bin/basaltwater-playwright-mcp` for MCP clients;
- `/usr/local/bin/basaltwater-playwright-doctor` for a local smoke test.

Codex registration is performed through `codex mcp`; OpenCode's existing JSON
or JSONC configuration is merged atomically. JSONC comments and formatting are
normalized during the rewrite, while configuration values are preserved. If
`--agent-config active` copied a config,
the browser registration is applied afterward, so the explicit setup choice
owns the `basaltwater-playwright` entry while preserving unrelated settings and
MCP servers.
Malformed target configuration is rejected. Symlinked, hard-linked, or
non-regular target files are rejected before their contents are read or merged.
Unterminated block comments and comments splitting a JSON token are treated as
malformed; registration leaves that file unchanged instead of repairing it
into a potentially different configuration.

The MCP launcher resolves the Chromium executable from the same pinned
Playwright package used to provision the user browser cache and passes that
path explicitly to MCP. It does not rely on MCP's default Chrome channel. The
launcher always starts Chromium in headless, isolated mode and enables
Playwright's bounded vision capability. Each MCP
connection receives a temporary browser profile; cookies and login state do not
persist between sessions. This avoids profile locking between simultaneous
agents and prevents one task from silently inheriting another task's browser
session. Use the normal desktop browser over RDP when a durable, human-managed
browser profile is required.

The managed MCP configuration at `/opt/basaltwater-playwright/mcp-config.json`
and the direct smoke test disable Chromium's `NativeNotifications` feature.
Headless browser startup therefore avoids activating the display-dependent
XFCE notification daemon on the user's service bus. This does not disable
notifications in the interactive desktop browser or modify the desktop session.
Rerunning setup refreshes the configuration and launchers; existing MCP sessions
pick it up when they next restart.

Generated snapshots, console captures, screenshots, and other MCP evidence
default to the private
`~/.local/state/basaltwater/playwright-mcp` directory instead of the current
project. The launcher applies a 256 MiB output ceiling so older evidence is
evicted without dirtying or filling active Git worktrees. An agent may still
name an explicit output file when the evidence belongs in a requested
deliverable. Omit `filename` for routine evidence: Playwright resolves unnamed
artifacts below the private output directory and returns their paths, while an
explicit name deliberately resolves in the active workspace.
