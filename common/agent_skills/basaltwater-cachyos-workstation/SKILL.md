---
name: basaltwater-cachyos-workstation
description: Install or diagnose coding tools on a locally managed CachyOS KDE workstation using its existing human account.
metadata:
  managed-by: basaltwater
---

# CachyOS coding workstation

This is an existing human-operated CachyOS KDE desktop. Use the current account
and its existing credentials and project configuration. The `agent_cachyos`
profile installs tooling; it does not manage the OS, graphics drivers, login
manager, account groups, network addressing, or power policy. Firewall management
is opt-in through `--lan-access`, `--access-source`, or `--no-lan-access`.
Target the latest fully updated rolling release: Plasma Wayland, Plasma Login
Manager, and Shelly are the current KDE baseline. Do not assume SDDM, X11,
Octopi, or a preinstalled paru from older CachyOS images. Optional helper
fallbacks do not extend support to outdated systems.

To add supported tools, rerun `basaltw setup agent_cachyos localhost` as the
desktop user, adding flags such as `--python`, `--node`, `--godot`, `--av-tools`,
`--gl-tools`, `--gaming`, `--sunshine`, `--moonlight`, `--obs`, `--blender`,
`--kdenlive`, `--krita`, `--inkscape`, `--scribus`, `--audacity`, `--ardour`,
`--lmms`, `--freecad`, `--kicad`, `--shotcut`, `--gimp`, `--remmina`, or
`--sysadmin-tools`. Material/publishing options are `--material-maker`, `--etcher`,
`--butler`, and `--steamcmd`. Preview with `--dry-run`. Keep the desired flags on reruns;
this profile does not populate the generic saved-host `patch`/`deploy` workflow.
For the same selection, use `basaltw refresh --dry-run`, then `basaltw refresh`
to upgrade Basaltwater on its selected channel and repeat the last successful
local setup with the updated code. The private record is
`~/.local/state/basaltwater/cachyos/last-setup.json`. Failed runs and previews
preserve it. Older setups need one explicit successful setup to create it;
never infer missing options from installed packages. Changing options requires
a full explicit setup command; its successful selection replaces the record.
Refresh can update agent CLIs and restart the selected web service; finish
active work first. It leaves OS and installed T3 desktop updates to CachyOS.
Codex and GitHub CLI are defaults; other coding agents require explicit flags.
Recognized standalone Codex installations are updated on rerun; npm,
version-manager, and system-package installations keep their original manager.
Existing Codex configuration and credentials are retained; custom `CODEX_HOME`
is unsupported when Codex is selected. Authentication uses local provider login.

Packages use pacman, not APT. Basaltwater installs missing packages using the
existing sync database. Leave full OS updates to the user's CachyOS workflow;
never repair an installation failure with a partial `pacman -Sy` upgrade.
Use the original manager for updates to externally managed tools.

For a recognized standalone Codex installation, preview with
`basaltw agent update --tool codex --dry-run`, then update with
`basaltw agent update --tool codex --tools-only-readiness`. The latter gates update
success on the selected tools while still recording host and any expected T3
readiness; it does not omit those observations. Complete package rollback
snapshots are retained; restart provider sessions when convenient to use the
new executable.

