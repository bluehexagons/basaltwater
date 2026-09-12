# Install infra-tools

Use the installer on the machine that will manage your hosts. It keeps a local
Git worktree, installs the managed `infra-tools` launcher, and can configure
the same machine immediately.

For a guided first experiment, use [Try infra-tools on a Debian
VM](GETTING_STARTED.md). This page also covers advanced installation choices;
choose one path rather than running every example.

This guide targets the upcoming stable `v2.0.0` release. Before that tag is
published, `dev` follows `main` and `stable` selects the latest published tag.

Debian is the only officially supported distribution. Ubuntu and Linux Mint are
recognized as best-effort Debian-compatible hosts.

For an existing CachyOS KDE workstation, use the experimental
[`agent_cachyos` local setup](CACHYOS.md). It uses the current desktop account,
installs missing tools through pacman/upstream installers, and leaves OS
updates and desktop configuration to you. The Debian examples below configure
a broader host stack and do not apply to CachyOS.

## Prerequisites

Use a regular account with access to `sudo` on Debian. `sudo` asks for your
account password and grants administrator privileges to that command. If the
account cannot use it, ask the machine's administrator to provide access before
following system-installation examples.

The installer needs either `wget` or `curl`. The examples below use wget,
which is commonly present on minimal Debian systems. If only `curl` is
installed, replace the download command with:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  -o "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
```

If neither command is available, install one first with:

```bash
sudo apt-get update && sudo apt-get install -y wget ca-certificates
```

The fetch command leaves DNS and connection diagnostics visible and limits
retries, so a VM with no network path fails clearly instead of appearing idle.
Every example downloads to a user-owned file before invoking the installer.
Run each command in order and confirm the download succeeds before running the
installer command; remove the file afterward.

## Choose an installation path

Run these commands in a terminal as the local account that should own the
installation. Replace `$USER` only when installing for a different existing
account.

### Install the launcher only

Use this when you want to choose the first setup later:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sh "$HOME/.infra_tools-install.sh"
rm -f "$HOME/.infra_tools-install.sh"
```

The installer uses `sudo` for packages when needed. To install the source in
`/opt/infra_tools` and expose a system launcher instead, use:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER"
rm -f "$HOME/.infra_tools-install.sh"
```

### Set up a minimal Debian control plane

This installs common administrator and Linux tools and configures the local
machine to manage other VMs and containers:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER" --local-setup control_plane
rm -f "$HOME/.infra_tools-install.sh"
```

Coding agents are optional; append `--agent-tool codex`, for example, only
when wanted. If the orchestration machine is itself a Proxmox VM, add
`--qemu-guest-agent` before `--local-setup`. The installer then installs the
guest-agent package and starts and enables its systemd service during
self-setup:

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER" --qemu-guest-agent \
  --local-setup control_plane
rm -f "$HOME/.infra_tools-install.sh"
```

For an already installed orchestration host, run the equivalent command:

```bash
sudo infra-tools self-setup --qemu-guest-agent
```

Use this on a VM only; the QEMU guest agent is not applicable to an LXC
container. `--qemu-guest-agent` requires root and system-package installation.

### Set up a Debian GNOME desktop control plane

This converts graphical access to one shared XFCE/XRDP desktop and installs
GitHub CLI and Codex. Log out of GNOME first and run from SSH or a text console.
Console graphical login is disabled; applications and home data remain.
See [XRDP migration and recovery](XRDP.md#migration-and-recovery) before converting
a machine that currently relies on local graphical login.

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sudo sh "$HOME/.infra_tools-install.sh" --user "$USER" --local-setup agent_workstation \
  --control-plane --desktop xfce --rdp --rdp-existing-password
rm -f "$HOME/.infra_tools-install.sh"
```

This expects `$USER` to be an existing non-root account with an unlocked
password. `--rdp-existing-password` reuses that password without putting it in
the command line; it does not create or reset an account password. Add a
trusted network restriction when you know the management range, for example:

```text
--rdp-source 192.168.1.0/24
```

Without `--rdp-source`, RDP is available on all local interfaces with the
configured rate-limited firewall rule. See [XRDP](XRDP.md) for connection and
firewall details.

