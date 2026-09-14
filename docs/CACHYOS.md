# CachyOS coding workstation

`agent_cachyos` adds coding tools to an **already installed x86-64 CachyOS KDE
Plasma workstation**, using the existing desktop account. This is limited,
experimental support for bare metal. It does not provision CachyOS VMs or
containers or extend CachyOS support to the general server profiles.

The initial implementation is covered by mocked setup tests and installer
fixtures on Debian. Real CachyOS hardware, graphics, and end-to-end provider
sessions still need the acceptance checks below. KDE automation and managed
Playwright are deferred.

## Install locally

First finish the normal CachyOS installation, full-system updates, KDE setup,
and GPU driver setup. Open a terminal in your normal desktop session. Run the
following block **as yourself, without sudo**; the installer requests sudo only
when missing system packages need installation. Keep the terminal attached:
the password prompt appears when the package step begins, and setup continues
after you enter your own password:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh &&
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos \
  --node --python --git-lfs
```

The final line is the setup for this machine. Choose its flags before running
it; this example is a general coding workstation with Node.js, Python, and Git
LFS in addition to the default Git, ripgrep, build tools, GitHub CLI, and Codex.
Remove any of those flags or replace them with the options in the table below
to match the machine's role. The installer and setup then run as one operation.

The `dev` channel contains this new profile on `main`; older release tags do
not. The source defaults to `~/.local/share/infra_tools` (or the configured XDG
data directory), and the launcher is `~/.local/bin/infra-tools`. The installer
adds PATH support for Bash, Zsh, or Fish without replacing shell preferences or
installing Python aliases. Open a new terminal after installation. Other shells
need `~/.local/bin` and `~/.opencode/bin` added to PATH manually.

The default tools are Git, ripgrep, native build prerequisites, GitHub CLI, and
Codex. Agent CLIs use their upstream user installers. Existing user-managed
executables are refreshed on rerun; system-managed executables are retained.
No account is created; passwords, group membership,
sudo policy, provider settings, credentials, and Git identity remain yours.
Run the selected provider's local login when needed. For example:

```bash
codex login
gh auth login
```

This profile does not install machine-use automation, managed Playwright, or
KDE input/screenshot helpers. The optional `--web-interface t3code` service is
only the localhost coding web interface; the T3 Code desktop app can be used
as its client without enabling that service. Keep the profile's setup and
readiness checks when using the desktop app because they provision native
packages, agent tools, workspaces, and user-owned state that the client does
not install.

Authenticate before adding private repositories. Your existing Git credential
helpers and SSH-agent environment are retained. Setup accepts HTTPS repository
URLs and does not copy credentials from another host.

## Choose setup flags

For a machine that does not need the general coding-workstation example above,
replace the setup command's final flag suffix with one of these use-case
patterns:

| Use case | Flags to append to `--local-setup agent_cachyos` |
| --- | --- |
| Minimal coding tools | *(no extra flags)* |
| Node/Python development | `--node --python --git-lfs` |
| Local T3 Code service | `--web-interface t3code` (Node tooling is implied) |
| Game and media work | `--gaming --node --godot --av-tools --gl-tools` |
| Game streaming workstation | `--gaming --sunshine --moonlight` |
| Creative workstation | `--obs --blender --kdenlive --krita` |
| Audio workstation | `--audacity --lmms --ardour` |
| Illustration and publishing | `--gimp --inkscape --scribus` |
| CAD and electronics | `--freecad --kicad` |
| Additional video editor | `--shotcut` |
| Remote desktop client | `--remmina` |
| Sysadmin workstation | `--sysadmin-tools --remmina` |
| Additional agent | `--agent-tool opencode` or `--agent-tool claude` |

For example, the complete initial command for a local T3 Code service is:

```bash
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos \
  --web-interface t3code
