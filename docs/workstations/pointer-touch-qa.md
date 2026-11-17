# Pointer and multitouch testing

The native KDE portal route supports absolute movement/clicks, bounded drag,
relative pointer motion, discrete scrolling, side buttons and real touch
contacts. Touch requires a **new explicit touchscreen grant**. It does not use
the virtual gamepad's uinput device or need another privileged driver.
Implementation/offline checks do not establish application delivery. After an
owner-selected Basaltwater refresh, qualify the running helper and a contained
input inspector before game testing. Keep desktop input under one owner.

## Platform and permission

| Route | Available testing path | Remaining qualification |
| --- | --- | --- |
| CachyOS KDE Wayland | RemoteDesktop portal; touch only when advertised and granted | Live new-helper, compositor and consuming-app checks |
| Debian XRDP/Xfce | Existing absolute pointer and click/wheel input | No true multitouch/relative/extra-button route in this supervisor |
| Other Linux portal sessions | Protocol support may differ | Not qualified by CachyOS or Debian tests |
| Windows, physical touchscreen/pen/touchpad, browser mobile emulation | Separate routes | Not implemented or qualified by these commands |

Do not create a host-seat uinput touchscreen to claim input into Debian's
virtual XRDP display. A kernel device and the session receiving events are
different boundaries. The CLI rejects extended native-only actions on XRDP.

At a safe checkpoint, finish owned actions before replacing an older helper.
After the owner refreshes Basaltwater, start the native helper with:

```sh
basaltw desktop --native start --touch --json
basaltw desktop --native status --json
```

Only the desktop owner can approve KDE's selected monitor, keyboard, pointer
and touchscreen request. `--touch` checks portal availability before requesting
consent, and checks actual granted devices before opening capture. A running
session without `touchscreen` is refused rather than silently restarted or
expanded. Stop/start at an agreed checkpoint; do not interrupt shared work.
The optional existing `--remember` mechanism can request persistence too; an
older saved grant may require fresh approval. Disabled control and human
override continue to block input. This guide grants no permission to change them.

Inspect `status.input_capabilities`: `relative-pointer`, `discrete-scroll`,
`extra-pointer-buttons`, and, only with touch consent, `touchscreen`.
`input_state.touch_contacts` counts helper-owned contacts pending release. It
cannot establish the state of a physical device or another input provider.

## Inspect and plan coordinates

Capture, save the JSON receipt privately and open the returned PNG before each
action. Copy its generation, full-monitor geometry and single-use observation:

```sh
basaltw desktop --native screenshot --compact --json > /private/frame.json
```

Use [capture mapping](game-testing.md) for scaled views/crops. Touch coordinates
are **full-monitor capture pixels**, just like absolute pointer coordinates.
The helper converts them to logical coordinates of the consent-selected stream.
Every contact in every frame must stay inside the inspected image/crop, even
when the full monitor is larger. Newer captures, mutation or control changes
invalidate the observation; select a fresh one with enough age remaining for
the gesture. Mapping and status do not renew freshness or verify focus.

## Complete contact plans

Save an array of complete active-contact snapshots in a private JSON file.
For example, this expands two contacts horizontally then lifts both. Replace
these example coordinates with points inspected in the current input fixture:

```json
[
  {"at_ms": 0, "contacts": [{"id": 0, "x": 400, "y": 300}, {"id": 1, "x": 600, "y": 300}]},
  {"at_ms": 250, "contacts": [{"id": 0, "x": 350, "y": 300}, {"id": 1, "x": 650, "y": 300}]},
  {"at_ms": 500, "contacts": []}
]
```

```sh
basaltw desktop --native input touch --generation GEN --geometry WIDTH HEIGHT \
  --observation ID --frames /private/pinch.json --observe --json
```

Limits: 2–128 frames, at most ten simultaneous IDs (0–9), integer pixel
coordinates, first time zero, strictly increasing integer times through 5000 ms,
final snapshot empty and at least one contact. Each frame has only `at_ms` and
`contacts`; each contact has only `id`, `x`, `y`. The input file and canonical
plan are bounded to 49152 bytes. Omitted IDs lift; moved IDs send motion; IDs
added after lifting start a new contact lifetime. All frames are validated
before dispatch. A late invalid frame sends no input.

Times are requested offsets from gesture start. Portal calls are sequential;
contacts within a snapshot are not physically simultaneous and frame timings
can overrun. Receipts include actual frame-completion monotonic timestamps,
`touch_frames_completed` and `touch_contacts_released`. Do not use requested
times as measured delivery, frame rate or input-to-photon latency.

Held gestures check control, capture lifetime, generation/geometry, request
deadline and client connection. Interruption attempts to lift **every attempted
contact**, including a down whose delivery timed out. Each touch bus call is
bounded to 250 ms; failed release stops the helper and retains a false release
receipt. Portal/session closure is a further compositor boundary, not proof of
consumer state. Inspect and reconcile `unknown` delivery; never automatically
replay. No contact can intentionally remain held across requests.

## Pointer extensions

```sh
basaltw desktop --native input relative --generation GEN --geometry WIDTH HEIGHT \
  --observation ID --dx 40 --dy -20 --observe --json
basaltw desktop --native input scroll --generation GEN --geometry WIDTH HEIGHT \
  --observation ID --dx 0 --dy -3 --observe --json
basaltw desktop --native input click --generation GEN --geometry WIDTH HEIGHT \
  --observation ID --x X --y Y --button 8 --hold-ms 120 --observe --json
```

Relative deltas are integer **logical pointer units**, each -4096..4096, with
at least one nonzero component. Acceleration and application capture affect the
result; they are not capture-pixel destinations. Motion can leave the selected
monitor. Use a contained fixture, inspect the resulting position and recapture
before clicking. This route does not guarantee raw mouse or pointer-lock support.

Scroll deltas are discrete steps, each -100..100, at least one nonzero. Positive
Y is down and positive X right. Diagonal requests send vertical then horizontal
events. Smooth scrolling, inertia and touchpad gestures are separate checks.
Existing click buttons 4–7 retain their single wheel-step behavior. Buttons
8/9 mean side/extra, with the same bounded hold/release behavior as 1–3;
drag still uses 1–3. All three new gesture kinds require a single-use screenshot.

## Evidence and consuming acceptance

The native recorder journals these portal actions. It stores pointer deltas,
touch plan canonical SHA-256/bytes/frame count, actual frame times and release
status. Preserve the original private plan alongside the receipt: the digest
uses sorted JSON keys, compact separators, UTF-8 and no NaN, so whitespace in
the source file does not affect it. Invalid plans are labeled invalid without
copying arbitrary fields. The recorder does not embed full contact arrays.
Gamepad uinput receipts still have their separate inventory.

Use the game-testing project's input probe/procedure after refresh. Observe
one finger down/move/up, independent two-finger lifetimes, expansion/contraction,
lift/re-add, ten contacts and no contacts after release. Check mouse movement,
button 1–3 and 8/9 press/release, vertical/horizontal wheel directions and
relative motion. Distinguish touch events from emulated mouse events in the
consumer log; a click alone cannot qualify touch or multitouch.

Then test the target app's actual menus and gestures on its chosen backend.
Record native Wayland versus XWayland, geometry/scaling, intended and received
events, visible effects, interruptions and cleanup. Test physical hardware,
touchpad gestures, pressure/tilt, Steam, browser APIs and other OSes separately.
No new live touch/pointer qualification is asserted by this implementation.

Protocol reference: [RemoteDesktop portal methods and device grants](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html).