The `agent_vm`, `agent_workstation`, and `agent_code_vm` profiles default to
GitHub CLI and Codex. `--agent-tool` values add to those defaults and accept
comma-separated lists; use `--no-agent-tool` to remove a default. The
`agent_code_vm` profile additionally defaults to Geany, T3 Code, RDP, T3
pairing, read-write Git, and active auth sources. It does not assume a
management network; add `--lan-access`, `--access-source`, or service-specific
source flags explicitly. Node.js is installed automatically because T3 Code
requires it. Playwright remains available explicitly with
`--browser-automation playwright` for SSH-only and standalone-agent browser
automation. Go, Python, and other project runtimes remain optional. Use
`--agent-config active`, `--git-auth active`, or the specified-file credential
options when the control plane should transfer selected settings or
credentials. Active GitHub auth can use the controller's `gh auth token`
command when the token is stored in the controller's keyring; file-backed
Codex, Claude Code, and OpenCode credentials must be supplied as files. Use
`--repo GIT_URL` for target-side HTTPS clones; public repositories on any
reachable Git host are supported.
The [credentials overview](CREDENTIALS.md) routes workspace passwords,
[Git access](GIT_ACCESS.md), and [agent authentication](AGENT_AUTHENTICATION.md)
to their focused workflows.

Node and agent-tool setup installs a managed Bash environment under
`~/.local/share/infra-tools/shell-env.sh`. The active Bash login file and
`.bashrc` load it, and they export `BASH_ENV` so nested non-interactive Bash
commands load it as well. This keeps the managed T3 runtime, `~/.local/bin`,
`~/.opencode/bin`, and an installed NVM default Node version available to
coding-agent command runners;
it also makes the `nvm` function available so an agent can inspect or switch
Node versions. Go continues to use `/etc/profile.d/go.sh`, while uv and other
user-scoped executables use `~/.local/bin`. Start a new agent session after a
setup or patch run changes this environment.

Every setup also exposes the system `python3` interpreter as the conventional
`python` command when that alias is missing. The separate `--python` option
still controls the additional uv-based Python tooling bundle.

Setup reruns reuse completed package and tool work. Use `--refresh-packages`
when you deliberately want a new APT update/upgrade and versioned runtime
check; the flag is one-shot and is not retained in the saved setup command.

## Verify the installation

Start a new login shell if necessary, then run:

```bash
command -v infra-tools
infra-tools --version
infra-tools channel
infra-tools --help
```

The installed command is `infra-tools`; the legacy `infra_tools` command is no
longer supported.
Rerunning bootstrap removes a regular-file or symlink launcher named
`infra_tools` from the configured system or user launcher directory before
installing the new command. Self-setup also removes generated shell completion
registrations and files for `infra_tools` and `infra_tools.py` while installing
the `infra-tools` completion for the configured shell.

If the command is not found in a user installation, add its directory for the
current shell and start a new login shell later:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Before applying a setup for the first time, validate its profile with a dry
run. This simple local preview does not need `sudo`:

```bash
infra-tools setup server_dev localhost "$USER" --node --dry-run
```

The preview shows the configuration and setup handoff without applying the
profile or saving a configured host. Live local setup requires `sudo`.
For the all-in-one commands above, the installer runs the selected local setup
after installing the launcher, so a separate setup command is not required.
SSH hardening is applied when `openssh-server` is present; an outbound-only
control plane without `sshd` reports a skip instead of failing the setup.

Tagged GitHub releases also attach a Python wheel. Release CI installs that
wheel into an isolated environment and smoke-tests both packaged entry points
before publication. The source installer remains the recommended operator path
because it provides channel selection and worktree-aware upgrades; the wheel is
primarily a verified release artifact and an option for externally managed
Python environments.

## Install and configure a remote host

Run these commands on the controller. Replace `server.example` with the
target's hostname or IP and `admin` with the target account to configure.
Setup connects as root to perform system changes; the positional username does
not select the SSH transport account. First arrange key-based root SSH access
on the Debian target, then verify its host key. See [SSH authentication](SSH.md)
for authentication requirements and passphrase handling.

From the target's VM console or another trusted session, obtain its fingerprint:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Back on the controller, enroll the host and compare the displayed fingerprint
with that trusted value. Preview the setup before applying it:

```bash
infra-tools ssh-key enroll server.example
infra-tools setup server_dev server.example admin --node --dry-run
```

After a successful preview, repeat the setup command without `--dry-run`.
Add `--key ~/.ssh/YOUR_KEY` if your root login uses a non-default private key.
A normal sudo password on the target is not a substitute for root SSH access;
infra-tools does not collect or forward sudo passwords.

