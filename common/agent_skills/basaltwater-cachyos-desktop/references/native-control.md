# User-approved KDE Wayland control

Run locally as the existing KDE user. `basaltw desktop --native doctor` checks
session sockets and library imports without requesting permission. `start`
requests KDE permission; only the desktop owner can select one monitor and
allow keyboard/pointer access. Wait for `status` to report `running`. Denied,
cancelled or incomplete consent grants no usable automation. If dependencies
are missing, use the workstation setup skill rather than replacing system Python.

`initializing` reports the current `portal_stage`. `awaiting-consent` means KDE
accepted Start and a response is pending, not that a visible dialog is verified.
After helper exit, `status.last_failure` retains the stage, timestamp and detail.
Stages/errors also remain in the private runtime `helper.log` across restarts.

The task helper uses a private same-user Unix socket, no network listener,
privileged input daemon, systemd service or login autostart. Its default
15-minute lifetime starts when control is ready; it closes on stop, portal revocation,
bus loss or loss of the session socket.
It is an account-local control channel, not a sandbox against other programs
already running as this account.

For autonomous future sessions the owner may opt in with `start --remember
--session-seconds 28800`, explicitly approving one monitor, keyboard, pointer
and KDE's persistence option. This requires RemoteDesktop version 2. Later
`start` calls use the saved token; KDE may decline restoration or require a new
prompt. `grant_saved` and `restore_attempted` do not verify prompt-free access.
The private grant record lives in `~/.local/state/basaltwater/native-desktop`
(0700 directory, 0600 file), outside projects and logs. Tokens are single-use;
the returned replacement is saved without exposing it in status or arguments.
An interrupted restore keeps its consumed identifier solely for revocation.

`renew --generation GEN --seconds SECONDS` accepts 60–28800 seconds, requires
running, unpaused, unexpired control, and cannot resume a human pause. Human
pause persists across grant restoration. After expiry use `start`, inspect
the new generation and recapture; initial or withdrawn grants need owner
approval. There is no automatic renewal or login control. `stop` keeps a grant;
owner `revoke` closes control, deletes only its portal PermissionStore entries
and removes the saved record, including while stopped. Retry reported store
errors; never work around revocation with a different input service.

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

```fish
basaltw desktop --native input click --generation GEN --geometry 2560 1440 --x 400 --y 200
basaltw desktop --native input key --generation GEN --geometry 2560 1440 --key ctrl+s
basaltw desktop --native input key --generation GEN --geometry 2560 1440 --key Return --hold-ms 120
basaltw desktop --native input text --generation GEN --geometry 2560 1440 \
  --text 'short trusted command' --delay-ms 20
```

Keys use XKB names such as `Return`, `Shift_L`, `F4`, `ctrl+s`.
Key chords and pointer buttons 1–3 accept `--hold-ms 0–5000`
(default 0); use 120 ms for game buttons that actuate during a press animation.
Pause, stop, expiry and client disconnection interrupt holds and release inputs.
Hold duration does not apply to text or scroll buttons. Text defaults
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

Open `handoff` while control is enabled to give the owner Pause, Resume,
Stop and Revoke saved access buttons. `control pause` revokes the current 30-second operation lease
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
