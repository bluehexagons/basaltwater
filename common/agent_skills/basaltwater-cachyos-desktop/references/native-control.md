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

Native `doctor` reports timestamped `checks.display_power` (on/off/mixed/unknown),
and the local CachyOS doctor includes `session.display-power`. These read-only
queries do not wake displays. An unlocked session/XWayland response does not
establish display power; unknown observations and partial output coverage remain
unverified.

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

For evaluation discovery, use `desktop --native preflight --repository PATH
--instruction /absolute/required/SKILL.md --json`. It saves verbose observations
privately and keeps unknown checks explicit. Retrieve required text with
`desktop instructions PATH --start N --sha256 DIGEST`; follow returned unread
ranges rather than bulk-reading files until tool output truncates. See the
[evaluation receipts](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-evaluation.md)
for limits: matching hashes cannot prove model comprehension or session-loaded
skill identity. Preflight never captures, starts the helper or changes control.

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
rejects snapshots older than their selected lifetime or mismatched geometry/generation.
`screenshot --max-age-seconds N` selects 1–600 seconds (default 60) for that
capture only. Use longer ages for stable, untimed scenes. `input --observation
ID` binds one action to the latest inspected screenshot; mutations, a newer
capture or control changes invalidate it. Pointer and drag endpoints must stay
inside that observed image/crop. See the cross-platform
[observation contract](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-observations.md).