Ruby/Rails setup and deployment support has been removed. Keep a pinned older
infra-tools installation for a legacy Rails host; `v2.0.0` refuses Ruby
repositories rather than attempting an in-place migration.

For agentic coding targets, use the [headless terminal example](WORKSTATIONS.md#headless-agentic-coding-host)
when no desktop or RDP is wanted, or the [desktop/RDP example](WORKSTATIONS.md#desktop-agentic-coding-workstation-with-rdp)
for graphical work. For a Proxmox node, start with [Proxmox workflows](PROXMOX.md).

## Channels and upgrades

The installer downloads the repository into a managed local worktree. The
launcher stays installed while the worktree's channel changes:

| Channel | Selects |
| --- | --- |
| `stable` | Latest `vMAJOR.MINOR.PATCH` release tag |
| `dev` | `main` branch |
| `v<version>` | One release tag, such as `v2.0.0` |
| `branch-<branch>` | Any existing branch |
| `commit-<hash>` | One exact commit |

Inspect or change the selected channel:

```bash
infra-tools channel
infra-tools channel stable
infra-tools channel dev
```

Update the local installation to the newest commit on its selected channel:

```bash
infra-tools upgrade
```

The default installer channel is `dev`, which tracks `main`. Use `stable` when
you want the latest versioned release. `upgrade` refuses to overwrite local
worktree changes; commit or stash changes before reinstalling or upgrading.

These channel commands manage the controller installation. A remote `setup`
or `patch` run sends a complete snapshot of the controller's current source to
the target and replaces the target's `/opt/infra_tools` runtime with it while
preserving `/opt/infra_tools/state`. The snapshot includes its project version,
source commit, branch, and whether the controller checkout was dirty, so
`infra-tools channel` on the target reports exactly what that setup run
deployed. A target snapshot is not a Git worktree: switch or upgrade the
controller channel first, then rerun `setup` or `patch` to update the target.

## Unsupported orchestration hosts

The installer can also install the remote-management launcher on another Linux
distribution, such as Fedora, but this remains outside the support guarantee.
It asks for an explicit `[y/N]` confirmation, does not attempt to install APT
packages, and requires these controller commands to already be available:

- `python3`
- `git`
- `ssh`
- `rsync`
- `curl` or `wget`

Install the equivalent packages with the host distribution's package manager
before rerunning the installer. CachyOS has its own limited
[local coding setup](CACHYOS.md), including prerequisite installation, and does
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
configured, infra-tools creates a managed source file and runs `apt-get
update` before installing packages. Existing `non-free-firmware` components
are preserved. Existing source files are backed up, and an unmanaged
`infra_tools-debian.sources` file is not overwritten. Existing current Debian
base and security entries are reused; the managed file is limited to any
missing suite, and a redundant managed file from an older installer run is
removed.

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
infra-tools completions --shell bash
infra-tools completions --shell zsh
```

See [Shell completion](SHELL_COMPLETION.md) for system-wide and Fish setup.

## Workspace and credentials

Saved host state defaults to `~/.config/infra_tools`. Use another workspace
when separating projects or test environments:

```bash
infra-tools --workspace /srv/infra-tools-workspace list
```

The `credentials` commands manage the workspace password store used by
features such as Samba/SMB; they do not configure GitHub, Codex, Claude Code,
or OpenCode:

```bash
infra-tools credentials set admin
infra-tools credentials list
infra-tools credentials remove admin
```

Passwords are excluded from saved setup state and reconstructed commands.

For agent VM authentication, active/file sources, per-VM isolation, and remote
rotation, see [Agent authentication](AGENT_AUTHENTICATION.md). For repository
policy and HTTPS credentials, see [Git access](GIT_ACCESS.md).

## Related guides

- [Command-line reference](COMMAND_LINE.md) — all setup flags and system types
- [Credentials overview](CREDENTIALS.md) — choose the correct credential flow
- [Agent authentication](AGENT_AUTHENTICATION.md) — agent auth and recovery
- [Git access](GIT_ACCESS.md) — repository policy and HTTPS authentication
- [Workstations](WORKSTATIONS.md) — desktop profiles, browsers, and agents
- [XRDP](XRDP.md) — RDP sessions and firewall behavior
- [Machine types](MACHINE_TYPES.md) — Debian VMs, bare metal, and containers

You can review the downloaded [`install.sh`](../install.sh) before executing
it. The examples download a file first and do not pipe network content into a
privileged shell.
