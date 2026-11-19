# Install or refresh the coding profile

[Documentation index](cachyos.md)

## Quick start

Finish the normal CachyOS installation first: update the system, configure KDE,
and install the appropriate GPU driver. Then open a terminal in your normal
desktop session and run this as yourself (without `sudo`).

The commands on this page use **fish**, CachyOS's
[default login shell](https://wiki.cachyos.org/configuration/post_install_setup/#changing-the-default-shell).
Copy the whole block. The quoted download script runs explicitly under `sh`,
so fish never has to parse POSIX assignments or traps; your login shell stays fish.

```fish
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh --local-setup agent_cachyos --codex \
  --node --python --git-lfs
```

The package step may ask for your desktop user's sudo password. Keep the
terminal attached until setup finishes. This example installs the default Git,
ripgrep, build tools, GitHub CLI, and Codex plus Node.js, Python, and Git LFS.
The installer defaults to the `dev` channel while this profile is new.
The temporary download is removed when the command exits, including on failure.

No account, password, group, sudo, provider, or Git identity changes are made.
Log in to providers through their normal commands when needed:

```fish
codex login
gh auth login
```

Provider login does not configure Git commit identity. If readiness reports a
missing identity, set your own `user.name` and `user.email` with
`git config --global`, or configure them within each repository. Setup never
guesses an identity or changes an existing one. The default-identity check runs
outside a project; repository-specific overrides may differ.

`--git-lfs` initializes missing per-user LFS filters before cloning repositories.
Existing system/user filter values and repository hooks are preserved, including
custom filters. Readiness checks that the last (effective) value of each filter
setting is nonempty. Empty overrides are preserved and reported for you to
resolve; rerunning setup alone will not replace them. Test transfers in your
project to verify custom filters and remote authentication.

The launcher is `~/.local/bin/basaltw`; open a new terminal if the
installer's PATH change is not visible. Bash, Zsh, and Fish are supported.
Other shells need `~/.local/bin` and `~/.opencode/bin` added to PATH manually.

### Install with T3 Code desktop

Choose this installer instead of the basic quick start when you want Codex
inside the native T3 Code app. Run it once from your KDE terminal as yourself:

```fish
sh -c '
  installer=$(mktemp) || exit
  cleanup() { rm -f -- "$installer"; }
  trap cleanup EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" "$@"
' sh --local-setup agent_cachyos --codex \
  --t3code-desktop --node --python --git-lfs
```

This installs the launcher, Codex, GitHub CLI, T3 desktop, and the selected
project tools. Omit `--node --python --git-lfs` for just the default coding tools
and T3 desktop; add `--no-agent-tool gh` to omit GitHub CLI. Setup installs or
checks for updates to `t3code-bin` using CachyOS's default Shelly CLI and may
prompt for package review or sudo. T3 Code starts automatically at subsequent KDE logins.

For an installer-wide preview, add `--plan` before `--local-setup` in your
chosen block. This does not install packages, download the repository, or
change the launcher. Keep installer options such as `--channel dev` before
`--local-setup`, and setup options after it. Adding `--dry-run` after
`--local-setup` previews only the final setup phase; it still installs the
launcher and prerequisites.

After setup, open a new terminal, complete `codex login` and `gh auth login`,
then open T3 Code from KDE and verify a Codex thread and terminal. Refresh setup
queues a detached user worker only after all steps, saved state and notifications
finish. After a short delay it gracefully closes and relaunches a running app,
or starts a stopped app using KDE's graphical environment. If no graphical session
is available, its journal reports deferred launch until KDE login. Check the
printed user journal unit if the worker fails.
Queueing does not prove provider readiness: verify an authenticated thread and
terminal. If T3 cannot
discover Codex, set its provider **Binary path** to the absolute path printed
by setup, typically `/home/USER/.local/bin/codex`. Use
**Settings → Connections** for desktop connection settings.

## Pick the options you need

Append options to `--local-setup agent_cachyos` in the installer command, or to
`basaltw setup agent_cachyos localhost` after the launcher is installed. Select
providers explicitly with `--codex`, `--claude`, or `--opencode`.

| Need | Options |
| --- | --- |
| Coding providers | `--codex`, `--claude`, `--opencode`; repeatable. Select at least one; no provider is selected by default. GitHub CLI is included unless omitted with `--no-agent-tool gh`. |
| Node, Python, Go, or Git LFS | `--node`, `--python`, `--go`, `--git-lfs` |
| Native game and Animator development | `--game-dev`, `--node-versions` ([Antistatic workflow](cachyos-game-development.md)) |
| Godot, media, or graphics diagnostics | `--godot`, `--av-tools`, `--gl-tools` (drivers are never installed) |
| Gaming and streaming | `--gaming`, `--sunshine`, `--moonlight` |
| Creative applications | `--obs`, `--blender`, `--kdenlive`, `--krita`, `--gimp`, `--inkscape`, `--scribus`, `--shotcut` |
| Audio, CAD, or electronics | `--audacity`, `--lmms`, `--ardour`, `--freecad`, `--kicad` |
| Remote desktop or diagnostics | `--remmina`, `--sysadmin-tools` |
| Materials, removable media, publishing | `--material-maker`, `--etcher`, `--butler`, `--steamcmd` ([packages and post-install checks](cachyos-software.md)) |
| Repository workspace | `--repo HTTPS_URL` (repeatable), `--agent-workspace /absolute/path` (default `~/repos`) |
| T3 Code desktop | `--t3code-desktop`: install or update `t3code-bin` through your AUR helper and enable KDE login startup; mutually exclusive with `--web-interface` |
| T3 Code web service | `--web-interface t3code`, then use the T3 Connect flow below; optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Setup plan only | `--dry-run` (use installer `--plan` before `--local-setup` for a full preview) |
| Restrict inbound workstation access | `--lan-access` and/or `--access-source PRIVATE_IP_OR_CIDR`; `--no-lan-access --no-access-source` closes managed access |
| Remote shell access | SSH follows detected service state on local setup; explicitly use `--ssh` or `--no-ssh` to override; remote setup defaults to SSH; see [SSH access](#ssh-access) |
| Setup-result webhooks | `--notify webhook URL` (repeatable), `--notification-level LEVEL`, `--notification-ca-file PATH` |

Examples:

```fish
# Game and media workstation with an additional coding agent
basaltw setup agent_cachyos localhost --codex \
  --opencode --node --python --git-lfs --godot \
  --av-tools --gl-tools

# Gaming and game streaming on the trusted LAN
basaltw setup agent_cachyos localhost --gaming --sunshine --moonlight --lan-access --codex

# Preview a plan without installing packages or changing files
basaltw setup agent_cachyos localhost --node --python --dry-run --codex
```

Application options install native packages from the configured CachyOS
repositories, except the explicit AUR options `--t3code-desktop`,
`--material-maker`, `--butler`, and `--steamcmd`. No Flatpak packages or graphics
drivers are installed. Most application selections only install packages;
Sunshine's startup and conditional encoder configuration are described below. See the
[creative and publishing software guide](cachyos-software.md) for all issue #106
software, source policies, and post-install checks.
`--gaming` selects CachyOS's gaming meta-packages;
`--sunshine` installs the native host plus VA-API diagnostics, enables its user
service at KDE login, and starts it during setup. Quit Sunshine normally from
its tray menu when you want it stopped for the session. `--moonlight` installs
the client. Add `--lan-access` or explicit `--access-source` values for restricted
inbound access, then configure and pair Sunshine in its local web UI.
Sunshine supports Intel, AMD, and NVIDIA GPUs; installing its package does not
verify capture or hardware encoding. See the
[Sunshine checks](cachyos-maintenance.md#sunshine-service-and-encoder-checks) before pairing.

The T3 web service selects Node automatically; both T3 modes require Codex,
Claude, or OpenCode. Python
is also installed when needed for native Node module builds. Existing
version-manager runtimes are retained when their commands are on PATH.
Readiness runs version checks for all selected language commands: Node, npm,
and pnpm for `--node`, and Python and uv for `--python`. A missing or broken
companion tool makes setup incomplete even if the main runtime works.
On CachyOS, `--av-tools` includes FFmpeg, ImageMagick, and ExifTool;
`--gl-tools` includes Mesa/Vulkan diagnostics and apitrace.

## SSH access

Local setup resolves whether SSH is selected from the workstation's current
service state. An active or enabled native `sshd.service`, `sshd.socket`, or
Basaltwater-managed SSH service means SSH stays enabled; a disabled or absent
service means it stays off. Merely having OpenSSH installed does not count as
enablement. An existing valid SSH session also counts as enabled. Use
`--ssh` or `--no-ssh` to choose explicitly. If local setup cannot determine the
state, apply requires one of those flags; a dry-run reports that selection is
deferred. Remote-controller setup defaults to SSH; `--no-ssh` opts out.

On a workstation without recognized SSH, Basaltwater can install its
root-managed `basaltwater-cachyos-ssh.service` for the existing non-root desktop
account on IPv4 port 22, with key-only authentication and no KDE login
requirement. It does not rewrite distro SSH configuration. A recognizable
packaged native SSH service can be safely adopted after validation, preserving
existing SSH sessions. Foreign custom units, drop-ins, and unknown port-22
listeners are refused for review; Basaltwater does not take them over. Turning
SSH off stops the managed service and recognizable stock native SSH, while
foreign custom SSH remains untouched and can make disablement fail safely.

### Install the administrator SSH helper

Before enabling, adopting, repairing, or removing SSH, provision the SSH
helper separately on the workstation. This also applies when setup detects
existing SSH or runs from a remote controller. A workstation with SSH already
off and no managed service to remove can complete setup without the helper.

1. Obtain the exact reviewed Basaltwater commit ID through a trusted release or
   administrator channel. As administrator, fetch from the official repository
   into a fresh root-owned staging directory under `/opt`. Verify that the
   fetched commit matches the approved ID before exporting files.
2. Export the commit's `common/`, `lib/`, and `plugins/` trees into a new directory
   in that staging area. Install the directory as `/opt/basaltwater-cachyos-ssh` with its
   contents owned by root, ordinary files and directories readable by the
   desktop account, and no group/other write access or symlinks. The entry
   point is `common/service_tools/cachyos_ssh.py` within that directory. Keep
   the complete import trees from the same approved commit.
3. Run the normal setup command as the desktop account. Run it again after
   provisioning a replacement helper when updating the SSH lifecycle code.
   Stage and verify the replacement separately before publishing it.

Desktop upgrades and controller uploads leave the administrator installation
alone. The setup preflight checks ownership and permissions before sudo. The
installed helper uses isolated system Python, verifies its import trees, and
uses native system commands with a fixed environment. Keep the administrator
installation in place through managed SSH removal with `--no-ssh`.

### Configure SSH access

When SSH is enabled without explicit access flags, setup automatically enables
private IPv4 LAN access for the selected T3, Sunshine, and SSH firewall scopes.
LAN discovery runs on each setup and refresh, so a roaming workstation follows
its current private IPv4 network. The private selection record saves the
resolved SSH boolean; explicit `--access-source` values persist. Use
`--access-source PRIVATE_IPV4_OR_CIDR` to specify private IPv4 sources, or
`--no-lan-access` / `--no-access-source` to disable automatic discovery.
Password modes still require explicit private access flags; automatic LAN
discovery does not satisfy that requirement. No public IPv4 or IPv6 SSH grants
are created, and unrelated firewall rules are retained. Before changing
firewall policy, setup requires an active SSH client source (when present) to
be one of the selected private IPv4 sources; otherwise setup stops without
changing the firewall. Connect from an allowed private IPv4 address and avoid
switching from a public or IPv6 SSH path during the handover.

Adopting an active or enabled stock SSH service into key-only mode requires a usable
`authorized_keys` file for the existing account. If it is missing or unsafe,
setup refuses adoption to avoid locking out the account unless a password mode
or enrollment window is explicitly selected. A new key-only service
does not require a key before setup: explicitly open a temporary password
bootstrap window to enroll one. It listens on port 2222 for 1–60 minutes, uses
the existing account password, and stores no password. Verify the host
fingerprint through a trusted local channel, copy the key, then test ordinary
port-22 access before the window expires:

```fish
basaltw setup agent_cachyos localhost --ssh --ssh-password-window 15 --lan-access --codex
ssh-copy-id -p 2222 USER@WORKSTATION_PRIVATE_IP
ssh USER@WORKSTATION_PRIVATE_IP
```

The temporary root service has a one-shot runtime, bounded stop, and no restart
policy. Reboot or expiry stops its process group and closes active sessions;
the refresh record never saves or replays a password window. Persistent
password authentication for the existing account remains enabled until
the next full setup unless `--ssh-password-auth` is selected again. Full setup
intentionally defaults to key-only authentication even for an existing managed
password service. An ordinary `refresh` preserves a saved persistent password
selection; use `--no-ssh-password-auth` or `--no-ssh` to clear it. Password mode
requires explicit `--lan-access` or private `--access-source` on each request.
`--no-ssh` also clears password selection. A refresh that includes a fresh
window starts a new one-shot window; an ordinary refresh does not recreate one. Do not combine
persistent password authentication with a temporary password window.

Lockdown restarts only the port-22 listener, preserving established SSH
sessions, including sessions authenticated with a password. Legacy managed units
first receive the same session-preserving stop policy before authentication
changes. Existing sessions are not revoked. Enroll and test a key in a second
connection before closing your password session; new password logins stop
working after lockdown. Temporary port-2222 sessions still close on expiry or
ordinary refresh.

To repeat a successful selection while opening a fresh 15-minute enrollment
window, preserve the selected tools and explicitly select LAN access for this
request:

```fish
basaltw refresh --ssh --ssh-password-window 15 --lan-access
```

The duration is used for this refresh only and is never saved. A saved refresh
record containing a password window is rejected. An ordinary refresh closes
any active bootstrap window; its port-2222 allow rule may remain until refresh
removes it, but no listener remains after expiry.

Remote setup is useful when a controller is separate from the workstation and
the target already has reachable SSH and a non-root desktop account:

```fish
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --dry-run --codex
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --codex
```

The controller does not create accounts, enable KDE autologin, or copy
credentials. Setup uses the existing account's sudo access for privileged
steps. Selected GUI tools that require the user's session still need that user
bus/session. Remote setup verifies CachyOS on the target before applying
profile steps. Its dry-run opens no SSH connections. Use `--key PATH` (or
`--ssh-key PATH`) for a controller identity; it is not copied to the target.
Remote setup requires Python 3 and the existing SSH service before upload.
The Debian `--restart-if-needed` / `--wait-for-restart` workflow is unsupported.

Uploaded source remains in the user's private
`~/.local/share/basaltwater-cachyos-remote/runtime` so selected service helpers
survive temporary upload cleanup and ordinary installer updates. New targets
receive `~/.local/bin/basaltw`; existing launchers are preserved. A launcher
using this source snapshot is updated by repeating setup from the controller;
it cannot use channel upgrade or `refresh`. Install through the local installer
when channel-based upgrades and local refresh are wanted. See
[remote development](cachyos-development.md) for commissioning details.

## Optional webhook notifications

Add webhook targets to your initial setup selection to receive its completion
or failure result. For example, after installing the launcher:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --codex \
  --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

CachyOS supports only `webhook` targets; `mailbox` is rejected before setup
actions. Repeat `--notify webhook URL` for multiple receivers. Delivery uses
Basaltwater's [schema-version-2 JSON payload](../services/notifications.md#webhook-api),
bounded retries, and optional HTTPS fragment-carried bearer token. The payload
identifies this workstation by its hostname. A Basaltwater web panel's full
sender link can be used as the URL; quote it in fish and treat it as a credential.

`normal` and `verbose` send successful and failed setup results. `warning` and
`error` send failures; `off` suppresses delivery while retaining targets. Dry
runs send nothing. Invalid options are rejected locally without sending an
event. Validated setup preflight or step failures send the failing phase and
exception type; inspect local output for details. Delivery failure prints a
warning and preserves the setup outcome.

HTTPS verifies receiver certificates and hostnames by default. For a private
CA, add trust on the workstation or supply a sender-local PEM path with
`--notification-ca-file`. Notifications need outbound access
only; no listener, mail transport, or notification timer is installed. The
CachyOS integration sends setup results, including those from refresh; it does
not install the Debian maintenance/security notification jobs or monitor Sunshine.

Successful setup saves targets and policy in the private local selection and
receipt for later refresh. Displayed refresh commands redact webhook URLs.
Setup also writes a private `last-notification.json` beside the selection,
recording whether delivery succeeded, failed, or was suppressed by policy.
It contains no webhook URLs or tokens. `basaltw local cachyos-doctor` reads this
evidence without sending a test event. Receiver acceptance does not verify
downstream processing; older setups without this record remain unverified.
For an existing setup, see [notification maintenance](cachyos-maintenance.md#webhook-notifications).

## Codex-only agent setup

No coding provider is installed by default. Select one or more with
`--codex --claude --opencode`. GitHub CLI (`gh`) remains selected for
GitHub operations; `--no-agent-tool gh` omits it.
For Codex with T3 desktop, use:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --codex
```

Add `--no-agent-tool gh` if you also want to omit GitHub CLI. Codex and T3
desktop do not require `--node`. Development projects may still need it.

Missing Codex installations use the [official standalone installer](https://learn.chatgpt.com/docs/codex/cli).
Reruns update recognized standalone installations at `~/.local/bin/codex`.
System packages, npm links, and version-manager paths remain with their existing
manager; setup reports the executable it retains. First installs and updates
use the same desktop-account environment, ignoring inherited installer target,
daemon-selection, and release-pin variables.

Existing `~/.codex/config.toml`, models, permissions, MCP servers, plugins, and
credentials are preserved. Setup does not create a replacement Codex config or
copy credentials from another account. A custom `CODEX_HOME` is rejected before
setup changes anything because this profile manages the standard `~/.codex`
location. An explicit choice of another provider with `--no-agent-tool codex`
leaves that custom Codex installation unmanaged.

Readiness runs `codex login status` without printing its output. A failed check
produces an actionable warning without prompting or replacing credentials.
Inspect `codex login status` locally and use `codex login` if needed. Codex
supports file and OS credential stores and refreshes ChatGPT tokens during
normal use; this profile installs no separate authentication refresher.
See [Codex authentication](https://learn.chatgpt.com/docs/auth).

Verify a real Codex task after login; local login status does not test provider
access or T3's GUI environment. Standalone update and rollback behavior is
documented in [maintenance](cachyos-maintenance.md#codex-updates).
