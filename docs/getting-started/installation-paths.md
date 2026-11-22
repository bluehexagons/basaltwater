# Install the controller or first remote host

[Documentation index](installation.md)

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

Open PowerShell as the intended account and copy the entire block:

```powershell
& {
    $installer = Join-Path $env:TEMP ("basaltwater-" + [guid]::NewGuid() + ".ps1")
    try {
        Invoke-WebRequest 'https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.ps1' -UseBasicParsing -OutFile $installer -TimeoutSec 120 -ErrorAction Stop
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Profile agent_windows -AgentTool codex -Node -Python
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

The user installation uses `sudo` for packages when needed. To install into
`/opt/basaltwater` and expose a system launcher, use a reviewed copy of
`install.sh`. Obtain the full 40-character commit ID through your release or
review process on a trusted controller. Review `install.sh` at that commit and
transfer that copy to the target if necessary. Set `reviewed_commit` to that ID
and run from the directory containing the reviewed script.

```sh
sudo sh ./install.sh --user "$USER" \
  --channel "commit-$reviewed_commit" --source-commit "$reviewed_commit"
```

For a system install that should track a channel, use `--channel dev`,
`--channel stable`, or the intended branch or tag in place of the commit
channel. Keep `--source-commit` to require the first resolution to match your
reviewed commit. A branch or tag mismatch stops before activation.

### Set up a minimal Debian control plane

This installs common administrator and Linux tools and configures the local
machine to manage other VMs and containers:

```sh
sudo sh ./install.sh --user "$USER" \
  --channel "commit-$reviewed_commit" --source-commit "$reviewed_commit" \
  --local-setup control_plane
```

Coding agents are optional; append `--codex`, for example, only
when wanted. If the orchestration machine is itself a Proxmox VM, add
`--qemu-guest-agent` before `--local-setup`. The installer then installs the
guest-agent package and starts and enables its systemd service during
self-setup:

```sh
sudo sh ./install.sh --user "$USER" \
  --channel "commit-$reviewed_commit" --source-commit "$reviewed_commit" \
  --qemu-guest-agent --local-setup control_plane
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
See [XRDP migration and recovery](../workstations/xrdp-recovery.md#migration-and-recovery) before converting
a machine that currently relies on local graphical login.

```sh
sudo sh ./install.sh --user "$USER" \
  --channel "commit-$reviewed_commit" --source-commit "$reviewed_commit" \
  --local-setup agent_workstation --codex \
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
GitHub CLI. No provider is selected by default. Agent setup requires at least
one of `--codex`, `--claude`, or `--opencode`.
Combine these flags to select multiple providers. The general `--agent-tool`
selector also accepts comma-separated lists; use `--no-agent-tool` to remove a default. The
`agent_code_vm` profile additionally defaults to Geany, T3 Code, RDP, T3
pairing, read-write Git, active GitHub auth, and target-owned login when Codex is selected. It
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
