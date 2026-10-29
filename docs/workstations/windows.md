# Native Windows coding workstation

The experimental `agent_windows` installer profile prepares the current
x86-64 Windows 11 account for local coding with T3 Code. It installs missing
Git, GitHub CLI, Codex and a small native `basaltw` command, with T3 Code
desktop enabled by default. Provider logins, Git identity,
and T3 settings are preserved. Run as the
intended Windows account; package installers may ask for UAC. Allow at least
5 GiB of free space, plus space for your projects and their dependencies.

Windows follows CachyOS's existing-account workstation approach at a lower
development priority. Its primary role is compatibility testing, CI, and native
packaging; CachyOS remains the main workstation for developing new features.
Project-owned Windows build and test commands stay authoritative.

This is a tools-and-skills setup. Basaltwater's Linux controller,
managed web services, privilege broker, and remote setup profiles
are not installed on Windows. WSL is a separate opt-in path in the
[Ubuntu WSL guide](windows-wsl.md). Native Windows build-host services remain
[planned work](../plans/windows-support.md).

## Install

Open PowerShell as the intended account and paste this command.
It installs the standard tools above,
plus Node.js LTS and Python/uv:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1))) -Profile agent_windows -Node -Python
```

Append optional flags to the same command:

- `-Sunshine` adds the native Moonlight streaming host.
- `-GameDev -ShaderTools -Godot` adds native game tools, shader validation and Godot.
- `-StorageDrive F:` selects a mounted NTFS drive for development caches.
- `-AgentTool claude,opencode` adds providers.
- `-Plan` previews selections without applying setup.
- `-NoUpdateSource` reapplies the saved Basaltwater revision while still updating tools.
- `-NoRestartT3` skips the T3 Code start/restart after setup.

Rerun the same command to update an existing workstation. Setup automatically
retains saved selections, resolves current Basaltwater source and installs
available updates for selected tools, then starts or gracefully restarts T3 Code.
No resume, source-update or desktop-restart flag is needed. These opt-outs apply
to one invocation and do not change the next run's defaults.

The downloaded root installer relays through a temporary script in a child
Windows PowerShell process with execution-policy bypass. It preserves typed
flags, provider arrays and literal paths, reports failures and removes the
temporary script afterward. The account's saved execution policy is unchanged.
Package installers and selected machine-wide service configuration can ask for
UAC; account tooling and provider logins remain with the intended account.
CI runner registration and managed build-host setup use their existing flags.

## Review selections from a checkout

The same root installer can preview selections without downloading source,
installing packages, changing PATH or writing skills. An existing installation
uses its saved source and selections for the preview; a fresh one uses the checkout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Profile agent_windows -Node -Python -Plan
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
`-Go` selects Go. `-AgentTool claude,opencode` adds providers to GitHub CLI and
Codex. npm installations use the current account's `%APPDATA%\npm` prefix and
invoke `npm.cmd`, avoiding PowerShell's script-policy restriction on `npm.ps1`.
Tool discovery follows PATH order for `.exe` and `.cmd` commands, so an updated
npm shim can take precedence over an older desktop-bundled executable later
on PATH.
When calling a script directly inside PowerShell, `-T3CodeDesktop:$false`
disables the desktop selection. Windows PowerShell 5.1 cannot pass that switch
value through `powershell.exe -File`; use its `-Command` form for this opt-out.

Setup and refresh install available updates for selected tools using exact
WinGet identities, without a separate update-check command. npm providers are
updated through npm. Tools are rediscovered and version-checked afterward;
a failed update or broken tool stops setup. T3 Code is updated too, and its
installer may close the running desktop. If that interrupts a T3 terminal,
resume from a separate PowerShell window using the saved installation.

WinGet's catalog can lag T3's stable release feed. Setup and refresh use the
version available for `T3Tools.T3Code` in WinGet, without a desktop version pin.
Use `winget show --id T3Tools.T3Code --exact --source winget --versions` to see
the catalog versions. If T3 offers a newer stable release, wait for the catalog
or use T3's [built-in updater](https://github.com/pingdotgg/t3code/blob/main/docs/user/updating.md).
Reapplying setup cannot install a release that WinGet has not published yet.

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
itself opens normally. Successful desktop setup also registers
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

At completion, setup also reports Windows restart indicators from Windows Update,
component servicing and pending file replacements. An unreadable check produces
an unknown-status notice, and a detected restart is advisory; setup never reboots
Windows. These conservative checks do not establish that every possible restart
requirement is absent. See Microsoft's
[Windows Update restart property](https://learn.microsoft.com/en-us/windows/win32/api/wuapi/nf-wuapi-isysteminformation-get_rebootrequired)
and [pending file replacement limitation](https://learn.microsoft.com/en-us/windows/win32/msi/msisystemrebootpending).

For Antistatic builds, Windows tests and packaging, select `-GameDev`; add
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

Setup enables Git's user-level `core.longpaths` when that setting is absent.
An explicit existing value is preserved, including `false`; repository-specific
settings still take precedence. Without long-path support, Git can warn that a
directory name is too long and omit its contents even while returning success.
This setting covers Git's built-in commands; other Windows tools have their own
path limits, and setup does not change the system-wide long-path policy.

## Pinned source and recovery

The download command resolves `main` to one commit, stages its source under
`%LOCALAPPDATA%\Basaltwater\source`, and
saves the account, selected tools, and revision in `windows-setup.json`,
separately from WSL state. The download-only preview summarizes selections;
the checkout preview above also discovers installed tools and skill collisions.
Source extraction handles paths longer than 260 characters without changing
Windows policy and rejects archive paths that escape the staging directory.
Use `-Version REF` to select a published tag, branch, or commit supported by
the installer. Both `-Channel main` and `-Channel dev` currently resolve `main`;
an explicit `-Version` overrides the channel. A reference predating the native
profile cannot supply it.

Fresh installs and resumes share an account-level setup lock with WSL setup.
If another setup is running, wait for it to exit before retrying. `-Plan` remains
read-only and can run while that lock is held. An updated source is saved as
`staged` before execution; it becomes `complete` only after setup succeeds.

For interruption, a package requiring restart, or later reconciliation, rerun
the download command or use `basaltw refresh`. To retry the saved Basaltwater
revision without fetching newer source, run:

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw | ConvertFrom-Json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $state.Source 'install.ps1') -Profile agent_windows -NoUpdateSource
```

