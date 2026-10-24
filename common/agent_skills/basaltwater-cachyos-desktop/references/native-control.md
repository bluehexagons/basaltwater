# User-approved KDE Wayland control

Run locally as the existing KDE user. `basaltw desktop --native doctor` checks
session sockets and library imports without requesting permission. `start`
requests KDE permission; only the desktop owner can select one monitor and
allow keyboard/pointer access. Wait for `status` to report `running`. Denied,
cancelled or incomplete consent grants no usable automation. If dependencies
are missing, use the workstation setup skill rather than replacing system Python.

`initializing` reports the current `portal_stage`. `awaiting-consent` means KDE
accepted Start and a response is pending, not that a visible dialog is verified.
`consent_expires_in` shows the approximate seconds left in the two-minute wait;
it is separate from session lifetime and also appears in the handoff window.
After helper exit, `status.last_failure` retains the stage, timestamp and detail.
Stages/errors also remain in the private runtime `helper.log` across restarts.

The helper uses a private same-user Unix socket, no network listener,
privileged input daemon, systemd service or login autostart. It has no default
lifetime limit; pending consent remains subject to the portal response timeout.
It closes on stop, portal
revocation, bus loss or loss of the session socket.
It is an account-local control channel, not a sandbox against other programs
already running as this account.

For autonomous future sessions the owner may opt in with `start --remember`,
explicitly approving one monitor, keyboard, pointer
and KDE's persistence option. This requires RemoteDesktop version 2. Later
`start` calls use the saved token; KDE may decline restoration or require a new
prompt. `grant_saved` and `restore_attempted` do not verify prompt-free access.
The private grant record lives in `~/.local/state/basaltwater/native-desktop`
(0700 directory, 0600 file), outside projects and logs. Tokens are single-use;
the returned replacement is saved without exposing it in status or arguments.
An interrupted restore keeps its consumed identifier solely for revocation.
Plain `start` preserves the saved opt-in and agent control state even without a reusable
token, requesting fresh owner approval if needed. Use `revoke` to remove the
opt-in before starting temporary access.

An optional `start --session-seconds SECONDS` bounds backend lifetime to 60–28800
seconds. `renew --generation GEN --seconds SECONDS` changes that limit on a
running, enabled, unexpired backend; `0` (the default) removes it. Neither
changes human permission. After an explicit limit expires, use `start`, inspect
the new generation and recapture; initial or withdrawn grants need owner
approval. There is no login autostart. `stop` keeps a grant;
owner `revoke` closes control, deletes only its portal PermissionStore entries
and removes the saved record, including while stopped. Retry reported store
errors; never work around revocation with a different input service.

## Sleeping displays

A sleeping monitor can leave capture black, frozen or unavailable even when the
application and portal are running. While agent control is enabled, try
`basaltw desktop --native wake` from the existing KDE session. It requests
`kscreen-doctor --dpms on` (from `libkscreen`) with a ten-second timeout, without
typing keys, moving the pointer, changing permission, or starting a portal.
It also works before the native helper starts. Disabled and human override
still block this agent action; do not enable agents merely to wake the monitor.

Wait briefly, then take and inspect a fresh capture before input. A successful
wake request does not prove the physical display or capture is ready. If needed,
inspect `kscreen-doctor --dpms show` and `kscreen-doctor -o`; an unplugged,
physically switched-off, disabled or locked display needs separate diagnosis.
Do not unlock, enable outputs, change layout or disable idle power policy as a
substitute. If capture stays stuck after wake, inspect status before restarting
the helper and obtaining any KDE approval it requests.