```

To preview a chosen initial configuration before applying it, install the
launcher without `--local-setup`, then run the same flags with `--dry-run`:

```bash
infra-tools setup agent_cachyos localhost --node --python --git-lfs --dry-run
```

`--dry-run` prints the step plan without installing software or changing files.
It can also run on a development/CI host; actual platform, account, and hardware
checks run before apply. Passing `--dry-run` to the **shell installer** still
installs the launcher and prerequisites; only the subsequent setup is a preview.

| Option | CachyOS behavior |
| --- | --- |
| `--agent-tool gh,codex,claude,opencode` | Add selected agents; repeatable; defaults include `gh,codex` |
| `--no-agent-tool codex` | Exclude a default from this run; does not uninstall an existing tool |
| `--node` | Install missing Node.js, npm, and pnpm commands using distro packages |
| `--python` | Install missing Python and uv commands; no global pip packages or interpreter aliases |
| `--go` | Install Go when absent |
| `--git-lfs` | Install Git LFS when absent; initialize it within projects as needed |
| `--godot` | Install the distro's Godot when absent; no export-template bundle or managed updater |
| `--av-tools` | Install missing FFmpeg and ImageMagick commands |
| `--gl-tools` | Install missing Mesa diagnostic and Vulkan diagnostic commands; no driver installation |
| `--gaming` | Install CachyOS's native gaming libraries, launchers, and tools (`cachyos-gaming-meta` and `cachyos-gaming-applications`) |
| `--sunshine` | Install the native CachyOS Sunshine game-stream host package; setup does not open firewall ports or create credentials |
| `--moonlight` | Install the native CachyOS Moonlight Qt game-stream client package |
| `--obs` | Install native OBS Studio for recording and live streaming |
| `--blender` | Install native Blender for 3D creation and rendering |
| `--kdenlive` | Install native Kdenlive for non-linear video editing |
| `--krita` | Install native Krita for digital painting and image editing |
| `--inkscape` | Install native Inkscape for vector illustration |
| `--scribus` | Install native Scribus for desktop publishing |
| `--audacity` | Install native Audacity for audio editing and recording |
| `--ardour` | Install native Ardour for multitrack audio production |
| `--lmms` | Install native LMMS for music production |
| `--freecad` | Install native FreeCAD for parametric 3D CAD |
| `--kicad` | Install native KiCad for schematics and PCB design |
| `--shotcut` | Install native Shotcut for video editing |
| `--gimp` | Install native GIMP for image editing |
| `--remmina` | Install native Remmina with common RDP, VNC, SPICE, and secret plugins |
| `--sysadmin-tools` | Install native Nmap, tcpdump, DNS tools, virt-manager, and Wireshark Qt |
| `--repo HTTPS_URL` | Clone a missing repository; repeatable; existing origins must match |
| `--agent-workspace /absolute/path` | Clone destination, defaulting to `~/repos`; must be writable by you |
| `--web-interface t3code` | Install the optional localhost user service; implies Node tooling |
| `--web-interface-port PORT` | T3 HTTP port, default 3773; must be 1024–65535 |
| `--machine hardware` | Optional declaration; apply still verifies actual bare metal |

For example, add game and media tools and another agent:

```bash
infra-tools setup agent_cachyos localhost \
  --agent-tool opencode --node --python --git-lfs --godot --av-tools --gl-tools
