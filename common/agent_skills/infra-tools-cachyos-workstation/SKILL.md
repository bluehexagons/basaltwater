---
name: infra-tools-cachyos-workstation
description: Install or diagnose coding tools on a locally managed CachyOS KDE workstation using its existing human account.
metadata:
  managed-by: infra_tools
---

# CachyOS coding workstation

This is an existing human-operated CachyOS KDE desktop. Use the current account
and its existing credentials and project configuration. The `agent_cachyos`
profile installs tooling; it does not manage the OS, graphics drivers, login
manager, account groups, network, firewall, or power policy.

To add supported tools, rerun `infra-tools setup agent_cachyos localhost` as the
desktop user, adding flags such as `--python`, `--node`, `--godot`, `--av-tools`,
`--gl-tools`, `--gaming`, `--sunshine`, `--moonlight`, `--obs`, `--blender`,
`--kdenlive`, `--krita`, `--inkscape`, `--scribus`, `--audacity`, `--ardour`,
`--lmms`, `--freecad`, `--kicad`, `--shotcut`, `--gimp`, `--remmina`, or
`--sysadmin-tools`. Preview with `--dry-run`. User-managed agent executables are
updated on rerun, while system-managed executables remain under their package
manager. Authentication uses the provider's local login.

Packages use pacman, not APT. infra-tools installs missing packages using the
existing sync database. Leave full OS updates to the user's CachyOS workflow;
never repair an installation failure with a partial `pacman -Sy` upgrade.
Use the original installer for deliberate tool updates.

Managed Playwright and KDE input/screenshot automation are not installed.
For browser checks, use tools actually available in the session or the project's
own test commands. For GUI-only validation, launch the application in the user's
desktop session and arrange human testing. Do not assume XRDP, X11 automation,
a managed gateway, remote pairing, or VM maintenance services exist here.

Start desktop prerequisite diagnosis with `infra-tools local cachyos-doctor --json`
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

This profile does not install machine-use automation. T3 Code's optional service
supports same-machine access, direct pairing from a trusted private LAN, and
T3 Connect; use the dedicated `infra-tools-cachyos-t3code` skill for its
service, pairing, and Connect commands. Keep the profile's native package,
agent, workspace, and readiness checks even when using the desktop app; they
provide setup and repeatability that the client does not provision.
