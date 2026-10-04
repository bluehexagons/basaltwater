# User-approved KDE Wayland control

Run locally as the existing KDE user. `basaltw desktop --native doctor` checks
session sockets and library imports without requesting permission. `start`
opens KDE's portal dialog; only the desktop owner can select one monitor and
allow keyboard/pointer access. Wait for `status` to report `running`. Denied,
cancelled or incomplete consent grants no usable automation. If dependencies
are missing, use the workstation setup skill rather than replacing system Python.

The task helper uses a private same-user Unix socket, no network listener,
privileged input daemon, saved grant, systemd service or login autostart. Its
15-minute lifetime starts when launched; it closes on stop, portal revocation,
bus loss or loss of the session socket. Starting again needs fresh consent.
It is an account-local control channel, not a sandbox against other programs
already running as this account.

## Observe, act, verify

Use `exec -- APPLICATION ARGS...` with absolute task paths and the intended cwd.
It returns `pid` and `launch`; `launch-status TOKEN --generation GEN`
checks that process, not document readiness. Task launches enable GTK/Qt
accessibility in their environment, preserving desktop-wide preferences.
Isolate test profiles to avoid reusing a personal application instance.

`screenshot --output /absolute/task/new.png` captures the selected monitor,
returns `generation` and `geometry`, and creates a private new file. Capture
before input, inspect it locally, identify the application and ensure focus.
Coordinates are capture pixels, converted to the portal's logical monitor
size. Recapture after layout changes. Stop/start with fresh consent after
monitor, scaling or resolution changes to refresh portal coordinates. Input
rejects snapshots older than 60 seconds or mismatched geometry/generation.

```bash
basaltw desktop --native input click --generation GEN --geometry 2560 1440 --x 400 --y 200
basaltw desktop --native input key --generation GEN --geometry 2560 1440 --key ctrl+s
basaltw desktop --native input text --generation GEN --geometry 2560 1440 \
  --text 'short trusted command' --delay-ms 20
```

Keys use XKB names such as `Return`, `Shift_L`, `F4`, `ctrl+s`. Text defaults
to 10 ms pacing. Replace the example generation, dimensions and coordinates
with the actual screenshot values. The CLI limits text submission to 20 seconds
and checks pause between characters. In Blender's console, load a task script through a short
`exec(open('/absolute/task/edit.py').read())` command, inspect the typed command,
then submit Return. Do not type long scripts rapidly into the console.

For semantic controls use `inspect --pid PID` and bounded name/role filters;
`element OPERATION --ref REF --generation GEN` uses a fresh observed reference from that PID.
References expire after 60 seconds and are invalidated by mutations and pause.
Inspect again after each action. Password controls are excluded. A partial or
empty accessibility tree is not proof that an application/window is absent.

Read `--help` for inspect/element/wait/sequence arguments. `windows` exposes
showing AT-SPI top-level windows only, always reports partial coverage, and
does not identify compositor focus. Active-window waits, compositor window
focus/move/resize, window-specific capture, clipboard, drag gestures and
desktop logout are unavailable. Never substitute a monitor screenshot for a
requested private window capture. Use observed portal input or application
scripting when these limits matter.

## Human control and shutdown

Open `handoff` while control is enabled to give the owner Pause, Resume and
Stop buttons. `control pause` revokes the current 30-second operation lease
and blocks input, launches and semantic mutations. Observation remains
available. In-flight operations can finish before pause is processed (bounded
capture/accessibility timeouts); pause is not emergency compositor isolation.
Do not issue `control resume` to work around a human pause.

`stop` closes the portal and helper, preserving the desktop and applications.
The handoff window can be closed independently without resuming input. Close
only disposable instances created for the task, after confirming saved files.
Keep editable sources, exports and useful verification logs with the project.
See the [native desktop guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/CACHYOS_DESKTOP.md)
for implementation limits and live qualification evidence.
