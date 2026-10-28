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
The native `basaltw` command supports help, version, saved-installation `info`
and `refresh`. Use `basaltw refresh --dry-run` to preview or `basaltw refresh`
to update Basaltwater from main and reapply saved selections; existing tool
versions and logins are preserved. `basaltw --help` lists Windows options.
Checkout-only setup needs a pinned installer run before refresh/info work.

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

Optional `-GameDev` supplies native C++/SDK, CMake, Ninja, LLVM and packaging
tools. Enter the supported x64 Visual Studio developer environment before
Ninja builds or C analysis; `cl.exe` on PATH alone does not supply SDK headers
and libraries. `-ShaderTools` separately supplies shader validation.
`-Godot` (or `basaltw refresh --godot`) supplies the standard engine/CLI.
Honor project engine pins and install matching export templates; Godot .NET,
its SDK and platform export SDKs remain separate project requirements.
With `-NodeVersions`, use fnm to install the project's reviewed runtime and
`fnm exec --using=VERSION -- npm.cmd ...` to select it per command. The system
Node LTS may be too old for the checkout. Keep the project's vcpkg/library pins
and bootstrap authoritative; host tools do not establish game/GPU readiness.
See [Windows game tooling](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-game-development.md).

A selected `-StorageRoot` puts Node versions and npm/uv/Go/vcpkg caches on that
mounted NTFS drive and provides `repos` for new checkouts. Inspect saved cache
environment variables and keep the drive connected. Existing checkouts/build
trees remain in place; do not assume the configured cache root moved them.

Use the installed `basaltwater-project-development` skill for repository work,
`basaltwater-frontend-design` for UI work, and `basaltwater-subagents` for
authorized delegation. Basaltwater CLI examples beyond the small native command
surface require a compatible Linux environment; native Git and the active T3
tools remain usable.
T3 owns worktrees it creates. Native Windows and WSL providers have separate
homes, settings, and skills; choose the local Windows connection for these projects.

T3 browser and orchestration tools exist only when exposed to the active session.
Follow that session's injected instructions. The installed
`basaltwater-t3-preview-testing` skill supplies browser guidance without installing
Playwright or configuring a browser. Authenticate selected providers as this
account and preserve existing permissions and approval settings.