```

For a graphical gaming and streaming workstation, use the native CachyOS
gaming bundle and select the host, client, or both:

```bash
infra-tools setup agent_cachyos localhost --gaming --sunshine --moonlight
```

`--gaming` installs CachyOS's `cachyos-gaming-meta` and
`cachyos-gaming-applications` packages, which provide the gaming libraries and
the supported launchers and tools. `--sunshine` and `--moonlight` use the
native packages from the configured CachyOS repositories; they do not install
graphics drivers. The package split follows the [CachyOS gaming
guide](https://wiki.cachyos.org/configuration/gaming/); the [CachyOS package
index](https://packages.cachyos.org/) supplies the currently configured native
package versions. Installing Sunshine does not start its user service or
change the firewall. After reviewing the network exposure and choosing a
pairing password, start it in the desktop session with:

```bash
systemctl --user --now enable sunshine
```

Keep Sunshine's streaming and web UI ports on a trusted LAN or VPN. The
profile leaves firewall policy and Sunshine's application configuration to the
desktop owner. Complete pairing from Sunshine's local web UI, then use
Moonlight on the client device.

The application flags install only the selected native CachyOS/Arch repository
packages. Remmina also installs the repository's common RDP, VNC, SPICE, and
secret plugins so its usual protocols are available. The sysadmin bundle adds
desktop and network diagnostics, but does not enable libvirt, grant packet
capture permissions, open firewall ports, or change network policy. These flags
do not install AUR or Flatpak packages, graphics drivers, or application-specific
configuration. Ardour is available when the configured CachyOS repository
provides its native CPU-optimized package; if it is unavailable for the
machine's architecture, pacman reports that dependency error and setup stops.

An existing version-manager installation is retained when its commands are on
the invoking shell's PATH. Setup checks executable availability and selected
tool versions; it does not promise that an existing runtime satisfies every
project. Use project-specific environments and lockfiles. T3's Node version
requirement is checked explicitly.

## Updates and reruns

System packages are queried with `pacman -Q`. Missing packages are installed
using `pacman -S --needed`, without refreshing repository databases or running
an OS upgrade. If you run setup as your desktop user, the package command keeps
the terminal attached so `sudo` can prompt for your password at that step.
Dependency resolution can still install or change dependencies.
If repositories are stale or dependencies conflict, setup stops; resolve the
error through CachyOS's normal update workflow and rerun.

Package downloads use the mirrors already configured on the workstation. A
malware-filtering DNS service can incorrectly block a package mirror and cause
pacman messages such as `Could not resolve host: archlinux.cachyos.org`. Check
the configured resolver and mirror before rerunning, for example:

```bash
resolvectl query archlinux.cachyos.org
getent hosts archlinux.cachyos.org
```

Use a resolver that permits the trusted CachyOS/Arch mirrors or allowlist the
mirror in the DNS policy. Setup does not replace DNS or rewrite pacman mirror
lists; keep the normal full-system update and mirror maintenance in CachyOS's
own workflow.

Do not use `pacman -Sy` as a repair step: Arch-based systems do not support
partial upgrades. See [CachyOS updates](https://wiki.cachyos.org/configuration/post_install_setup/)
and [Arch system maintenance](https://wiki.archlinux.org/title/System_maintenance).

infra-tools installs no OS, language-runtime, agent, or Godot update timers on
this profile. A setup rerun refreshes selected user-managed agent CLIs and the
managed T3 runtime, then runs a bounded user-cache cleanup for this account;
system-managed executables remain under the package manager's control. Upstream
tools may have their own update behavior. Update infra-tools with
`infra-tools upgrade` or by rerunning the download block. Existing repositories
are never pulled, reset, or recursively chowned by setup.

Rerun `setup agent_cachyos localhost` with the options you want checked. This
profile does not save a controller-side host configuration and does not use
`patch`, `deploy`, or `recall`. Omitting an option does not uninstall software.
After a failed step, earlier successful installations remain; fix the reported
problem and rerun. There is no automatic rollback of installed packages.

An existing clone destination must be the root of a Git repository with the
requested origin. An ordinary directory inside another repository does not
qualify. If a destination conflicts or a clone was interrupted, inspect and move
that directory aside yourself, or choose another `--agent-workspace`; setup
does not delete its contents.

## Optional T3 Code

The CachyOS profile supports a local, user-owned T3 Code service. Its T3
specific options are:

| Option | Effect |
| --- | --- |
| `--web-interface t3code` | Install or reconcile the dedicated `infra-tools-cachyos-t3.service` user unit and its runtime. This also selects Node.js and requires at least one provider CLI. |
| `--web-interface-port PORT` | Listen on `127.0.0.1:PORT`; the default is `3773` and the allowed range is `1024`–`65535`. |
| `--web-interface-host 127.0.0.1` | Explicitly repeat the fixed loopback bind. Other addresses are rejected. |
| `--agent-tool gh,codex,claude,opencode` | Select the provider CLIs available to the T3 service. The default is GitHub CLI plus Codex; T3 requires Codex, Claude, or OpenCode. |
| `--agent-workspace /absolute/path` | Set the service working directory; it defaults to `~/repos`. |
| `--repo HTTPS_URL` | Clone a missing repository into that workspace; existing repositories are checked but never pulled. |
| `--dry-run` | Show the T3 step in the plan without probing the host or changing the service. |

The service does not support `--t3code-ready`, device pairing, web-interface
source allowlists, non-loopback binds, gateways, or remote setup. Those options
belong to the VM/server T3 path and are rejected for `agent_cachyos`. `--node`
is implicit when T3 is selected; the setup also installs Python for native
Node module builds when it is missing.

Copy and paste this complete block in the existing KDE terminal to install the
launcher and configure the default local T3 service in one operation:

```bash
curl --fail --location --connect-timeout 15 --max-time 120 \
  --output "$HOME/.infra_tools-install.sh" \
  https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh &&
