# CachyOS coding workstation

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

The current workstation's running stack has been tested through daily use and
the September 2026 audit. See the [qualification record](plans/CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md)
for that evidence and the separate fresh-install/recovery cases.

## Quick start

Finish the normal CachyOS installation first: update the system, configure KDE,
and install the appropriate GPU driver. Then open a terminal in your normal
desktop session and run this as yourself (without `sudo`):

```bash
(
  installer=$(mktemp) || exit
  trap 'rm -f -- "$installer"' EXIT
  curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
  sh "$installer" --local-setup agent_cachyos \
    --node --python --git-lfs
)
```

The package step may ask for your desktop user's sudo password. Keep the
terminal attached until setup finishes. This example installs the default Git,
ripgrep, build tools, GitHub CLI, and Codex plus Node.js, Python, and Git LFS.
The installer defaults to the `dev` channel while this profile is new.
The temporary download is removed when the command exits, including on failure.

The installer no longer migrates `infra_tools` user data. Any remaining old
installation must first use the [intermediate version](BASALTWATER_MIGRATION.md).
An existing `cachyos-t3` data directory at the default Basaltwater install path
is retained during reinstall.
Installer updates also carry forward managed `state`, `deployments`, and
`worktrees` directories.
If that path also contains other unmanaged files, move or resolve them before
rerunning the installer.

No account, password, group, sudo, provider, or Git identity changes are made.
Log in to providers through their normal commands when needed:

```bash
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

For Codex and the native T3 Code desktop app, add `--t3code-desktop` to the
[quick-start installer](#quick-start), or use the commands below once the
launcher is installed. Run as your normal KDE user, without `sudo`.

This selects Codex and GitHub CLI, installs or retains `t3code-bin`, and prepares
the workspace and managed skills. Claude and OpenCode are not selected.
`--node --python --git-lfs` supplies optional project tools; omit those flags
for just the default coding tools and T3 desktop. Add `--no-agent-tool gh`
to omit GitHub CLI too. A missing `t3code-bin` is installed using CachyOS's
default Shelly CLI and may prompt for package review or sudo; an installed package
stays on its current version. Setup preserves existing Codex/T3 settings and
credentials and runs the [bounded cleanup](#cleanup-during-setup).

With the launcher already installed, preview and then apply the same selection:

```bash
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs --dry-run
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs
```

Add `--plan` before `--local-setup` for an installer-wide preview with no
package installation, repository download, or launcher changes.
The direct setup preview makes no changes. Adding `--dry-run` to the shell
installer previews only its final setup phase; the installer still installs
the launcher and prerequisites. Keep installer options such as `--channel dev`
before `--local-setup`, and setup options such as `--t3code-desktop` after it.

After setup, open T3 Code from KDE and verify a Codex thread and terminal.
If the app cannot discover Codex, use the absolute provider binary path printed
by setup. Use the desktop app's **Settings → Connections** for its connection
settings. See [desktop mode and switching](#t3-code-desktop) for later reruns.

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
| T3 Code desktop | `--t3code-desktop`: install or retain the upstream-listed `t3code-bin` AUR package; mutually exclusive with `--web-interface` |
| T3 Code web service | `--web-interface t3code`, then use the T3 Connect flow below; optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Plan only | `--dry-run` |
| Restrict inbound workstation access | `--lan-access`, or `--access-source PRIVATE_IP_OR_CIDR`; `--no-lan-access` closes managed access |

Examples:

```bash
# Game and media workstation with an additional coding agent
basaltw setup agent_cachyos localhost \
  --agent-tool opencode --node --python --git-lfs --godot \
  --av-tools --gl-tools

# Gaming and game streaming
basaltw setup agent_cachyos localhost --gaming --sunshine --moonlight

# Preview a plan without installing packages or changing files
basaltw setup agent_cachyos localhost --node --python --dry-run
```

Application options install native packages from the configured CachyOS
repositories, except the explicit AUR options `--t3code-desktop`,
`--material-maker`, `--butler`, and `--steamcmd`. No Flatpak packages, graphics
drivers, or application configuration are installed. See the
[creative and publishing software guide](CACHYOS_SOFTWARE.md) for all issue #106
software, source policies, and post-install checks.
`--gaming` selects CachyOS's gaming meta-packages;
`--sunshine` and `--moonlight` install native host and client packages but do
not open firewall ports or create credentials. Configure and pair Sunshine in
its own web UI on a trusted network.

The T3 web service selects Node automatically; both T3 modes require Codex,
Claude, or OpenCode. Python
is also installed when needed for native Node module builds. Existing
version-manager runtimes are retained when their commands are on PATH.
Readiness runs version checks for all selected language commands: Node, npm,
and pnpm for `--node`, and Python and uv for `--python`. A missing or broken
companion tool makes setup incomplete even if the main runtime works.
On CachyOS, `--av-tools` includes FFmpeg, ImageMagick, and ExifTool;
`--gl-tools` includes Mesa/Vulkan diagnostics and apitrace.

## Codex-only agent setup

Codex is the only coding agent installed by default. GitHub CLI (`gh`) is also
selected for GitHub operations; Claude and OpenCode require explicit flags.
For Codex with T3 desktop, use:

```bash
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

