# Desktop evaluation receipts

These tools address instruction delivery, targeting and evidence friction in
bounded desktop evaluations. They do not play a game, select strategy, prove
focus or relax human control. See [observations](desktop-observations.md) for
frame binding and input failure stages, and [game testing](game-testing.md) for
qualification, original clocks and normal process cleanup.

## Compact read-only preflight

```bash
basaltw desktop --native preflight --repository /absolute/project \
  --instruction /absolute/skills/desktop/SKILL.md \
  --instruction /absolute/project/AGENTS.md --output /absolute/private/preflight --json
basaltw desktop instructions /absolute/skills/desktop/SKILL.md --json
basaltw desktop instructions /absolute/skills/desktop/SKILL.md \
  --start 4000 --sha256 RETURNED_DIGEST --json
```

Omit `--native` on Debian. Preflight uses the existing manifest, installation
context, skill comparison and desktop status facilities. It performs no capture,
input, start, repair or permission transition. Full selected observations stay in
private `preflight.json`; checks individually report `observed`, `unavailable`,
`mismatched` or `not-attempted`. There is no aggregate readiness success. An
observed helper state of `stopped` or control state of `human-override` is an
observation, not permission to proceed. Inspect capture freshness separately.

Only clean, comparable source evidence can report an observed source match.
Missing worker startup evidence and stopped-worker receipts remain unavailable;
the current on-disk HEAD cannot substitute for startup provenance. Matching
startup metadata is not immutable loaded-code attestation. Delivered skill
comparisons expose unknown counts even alongside mismatches. Session-loaded
skill identity and model comprehension remain unverified.

Select only relevant required instruction files (at most 16). Preflight lists
their digests and complete unread character ranges without flooding the context.
`instructions` returns at most 4000 Unicode characters, the exact half-open
range, remaining ranges and a continuation argv bound to the digest. Follow all
needed ranges and keep a caller-owned checklist; each response describes only
its own emitted range. File changes refuse continuation with the old digest.
Referenced files require their own reads. These receipts prove bytes emitted by
the CLI, not delivery through an outer tool that truncates output, image access,
instruction understanding or compliance. Retrieve omitted ranges explicitly.

## Durable controller checkpoints

```bash
basaltw desktop evidence run start --run-id evaluation-a --match-id match-a \
  --phase preparation --review-seconds 1800 --hard-seconds 7200 \
  --output /absolute/private/run --json
basaltw desktop evidence run checkpoint /absolute/private/run/run.json \
  --phase play --note 'Last verified state and next review' \
  --artifact /absolute/private/recording-segment.json --game-clock '12:34' --json
basaltw desktop evidence run suspend /absolute/private/run/run.json \
  --note 'Waiting for owner decision; preserve the active match' --json
basaltw desktop evidence run context /absolute/private/run/run.json \
  --context continuation-a --note 'New controller context, same match' --json
basaltw desktop evidence run resume /absolute/private/run/run.json --json
basaltw desktop evidence run policy /absolute/private/run/run.json \
  --hard-seconds 14400 --note 'Owner authorized extension in the task thread' --json
basaltw desktop evidence run show /absolute/private/run/run.json --json
basaltw desktop evidence run finish /absolute/private/run/run.json \
  --note 'Outcome saved; process/evidence disposition tracked separately' --json
```

Linux controller-local receipts store original run/match identity, UTC and the
same boot monotonic clock as action/audio/accessibility evidence. Each update
appends an event in an atomic, locked private file. Continuations, phase changes,
explicit owner waits and owner policy overrides preserve original clocks. The
1000-event capacity refuses further writes instead of truncating history.
Unsupported/corrupt records, missing boot clocks and later boots never silently
reset or align a run. A finished run cannot be continued.
Reads validate saved transitions, UTC timestamps and event details as well as
monotonic ordering. Impossible histories, such as duplicate suspensions or an
event after finish, fail without rewriting the original file.
When saved `previous_gap_seconds` or `checkpoint_late_seconds` fields are present,
reads also recompute them from preceding event clocks and effective review policy.
Contradictory values are refused without changing the record, including on
`checkpoint` and `finish`. Preserve rejected files and inspect a retained receipt;
do not remove the fields to make history load. Older records without these fields
remain readable, with their missing metadata left untouched.

