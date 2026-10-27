# Install Basaltwater

Use the installer on the machine that will manage your hosts. It keeps a local
Git worktree, installs the managed `basaltw` launcher, and can configure
the same machine immediately.

For a guided first experiment, use [Try Basaltwater on a Debian
VM](getting-started.md). This page also covers advanced installation choices;
choose one path rather than running every example.

This guide describes the `v2.0.0` release-candidate series. The current source
package version is `2.0.0rc1`, corresponding to preview tag `v2.0.0-rc.1`.
Before a preview tag is published, `dev` follows `main`; `stable` selects the
latest final release and does not select release candidates.

Debian is the only officially supported distribution. Ubuntu and Linux Mint are
recognized as best-effort Debian-compatible hosts.

For an existing Windows 11 coding account, use the experimental
[native Windows setup](../workstations/windows.md). Its PowerShell installer
prepares Git, provider CLIs, optional runtimes, shared skills, and T3 Code desktop.
It does not install the Linux controller. The separate
[Ubuntu WSL setup](../workstations/windows-wsl.md) installs Linux tools in Ubuntu.

For an existing CachyOS KDE workstation, use the experimental
[`agent_cachyos` local setup](../workstations/cachyos.md). It uses the current desktop account,
installs missing tools through pacman/upstream installers, and leaves OS
updates and desktop configuration to you. The Debian examples below configure
a broader host stack and do not apply to CachyOS.
For Codex with T3 Code's native desktop package, follow the
[complete CachyOS desktop installer example](../workstations/cachyos.md#install-with-t3-code-desktop).
It uses `--local-setup agent_cachyos --t3code-desktop` as the desktop user,
without running the installer under `sudo`.

## Prerequisites

Basaltwater requires Python 3.11 or newer on the controller and target. This
matches its use of the standard-library TOML parser; Python 3.10 is no longer
supported. Use a distribution with a supported system Python rather than
replacing `/usr/bin/python3` independently of its package manager.
The shell installer checks an existing interpreter before package or source
changes, including during `--plan`; if Python is missing, it checks again after
installing prerequisites. It does not upgrade the operating-system release to
obtain a newer Python.

Use a regular account with access to `sudo` on Debian. `sudo` asks for your
account password and grants administrator privileges to that command. If the
account cannot use it, ask the machine's administrator to provide access before
following system-installation examples.

The examples work from fish, Bash, and Zsh because they explicitly invoke
`sh`. They use `curl` and a temporary download that is removed on exit.
They execute only after a successful complete download. If curl is missing,
install it through your distribution's package manager; on Debian:

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates
```

The fetch command leaves DNS and connection diagnostics visible and limits
download time. Copy the complete block for your chosen setup; it preserves the
installer's exit status and cleans up the temporary script even after failure.
Add `--plan` before `--setup` or `--local-setup` for a preview that does not
install packages, download the repository, or change the launcher. A setup
`--dry-run` alone previews only the final setup phase.

## Choose an installation path

Run these commands in a terminal as the local account that should own the
installation. Replace `$USER` only when installing for a different existing
account.

### Set up a native Windows coding workstation

Windows is a lower-priority companion to CachyOS for compatibility testing,
CI, and native packaging. This experimental setup prepares the current
Windows 11 x64 account with Git, GitHub CLI, Codex, T3 Code desktop, Node.js LTS,
Python/uv, and shared agent skills. It does not register a CI runner.

Once the native profile is published to `main`, open PowerShell as the
intended account and copy the entire block:

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

Add `-Plan` after `-Python` to preview selections. The block downloads before
executing, reports installer failure, and removes the temporary download even
on failure. It uses process-scoped execution-policy bypass. Follow the
[Windows workstation guide](../workstations/windows.md) for the current-checkout
command, optional tools, authentication, and recovery.

### Install the launcher only

Use this when you want to choose the first setup later:

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh
```

The installer uses `sudo` for packages when needed. To install the source in
`/opt/basaltwater` and expose a system launcher instead, use:

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sudo sh "$installer" "$@"
' sh --user "$USER"
```

### Set up a minimal Debian control plane

This installs common administrator and Linux tools and configures the local
machine to manage other VMs and containers:

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sudo sh "$installer" "$@"
' sh --user "$USER" --local-setup control_plane
```

Coding agents are optional; append `--agent-tool codex`, for example, only
when wanted. If the orchestration machine is itself a Proxmox VM, add
`--qemu-guest-agent` before `--local-setup`. The installer then installs the
guest-agent package and starts and enables its systemd service during
self-setup:

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sudo sh "$installer" "$@"
' sh --user "$USER" --qemu-guest-agent \
    --local-setup control_plane
```

For an already installed orchestration host, run the equivalent command:

```bash
sudo basaltw self-setup --qemu-guest-agent
```

Use this on a VM only; the QEMU guest agent is not applicable to an LXC
container. `--qemu-guest-agent` requires root and system-package installation.

### Set up a Debian GNOME desktop control plane

This converts graphical access to one shared XFCE/XRDP desktop and installs
GitHub CLI and Codex. Log out of GNOME first and run from SSH or a text console.
Console graphical login is disabled; applications and home data remain.
See [XRDP migration and recovery](../workstations/xrdp.md#migration-and-recovery) before converting
a machine that currently relies on local graphical login.

```sh
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sudo sh "$installer" "$@"
' sh --user "$USER" --local-setup agent_workstation \
    --control-plane --desktop xfce --rdp --rdp-existing-password
```

This expects `$USER` to be an existing non-root account with an unlocked
password. `--rdp-existing-password` reuses that password without putting it in
the command line; it does not create or reset an account password. Add a
trusted network restriction when you know the management range, for example:

```text
--rdp-source 192.168.1.0/24
```

Without `--rdp-source`, RDP is available on all local interfaces with the
configured rate-limited firewall rule. See [XRDP](../workstations/xrdp.md) for connection and
firewall details.

The `agent_vm`, `agent_workstation`, and `agent_code_vm` profiles default to
GitHub CLI and Codex. `--agent-tool` values add to those defaults and accept
comma-separated lists; use `--no-agent-tool` to remove a default. The
`agent_code_vm` profile additionally defaults to Geany, T3 Code, RDP, T3
pairing, read-write Git, active GitHub auth, and target-owned Codex login. It
does not assume a management network; add `--lan-access`, `--access-source`, or service-specific
source flags explicitly. Node.js is installed automatically because T3 Code
requires it. Playwright remains available explicitly with
`--browser-automation playwright` for SSH-only and standalone-agent browser
automation. Go, Python, and other project runtimes remain optional. Use
`--agent-config active`, `--git-auth active`, or the specified-file credential
options when the control plane should transfer selected settings or
credentials. Active GitHub auth can use the controller's `gh auth token`
command when the token is stored in the controller's keyring. Codex subscription
login uses a device code through `--agent-auth login`,
without a controller Codex installation. Claude Code and OpenCode imports
use file-backed credentials. Use
`--repo GIT_URL` for target-side HTTPS clones; public repositories on any
reachable Git host are supported.
The [credentials overview](../agents/credentials.md) routes workspace passwords,
[Git access](../agents/git-access.md), and [agent authentication](../agents/agent-authentication.md)
to their focused workflows.

Node and agent-tool setup installs a managed Bash environment under
`~/.local/share/basaltwater/shell-env.sh`. The active Bash login file and
`.bashrc` load it, and they export `BASH_ENV` so nested non-interactive Bash
commands load it as well. This keeps the managed T3 runtime, `~/.local/bin`,
`~/.opencode/bin`, and an installed NVM default Node version available to
coding-agent command runners;
it also makes the `nvm` function available so an agent can inspect or switch
Node versions. Go continues to use `/etc/profile.d/go.sh`, while uv and other
user-scoped executables use `~/.local/bin`. Start a new agent session after a
setup or patch run changes this environment.

When setup switches from root to a login or deployment build account, it removes
the caller's `BASH_ENV`, `ENV` and NVM paths before starting that account's Bash.
The target account's own login profile still loads its managed environment.
This also works on the first run, before that profile or environment file exists.
It prevents new users from trying to read `/root/.local/share/basaltwater/shell-env.sh`;
root's directory permissions stay private.

Every setup also exposes the system `python3` interpreter as the conventional
`python` command when that alias is missing. The separate `--python` option
still controls the additional uv-based Python tooling bundle.

Setup reruns reuse completed package work while reconciling selected agent tools
and the T3 Code service. Use `--refresh-packages` when you deliberately want a
new APT update/upgrade and versioned runtime check; the flag is one-shot and is
not retained in the saved setup command.

## Verify the installation

Start a new login shell if necessary, then run:

```bash
command -v basaltw
basaltw --version
basaltw channel
basaltw --help
```

The command is `basaltw`, backed by `basaltwater.py`. No old executable or
module alias is installed. Existing recent infra-tools installations require
a [one-time migration through an intermediate version](../migration-guide.md)
before current setup or installation; afterward the new
paths and command names are the only supported interfaces. Bootstrap installs
completion for `basaltw` in the configured shell.

If the command is not found in a user installation, add its directory for the
current shell and start a new login shell later:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Before applying a setup for the first time, validate its profile with a dry
run. This simple local preview does not need `sudo`:

```bash
basaltw setup server_dev localhost "$USER" --node --dry-run
```

The preview shows the configuration and setup handoff without applying the
profile or saving a configured host. Live local setup requires `sudo`.
For the all-in-one commands above, the installer runs the selected local setup
after installing the launcher, so a separate setup command is not required.
SSH hardening is applied when `openssh-server` is present; an outbound-only
control plane without `sshd` reports a skip instead of failing the setup.

Tagged GitHub releases also attach a Python wheel. Release CI installs that
wheel into an isolated environment and smoke-tests all packaged entry points
before publication. The source installer remains the recommended operator path
because it provides channel selection and worktree-aware upgrades; the wheel is
primarily a verified release artifact and an option for externally managed
Python environments.

## Install and configure a remote host

Run these commands on the controller. Replace `server.example` with the
target's hostname or IP and `admin` with the target account to configure.
Setup connects as root to perform system changes; the positional username does
not select the SSH transport account. First arrange key-based root SSH access
on the Debian target. See [SSH authentication](../operations/ssh.md)
for authentication requirements and passphrase handling.

From the target's VM console or another trusted session, obtain its fingerprint:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Back on the controller, preview the setup before applying it:

```bash
basaltw setup server_dev server.example admin --node --dry-run
```

After a successful preview, repeat the setup command without `--dry-run` from
a terminal. On the first connection, OpenSSH asks you to confirm the target's
fingerprint; compare it with the trusted value above. Accepting it saves the
key in the workspace and continues setup without a separate enrollment
command. An already enrolled host connects directly; a changed key is rejected.
For unattended setup, first use `basaltw ssh-key enroll server.example
--fingerprint SHA256:YOUR_VERIFIED_FINGERPRINT` in the same workspace.
Add `--key ~/.ssh/YOUR_KEY` if your root login uses a non-default private key.
A normal sudo password on the target is not a substitute for root SSH access;
Basaltwater does not collect or forward sudo passwords.

Ruby/Rails setup and deployment support has been discontinued. `v2.0.0`
refuses Ruby repositories before changing the target. Current Basaltwater
machines have no Rails services, and Basaltwater does not install or
maintain Ruby applications.

For agentic coding targets, use the [headless terminal example](../workstations/workstations.md#headless-agentic-coding-host)
when no desktop or RDP is wanted, or the [desktop/RDP example](../workstations/workstations.md#desktop-agentic-coding-workstation-with-rdp)
for graphical work. For a Proxmox node, start with [Proxmox workflows](../operations/proxmox.md).

## Channels and upgrades

The installer downloads the repository into a managed local worktree. The
launcher stays installed while the worktree's channel changes:

| Channel | Selects |
| --- | --- |
| `stable` | Latest `vMAJOR.MINOR.PATCH` release tag |
| `dev` | `main` branch |
| `v<version>` | One exact tag, such as `v2.0.0` or `v2.0.0-rc.1` |
| `branch-<branch>` | Any existing branch |
| `commit-<hash>` | One exact commit |

The v2.0 preview sequence uses Git tags `v2.0.0-rc.N`, with matching Python
package versions `2.0.0rcN`. Once a preview tag is published, select it
explicitly with `basaltw channel v2.0.0-rc.1` and update with `basaltw upgrade`.
The `stable` selector only matches plain three-part version tags, so it will not
move to a preview. A release candidate is for qualification and feedback; it is
not the final `v2.0.0` release.

Inspect or change the selected channel:

```bash
basaltw channel
basaltw channel stable
basaltw channel dev
```

Update the local installation to the newest commit on its selected channel:

```bash
basaltw upgrade
```

The default installer channel is `dev`, which tracks `main`. Use `stable` when
you want the latest versioned release. `upgrade` refuses to overwrite local
worktree changes; commit or stash changes before reinstalling or upgrading.

Channel Git commands have a five-minute deadline and disable terminal credential
prompts. A timeout terminates the local command group and leaves saved channel
state unchanged; inspect `git status` and `HEAD` before retrying an interrupted
checkout. Configure a credential helper or SSH agent before accessing private
remotes.

`--install-dir` must name a dedicated application directory. The installer
refuses symlinks, mount points, home directories, broad system paths, and
unmanaged existing directories. Successful installs record
`.basaltwater/managed-install` for later reinstalls. Recent infra-tools
installations require the [intermediate-version cutover](../migration-guide.md) first;
the installer refuses to create a parallel namespace beside an old installation.

Activation failures and HUP/INT/TERM interruptions restore the previous source
tree. The installer keeps staged or failed source alongside it for inspection
and prints recovery paths if restoration fails. SIGKILL or power loss cannot
run shell traps: inspect the adjacent `.backup.*`, `.new.*`, and `.failed.*`
directories and restore the previous backup to the install path before retrying.
Source rollback does not undo package or launcher changes already made by
bootstrap. Run only one installer for a given destination at a time.

These channel commands manage the controller installation. A remote `setup`
or `patch` run sends a complete snapshot of the controller's current source to
the target and replaces the target's `/opt/basaltwater` runtime with it while
preserving `/opt/basaltwater/state`. The snapshot includes its project version,
source commit, branch, and whether the controller checkout was dirty, so
`basaltw channel` on the target reports exactly what that setup run
deployed. To replace a snapshot from the controller, switch or upgrade the
controller channel first, then rerun `setup` or `patch` to update the target.
Alternatively, [Debian refresh](../reference/command-line.md#refresh-this-machine) downloads
the snapshot's branch into a managed target checkout and repeats saved setup.
Downloaded source is made readable before activation even under a private
administrative-job umask; Git/channel metadata and retained runtime data keep
their existing private permissions. The previous source remains in a private
adjacent backup. A failed setup can follow successful source activation, so
inspect both the installed source and setup result before retrying.
Subsequent managed-checkout refreshes reconcile only runtime source permissions;
retained state and application data are excluded.
The root `state` link is ignored as a path rather than a directory, keeping the
converted checkout clean for later upgrades. A broker-enabled Debian refresh
restores its approval hostname from the protected policy and verifies its port
and requester UID, preserving the existing HTTPS approval origin during replay.

## Unsupported orchestration hosts

The installer can also install the remote-management launcher on another Linux
distribution, such as Fedora, but this remains outside the support guarantee.
It asks for an explicit `[y/N]` confirmation, does not attempt to install APT
packages, and requires these controller commands to already be available:

- `python3`
- `git`
- `ssh`
- `rsync`
- `curl`

Install the equivalent packages with the host distribution's package manager
before rerunning the installer. The uv bootstrap also requires `curl`, so
`wget` alone does not satisfy the installer prerequisites. CachyOS has its own limited
[local coding setup](../workstations/cachyos.md), including prerequisite installation, and does
not use this unsupported-host confirmation path.

The installer skips local system-package bootstrap on unsupported hosts. Do not
use `--local-setup` or `--qemu-guest-agent` there; install the launcher and use
remote-management commands such as `setup`, `patch`, `ssh`, `push`, and `pull`.
Remote setup profiles and target-side package operations remain Debian-oriented.

## Debian package sources

The installer and setup need network access to APT. On Debian, they check the
release codename and archive keyring, disable active CD-ROM-only entries, and
ensure the current release uses the official mirrors:

- `https://deb.debian.org/debian` for the base and updates suites;
- `https://security.debian.org/debian-security` for security updates.

If a minimal or offline Debian installation has only installation media
configured, Basaltwater creates a managed source file and runs `apt-get
update` before installing packages. Existing `non-free-firmware` components
are preserved. Existing source files are backed up, and an unmanaged
`basaltwater-debian.sources` file is not overwritten. Existing current Debian
base and security entries are reused; the managed file is limited to any
missing suite, and a redundant managed file from an older installer run is
removed. An existing entry must be enabled, include binary packages and the
case-sensitive `main` component,
and associate the official mirror with the current suite in the same line or
deb822 stanza. CD-ROM cleanup affects only its active entry; other stanzas in
the same source file are retained.

The installer and setup print APT progress directly. Setup package operations
wait up to five minutes while another package operation releases the APT lock,
including an automatic update that starts during setup. During that bounded
wait, setup should continue after the other operation finishes instead of
failing immediately. Keep the terminal open until the installer reports its
completion message. If a previously started installer was interrupted, rerun
the same command after checking that no other `apt` or `dpkg` process is still
active.

If this step fails, fix network, DNS, proxy, or mirror access and rerun the
same installer command. Do not work around a failed package-list update by
leaving a CD-ROM as the only source.

The common CLI-tool bundle also installs `pv`, which is available from Debian
13's repositories but is not present in a minimal Debian installation by
default. It is available for explicit byte-stream pipelines; rsync-based
transfers continue to use rsync's own progress display.

## Optional local tools

The installer already bootstraps the launcher and shell completion. For a
manual completion refresh or another shell:

```bash
basaltw completions --shell bash
basaltw completions --shell zsh
```

See [Shell completion](../reference/shell-completion.md) for system-wide and Fish setup.

## Workspace and credentials

Saved host state defaults to `~/.config/basaltwater`. Use another workspace
when separating projects or test environments:

```bash
basaltw --workspace /srv/basaltwater-workspace list
```

The `credentials` commands manage the workspace password store used by
features such as Samba/SMB; they do not configure GitHub, Codex, Claude Code,
or OpenCode:

```bash
basaltw credentials set admin
basaltw credentials list
basaltw credentials remove admin
```

Passwords are excluded from saved setup state and reconstructed commands.

Use `basaltw cleanup --dry-run` to inspect incompatible local setup or Proxmox
records, then `basaltw cleanup --yes` to remove the reviewed findings. Cleanup
locks affected state against managed writers and refuses files changed since
inspection. Its private timestamped backups under `<workspace>/cleanup-backups/`
retain workspace-relative paths, so identically named files keep separate
copies. Symlinked cache or backup directories are refused.

For agent VM authentication, provider-specific login/file/GitHub sources, per-VM isolation, and remote
rotation, see [Agent authentication](../agents/agent-authentication.md). For repository
policy and HTTPS credentials, see [Git access](../agents/git-access.md).

## Related guides

- [Command-line reference](../reference/command-line.md) — all setup flags and system types
- [Credentials overview](../agents/credentials.md) — choose the correct credential flow
- [Agent authentication](../agents/agent-authentication.md) — agent auth and recovery
- [Git access](../agents/git-access.md) — repository policy and HTTPS authentication
- [Workstations](../workstations/workstations.md) — desktop profiles, browsers, and agents
- [XRDP](../workstations/xrdp.md) — RDP sessions and firewall behavior
- [Machine types](../reference/machine-types.md) — Debian VMs, bare metal, and containers

You can review the downloaded [`install.sh`](../../install.sh) before executing
it. The examples download a file first and do not pipe network content into a
privileged shell.
