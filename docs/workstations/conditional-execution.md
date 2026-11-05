# Bounded conditional execution and event waits

BW-GL05A provides source and mocked acceptance for CachyOS, plus
[bounded disposable-app qualification](../testing/conditional-cachyos.md) and a
[game-menu pilot](../testing/conditional-cachyos.md#owner-authorized-steam-game-pilot).
Broader game-profile acceptance and additional native boundaries remain pending.
It uses existing enabled control and
observation-bound input; it never starts, refreshes or grants a helper session.
Other platforms need an adapter for their actual authority/receipt contract.

## Commands and API

```text
basaltw desktop evidence conditional validate PLAN --json
basaltw desktop --native evidence conditional execute PLAN --profile PROFILE --template ID PNG [--template ID PNG ...] [--output NEW_PRIVATE_DIRECTORY] --json
basaltw desktop --native evidence conditional wait PLAN --profile PROFILE --template ID PNG [--template ID PNG ...] [--output NEW_PRIVATE_DIRECTORY] --json
```

`validate` is offline and confers no input authority. `execute` and `wait` operate
the real desktop when explicitly invoked with `--native`. Do not use the
[synthetic plan](../examples/learning-records/conditional.json) on a real app.
Profiles/templates use the [perception contract](local-perception.md).
Missing, duplicate or unpinned templates fail before desktop access. Output
defaults to a new private temporary directory; an explicit output must be new,
outside Git, beneath a private owned parent. Existing output is never resumed.

Python callers use `desktop.conditional.execute(plan, controller, output=...)`
or `wait(...)`. A trusted controller implements `status(deadline)`,
`observe(new_png_path, deadline, max_bytes)` and `dispatch(payload, deadline)`.
`observe` returns canonical `packet`, `frame_id`, exact `frame` bytes, helper
`capture` metadata and `recognition`. The executor verifies their identities,
pins and geometry; the adapter owns authentic capture/recognition. Serialized
records cannot substitute for a live adapter. `NativeController` connects the
existing CachyOS backend and fixed-region recognizer without a helper change.
It rejects frame budgets smaller than a conservative worst-case profile PNG.
Tests inject `now`, `cancelled` and (for waits) `sleep`; defaults use the local
monotonic clock. Cancellation is checked between bounded backend hops. Ctrl+C
returns retained uncertainty; there is no guarantee of interrupting an in-flight
input before delivery, and no automatic replay.

## Version-one plans

Objects reject extra keys, unsupported versions, booleans in integer fields and
nonfinite numbers. Maximum serialized plan size is 64 KiB. Common exact keys:

| Key | Meaning / bound |
| --- | --- |
| `schema_version` | Integer `1` |
| `kind` | `conditional-plan` or `event-wait` |
| `id`, `run_id`, `match_id`, `phase` | Nonempty bounded text matching the current packet context |
| `scene_field` | Observed field used for scene admission |
| `allowed_scenes` | 1–16 distinct labels |
| `terminal_scenes` | 0–16 distinct labels, disjoint from allowed labels; return to owner |
| `limits` | Exact `seconds`, `inputs`, `storage_bytes`, `frame_bytes` integers |

Text uses the [record contract](learning-records.md) limit of 128 characters.
Limits are 1–20 seconds for execution (1–300 for waits), 0–20 inputs,
64 KiB–128 MiB storage and 1 byte–16 MiB per frame. At least one frame plus
32 KiB must fit the declared storage. Actual admission reserves 256 KiB for
each metadata file and the final receipt; execution reserves post-input frame
and stage receipt space **before sending input**. Small declarations can be
valid plans yet stop for storage before capture/input. Size for both the retained
ring and its next replacement, including metadata; maximums are caps, not targets.

A `conditional-plan` additionally has `steps`: 1–20 objects within `limits.inputs`.
Each has exact `id`, `action`, `target`, `preconditions`, `effect`, `input` keys.
Step IDs are unique; action is a review description. `target` is one
`{"field":"name","equals":"label"}` predicate. Preconditions and effects are
lists of 1–4 such predicates with distinct field names. Values must be known
observed labels; inferred, unknown, ambiguous or missing required fields stop.
Input is exactly `{"kind":"click","button":1,"hold_ms":0}` with button 1–3
and hold 0–5000 ms. There are no loops, strategy choices or arbitrary coordinates
in the plan. A multi-step plan composes the same single effect-verified operation.

An `event-wait` instead has `until` (1–4 predicates), `cadence_ms` (100–10000),
`max_frames` (1–200), `retain_frames` (1–16), and zero `limits.inputs`. It calls
capture/recognition locally under the owning controller and never `dispatch`.

## Admission, transitions and retained uncertainty

Every step reobserves and reidentifies. Generation, enabled control/revision,
profile/conditions, full/crop geometry, frame hash, packet/state identity and
helper token/freshness must agree. Target coordinates come from the current
field's single source rectangle in that frame and are mapped once to monitor
pixels. The helper revalidates authority at dispatch. No target/token is reused
from a preceding action or a post-action capture. The fixed-region prototype
supports qualified stable anchors; dynamic target tracking requires another
qualified adapter, rather than silently reusing an old anchor.

Preconditions must hold and the complete expected effect must not already hold.
After a completed dispatch, another subsequent-frame capture must show the
expected effect under a comparable profile. Pixel motion/stability and a token
change alone do not verify an effect. Declared terminal scenes return control;
post-input effects still need verification. Unknown/unexpected scenes, failed
effects, takeover, expiry, consumed tokens, disconnects, deadline/cancellation or
storage/persistence errors stop all later input. There is no retry, new-match
action, policy change or run-clock mutation. The owner decides match completion.

The new private bundle pins `plan.json`, `capture-N.png` and `capture-N.json`
(canonical packet, recognition, compact helper capture). A durable
`step-N-intent.json` records `unknown` delivery before dispatch. Then
`step-N-dispatch.json` preserves the helper result immediately, followed by
`step-N-effect.json` after successful verification. Helper input IDs remain
helper-owned. A crash after intent requires inspection, even if no dispatch
receipt exists. Never execute the plan again to recover an uncertain step.

`receipt.json` is the final marker, with per-step `not-attempted`, `refused`,
`attempted` or `completed` status; delivery and semantic effect are separate.
A completed input with a missing post-capture remains delivery `completed` and
effect `unknown`. The receipt retains deltas, capture references, error/partial
state, elapsed time and explicit unasserted match completion/game outcome.
If final persistence fails, JSON returns `artifact:null`, the bundle path and a
persistence error; earlier intent/evidence remain. Errors before bundle creation
return `partial:false`; later errors retain the partial bundle. CLI exits nonzero
for errors. Ordinary terminal return, predicate satisfaction or capture-count
exhaustion is reported in `stop` and does not assert game success.

Waits record request/capture/completion timestamps and actual start-to-start gaps,
including capture latency; configured cadence is the delay between captures.
After a successful replacement, only this call's oldest frame/metadata files are
evicted to its declared ring size. Failed captures preserve the previous frame
plus any issue media. Retention dispositions stay in the receipt, including
incomplete eviction and the exact files already removed. Unknown/unexpected
scenes, cancellation and deadline return control; a wait timeout is never a game loss.

## Disposable application qualification, after refresh

This is a recipe for a separately authorized session. The
[dated qualification](../testing/conditional-cachyos.md) records the subset
already exercised and its remaining boundaries:

1. Record installed source/helper revisions and current consent/control state in
   a private preflight bundle. Refresh only at an owner-selected safe checkpoint.
2. Open a disposable app with two large stable colored panels/buttons and an
   independently visible state counter. Label captures, freeze the small profile
   and templates, and validate it against separate held-out captures before input.
3. In a new private run, execute **one** click whose scene predicate changes and
   verify the counter independently. Inspect intent, helper delivery, subsequent
   frame, source rectangles, hashes and effect receipt. Confirm coordinates under
   the selected monitor's actual scaling/crop.
4. Compose two qualified steps; move/resize the app, change display scaling,
   occlude an anchor and take human control between steps. Confirm the operation
   stops and retains evidence without a second click. Expire/consume a token,
   interrupt transport and simulate unavailable post-capture in disposable tests;
   inspect unknown/completed delivery without replay.
5. Run a capture-only wait for the second scene. Check no input events, measured
   cadence, deadline/cancel return, ring evictions and retained issue media.
6. Record pass/fail per boundary and release only the qualified profile/adapter
   pair. A subsequent game trial needs its own scope, thresholds and outcome
   mapping; preserve original run clocks and both repositories' source commits.

The dated qualification covers one native profile/crop and current monitor
mapping. Other scaling/layouts, app focus/animation, real transport interruption,
input release under interruption and game semantics remain unqualified.
Mocked acceptance does not establish those properties on the workstation.
