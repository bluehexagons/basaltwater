# Preview and change tool selections

[Topic index](windows.md)

## Review selections from a checkout

The same root installer can preview selections without downloading source,
installing packages, changing PATH or writing skills. An existing installation
uses its saved source and selections for the preview; a fresh one uses the checkout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Profile agent_windows -AgentTool codex -Node -Python -Plan
```

Omitting `-Plan` follows the normal pinned-source installation flow. Use
`-Version REF` for a reviewed published revision. Setup always saves selections
through this root installer; feature-specific installation wrappers are unnecessary.

`-Node` selects Node.js LTS. Selected npm-based providers also select Node
automatically. `-Python` installs uv and reuses its installed managed Python,
or installs the current stable Python when none is managed. It ignores the
invoking project's version files/configuration and temporarily clears
`UV_PYTHON` for host selection, restoring that process value even on failure.
It does not replace another installation. Select project Python pins separately.
`-Go` selects Go. No coding provider is installed by default; new setup requires
`-AgentTool codex`, `-AgentTool claude`, or `-AgentTool opencode` (comma-separated
combinations are supported). GitHub CLI remains a utility default.
npm installations use the current account's `%APPDATA%\npm` prefix and
invoke `npm.cmd`, avoiding PowerShell's script-policy restriction on `npm.ps1`.
Tool discovery follows PATH order for `.exe` and `.cmd` commands, so an updated
npm shim can take precedence over an older desktop-bundled executable later
on PATH.
When calling a script directly inside PowerShell, `-T3CodeDesktop:$false`
disables the desktop selection. Windows PowerShell 5.1 cannot pass that switch
value through `powershell.exe -File`; use its `-Command` form for this opt-out.

Setup and refresh start package maintenance with `winget upgrade --all`.
The bulk pass covers applicable installed apps from enabled implicit sources,
including the Microsoft Store when configured. It honors WinGet pins and skips
packages with unknown installed versions. Windows OS updates are handled through
Windows Update. Setup then reconciles selected tools by exact WinGet identity and updates
npm providers through npm. Tools are rediscovered and version-checked afterward.
A failed upgrade or broken tool stops setup. For a bulk upgrade failure, review
WinGet's per-package output for the reported error or restart requirement.
T3 Code is updated too, and its
installer may close the running desktop. If that interrupts a T3 terminal,
the default detached refresh continues independently. For a foreground installer,
resume from a separate PowerShell window using the saved installation.

WinGet's catalog can lag T3's stable release feed. Setup and refresh use the
version available for `T3Tools.T3Code` in WinGet, without a desktop version pin.
Use `winget show --id T3Tools.T3Code --exact --source winget --versions` to see
the catalog versions. If T3 offers a newer stable release, wait for the catalog
or use T3's [built-in updater](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md).
Reapplying setup cannot install a release that WinGet has not published yet.

Compare the version in T3's About screen with the
[shared T3 integration baseline](../agents/t3-qualification.md#stable-feature-baseline).
`basaltw refresh` updates the [client workflows](../../common/agent_skills/basaltwater-project-development/references/t3-workflows.md)
in the project-development skill, as well as available packages. Verify an
authenticated provider turn and native terminal in the local Windows
connection. See the [platform reference](../../common/agent_skills/basaltwater-project-development/references/t3-platforms.md#windows-background-service)
for upstream background service ownership.

The desktop supplies a `t3` command for managed-policy diagnostics. Check
`Get-Command t3` and `t3 --version` in a new terminal before running
`t3 policy show`. If the command is unavailable, use
**Settings → About → t3 command → Install** and reopen the terminal. The app
preserves an existing `t3` command. Read the [platform boundaries](../../common/agent_skills/basaltwater-project-development/references/t3-platforms.md)
for command selection and policy behavior. Windows managed policy disables
desktop-managed WSL environments; successful WSL installation cannot restore
that connection. Preserve policy and request administrator review.

Successful setup or refresh starts the selected T3 Code desktop if stopped,
or requests a graceful close and relaunch to load the updated PATH and skills.
This runs as a separate, limited current-user interactive Scheduled Task after
the successful setup receipt and setup lock release, so refresh can finish from
a local or remote T3 terminal before its desktop closes. The task operates in
its Windows login session and never forcibly terminates T3. Multiple instances,
a blocked close, unavailable Task Scheduler, or an account without an interactive
login require a manual close/reopen. A task assigned to a different Windows
login session also requires a manual restart; it does not touch that session's
desktop. Completion status is printed as a file
under `%LOCALAPPDATA%\Basaltwater\t3-completion`; it contains no provider arguments
or credentials. An app already relaunched after the completion request is kept.

Use `-NoRestartT3` (or `basaltw refresh --no-restart-t3`) to skip this step.
Login startup is still configured. A vendor installer may itself close or
relaunch T3 during an upgrade, independently of setup's completion step.

The PowerShell helpers for setup completion and login run hidden; the T3 desktop
itself opens normally without allocating a separate output console.
Successful desktop setup also registers
`Basaltwater-T3-Login-<account SID>`, a limited interactive task triggered only
by that account's login. It starts a stopped desktop and leaves an existing
instance alone. Quitting T3 keeps it stopped until the next login or setup;
there is no restart loop. You can disable the task in Task Scheduler to pause
login startup; applying desktop setup again enables it. No password or automatic
Windows login is configured.

Optional native desktop streaming is available through the
[Windows Sunshine option](windows-sunshine.md). Append `-Sunshine` to setup or
use `basaltw refresh --sunshine`; it uses the vendor service and is saved for
subsequent refreshes. `basaltw doctor` checks the selected service without
starting it. Pairing and streaming qualification remain manual.

Native setup automatically reboots only when a package installer explicitly
returns a restart-required code (1641, 3010 or WinGet's corresponding mapped
codes), or Windows Update/component servicing reports a required reboot.
Pending file renames/deletions alone are advisory: installers and other apps can
queue routine cleanup for the next natural boot. This queue does not establish
that the development system needs to restart, and setup leaves it untouched.
See Microsoft's [delayed file operation reference](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw).

A requirement queues a hidden, limited current-account worker. After setup releases
its lock, a visible desktop dialog explains the reason and restart time, counts
down 60 seconds, and offers **Restart now** and **Later**. Closing the dialog or
pressing Escape also defers the restart. The warning appears even for detached
refresh. A locked, disconnected
or secure desktop, a notification failure, or a suspended countdown defers reboot
instead of restarting unseen. Setup rechecks Windows' requirement before and
after the countdown; a requirement that has cleared does not reboot the PC.
An explicit installer requirement remains authoritative until Windows boots.
Windows can ask you to close apps or block the restart; setup never forces apps
closed. The delay runs in the helper because a positive `shutdown.exe` timeout
implicitly forces closure; see Microsoft's [shutdown reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown/).
Unreadable indicators alone produce a notice and do not trigger a reboot.
These checks do not establish that every possible restart requirement is absent.
See Microsoft's
[Windows Update restart property](https://learn.microsoft.com/en-us/windows/win32/api/wuapi/nf-wuapi-isysteminformation-get_rebootrequired)
and [pending file replacement limitation](https://learn.microsoft.com/en-us/windows/win32/msi/msisystemrebootpending).

An installer that requires reboot before setup can finish leaves the saved phase
as `restart-required`. Setup queues the reboot and a one-time, hidden continuation
for this account's next login after an actual Windows boot. Logging off and back
on without rebooting does not consume it. Recovery reapplies the same pinned
source and saved selections, preserving the T3 restart opt-out. It disables
further automatic reboots for that continuation to prevent a restart loop;
if another reboot is required, restart and rerun setup deliberately. No password
or automatic Windows login is configured. Other installer failures stop setup
without queuing a reboot.

Use `-NoReboot` or `basaltw refresh --no-reboot` to defer a reboot for one run.
T3 still starts/restarts unless separately disabled. Restart/recovery status is
saved in `%LOCALAPPDATA%\Basaltwater\windows-restart.json`, including the triggering
reasons (package identity and exit code for installer requirements), queued/update
times and countdown deadline. `queued` means the worker is waiting for setup;
`countdown` means the desktop warning is active; `restart-deferred` means Later,
dialog closure or an unavailable desktop prevented reboot; `no-longer-required`
means the requirement cleared. `restart-requested` records a submitted OS request,
not proof that Windows finished rebooting. The OS shutdown event also retains
the reason after later setup runs replace the request. Inspect the request with:

```powershell
Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-restart.json" -Raw | ConvertFrom-Json
```

Choosing Later keeps the interrupted setup's one-time login continuation for a
later manual reboot. If setup was already complete, T3 login startup runs at the
next login; reopen T3 manually if you defer that reboot. Scheduler or shutdown
failures require a manual restart/retry. A later setup run clears prior queued
restart work before applying changes. The WSL profile retains its existing
manual Windows-reboot flow.

For native game builds, Windows tests and packaging, select `-GameDev`; add
`-ShaderTools` for shader validation. `-NodeVersions` independently prepares
fnm for project-selected Node versions. Optional `-StorageDrive F:` uses
`F:\Basaltwater` for runtime versions, development caches and new checkouts
on a mounted NTFS volume. Use `-StorageRoot PATH` for a custom directory;
choose one of these options. The resolved root is saved for refresh/resume.
See [Windows game tooling](windows-game-development.md#storage) for split-storage
setup commands, the layout and validation. See the same guide for the bundle,
copy-and-paste setup, storage limits and project validation commands.
`-Godot` independently installs the standard Godot editor/CLI; existing engines
are reused and checked with `--headless --version`. Setup waits for console or
GUI executables to exit successfully, with a 30-second timeout. Export templates,
.NET/C# and mobile SDKs remain project requirements.

Git for Windows and its bundled Bash are required for every native profile.
Setup exposes the `git-bash` desktop launcher on the account PATH. For scripts,
use that installation's `bin\bash.exe`; a bare `bash.exe` can resolve to WSL's
launcher. Doctor probes the bundled Bash explicitly with `--version`. Setup
keeps Git's Unix utilities out of the Windows PATH and preserves shell profiles.

Setup enables Git's user-level `core.longpaths` when that setting is absent.
An explicit existing value is preserved, including `false`; repository-specific
settings still take precedence. Without long-path support, Git can warn that a
directory name is too long and omit its contents even while returning success.
This setting covers Git's built-in commands; other Windows tools have their own
path limits, and setup does not change the system-wide long-path policy.
