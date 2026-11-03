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

`wall_seconds` is elapsed monotonic time, including explicitly suspended owner
waits. `declared_active_wall_seconds` excludes only recorded owner waits; it still
includes game, reasoning, tool and coordination gaps. Host system suspend can
pause this monotonic clock; UTC receipts remain available separately. Neither
quantity measures active inference. Observed active work, model reasoning time,
tokens and cost remain null without provider telemetry. `--game-clock` records
an operator observation, with no conversion to host elapsed time. Native input
receipts additionally retain helper receive, first dispatch-attempt and completion
UTC/monotonic times; failures before dispatch omit a dispatch time. Capture age
and frame receipts retain their existing independent clocks.

Check `show` within the controller's normal work loop. Checkpoint events retain
actual previous gaps and lateness before restarting the chosen soft review
cadence. `checkpoint_due_monotonic_ns`, `checkpoint_remaining_seconds` and
`checkpoint_late_seconds` expose the current review deadline and actual timing.
Policy changes require a source/authorization note and record a new
limit; hard seconds always refer to the ORIGINAL start. Recording an override
does not authenticate its author or grant permission. These commands neither
schedule reminders nor poll a delegate. A late soft review never triggers an
approval pause. The controller must honor hard limits, human takeover and the
task's continuation policy; this offline ledger does not enforce desktop input
admission. Follow the active orchestration policy for delegates and reminders.

## Recorder inventory and closeout

Native status includes the active recorder artifact and remaining capacity. A
duplicate recorder start returns its existing artifact and starts no extra job.
At a safe checkpoint, finish/finalize the current recording, then start another
and attach both segment paths to the SAME run ledger with `checkpoint --artifact`
(repeat as needed). This links segments without resetting match clocks. Rotation
is explicit, with a possible observation gap; it is not an automatic streaming
journal or atomic recorder replacement.

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
