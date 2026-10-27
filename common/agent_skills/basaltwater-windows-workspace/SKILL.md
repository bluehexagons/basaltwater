---
name: basaltwater-windows-workspace
description: Work in a native Windows coding account prepared by Basaltwater, including PowerShell commands, tool discovery, and local T3 Code projects. Use Ubuntu-specific guidance for WSL projects.
metadata:
  managed-by: basaltwater
---

# Native Windows coding

Keep Windows projects on native paths such as `C:\Users\NAME\repos`. Use the
project's documented Windows build/test commands. The native setup installs
tools and skills for the current Windows account; it does not port Basaltwater's
Linux controller, `basaltw agent` commands, systemd, or Debian setup profiles.

Discover the actual shell and tools before composing commands. Windows
PowerShell 5.1 does not support `&&`; use separate invocations and check
`$LASTEXITCODE` after native executables. Prefer `npm.cmd` to `npm.ps1` when
script execution policy blocks npm. Quote paths and use `-LiteralPath` for file
operations. Do not substitute WSL or translate a Linux build command into a
Windows command without project guidance.

The Microsoft Store `python.exe` alias can exist without an installed Python.
Verify the interpreter or use `uv run`/the project's virtual environment.
Use `Get-Command TOOL` and `TOOL --version` to distinguish discovery from a
working runtime. After installations, start a new terminal and restart T3 Code
so provider processes inherit the new PATH. Existing providers may be supplied
by a desktop app rather than npm; preserve their installation and login.

Use the installed `basaltwater-project-development` skill for repository work,
`basaltwater-frontend-design` for UI work, and `basaltwater-subagents` for
authorized delegation. Basaltwater CLI examples in shared references require
a compatible Linux environment; native Git and the active T3 tools remain usable.
T3 owns worktrees it creates. Native Windows and WSL providers have separate
homes, settings, and skills; choose the local Windows connection for these projects.

T3 browser and orchestration tools exist only when exposed to the active session.
Follow that session's injected instructions. The installed
`basaltwater-t3-preview-testing` skill supplies browser guidance without installing
Playwright or configuring a browser. Authenticate selected providers as this
account and preserve existing permissions and approval settings.