For deliberate standalone updates outside setup:

```bash
basaltw agent update --tool codex --dry-run
basaltw agent update --tool codex --tools-only-readiness
```

The update checks successful version/help commands and keeps a complete package
snapshot for rollback, including adjacent sandbox and other runtime resources.
It retains the latest snapshot plus any selected or running snapshots; older
validated snapshots are pruned. Update records and backups are private under
`~/.local/state/basaltwater`. Concurrent Basaltwater updates are refused until
the current update finishes. `--tools-only-readiness` avoids gating CLI updates
on the generic VM host/T3 checks, which do not qualify this desktop profile.
Restart Codex/T3 provider sessions when convenient to use the new executable.
Verify a real Codex task in T3 after setup; local login status does not test API
access or the GUI's provider environment. If needed, set T3's Codex **Binary
path** to the absolute launcher path printed by setup, typically
`/home/USER/.local/bin/codex`; keep that stable path across updates.

## T3 Code desktop

Use this for an existing desktop installation or to install the
[upstream-listed Arch package](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
on this CachyOS workstation:

```bash
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
Setup prints provider executable paths; if a KDE-launched T3 cannot find a
provider, use its **Binary path** setting. Verify a thread and terminal in the
app; a terminal CLI check does not establish GUI provider discovery. Desktop
mode does not require the separate Node/npm runtime used by the web service.

Use the app's **Settings → Connections** for desktop pairing or T3 Connect,
when supported by the installed version. The managed web-service commands below
target a separate environment and should not be used to configure the desktop.

### AUR download failures

Shelly's generic source-download failure can hide the underlying Git error:
its [3.1.6 download implementation](https://github.com/Seafoam-Labs/Shelly-ALPM/blob/v3.1.6/Shelly.PackageManager/src/aur/manager.zig)
discards failed clone/pull output. This message alone does not establish a
network problem. Basaltwater now includes the helper's exit status, retry
command, and diagnostic pointers when installation fails.

First inspect the version, configured source, and cache ownership as your normal
desktop user:

```bash
pacman -Q shelly
shelly config get AurUrl
ls -ld -- "$HOME/.cache" "$HOME/.cache/Shelly"
# If you configured an absolute XDG_CACHE_HOME, inspect its Shelly directory too.
```

If the reported **Shelly directory itself** is root-owned, and it is your normal
cache directory rather than a symlink or shared location, repair just that
directory and retry setup:

```bash
sudo chown -- "$(id -u):$(id -g)" "$HOME/.cache/Shelly"
```

Use the actual path reported by preflight for a custom cache. Do not recursively
chown your home or delete the AUR cache; existing checkouts may contain edits.
If another parent directory or checkout has incorrect permissions, inspect it
separately. Setup does not assume every download failure is an ownership issue.

To expose Git's own network/TLS/proxy error without building or installing,
clone into a new temporary directory as your normal user. The URL below is for
the default Arch AUR: replace its base with your configured `AurUrl` if different,
so the check tests the same service as Shelly.

```bash
aur_probe=$(mktemp -d) &&
git clone -- https://aur.archlinux.org/t3code-bin.git "$aur_probe/t3code-bin"
```

This only downloads packaging files; do not execute them for diagnosis. A
successful clone tests access as your user, but does not rule out a Shelly cache,
elevation, dependency, or later application-download failure. Inspect Shelly's
session log at `/var/log/shelly.log` (may require sudo) or its unprivileged
fallback `${XDG_STATE_HOME:-$HOME/.local/state}/shelly/shelly.log`. The log may
still omit Git's discarded error. Resolve the reported cause, then rerun the
same full setup command. The existing managed web service stays running if
desktop installation fails.

### Switch modes on a later setup

`--t3code-desktop` and `--web-interface t3code` cannot be selected together.
Finish active work before switching:

```bash
# Web → desktop: install/verify the package, then disable the managed web service
basaltw setup agent_cachyos localhost --t3code-desktop

# Desktop → web: quit the desktop app first to free its listening port
basaltw setup agent_cachyos localhost --web-interface t3code
```

Desktop mode disables only the Basaltwater-owned user service and retains its
unit, runtime, and data. Web mode stages and starts the managed service again;
the desktop package stays installed. Omitting both flags leaves the current
mode alone. Setup never kills the desktop app or takes over an upstream service.

The web service uses `~/.local/share/basaltwater/cachyos-t3/data` explicitly,
separate from the desktop's default `~/.t3`. Switching modes does not copy,
delete, or merge their databases, projects, pairing identities, or Connect
credentials. Repositories can be opened in either environment.
Older Basaltwater web units used `~/.t3`: their next web setup starts a separate
environment in the new directory and reports this change. The old data remains
untouched in `~/.t3`; back it up and plan any history migration separately.
This separation also prevents database contention if the desktop is later opened
while the web service is running; see the
[upstream duplicate-backend report](https://github.com/pingdotgg/t3code/issues/6097).
A port conflict still requires quitting the desktop or choosing another web port.

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

```bash
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

```bash
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

```bash
sudo loginctl enable-linger "$USER"
```

Use direct LAN pairing below when clients should connect to the workstation's
private address. Treat that as a separate access mode from T3 Connect unless
the installed T3 release documents support for combining the two binds.

### Pair this workstation or another device

Generate a fresh native T3 pairing link with the managed runtime:

```bash
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
replace the example below and rerun setup:

```bash
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

Keep your full selection when enabling restricted LAN access:

```bash
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

If a previous checkout produced `has a bad unit file setting`, update
Basaltwater and rerun setup. Validate the generated unit with:

```bash
systemd-analyze verify "$HOME/.config/systemd/user/basaltwater-cachyos-t3.service"
```

The service follows the user session; lingering is not enabled. An existing
upstream `t3code.service` is refused without being stopped or adopted. Manage
that unit with its original installer before selecting either T3 mode. Preflight
checks effective systemd units in all user-unit search locations, as well as
local files, and refuses masks or unmanaged drop-ins on the Basaltwater unit.
Resolve those overrides before switching or updating. To stop this managed
service persistently:

```bash
systemctl --user disable --now basaltwater-cachyos-t3.service
```

Use `journalctl --user -u basaltwater-cachyos-t3.service` for startup errors.
The generic VM T3 pairing and update commands do not manage this unit. For a
deliberate runtime update, finish active work and rerun setup as yourself:

```bash
basaltw setup agent_cachyos localhost --web-interface t3code
```

Keep your selected host, port, and workspace options when rerunning. Each T3
setup stages `t3@latest` in a separate release directory, checks its CLI and
native terminal dependency, then validates the unit before stopping the old
service. Activation switches the CLI link and unit and starts the new runtime.
Even an unchanged T3 version is rebuilt and restarted so a Node upgrade does
not leave an incompatible native addon. Do not use `npm install --prefix` on
the managed root; it bypasses staging and can replace the stable CLI link.

Installation/validation failures leave the old service untouched. Activation
failures restore the previous runtime, unit permissions, and enabled/running
state. An interrupted activation leaves private recovery snapshots in
`~/.local/share/basaltwater/cachyos-t3/.activation`; the next setup retries
recovery before installing. If recovery is incomplete, retain that directory
and both runtimes while resolving the reported service error. Changes or
removals made to the unit or CLI link outside setup stop recovery and preserve
the snapshots for manual inspection.

Rollback covers runtime and service configuration, **not T3 database migrations**.
Back up application data before updates that may change its schema. Successful
updates retain the current and previous managed release, prune older marked
releases, and leave legacy npm files and unmarked directories alone. New files
created by the service use `UMask=0077`; existing personal data permissions are
not changed. Missing optional-provider warnings (for example, Claude on a
Codex-only installation) do not by themselves mean the selected provider failed.

See the upstream [T3 installation guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
and [remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
for current provider, client, and T3 Connect requirements.

## Reruns, updates, and repositories

### Upgrade and repeat your last setup

After a successful `agent_cachyos` setup, use this from your KDE terminal as
the same user, without `sudo`:

```bash
basaltw refresh --dry-run
basaltw refresh
```

`refresh` upgrades Basaltwater to the latest source on the selected channel
(`dev` follows `main`), then starts the updated code with the last successful
setup selection. Version/commit channels remain pinned, just as with
`basaltw upgrade`. Setup runs even if Basaltwater was already up to date.
The dry run displays the saved command and setup plan without fetching source,
upgrading, installing, or changing the saved selection.

The private record is `~/.local/state/basaltwater/cachyos/last-setup.json`.
The adjacent private `last-report.json` records completion time, source commit
and channel (when available), selected tool/package versions, and diagnostic
observations including warnings. It is informational, not replay input. A
receipt failure warns without discarding a successful saved selection.
It saves supported setup options, including provider selections/exclusions,
T3 mode, optional tools, repositories, workspace, and web bind/port. It does
not copy authentication files or save provider credentials. The record is
written only after all setup steps, including cleanup, succeed. A failed or
interrupted run and a dry run leave the previous successful selection intact.
Saved commands explicitly include or exclude every currently supported agent,
so changes to profile defaults do not select an unwanted agent during refresh.
Records written by older versions may omit optional-agent exclusions; repeat
your full setup command with the updated code to save those exclusions.

**First use after upgrading from an older checkout:** run `basaltw upgrade`,
then run your usual full setup command once. Older CachyOS runs did not save
their options, so refresh cannot recover them from installed packages. For
example, to establish the desktop selection:

```bash
basaltw setup agent_cachyos localhost --t3code-desktop --node --python --git-lfs
```

To add software without repeating your saved flags, use:

```bash
basaltw refresh --material-maker --etcher --butler --steamcmd --dry-run
basaltw refresh --material-maker --etcher --butler --steamcmd
```

Refresh accepts the supported CachyOS setup flags (`refresh --help` lists them).
Unspecified options are preserved. Boolean and single-value options override
saved values; repeatable repositories and access sources are added without
duplicates. Agent selections/exclusions override their saved opposite for that
provider. `--no-access-source` clears saved explicit sources; combine it with
`--no-lan-access` to close managed remote access. Selecting a T3 mode replaces
the saved mode; switching to desktop clears the old web bind/port.
Conflicting or invalid new options stop before upgrade. A successful setup
saves the combined selection; after a failed attempt, repeat the added flags
when retrying. Omitting an option does not remove it; software `--no-*` flags
stop managing a selection, without uninstalling it.

An older installed CLI cannot recognize newly added flags. Run `basaltw upgrade`
once first to acquire this refresh interface and any new options.
Alternatively, rerun `setup` with all desired options. That successful run
replaces the record rather than merging with earlier selections.
Refresh follows the same update behavior as setup: managed agent CLIs update,
web mode updates/restarts its service, and an installed T3 desktop package is
retained for your normal AUR update workflow. Finish active work first.

Missing or invalid saved options stop refresh before the source upgrade.
An upgrade failure prevents setup. If setup fails after a successful upgrade,
Basaltwater stays upgraded and returns setup's failure status; resolve the
error and repeat `refresh`, or run an explicit setup to change the selection.
On CachyOS this repeats only the local `agent_cachyos` profile. It does not
upgrade CachyOS itself or redeploy saved remote hosts. Debian also supports
refresh, using its existing target-side record and root setup runner; see
[refresh on Debian](COMMAND_LINE.md#refresh-this-machine).

### Package and repository behavior

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
- Reruns retain installed software, credentials, and repositories. Omitting an
  option does not uninstall it; existing repositories are never pulled, reset,
  or recursively chowned. Selecting the T3 web interface on a rerun updates and
  restarts its service; selecting desktop mode retains its installed AUR version.
- This local profile does not save a generic host configuration for `patch`,
  `deploy`, or `cmd`. Use `refresh` to repeat its private successful selection,
  or repeat all desired flags when running `setup` explicitly. The T3 mode
  marker does not replace the saved setup selection.
- `basaltw upgrade` updates Basaltwater itself. If a CachyOS mirror or DNS
  lookup fails, fix the resolver or mirror through CachyOS's normal maintenance
  workflow and rerun.
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

```bash
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
pause/resume/stop controls. `stop` closes only automation, preserving KDE and
applications. Sessions expire after 15 minutes and retain no saved portal grant.
See [native control and limitations](CACHYOS_DESKTOP.md).

The read-only desktop doctor reports package versions, user-bus sockets, and
PipeWire, WirePlumber, the `t3code-bin` package, and managed/upstream T3 unit state.
An inactive managed T3 service is expected in desktop mode:

```bash
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
Missing unselected browsers remain informational. It also reports failed units,
root capacity, booted kernel module presence, firmware/encryption observations,
non-loopback listeners, and potentially broad saved UFW rules. It never elevates
privileges; saved firewall rules do not prove effective packet filtering.
Update observations use existing pacman metadata, which may be stale. Setup
prints the same host observations before installing packages.

Before calling a workstation validated, record its CachyOS, Plasma, kernel,
GPU/driver, and tool versions; repeat setup; authenticate an agent; complete a
small edit/test task and disposable worktree; and, when selected, test a T3
thread, terminal command, logout/login, and the intended loopback or LAN
listener. Test Godot, Vulkan/OpenGL, audio, and media on the actual GPU. Repeat
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
