# KDE Wayland native control recipes

Check [session, consent, and human control](native-control.md) before acting.

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
per-axis scale, origin and `input_argv`. Pass those literal arguments directly.
The input helper handles portal logical scaling. This calculation uses only the
supplied geometry and cannot refresh an expired observation.
For unknown viewer transforms, obtain a known full view or a bounded native crop.
See [mapping examples and playtest qualification](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/game-testing.md).

From `screenshot --compact`, open the `screenshot` entry in `artifacts` to inspect
the PNG. Pass the `capture_receipt` entry's path to `map-point
--capture /absolute/private/frame.json --view-geometry W H --x X --y Y`.
For viewer margins add `--view-origin LEFT TOP`; points include those offsets.
This mode checks frame identity, transform and helper freshness together,
returning `input_context_argv` and `input_argv` as literal argument arrays. It
makes a read-only status request. A changed or recentered target
requires fresh inspection even when the token remains valid.

For a known-size view, use `screenshot --compact --preview-width 1200`.
Open the `screenshot_preview` artifact and pass its exact coordinates to
`map-point --capture RECEIPT.json --preview-coordinates --x X --y Y`. The receipt
keeps the original and the same token. For a viewer that resizes the preview,
use its actual presentation dimensions.

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
[dated qualification](https://github.com/bluehexagons/basaltwater/blob/main/docs/testing/cachyos-desktop-evidence.md#native-keyboard-and-task-inventory-improvements-2026-11-02).

`status.input_capabilities` identifies helpers supporting physical input;
restart older helpers after updating. `status.input_state` reports counts of
pending helper-injected key presses and their named modifiers during a hold.
It omits key values. A failed release retains its pending count and stops the
helper. This is not global keyboard state: physical modifiers, lock keys and
other input providers remain unavailable. A zero count cannot establish that
the human's keyboard or another virtual keyboard has no stuck modifier.

For real touch contacts, explicit `start --touch` consent, relative movement,
discrete scrolling and side buttons, follow
[pointer and touch QA](https://github.com/bluehexagons/basaltwater/blob/main/docs/workstations/pointer-touch-qa.md).
Check advertised capabilities after refresh; a pointer grant does not grant touch.
Use full-monitor capture pixels for contacts and preserve the private gesture
plan with its recorder digest. XRDP and physical devices need separate qualification.

Key chords and pointer buttons 1–3 and 8–9 accept `--hold-ms 0–5000`
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
For a Quit action on a running development task, use `--observe-task TASK_ID`
in place of `--observe`. It preserves input delivery and returns a task exit
receipt without capturing the screen. Save the terminal game image before Quit.
For Steam or other externally launched games, pass the same-boot receipt from
`desktop evidence process` to `--observe-process /absolute/private/process.json`.
Repeat the flag for the wrapper if it should also exit. The receipt reports
absence or replacement of each identity after confirmed delivery, with a shared
`--process-timeout` (default 10 seconds). This check also captures no pixels.
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
three preview entries, and a `full_result` artifact. Read only needed fields from
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
