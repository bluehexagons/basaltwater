# Native desktop application work on CachyOS

Use the existing KDE Wayland session and native packages. The managed
`basaltwater-cachyos-desktop` skill covers application scripts, assets, exports,
and native interaction. Discover only the needed application with
`basaltw agent manifest . --application blender --json`; executable presence
does not establish editing, export, GPU, or audio readiness.

For Godot, Electron, and native project launches over T3, Sunshine/Moonlight,
or SSH, use the [remote development guide](cachyos-development.md).
`basaltw desktop --native develop` launches supervised tasks without requesting
screen/input access, while honoring the persistent agent control state.

## Agent permission and portal access

[Agent desktop control](desktop-control.md) explains the three persistent states:
enabled, human override, and disabled. Enabled agents can perform authorized
desktop work without asking to unpause or obtaining a control lease. The human
control window offers timed or indefinite takeover and explicit enable/disable
controls. Restarting a helper or closing a connection preserves this choice.
Open **Agent Desktop Control** from KDE's application menu even when the helper
is stopped or agents are disabled; setup installs the launcher with native tools.

KDE portal permission is separate. Start locally as the graphical desktop user:

```fish
basaltw desktop --native doctor
basaltw desktop --native start --remember
# Approve one monitor, keyboard, pointer, and persistence if KDE requests them.
basaltw desktop --native status
basaltw desktop --native handoff
basaltw desktop --native exec -- inkscape /absolute/task/icon.svg
basaltw desktop --native screenshot --output /absolute/task/observe-1.png
```

`--remember` opts into a reusable KDE grant. Later `start` calls restore it when
KDE permits; initial or withdrawn access can still require human approval.
Wait for `running` before input. Native helpers have no default lifetime limit.
An optional `--session-seconds` bounds backend availability without changing
agent permission. Nothing starts the helper at login.

If a monitor is asleep or capture is black/frozen, enabled agents should try
`basaltw desktop --native wake`, wait briefly, and inspect a fresh screenshot
before input. This bounded KDE display-power request needs no running portal
and preserves agent permission, screen locking and idle policy. See the
[native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md#sleeping-displays)
and [Moonlight recovery](cachyos-maintenance.md#sleeping-monitors-and-moonlight).

The [native control reference](../../common/agent_skills/basaltwater-cachyos-desktop/references/native-control.md)
is the operational guide for consent stages, saved grants, bounded capture/input,
accessibility, physical-key troubleshooting, stop/revoke, and recovery.
It is installed with the skill; use it for exact procedures and limits.

## Installation and verification

Selecting a supported desktop application during setup installs
`python-gobject`, `at-spi2-core`, `gstreamer`, `gst-plugins-base`,
`gst-plugin-pipewire`, `gtk3`, `libxkbcommon`, and `libkscreen`. Blender also selects
`libdecor`. Setup never starts automation or changes KDE preferences, account
groups, graphics drivers, or existing control permission. Rerun setup to install
runtime and guidance changes; restart an existing native helper to load them.
The CachyOS doctor reports prerequisites without starting automation.

Use `--native` explicitly; ordinary `basaltw desktop` addresses Debian XRDP.
The helper uses a private same-account Unix socket, without a network listener,
global input daemon, systemd service, or autostart.

Prefer application scripting for bulk edits and focused UI checks for interaction.
Use exact PID/name/role/root selectors, fresh monitor captures, task copies,
and isolated test profiles. Reopen editable sources and verify exports in their
consuming project. [Application recipes](desktop-development.md) explain native
API differences, companion files, formats, and render settings.

Capture/input permission does not establish every application's readiness.
Window inventory is partial AT-SPI coverage; it cannot prove compositor focus
or window absence. Window-specific capture, compositor window management,
clipboard, drag gestures, and desktop logout remain unavailable. Monitor images
can include personal content; inspect evidence before sharing it.
