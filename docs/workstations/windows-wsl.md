# Ubuntu WSL on Windows 11

Basaltwater can prepare a fully updated x86-64 Windows 11 desktop installation,
including a VM provisioned separately, for Ubuntu WSL builds and agent tools.
Run setup from the Windows account that will own the build worker and T3 Code
desktop. That account must have administrator rights for initial WSL
installation. Windows Server editions, ARM, and pre-login operation are
outside this release. Basaltwater does not provision VMs or configure the
hypervisor. Windows activation is not a setup prerequisite.

## Use a Proxmox Windows VM

Create and install a Windows 11 x86-64 VM in Proxmox, and configure its CPU to
expose virtualization extensions for nested virtualization. WSL 2 requires
nested virtualization when Windows runs in a VM; see Microsoft's
[WSL FAQ](https://learn.microsoft.com/en-us/windows/wsl/faq). Install Windows
updates, log in as the intended owner, then use the install steps below.
`-T3CodeDesktop` installs T3 Code natively on Windows; in T3 Code, connect to
the Ubuntu distribution under Settings > Connections as described below. This
is a supported setup path for a manually provisioned guest, but the guest's
creation and lifecycle remain outside Basaltwater setup.

## Install

Apply Windows Updates first, log in as the intended owner, and open PowerShell.
This command downloads the current `main` installer and pins its setup source to
one commit. It may ask for UAC and an Ubuntu password.

```powershell
$p = Join-Path $env:TEMP ("basaltwater-wsl-" + [guid]::NewGuid() + ".ps1")
Invoke-WebRequest "https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1" -UseBasicParsing -OutFile $p -ErrorAction Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Profile server_wsl -BuildServer -T3CodeDesktop -Node -Python -AgentTool gh,codex
```

From Command Prompt, the equivalent one-line entry point is:

```cmd
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:TEMP ('basaltwater-wsl-'+[guid]::NewGuid()+'.ps1'); Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' -UseBasicParsing -OutFile $p -ErrorAction Stop; & $p -Profile server_wsl -BuildServer -T3CodeDesktop -Node -Python -AgentTool gh,codex"
```

Use `-Plan` to check the intended options without installing. `-LinuxUser`,
`-DistroName`, and `-DistroLocation` select the Ubuntu account, official stable
Ubuntu WSL distribution, and absolute Windows drive directory for a new
distro. Without a distro name, setup selects the newest versioned stable Ubuntu
listed by WSL. Only an explicitly selected existing distro is reused; setup
preserves other distros.
Setup saves whether the selection was explicit and whether installation
successfully created the distro. If an automatically selected name is already
installed, setup stops before Linux preparation. Authorize reuse with the saved
installer's `-Resume -DistroName Ubuntu-XX.XX`, using the exact saved name.
Older saved states lack selection evidence and require the same explicit
confirmation for an existing distro. An interruption after WSL installation but
before ownership is saved also requires explicit confirmation; setup never
assumes ownership from an attempted installation.
Catalog selection handles WSL's null-padded output and excludes preview names.
Before running Linux preparation scripts, setup checks Ubuntu's system Python
and installs `python3` if missing. Python 3.10 or newer is required; an older
system Python stops preparation with remediation rather than upgrading Ubuntu.
Bootstrap probes run explicitly as root;
coding tools and builds still belong to the configured non-root Linux account.
Existing `wsl.conf` settings are retained, including files without a final newline.
Restart intent is saved before account/systemd preparation. After interruption,
resume restarts only the selected distro even if its configuration is already
unchanged; a live PID1 check also detects systemd settings not yet applied.
New accounts have root-protected preparation markers under
`/var/lib/basaltwater/wsl-accounts`. An interrupted password prompt is retried
before adding sudo membership or completing preparation. Existing accounts
without a pending managed marker retain their password and lock policy.
Setup syncs the state directory hierarchy and pending marker before creating
an account; a directory sync failure stops preparation before account changes.
`-Channel main|dev` or `-Version REF` pins the source revision. `-Go`, `-Node`,
`-Python`, and `-AgentTool gh,codex,claude,opencode` select Ubuntu tools.
`-T3CodeDesktop` installs the native Windows app with WinGet and does not create
a Basaltwater-managed T3 service in WSL. `-BuildServer` installs Git for
Windows and starts a WSL keepalive task at login.

If WSL needs a restart, setup prints an exact `-Resume` command. Restart
Windows, log in to the same account, and run it. The state and pinned source
are under `%LOCALAPPDATA%\Basaltwater`; credentials are never saved in setup
state. Reusing `-Resume` reapplies the pinned setup and updates Ubuntu packages.
WSL and Ubuntu release upgrades are deliberate maintenance; an existing
Ubuntu distribution is not silently replaced. The user may need to log in to
each selected agent after setup. In T3 Code, choose the Ubuntu distribution in
Settings > Connections.

Selected Codex, Claude Code, or OpenCode installations also receive the `basaltw`
helper and the `basaltwater-wsl-workspace`, `basaltwater-project-development`,
`basaltwater-frontend-design`, and `basaltwater-subagents` skills in the Ubuntu
account's catalog. Codex/OpenCode use `~/.agents/skills`; Claude uses
`~/.claude/skills`. These skills cover Linux workspaces, general project
workflow, frontend design, and bounded delegation without assuming Debian VM
services. T3 Code must connect to the intended distribution/account to discover
them; a native Windows provider uses a separate home. Use
`basaltw agent manifest /absolute/project --json` inside Ubuntu and check the
active agent's skill list. The manifest's non-CachyOS profile label is generic;
it does not establish WSL service readiness. Rerun installation from updated
pinned source to refresh skills; `Manage-Wsl.ps1 refresh` retains the existing
source revision. See [skill access](../agents/agent-skills.md#access-from-different-sessions).

## Run a native PowerShell job

Native jobs are submitted locally by the logged-in Windows owner. Prepare a
Git checkout on an NTFS volume, with the job script and its output path inside
that checkout. Commit the job script before running it: the runner checks its
exact 40-character commit revision and rejects untracked or modified scripts.
It uploads one artifact only after a successful job. The upload destination must
use HTTPS, accept bearer authentication in an HTTP PUT, and echo the received
SHA-256 digest in `X-Artifact-SHA256`. The receiver should verify that digest
before acknowledging it. The runner refuses URL credentials and redirects, and
requires a successful HTTP status as well as the matching digest.

First save the upload token using Windows user-bound DPAPI encryption:

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\wsl-setup.json" -Raw | ConvertFrom-Json
& (Join-Path $state.Source 'windows\Save-BasaltwaterToken.ps1')
```

Then run a job from the pinned installation source, using your own checkout,
script, and endpoint. For a first qualification, clone this repository and use
`windows\examples\Smoke-Artifact.ps1`. It produces `out\wsl-smoke.txt`; that
path must be absent when the job starts.

```powershell
$null = New-Item -ItemType Directory C:\work -Force
git clone https://github.com/bluehexagons/basaltwater.git C:\work\project
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\wsl-setup.json" -Raw | ConvertFrom-Json
$tools = Join-Path $state.Source 'windows'
$commit = (git -C C:\work\project rev-parse HEAD).Trim()
& "$tools\Invoke-BasaltwaterJob.ps1" -SourceDirectory C:\work\project -Revision $commit -Script windows\examples\Smoke-Artifact.ps1 -Artifact out\wsl-smoke.txt -UploadUri https://artifacts.example.test/upload -CredentialFile "$env:LOCALAPPDATA\Basaltwater\artifact-token.dpapi"
```

The runner writes a result receipt and separate logs under
`%LOCALAPPDATA%\Basaltwater\jobs`. Jobs have a one-hour timeout by default,
50 MiB combined log limit, and 1 GiB artifact limit. On timeout it kills the
PowerShell process tree; on failure or checksum mismatch it reports failure and
does not report an uploaded artifact. A job can be retried explicitly with a
fresh output path or clean checkout. Run Ubuntu builds in the selected distro
as the configured non-root Linux user; keep Linux workspaces under its native
filesystem rather than `/mnt/c`.

## Manage and recover

Run `windows\Manage-Wsl.ps1 -Action status|start|stop|refresh|logs` from the
pinned source directory. `refresh` updates WSL, Ubuntu packages, selected
tools, and selected WinGet packages without changing the pinned Basaltwater
source. `stop` terminates the selected distribution and interrupts its active
work. The optional login worker keeps WSL available after its terminal closes;
logout, sleep, reboot, or `wsl --shutdown` can still interrupt jobs. Use
`-Resume` after fixing an interrupted installation.
Selected user-managed Codex, Claude Code, and OpenCode tools run the shared
agent updater after installation and skill setup. Tools freshly installed in
that run skip the redundant update; executables owned by an external package
manager remain under that manager's control.

## Run manual Windows CI

The [Windows qualification workflow](../../.github/workflows/windows-manual.yml)
has only a manual `workflow_dispatch` trigger. From GitHub Actions, choose
**Windows qualification (manual)**, select the branch to test, then choose
`native` or `wsl`. The same choices are available from a checkout after the
workflow is present on the repository's default branch:

```bash
gh workflow run windows-manual.yml --ref main -f suite=native
gh workflow run windows-manual.yml --ref main -f suite=wsl
```

`native` uses a GitHub-hosted x64 Windows Server runner to parse the PowerShell
files and test installer planning and native job success, script changes,
timeout, log limits, and digest rejection. Upload is replaced by a local test
function; no artifact token or external receiver is needed. A hosted Server
runner cannot qualify the Windows 11 bootstrap or T3 desktop.
Installer recovery tests mock WSL entirely, including explicit distro reuse,
saved ownership, and interrupted systemd restarts. Python account recovery tests
mock account commands and use temporary directories. These checks do not qualify
real Windows/WSL interruption behavior.

`wsl` runs the native tests first, then uses a self-hosted runner labeled
`self-hosted`, `Windows`, `X64`, and `basaltwater-wsl`. Configure that runner on
an isolated, already prepared Windows 11 x86-64 machine. Start the runner
interactively as the same Windows account that owns the completed Ubuntu WSL
setup; WSL distributions are account-scoped. The WSL job checks Windows 11,
WSL, the Ubuntu distribution, systemd, the installer plan, the Ubuntu setup dry
run, and focused Python tests. Its `distribution` input can name the selected
distro explicitly; if omitted, the job uses the saved setup state. The job does
not install, update, restart, or shut down WSL.

The first release must be qualified on a Windows machine before declaring
Windows support production ready. Exercise fresh install and restart, WinGet
repair, a deliberate failing job, successful PowerShell artifact upload with a
digest-confirming receiver, upload failure, worker lifecycle, T3 desktop, and
an Ubuntu build. Record the exact Windows, WSL, Ubuntu, and WinGet versions.
