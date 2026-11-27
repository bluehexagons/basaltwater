# Observe and act during a game test

[Topic index](game-testing.md)

[Qualify the controller on fresh state](game-testing-preparation.md#qualify-the-controller-on-fresh-state)
before relying on the capture and input results here.

## Map a presented image

`map-point` performs a read-only calculation with explicit metadata. It needs
no running helper, sends no input and does not refresh a capture's age. For a
fictional 2400 × 1600 monitor image presented at 1920 × 1280:

```fish
basaltw desktop --native map-point --geometry 2400 1600 \
  --view-geometry 1920 1280 --x 800 --y 600 --json
```

The result reports per-axis scale `[1.25, 1.25]`, the image origin and dimensions,
and `input_args: "--x 1000 --y 750"`. Review that monitor position against the
intended target. The same values are available as `input_argv`, a JSON array
with one string per argument. Use the mapped coordinates directly. The backend
applies portal logical scaling later.

For live input, inspect a fresh screenshot or nested post-input observation
and map the chosen point with `map-point --capture`. That mapping also returns
`input_context_argv` for the generation, full-monitor geometry and observation
token. Pass the arrays directly to the input process, with the context before
the input kind and the coordinates after it. For example, given the parsed
mapping result for a left-click target:

```python
subprocess.run([
    "basaltw", "desktop", "--native", "input", *mapped["input_context_argv"],
    "click", "--button", "1", *mapped["input_argv"],
], check=True)
```

`input` emits JSON by default. The existing `input_args` and `input_context_args`
fields are strings for use when reading or copying a mapping by hand.

If a focused text field needs a value starting with `--`, pass that value in
the same argument as `--text=`. For example, use `text --text='--windowed'`
in a shell or `['text', '--text=--windowed']` in a process argument list.
With detached `--text '--windowed'`, the parser rejects the request before
sending input. Inspect the field after delivery to verify the entered value.

For a screenshot made with `--crop 100 200 800 600`, presented at 400 × 300,
map a drag within that image as follows:

```fish
basaltw desktop --native map-point --geometry 2400 1600 \
  --crop 100 200 800 600 --view-geometry 400 300 \
  --x 100 --y 100 --to-x 300 --to-y 200 --json
```

This returns `--x 300 --y 400 --to-x 700 --to-y 600`. Both endpoints must be
inside the presented image; the crop must be inside the monitor. Conversion
uses `origin + floor(view_point * image_size / view_size)` independently per
axis. Bounds and types are validated, including paired drag endpoints.

Use coordinates relative to the entire displayed PNG, excluding viewer borders
and letterboxing. The view dimensions describe that PNG's presented content,
not the chat pane or the viewer window. If dimensions are unknown or the viewer
has zoomed/clipped the image, obtain a known full view or take a bounded native
crop and inspect it at a known size. The aid cannot infer viewer transforms,
verify scene/focus, select a target or validate freshness. Image presentation
resizing and desktop logical scaling are separate transforms.

Retain the map result with the semantic target, actual presented dimensions and
source capture identity, then link the input receipt ID. Standalone native input
receipts may omit source/destination coordinates; the evidence recorder's input
event retains monitor points/endpoints and the input ID. Consult that existing
event when diagnosing a miss. Neither completed delivery nor equal post-action
pixels identify the intended target or prove a sale/turn effect.

## Observe, act, verify during play

Before every shop action or other scene-dependent interaction, confirm current
scene cues, resources, target identity and expected effect from a fresh image.
Use bounded actions followed by observation; do not queue speculative gestures
across scene transitions. If evidence contradicts a worker's scene description,
correct the note and inspect current state before continuing.

`input --observe --settle-ms N` accepts **0–5000 ms** (default 120). For longer
combat or animation waits, use later `screenshot --next-frame --compact`
observations at the brief's cadence. Do not request an unsupported longer settle
or send meaningless input to spend time. A transitional first frame calls for
another observation at the declared cadence; repeated equal pixels alone do
not establish a frozen capture. Apply the stuck-state threshold before recovery.

Prepare and exercise the capture-and-open path before a time-sensitive window.
Save the compact screenshot receipt, read its returned `output` path, and open
that local PNG with the controller's image viewer. This needs no browser base64
helper such as `btoa`. After End Turn, prioritize the next observation over
prose or tool construction. A failed capture script establishes no observation;
record the actual gap and any missed combat rather than reconstructing it from
the next shop or cumulative result screen.

Input rejects expired captures (60 seconds by default), wrong geometry and
changed generations. Recapture and re-plan after rejection. When input completed
but its follow-up capture failed, retain the completion result and capture again;
never replay an uncertain purchase or placement blindly.

A no-repeat rule for turn submission applies during Ready/combat/transitions or
while delivery/effect is unresolved. It is not a lifetime limit on that button.
When fresh evidence confirms a click missed and the same actionable shop/turn
remains, correct the target mapping, make one bounded attempt and verify its
effect. Later turns have their own commitment. Retain the missed action and
recovery; do not classify ordinary correction as another match Start.

### Keep decisions inside the capture window

Native `status --json` and full/compact screenshots include `capture_freshness`:
`age_seconds`, `remaining_seconds`, `max_age_seconds` (default 60), and `fresh`.
These describe the helper's most recent successful capture, timed from
capture admission with its monotonic clock, including acquisition time.
`captured_at` separately describes PNG completion. No current capture gives null age, zero
remaining time and false freshness. Stopped/expired helpers and observations
invalidated by a control change cannot advertise a fresh capture.

Values are snapshots at response time, not a countdown in a saved packet.
`status` and `map-point` do not renew the capture. After long reasoning, an
observer response or a handoff, check status; when the window has expired or
little time remains, recapture, inspect and confirm the intended target before
input. A stable shop and unchanged generation do not extend the window. A
failed recapture also leaves the old window unchanged. Older helpers may omit
the field; update/restart at a safe checkpoint rather than assuming freshness.

Do longer strategic reasoning first, then take a fresh observation and check
the plan's conditions immediately before acting. Freshness is an input safety
check, not proof of focus, scene, target identity or animation completion; a
purchase or transition can invalidate a target within the 60-second window.
The backend still checks age and geometry at delivery and never automatically
refreshes a capture or replays a rejected action.

For stable, untimed turns, explicitly select `screenshot --max-age-seconds 180`
(allowed range 1–600; default 60). This changes only that capture's age limit,
not scene validity or request deadlines. Prefer `input --observation ID` from
the inspected capture for one action; new captures and mutations invalidate
that token. See the cross-platform [observation contract](desktop-observations.md)
for coordinate spaces, existing non-token routes and planner/operator guidance.

At a handoff, stop issuing actions and record the current match ID, objective,
last verified scene/resources/board, learned strategy, pending/uncertain inputs,
helper generation, evidence path and budget remaining. The receiver takes a
fresh capture and qualifies before acting. Historical status is context,
not live desktop authority. Use the [shared handoff conventions](../agents/handoffs.md).
Bound worker phases by a progress checkpoint and time allowance as well as
turn count. If reasoning or tool overhead consumes that allowance, hand back
the verified partial state and remaining budget under the same match ID.

## Controller capability boundaries

The desktop CLI provides keyboard, text, pointer and drag input. Its optional
[Linux virtual controller](gamepad-qa.md), `desktop [--native] gamepad`, supplies
bounded uinput events on CachyOS and Debian while honoring shared human control. Qualify
its device, interruption paths and target game before use. Package/device
presence, keyboard remapping and a physical pad are separate conditions.
Record Steam Input or other remapping separately from game-native input.
The [game-testing capability plan](../plans/game-testing.md) defines the remaining
adapter, failure-path and game-recognition acceptance work. Do not use an
independent input script to bypass human override or disabled control.

## Coordinate aid verification scope

The coordinate calculation has offline contract coverage for resized full
images, resized crops, drag endpoints, rounding, bounds and invalid inputs.
CLI tests verify that mapping makes no backend status/input requests or
artifact allocations. Existing native freshness and control guards remain
covered by the desktop suite.

A native CachyOS KDE Wayland check on 2026-11-02 used a disposable test canvas:
a target selected from a resized full-monitor image received the mapped click,
and a cropped-image drag reached the intended destination. The canvas event
log and post-input image both confirmed one click and one completed drag.
The fixture exited successfully afterward. Raw evidence remains private.
This checks the aid with native input; fresh-worker game scene recognition
and purchase/placement qualification still require the next game's run brief.
