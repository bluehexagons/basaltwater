# Native Windows coding workstation

The experimental `agent_windows` installer profile prepares the current
x86-64 Windows 11 account for local coding with T3 Code. It installs missing
Git, GitHub CLI, and Codex, with T3 Code desktop enabled by default. Existing
tools, provider logins, Git identity, and T3 settings are preserved. Run as the
intended Windows account; package installers may ask for UAC. Allow at least
5 GiB of free space, plus space for your projects and their dependencies.

Windows follows CachyOS's existing-account workstation approach at a lower
development priority. Its primary role is compatibility testing, CI, and native
packaging; CachyOS remains the main workstation for developing new features.
Project-owned Windows build and test commands stay authoritative.

This is a tools-and-skills setup. Basaltwater's Linux controller, `basaltw`
launcher, managed web services, privilege broker, and remote setup profiles
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
automatically. `-Python` installs uv and uses `uv python install --default`
to provide a current managed Python; it does not replace another installation.
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
the installer. A reference predating the native profile cannot supply it.

For interruption, a package requiring restart, or later reconciliation, run
the saved source's installer with `-Profile agent_windows -Resume`:

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\windows-setup.json" -Raw | ConvertFrom-Json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $state.Source 'install.ps1') -Profile agent_windows -Resume
```

Add `-UpdateSource` to deliberately resolve and save a newer source revision.
Resume reuses saved tool selections; add `-Plan` to preview them without writes.
The checkout command applies its explicit options directly and does not create
or update pinned-installer state.

## Verify this account

Restart T3 Code and open a new terminal after setup so providers inherit PATH.
Select the local Windows connection and open a native repository, for example
`C:\Users\NAME\repos\PROJECT`. T3 controls separately installed, authenticated
[provider CLIs](https://github.com/pingdotgg/t3code#installation).

```powershell
git --version
gh --version
gh auth status
codex --version
codex login status
node --version
npm.cmd --version
uv --version
uv run --no-project python --version
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

Python's Microsoft Store execution alias can exist before Python is installed.
Use the project's uv environment, or disable that alias in Windows App execution
aliases if it shadows the managed interpreter in a fresh terminal. Do not
consider `Get-Command python` alone proof of a usable interpreter.

Python packages that fall back to a native build require the project's Windows
build prerequisites. The basic profile supplies Python/uv; MSVC and the Windows
SDK remain project-specific requirements. Honor the project's Python version
pin and dependency choices when qualifying Windows builds.

Codex/OpenCode receive skills under `%USERPROFILE%\.agents\skills`; Claude
uses `%USERPROFILE%\.claude\skills`. Setup installs Windows workspace, project
development, frontend design, subagent, and T3 preview guidance, including their
supporting files. It records file hashes, refreshes unchanged managed files,
and stops before overwriting personal edits. Generated Python caches are excluded
when applying a checkout. Junctions/symlinks in a skill path
are refused. No browser payload is installed, and skills do not grant tools
to sessions that do not expose them.

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
