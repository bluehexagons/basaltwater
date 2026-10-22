# Windows GitHub Actions runners with WSL

Prepare [Windows/WSL](windows-wsl.md), then run these commands in PowerShell as
the same Windows owner. Jobs run natively and can invoke that owner's Ubuntu.
There is no Windows web panel. Live Windows 11/WSL qualification remains open.

## Set up

```powershell
$state = Get-Content "$env:LOCALAPPDATA\Basaltwater\wsl-setup.json" -Raw | ConvertFrom-Json
$runner = Join-Path $state.Source 'windows\Setup-GitHubRunner.ps1'
& $runner -Repository owner/project,owner/another -AllowedUser owner -RegisterWithGh
```

Sign in with **Windows** `gh auth login --hostname github.com` first; a WSL
login is separate. Repository registration requires repository admin access and
`repo` scope. For organizations use `-Organization ORG`, optional
`-RunnerGroup GROUP`, and runner-management access with `admin:org` for
OAuth/classic tokens. `GH_TOKEN`/`GITHUB_TOKEN` override saved logins.
See [token permissions and CLI commands](../services/github-runner.md#get-a-registration-token).

Omit the registration option to install pending runners. To register later:

- **CLI:** `& $runner -Action register -RegisterWithGh` registers all pending scopes.
- **Website:** Open the repository's **Settings → Actions → Runners → New self-hosted runner**.
  Copy only the value after `--token`, then run
  `& $runner -Action register -Repository owner/project -PromptToken` and paste
  into the hidden prompt. The token lasts one hour. Alternatively pass
  `-RegistrationTokenFile PATH` and delete your file afterward.

For a fresh Windows/WSL installation, add the corresponding installer options:

```powershell
& .\install.ps1 -BuildServer -CIRepository owner/project,owner/another -CIAllowedUser owner -CIRegisterWithGh
```

The installer supplies Git, PowerShell and, when requested, Windows `gh`.
`-CIPromptToken` and `-CIRegistrationTokenFile PATH` are alternatives for one
scope. After a required restart, repeat the registration option with `-Resume`,
or register later. Credential failures leave runners pending and let other setup
finish. Tokens never enter saved setup state or command arguments.

## Run and maintain

Use `runs-on: [self-hosted, Windows, X64, windows-ci]` and the
[smoke workflow](../../windows/examples/github-actions.yml) in a private repo.
Confirm **Online/Idle** in GitHub; local task status alone cannot prove it.

```powershell
& $runner -Action status
& $runner -Action stop   # interrupts jobs
& $runner -Action start
wsl.exe --distribution $env:BASALTWATER_WSL_DISTRO --user $env:BASALTWATER_WSL_USER --exec /usr/bin/git --version
if ($LASTEXITCODE -ne 0) { throw 'WSL command failed' }
```

The WSL variables above are provided **inside workflow jobs**. Tasks start at
the owner's login with limited privileges. Logout, sleep, restart and WSL
shutdown can interrupt jobs. Use NTFS for Windows builds and the Linux filesystem
for Linux builds. Keep WSL commands in the foreground and clean up background
Linux processes in the workflow; Windows cleanup cannot guarantee their termination.

Up to 20 repositories share one job slot, including post-job cleanup. Waiting
counts toward timeouts; order is unspecified and downloads before the hook can
overlap. Drain jobs before setup and repeat the complete desired repository,
allowlist and label sets. Omitted repos are disabled; re-adding reuses healthy
credentials. Group/label changes retire credentials and accept a fresh token;
remove retired GitHub entries. Self-updates are disabled; use a reviewed new
Basaltwater source revision for updates. From an updated installer, run
`& .\install.ps1 -Resume -UpdateSource` (optionally `-Version REF`);
ordinary `-Resume` retains its pin. This also upgrades an existing WSL installation
that predates runner support. Add the desired `-CIRepository`/`-CIAllowedUser`
options to that update command to configure it.

The checksum-pinned official x64 runner is **2.338.1**. State, workspaces, bounded
gate outcomes and `_diag` logs live under `%LOCALAPPDATA%\Basaltwater\github-runner`,
restricted to the owner, Administrators and SYSTEM. Registration diagnostics are
discarded. Jobs can access the owner's Windows/WSL files and saved logins; use
a dedicated CI account and mutually trusted private repositories. The shared
[identity rules](../services/github-runner-operations.md#job-identity-and-gate)
deny forks, public repositories, bots, reruns and unsupported events.

## Verify

Run the [manual Windows tests](../../.github/workflows/windows-manual.yml).
Before production use, qualify on Windows 11: two private repository registrations,
Windows/WSL smoke and a real build, denied-job canaries including `if: always()`,
overlapping jobs, cancellation, logout/login, reboot and fresh-token retry.
Record Windows/WSL versions; hosted native tests do not establish WSL readiness.

See [multi-repository builds](../services/github-runner-repositories.md) for Antistatic.
