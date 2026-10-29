# Windows desktop and development integration

The native `agent_windows` setup installs stable PowerShell 7 when missing and
continues the pinned installation in it before applying development tools.
Windows PowerShell 5.1 remains the initial bootstrap and the bounded UI Automation
worker. Setup preserves switches, arrays and literal paths across the handoff.
`-Plan` neither installs PowerShell nor starts a replacement shell.

The same one-command installer and saved `basaltw refresh` selections supply
these features. No separate desktop installer is required. See
[Windows setup](windows.md) for installation and recovery.

## Terminal and artifact handoff

Setup adds a **Basaltwater** Windows Terminal profile and a **Basaltwater
Development** Start Menu shortcut. These open PowerShell 7 with current account
PATH settings. The Terminal profile is a managed JSON fragment; setup does not
replace personal Terminal settings or select a new default profile. Reapplying
setup refreshes the Basaltwater fragment and shortcut.
MSIX installations use the verified stable app execution alias so the profile
and shortcut continue working when PowerShell's versioned package is replaced.
The batch CLI and bounded Windows PowerShell worker rebuild their own module
paths, avoiding incompatible PowerShell 7 modules inherited through intermediaries;
see [Microsoft's module-path guidance](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath).

`$PSVersionTable.PSVersion` reports the invoking shell. The prepared development
shell and supervised task guardians use PowerShell 7 (`pwsh.exe`). The public
`basaltw.cmd` launcher uses Windows PowerShell 5.1 (`powershell.exe`) for its
compatibility entry point, and desktop observation/control uses a separate,
hidden worker bounded to eight seconds. Doctor's `worker: WindowsPowerShell-5.1`
identifies that UI Automation backend, which loads Windows' installed .NET
Framework assemblies; it does not mean project commands run in PowerShell 5.1.
Invoke `basaltw` normally from PowerShell 7. Child module-path isolation preserves
the caller's `PSModulePath`; no global module-path changes or shell downgrade are
needed. Keep internal worker functions behind the public CLI. Use
`pwsh.exe -NoProfile -File SCRIPT.ps1` for project scripts that need PowerShell 7.

```powershell
basaltw desktop open 'F:\builds\package.zip' --reveal
basaltw desktop open 'F:\reports\result.html'
basaltw desktop terminal --project 'F:\Repos\game' --msvc
basaltw desktop notify --title 'Build finished' --message 'The package is ready.'
```

`open` uses the file's Windows association; `--reveal` selects it in Explorer.
`terminal --msvc` activates the supported x64 Visual Studio environment inside
that terminal. It does not change global PATH, project runtimes or compiler pins.
Notification delivery depends on the Windows session and notification settings;
task receipts/logs remain authoritative.

## Observe a native application

```powershell
basaltw desktop doctor
basaltw desktop status
basaltw desktop windows
basaltw desktop inspect --pid 1234
```

These are native Windows commands. Doctor tests interactive desktop/API access,
token logon identity and readable control state without screenshots, input or
application launch. It uses the token's AuthenticationId plus Windows boot and
session identity; it does not assume a logon SID appears in the .NET group list.
See [Microsoft's token statistics](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-token_statistics).
Status also reports human
control state. Window discovery returns current-account/session windows with
decimal handles, identities, physical geometry and monitor device IDs. It can
omit inaccessible windows. UI Automation inspection returns bounded controls
and references; some custom or embedded controls expose limited information.

For an explicit capture, substitute a freshly observed window identity and
generation and choose a new PNG path in an existing directory:

```powershell
basaltw desktop screenshot --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --output 'F:\reports\window.png'
```

Alternatively supply an explicit `--monitor DEVICE` instead of the window
selectors. Capture does not raise the target. Window capture uses PrintWindow;
hardware-rendered applications may return incomplete or blank content. A PNG
file establishes capture completion, not visual correctness. Existing files
are never overwritten. Monitor captures can include other visible applications.

## Act on observed controls

```powershell
basaltw desktop element set-text --ref OBSERVED_REF --generation OBSERVED_GENERATION --text 'Example'
basaltw desktop window resize --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --width 1000 --height 700
basaltw desktop input key --window 123456 --identity 'OBSERVED_IDENTITY' --generation OBSERVED_GENERATION --width 1000 --height 700 --key ENTER
```

Observe again after mutations or an uncertain result. Window and element
references expire after 60 seconds and are invalidated before mutations.
Window actions verify their observed outcome; `invoke` and raw input explicitly
require application-specific verification. Raw input requires the window to
remain foreground with the observed geometry. Click coordinates are relative
to that window; multi-monitor origins and DPI scaling are handled by the worker.
The first input slice supports individual keys, Unicode text and clicks, without
key chords or held gamepad/key input. It does not bypass UAC or a locked desktop.

One controller operates at a time. Human control state persists for this account:

```powershell
basaltw desktop control human-override --human-approved --seconds 900
basaltw desktop control disabled --human-approved
basaltw desktop control enabled --human-approved
```

Only an explicit human decision authorizes a control-state transition. Agents
can perform requested desktop work while enabled without another approval or
lease. Pause blocks input, window actions and artifact/terminal handoffs while
leaving observation available. The optional override timer restores enabled
control when it expires. Secure/alternate desktops and other accounts/sessions
are outside the adapter. GUI automation follows the active browser-tool policy;
this backend does not grant permission to substitute another browser.

## Supervised project tasks

```powershell
basaltw desktop develop doctor --project 'F:\Repos\game'
basaltw desktop develop node build --project 'F:\Repos\game' --dry-run
basaltw desktop develop node build --project 'F:\Repos\game'
basaltw desktop develop exec --project 'F:\Repos\native' --msvc -- cl.exe /c main.cpp
basaltw desktop develop editor --project 'F:\Repos\godot-game'
basaltw desktop develop run --project 'F:\Repos\godot-game'
basaltw desktop develop import --project 'F:\Repos\godot-game'
basaltw desktop develop status TASK_ID
basaltw desktop develop logs TASK_ID
basaltw desktop develop cancel TASK_ID
```

Tasks return IDs and run through limited interactive Scheduled Task guardians,
independently of the invoking T3 terminal. Project code starts only after its
process has entered an owned Windows Job Object. Basaltwater helper shells use
process-local execution-policy bypass without changing account or machine policy.
Cancellation and timeout settle
the process tree before a terminal receipt is published. Interrupted or uncertain
cleanup blocks setup/reboot; inspect the task rather than blindly replaying it.
Failed dispatch never replaces a running or completed guardian receipt.
`develop status` exits nonzero for failure, timeout, cancellation, interrupted
work or uncertain cleanup; queued/running/complete queries exit zero.

Cancellation retires an unstarted task under the setup and guardian locks,
removes its registration and private environment, and prevents late execution.
A running guardian receives a cancellation request; confirm its terminal status.
If that guardian is gone during the same boot, inspect the task and reboot
Windows to establish cleanup. After a real boot, cancellation or setup retires
abandoned work as `interrupted` without replaying it. Scheduler or environment
cleanup failures remain visible and block maintenance.

`exec` accepts a real executable followed by literal arguments after `--`.
For a script, use `pwsh.exe -File script.ps1`; batch files require an explicit
wrapper. `--env NAME=VALUE` applies to the task, and `--msvc` activates Visual
Studio only in its child. Environment snapshots are private and removed after
confirmed cleanup. No artifact-upload credentials are needed. Tasks temporarily
inhibit idle sleep while active and release that request afterward; they do not
change the power plan or disable locking.

`node SCRIPT` requires a numeric `.node-version`/`.nvmrc` or explicit
`--runtime VERSION` and an already installed fnm runtime. It runs that runtime's
npm and rejects incompatible `packageManager` pins. Other package managers need
an explicitly provisioned executable through `exec`; system Node is not silently
substituted. Godot honors `GODOT_BIN`, then the managed engine selection. Engine
pins, export templates and renderer requirements remain project-owned.

Default task timeout is one hour; `--timeout SECONDS` accepts 1–86400. Logs retain
up to 4 MiB per stream; `logs` returns the last 64 KiB. Receipts and logs live in
`%LOCALAPPDATA%\Basaltwater\development`. Headless commands need no input control;
GUI interactions remain subject to desktop handoff and readiness rules.

## Maintenance and qualification

`basaltw refresh` returns a detached refresh ID by default, allowing T3 upgrades
to close/restart the desktop without killing setup. Use `refresh --status ID`
and `refresh --logs ID` for progress; `--wait` runs in the invoking terminal.
`refresh --cancel ID` can cancel a queued job before setup starts; an applying
package installer must finish before another attempt.
Status reports `cleanup-uncertain` when a running job loses its worker in the
current boot. An installer may have survived, so inspect its logs and reboot
before another detached refresh. After reboot, the next refresh marks abandoned
queued/running jobs `interrupted` while holding their claim and setup locks.
It starts a new job without replaying the abandoned one; `--status` returns
nonzero for failed, interrupted or uncertain jobs.
`--dry-run` remains read-only and foreground. Refresh logs are capped at 16 MiB.
Active or uncertain development tasks block setup/reboot; wait for them or cancel
and confirm cleanup. Explicit installer, Windows Update or component servicing
requirements queue a visible desktop dialog showing the reason and a 60-second
countdown with Restart now and Later buttons. Pending file operations alone do
not trigger reboot. `--no-reboot` defers reboot for this run; see
[restart conditions and status](windows.md) for details.

The workstation doctor checks stable PowerShell 7 and, when T3 is selected,
desktop readiness. Neither installed GPU drivers, an accessible desktop nor a
healthy Sunshine service proves rendering, audio, encoder or streaming readiness.
Qualify actual Moonlight video/audio/input, display/scaling changes, lock and
reconnect behavior using the [Sunshine guide](windows-sunshine.md).
Use the [Windows qualification procedure](windows-qualification.md) for recorded
workload evidence. Offline contracts and owned-process tests do not establish
unattended GUI access, UAC automation or hardware-rendered capture support.
