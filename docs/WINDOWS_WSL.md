# Ubuntu WSL on Windows 11

Basaltwater can prepare a fully updated x86-64 Windows 11 desktop installation
for Ubuntu WSL builds and agent tools. Run setup from the Windows account that
will own the build worker and T3 Code desktop. That account must have
administrator rights for initial WSL installation. Windows Server editions,
ARM, pre-login operation, and VM provisioning are outside this release.
Windows activation is not a setup prerequisite.

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
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path $env:TEMP ('basaltwater-wsl-'+[guid]::NewGuid()+'.ps1'); Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' -UseBasicParsing -OutFile $p -ErrorAction Stop; & $p -Profile server_wsl -BuildServer -Node -Python"
```

Use `-Plan` to check the intended options without installing. `-LinuxUser`,
`-DistroName`, and `-DistroLocation` select the Ubuntu account, official WSL
distribution, and absolute Windows installation directory for a new distro. Without a distro
name, setup selects the newest versioned stable Ubuntu listed by WSL. Only an
explicitly selected existing distro is reused; setup preserves other distros.
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

## Run a native PowerShell job

Native jobs are submitted locally by the logged-in Windows owner. Prepare a
Git checkout on an NTFS volume, with the job script and its output path inside
that checkout. The runner checks its exact 40-character commit revision and
uploads one artifact only after a successful job. The upload destination must
use HTTPS, accept bearer authentication in an HTTP PUT, and echo the received
SHA-256 digest in `X-Artifact-SHA256`. The receiver should verify that digest
before acknowledging it.

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

The first release must be qualified on a Windows machine before declaring
Windows support production ready. Exercise fresh install and restart, WinGet
repair, a deliberate failing job, successful PowerShell artifact upload with a
digest-confirming receiver, upload failure, worker lifecycle, T3 desktop, and
an Ubuntu build. Record the exact Windows, WSL, Ubuntu, and WinGet versions.
