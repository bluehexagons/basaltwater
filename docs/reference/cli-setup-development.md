# Development and CachyOS SSH flags

[Documentation index](cli-setup.md)

### Development Flags

| Flag | Description |
|------|-------------|
| `--node` | Install Node.js, npm and pnpm using native packages on CachyOS or NVM on Debian |
| `--node-versions` / `--no-node-versions` | CachyOS only: prepare user-local NVM without selecting a Node version; independent of native `--node`; use `basaltw node install` for each project pin |
| `--update-nvm-npm NODE_VERSION\|off` | CachyOS only. Bring npm on one installed NVM runtime up to the Pacman npm version on setup and refresh. `off` clears the saved selection. See [CachyOS npm maintenance](../workstations/cachyos-refresh.md#npm-updates). |
| `--game-dev` / `--no-game-dev` | Select native game build/debug tools and dependency headers on Debian or CachyOS; Debian leaves SDL versions to project bootstrap; see [Project tooling](../agents/project-tooling.md) and [CachyOS game development](../workstations/cachyos-game-development.md) |
| `--go` | Install Go |
| `--python` | Install Python aliases + uv |
| `--data-analysis` | Install NumPy, pandas, SciPy, Matplotlib and csvkit. Also enables `--python`. |
| `--jupyterlab` / `--no-jupyterlab` | Install the Debian notebook environment, `jupyter lab` launcher and Python kernel with required dependencies. Debian recommendations are optional. Existing saved analysis setups retain JupyterLab on replay. |
| `--av-tools` | Install ImageMagick, FFmpeg (including ffprobe), and ExifTool for image, audio, video, and metadata processing |
| `--gl-tools` | Install the minimal OpenGL inspection and debugging bundle: Mesa utilities and apitrace |
| `--godot` | Install the newest stable verified Godot Engine release for graphical or headless use |
| `--godot-bundle BUNDLE` | Add `web` or `publishing`; repeatable and automatically enables `--godot` |
| `--publishing-tool TOOL` | Independently install `butler` or `steamcmd` on Debian for a non-root user; repeatable, no Godot requirement; SteamCMD requires x86_64 |
| `--gaming` | CachyOS `agent_cachyos` only: install the native gaming libraries, launchers, and tools bundle |
| `--sunshine` | CachyOS `agent_cachyos` only: install Sunshine and enable/start its existing user service for the graphical session; pairing and stream configuration remain interactive |
| `--moonlight` | CachyOS `agent_cachyos` only: install the native Moonlight Qt game-stream client package |
| `--obs` | CachyOS `agent_cachyos` only: install native OBS Studio |
| `--blender` / `--no-blender` | Select Blender for graphical or headless use: Debian APT package on standard server/workstation profiles; native package on CachyOS `agent_cachyos` |
| `--kdenlive` | CachyOS `agent_cachyos` only: install native Kdenlive |
| `--krita` / `--no-krita` | Select Krita for painting, sprites and textures; Debian APT or native CachyOS package |
| `--inkscape` / `--no-inkscape` | Select Inkscape for SVG/vector asset editing; Debian APT or native CachyOS package |
| `--scribus` | CachyOS `agent_cachyos` only: install native Scribus |
| `--audacity` / `--no-audacity` | Select Audacity for sound effect and audio touch-ups; Debian APT or native CachyOS package |
| `--ardour` | CachyOS `agent_cachyos` only: install native Ardour |
| `--lmms` | CachyOS `agent_cachyos` only: install native LMMS |
| `--freecad` | CachyOS `agent_cachyos` only: install native FreeCAD |
| `--kicad` | CachyOS `agent_cachyos` only: install native KiCad |
| `--shotcut` / `--no-shotcut` | Select Shotcut for short video edits; Debian APT or native CachyOS package |
| `--gimp` / `--no-gimp` | Select GIMP for raster image touch-ups; Debian APT or native CachyOS package |
| `--remmina` | CachyOS `agent_cachyos` only: install native Remmina and common desktop plugins |
| `--sysadmin-tools` | CachyOS `agent_cachyos` only: install native desktop and network sysadmin tools |
| `--material-maker` | CachyOS only: reviewed AUR Material Maker binary package; retain an installed source package |
| `--etcher` | CachyOS only: repository Balena Etcher; no flashing or blanket disk permissions |
| `--butler` | CachyOS only: reviewed AUR itch.io publishing CLI; no login/upload |
| `--steamcmd` | CachyOS only: reviewed AUR SteamCMD plus 32-bit runtime libraries; no startup/login/upload |

### CachyOS SSH flags

These options apply to local `agent_cachyos` setup and refresh, and to remote
controller setup. Local setup detects current SSH service state: active or
enabled native `sshd.service`, `sshd.socket`, or managed SSH means enabled;
disabled or absent means off. Installed OpenSSH alone does not count. A valid
existing SSH session also means enabled. Use `--ssh` or `--no-ssh` to override.
If local apply cannot determine the state, choose explicitly; dry-run defers
unknown state. Remote controller setup defaults to SSH and accepts
`basaltw setup agent_cachyos HOST USER`; it requires reachable SSH and an
existing non-root desktop account, creates no account, and does not configure
autologin or copy credentials. Its dry-run makes zero SSH connections, and
apply verifies the target OS.

When no managed service exists, `--ssh` provides a root-managed
`basaltwater-cachyos-ssh.service` on IPv4 port 22 for the existing non-root
desktop account. It runs without KDE and accepts keys only. The service can
safely adopt a recognizable packaged native sshd service after validation,
without rewriting distro configuration and while preserving sessions. Foreign
custom units, drop-ins, or unknown listeners on port 22 are refused. `--no-ssh`
stops the managed service and recognizable stock native SSH; foreign custom
services remain untouched and may make the operation refuse safely.

| Flag | Description |
|------|-------------|
| `--ssh` / `--no-ssh` | Enable or disable SSH explicitly. On local setup, omission detects current service state; on remote-controller setup SSH defaults on. |
| `--ssh-password-auth` / `--no-ssh-password-auth` | Persistently permit or disable existing-account password authentication; requires `--ssh` and explicit `--lan-access` or private `--access-source`. `--no-ssh` clears password selection. |
| `--ssh-password-window MINUTES` | Open a separate temporary password bootstrap service on port 2222 for 1–60 minutes; requires `--ssh` and explicit private access. Never saved or replayed. |

Password modes require explicit private access flags; automatic discovery
does not satisfy this validation. Otherwise, when SSH is enabled and no access
flags are supplied, setup selects private IPv4 LAN access for UFW and manages
the selected T3, Sunshine, and SSH port scopes. The saved selection records
the resolved SSH boolean, and discovery runs again on every
setup and refresh. Explicit `--access-source` values are saved. Use
`--no-lan-access` or `--no-access-source` to disable automatic discovery, or
pass private IPv4 `--access-source` values. No public IPv4 or IPv6 SSH grants
are created, and unrelated firewall rules are retained. Key-only adoption of
an enabled stock SSH service requires usable `authorized_keys`; a fresh
key-only service can be paired with a password bootstrap window to enroll a key
without a prior key. The bootstrap listener is never saved or replayed; expiry
or reboot closes it and its sessions. The managed root service boots without
KDE. GUI operations that need a user bus/session still require the existing
desktop session.

Before firewall handover, setup requires the current `SSH_CONNECTION` client
address, when present, to be among the selected private IPv4 sources. If it is
not, setup stops before changing the firewall. Connect the remote controller
over an allowed private IPv4 path; switching from public IPv4 or IPv6 SSH
during handover can lose access.

For first-time key enrollment, start the window, then copy the key and test
normal port-22 access before it expires. The existing account password is
used only for this one-shot window:

```fish
basaltw setup agent_cachyos localhost --ssh --ssh-password-window 15 --lan-access --codex
ssh-copy-id -p 2222 USER@WORKSTATION_PRIVATE_IP
ssh USER@WORKSTATION_PRIVATE_IP
```

Verify the workstation host fingerprint through a trusted local channel before
accepting it; `ssh-copy-id` prompts for the existing account password. The normal
SSH service remains key-only on port 22. The temporary
service uses the existing account password, generates or stores no password,
and is stopped at its deadline or reboot; expiry terminates its daemon and
bootstrap sessions. A persistent password mode remains enabled until explicitly
disabled. See [CachyOS SSH setup](../workstations/cachyos.md#ssh-access).

Debian's `--blender` installs the configured release's `blender` APT package,
including on headless `agent_vm` hosts. It does not download the newest upstream
release, add a repository, or install a dedicated update timer; normal host
package maintenance owns updates. `--flatpak` does not change this source.
`--no-blender` clears the install selection without uninstalling Blender.
See [desktop software support and flags](../workstations/workstations.md#desktop-software-support-and-flags)
for the application support table and [Blender workflows](../workstations/desktop-development.md#blender)
for background rendering and native desktop checks.

Debian also accepts `--inkscape`, `--gimp`, `--krita`, `--audacity` and
`--shotcut` on standard server/workstation profiles. These optional asset
editors use the configured release's APT packages, without recommended extras;
`--flatpak` does not change their source. Reruns retain installed packages and
fail when a requested installation cannot be verified. Their `--no-*` forms
clear saved install selections without uninstalling software. The dedicated
Proxmox and WSL profiles reject these editor flags. Start with
`--inkscape --gimp --av-tools` for common game and website asset touch-ups,
adding the other editors when needed. Installation does not enable a desktop
on a headless profile; interactive work also needs `--desktop` or a workstation
profile. See [media touch-ups](../workstations/desktop-development.md#media-touch-ups-for-games-and-websites)
for autonomous edit/export/validation workflows.

The CachyOS-only gaming and desktop application flags use packages in the
workstation's configured repositories, except the explicit AUR selections noted
above. They do not install Flatpak packages, graphics drivers, open firewall ports, or configure application
credentials. The sysadmin bundle also leaves libvirt service access and packet
capture permissions to the user. See the [CachyOS workstation guide](../workstations/cachyos.md)
for Sunshine service discovery, pairing, and explicitly selected firewall access.

On CachyOS, `--godot` uses the native package or retains an existing executable;
there are no managed Godot update timers or `--godot-bundle` options. Use the
independent publishing flags and see [CachyOS software](../workstations/cachyos-software.md).

On Debian, selecting a managed runtime also installs its update timer. Godot is fetched
from the official release channel rather than Debian's package version and is
placed on the system `PATH` as `godot` and `godot4`, including for agent users.
The `web` bundle adds verified, version-matched web export templates for the
configured account. The `publishing` bundle adds Butler and, on x86_64,
user-owned SteamCMD; publishing credentials remain user-managed and are never
copied by setup.
See [`godot.md`](../workstations/godot.md) for the artifact and headless-use contract, and
[`maintenance.md`](../operations/maintenance.md) for schedules and update policy.
