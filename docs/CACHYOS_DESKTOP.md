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
`consent_expires_in` and the handoff window show the approximate seconds left
in the two-minute response wait. This deadline is separate from session lifetime.
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
Ordinary first-time `start` uses no persistence. Nothing starts control at login.

## Reuse an approved grant

For unattended future tasks, opt in once from the graphical user's session:

```fish
basaltw desktop --native start --remember --session-seconds 28800
# The owner approves one monitor and both input devices, including KDE's restore option.
basaltw desktop --native status
# Later tasks can restore that grant; KDE may still require approval:
basaltw desktop --native start
basaltw desktop --native renew --generation GEN --seconds 28800
# Owner-controlled removal, including when the helper is stopped:
basaltw desktop --native revoke
```

`--remember` requires RemoteDesktop portal version 2 and requests `persist_mode
2`. KDE can refuse persistence or ignore an invalid/withdrawn token and ask
again. `grant_saved` reports a returned token, and `restore_attempted` reports
its submission; neither proves that restoration will avoid a prompt.
`interactive_required` is `null` when approval cannot yet be determined, and
`false` only for established running control. No grant
is manufactured and no global input service replaces the portal. The desktop
owner makes the initial monitor/device and persistence choices.

The private record is `~/.local/state/basaltwater/native-desktop/grant.json`
(directory 0700, file 0600). Tokens are consumed once and replaced with the
returned token. An interrupted restore retains the consumed identifier only for
revocation, never for reuse. Tokens stay out of logs, status and process arguments.
Plain `start` keeps the saved opt-in even when restoration was interrupted or
KDE returned no token; a new owner approval may be needed. Use `revoke` to remove
the opt-in before switching back to temporary access.
Human pause persists across restored sessions; agents must not resume it.
`stop` retains the grant, while `revoke` closes control, deletes only its
`remote-desktop` PermissionStore entries and removes the private record. Store
errors retain state for retry, including replacement tokens from incomplete
device consent or invalid monitor geometry. Revocation waits for initialization
to finish before removing the record; if a helper is still stopping, retry
after it exits. The handoff window also offers Revoke saved access.
KDE's own session controls can stop active sharing, and withdrawn permissions
prevent subsequent restoration.

Session lifetime defaults to 900 seconds from readiness. `--session-seconds`
and `renew --seconds` accept 60–28800 seconds, at most eight hours per explicit
start/renewal. Initialization and pending consent use the portal's bounded
response timeout without consuming that lifetime. Renewal requires the current
generation and a running, unpaused, unexpired session; it does not grant new
devices or extend operation leases.
After expiration use `start` and observe the new generation/capture. There is
no timer that renews control or resumes a pause automatically.

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

The handoff window provides Pause, Resume, Stop and Revoke and reports pending consent,
failed sessions and expiration without claiming input is enabled. Human control
requests wait in order behind an in-flight status poll or control request.
`control pause` revokes the current operation lease and blocks launches, input
and semantic mutations; observation remains available. In-flight bounded
operations may finish before pause is processed. Honor human pause; do not
resume it automatically.
`basaltw desktop --native stop` closes the portal/helper without logging out
KDE or closing applications. Sessions expire after their configured lifetime; revocation,
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
