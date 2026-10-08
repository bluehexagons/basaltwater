# Set up a CachyOS coding workstation

`agent_cachyos` adds a small coding stack to an **already installed x86-64
CachyOS KDE Plasma workstation**. It uses the existing desktop account and is
intended for bare metal machines, including workstations with dedicated GPUs.
It does not provision CachyOS VMs or containers and does not extend the
general server profiles. KDE capture/input is available through explicitly
started, user-approved [native desktop automation](CACHYOS_DESKTOP.md).
Managed Playwright remains deferred.

Support targets the **latest fully updated CachyOS rolling release**, not older
ISO defaults or generic Arch installations. Update through CachyOS's normal
full-system update workflow before setup. The current KDE baseline uses Plasma
Wayland and Plasma Login Manager; Shelly supplies the default package-manager
GUI and CLI. Basaltwater uses that CLI for explicitly selected AUR software and
leaves the desktop, login manager, DNS, drivers, and distro update services under
CachyOS management. See the upstream [January](https://blog.cachyos.org/blog/2601-january-release/),
[June](https://cachyos.org/blog/2606-june-release/), and
[August 2026 release notes](https://blog.cachyos.org/blog/2608-august-release/)
for these defaults; do not infer current packages from an older installation ISO.

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
' sh --local-setup agent_cachyos \
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
' sh --local-setup agent_cachyos \
  --t3code-desktop --node --python --git-lfs
```

This installs the launcher, Codex, GitHub CLI, T3 desktop, and the selected
project tools. Omit `--node --python --git-lfs` for just the default coding tools
and T3 desktop; add `--no-agent-tool gh` to omit GitHub CLI. A missing
`t3code-bin` is installed using CachyOS's default Shelly CLI and may prompt for
package review or sudo. T3 Code starts automatically at subsequent KDE logins.

For an installer-wide preview, add `--plan` before `--local-setup` in your
chosen block. This does not install packages, download the repository, or
change the launcher. Keep installer options such as `--channel dev` before
`--local-setup`, and setup options after it. Adding `--dry-run` after
`--local-setup` previews only the final setup phase; it still installs the
launcher and prerequisites.

After setup, open a new terminal, complete `codex login` and `gh auth login`,
then open T3 Code from KDE and verify a Codex thread and terminal. If T3 cannot
discover Codex, set its provider **Binary path** to the absolute path printed
by setup, typically `/home/USER/.local/bin/codex`. Use
**Settings → Connections** for desktop connection settings.

## Pick the options you need

Append options to `--local-setup agent_cachyos` in the installer command, or to
`basaltw setup agent_cachyos localhost` after the launcher is installed.

| Need | Options |
| --- | --- |
| Extra agents | `--agent-tool opencode`, `--agent-tool claude`; repeatable. Defaults are `gh,codex`. Use `--no-agent-tool NAME` to omit a default for this run. |
| Node, Python, Go, or Git LFS | `--node`, `--python`, `--go`, `--git-lfs` |
| Native game and Animator development | `--game-dev`, `--node-versions` ([Antistatic workflow](CACHYOS_GAME_DEVELOPMENT.md)) |
| Godot, media, or graphics diagnostics | `--godot`, `--av-tools`, `--gl-tools` (drivers are never installed) |
| Gaming and streaming | `--gaming`, `--sunshine`, `--moonlight` |
| Creative applications | `--obs`, `--blender`, `--kdenlive`, `--krita`, `--gimp`, `--inkscape`, `--scribus`, `--shotcut` |
| Audio, CAD, or electronics | `--audacity`, `--lmms`, `--ardour`, `--freecad`, `--kicad` |
| Remote desktop or diagnostics | `--remmina`, `--sysadmin-tools` |
| Materials, removable media, publishing | `--material-maker`, `--etcher`, `--butler`, `--steamcmd` ([packages and post-install checks](CACHYOS_SOFTWARE.md)) |
| Repository workspace | `--repo HTTPS_URL` (repeatable), `--agent-workspace /absolute/path` (default `~/repos`) |
| T3 Code desktop | `--t3code-desktop`: install or retain `t3code-bin` and enable KDE login startup; mutually exclusive with `--web-interface` |
| T3 Code web service | `--web-interface t3code`, then use the T3 Connect flow below; optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Setup plan only | `--dry-run` (use installer `--plan` before `--local-setup` for a full preview) |
| Restrict inbound workstation access | `--lan-access`, or `--access-source PRIVATE_IP_OR_CIDR`; `--no-lan-access` closes managed access |
| Setup-result webhooks | `--notify webhook URL` (repeatable), `--notification-level LEVEL`, `--notification-strict-https` |

Examples:

```fish
# Game and media workstation with an additional coding agent
basaltw setup agent_cachyos localhost \
  --agent-tool opencode --node --python --git-lfs --godot \
  --av-tools --gl-tools

# Gaming and game streaming on the trusted LAN
basaltw setup agent_cachyos localhost --gaming --sunshine --moonlight --lan-access

# Preview a plan without installing packages or changing files
basaltw setup agent_cachyos localhost --node --python --dry-run
```

Application options install native packages from the configured CachyOS
repositories, except the explicit AUR options `--t3code-desktop`,
`--material-maker`, `--butler`, and `--steamcmd`. No Flatpak packages or graphics
drivers are installed. Most application selections only install packages;
Sunshine's startup and conditional encoder configuration are described below. See the
[creative and publishing software guide](CACHYOS_SOFTWARE.md) for all issue #106
software, source policies, and post-install checks.
`--gaming` selects CachyOS's gaming meta-packages;
`--sunshine` installs the native host plus VA-API diagnostics, enables its user
service at KDE login, and starts it during setup. Quit Sunshine normally from
its tray menu when you want it stopped for the session. `--moonlight` installs
the client. Add `--lan-access` or explicit `--access-source` values for restricted
inbound access, then configure and pair Sunshine in its local web UI.
Sunshine supports Intel, AMD, and NVIDIA GPUs; installing its package does not
verify capture or hardware encoding. See the
[Sunshine checks](CACHYOS_MAINTENANCE.md#sunshine-service-and-encoder-checks) before pairing.

The T3 web service selects Node automatically; both T3 modes require Codex,
Claude, or OpenCode. Python
is also installed when needed for native Node module builds. Existing
version-manager runtimes are retained when their commands are on PATH.
Readiness runs version checks for all selected language commands: Node, npm,
and pnpm for `--node`, and Python and uv for `--python`. A missing or broken
companion tool makes setup incomplete even if the main runtime works.
On CachyOS, `--av-tools` includes FFmpeg, ImageMagick, and ExifTool;
`--gl-tools` includes Mesa/Vulkan diagnostics and apitrace.

## Optional webhook notifications

Add webhook targets to your initial setup selection to receive its completion
or failure result. For example, after installing the launcher:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs \
  --notify webhook 'https://hooks.example.net/infra' \
  --notification-level normal --notification-strict-https
```

CachyOS supports only `webhook` targets; `mailbox` is rejected before setup
actions. Repeat `--notify webhook URL` for multiple receivers. Delivery uses
Basaltwater's [schema-version-2 JSON payload](NOTIFICATIONS.md#webhook-api),
bounded retries, and optional HTTPS fragment-carried bearer token. The payload
identifies this workstation by its hostname. A Basaltwater web panel's full
sender link can be used as the URL; quote it in fish and treat it as a credential.

`normal` and `verbose` send successful and failed setup results. `warning` and
`error` send failures; `off` suppresses delivery while retaining targets. Dry
runs send nothing. Invalid options are rejected locally without sending an
event. Validated setup preflight or step failures send the failing phase and
exception type; inspect local output for details. Delivery failure prints a
warning and preserves the setup outcome.

HTTPS accepts self-signed certificates by default, matching other Basaltwater
senders. `--notification-strict-https` verifies the receiver certificate and
hostname using the normal CA trust store. Notifications need outbound access
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
For an existing setup, see [notification maintenance](CACHYOS_MAINTENANCE.md#webhook-notifications).

## Codex-only agent setup

Codex is the only coding agent installed by default. GitHub CLI (`gh`) is also
selected for GitHub operations; Claude and OpenCode require explicit flags.
For Codex with T3 desktop, use:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop
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
documented in [maintenance](CACHYOS_MAINTENANCE.md#codex-updates).

## T3 Code desktop

The desktop installer above selects the
[upstream-listed Arch package](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md).
If you installed only the launcher, apply your first desktop selection with:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop
```

The flag is currently supported only by `agent_cachyos`, not Debian profiles or
generic Arch installations. If `t3code-bin` is missing, setup runs
`shelly install aur t3code-bin` as the desktop user, with interactive package
review, build, and sudo prompts. Shelly is included in current CachyOS; installing
`paru` or `yay` first is unnecessary. Only when Shelly is absent does setup look
for `paru`, then `yay`. These are optional alternatives, not an older-CachyOS
support target. If all three are absent, preflight explains how to restore Shelly
with `sudo pacman -S --needed shelly` on an already updated system.
Preflight also verifies that the selected helper executable belongs to an
installed pacman package. Remove or rename an unowned executable shadowing
Shelly, paru, or yay in your PATH before retrying.

Setup preserves Shelly's configured AUR source and review policy. A failed or
cancelled installation stops setup before disabling the working web service;
it does not retry through another helper or bypass Shelly's policy. An installed
package is retained without requiring any AUR helper; update it through your
normal AUR workflow. Setup checks package metadata and executable ownership
without launching Electron. See [Shelly's CLI reference](https://www.seafoam-labs.org/shelly-alpm/docs/cli-reference/)
for its native AUR commands.

Before invoking Shelly, setup creates its missing AUR cache directories as your
desktop user. This avoids a Shelly 3.1.6 first-install path that creates the cache
after elevation, then runs Git as the unprivileged user. Preflight reports an
existing inaccessible or incorrectly owned cache before package installation.
Both `~/.cache/Shelly` and an absolute `$XDG_CACHE_HOME/Shelly` are checked,
because privilege elevation may discard the XDG override. Existing cache contents
and permissions are preserved; setup does not recursively repair ownership.

Basaltwater installs the selected provider CLIs, workspace, and T3 agent skill.
It preserves T3 settings, history, login credentials, and desktop launchers.
Desktop setup protects the default `~/.t3` state directories with private
permissions, retaining their contents. Unsafe symlinks or ownership stop setup.
Setup prints provider executable paths; if a KDE-launched T3 cannot find a
provider, use its **Binary path** setting. Verify a thread and terminal in the
app; a terminal CLI check does not establish GUI provider discovery. Desktop
mode does not require the separate Node/npm runtime used by the web service.

Desktop setup enables T3 Code at KDE login using a managed
[freedesktop autostart entry](https://specifications.freedesktop.org/autostart/latest/)
at `~/.config/autostart/basaltwater-cachyos-t3.desktop` (or under an absolute
`$XDG_CONFIG_HOME`). It launches the native `/usr/bin/t3code` only after login
and skips startup if that executable has been uninstalled. Setup itself does
not launch another app. A normal quit leaves T3 stopped until the next login.
Manage startup in KDE's **System Settings → Autostart**; a desktop setup or
refresh re-enables the managed entry. Custom files or symlinks at its destination
are preserved and reported before package changes. Switching successfully to
web mode removes this entry; a failed web activation preserves it. See
[login-startup maintenance](CACHYOS_MAINTENANCE.md#t3-desktop-login-startup).

Use the app's **Settings → Connections** for desktop pairing or T3 Connect,
when supported by the installed version. The managed web-service commands below
target a separate environment and should not be used to configure the desktop.

For helper errors, see [AUR download failures](CACHYOS_MAINTENANCE.md#aur-download-failures).
For an established installation, see [switching modes](CACHYOS_MAINTENANCE.md#switch-modes-on-a-later-setup).

## T3 Code web service: host locally or on a trusted LAN

T3 is an optional, user-owned systemd service. It runs as the logged-in desktop
user with provider credentials from that account. By default it listens only on
`127.0.0.1:3773`; selecting a specific private IPv4 address enables clients on
that LAN. Public, wildcard, and IPv6 bind addresses are rejected by this
profile.
Private LAN binds must be in RFC 1918 space (`10/8`, `172.16/12`, or
`192.168/16`); reserved/documentation and link-local addresses are rejected.
`localhost` is normalized to `127.0.0.1`. A custom web port requires the web
interface flag; desktop listeners are configured in the desktop app.

### Install the local service

Add `--web-interface t3code` to the [quick-start installer](#quick-start),
or configure the default loopback service with the installed launcher:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code
systemctl --user status basaltwater-cachyos-t3.service
```

The runtime is under `~/.local/share/basaltwater/cachyos-t3/releases`, and the
unit is `~/.config/systemd/user/basaltwater-cachyos-t3.service`. The stable
`~/.local/share/basaltwater/cachyos-t3/bin/t3` link selects the current release
for pairing and Connect commands. Setup validates the CLI, a disposable native
PTY shell, the generated unit, and HTTP UI reachability. Provider login and a
real coding thread still need verification. Service output passes through a
credential filter before systemd records it in the journal, so headless startup
tokens, pairing URLs, and QR rows are not persisted. HTTP 200 alone is not
backend health: an unknown route such as `/api/health` can return the frontend HTML.
If port 3773 is busy, rerun with another port from 1024 through 65535.

### T3 Connect

T3 Connect is the cloud access option. It is separate from direct LAN pairing:
the setup installs the T3 CLI and service, while you authorize the workstation
with your T3 account once from the desktop session. Keep the default loopback
bind when using T3 Connect; the managed relay expects the server's loopback
origin. Use `connect link` instead of `connect`: the latter may offer to install
a second upstream `t3code.service`, while Basaltwater already owns this unit.
Run:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect link --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
systemctl --user restart basaltwater-cachyos-t3.service
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" connect status --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

Follow the browser sign-in flow printed by `connect link`. Then sign in to the
same T3 Connect account on the other desktop, web, or mobile client and choose
this environment. Run `connect unlink` to disable cloud exposure while keeping
the login, or `connect logout` to remove the login. T3 Connect does not require
LAN firewall rules or router forwarding, but the host must remain powered on.
The profile does not automatically enable systemd lingering; if the service
must stay available after logout, enable it deliberately as the desktop user:

```fish
sudo loginctl enable-linger "$USER"
```

Use direct LAN pairing below when clients should connect to the workstation's
private address. Treat that as a separate access mode from T3 Connect unless
the installed T3 release documents support for combining the two binds.

### Pair this workstation or another device

Generate a fresh native T3 pairing link with the managed runtime:

```fish
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" pair --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

The command prints a QR code, a `Pairing URL`, and a token. Treat the URL and
token as credentials and use the link once. This explicit pairing command
returns the secret to the local caller; avoid saving its output in shared logs.
Paste the complete URL into the T3 desktop app at
**Settings → Connections → Add environment**, or open it in a browser. The bare
`http://127.0.0.1:3773` address redirects to T3's pairing page; it is not the
pairing link itself.

To pair a browser, desktop app, or phone on another system, bind T3 to the
workstation's private LAN address. Find that address with `ip -4 addr`, then
choose that address in your first web-service setup:

```fish
basaltw setup agent_cachyos localhost --web-interface t3code \
  --web-interface-host 192.168.1.50 --web-interface-port 3773
systemctl --user status basaltwater-cachyos-t3.service
"$HOME/.local/share/basaltwater/cachyos-t3/bin/t3" pair --base-dir "$HOME/.local/share/basaltwater/cachyos-t3/data"
```

Open the generated URL on the other device, or paste it into its T3 desktop
app. The URL will contain `192.168.1.50`; a loopback URL only works on the
workstation itself. Add `--lan-access` or `--access-source` to manage restricted
UFW access, and use a static or reserved address. Basaltwater does not provide
the VM pairing broker or configure a gateway.

### Optional workstation firewall

If you need LAN access, include its policy in your initial selection:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --lan-access
```

This explicitly opts into UFW management. It installs UFW if missing, enables
its service now and at boot, enables
IPv4/IPv6 filtering, denies incoming and routed traffic by default, and allows
outgoing traffic so normal browsing, downloads, provider access, and game clients
continue working. Existing unrelated rules are retained; this is not a firewall
reset. Active firewalld/nftables services or disabled UFW IPv6 support stop
preflight with remediation instead of combining incompatible policies.

`--lan-access` trusts the single private IPv4 subnet on the default-route
interface. Ambiguous/VPN/multiple-network cases require `--access-source`, which
overrides discovery. Explicit sources must be RFC1918 IPv4 addresses or subnets;
public sources, IPv6 sources, and all-network wildcards are refused. Rerunning
refresh re-evaluates the current LAN; use explicit device addresses when you do
not want that behavior on a roaming workstation.

Only selected T3 TCP and Sunshine streaming/pairing ports are allowed from those
sources. Sunshine's administration port 47990, legacy RDP 3389, and T3 UDP remain
blocked remotely. The default T3 port and standard Sunshine ports receive deny
guards or replace equivalent broad allow rules, including IPv6 rules. Rule
precedence is verified; an earlier overlapping unmanaged allow stops setup for
manual review. Previous custom port guards are retained.
New guards are installed before old managed allows are
removed, so a failed update can leave access closed; rerun the same selection to
recover. Existing established connections and custom UFW before-rules are not
revoked. Unrelated ports (including old development-server rules) require review.

`--no-lan-access` with no explicit sources closes these managed ports remotely.
Omitting all access flags leaves the firewall alone. This does not change T3's
bind address, start Sunshine, enable SSH/RDP, or open KDE Connect/Steam hosting
ports. Desktop T3 uses port 3773 here; nonstandard desktop ports need manual
firewall configuration. Sunshine with custom ports also needs manual rules.
Managed T3 ports cannot overlap the protected RDP/Sunshine TCP ports.
Inspect `sudo ufw status verbose` and verify access from the intended client.

The service follows the desktop user's session. Setup validates its CLI,
native terminal dependency, generated unit, and HTTP UI before reporting
success. Check a real thread and terminal after pairing. For service errors,
runtime updates, and activation recovery, use the
[maintenance reference](CACHYOS_MAINTENANCE.md#web-service-updates-and-recovery).

## Package and repository behavior

- Package state is checked with `pacman -Q`; missing packages use
  `pacman -S --needed`. Setup does not refresh package databases or perform a
  system upgrade. Use CachyOS's normal update workflow first, and never use
  `pacman -Sy` as a repair for a partial upgrade.
- Package installation and pruning old pacman downloads may prompt for sudo.
  Agent CLIs, the T3 runtime, user-cache cleanup, and repository checks are
  otherwise noninteractive. No update or cleanup timers are installed.
- Cache cleanup runs after tool readiness. If cleanup fails, setup reports an
  incomplete result and returns nonzero; installed tools are retained. Resolve
  the cleanup error and rerun setup. This profile has no automatic cache retry.
- This local profile saves a private successful selection for later maintenance,
  rather than a generic host configuration for `patch`, `deploy`, or `cmd`.
- If a CachyOS mirror or DNS lookup fails, fix the resolver or mirror through
  CachyOS's normal maintenance workflow and retry your selected installer.
- `--repo` clones only a missing repository. An existing destination must be a
  Git repository with the requested origin and must be writable by you; setup
  does not delete or repair conflicting directories.

## Cleanup during setup

Every setup run performs cleanup after tool readiness. It installs
`pacman-contrib` for the distro's [paccache version selection](https://man.archlinux.org/man/paccache.8),
but does not enable `paccache.timer` or install another scheduled service.
Current CachyOS already offers cache cleanup in Shelly's Utilities page, and
`paccache` is available through its repositories. This setup pass adds the
conservative retention rules below; it does not replace or reconfigure existing
distro cleanup or update tools. AUR build caches, including Shelly's, stay intact.

- **Package downloads:** retain the newest three cached versions of each
  package, the currently installed version, and any archive accessed or
  modified within 30 days. Only recognized, root-owned regular archives and
  their signatures directly in `/var/cache/pacman/pkg` are eligible. Setup
  previews without sudo, then rechecks candidates before removal if needed.
  Package transaction locks defer cleanup. Private download directories,
  partial downloads, symlinks, custom cache locations, and AUR build trees are
  preserved. Packages are never uninstalled, including orphaned packages.
- **Developer caches:** reuse the shared user-cache policies for npm/npx, pip,
  uv, Go, Codex, and Electron downloads. Only known rebuildable caches are
  pruned; size/age limits and free-space checks control larger evictions.
  Active tool/process checks defer cleanup where applicable. Codex temporary
  files expire after seven days when Codex is idle; configuration, login files,
  sessions, databases, plugins, and repositories are preserved.
- **Agent releases and logs:** retain the current, rollback, and running Codex
  releases. T3 numbered log rotations are bounded to 14 days and 256 MiB per
  environment, covering both desktop `~/.t3` and the isolated managed web
  directory. Current logs and T3 databases are preserved.

For a read-only package-cache inventory from this checkout:

```fish
python3 common/cachyos_cleanup.py --dry-run
```

The helper reports candidate archive/signature counts. Setup's `--dry-run`
shows the cleanup steps without inventorying or changing the machine. Cleanup
errors make setup incomplete and request a rerun; installed tools remain in
place. Deferred cleanup can run on a later setup invocation.

## Skills, diagnostics, and boundaries

Codex and OpenCode receive the CachyOS workstation, workspace, desktop, and (when T3 is
selected) T3 skills under `~/.agents/skills`. Standard VM, XRDP, gateway,
browser-automation, and Godot-web skills are not installed. Personal skills are
preserved. The desktop skill covers autonomous Blender/Inkscape and other
application edits, scripting, editable sources and verified exports. The manifest
uses direct native launch commands instead of Debian XRDP commands.

Selecting a supported desktop application installs Python GObject, AT-SPI,
GStreamer, its base/PipeWire plugins and GTK3 prerequisites. Setup starts no
control helper. From KDE, use `basaltw desktop --native doctor`, then
`basaltw desktop --native start`; the owner must select one monitor and allow
keyboard/pointer access in KDE's portal dialog. `handoff` provides human
pause/resume/stop/revoke controls. `stop` closes only automation, preserving KDE
and applications. The default lifetime is 15 minutes, with explicit renewal up
to eight hours. `start --remember` opts into a reusable portal grant where KDE
supports it, after initial owner approval. Human pause persists across restored
sessions; `revoke` removes saved access. There is no login autostart.
See [native control and limitations](CACHYOS_DESKTOP.md).

The read-only desktop doctor reports package versions, user-bus sockets, and
PipeWire, WirePlumber, the `t3code-bin` package, and managed/upstream T3 unit state.
An inactive managed T3 service is expected in desktop mode:

```fish
basaltw local cachyos-doctor
basaltw local cachyos-doctor --json
```

It does not install, launch, capture, open listeners, or write a report. A
successful report proves only the observations it lists; it does not prove GPU
rendering, desktop input, provider authentication, or an end-to-end thread.
The command runs without the Debian maintenance confirmation, including with
`--json` in a noninteractive session. Browser observations recognize Chromium,
Firefox, Brave, LibreWolf, and Cachy Browser native packages. An absent optional browser
package does not mean there is no usable browser; custom installations are not
inventoried, and the doctor does not launch a browser to test it.

The doctor loads the validated saved selection: a missing selected T3 package,
inactive selected web service, or broken selected CLI is reported as failed.
Desktop selections also check the native T3 executable and managed login startup;
an active managed web service in desktop mode or active upstream T3 service
alongside a selected Basaltwater T3 mode is reported as a conflict.
Selected media/graphics, desktop, streaming, sysadmin, and publishing commands
also receive PATH presence checks without launching them. Remmina's selected
protocol/secret dependencies are checked as packages. Command presence does not
verify runtime behavior or executable ownership; setup performs its own readiness checks.
Missing unselected browsers remain informational. It also reports failed units,
root and home filesystem capacity, booted kernel module presence,
firmware/encryption observations,
non-loopback listeners, and potentially broad saved UFW input rules, including
port ranges and Sunshine UDP. It distinguishes broad allows preceded by covering
unconditional denies from uncovered or unparsed rules needing review. It never
elevates privileges; saved rules do not prove effective packet filtering.
When Sunshine is selected, it checks the user service and, if `vainfo` is installed,
VA-API encoding profiles without starting capture. A crashed Sunshine service is
reported as failed; an inactive service may reflect an intentional quit.
`startup.sunshine` separately checks persistent login enablement, so an enabled
service quit for the session is distinguished from disabled or temporary startup.
`health.mirrors` separately identifies a failed CachyOS mirror refresh service
and points to its local journal and DNS/connectivity checks. It does not contact
mirrors, retry the service, or verify mirror freshness. See
[mirror recovery](CACHYOS_MAINTENANCE.md#mirror-refresh-failures).
Update observations use existing pacman metadata, which may be stale and does not
cover AUR releases. Setup prints the same host observations before installing
packages.

To check your first setup, record its CachyOS, Plasma, kernel, GPU/driver,
and tool versions; authenticate an agent; complete a small edit/test task and
disposable worktree; and, when selected, test a T3 thread, terminal command,
logout/login, and the intended loopback or LAN listener. Test Godot,
Vulkan/OpenGL, audio, and media on the actual GPU. Repeat
the relevant checks after a normal CachyOS update and record AMD and NVIDIA
results separately.

For contributors, `plugins/cachyos.py` owns composition, `lib/cachyos.py` owns
the local support boundary, and `common/cachyos_steps.py` owns target-side
operations; `common/cachyos_t3.py` owns staged T3 activation and recovery.
`common/cachyos_cleanup.py` owns package-cache pruning. Arch package inputs use
`lib.validation.validate_arch_package_name`, which accepts the distro's
underscore and `@` characters without loosening APT validation. The
CLI routes directly to the CachyOS runner rather than the SSH
host lifecycle. Keep additions explicitly allowed and independently tested;
mock pacman, sudo, downloads, service operations, and hardware probes.

## Later maintenance

For an established setup, use the [maintenance reference](CACHYOS_MAINTENANCE.md)
for updates, saved selections, mode changes, and recovery.

### Upgrade and repeat your last setup

See [saved setup and refresh](CACHYOS_MAINTENANCE.md#upgrade-and-repeat-your-last-setup).

### AUR download failures

See [helper diagnostics](CACHYOS_MAINTENANCE.md#aur-download-failures).

### Switch modes on a later setup

See [mode changes and data boundaries](CACHYOS_MAINTENANCE.md#switch-modes-on-a-later-setup).
