# CachyOS coding workstation

`agent_cachyos` adds a small coding stack to an **already installed x86-64
CachyOS KDE Plasma workstation**. It uses the existing desktop account and is
intended for bare metal machines, including workstations with dedicated GPUs.
It does not provision CachyOS VMs or containers and does not extend the
general server profiles. KDE automation and managed Playwright are deferred.

The setup is experimental until it has been exercised on the target hardware.
The repository has mocked setup tests; the hardware checks at the end of this
page still need to be run on each workstation class.

## Quick start

Finish the normal CachyOS installation first: update the system, configure KDE,
and install the appropriate GPU driver. Then open a terminal in your normal
desktop session and run this as yourself (without `sudo`):

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.basaltwater-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh &&
sh "$HOME/.basaltwater-install.sh" --channel dev --local-setup agent_cachyos \
  --node --python --git-lfs
```

The package step may ask for your desktop user's sudo password. Keep the
terminal attached until setup finishes. This example installs the default Git,
ripgrep, build tools, GitHub CLI, and Codex plus Node.js, Python, and Git LFS.
The `dev` channel is required while this profile is new; older release tags do
not contain it.

When the installer finds recent `infra_tools` user data, it runs the one-time
Basaltwater migration from the selected checkout before installing and starting
the CachyOS setup. Migration conflicts stop the installer with the reported
path or recovery instructions. An existing `cachyos-t3` data directory at the
default Basaltwater install path is retained during migration and reinstall.
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

## Pick the options you need

Append options to `--local-setup agent_cachyos` in the installer command, or to
`basaltw setup agent_cachyos localhost` after the launcher is installed.

| Need | Options |
| --- | --- |
| Extra agents | `--agent-tool opencode`, `--agent-tool claude`; repeatable. Defaults are `gh,codex`. Use `--no-agent-tool NAME` to omit a default for this run. |
| Node, Python, Go, or Git LFS | `--node`, `--python`, `--go`, `--git-lfs` |
| Godot, media, or graphics diagnostics | `--godot`, `--av-tools`, `--gl-tools` (drivers are never installed) |
| Gaming and streaming | `--gaming`, `--sunshine`, `--moonlight` |
| Creative applications | `--obs`, `--blender`, `--kdenlive`, `--krita`, `--gimp`, `--inkscape`, `--scribus`, `--shotcut` |
| Audio, CAD, or electronics | `--audacity`, `--lmms`, `--ardour`, `--freecad`, `--kicad` |
| Remote desktop or diagnostics | `--remmina`, `--sysadmin-tools` |
| Repository workspace | `--repo HTTPS_URL` (repeatable), `--agent-workspace /absolute/path` (default `~/repos`) |
| T3 Code desktop | `--t3code-desktop`: install or retain the upstream-listed `t3code-bin` AUR package; mutually exclusive with `--web-interface` |
| T3 Code web service | `--web-interface t3code`, then use the T3 Connect flow below; optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Plan only | `--dry-run` |

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

Except for `--t3code-desktop`, application options install native packages from
the configured CachyOS repositories. They do not install AUR or Flatpak packages, graphics drivers, or
application configuration. `--gaming` selects CachyOS's gaming meta-packages;
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

## T3 Code desktop

Use this for an existing desktop installation or to install the
[upstream-listed Arch package](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md)
on this CachyOS workstation:

```bash
basaltw setup agent_cachyos localhost --t3code-desktop
```

The flag is currently supported only by `agent_cachyos`, not Debian profiles or
generic Arch installations. If `t3code-bin` is missing, setup uses an existing
`paru` or `yay` helper as the desktop user, with interactive build/sudo prompts.
Install an AUR helper through CachyOS first if neither is available. An installed
package is retained; update it through your normal AUR workflow. Setup checks
package metadata and executable ownership without launching Electron.

Basaltwater installs the selected provider CLIs, workspace, and T3 agent skill.
It preserves T3 settings, history, login credentials, and desktop launchers.
Setup prints provider executable paths; if a KDE-launched T3 cannot find a
provider, use its **Binary path** setting. Verify a thread and terminal in the
app; a terminal CLI check does not establish GUI provider discovery. Desktop
mode does not require the separate Node/npm runtime used by the web service.

Use the app's **Settings → Connections** for desktop pairing or T3 Connect,
when supported by the installed version. The managed web-service commands below
target a separate environment and should not be used to configure the desktop.

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

This complete block installs the launcher and configures the default loopback
service:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.basaltwater-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh &&
sh "$HOME/.basaltwater-install.sh" --channel dev --local-setup agent_cachyos \
  --agent-tool gh --agent-tool codex --web-interface t3code \
  --web-interface-port 3773
```