Timing-metadata refusals identify the first contradictory zero-based
`events[index]`, its event kind and the value recomputed from saved clocks/policy.
That expected value is a diagnostic, not an instruction to replace a field.
Preserve the rejected artifact and command receipt. Keep subsequent observations
in a separate companion note, or a linked new segment with an explicit timing
gap and the original match identity/limits; never insert missing events into the
rejected history. A validation error alone cannot establish which writer caused
it. Audit retained execution receipts before attributing it to the CLI.

Structural validation does not authenticate who wrote earlier events. A later
successful `show` or `finish` cannot establish that every checkpoint came through
the CLI. Keep returned receipts and record execution provenance separately from
arithmetic validity. Never append events with a script or edit saved lateness to
make the record appear complete. If direct writes are discovered, retain the
original file and report the affected timing as unqualified; do not silently
repair history. Independent capture/input timestamps can still support their own
observations. This distinction does not grant an extra application Start.

When a phase needs a different soft review interval, record a sourced
`run policy --review-seconds SECONDS --note TEXT` event at its boundary.
Changing `--phase` on a checkpoint alone retains the preceding review interval.
Soft review timing and a project's independently observed match-Start anchor are
separate; retain any difference instead of rewriting timestamps or lateness.

`wall_seconds` is elapsed monotonic time, including explicitly suspended owner
waits. `declared_active_wall_seconds` excludes only recorded owner waits; it still
includes game, reasoning, tool and coordination gaps. Host system suspend can
pause this monotonic clock; UTC receipts remain available separately. Neither
quantity measures active inference. Observed active work, model reasoning time,
tokens and cost remain null without provider telemetry. `--game-clock` records
an operator observation, with no conversion to host elapsed time. Linux input
receipts additionally retain helper receive, first dispatch-attempt and completion
UTC/monotonic times; failures before dispatch omit a dispatch time. Capture age
and frame receipts retain their existing independent clocks.

Check `show` within the controller's normal work loop. Checkpoint events retain
actual previous gaps and lateness before restarting the chosen soft review
cadence. `checkpoint_due_monotonic_ns`, `checkpoint_remaining_seconds` and
`checkpoint_late_seconds` expose the current review deadline and actual timing.
`checkpoint_history` separately reports the number of completed checkpoints with
a prior review deadline, how many were late, and maximum lateness. It recomputes
from clocks/policy before each checkpoint resets the deadline; a currently
on-time status does not erase earlier late reviews. No qualifying checkpoints
means a zero count and null maximum. These checks do not authenticate authorship.
Policy changes require a source/authorization note and record a timing change;
hard seconds always refer to the ORIGINAL start. Recording an override
does not authenticate its author or grant permission. These commands neither
schedule reminders nor poll a delegate. A late soft review never triggers an
approval pause. The controller must honor hard limits, human takeover and the
task's continuation policy; this offline ledger does not enforce desktop input
admission. Follow the active orchestration policy for delegates and reminders.

Use `show` rather than dumping the full ledger into each continuation. Its compact
`phase_times` attributes each interval to the phase established by its left event,
with wall, recorded owner-wait and declared active wall seconds separately. Write
phase checkpoints at actual setup/play/cleanup boundaries; these labels are
controller observations, not automatic scene detection. Repeated phase labels
are combined. The final open interval counts through the current observation;
a finished record stops at its finish event, including on later reads.

`event_gaps` reports completed ledger intervals: count, median, maximum and at
most three longest intervals with event indices and owner-wait state.
`context_changes` counts explicit context events. Gaps include reasoning, tools,
game animations, coordination and recorded waits; they are neither action latency
nor model inference time. Sparse checkpoints cannot identify which component was
slow. A new boot remains unsupported and produces no derived phase/gap timing.
This controller-only reporting needs an updated CLI, not a helper restart.

To remove an earlier hard limit, first verify that the effective owner instruction
explicitly authorizes open-ended completion of the SAME match. If that authority
is absent or ambiguous, do not clear the limit; resolve the brief with the owner.
Then record the instruction/source in the required note, for example:

```bash
basaltw desktop evidence run policy /absolute/private/run/run.json \
  --clear-hard-limit \
  --note 'Owner instruction in task brief: finish this same match without a hard cap' --json
```

The resulting receipt and `show` report `hard_limit_seconds: null` and
`hard_limit_reached: null`. Original run/match IDs, UTC/monotonic start, prior
limits, elapsed time and policy events remain intact. Omitting both hard-limit
options leaves the current hard limit unchanged, including after clearing it;
`--hard-seconds 0` remains invalid. `--clear-hard-limit` is exclusive with
`--hard-seconds` and available only on `policy`. A later positive hard limit
still measures from the original start, so it can be reached immediately.
The note records the claimed instruction; the CLI cannot authenticate its author
or decide whether the task brief authorizes it. Clearing grants no input authority
and does not change human control, soft checkpoints or orchestration policy.

