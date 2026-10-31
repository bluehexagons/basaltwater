---
name: basaltwater-cachyos-desktop
description: Edit and verify native application assets, scripts, and exports in an existing CachyOS KDE session, including user-approved Wayland capture/input.
metadata:
  managed-by: basaltwater
---

# Native CachyOS application work

Use the existing desktop account, project and KDE Wayland session. Discover
only what the task needs: `basaltw agent manifest . --application blender --json`
for Blender, or `--capability development` for Godot/Electron/native project
recipes. Repeat `--application NAME` for another app. PATH presence is discovery,
not editing, export, GPU or audio readiness.
Use `--capability workstation` for a compact installed-tool/backend/GPU report;
follow the [workflow routes](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/workflows.md)
for commands. Project policy and scripts own project-specific decisions and
capture recipes; this skill owns native session operation.

Complete requested edits using repeatable application scripts/exports and
available native UI tools. Save editable task copies, reopen saved results and
verify delivery files in the consuming project. Use UI checks where the task
requires them; report checks unavailable in the session. A successful export
does not establish native interaction coverage. Human review is optional when
requested.

Read only the reference needed for the current operation:

| Task | Read |
| --- | --- |
| Godot/Electron/native launches, project checks, task logs | [Development tasks](references/development.md) |
| Blender model/material/UV edits, bpy, export round trips | [Blender](references/blender.md) |
| Vector, raster, audio, video and other native assets | [Media](references/media.md) |
| Capture backends, Xvfb, high-resolution and post-refresh evidence | [Rendering evidence](references/rendering.md) |
| Persistent agent control, Wayland consent, accessibility, input and recovery | [Native control](references/native-control.md) |

## Keep the desktop loop focused

Use scripts for bulk edits and file tools for export/content checks. Observe
before and after a bounded operation; use exact PID/name/role/root selectors
for controls and fresh monitor captures for custom canvases. Add
`--save-output` to `windows`, `inspect`, `wait`, or `wait-element` to retain
full JSON in a private temporary file with a short preview. Read needed fields
from its artifact path. Partial/empty AT-SPI inventories do not establish
window absence or compositor focus; shortened output never implies completeness.

Keep scripts and verbose logs in task files/private artifacts. Use the
project-development skill's capture helper for verbose commands; return status,
actionable excerpts and paths. Maintain a short current task note with decisions,
unresolved issues, job handles and validation rather than copying full histories.
Do not relaunch a mutation after timeout without checking its state.

Follow project-development and active orchestration guidance for delegation and
handoffs. Only one worker controls the shared desktop.

## Native control

Use `basaltw desktop --native` explicitly; ordinary `desktop` is Debian XRDP.
Read the native control reference before input. Start locally from the graphical
session with owner-approved monitor/keyboard/pointer access; background scripts
and exports can work without capture/input. Never create XRDP, guess another
session's bus/display, switch KDE to X11, or install global input daemons.

When `status.agent_control` is `enabled`, use authorized desktop software without
asking to unpause or acquiring a lease. `human-override` and `disabled` block
agent mutations; change these states only with explicit human approval or the
human control UI. A desktop task alone does not authorize a state change.
Timed override returns automatically at its human-approved deadline.
If the monitor is asleep or captures are black/frozen, try
`basaltw desktop --native wake` while enabled, then recapture before input.
Verify identity, focus, mode and current selected-monitor geometry before input; restart the portal
after monitor/scaling/resolution changes. Review captures before sharing unrelated
content. Leave enabled control available between tasks, preserving KDE and applications.
For missing/stuck physical keys, stop control and read the native recovery steps.

For web consumers, follow session browser policy and the installed browser skill
when available (`basaltwater-t3-preview-testing` for T3 on CachyOS).
Use the permitted available browser after confirmed T3 unavailability; preserve
profiles and existing sessions. Report browser, origin and observed checks;
editor previews and desktop pixels do not establish DOM/network or game fidelity.

Use `basaltwater-cachyos-workstation` for installation/diagnostics. Keep output
paths, versions, export settings and useful evidence with the task.