Native Windows reruns reuse saved tool selections and update source by default.
`-Resume` remains available to require an existing saved installation.
Explicit runtime, game-tool and desktop
switches update those selections; `-AgentTool` adds providers to the saved list.
For example, append `-Go -AgentTool claude` to add Go and Claude while
retaining existing providers. Add `-Plan` to preview the merged selections
without writes. Selected bundles continue to supply their dependencies;
selected host tools receive available updates and provider logins are retained.
The downloaded and checkout entry points share the same pinned-installer state.

## Native basaltw commands

Setup adds `%LOCALAPPDATA%\Basaltwater\bin` to the account PATH. Its `.cmd`
launcher works from PowerShell and Command Prompt without installing Python or
changing the account execution policy. Open a new terminal after first setup.

```powershell
basaltw --help
basaltw --version
basaltw info --json
basaltw doctor
basaltw doctor --json
basaltw refresh --dry-run
basaltw refresh
basaltw refresh --godot
basaltw refresh --sunshine
```

`refresh` resolves current `main` and reapplies the saved native selections,
including providers, game tools and storage. It installs available updates for
selected host tools and preserves provider logins.
`--dry-run` previews the currently pinned source without downloads or writes.
`--channel dev` is currently an alias for `main`; use `--version REF` for a
specific source reference.
`--no-update-source` reapplies the saved Basaltwater revision while still updating
selected tools. `--no-restart-t3` skips the completion start/restart but retains
login startup. Neither option is saved for subsequent runs. A source-update
opt-out cannot be combined with an explicit source version.
`--game-dev`, `--shader-tools`, `--godot`, `--sunshine`, `--node-versions` and
`--storage-drive F:` (or `--storage-root PATH`) can add saved selections;
run `basaltw refresh --help`. Storage selection requires a healthy mounted
NTFS volume. Invalid selections are rejected before changing saved setup.
Trailing directory separators are normalized before forwarding storage paths
through Windows PowerShell's native command boundary.
The CLI reports setup failures through its exit code, including restart needs.

`info` reports only the saved local native installation. Linux controller,
server, desktop and agent subcommands remain unavailable on Windows.
`refresh`/`info`/`doctor` require the pinned installation created by
`install.ps1 -Profile agent_windows`. Existing
PCs without the launcher can use the same current installer download with
`-Profile agent_windows`; existing selections are retained automatically.

