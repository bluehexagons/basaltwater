# Game testing handoff requirements

[Topic index](next-cycle.md)

## Required reading and exposure (NC-03)

Read repository authority first: `AGENTS.md`, the contributor guide, the
[handoff scope](next-cycle.md) and its relevant topic pages, then the capability
procedure. This entry point does not waive any
whole-file reading requirement. Existing historical links are optional only
where the owning instructions already say so. The consuming project must approve
any reduction in its own mandatory reading prospectively.

Before settings changes or application launch, create a private checklist:

```text
Context/controller ID; actual model/effort/service evidence or unavailable:
Authority source and current scope; current task entry point:
For each required source:
  role (authority/task/knowledge), path, SHA-256, character length
  required dependencies (explicit paths only), required image IDs
  emitted half-open character ranges; outer-tool received ranges or unknown
  reported model exposure; outstanding ranges/images; comprehension limits
Optional history (excluded from required closure with owning instruction):
Closure review: cycles/missing sources/changed bytes/bound exceeded:
Preparation complete or blocked, with unresolved requirements:
```

Use [preflight and instruction paging](../../workstations/desktop-evaluation.md#compact-read-only-preflight)
for each required source. Preflight accepts at most 16 instruction files per
invocation and pages emit at most 4000 Unicode characters. These command limits
do not impose a total context budget or waive longer mandatory reading. The
consuming project sets its prospective closure bounds; record them in the
checklist. If required closure exceeds those bounds, resolve its organization
before proceeding without dropping authority. Do not crawl links automatically.
Review explicit dependency edges for cycles and missing sources before reading the closure.
Pinned continuation digests reject changed bytes; invalidate the affected read
checklist and review its dependents when bytes change. Reconcile received ranges
after outer-tool truncation. A new context starts with no claimed exposure even
if file hashes match an earlier context. Image paths and emitted text do not
prove image access or comprehension. Resolve gaps before desktop mutations.

## Linked successors and attempts (NC-04)

Prefer `run context` on an existing nonterminal ledger; it preserves original
clocks. If a ledger was finished early, keep it unchanged. Do not reopen it or
backdate events in a successor. Read and validate the predecessor with `run show`
first. Save its artifact path and SHA-256 in a private continuation note:

```text
Same match ID; distinct successor run ID; predecessor path and SHA-256:
Original root run ID, UTC/monotonic anchors and boot identity:
Continuation reason; observed current state; authority source:
Controller/context/model exposure before and after; unknown intervals:
Successor start/boot and segment elapsed; original elapsed or unavailable:
Input intent / dispatch-delivery / observed match-start effect:
Start budget consumed so far; setup failures and missed inputs separately:
```

Check the entire declared ancestor chain for duplicate/cyclic IDs, changed
hashes and mismatched match/root identities. A corrupt or missing parent blocks
a trusted join; preserve it as unresolved evidence. Create a new ledger with a
distinct `--run-id` and the **same** `--match-id`, using the existing
[run commands](../../workstations/desktop-evaluation.md#durable-controller-checkpoints).
Attach the predecessor and continuation note with `checkpoint --artifact`.
The new ledger starts now: its built-in elapsed and hard limit describe that
segment, so separately carry and enforce the original authorized deadline.
Creating it grants no new Start allowance. If original deadline/authority cannot
be established, record the gap and resolve it before further input.

For example, after independently checking the chain and writing the private
continuation note, a successor can be created and linked as follows. The paths
and IDs are fictional. The 80-second limit is illustrative: calculate the actual
remaining allowance against the original deadline immediately before starting.
Continue enforcing that original deadline as well, including command gaps.

```bash
basaltw desktop evidence run show /absolute/private/predecessor/run.json --json
basaltw desktop evidence run start --run-id recovery-b --match-id match-a \
  --phase recovery --hard-seconds 80 --output /absolute/private/recovery-b --json
basaltw desktop evidence run checkpoint /absolute/private/recovery-b/run.json \
  --context controller-b --artifact /absolute/private/predecessor/run.json \
  --artifact /absolute/private/continuation.md \
  --note 'Same match; root deadline and parent digest retained in continuation note' --json
```

Read each exit status before continuing. Use the returned successor artifact
path, not an assumed filename if your command differs. Artifact references are
paths only: checkpoint does not hash, pin or validate a parent chain. Preserve
the parent bytes and note digest separately. After reboot, `show` validates the
saved history but returns `timing: unsupported-clock`; its missing elapsed fields
must remain unavailable. A new same-match segment on the new boot cannot repair
that gap or justify replaying an uncertain input.

Report root elapsed and successor elapsed separately. Same-boot comparable
anchors permit original monotonic elapsed; across reboots keep UTC and uncertainty
without fabricating monotonic continuity. Do not sum overlapping segment active
times. Use existing corpus match grouping for outcomes. Count an observed start
once; retain unknown delivery and setup-only failures separately, including their
resource cost. A labelled Start click does not prove a start or permit retry.

The [corpus contract](../../workstations/learning-corpus.md) requires included
segments for a match to agree on start UTC and condition ID. A changed model or
other changed condition during recovery cannot be silently grouped into a fixed
comparison condition. Retain the exposure in the continuation note and explicitly
exclude incompatible segments from that comparison, with reasons; preserve the
match's start accounting outside that comparison. Do not relabel conditions to
make validation pass.

## Results, session closeout and timing (NC-05/06/08)

Use existing [learning records](../../workstations/learning-records.md) for result
fields and evidence references. A separate reviewer rereads the decisive images
before publication. Keep the worker's original prose; append a dated correction
with original value, corrected value or unresolved ambiguity, evidence ID and
reviewer. Inspect placement, rounds, combat totals, displayed duration and relevant
counters independently. Game-owned rules determine interpretation. A correct PNG
inventory with a dropped duration digit is a reporting correction, not proof of
clock speed or a semantic pass. Leave ambiguous digits unresolved.

Create a private session summary linking artifacts, without editing journals:

```text
Match/root ID; predecessor/successor chain; canonical artifact paths and hashes:
Per recorder segment: verification receipt, finalized state, independent counts,
  missing/image-less/uncertain/truncated evidence, start/end and rotation gaps
Per closeout: recording identity and prior check of that SAME recording
Per owned resource: original setting/process identity, responsible controller,
  intended restoration, observed completion evidence or explicitly deferred
Normal quit; exact game-process absence; owned launcher completion; visible sync:
Logical controller ownership; permission; portal grants (reported separately):
Uncovered resources, unavailable exit telemetry and unresolved cleanup:
```

Use [rotation and closeout](../../workstations/desktop-evaluation.md#recorder-inventory-and-closeout)
and retain unhealthy segments alongside healthy ones. `closeout --previous`
cannot link different recordings. Two healthy segments and one uncertain segment
remain three separately reviewed entries. Selected process absence does not prove
normal exit. Restore owned temporary settings or explicitly defer them; preserve
shared launchers/helpers/grants. The summary never deletes media, force-terminates
resources or declares aggregate cleanup success.

For a small baseline, record instruction emission, preparation, input completion,
capture acquisition, controller gaps, observed match completion and cleanup as
separate intervals. Each row names artifact/field, units, boot/clock scope,
start/end, missing coverage and overlap/deduplication decisions. Do not subtract
monotonic timestamps across boots or helper/controller scopes without proven clock
alignment. Keep displayed game duration, host elapsed, declared active wall time
and provider-measured inference separate. Unknown gaps stay unknown; unavailable
tokens/cost/model telemetry stays unavailable, never zero. Provider-requested
settings alone do not establish actual execution. No latency claim follows from
controller receipt gaps or stream PTS.
