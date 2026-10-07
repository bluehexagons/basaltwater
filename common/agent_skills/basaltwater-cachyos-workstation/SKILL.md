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
never infer missing options from installed packages. Supported refresh flags
merge into the saved selection; a full explicit setup replaces the record.
Refresh can update agent CLIs and restart the selected web service; finish
active work first. It leaves OS and installed T3 desktop updates to CachyOS.
Codex and GitHub CLI are defaults; other coding agents require explicit flags.
Recognized standalone Codex installations are updated on rerun; npm,
version-manager, and system-package installations keep their original manager.
Existing Codex configuration and credentials are retained; custom `CODEX_HOME`
is unsupported when Codex is selected. Authentication uses local provider login.

Optional `--notify webhook URL` targets send the result of local setup and
refresh through the shared JSON webhook sender. `mailbox` is unsupported;
no notification timer or Debian maintenance/security jobs are installed.
Repeat targets as needed. `normal`/`verbose` sends success and failure;
`warning`/`error` sends failures, and `off` pauses delivery. Previews send nothing.
`--notification-strict-https` verifies certificates/hostnames; the default
accepts self-signed certificates. Preserve targets through refresh, which
merges additions; a full explicit setup replaces the selection. Webhook URLs
can contain credentials and remain in the private saved selection and receipt.
Displayed refresh commands redact them. Delivery failure warns without changing
the setup result. Inspect local output for full setup error details.

Packages use pacman, not APT. Basaltwater installs missing packages using the
existing sync database. Leave full OS updates to the user's CachyOS workflow;
never repair an installation failure with a partial `pacman -Sy` upgrade.
Use the original manager for updates to externally managed tools.

For native game and Animator development, `--game-dev` supplies CMake/native
libraries, debugging/caching tools, Xvfb/xauth and Electron host libraries.
`--node-versions` prepares user-local NVM without choosing a runtime or changing
shell defaults. Use `basaltw node install` when a project needs its committed
pin; it prepares missing user-local NVM on demand, so an earlier
`--node-versions` selection is unnecessary. It keeps saved workstation options
intact and does not rerun full setup. Check with `basaltw node status` in each
project; `basaltw node exec -- COMMAND` works from
Fish and agent shells too. `--node` alone retains system/PATH runtimes and does
not prepare NVM on CachyOS. Custom NVM installations keep their own manager.
Add `--blender --git-lfs` for source assets. Game development also selects KDE
automation prerequisites for native Animator work, but starts no input/capture.
Use the [game development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_GAME_DEVELOPMENT.md)
for package scope and sibling workflows. Both OS profiles support game
development; Debian also covers validation, builds, publishing, and services.
This desktop bundle does not reproduce Debian compiler/formatter pins or
prepare a release SDK. Use the project's separately prepared SDK and ABI gates
for release packages on either host; record where each validation check ran.

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

Managed Playwright is not installed. Native KDE automation is explicitly
started with `basaltw desktop --native start` and requires the owner's portal
consent for one monitor plus keyboard/pointer access. Selecting desktop apps
installs its Python GObject, AT-SPI, GStreamer/PipeWire and GTK3 dependencies;
setup does not start control or install an autostart service. Check dependencies
with `basaltw desktop --native doctor` from KDE.
Run `basaltw agent manifest` in the project to discover active tools, installed
desktop applications, workflow instructions and declared deployment mappings.
Use `basaltwater-cachyos-desktop` for autonomous Blender model/material/UV work
and Inkscape, GIMP, Krita, Audacity and Shotcut asset edits. It covers trusted
application scripting, editable task copies, exports and reopen/consumer checks,
with references for the other supported native applications. The manifest
provides direct native launch commands on CachyOS rather than XRDP commands.
See the [desktop development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/DESKTOP_DEVELOPMENT.md).
For browser checks, use tools actually available in the session or the project's
own test commands. Complete scripting/export tasks autonomously. For required
GUI-only validation, use the user-approved native portal/AT-SPI tools; report
unavailable checks accurately. Do not assume XRDP, X11 automation,
a managed gateway, remote pairing, or VM maintenance services exist here.

Start desktop prerequisite diagnosis with `basaltw local cachyos-doctor --json`
as the desktop user. It does not activate services or capture content.
An available package, socket, or bus owner is only a prerequisite observation;
it does not verify automation or permission. Treat null selection/permission
fields as unknown and keep live qualification separate from observation time.
The doctor also checks saved selections, selected CLIs, host health, listeners,
and saved UFW input rules, accounting for earlier unconditional denies, port
ranges, and Sunshine UDP. Effective firewall rules require privileged verification;
local update metadata may be stale. Successful setup writes a private diagnostic
receipt at `~/.local/state/basaltwater/cachyos/last-report.json`.
Webhook attempts write a separate private `last-notification.json` with delivery
status and selection/policy digest, without URLs or tokens. Doctor reads it
without sending an event. Acceptance does not prove receiver processing; missing,
older, suppressed, or different-selection evidence leaves delivery unverified.

For an authorized privileged check or recovery when execution policy permits,
try KDE's existing polkit authentication agent before concluding that a missing
sudo password blocks the agent. A scoped command such as
`/usr/bin/pkexec --disable-internal-agent /usr/bin/ufw status verbose` lets the
owner authenticate in KDE while the agent receives only command output. Announce
the command's purpose before requesting authentication. If no graphical agent
is available or authentication is rejected, use the owner's terminal instead;
never request a password through chat or tool stdin. Keep the doctor read-only
and unprivileged. See the
[privileged-check procedure](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_MAINTENANCE.md#privileged-checks-from-an-agent)
for mirror recovery and effective firewall inspection.

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
host plus `libva-utils`, enables it at KDE login, and starts its user service;
`--moonlight` installs the Qt client. A normal Sunshine tray quit or user-service
stop leaves it stopped for that session; it returns at the next KDE login.
Setup retains an active service without restarting it and refuses custom units,
masks, and drop-ins. No automatic login or pre-login display is configured.
On Intel-only systems with verified VA-API encoding, an absent encoder setting
defaults to VA-API while Sunshine is stopped; explicit settings are preserved.
It does not install graphics drivers. Firewall changes still require access
flags. Include `--lan-access` or explicit sources and complete pairing through
local Sunshine administration. The doctor checks service failures and VA-API
profiles without starting capture; test video, audio, and input in Moonlight.

For missing or stuck physical keys, stop Basaltwater native control first and
check layout, shortcuts and whether the failure spans applications. Inspect other
virtual input providers: an idle Sunshine virtual keyboard can retain stale state.
In a verified 2026-10-05 recovery, portal P worked but physical P did not;
reconnecting the keyboard did not help, while restarting idle Sunshine restored P.
The exact trigger was not established. Discover the user-service unit, check
for active streams before a targeted restart, preserve pairings/configuration,
and ask the owner to verify physical input afterward. See the
[keyboard input recovery procedure](https://github.com/bluehexagons/basaltwater/blob/main/common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md#missing-or-stuck-physical-keys)
for focused event comparisons and stopping conditions.

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
Native control is a separate, task-scoped opt-in. Keep its agent, workspace,
and readiness checks when using the desktop app; verify a real provider thread
and terminal in T3 after setup.