Native `status` and screenshots (including `--compact`) report
`capture_freshness`: age, remaining seconds, the selected maximum and `fresh`.
Age starts at capture admission, conservatively including frame acquisition.
This is a response-time snapshot for the helper's latest successful capture;
status queries and coordinate mapping never renew it. After long reasoning or
waiting for advice, recapture and inspect the scene/target before input if the
window expired or little time remains. Null age means no current capture.
Freshness does not establish focus, scene correctness or target stability.
See the [capture-window procedure](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/game-testing.md#keep-decisions-inside-the-capture-window).

Use `screenshot --crop X Y WIDTH HEIGHT` to inspect a bounded rectangle of that
same selected monitor. `geometry` remains the full-monitor pixel dimensions;
`image_geometry` describes the PNG and `image_origin` gives its monitor-pixel
offset. Convert a point in the cropped PNG with
`monitor_x = image_origin[0] + crop_x` and
`monitor_y = image_origin[1] + crop_y`, then pass those monitor coordinates and
the full `geometry` to input. Portal logical scaling is applied afterward by
the helper. Crop bounds are validated against the captured frame, and a crop
change counts as `changed` even when its pixel hash is identical. Cropping is
a monitor observation, not private window capture or compositor targeting.
The output, compact observation and evidence record retain this mapping.

When the image is presented at a different size, calculate monitor coordinates
before input with `basaltw desktop --native map-point --geometry WIDTH HEIGHT
--view-geometry WIDTH HEIGHT --x X --y Y`. Use the full-monitor capture geometry,
the size of the entire PNG as presented (excluding viewer margins), and points
relative to that view. Pass the same `--crop` for a cropped capture; optional
paired `--to-x`/`--to-y` map a drag endpoint. The read-only result includes
per-axis scale, origin and ready-to-copy `input_args`. Copy them without applying
another scale; the input helper handles portal logical scaling. No desktop
request is made, so this calculation cannot refresh an expired observation.
For unknown viewer transforms, obtain a known full view or a bounded native crop.
See [mapping examples and playtest qualification](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/game-testing.md).

Prefer saving the inspected `screenshot --compact` JSON and using `map-point
--capture /absolute/private/frame.json --view-geometry W H --x X --y Y`.
For viewer margins add `--view-origin LEFT TOP`; points include those offsets.
This mode checks frame identity, transform and helper freshness together,
returning `input_context_args` and `input_args` to copy. It makes a read-only
status request and never recaptures or sends input. A changed/recentered target
requires fresh inspection even when the token remains valid.

Native failures preserve `reason`, `dispatch_stage` and `input_delivery`.
`not-sent` proves a guard refused before dispatch; `completed` preserves input
when observation fails; `unknown` requires inspection without replay. Older
helpers/journals cannot prove these stages from a generic error. See the
[failure receipt contract](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-observations.md#native-input-failure-receipts).
Status and action replies expose recorder capacity and its existing artifact;
`headroom: low` recommends rotation with 50 events remaining. A duplicate start
reports that handle without creating another recorder. Follow the
[complete rotation example](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-evaluation.md#rotate-a-recording-at-a-safe-checkpoint):
different recordings belong in the same run ledger; `closeout --previous` only
links checks of the same recording.
For long evaluations, link explicit recorder segments to `desktop evidence run`
checkpoints under the same match clocks. Record owner waits and timing changes
explicitly; check due reviews in the controller's normal loop. `evidence closeout`
saves a new inventory/verification receipt without deleting media or stopping
processes. See the [evaluation recipes](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-evaluation.md#durable-controller-checkpoints).

```fish
basaltw desktop --native input click --generation GEN --geometry 2560 1440 --x 400 --y 200
basaltw desktop --native input key --generation GEN --geometry 2560 1440 --key ctrl+s
basaltw desktop --native input key --generation GEN --geometry 2560 1440 --key Return --hold-ms 120
basaltw desktop --native input text --generation GEN --geometry 2560 1440 \
  --text 'short trusted command' --delay-ms 20
basaltw desktop --native input drag --generation GEN --geometry 2560 1440 \
  --x 400 --y 200 --to-x 700 --to-y 500 --button 1 --duration-ms 300 --observe
```

Keys use XKB names such as `Return`, `Shift_L`, `F4`, `ctrl+s`.
For a game's physical bindings use `input keycode --codes 26` (left bracket),
`--codes 27` (right bracket), or `--codes 29 30` (left Ctrl+A). Codes are Linux
evdev keyboard codes, with no XKB +8 offset; they are not Godot key constants.
The native-only chord accepts 1–8 distinct integers from 1 through 255,
presses in order and releases in reverse. Use `--hold-ms` and `--observe` as
with symbolic chords. Layout, shortcuts and application focus still affect
the result. This uses the portal's
[NotifyKeyboardKeycode method](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html).
Verify delivered application events before claiming binding coverage: a native
Godot 4.7.2 probe reported bracket physical codes 123/125 on Wayland and 91/93
on XWayland for the same evdev codes 26/27. Physical injection does not normalize
an engine's backend-specific key mapping. See the
[dated qualification](https://github.com/bluehexagons/basaltwater/blob/main/docs/testing/cachyos.md#native-keyboard-and-task-inventory-improvements-2026-11-02).

`status.input_capabilities` identifies helpers supporting physical input;
restart older helpers after updating. `status.input_state` reports counts of
pending helper-injected key presses and their named modifiers during a hold.
It omits key values. A failed release retains its pending count and stops the
helper. This is not global keyboard state: physical modifiers, lock keys and
other input providers remain unavailable. A zero count cannot establish that
the human's keyboard or another virtual keyboard has no stuck modifier.

Key chords and pointer buttons 1–3 accept `--hold-ms 0–5000`
(default 0); use 120 ms for game buttons that actuate during a press animation.
Human override, disabled state, stop, backend expiry and client disconnection
interrupt holds and release inputs.
Native `drag` interpolates a straight line between two in-bounds monitor-pixel
endpoints while holding button 1–3. Duration is 1–5000 ms (default 300); it
uses at most 250 motion steps and checks control, deadlines and client connection
between steps. Human override, disabled state, stop, expiry and disconnect
release the button. Failed releases stop the helper. `--hold-ms` does not apply
to drag. Inspect the application, focus, canvas mode and fresh coordinate
mapping before a gesture; successful input does not establish its effect.
Drag and crop behavior have mocked contract coverage; native application
interaction still needs task-specific verification.
Hold duration does not apply to text or scroll buttons. Text defaults
to 10 ms pacing. Replace the example generation, dimensions and coordinates
with the actual screenshot values. The CLI limits text submission to 20 seconds
and checks the human control revision between characters. In Blender's console, load a task script through a short
`exec(open('/absolute/task/edit.py').read())` command, inspect the typed command,
then submit Return. Do not type long scripts rapidly into the console.

For post-input evidence, add `--observe --settle-ms 150` to `input`. After input
release it waits the requested 0–5000 ms (default 120), skips the capture
stream's first sample and awaits a subsequent sample within eight seconds.
It returns input completion and frame receipt UTC/monotonic timestamps plus
stream-relative PTS; receipt/PTS are not compositor presentation or proof of
application readiness. A stalled/static stream may time out. Capture failure
preserves input completion: inspect and recapture; never replay the input.
Use `screenshot --next-frame --compact` for a separate bounded observation.
For one inspected input and two delayed captures, use the tested
[short observation recipe](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/short-observation-sequences.md).
It retains completed input, partial capture results and actual timing without
automatic replay. Screenshot `after_ms` delays belong to the Linux JSON sequence,
not standalone screenshot flags or Windows keyboard sequences.
Compact output retains path, generation, geometry, timestamps, pixel hash and
`changed` (null for the first comparison); full screenshot/status/doctor output
remains available. Pixel equality is not an application liveness assertion.

For manual playtest reports, `evidence start --project PATH --json` records
this helper's actions privately without text, key values or argv. Finish with
`evidence finish --task TASK_ID --renderer 'renderer/device from app log' --json`.
It attaches project tasks created during recording automatically and retains
Git provenance, capture paths/hashes/timing and process/log/report snapshots.
Renderer text is operator-observed; the project owns assertions. After helper
exit or interrupted collection use `evidence finalize ARTIFACT --json`.
See [evidence contracts and limits](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/cachyos-desktop.md#manual-testing-evidence).

Native captures carry `artifact_sha256` for PNG file bytes, separate from
`pixel_sha256` for visible pixels. Use `evidence verify /absolute/record.json
--json` to check retained capture references without live desktop access or
modifying the record. Missing/mismatched/unsafe files, old captures without file
hashes, empty capture inventories and truncated journals do not pass. This
verifies file integrity only; reopen images to verify their meaning. Keep needed
media and temporary JSON observations in durable private storage with the
project's run/phase/assistance record. See the
[playtest guide](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/game-testing.md)
for immutable observer packets, update retests, long-run recording segments,
cleanup inventories and capability boundaries.

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
focus/move/resize, window-specific capture, clipboard and
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

`basaltw desktop --native handoff` opens the human control window from the local
KDE session, even while the helper is stopped or agents are blocked. Keep
`--native` before `handoff`; the bare command addresses Debian XRDP. Opening the
window neither stops an agent's loop nor transfers ownership or changes permission.
Relinquish your own action loop separately; only the human's UI choice or an
explicitly authorized control request changes persistent permission. Spawn success
does not establish window visibility. See the
[handoff distinction](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/desktop-control.md#handoff-is-not-a-permission-change).

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
