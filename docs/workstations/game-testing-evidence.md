# Game test evidence and closeout

[Topic index](game-testing.md)

## Give observers immutable packets

Use a sole controller plus a bounded observer/advisor when useful. The controller
alone captures and acts; helpers read saved artifacts and return conditional
advice. Follow active orchestration instructions for models, deadlines,
completion and cancellation. Separate simultaneous live matches need separate
qualified desktop resources.

Keep each packet outside Git with these fields:

```text
packet ID; private run/phase/match/session record; recorder artifact
capture path, UTC, artifact_sha256; scene/turn; recent action and observed result
monitor geometry; PNG size/origin; presentation size; coordinate convention
one question; expected deliverable; validity conditions; decision deadline
remaining shared budget; requested/actual model and effort; advice access policy
```

Verify that the actual worker can open the image and has visual input. An
absolute local path does not establish access from another environment. Preserve
originals and packet IDs; use separate crops/redactions as derivatives. Require
the reply to identify the packet, observations, uncertainty, coordinate space,
conditional recommendation and expiry. The controller rechecks current scene
and target after any reorder, purchase or transition; old advice can be stale
before the capture's selected age limit. Disregard late optional advice.

For document reviewers, give an explicit repository root and exact paths
relative to it, or private absolute paths. Resolve links to required inputs
before assigning the review, including any separate protocol or comparison
matrix. Require the worker to list what it read and any unread inputs. Missing
or ambiguous paths mean partial review; the coordinator must cover that gap
before treating the material as reviewed.

Record material assistance, corrections and advice used/rejected in the private
session record linked to the recorder artifact. The recorder does not infer
run IDs, model settings or strategy assistance. Freeze these conditions for
team comparisons; quarantine optional advice until the outcome for solo tests.
All workers share the original clocks and attempt limits. Report elapsed wall
time once and aggregate worker time separately when available.

## Retain and verify evidence

Keep controller-local `evidence run` clocks separate from the screenshot recorder.
Create and append run events through the CLI, using its returned artifact path,
absolute artifact references and bounded notes. Use `policy` for changed timing
limits; checkpoints record observations. Inspect `show` after creation and at
context transfers. Do not handwrite, append JSON to, shorten or normalize the
canonical ledger to make it validate. Preserve a rejected record and failed
receipt; record a linked new segment and explicit timing gap if continuation is
necessary. If a ledger was already edited, disclose the edits and retained
before/after evidence. Successful validation cannot establish untouched history.

On context recovery, compare the dispatch timestamp and its state cutoff with
later input receipts and the latest checkpoint. An original zero-Start brief
can correctly precede a Start made in that same assignment. Continue the counted
match; do not treat the historical brief as authority for another Start or claim
a contradiction without checking chronology.

Choose durable private storage outside Git for each run, with enough space and
a declared retention window. Record actual locations privately. `--save-output`
observations use temporary storage; copy needed JSON/media to durable storage,
verify bytes/hashes, reopen important captures and update the private index
before retiring scratch files. A temporary path is no durability guarantee.

New native captures include `artifact_sha256` for PNG file bytes as well as
`pixel_sha256` for visible RGBA pixels. Compact responses and the evidence
journal retain both. Check referenced originals before citing them and after
any reviewed storage consolidation:

```fish
basaltw desktop --native evidence verify /absolute/private/record.json --json
```

This command reads the existing journal and capture files without contacting
the desktop, finalizing, moving, deleting or repairing evidence. It reports
verified, missing, mismatched and unverified counts, with up to ten problem
details and an explicit flag when further problems are omitted. Unsafe or
oversized files, absent file hashes on older records, and uncertain captures
remain unverified. Truncated journals and records with no captures cannot return
a successful verification. A nonzero status calls for investigation, never
blind input replay. An active record can be checked; finalization is reported
separately. File equality does not verify image meaning, outcome, task logs,
omitted events or video/audio. Reopen images to check the claim they support.

Keep original paths usable when retaining recorder references. If evidence must
move, keep the original until a verified private relocation index explains the
new location; this verifier does not rewrite journals or follow a relocation
index. Hard-linked retained copies are immutable: crops and annotations must
be new files. Age alone is not disposal authority, and referenced media stays
retained while an unresolved finding needs it.

## Close out and report limits

Inspect the result screen or other declared terminal state independently of
worker notes. Finish the evidence recorder and inspect its finalization,
truncation, capture paths/hashes, task snapshots and errors. Finalization does
not stop applications or prove tasks completed. Preserve original artifacts
privately. Close task-owned game/test processes after saving the outcome unless
the brief requests continuation; verify exit through their launch/task handles.
Leave helper/agent control in the owner's existing state and record any active
task or pending input. Remove only disposable, unreferenced artifacts under the
project's retention policy; keep evidence needed to reproduce findings. Share
sanitized findings only.

Use a private closeout inventory for each owned launch, supervised task, helper
job, recorder, temporary setting/device and artifact group. Mark each as exited,
finalized, restored, retained with reason, or unresolved, with current evidence.
Recorder finalization snapshots are historical; recheck live handles when
closing processes. A launcher exiting does not prove its game child exited,
and an absent accessibility window does not prove process exit. Preserve shared
launchers and desktop services. Never use broad process-name kills for cleanup.
Record reviewed artifact dispositions, retained-copy verification and measured
storage reclaimed. If a close action may lose progress, preserve state and
report partial cleanup rather than silently force termination.

The journal caps events at 500 and task attachments at 50. Plan recording
boundaries at safe checkpoints for long sessions: finish and inspect the
existing recording, then start the next and link both to the same run/match
without resetting clocks. Retain any truncation as a coverage gap. Recording
boundaries do not close the game or authorize another attempt.

Separate intended action, observed input delivery, verified game effect,
targeting/scene mistakes, rejected calls and unresolved platform questions.
Attribute a backend defect only with reproducible evidence. Missing renderer,
desktop-scale, video/audio, latency, token or cost observations remain unknown.
Frame receipt and stream PTS do not measure input-to-photon or model latency.
Report actual stage timings only when measured; wall-clock duration includes
game waits, reasoning, tool and coordination overhead.

For model comparisons, require each worker's qualification and comparable game
build/state, rules, strategy assistance, waits, budgets and completion criteria.
Record model/effort from the active orchestration system. Targeting failures,
scene errors or unequal handoff assistance confound play quality: report the
run as exploratory and repeat qualification before drawing model rankings.
This procedure and the coordinate aid do not themselves qualify a game, a fresh
worker, another platform route, or any unmeasured telemetry.
