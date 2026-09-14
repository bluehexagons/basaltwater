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
  --output "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh &&
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos \
  --node --python --git-lfs
```

The package step may ask for your desktop user's sudo password. Keep the
terminal attached until setup finishes. This example installs the default Git,
ripgrep, build tools, GitHub CLI, and Codex plus Node.js, Python, and Git LFS.
The `dev` channel is required while this profile is new; older release tags do
not contain it.

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
custom filters. Readiness checks filter presence; test transfers in your project
to verify custom filters and remote authentication.

The launcher is `~/.local/bin/infra-tools`; open a new terminal if the
installer's PATH change is not visible. Bash, Zsh, and Fish are supported.
Other shells need `~/.local/bin` and `~/.opencode/bin` added to PATH manually.

## Pick the options you need

Append options to `--local-setup agent_cachyos` in the installer command, or to
`infra-tools setup agent_cachyos localhost` after the launcher is installed.

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
| T3 Code or T3 Connect | `--web-interface t3code`, then use the T3 Connect flow below; optionally add `--web-interface-host PRIVATE_IPV4` and `--web-interface-port PORT` for direct LAN pairing |
| Machine declaration | `--machine hardware` (the bare-metal check still runs) |
| Plan only | `--dry-run` |

Examples:

```bash
# Game and media workstation with an additional coding agent
infra-tools setup agent_cachyos localhost \
  --agent-tool opencode --node --python --git-lfs --godot \
  --av-tools --gl-tools

# Gaming and game streaming
infra-tools setup agent_cachyos localhost --gaming --sunshine --moonlight

# Preview a plan without installing packages or changing files
infra-tools setup agent_cachyos localhost --node --python --dry-run
```

All application options install native packages from the configured CachyOS
repositories. They do not install AUR or Flatpak packages, graphics drivers, or
application configuration. `--gaming` selects CachyOS's gaming meta-packages;
`--sunshine` and `--moonlight` install native host and client packages but do
not open firewall ports or create credentials. Configure and pair Sunshine in
its own web UI on a trusted network.

T3 selects Node automatically and requires Codex, Claude, or OpenCode. Python
is also installed when needed for native Node module builds. Existing
version-manager runtimes are retained when their commands are on PATH.

## T3 Code: host locally or on a trusted LAN

T3 is an optional, user-owned systemd service. It runs as the logged-in desktop
user with provider credentials from that account. By default it listens only on
`127.0.0.1:3773`; selecting a specific private IPv4 address enables clients on
that LAN. Public, wildcard, and IPv6 bind addresses are rejected by this
profile.

### Install the local service

This complete block installs the launcher and configures the default loopback
service:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh &&
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos \
  --agent-tool gh --agent-tool codex --web-interface t3code \
  --web-interface-port 3773
```

For an already installed launcher, the equivalent setup is:

```bash
infra-tools setup agent_cachyos localhost --web-interface t3code
systemctl --user status infra-tools-cachyos-t3.service
```

The runtime is under `~/.local/share/infra-tools/cachyos-t3`, and the unit is
`~/.config/systemd/user/infra-tools-cachyos-t3.service`. Setup checks HTTP
readiness, but provider login and a real coding thread still need verification.
If port 3773 is busy, rerun with another port from 1024 through 65535.

### T3 Connect

T3 Connect is the cloud access option. It is separate from direct LAN pairing:
the setup installs the T3 CLI and service, while you authorize the workstation
with your T3 account once from the desktop session. Keep the default loopback
bind when using T3 Connect; the managed relay expects the server's loopback
origin. Use `connect link` instead of `connect`: the latter may offer to install
a second upstream `t3code.service`, while infra-tools already owns this unit.
Run:

```bash
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" connect link --base-dir "$HOME/.t3"
systemctl --user restart infra-tools-cachyos-t3.service
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" connect status --base-dir "$HOME/.t3"
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
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" pair --base-dir "$HOME/.t3"
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
infra-tools setup agent_cachyos localhost --web-interface t3code \
  --web-interface-host 192.168.1.50 --web-interface-port 3773
systemctl --user status infra-tools-cachyos-t3.service
"$HOME/.local/share/infra-tools/cachyos-t3/bin/t3" pair --base-dir "$HOME/.t3"
```

Open the generated URL on the other device, or paste it into its T3 desktop
app. The URL will contain `192.168.1.50`; a loopback URL only works on the
workstation itself. Allow TCP 3773 (or your selected port) from the trusted LAN
in the workstation's firewall and use a static or reserved address. infra-tools
does not change firewall rules, provide the VM pairing broker, configure a
gateway, or maintain a source allowlist.

If a previous checkout produced `has a bad unit file setting`, update
infra-tools and rerun setup. Validate the generated unit with:

```bash
systemd-analyze verify "$HOME/.config/systemd/user/infra-tools-cachyos-t3.service"
```

The service follows the user session; lingering is not enabled. An existing
upstream `t3code.service` is stopped rather than adopted. To stop this managed
service persistently:

```bash
systemctl --user disable --now infra-tools-cachyos-t3.service
```

Use `journalctl --user -u infra-tools-cachyos-t3.service` for startup errors.
The generic VM T3 pairing and update commands do not manage this unit. For a
deliberate runtime update, stop active work, stop the unit, update the dedicated
prefix as yourself, and start it again:

```bash
systemctl --user stop infra-tools-cachyos-t3.service
npm install --global --prefix "$HOME/.local/share/infra-tools/cachyos-t3" \
  --allow-scripts=node-pty,msgpackr-extract t3@latest
systemctl --user start infra-tools-cachyos-t3.service
```

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
  or recursively chowned. A T3 configuration change can restart its service.
- `infra-tools upgrade` updates infra-tools itself. If a CachyOS mirror or DNS
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
PipeWire, WirePlumber, and optional T3 unit state:

```bash
infra-tools local cachyos-doctor
infra-tools local cachyos-doctor --json
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
operations. The CLI routes directly to the CachyOS runner rather than the SSH
host lifecycle. Keep additions explicitly allowed and independently tested;
mock pacman, sudo, downloads, service operations, and hardware probes.
