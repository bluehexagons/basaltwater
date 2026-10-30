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
The native `basaltw` command supports help, version, saved-installation `info`,
`doctor`, `refresh`, and `desktop` observation/control and supervised project tasks.
Use `basaltw refresh --dry-run` to preview or `basaltw refresh`
to update Basaltwater from main and reapply saved selections, including available
WinGet/npm tool updates; provider logins are preserved. `basaltw --help` lists Windows options.
Refresh follows live output in the terminal by default and opens a desktop
progress window that keeps its summary and survives T3 closing. Ctrl+C stops
following without cancelling setup; `--detach` returns after submission.
Inspect `refresh --status ID` and `--logs ID`, resume terminal output with
`--follow ID`, or reopen the progress window with `--progress ID`.
`--wait` runs setup directly in the invoking terminal; `--cancel ID` only cancels
a queued job. An updating T3 installer can close its desktop. Successful desktop
setup configures current-account login startup with a hidden PowerShell helper
and launches T3 without allocating an output console.
Windows setup uses the root `install.ps1` with `-Profile agent_windows` and
optional feature flags; downloaded and checkout entry points save the same
selections. The documented one-command PowerShell invocation installs a new PC
or updates an existing setup automatically, retaining its selections.
Native setup installs stable PowerShell 7 if missing and hands off to it before
applying development tools. It also supplies a managed Windows Terminal profile
and Start Menu development shell without replacing personal Terminal settings.
T3 starts or gracefully restarts after successful setup by default. For one run,
`-NoUpdateSource` / `refresh --no-update-source` keeps the saved Basaltwater source
while still updating tools; `-NoRestartT3` / `refresh --no-restart-t3` skips the
completion start/restart, retaining login startup. A vendor upgrade can still
close T3 independently.
Native setup also reboots Windows when an installer explicitly requires it or
Windows Update/component servicing reports a requirement. Pending file renames
or deletions alone are advisory and never trigger automatic reboot. A visible
desktop dialog shows the reason, restart time and a 60-second countdown with
Restart now and Later buttons; closing it also defers. A locked/unavailable
desktop or notification failure defers reboot. Apps are not forced closed. Use
`-NoReboot` / `refresh --no-reboot` to defer that for one run. Interrupted setup
continues once after a real boot and account login using saved source/selections;
that continuation defers any further reboot to prevent a loop. Inspect
`%LOCALAPPDATA%\Basaltwater\windows-restart.json` for reasons, queued/countdown
timestamps and outcome, and rerun setup if recovery
requires manual attention. WSL retains its manual Windows-reboot flow.
Use `basaltw doctor --json` for read-only checks of saved selections and actual
tool versions. Probes time out; failures return a nonzero exit. Desktop/C++ SDK
checks establish discovery, while logins, project builds, export templates and
GPU readiness require their own qualification. Doctor does not apply setup.

For native application observation, human handoff, or supervised project runs,
read [Windows desktop and tasks](references/desktop.md). Active or uncertain
development tasks block setup/reboot until cleanup is confirmed. These commands
do not install Basaltwater's Linux controller or expose GUI access from services.

Discover the actual shell with `$PSVersionTable.PSVersion` before composing
commands. The prepared development shell and supervised task guardians use
PowerShell 7 (`pwsh.exe`). `powershell.exe` selects Windows PowerShell 5.1;
use `pwsh.exe -NoProfile -File SCRIPT.ps1` for project scripts needing PowerShell 7.
`desktop doctor` reporting `worker: WindowsPowerShell-5.1` identifies its private
UI Automation helper, not the invoking development shell. That helper uses the
installed .NET Framework UI Automation assemblies. The CLI wrapper and worker
rebuild only their child module paths; the invoking shell's `PSModulePath` is
preserved. Call the public `basaltw` commands from PowerShell 7; no shell switch
or global module-path change is needed for desktop commands.
PowerShell 7 supports `&&`; Windows PowerShell 5.1 does not. Check
`$LASTEXITCODE` after native executables. Prefer `npm.cmd` to `npm.ps1` when
script execution policy blocks npm. Quote paths and use `-LiteralPath` for file
operations. Do not substitute WSL or translate a Linux build command into a
Windows command without project guidance.

The Microsoft Store `python.exe` alias can exist without an installed Python.
Verify the interpreter or use `uv run`/the project's virtual environment.
Use `Get-Command TOOL` and `TOOL --version` to distinguish discovery from a
working runtime. Setup normally restarts T3; reopen other terminals to inherit
the new PATH, and inspect the completion status if restart was deferred or failed.
Existing providers may be supplied
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

`-StorageDrive F:` (or `basaltw refresh --storage-drive F:`) selects
`F:\Basaltwater`; use `-StorageRoot PATH` for a custom directory, choosing
one option. The resolved root is saved for subsequent refresh/resume runs.
It puts Node versions and npm/uv/Go/vcpkg caches on that mounted NTFS drive
and provides `repos` for new checkouts. An unavailable drive fails without
falling back to C: or replacing saved selections. Inspect saved cache
environment variables and keep the drive connected. Existing checkouts/build
trees remain in place; do not assume the configured cache root moved them.

Optional `-Sunshine` (or `basaltw refresh --sunshine`) selects the native
streaming service and saves it for later refreshes. Setup can request UAC for
package/service configuration while retaining the intended account's tools
and logins. Doctor checks the selected service without mutations. It starts
at Windows boot. Pairing, display/GPU, sound and input still
need a Moonlight check. See [Windows Sunshine](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/windows-sunshine.md).

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
