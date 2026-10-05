---
name: basaltwater-cachyos-desktop
description: Edit and verify Blender models and vector, raster, audio or video assets with native applications on a CachyOS KDE workstation, using application scripts, exports and user-approved Wayland capture/input. Use for application workflows and native GUI work, not workstation installation or VM XRDP control.
metadata:
  managed-by: basaltwater
---

# Native CachyOS application work

Use the existing desktop account, project and KDE Wayland session. Start with
`basaltw agent manifest --json`: applications include active executables,
launch argument vectors, workflows and guidance. PATH presence is discovery;
it does not establish editing, export, GPU or audio readiness.

Complete requested asset edits autonomously using application scripting,
repeatable CLI exports and available native UI tools. Save editable task copies,
reopen saved results and verify delivery files in the consuming project. Human
review is optional when requested. Report any required UI check that cannot be
performed with the session's available tools; a successful file export does not
establish native interaction coverage.

Read the reference for the task:

- [Blender](references/blender.md): model/material/UV edits, `bpy`, textured
  glTF/GLB and OBJ/MTL round trips, rendering and private test preferences.
- [Media](references/media.md): Inkscape object actions and SVG/PNG exports,
  GIMP/Krita editable sources, Audacity/Shotcut audio/video verification,
  and the other supported creative and engineering applications.

## Launch and observe

Launch the active executable directly from the invoking graphical session:

```fish
blender /absolute/project/model.blend
inkscape /absolute/project/assets/icon.svg
gimp /absolute/project/assets/banner.xcf
krita /absolute/project/assets/sprite.kra
audacity /absolute/project/assets/sound.wav
shotcut /absolute/project/assets/clip.mlt
```

Use the manifest's actual executable when PATH shadows or multiple installations
exist. Normal project work uses its intended preferences and add-ons. Disposable
checks should use isolated profiles and synthetic files, preserving personal
settings and unrelated running applications.

Use `basaltw desktop --native` explicitly for KDE Wayland. The ordinary
`basaltw desktop` backend remains Debian XRDP/Xorg. Start locally from the
graphical session; background scripts/exports can work without it. Do not
create XRDP, guess another session's bus/display, force X11 or install global
input daemons. See the [native control reference](references/native-control.md).

```fish
basaltw desktop --native doctor
basaltw desktop --native start
# The desktop owner selects one monitor and grants keyboard/pointer access in KDE.
basaltw desktop --native status
basaltw desktop --native handoff
basaltw desktop --native exec -- inkscape /absolute/task/icon.svg
basaltw desktop --native screenshot --output /absolute/task/observe-1.png
```

Prefer PID-scoped accessible controls where usable; use fresh selected-monitor
captures for custom canvases such as Blender. Monitor capture can include
unrelated content: inspect locally and share only reviewed evidence. Check
window identity, focus, mode and bounds before shortcuts. `windows` is a partial
AT-SPI inventory, not a complete compositor/focus inventory. Keep captures
recent and coordinates within the selected monitor; new files are never
overwritten. Restart the portal after monitor/scaling/resolution changes.
Honor human pause without automatically resuming it. Stop with
`basaltw desktop --native stop` when finished; KDE and applications keep running.
The portal session expires after 15 minutes and is never restored at login.
Package/portal observations in `basaltw local cachyos-doctor --json` do not
grant or prove automation. Report unavailable checks accurately.

For web consumers use the session's collaborative browser tools and browser
policy. A desktop editor preview does not prove browser/game rendering fidelity.
Keep output paths, versions, export settings and useful evidence with the task;
avoid including unrelated desktop content in captures.

Native packages and their dependencies stay under normal CachyOS maintenance.
Use `basaltwater-cachyos-workstation` for installation and diagnostics, and the
[desktop development guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/DESKTOP_DEVELOPMENT.md)
for shared application recipes and the distinction between artifact and UI checks.