For Moonlight connection failures while the monitor sleeps, see
[Sunshine display wake](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-maintenance.md#sleeping-monitors-and-moonlight).

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
Human override, disabled state, stop, backend expiry and client disconnection
interrupt holds and release inputs.
Hold duration does not apply to text or scroll buttons. Text defaults
to 10 ms pacing. Replace the example generation, dimensions and coordinates
with the actual screenshot values. The CLI limits text submission to 20 seconds
and checks the human control revision between characters. In Blender's console, load a task script through a short
`exec(open('/absolute/task/edit.py').read())` command, inspect the typed command,
then submit Return. Do not type long scripts rapidly into the console.

For semantic controls use `inspect --pid PID --name NAME --role ROLE` with
exact selectors; scope repeated reads to an observed `--root` where possible.
Use `--save-output` for a broad `inspect`, `windows`, `wait` or `wait-element`:
the full JSON is stored privately and the response contains counts, at most
three preview entries, and an `artifact` path. Read only needed fields from
that file; scan truncation/partial coverage remain authoritative. Counts refer
to returned items, not all controls. Delete disposable observations when done.

`element OPERATION --ref REF --generation GEN` uses a fresh observed reference from that PID.
References expire after 60 seconds and are invalidated by mutations and human control changes.
Inspect again after each action. Password controls are excluded. A partial or
empty accessibility tree is not proof that an application/window is absent.

Read `--help` for inspect/element/wait/sequence arguments. `windows` exposes
showing AT-SPI top-level windows only, always reports partial coverage, and
does not identify compositor focus. Active-window waits, compositor window
focus/move/resize, window-specific capture, clipboard, drag gestures and
desktop logout are unavailable. Never substitute a monitor screenshot for a
requested private window capture. Use observed portal input or application
scripting when these limits matter.

## Missing or stuck physical keys

Stop native control and inspect `status` before diagnosing physical input.
Ask the owner whether the key fails across applications and with Shift. Check
the active layout and shortcuts; do not infer multiple physical keyboards from
Linux device names. A keyboard LED response does not prove an event reached KDE.

If comparison is needed, use a disposable field that logs only the affected
key and a working neighbor's press/release events. Ask the owner for a short
physical sequence, then compare with one bounded portal press/release using
existing approved access. Verify the field's focus immediately before input
and read its log; a screenshot alone cannot prove missing events. Stop control
afterward, retaining the saved grant. Do not collect unrelated keystrokes or
work around device permissions with a privileged input daemon.

If physical input still fails while portal input works, inspect other input
providers, especially Sunshine's `libvirtualhid Keyboard`. A disconnected client
does not establish that its virtual keyboard has clean state. Read user-service
status and recent logs, and check for an active stream before a targeted restart.
Discover the local unit rather than assuming its name.
When Sunshine is idle and a restart is within the authorized troubleshooting
scope, use `systemctl --user restart UNIT`, wait for it to become active, and
have the owner retest the physical key. Preserve configuration, pairings and
enablement. Do not interrupt an active stream without the owner's authorization.

If a targeted restart fails, continue diagnosis rather than repeating it or
resetting unrelated input services. Close only disposable test windows and keep
desktop captures/logs private.

## Human control and shutdown

Check `status.agent_control`: `enabled` permits authorized task input without
asking to unpause or obtaining a lease. `human-override` and `disabled` block
launches, input and semantic mutations while observation remains available.
Only explicit human approval or the human UI permits a state change; asking to
use an application alone is insufficient. Never change the state to bypass a block.

The handoff window offers Take control (15 minutes by default, or indefinite),
Enable agents, and Disable agents, with backend health shown separately.
State survives helper restarts, logout and reboot. A timed override stores its
original deadline and returns to enabled as approved by that human choice.
Indefinite override and disabled never expire. For an explicitly approved CLI
change, use `control enabled|human-override|disabled --human-approved`;
override accepts `--seconds 900` or `--indefinite`. State controls work even while
the helper is stopped. See [persistent control](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md)
for migration, storage, and the same-account authorization boundary.

In-flight operations can finish before a human request is processed (bounded
capture/accessibility timeouts); this is not emergency compositor isolation.

`stop` closes the portal and helper, preserving the desktop and applications.
Stop, revoke and closing the handoff window do not change agent permission.
Leave enabled control available between tasks. Close
only disposable instances created for the task, after confirming saved files.
Keep editable sources, exports and useful verification logs with the project.
See the [native desktop guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-desktop.md)
for implementation limits and live qualification evidence.
