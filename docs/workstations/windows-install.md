# Install or refresh the Windows profile

[Topic index](windows.md)

## Install

Open PowerShell as the intended account. Allow at least 5 GiB free for the
tools, plus space for projects and dependencies. This invocation adds Node.js
LTS and Python/uv to the [default tool set](windows.md).

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1))) -Profile agent_windows -AgentTool codex -Node -Python
```

To add providers on the first run, replace `-AgentTool codex` with one list,
such as `-AgentTool codex,claude,opencode`.

Append optional flags to the same command:

- `-Sunshine` adds the native Moonlight streaming host.
- `-GameDev -ShaderTools -Godot` adds native game tools, shader validation and Godot.
- `-StorageDrive F:` selects a mounted NTFS drive for development caches.
- `-Plan` previews selections without applying setup.
- `-NoUpdateSource` reapplies the saved Basaltwater revision while still updating tools.
- `-NoRestartT3` skips the T3 Code start/restart after setup.
- `-NoReboot` reports required Windows reboots without restarting Windows.

Rerun the same command to update an existing workstation. Setup automatically
retains saved selections, resolves current Basaltwater source and installs
or updates selected tools, then starts or gracefully restarts T3 Code.
It also runs `winget upgrade --all`, which can update other installed Windows
packages managed by WinGet.
When Windows requires a reboot, setup queues it automatically instead of
restarting T3 immediately; desktop startup uses the next account login.
No resume, source-update or desktop-restart flag is needed. These opt-outs apply
to one invocation and do not change the next run's defaults.

After source staging, native setup installs stable PowerShell 7 if missing and
hands off to it before applying the development tools. It preserves saved
selections and literal arguments and releases the setup lock before re-entry.
The PowerShell 5.1 bootstrap remains usable on a fresh Windows installation.
If WinGet is missing or its version check fails, setup tries to register App
Installer for the account and then repairs WinGet if it is still unavailable.
Setup also adds a Basaltwater Windows Terminal profile and Start Menu development
shell. See [native desktop and project integration](windows-desktop.md) for
artifact opening, UI Automation, screenshots and supervised tasks.

The downloaded root installer relays through a temporary script in a child
Windows PowerShell process with execution-policy bypass. It preserves typed
flags, provider arrays and literal paths, reports failures and removes the
temporary script afterward. The account's saved execution policy is unchanged.
Package installers and selected machine-wide service configuration can ask for
UAC; account tooling and provider logins remain with the intended account.
CI runner registration and managed build-host setup use their existing flags.

`basaltw refresh --detach --json` returns the queued `job.id`. Pass that ID
to `refresh --status ID` for the current `job.state`, `refresh --logs ID --json`
for a bounded log excerpt, or `refresh --cancel ID` while still queued.
`refresh --follow ID` streams text, as does an ordinary foreground refresh.

## Verify the fresh installation

After setup and any requested reboot, open a new terminal as the same Windows
account and check the installation and delivered skills.

```powershell
basaltw info --json
basaltw doctor --json
basaltw doctor --skills --json
```

Use the [Windows account checks](windows-first-checks.md) to verify provider
sign-in, T3 Code, and your first project.
