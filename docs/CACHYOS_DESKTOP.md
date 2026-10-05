# Native desktop application work on CachyOS

Use the existing KDE Wayland session and native CachyOS packages. The managed
`basaltwater-cachyos-desktop` skill covers Blender model/material/UV work,
Inkscape vectors, GIMP/Krita raster assets, audio/video editors and the other
supported applications. `basaltw agent manifest --json` discovers installed
executables with native launch arguments and workflow guidance. Discovery
keeps readiness unverified; it does not launch applications or request access.

## Start a task's portal session

Run locally as the graphical desktop user:

```fish
basaltw desktop --native doctor
basaltw desktop --native start
# Select one monitor and allow keyboard/pointer access in KDE's dialog.
basaltw desktop --native status
basaltw desktop --native handoff
basaltw desktop --native exec -- inkscape /absolute/task/icon.svg
basaltw desktop --native screenshot --output /absolute/task/observe-1.png
```

`start` first reports `initializing`, with the current `portal_stage`. Only
after KDE accepts the Start request does it report `awaiting-consent`, meaning
a portal response is pending; this does not prove that a dialog is visible.
Wait for `running` before interaction. Basaltwater cannot grant portal permission.
Cancelled, denied or incomplete consent does not enable usable control.
If the helper reports a failed session, `start` and `status` return a nonzero
exit code with the failure detail; pending consent remains a normal start state.
After the helper exits, `status` reports `stopped` and retains `last_failure`
with its stage, timestamp and detail in the private runtime `status.json`.
The same stages and errors are appended to
`/run/user/UID/basaltwater-wayland/helper.log`; restarting does not erase it.
The combined [RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)/
[ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
session shares exactly one selected monitor and requests both input devices.
There is no saved grant or automatic control at login.

Selecting a supported desktop application during setup, including Godot,
Material Maker, Moonlight or the sysadmin GUI tools, installs
`python-gobject`, `at-spi2-core`, `gstreamer`, `gst-plugins-base`,
`gst-plugin-pipewire`, `gtk3` and `libxkbcommon`. Setup never starts the helper or changes
KDE preferences, account groups or graphics drivers. The default
`basaltw desktop` backend continues to address Debian XRDP; `--native` explicitly
selects KDE. No root access, global input daemon or remote listener is needed.
The CachyOS doctor and setup receipt mark these prerequisites as selected;
missing packages appear as failures without starting automation.

## Observe and edit

Captures create new private PNG files and return generation and pixel geometry.
Inspect the selected-monitor capture before input, identify the application
and check focus/mode. Coordinates use capture pixels and map to the portal's
logical monitor size. Input rejects mismatched geometry/generation, coordinates
outside that monitor and captures older than 60 seconds. Recapture after layout
changes. Stop/start with fresh consent after monitor, scaling or resolution
changes to refresh portal coordinates; automatic change detection is limited.

```fish
basaltw desktop --native input click --generation GEN --geometry WIDTH HEIGHT --x X --y Y
basaltw desktop --native input key --generation GEN --geometry WIDTH HEIGHT --key ctrl+s
basaltw desktop --native input click --generation GEN --geometry WIDTH HEIGHT --x X --y Y --hold-ms 120
basaltw desktop --native input text --generation GEN --geometry WIDTH HEIGHT \
  --text 'short trusted command' --delay-ms 20
basaltw desktop --native inspect --pid PID
basaltw desktop --native element set-text --ref REF --generation GEN --text '40'
```

Native key chords and pointer buttons 1–3 accept `--hold-ms 0–5000` (default
0). Use a 120 ms press for game controls that actuate during a press animation.
Hold duration is separate from `--delay-ms` text pacing. Pause, stop, expiration
or client disconnection interrupts a hold and releases inputs; release failures
stop control. Scroll buttons and text cannot take a hold duration.
Text defaults to 10 ms pacing with a 20-second CLI budget. For Blender's
console, write a trusted task script and type a short loader, inspect the
entered command, then submit Return. Prefer bounded PID-scoped AT-SPI actions
for exposed controls. References expire after 60 seconds and are invalidated
by mutations/pause; inspect again after actions. Password controls are excluded.
Task launches enable accessibility in their GTK/Qt process environment without
changing desktop-wide settings. Existing applications may still expose no tree.

Use task copies and isolated test profiles; preserve personal settings and
unrelated applications. Verify editable sources by reopening them and check
exports in the consuming project. [Application recipes](DESKTOP_DEVELOPMENT.md)
and the skill's Blender/media references explain native API differences,
texture companions, export formats and render settings. Monitor captures can
include personal content; review evidence before sharing it.

## Pause, stop and limitations

The handoff window provides Pause, Resume and Stop and reports pending consent,
failed sessions and expiration without claiming input is enabled. Human control
requests wait in order behind an in-flight status poll or control request.
`control pause` revokes the current operation lease and blocks launches, input
and semantic mutations; observation remains available. In-flight bounded
operations may finish before pause is processed. Honor human pause; do not
resume it automatically.
`basaltw desktop --native stop` closes the portal/helper without logging out
KDE or closing applications. Sessions expire after 15 minutes; revocation,
portal/bus loss or disappearance of the session socket stops control.

The private helper socket permits only the same desktop account. It does not
sandbox other programs already running as that account. The helper installs
no systemd service or autostart and opens no network listener. Capture is
bounded to 32 megapixels and a 64 MiB compressed artifact.

Window inventory covers showing AT-SPI top-level windows only and always
reports partial coverage. It cannot prove window absence or compositor focus.
Compositor focus/move/resize, active-window waits, private window capture,
clipboard, drag gestures and desktop logout are unavailable. Unsupported
capture requests fail rather than silently capturing the entire monitor.

The [qualification record](plans/CACHYOS_AGENTIC_DESKTOP_QUALIFICATION.md)
records successful native Blender/Inkscape checks and the remaining application,
hardware and lifecycle cases. Package installation, library imports and a
successful screenshot do not establish every application's editing readiness.