For an already installed launcher, the equivalent setup is:

```bash
basaltw setup agent_cachyos localhost --web-interface t3code
systemctl --user status basaltwater-cachyos-t3.service
```

The runtime is under `~/.local/share/basaltwater/cachyos-t3/releases`, and the
unit is `~/.config/systemd/user/basaltwater-cachyos-t3.service`. The stable
`~/.local/share/basaltwater/cachyos-t3/bin/t3` link selects the current release
for pairing and Connect commands. Setup validates the CLI, a disposable native
PTY shell, the generated unit, and HTTP UI reachability. Provider login and a
real coding thread still need verification. HTTP 200 alone is not backend
health: an unknown route such as `/api/health` can return the frontend HTML.
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
token as credentials and use the link once. Paste the complete URL into the T3
desktop app at **Settings → Connections → Add environment**, or open it in a
browser. The bare `http://127.0.0.1:3773` address redirects to T3's pairing
page; it is not the pairing link itself.

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
workstation itself. Allow TCP 3773 (or your selected port) from the trusted LAN
in the workstation's firewall and use a static or reserved address. Basaltwater
does not change firewall rules, provide the VM pairing broker, configure a
gateway, or maintain a source allowlist.

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

- Package state is checked with `pacman -Q`; missing packages use
  `pacman -S --needed`. Setup does not refresh package databases or perform a
  system upgrade. Use CachyOS's normal update workflow first, and never use
  `pacman -Sy` as a repair for a partial upgrade.
- A package install may prompt for sudo. Agent CLIs, the T3 runtime, cache
  cleanup, and repository checks are otherwise noninteractive. No update timers
  are installed.
- Cache cleanup runs after tool readiness. If cleanup fails, setup reports an
  incomplete result and returns nonzero; installed tools are retained. Resolve
  the cleanup error and rerun setup. This profile has no automatic cache retry.
- Reruns retain installed software, credentials, and repositories. Omitting an
  option does not uninstall it; existing repositories are never pulled, reset,
  or recursively chowned. Selecting the T3 web interface on a rerun updates and
  restarts its service; selecting desktop mode retains its installed AUR version.
- `basaltw upgrade` updates Basaltwater itself. If a CachyOS mirror or DNS
  lookup fails, fix the resolver or mirror through CachyOS's normal maintenance
  workflow and rerun.
- `--repo` clones only a missing repository. An existing destination must be a
  Git repository with the requested origin and must be writable by you; setup
  does not delete or repair conflicting directories.

## Skills, diagnostics, and boundaries

Codex and OpenCode receive the CachyOS workstation, workspace, and (when T3 is
selected) T3 skills under `~/.agents/skills`. Standard VM, XRDP, gateway,
browser-automation, and Godot-web skills are not installed. Personal skills are
preserved. KDE automation remains a future, separately selected capability.

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
Firefox, Brave, and Cachy Browser native packages. An absent optional browser
package does not mean there is no usable browser; custom installations are not
inventoried, and the doctor does not launch a browser to test it.

Before calling a workstation validated, record its CachyOS, Plasma, kernel,
GPU/driver, and tool versions; repeat setup; authenticate an agent; complete a
small edit/test task and disposable worktree; and, when selected, test a T3
thread, terminal command, logout/login, and the intended loopback or LAN
listener. Test Godot, Vulkan/OpenGL, audio, and media on the actual GPU. Repeat
the relevant checks after a normal CachyOS update and record AMD and NVIDIA
results separately.

For contributors, `plugins/cachyos.py` owns composition, `lib/cachyos.py` owns
the local support boundary, and `common/cachyos_steps.py` owns target-side
operations; `common/cachyos_t3.py` owns staged T3 activation and recovery. The
CLI routes directly to the CachyOS runner rather than the SSH
host lifecycle. Keep additions explicitly allowed and independently tested;
mock pacman, sudo, downloads, service operations, and hardware probes.
