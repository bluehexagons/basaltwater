# Remote development on CachyOS

Use these workstations primarily to edit, run, playtest, and debug Godot games
and applications. Electron and other native client software are supported
secondary workflows. Production builds and publishing belong on separate build
systems; containers and isolated project environments are not part of this
workstation role. Managed Git worktrees remain useful for concurrent editing.

The owner logs into the existing KDE Wayland account initially. Subsequent work
normally uses T3 Code, Sunshine/Moonlight, or SSH. A separate controller can
also run `agent_cachyos` setup over SSH using the existing non-root desktop
account; this does not create accounts, copy credentials, enable KDE autologin,
or start a pre-login display. Keep the existing desktop session and its
applications intact. SSH access alone does not establish a usable graphical
session or user bus.

## Choose a topic

<a id="prepare-the-workstation"></a>
<a id="verify-remote-access-before-leaving-the-machine"></a>

- [Prepare and access a CachyOS development workstation](cachyos-development-access.md) Set up the workstation and verify remote access.

<a id="edit-run-inspect-and-hand-back"></a>

- [Run a CachyOS development session](cachyos-development-session.md) Edit, run checks, inspect the native desktop, and hand back control.
