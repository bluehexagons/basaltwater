# Install or refresh the Windows profile

[Topic index](windows.md)

## Install

Open PowerShell as the intended account. Allow at least 5 GiB free for the
tools, plus space for projects and dependencies. This invocation adds Node.js
LTS and Python/uv to the [default tool set](windows.md).

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1))) -Profile agent_windows -AgentTool codex -Node -Python
```

Append optional flags to the same command:

- `-Sunshine` adds the native Moonlight streaming host.
- `-GameDev -ShaderTools -Godot` adds native game tools, shader validation and Godot.
- `-StorageDrive F:` selects a mounted NTFS drive for development caches.
- `-AgentTool claude,opencode` adds providers.
- `-Plan` previews selections without applying setup.
- `-NoUpdateSource` reapplies the saved Basaltwater revision while still updating tools.
- `-NoRestartT3` skips the T3 Code start/restart after setup.
- `-NoReboot` reports required Windows reboots without restarting Windows.

Rerun the same command to update an existing workstation. Setup automatically
retains saved selections, resolves current Basaltwater source and installs
available updates for selected tools, then starts or gracefully restarts T3 Code.
When Windows requires a reboot, setup queues it automatically instead of
restarting T3 immediately; desktop startup uses the next account login.
No resume, source-update or desktop-restart flag is needed. These opt-outs apply
to one invocation and do not change the next run's defaults.

After source staging, native setup installs stable PowerShell 7 if missing and
hands off to it before applying the development tools. It preserves saved
selections and literal arguments and releases the setup lock before re-entry.
The PowerShell 5.1 bootstrap remains usable on a fresh Windows installation.
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