Setup prunes old pacman downloads while retaining three cached versions, the
installed version, and files accessed or modified within 30 days. It also
reconciles known developer caches, Codex releases, and numbered T3 log rotations.
Current Shelly also offers cache cleanup; setup leaves its AUR build cache and
existing distro maintenance configuration alone. No cleanup timer is installed.
Preserve credentials, sessions, application data,
and repositories; do not substitute blanket cache deletion or orphan-package
removal. See the [cleanup policy](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS.md#cleanup-during-setup).

Managed Playwright and KDE input/screenshot automation are not installed.
Run `basaltw agent manifest` in the project to discover active tools, installed
desktop applications, workflow instructions and declared deployment mappings.
When Blender is detected, use its background-render guidance for repeatable
output checks; validate editing behavior in the existing native desktop.
See the [desktop development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/DESKTOP_DEVELOPMENT.md).
For browser checks, use tools actually available in the session or the project's
own test commands. For GUI-only validation, launch the application in the user's
desktop session and arrange human testing. Do not assume XRDP, X11 automation,
a managed gateway, remote pairing, or VM maintenance services exist here.

Start desktop prerequisite diagnosis with `basaltw local cachyos-doctor --json`
as the desktop user. It does not activate services or capture content.
An available package, socket, or bus owner is only a prerequisite observation;
it does not verify automation or permission. Treat null selection/permission
fields as unknown and keep live qualification separate from observation time.
The doctor also checks saved selections, selected CLIs, host health, listeners,
and saved UFW rules. Effective firewall rules require privileged verification;
local update metadata may be stale. Successful setup writes a private diagnostic
receipt at `~/.local/state/basaltwater/cachyos/last-report.json`.

For explicitly requested firewall management, retain the full setup selection
and add `--lan-access` for the single private default-route LAN, or
`--access-source PRIVATE_IP_OR_CIDR` for fixed sources. UFW allows outgoing
traffic and limits selected T3/Sunshine inbound ports to those sources; old
broad rules are overridden by earlier guards. Remote Sunshine administration,
T3 UDP, and legacy RDP stay blocked. Other rules are retained. A rerun can change
the inferred LAN; prefer explicit sources on roaming desktops. No access flags
leaves policy unchanged; `--no-lan-access` without explicit sources closes managed
inbound ports. Inspect the documented limitations and effective rules in the
[firewall guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS.md#optional-workstation-firewall).

Additional read-only diagnostics include `command -v`, tool version checks,
`pacman -Q`, and user service logs. Graphics diagnostics may use `vulkaninfo`
or `glxinfo` when installed; a successful CLI check does not prove GPU rendering.
An agent running as this account has the account's access to personal files.
Keep work inside the requested project and preserve existing application settings.

The gaming flags use CachyOS-native packages. `--gaming` installs the gaming
libraries, launchers, and tools bundle; `--sunshine` installs the game-stream
host; and `--moonlight` installs the Qt client. The setup does not install
graphics drivers, enable Sunshine, or change firewall policy. After reviewing
network exposure, the desktop owner can start the user service with
`systemctl --user enable --now app-dev.lizardbyte.app.Sunshine.service` and complete pairing in Sunshine's web
UI.

Most application flags install only selected native repository packages. The
Remmina flag includes common native RDP, VNC, SPICE, and secret plugins. The
sysadmin bundle includes Nmap, tcpdump, DNS tools, virt-manager, and Wireshark
Qt; it does not enable libvirt, grant packet-capture permissions, or change
network policy. These native options do not install AUR or Flatpak packages, graphics
drivers, or application-specific configuration.

Material Maker, butler, and SteamCMD are explicit AUR exceptions, sharing T3's
reviewed helper/cache path. Existing packages stay with their package manager;
unmanaged executables require an explicit migration or omitting the flag. Etcher
uses repository `etcher-bin`. Do not launch SteamCMD as a readiness/version probe:
even `+quit` can download updates and initialize personal Steam state. Setup does
not log in, publish, flash media, grant disk/input access, or disable Electron's
sandbox. See the [software guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_SOFTWARE.md)
for package mappings, Godot export-template requirements, and manual checks.

T3 desktop is another explicit AUR exception: `--t3code-desktop` installs or retains
`t3code-bin` through the default Shelly CLI, using paru or yay only if Shelly is
absent. Preserve the selected helper's review prompts and source policy.
It is mutually exclusive with
`--web-interface t3code`, which selects a separate managed web environment.
Use `basaltwater-cachyos-t3code` for switching, pairing, and Connect commands.
This profile does not install machine-use automation. Keep its agent, workspace,
and readiness checks when using the desktop app; verify a real provider thread
and terminal in T3 after setup.