Windows batch launchers expand literal `%NAME%` text in arguments, even when
called from PowerShell. For a storage path containing percent expressions,
invoke the PowerShell entry point directly so the path stays literal:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Basaltwater\bin\Invoke-Basaltw.ps1" refresh --storage-root 'F:\cache %NAME%' --dry-run
```

The direct installer/checkout scripts also accept literal paths through
`-StorageRoot`. For Godot project paths use its real console executable,
available as `BASALTWATER_GODOT_EXE` for Basaltwater's managed launcher.

## Verify this account

Allow the queued T3 start/restart to finish, then open a new terminal so providers
inherit PATH. If its completion status says `manual-restart-required`, close and
reopen T3 Code yourself.
Select the local Windows connection and open a native repository, for example
`C:\Users\NAME\repos\PROJECT`. T3 controls separately installed, authenticated
[provider CLIs](https://github.com/pingdotgg/t3code#installation).

```powershell
basaltw --version
basaltw info --json
git --version
gh --version
gh auth status
codex --version
codex login status
node --version
npm.cmd --version
uv --version
python --version
git config --get user.name
git config --get user.email
git config --get core.longpaths
```

Only check optional runtimes you selected. Sign in with `gh auth login` or
`codex login` if needed; Codex's [native Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
is configured through Codex, not by weakening provider approvals in setup.
Configure your preferred Git name/email if the final commands return nothing.
Login available through a temporary environment token in one session may not
be available in a fresh terminal. Setup does not collect or copy credentials.

`info` describes saved selections and the last installer phase; `complete`
does not establish current tool health. `doctor` checks that phase and runs
version probes for Git, GitHub CLI, Codex, and saved optional providers/runtimes,
including dependencies selected by the game bundle. It checks npm alongside
selected Node, identifies a failing Microsoft Store Python alias, resolves managed
Godot to its actual executable, and validates configured storage paths,
required directories and the six persisted cache/runtime variables. Storage
diagnostics do not repair missing directories or variables; rerun setup to repair them.
Selected Visual Studio/Windows SDK and T3 desktop checks establish discovery;
they do not compile code or launch the desktop. A separate `t3-login` check
verifies that the current account's task is enabled with its limited interactive
principal, account-specific logon trigger and expected hidden helper from the
saved source. Missing, disabled or changed tasks fail doctor; reapply desktop
setup to repair them. Intentionally disabling login startup also fails that check.

`doctor` runs no installers or sign-in commands and does not change saved
setup, PATH, skills or storage configuration. Each executable probe has a
30-second timeout; a hung probe's Windows process tree is terminated. It keeps
checking other tools after a failed version probe. `--json` returns a schema-1
report with `Healthy` and named `Checks` containing `State`, `Command` and
`Detail`; it exports no arbitrary saved-state fields or vendor stderr.
Exit status is zero when all selected host checks pass, or one for failed,
missing, timed-out or incomplete setup. Provider login, project dependencies,
builds, engine/export-template pins and GPU readiness need separate checks.

After updating the CLI, use `basaltw doctor`. To check a saved installation with
the current checkout's diagnostics before applying an update, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Test-AgentWorkstation.ps1 -Json
```

The script also accepts `-TimeoutSeconds 10` to change the per-probe bound.
It requires the account's saved native installation, including when optional
selections are absent in an older state. Checkout-only setup has no saved
selection to diagnose. `refresh --dry-run` checks discovery, storage paths and
managed skill collisions without running version probes. Use the
[game tooling examples](windows-game-development.md#antistatic-qualification)
for Antistatic's Node selection, native bootstrap and Windows test gate.

Python's Microsoft Store execution alias can exist before Python is installed.
Use the project's uv environment, or disable that alias in Windows App execution
aliases if it shadows the managed interpreter in a fresh terminal. Do not
consider `Get-Command python` alone proof of a usable interpreter.

Python packages that fall back to a native build require the project's Windows
build prerequisites. The basic profile supplies Python/uv; the optional
`-GameDev` bundle supplies MSVC and the Windows SDK. Honor the project's Python version
pin and dependency choices when qualifying Windows builds.

Codex/OpenCode receive skills under `%USERPROFILE%\.agents\skills`; Claude
uses `%USERPROFILE%\.claude\skills`. Setup installs Windows workspace, project
development, frontend design, subagent, and T3 preview guidance, including their
supporting files. It records file hashes, refreshes unchanged managed files,
and stops before overwriting personal edits. Each managed file is copied and
hash-checked in a temporary sibling before replacement, so an interrupted copy
can be retried without mistaking partial contents for a personal edit.
Generated Python caches are excluded when applying a checkout.
Junctions/symlinks in a skill path
are refused. No browser payload is installed, and skills do not grant tools
to sessions that do not expose them.

The installer and repository catalog check share the
[Windows skill catalog](../../windows/agent-skills.json), keeping the delivered
bundle selection aligned with the complete shipped skill collection.

## Validation boundary

Use the [local qualification procedure](windows-qualification.md) and its report
template to retain host, workload and operator-reported lifecycle evidence.
Windows qualification can run entirely on the workstation.

Run the offline native setup tests without changing this account's tools:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-AgentWorkstationTests.ps1
```

These tests use mocked package/provider commands, desktop completion, reboot
signals and temporary catalogs. They verify completion follows a successful
receipt, that hidden login tasks stay within the current account, and that
graceful close failures do not force termination or launch a duplicate desktop.
Sunshine tests mock package and service operations; they do not install it.
The [manual Windows workflow](../../.github/workflows/windows-manual.yml) also
runs them with the existing native/WSL bootstrap tests and Python output-capture
checks. A real plan on an existing Windows 11 machine verifies discovery, not
installation on a fresh PC.
Fresh WinGet repair, UAC, package downloads, reboot recovery, provider login,
Task Scheduler independence when refreshing inside T3, graceful desktop relaunch,
and a new T3 coding session still require live qualification before declaring
this experimental profile production ready.

The complete native offline suite requires Python 3.11+ on PATH and also runs
directly on a workstation. It uses the local temporary directory when GitHub
Actions' `RUNNER_TEMP` is absent:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1
```