sh "$HOME/.infra_tools-install.sh" --channel dev --local-setup agent_cachyos \
  --agent-tool gh --agent-tool codex --web-interface t3code \
  --web-interface-port 3773
```

```bash
infra-tools setup agent_cachyos localhost --web-interface t3code
systemctl --user status infra-tools-cachyos-t3.service
```

Open `http://127.0.0.1:3773` on the workstation. The dedicated runtime lives in
`~/.local/share/infra-tools/cachyos-t3`, and the unit is
`~/.config/systemd/user/infra-tools-cachyos-t3.service`. It runs as you with your
provider credentials and the setup terminal's PATH. Setup checks HTTP readiness;
provider authentication and a real coding thread must still be verified.
Readiness probes bypass HTTP proxies and require consecutive successful
responses from the local URL. If startup fails, inspect the service journal and
check whether another application already uses port 3773; select another port
with `--web-interface-port` if needed.

If a setup run from an older checkout reported `has a bad unit file setting`,
update infra-tools and rerun the same setup command. The managed unit is
rewritten with systemd-compatible path escaping. You can validate it before
starting T3 with:

```bash
systemd-analyze verify "$HOME/.config/systemd/user/infra-tools-cachyos-t3.service"
```

The service starts with your user session. Setup does not enable lingering,
change suspend policy, configure a gateway, enroll other devices, or expose
network listeners beyond IPv4 loopback. An existing upstream `t3code.service`
causes setup to stop rather than adopt or replace it. A separately installed T3
desktop client is outside this profile's ownership.

Reruns retain the runtime. Changes to the unit may restart the service, so finish
active work before changing its port, workspace, or PATH. To stop it persistently:

```bash
systemctl --user disable --now infra-tools-cachyos-t3.service
```

For a deliberate runtime update outside setup, finish active threads, stop the
service, update its dedicated npm prefix as yourself, and restart it:

```bash
systemctl --user stop infra-tools-cachyos-t3.service
npm install --global --prefix "$HOME/.local/share/infra-tools/cachyos-t3" \
  --allow-scripts=node-pty,msgpackr-extract t3@latest
systemctl --user start infra-tools-cachyos-t3.service
```

Run each line only after the previous one succeeds. Check the service and open
the local UI afterwards. Use `journalctl --user -u infra-tools-cachyos-t3.service`
for startup errors. The generic VM T3 update/pairing commands do not manage this
unit. Upstream requirements and CLI behavior are documented in
[T3 installation](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md).

## Agent skills

Codex and OpenCode receive `infra-tools-cachyos-workstation` and
`infra-tools-cachyos-workspace` under `~/.agents/skills`. Selecting T3 adds
`infra-tools-cachyos-t3code`; the skill is retained on reruns while the managed
unit exists. The standard VM, XRDP, gateway, Godot-web, and browser-automation
skills are not installed. Known obsolete skills bearing infra-tools' managed
marker are removed; personal skills are preserved. Claude-only configurations
do not receive this shared Codex/OpenCode catalog.

