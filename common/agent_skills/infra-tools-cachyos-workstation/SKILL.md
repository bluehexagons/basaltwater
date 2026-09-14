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
`--gl-tools`, `--gaming`, `--sunshine`, or `--moonlight`. Preview with
`--dry-run`. Existing executables are retained;
setup is not a tool updater. Authentication uses the provider's local login.

Packages use pacman, not APT. infra-tools installs missing packages using the
existing sync database. Leave full OS updates to the user's CachyOS workflow;
never repair an installation failure with a partial `pacman -Sy` upgrade.
Use the original installer for deliberate tool updates.

Managed Playwright and KDE input/screenshot automation are not installed.
For browser checks, use tools actually available in the session or the project's
own test commands. For GUI-only validation, launch the application in the user's
desktop session and arrange human testing. Do not assume XRDP, X11 automation,
a managed gateway, remote pairing, or VM maintenance services exist here.

Prefer read-only diagnostics such as `command -v`, tool version checks,
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
