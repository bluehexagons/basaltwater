# Native Windows coding workstation

The experimental `agent_windows` installer profile prepares the current
x86-64 Windows 11 account for local coding with T3 Code. It installs missing
Git, GitHub CLI, Codex and a small native `basaltw` command, with T3 Code
desktop enabled by default. Existing tools, provider logins, Git identity,
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

Open PowerShell as the intended account and paste the complete block.
It installs the standard tools above,
plus Node.js LTS and Python/uv:

```powershell
& {
    $installer = Join-Path $env:TEMP ("basaltwater-" + [guid]::NewGuid() + ".ps1")
    try {
        Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' -UseBasicParsing -OutFile $installer -TimeoutSec 120 -ErrorAction Stop
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Profile agent_windows -Node -Python
        if ($LASTEXITCODE -ne 0) { throw "Windows workstation setup failed (exit code $LASTEXITCODE)." }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}
```

Add `-Plan` after `-Python` to preview selections. The download must succeed
before execution; the block reports installer failures and removes its temporary
script on exit. The child PowerShell process uses execution-policy bypass
without changing the account's saved policy. Package installers may ask for UAC.
CI runner registration and managed build-host setup are separate operations.

To apply a reviewed checkout, use the local command below.

## Review and apply the current checkout

From the repository root, preview without downloading source, installing
packages, changing PATH, or writing skills:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Setup-AgentWorkstation.ps1 -Node -Python -Plan
```

Apply the reviewed checkout by omitting `-Plan`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\Setup-AgentWorkstation.ps1 -Node -Python
```

`-Node` selects Node.js LTS. Missing npm-based providers also select Node
automatically. `-Python` installs uv and reuses its installed managed Python,
or installs the current stable Python when none is managed. It ignores the
invoking project's version files/configuration and temporarily clears
`UV_PYTHON` for host selection, restoring that process value even on failure.
It does not replace another installation. Select project Python pins separately.
`-Go` selects Go. `-AgentTool claude,opencode` adds providers to GitHub CLI and
Codex. npm installations use the current account's `%APPDATA%\npm` prefix and
invoke `npm.cmd`, avoiding PowerShell's script-policy restriction on `npm.ps1`.
When calling a script directly inside PowerShell, `-T3CodeDesktop:$false`
disables the desktop selection. Windows PowerShell 5.1 cannot pass that switch
value through `powershell.exe -File`; use its `-Command` form for this opt-out.

Detected tools are version-checked during application. A broken existing tool
stops setup with its failure; repair it and rerun. Missing packages use exact
WinGet identities and preserve installed versions with `--no-upgrade`. Existing
T3 desktop installations are detected without launching or stopping the app.
Updates remain deliberate through the tool's updater or package manager.

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

For interruption, a package requiring restart, or later reconciliation, run
the saved source's installer with `-Profile agent_windows -Resume`:

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw | ConvertFrom-Json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $state.Source 'install.ps1') -Profile agent_windows -Resume
```

Add `-UpdateSource` to deliberately resolve and save a newer source revision.
Resume reuses saved tool selections. Explicit runtime, game-tool and desktop
switches update those selections; `-AgentTool` adds providers to the saved list.
For example, use `-Resume -Go -AgentTool claude` to add Go and Claude while
retaining existing providers. Add `-Plan` to preview the merged selections
without writes. Selected bundles continue to supply their dependencies;
existing installed tools and provider logins are retained.
The checkout command applies its explicit options directly and does not create
or update pinned-installer state.

## Native basaltw commands

Setup adds `%LOCALAPPDATA%\Basaltwater\bin` to the account PATH. Its `.cmd`
launcher works from PowerShell and Command Prompt without installing Python or
changing the account execution policy. Open a new terminal after first setup.

```powershell
basaltw --help
basaltw --version
basaltw info --json
basaltw refresh --dry-run
basaltw refresh
basaltw refresh --godot
```

`refresh` resolves current `main` and reapplies the saved native selections,
including providers, game tools and storage. It preserves installed package
versions and provider logins; use vendor/package updaters for tool upgrades.
`--dry-run` previews the currently pinned source without downloads or writes.
`--channel dev` is currently an alias for `main`; use `--version REF` for a
specific source reference.
`--game-dev`, `--shader-tools`, `--godot`, `--node-versions` and
`--storage-drive F:` (or `--storage-root PATH`) can add saved selections;
run `basaltw refresh --help`. Storage selection requires a healthy mounted
NTFS volume. Invalid selections are rejected before changing saved setup.
Trailing directory separators are normalized before forwarding storage paths
through Windows PowerShell's native command boundary.
The CLI reports setup failures through its exit code, including restart needs.

`info` reports only the saved local native installation. Linux controller,
server, desktop and agent subcommands remain unavailable on Windows. A
checkout-only setup installs the launcher, but `refresh`/`info` require the
pinned installation created by `install.ps1 -Profile agent_windows`. Existing
PCs without the launcher must first use a current installer download with
`-Profile agent_windows -Resume -UpdateSource`.

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

Restart T3 Code and open a new terminal after setup so providers inherit PATH.
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
does not establish current tool health. `refresh --dry-run` checks discovery,
storage paths and managed skill collisions, without running every tool's
version probe. Run the relevant version commands above to check executable
health. Neither command installs a project's dependencies or validates its
build. Use the [game tooling examples](windows-game-development.md#antistatic-qualification)
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

Run the offline native setup tests without changing this account's tools:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-AgentWorkstationTests.ps1
```

These tests use mocked package/provider commands and temporary catalogs.
The [manual Windows workflow](../../.github/workflows/windows-manual.yml) also
runs them with the existing native/WSL bootstrap tests and Python output-capture
checks. A real plan on an existing Windows 11 machine verifies discovery, not
installation on a fresh PC.
Fresh WinGet repair, UAC, package downloads, reboot recovery, provider login,
and a new T3 coding session still require live qualification before declaring
this experimental profile production ready.

The complete native offline suite requires Python 3.11+ on PATH and also runs
directly on a workstation. It uses the local temporary directory when GitHub
Actions' `RUNNER_TEMP` is absent:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows\tests\Run-NativeTests.ps1
```
