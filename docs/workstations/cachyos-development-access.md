# Prepare and access a CachyOS development workstation

[Topic index](cachyos-development.md)

## Prepare the workstation

Start with the [CachyOS installation guide](cachyos.md). Preview the selected
tools as the existing desktop account, without sudo:

```fish
basaltw setup agent_cachyos localhost --t3code-desktop --godot --codex \
  --game-dev --git-lfs --sunshine --dry-run
```

Add `--node --node-versions` for Electron/TypeScript work, `--python` for project
tools, and selected creative applications for asset work. `--game-dev` supplies
native libraries and development/debugging tools; `--godot` selects the native
engine package. Project-specific C#/.NET and GDExtension requirements need their
own checks. The [software guide](cachyos-software.md) and
[native/Electron guide](cachyos-game-development.md) describe package scope.
Moonlight belongs on the connecting client unless this workstation also needs
to act as a streaming client. Pair Sunshine and configure the intended access
policy through the existing setup and maintenance procedures.

For remote shell access, local setup follows detected SSH service state and
accepts `--ssh` or `--no-ssh` as explicit overrides. Remote-controller setup
defaults to SSH. The root-managed service runs without KDE login and grants the
existing non-root account key-only IPv4 port 22 access. When SSH is enabled,
private LAN firewall discovery is selected by default unless explicit access
flags disable automatic discovery or select private sources. When SSH is
enabled, automatic discovery manages selected T3, Sunshine, and SSH firewall
ports. Discovery runs again on each setup and refresh; explicit
`--access-source` values persist. Password modes require explicit private
access flags. For initial key enrollment, a one-shot password window accepts
the existing account password on port 2222 for 1–60 minutes; complete
`ssh-copy-id -p 2222 USER@WORKSTATION_PRIVATE_IP`, then verify normal
`ssh USER@WORKSTATION_PRIVATE_IP` before expiry. This works for a fresh
key-only service without a preexisting key. Key-only adoption of enabled stock
sshd requires usable `authorized_keys`. Verify the host fingerprint at the
workstation. Read [SSH access](cachyos.md#ssh-access) for service adoption
limits, password modes, and refresh behavior.

From the controller, preview and apply with the existing desktop username:

```fish
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --dry-run --codex
basaltw setup agent_cachyos WORKSTATION_PRIVATE_IP USER --godot --node --codex
```

The preview makes no SSH connection. Apply requires already reachable SSH for
the existing non-root account and its sudo access for privileged steps. The
controller must connect from a private IPv4 source allowed by the selected
firewall policy; preflight stops before changing the firewall if the current
SSH client source is outside those sources. GUI selections that need
the desktop user bus/session still require that user's existing KDE session;
SSH alone does not open a GUI session.

## Verify remote access before leaving the machine

Use the actual remote client to verify each selected access path independently:

1. Connect through Moonlight; confirm video, game audio, keyboard, pointer,
   controller input when used, and reconnection to the existing desktop.
2. Open a T3 provider thread and terminal. Verify provider credentials and
   executable discovery from the KDE-launched app, not only from SSH.
3. Connect through SSH as the same desktop account, locally or from the
   separate controller. Keep it available for diagnostics when streaming or a
   graphical application fails. SSH service and firewall configuration are
   separate from selecting Sunshine.
4. If agent desktop control is wanted, approve the selected monitor and devices
   with `basaltw desktop --native start --remember`. Test restoration from the
   intended T3 context. A saved grant can still require renewed approval; handle
   its KDE dialog through Moonlight when streaming is available.
5. Check monitor-off behavior, desktop locking, and stream disconnection with
   the owner present. Record which cases work; logout/reboot, display changes,
   and different hardware need separate qualification.

T3 desktop and selected Sunshine start after KDE login. A normal quit can leave
either stopped until the next login. Read
[startup and recovery](cachyos-maintenance.md) before changing services remotely.
Target the affected service; preserve unrelated editors, projects, pairings,
and application settings. Reboot and logout require a plan for the next login.
