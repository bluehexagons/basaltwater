---
name: basaltwater-windows-workspace
description: Work in a native Windows coding account prepared by Basaltwater, including PowerShell commands, tool discovery, and local T3 Code projects. Use Ubuntu-specific guidance for WSL projects.
metadata:
  managed-by: basaltwater
---

# Native Windows coding

Use native project paths such as `C:\Users\NAME\repos` and the project's Windows
build/test commands. Native setup prepares this account's tools and skills; it
does not port the Linux controller, `basaltw agent`, systemd or Debian profiles.
Windows and WSL have separate homes, settings and skills. Choose the actual
Windows connection for native projects; WSL results do not qualify them.

The native `basaltw` surface includes help/version, saved-installation `info`,
read-only `doctor`, maintenance `refresh`, and `desktop` commands. Discover only
the surface needed for the task with its `--help`. Host maintenance is not a
startup checklist: refresh can update tools, close T3 and request reboot.
Active or uncertain development tasks block setup/reboot until cleanup settles.

Use `basaltw doctor --json` for selected host-tool health, or
`desktop develop doctor --project PATH` for project discovery. Discovery is not
a build, login, GPU or export qualification. `doctor --skills --json` compares
known bundles with saved source/revision without catalog writes; absent optional
bundles are informational and session-loaded provenance can remain unknown.
Do not collect every inventory for a narrow question.

The prepared shell and task guardians use PowerShell 7 (`pwsh.exe`);
`powershell.exe` selects 5.1. Check `$PSVersionTable.PSVersion` before composing
commands. Use the native `basaltw.exe` launcher from PowerShell 7 for literal
argv; the explicit `.cmd` fallback and 5.1 native binder have legacy quoting
limits. Check `$LASTEXITCODE` after native programs, quote paths and use
`-LiteralPath` for file operations. Do not translate Linux commands or substitute
WSL without project guidance. Preserve provider installations and logins.

Read only the relevant procedure:

| Work | Reference |
| --- | --- |
| Native desktop observation/control, human handoff or supervised project tasks | [Desktop and tasks](references/desktop.md) |
| Shell/runtime discovery, C++/SDK, Node pins or Godot | [Development tools](references/development.md) |
| Authorized setup/refresh, interrupted updates, reboot, storage or streaming | [Setup and maintenance](references/setup.md) |
| T3 source/version, connection, policy or session-tool mismatches | [T3 environment](references/t3.md) |

For repository changes use the installed project-development skill; select
frontend-design or subagent guidance only when that work applies. T3 owns its
worktrees. Browser/orchestration tools exist only when exposed to this session;
follow its injected policy and retain existing permissions.