Existing version 1 run ledgers remain readable and unchanged by `show`. The first
clear event atomically upgrades that ledger to schema version 2 while preserving
all prior events. Older clients refuse version 2 rather than silently retaining
the superseded cap; use an updated client for subsequent reads and updates.
This offline change needs an updated controller CLI, with no desktop-helper restart.

## Recorder inventory and closeout

Native status includes the active recorder artifact and remaining capacity. A
duplicate recorder start returns its existing artifact and starts no extra job.
Updated helpers also attach `evidence_capacity` to action replies and compact
captures. `headroom: low` and `rotation_recommended: true` start at 50 remaining
events out of 500. `full` means no headroom; a further recorded action marks
history `truncated`. A warning does not cancel completed input or rotate silently.
Check headroom before each short batch: the observation-pair recipe uses three
recorded events (one input, two capture attempts), and other launches/inputs can
consume space too. Leave room for those operations; do not wait until 499/500.
Old helpers without these fields still need explicit status checks and safe refresh.

### Rotate a recording at a safe checkpoint

Use one existing run ledger for the match. Substitute the exact recording paths
returned by start/finish; the filenames below illustrate two different segments.

```bash
basaltw desktop --native status --json
basaltw desktop --native evidence finish --json
basaltw desktop --native evidence verify /absolute/private/segment-a.json --json
basaltw desktop --native evidence closeout /absolute/private/segment-a.json \
  --output /absolute/private/segment-a-closeout --json
basaltw desktop --native evidence start --project /absolute/project --json
basaltw desktop evidence run checkpoint /absolute/private/run/run.json \
  --artifact /absolute/private/segment-a.json \
  --artifact /absolute/private/segment-a-closeout/closeout.json \
  --artifact /absolute/private/segment-b.json \
  --note 'Rotated recording at a safe checkpoint; gap between finish and start' --json
```

Read every command's status before continuing. `finish` freezes and finalizes the
journal; if its collection fails, retain the artifact and use `evidence finalize`
on that same path. Verification failure is retained evidence, never a reason to
replay input or rewrite history. A closeout receipt is saved even when verification
is unhealthy. Record the failure and, if continuing is within scope, the explicit
rotation gap and uncertainty in the ledger. `start` returns the new segment's real
artifact; link that path after confirming it started. Finish/verify/closeout that
segment at its own safe endpoint. Rotation preserves original match clocks and
does not stop the application, restart the helper or change permission. It is
manual, with an observation gap rather than an atomic recorder replacement.

### Recheck the same recording

`closeout --previous` links repeated checks of **the same recording**. It does
not link segment A to segment B. Pass the prior `closeout.json` file, not its
directory or the recording itself. Different recordings and their closeouts
belong in `run checkpoint --artifact`, as above.

```bash
basaltw desktop --native evidence closeout /absolute/private/recording-segment.json \
  --extra /absolute/private/extra.png --derived /absolute/private/crop/manifest.json \
  --process 12345 987654 --output /absolute/private/closeout-a --json
basaltw desktop --native evidence closeout /absolute/private/recording-segment.json \
  --previous /absolute/private/closeout-a/closeout.json \
  --output /absolute/private/closeout-b --json
```

The new receipt inventories canonical recorded PNG paths, extra captures,
literal crop/source copies and image-less failures separately. It uses existing
verification and saves full private details. A saved receipt is not an aggregate
cleanup success: inspect its verification, recording/finalization state, derivative
media and resources. Missing/moved paths fail verification; bytes are never moved,
deleted or repaired automatically. After a separately reviewed, hash-verified
restoration of the original path, `--previous` links the later check to the
original failed receipt without editing either receipt or the journal. No old
uncertain/image-less event is erased by a later successful check.

`--process PID START_TICKS` takes a prior observed Linux process identity and
reports running-preserve, identity-replaced, identity-not-present or unavailable.
Missing/replaced identities do not prove normal exit. Verify normal game exit
using the exact game process/start identity AND its owned launch/task completion
receipt; a launcher exit or result screenshot alone is insufficient. Unselected
or ownership-uncertain resources remain outside the receipt's coverage. Preserve
active matches. Closing shared launchers, Steam, T3 or the helper, force-killing,
revoking grants and deleting referenced media are never default closeout actions.

