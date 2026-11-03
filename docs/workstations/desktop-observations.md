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
basaltw desktop screenshot --window HWND --identity IDENTITY --generation GEN --output 'F:\reports\turn.png' --max-age-seconds 180
basaltw desktop input click --window HWND --identity IDENTITY --generation GEN --width W --height H --observation OBSERVATION_ID --x X --y Y
```

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

`--view-geometry` is the presented image content size; `--view-origin` is its
top-left offset inside the viewer (default zero). Points include that offset.
Both drag endpoints use the same transform. The read-only status comparison
rejects changed token, generation, control revision, geometry, crop or expiry.
Copy `input_context_args` before the input kind and `input_args` after it.
Input revalidates the single-use token; mapping does not reserve or renew it.
Legacy `map-point --geometry ... --crop ...` remains an offline calculation,
without freshness validation. Neither route recognizes moving/fanned targets.

### Native input failure receipts

Updated native helpers and clients preserve `reason`, `dispatch_stage`,
`input_delivery`, `input_id` and related `observation_id` through CLI failures
and recorder events:

Capture guard reasons include `capture-unavailable`, `capture-expired`,
`capture-geometry-changed` and `observation-expired`. Legacy capture guard
failures retain the observed `capture_freshness` in both CLI and journal.

| Stage | Delivery | Meaning |
| --- | --- | --- |
| `before-input` | `not-sent` | Validation/guard refusal before the first portal notification; no dispatch. |
| `input-dispatch` | `unknown` | At least one portal notification was attempted; partial delivery/release may have occurred. |
| `transport` | `unknown` | No authoritative worker response; delivery cannot be inferred. |
| `input-completed` | `completed` | All intended input and release calls returned. Game effect remains unverified. |
| `post-input-observation` | `completed` | Input retained, subsequent observation failed. Capture again, never replay automatically. |

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
uncertain and are never rewritten. Debian/Windows worker stage parity remains
separate work; a new client cannot infer it from old strings.

Existing input without `--observation` retains its platform-specific checks:
Debian geometry/revision checks, CachyOS latest-capture age checks, and Windows
fresh window-inventory checks. Do not describe these paths as exact observation
binding. Updated Linux status advertises `observation-input`; older helpers
need a safe restart before token-bound input. A non-default lifetime must be
confirmed in the capture response, rather than assumed from a CLI flag.

Linux `input --observe` retains the input completion receipt, settles, and
captures again. Completion receipts retain the geometry admitted for input;
take a new capture to observe current dimensions. Debian records completion
without a final geometry query that could obscure successfully submitted input.
CachyOS skips the stream's initial sample; Debian performs a
new scrot capture without claiming compositor frame timing. A capture failure
does not replay input. Windows post-input observation remains follow-up work;
its observation-bound route currently supports discrete input, with held-key
watchdog deadline plumbing still pending.
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
