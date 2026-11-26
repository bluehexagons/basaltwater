# Desktop observations and slow decisions

Debian XFCE/XRDP, CachyOS KDE Plasma and Windows 11 share a preferred
capture-to-input contract. Use explicit observation IDs for new controllers;
platform-specific input backends and coordinate spaces remain visible.
See the [validation and development backlog](../plans/game-testing.md#desktop-observation-follow-up)
for native checks still required after this implementation.

## Capture and bind one action

A successful screenshot returns an opaque `observation_id`, `generation`,
`control_revision`, `geometry`, `image_geometry`, `image_origin`,
`coordinate_space`, `captured_at` and `capture_freshness`. Windows monitoring
captures during an active or uncertain keyboard lease instead return a null
`observation_id` and `observation_unavailable_reason` of
`keyboard-lease-active-or-uncertain`. Image age remains available, but these
images cannot authorize input. Capture again after confirmed lease cleanup;
cleanup completing during capture does not authorize that image. Copy the returned
values rather than reconstructing an ID or inferring coordinate transforms.
`captured_at` is PNG completion time; age starts at capture admission using
the helper's monotonic clock, so acquisition consumes part of the lifetime.

`capture_freshness` contains `age_seconds`, `remaining_seconds`,
`max_age_seconds` and `fresh`. It describes age at response time, not a live
countdown. No current capture has null age, zero remaining time and false
freshness. The helper validates age again at input admission.

Capture lifetime defaults to 60 seconds. `screenshot --max-age-seconds N`
accepts integers from 1 to 600. Select a longer lifetime explicitly for a
stable, untimed scene when a model needs more thinking time. It applies only
to that capture, without lengthening request execution deadlines, helper
lifetime, accessibility references or human override. A newer screenshot uses
its own selected lifetime, defaulting to 60 again.

On Linux, inspect the image and submit one bounded action:

```bash
basaltw desktop screenshot --max-age-seconds 180 --compact
basaltw desktop input --generation GENERATION --geometry WIDTH HEIGHT \
  --observation OBSERVATION_ID click --x X --y Y --observe
```

Add `--native` before the subcommand on CachyOS. Windows retains its explicit
window selectors:

```powershell
basaltw desktop screenshot --window HWND --identity IDENTITY --generation GEN --max-age-seconds 180
basaltw desktop input click --window HWND --identity IDENTITY --generation GEN --width W --height H --observation OBSERVATION_ID --x X --y Y --observe
```

Windows preserves completed input when a later capture fails. Holds capture
after confirmed release and cleanup. Use `--observe --output NEW.png` to
choose the path. The older `--observe NEW.png` form also works on Windows.
The settle delay accepts 0–5000 ms on all three platforms. The output must be
new, and the CLI checks its parent and write access before input.
`screenshot --compact` retains the existing small JSON geometry/origin receipt.
Save that JSON and use `map-point --capture frame.json --view-width 1200
--view-height 800 --x 600 --y 400` for a resized, full window image. Supply
`--view-origin-x` and `--view-origin-y` for viewer margins, or use
`--image-coordinates` alone for source-PNG pixels. Supply both `--to-x` and
`--to-y` to map a drag endpoint in the same coordinate system. Windows mapping
also accepts the nested post-input observation, checks the current receipt and
age, and returns `input_context_args` plus `input_args`. The command is
read-only and uses full-window captures.

`--observation` binds input to the latest successfully published screenshot.
An admitted mutation consumes or invalidates the token, including an uncertain
input attempt. Capture and inspect again before another action. A later
successful capture supersedes the old token; a failed capture does not renew
or replace it. Session and human-control changes invalidate tokens. `status`,
`windows`, inspection and coordinate calculations do not extend capture age.
Status may still report a recent image after its token is consumed; age alone
does not establish that a token remains usable.

Keep one desktop controller. Background advisors receive immutable image
packets and return conditional recommendations, without taking new captures
or issuing input. Tokens bind an action to an observation; they do not detect
every application change, prove focus or establish semantic target validity.

## Coordinate spaces and platform boundaries

| Backend | `coordinate_space` | Input geometry and origin |
| --- | --- | --- |
| Debian XFCE/XRDP | `desktop` | `geometry` is the full desktop; `image_origin` is the window offset or `[0, 0]`. A token for a window also checks its identity, placement and active state before injection. |
| CachyOS KDE Plasma | `monitor` | `geometry` is the selected monitor; `image_origin` is the crop offset or `[0, 0]`. The backend applies portal logical scaling. |
| Windows 11 | `window` or `monitor` | Window input uses the outer-window dimensions and window-relative pixels, with image origin `[0, 0]`; desktop placement is separate. A monitor capture cannot authorize window input. |

Token-bound pointer targets must remain inside the observed image, including
both endpoints of a native drag. Account for any image presentation scaling
before submitting coordinates. A window move does not necessarily change the
session generation. Compact Linux captures preserve window and origin metadata.

On CachyOS, save the inspected capture's JSON receipt alongside its PNG, then
map against that exact receipt:

```bash
basaltw desktop --native screenshot --compact > /absolute/private/frame.json
basaltw desktop --native map-point --capture /absolute/private/frame.json \
  --view-geometry 1200 800 --view-origin 20 30 --x 620 --y 430
```

`--capture` also accepts the complete JSON receipt from `input --observe`.
Inspect the PNG in its nested `observation`, then pass the full receipt to
`map-point`; `capture_source: observation` confirms that selection. The top-level
input token is consumed and is never a fallback. A missing, failed or stale nested
frame requires another capture and inspection, never replay of the completed input.
Omit `--output` on screenshots and `input --observe` to generate a unique private
PNG path. On Windows, read `result.output` or `result.observation.output`.
On Linux, read `output` or `observation.output`.

### Map points from a saved Linux capture preview

Use `screenshot --preview-width N` or `input --observe --preview-width N` to
retain a private resized PNG alongside the original. The client uses FFmpeg,
accepts widths from 1 through 2048, never enlarges the image and rounds the
proportional height down to a minimum of one pixel. The returned `preview`
contains the path, exact pixel dimensions, coordinate transform and both file
hashes. The receipt keeps the original PNG, its geometry/origin and the same
observation token.
When using a preview's actual PNG pixels, read its geometry from that receipt.

```bash
basaltw desktop --native screenshot --compact --preview-width 1200 > /absolute/private/frame.json
basaltw desktop --native map-point --capture /absolute/private/frame.json \
  --preview-coordinates --x X --y Y
```

Open `preview.output` for that view. Scaling maps preview coordinates to original
image coordinates by integer division, then adds the original image origin.
For a viewer that further resizes the preview, use the displayed content's actual
`--view-geometry` and any margins. `preview_error` means the derivative failed
and the command exits nonzero. The original is still available for inspection
and mapping while its token remains valid. Keep needed previews in durable private
storage. Preview rendering and mapping do not renew the observation lifetime.

### Observe a supervised task's exit after input

Capture the terminal game state first. For an input expected to quit a game
with a verified running `desktop develop` task, use `--observe-task TASK_ID`.
The command checks the task before sending input and returns the independent
`task_observation` after confirmed delivery. It takes no screenshot.

```bash
basaltw desktop --native input --generation GENERATION --geometry WIDTH HEIGHT \
  --observation OBSERVATION_ID click --x X --y Y --observe-task TASK_ID --task-timeout 10
```

The deadline is 1–60 seconds (default 10). Exit zero requires a verified
successful task exit. A failed, stopped or unverified task or an expired wait
returns nonzero and preserves the completed input. Use `develop status TASK_ID`
to reconcile the same task after uncertainty. Task exit records do not establish
that the intended button received the input. For a real follow-up image, use
`--observe` separately.

### Observe external Linux processes after Quit

For a game launched outside `desktop develop`, take a terminal game capture before
Quit and record the game and wrapper identities using
[`desktop evidence process`](native-qa-evidence.md#retain-process-identities).
Pass the saved JSON receipt for each process to the input command:

```bash
basaltw desktop --native input --generation GENERATION --geometry WIDTH HEIGHT \
  --observation OBSERVATION_ID click --x X --y Y \
  --observe-process /absolute/private/game/process.json \
  --observe-process /absolute/private/wrapper/process.json --process-timeout 10
```

The CLI checks that each of up to 16 distinct processes still matches and that
the receipts came from the current Linux boot. After confirmed delivery it polls
all original identities under one deadline of 1–60 seconds (default 10) without
taking a screenshot. Exit zero requires every original identity to have exited,
be absent, or be replaced. `identity-exited` identifies a process that Linux
still lists because its parent has yet to reap it. `process_observation`
retains the last timestamped result for each PID, including completed checks
if the deadline expires. A timeout returns nonzero and preserves the completed
input receipt. `normal_exit` remains `unknown` because these processes are
externally managed. Recheck their identities separately if needed.

### Capture a selected Debian window after input

On Debian, keep unrelated desktop content out of the follow-up image by selecting
the application's window explicitly:

```bash
basaltw desktop screenshot --window WINDOW --compact
basaltw desktop input --generation GENERATION --geometry WIDTH HEIGHT \
  --observation OBSERVATION_ID click --x X --y Y --observe --observe-window WINDOW
```

Use the window ID from `desktop windows` and the input coordinates from the
inspected capture, including its `image_origin`. `--observe-window` selects only
the subsequent capture; it does not focus the window or change the input target.
The follow-up retains full-desktop `geometry`, window `image_origin` and a new
observation ID. A closed, hidden or otherwise uncapturable window fails that
observation, preserving completed input without a full-desktop fallback. Inspect
dialogs separately when they are outside the selected window. This option requires
`--observe`; native monitor capture rejects it before sending input.

### Map image points to input coordinates

`--view-geometry` is the presented image content size; `--view-origin` is its
top-left offset inside the viewer (default zero). Points include that offset.
Both drag endpoints use the same transform. The read-only status comparison
rejects changed token, generation, control revision, geometry, crop or expiry.
Copy `input_context_args` before the input kind and `input_args` after it.
Input revalidates the single-use token; mapping does not reserve or renew it.
Legacy `map-point --geometry ... --crop ...` remains an offline calculation,
without freshness validation. Neither route recognizes moving/fanned targets.

### Small consequential controls

Prefer a bounded native crop containing the whole control and enough surrounding
context to distinguish its neighbors. On CachyOS, for example:

```bash
basaltw desktop --native screenshot --crop 1000 600 400 300 --compact > /absolute/private/control.json
basaltw desktop --native map-point --capture /absolute/private/control.json \
  --view-geometry 400 300 --x 200 --y 150
```

These fictional dimensions assume the inspected crop was actually presented at
400 by 300 pixels. Confirm that fact before mapping. The crop's native center
maps to monitor point `(1200, 750)`; do not add the crop origin again. Copy the
returned context and point arguments into the input command described above.
Both drag endpoints must fit inside this same crop.

The PNG dimensions and an image API's original-detail option do not establish
the outer viewer's presentation size. If that transform is unknown, either obtain
a view whose dimensions can be established or explicitly choose source-PNG pixels
as described below. A crop-width limit alone does not establish a view transform.
Record the chosen basis, receipt origin/image dimensions, selected point and mapped
point privately; presented-view mapping also needs content dimensions and margins.

### Explicit source-PNG points and offline previews

`--image-coordinates` is an alternative to `--view-geometry`. Points are integer
pixels in the exact capture PNG, independent of its display size. For a 400 by
300 native crop, its arithmetic center is `(200, 150)`; the example receipt above
adds origin `(1000, 600)` once to produce monitor point `(1200, 750)`:

```bash
basaltw desktop --native map-point --capture /absolute/private/control.json \
  --image-coordinates --x 200 --y 150
```

This selects a coordinate basis, not a target. Do not relabel positions estimated
from an unknown-size viewer as source pixels. Inspect a marked derivative first
when the source point is uncertain:

```bash
basaltw desktop --native evidence point-preview /absolute/private/control.png \
  --rectangle 0 0 400 300 --point 200 150 --caption 'Proposed control center' \
  --output /absolute/private/new-point-preview --json
```

Open `point-preview.png` and the unchanged `crop.png`; verify the red cross lies
inside the intended control. The preview's source hash must match the exact PNG
in the saved capture receipt. Rectangles and points refer to that PNG, so an
already cropped capture uses local coordinates. A full-frame PNG uses full-frame
coordinates even if the preview shows only a small region. Do not add the preview
rectangle origin to a point already expressed in source-PNG pixels.

Before accepting a point, describe the visible label or object under the cross,
then compare it with the intended action. A preview caption records caller intent;
it is not recognition or confirmation. For example, `intended: Continue;
observed: Help; decision: reject` requires a corrected target, even when the point
is in bounds and the token is fresh. A newer capture with the same misplaced point
does not correct that mismatch. Keep this acknowledgment short and retain it with
the action receipt; the CLI does not validate its meaning.

For an unlabeled or drag-dependent destination, record what is actually visible
and the evidence supporting the gesture. An empty region alone cannot confirm a
drop target; absence of a target before dragging does not prove it is invalid.
Use the task's bounded qualification/failure policy and verify the returned effect.

Image mode rejects view geometry and viewer margins. Both drag endpoints use
the same source basis and must remain inside the capture. Receipt-bound mapping
still checks current status, crop, token, revision and age; a preview or mapping
never reserves or renews freshness. If preparation expires the capture, inspect
a fresh one and re-establish its target. Offline `--geometry ... --crop ...`
image-mode arithmetic has no live checks. Neither mode recognizes controls or
proves focus, intent or application effect. See the
[preview artifact contract](native-qa-evidence.md#offline-point-previews).

For a synthetic 2400 by 1600 full frame displayed at 1200 by 800, presented
point `(600, 400)` maps to `(1200, 800)`. Claiming the view is 2400 by 1600
instead produces `(600, 400)`: valid monitor coordinates that can hit a different
control. Bounds and token checks cannot detect this caller error. After a visible
miss, inspect a fresh capture and document the changed transform or target
explanation before another attempt. Completed delivery alone is no evidence
that the intended control was activated.

Use the [next-cycle qualification checklist](../plans/game-testing-handoffs/next-cycle.md)
to exercise reduced views and crops in the disposable probe before gameplay.

### Native input failure receipts

Updated CachyOS and Debian helpers and clients preserve `reason`, `dispatch_stage`,
`input_delivery`, `input_id` and related `observation_id` through CLI failures
and, on CachyOS, native recorder events:

Capture guard reasons include `capture-unavailable`, `capture-expired`,
`capture-geometry-changed` and `observation-expired`. Legacy capture guard
failures retain the observed `capture_freshness` in both CLI and journal.

| Stage | Delivery | Meaning |
| --- | --- | --- |
| `before-input` | `not-sent` | Validation/guard refusal before the first portal notification or xdotool invocation; no dispatch. |
| `input-dispatch` | `unknown` | At least one portal notification or xdotool invocation was attempted; partial delivery/release may have occurred. |
| `transport` | `unknown` | No authoritative worker response; delivery cannot be inferred. |
| `input-completed` | `completed` | All intended input and release calls returned. Game effect remains unverified. |
| `post-input-observation` | `completed` | Input retained, subsequent observation failed. Capture again, never replay automatically. |
| `post-input-task-observation` | `completed` | Input retained, task exit check failed or timed out. Recheck the same task. |
| `post-input-process-observation` | `completed` | Input retained, one or more original process identities could not be resolved by the deadline. |

On Debian, local failures before attempting to send the socket request also
prove `not-sent`. Once sending is attempted, a transport failure remains
`unknown`, even if only part of the request may have reached the worker.
Helper-owned input IDs and receive/first-dispatch UTC and monotonic timestamps
survive structured failures. Completion adds its own timestamps only after all
input calls return. These times describe helper operations, not game effect or
model inference; compare monotonic clocks only on the same boot.

Linux captures expose `last_input`, a content-free summary of the latest
worker-received input outcome, including a refusal or interrupted dispatch.
The journal's `related_input_id` links to that attempt rather than an earlier
success. Check `input_delivery` and `dispatch_stage`; completion timestamps
exist only for completed input. A client-side refusal or transport failure
that never reached the worker cannot update this summary. Older helpers
reported only the last successful input, so inspect their original journals
before attributing a post-failure image.

Linux clients also retain these receipts for action sequences and paced typing.
A sequence reports earlier completed results, a zero-based `failed_action_index`
and the full `failed_action` receipt; failure stops subsequent steps. For legacy
character-by-character typing, `submitted_characters` counts acknowledged complete
character requests. If a later request is refused, overall `input_delivery` is
`partial`, and `failed_request.input_delivery` is `not-sent`. A lost response
remains `unknown`, even with an acknowledged prefix. Failure before the first
request is `not-sent`. `last_completed_input` keeps the prior character's receipt
separate from the failed request; its timestamp never certifies the whole text.
Server-paced errors preserve a worker's count when available, otherwise null.
Never replay a partial or unknown text operation automatically. Legacy string
errors cannot prove refusal just because their wording resembles a guard error.

The recorder retains refused events separately from uncertain ones. Verification
counts `image_less` capture events separately from `missing` saved paths and
includes uncertain events in health. PNG hashes alone cannot certify journal
health. Older errors/journals have no proven dispatch stage; they remain
uncertain and are never rewritten. Windows input receipts include a stable ID,
generation, observation ID when supplied, dispatch stage and delivery. Windows
captures include the exact PNG's SHA-256 and a content-free `last_input` for
the latest session input admitted for dispatch. A refusal before admission keeps
the preceding observation and input summary. Cold and warm envelope refusals
report `not-sent`. Once the worker body is entered, a failure may report
`unknown` even when a native guard refused. Use the saved receipts for the
[offline decision audit](learning-records.md#auditing-one-decision).
Restart a
Debian desktop session normally after installing updated worker code; updating
the client alone cannot add stages to an already running old worker.

Offline verification includes `uncertain_by_action` and up to ten
`uncertain_event_details` with zero-based journal indices, action, reason,
dispatch stage and delivery when recorded. Its truncation flag indicates more
uncertain entries. These diagnostics do not infer delivery from action names or
error wording, change journal classifications, or make an unhealthy record healthy.
Inspect the original private journal at those indices for additional context.

Existing input without `--observation` retains its platform-specific checks:
Debian geometry/revision checks, CachyOS latest-capture age checks, and Windows
fresh window-inventory checks. Do not describe these paths as exact observation
binding. Updated Linux status advertises `observation-input`; older helpers
need a safe restart before token-bound input. A non-default lifetime must be
confirmed in the capture response, rather than assumed from a CLI flag.

Linux `input --observe` retains the input completion receipt, settles, and
captures again. It checks the output path before input, refusing existing files,
dangling symlinks and invalid/unwritable parent directories. The precheck does
not reserve a path; a later filesystem change can still cause post-input capture
failure, which retains completed input and never replays it. The response
details the capture failure in `observation_failure`.
Without `--observe-window`, Debian captures the desktop and CachyOS captures the
selected monitor, regardless of the scope of the preceding screenshot.
Completion receipts retain the geometry admitted for input;
take a new capture to observe current dimensions. Debian records completion
without a final geometry query that could obscure successfully submitted input.
CachyOS skips the stream's initial sample; Debian performs a
new scrot capture without claiming compositor frame timing. Linux rejects a
follow-up when the capture explicitly reports a different input ID. Windows
rechecks the latest input ID and control state after the settle delay. Held
input carries capture deadlines through watchdog startup to the first down.
PrintWindow captures reflect the application's Win32 capture support. A capture
failure does not replay input.
Debian observation-bound ASCII text uses one unpaced request; `--delay-ms`
requires server-paced text support for a single-use token. Non-ASCII text
defaults to separate character requests with 10 ms pacing because rapid X11
keymap changes can lose characters in native clients. This paced route is
available without a token, with its existing generation/control/geometry
checks. Token-bound non-ASCII text is refused before dispatch on Debian until
server-paced text is implemented. Use accessible `element set-text` and verify
its result when a control supports it; otherwise inspect a fresh capture and
use paced text without `--observation`, then verify the delivered text.

## Planner and operator workflow

For one inspected input followed by two delayed captures, use the
[bounded observation recipe](short-observation-sequences.md). It retains the
completed prefix and actual timing when a later capture fails; never replay
input to repair evidence. Linux sequence JSON and native Windows keyboard
sequences are separate command contracts.

Keep strategic reasoning outside the short action loop. A planner can use
high reasoning and a compact checkpoint: phase/turn, resources, board,
objective, previous verified effect and unresolved uncertainty. It returns
one conditional next action and its expected result. A smaller or lower-effort
operator gets that checkpoint, the relevant plan step and a fresh image.
The operator checks conditions, targets, acts and verifies before continuing.
Escalate ambiguity; refresh context at phase boundaries instead of starting a
worker for every click. The harness owns worker/model selection, not the
desktop helper.

Tag advice with packet and phase IDs and conditions that invalidate it, such
as purchase, reroll, selection change or a transition. Optional advice should
not block an already-established action. Verify image access and visual input
for each worker; a file path alone does not prove access.

Compare workflows on matched saved positions and the same completion unit.
Measure capture-to-input time, verified action success, stale-token refusals,
coordination overhead, strategic outcomes and total time/cost. Lower reasoning
is an experiment, not a latency or correctness guarantee. Keep model settings,
raw evidence and run-specific checkpoints in private project state.