Reviewed crop exports now explicitly list included originals, derivatives and
all files. The full `source.png` travels with `crop.png`; review both for privacy.
Closeout does not export media. Optional audio/video inventory remains separate.

## Compatibility and qualification

Existing offline `map-point` and journal version 1 remain supported. New optional
receipt fields do not reconstruct old dispatch stages or missing hashes. Old
journals without a comparable boot clock remain unsupported for timeline joins.
Updated native helper behavior requires a normal safe helper restart after
installation; these development checks perform no restart or deployment.

Fixture tests cover coordinate transforms/margins, changed frame context,
pre-input refusal, ambiguous transport, post-input capture failure, missing and
image-less evidence, bounded Unicode instruction reads and explicit preflight
gaps. They do not constitute a native CachyOS, Windows or game smoke test.
Windows still uses its native CLI contracts; these Linux CLI additions do not
implement Windows input-stage or post-input-observation parity. Optional gamepad,
audio and video qualification remains separate.

## Refresh and freeze a comparison condition

After saving active work, follow the existing [CachyOS refresh procedure](cachyos-maintenance.md#upgrade-and-repeat-your-last-setup).
`basaltw refresh --dry-run` previews the saved selection; `basaltw refresh` applies
it and can reload T3 after setup. A development channel follows `main`; a pinned
channel remains pinned. The unchanged `2.0.0rc1` version string cannot identify
this batch: retain the exact source revisions from preflight.

At a safe desktop checkpoint after installation, inspect native status and its
running-worker startup provenance. A stale running helper needs a normal, explicit
stop/start before testing updated input behavior; this closes its portal session
and may require renewed user consent. Do not restart during an active match or
change human control merely to make a preflight look ready. Rerun the compact
preflight into a NEW private directory, retrieve required instruction ranges,
and preserve unavailable/mismatched checks for review. Confirm invoked, managed
and running source separately. An observed startup match is still subject to
the loaded-code limits above.

Freeze the comparison condition only after that review and an independently
authorized native pointer check in a disposable application. Start a new run
ledger for a new match; continue the same ledger for the same match. Retain
capture receipts, recorder segments, errors and the final closeout inventory
privately. Never substitute offline fixture success for native qualification.

| Request | Implemented/reused contract | Offline validation | Remaining qualification/limit |
| --- | --- | --- | --- |
| BW-D01 | Frame receipt + viewer transform + single-use input token; existing offline map retained | Margins, independent scales, crops, endpoints, changed context, bounds and parser roundtrip | Controller must inspect moving targets and verify effect/focus |
| BW-D02 | CachyOS/Debian structured stage/reason/delivery and input/capture IDs; partial typing and sequence failures retained | Refusal, socket/worker receipt roundtrip, ambiguous transport, completed input/capture failure and image-less verification | Old strings stay unknown; Windows stages and live Debian qualification remain separate |
| BW-D03 | Compact existing provenance/status bundle; digest-bound instruction ranges and explicit gaps | Unicode reconstruction, changed bytes, stopped worker, skill mismatch plus unknowns | No model comprehension or immutable loaded-code attestation |
| BW-D04 | Run/match clocks, explicit waits, context/policy events and due/late checkpoint receipts | Continuation, suspension accounting, original hard limit, capacity and corrupt history | Linux same-boot timing; no provider inference telemetry or scheduler |
| BW-D05 | Recorder capacity/duplicate handle, existing verify/crop/export, new closeout inventory/receipt links | Moved paths, restoration retains prior failure, derived hashes and exact process identity | Manual segment rotation has a gap; selected process absence cannot prove normal exit |
| BW-D06 | Current parser examples, numeric buttons, pre-input path validation and duplicate-start diagnostic | Parser-to-client fixture, reused output refusal and installed-wheel offline commands | Refresh and safe helper restart required before native behavior qualification |
| BW-D07 | Explicit sourced `policy --clear-hard-limit`; omission retains policy and later caps use original start | Finite/clear/omit/reapply history, invalid requests, versioned saved records and installed CLI | Controller must establish owner authority; clearing grants no permission or scheduler |

`make check-fast SUITE=desktop` validates the relevant fixtures and static checks.
`make artifact-check` builds and installs a wheel outside the checkout; on Linux
it also exercises bounded instruction pages, offline mapping and run
suspend/context/resume/policy/clear/finish through the installed CLI. These commands
perform no desktop input, capture, helper restart or system refresh. Full Debian
CI remains the broad repository check described in the contributor guide.
