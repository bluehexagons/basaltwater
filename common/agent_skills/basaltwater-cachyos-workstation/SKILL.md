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
manager, account groups, network, firewall, or power policy.

To add supported tools, rerun `basaltw setup agent_cachyos localhost` as the
desktop user, adding flags such as `--python`, `--node`, `--godot`, `--av-tools`,
`--gl-tools`, `--gaming`, `--sunshine`, `--moonlight`, `--obs`, `--blender`,
`--kdenlive`, `--krita`, `--inkscape`, `--scribus`, `--audacity`, `--ardour`,
`--lmms`, `--freecad`, `--kicad`, `--shotcut`, `--gimp`, `--remmina`, or
`--sysadmin-tools`. Preview with `--dry-run`. Keep the desired flags on reruns;
this profile does not populate the generic saved-host `patch`/`deploy` workflow.
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
`basaltw agent update --tool codex --tools-only-readiness`. The latter avoids
generic VM checks on this workstation. Complete package rollback snapshots are
retained; restart provider sessions when convenient to use the new executable.

Setup prunes old pacman downloads while retaining three cached versions, the
installed version, and files accessed or modified within 30 days. It also
reconciles known developer caches, Codex releases, and numbered T3 log rotations.
No cleanup timer is installed. Preserve credentials, sessions, application data,
and repositories; do not substitute blanket cache deletion or orphan-package
removal. See the [cleanup policy](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS.md#cleanup-during-setup).

Managed Playwright and KDE input/screenshot automation are not installed.
For browser checks, use tools actually available in the session or the project's
own test commands. For GUI-only validation, launch the application in the user's
desktop session and arrange human testing. Do not assume XRDP, X11 automation,
a managed gateway, remote pairing, or VM maintenance services exist here.

Start desktop prerequisite diagnosis with `basaltw local cachyos-doctor --json`
as the desktop user. It does not activate services or capture content.
An available package, socket, or bus owner is only a prerequisite observation;
it does not verify automation or permission. Treat null selection/permission
fields as unknown and keep live qualification separate from observation time.

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
`systemctl --user --now enable sunshine` and complete pairing in Sunshine's web
UI.

The application flags install only selected native repository packages. The
Remmina flag includes common native RDP, VNC, SPICE, and secret plugins. The
sysadmin bundle includes Nmap, tcpdump, DNS tools, virt-manager, and Wireshark
Qt; it does not enable libvirt, grant packet-capture permissions, or change
network policy. These options do not install AUR or Flatpak packages, graphics
drivers, or application-specific configuration.

T3 desktop is the explicit AUR exception: `--t3code-desktop` installs or retains
`t3code-bin` through an existing paru/yay helper. It is mutually exclusive with
`--web-interface t3code`, which selects a separate managed web environment.
Use `basaltwater-cachyos-t3code` for switching, pairing, and Connect commands.
This profile does not install machine-use automation. Keep its agent, workspace,
and readiness checks when using the desktop app; verify a real provider thread
and terminal in T3 after setup.