Managed Git worktrees work locally through `infra-tools agent workspace`.
Other VM-oriented agent diagnostics, authentication rotation, maintenance
commands, desktop control, and gateway helpers are outside this profile.

## Read-only desktop doctor

Run from the existing desktop user's terminal, without sudo:

```bash
infra-tools local cachyos-doctor
infra-tools local cachyos-doctor --json
```

This initial qualification tool reports native prerequisite package versions,
owned Wayland/user-bus sockets, already-owned KWin/portal/accessibility bus
names, and systemd user-unit state for PipeWire, WirePlumber, and optional T3.
It does not launch applications, activate portal or accessibility services,
open listeners, capture the desktop, or install dependencies. On other operating
systems or when run as root it returns a deferred host result without desktop
probes. It can inspect CachyOS VM prerequisites, but setup still requires bare
metal and a VM does not qualify hardware behavior.

Each subprocess uses a three-second deadline and a 16 KiB output limit with
process-group cleanup on timeout or overflow. Only validated package versions
and fixed status messages reach the report. Environment values, raw errors,
journals, window contents, device names, and personal paths are omitted.
The report includes the account name, package versions, and observation time;
review that metadata before sharing it. The command writes no report file.

JSON schema version 1 contains a `capabilities` array. Every record carries
`name`, `state`, `reason`, `owner`, `session`, `origin`, `sensitivity`,
`observed_at`, `last_verified`, `version`, `selected`, `interactive_required`,
and its own `schema_version`. Selection and pending permission may be `null`
(unknown); consumers must not interpret that as `false`. Prerequisites marked
`available` prove only the observation described by their reason. They do not
prove usable input, capture, rendering, provider authentication, or a functioning
application. `last_verified` remains `null` until live qualification is recorded.
Managed browser, AT-SPI, and portal control remain unselected and deferred.

Exit code 0 means the diagnostic report was produced, including deferred
results. Exit code 1 indicates that the account or diagnostic contract could
not be resolved. There is no setup persistence or web-panel integration in
this first milestone. The remaining implementation and release gates are in
the [agentic desktop plan](plans/CACHYOS_AGENTIC_DESKTOP.md); record live results
using its [qualification checklist](plans/CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md).

## Hardware acceptance checks

Before treating a workstation as validated:

1. Record CachyOS, Plasma, kernel, GPU/driver, and installed tool versions.
2. Run the copy/paste install and repeat it; confirm desktop login, groups,
   shell customization, graphics settings, and existing repositories survive.
3. Authenticate an agent, complete a small edit/test workflow, and create and
   remove a managed worktree using a disposable test repository.
4. If selected, open T3, run a provider thread and terminal command, log out/in,
   and verify the user service. Check that only loopback is listening.
5. If selected, open a Godot test project, run it on the intended GPU, and check
   media conversion and Vulkan/OpenGL diagnostics. CLI versions alone do not
   validate rendering, audio, fullscreen behavior, or GPU selection.
6. Repeat relevant checks after a normal CachyOS update. Record AMD and NVIDIA
   results separately; passing one does not validate the other.

## Contributor boundaries

`plugins/cachyos.py` owns the composition, `lib/cachyos.py` owns the local support
boundary, and `common/cachyos_steps.py` owns tooling operations. The CLI routes
directly to the dedicated target-side runner in `remote_setup.py`; it does not
stage SSH payloads or invoke the general host setup lifecycle. Unsupported
options are rejected before normalization can enable additional capabilities.

Keep additions explicitly allowed and independently testable. Do not enable
generic workstation flags to obtain tools indirectly. Tests must mock pacman,
sudo, downloads, service operations, and hardware probes, using temporary
directories for user files. Native KDE automation and Playwright require their
own future compatibility work and skill selection.
