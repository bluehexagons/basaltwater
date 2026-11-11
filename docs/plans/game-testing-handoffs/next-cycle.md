# Next-cycle implementation and CachyOS qualification handoff

This is the current entry point for Basaltwater's NC-01–08 follow-up to the
[next-cycle plan](../game-testing-next-cycle.md). It authorizes no desktop work,
helper refresh, permission change or new match. Reconcile any existing run and
its owned resources before selecting a maintenance or testing window. Private
current-run state belongs in the controller's checkpoint, not this repository.

## Delivered scope and remaining ownership

| Item | Delivered here | Next owner / acceptance still needed |
| --- | --- | --- |
| NC-01 | [Crop-first targeting recipe](../../workstations/desktop-observations.md#small-consequential-controls), optional small-control/decoy probe, synthetic wrong-but-valid mapping regression | CachyOS agent: reduced-view and crop activation in disposable probe |
| NC-02 | Reuse [observation sequence and probe](../../workstations/short-observation-sequences.md); qualification matrix below | CachyOS agent: exact installed CLI/helper/skills qualification; consuming project: game transitions |
| NC-03 | Private read/exposure checklist below, using existing digest-bound paging | Consuming project: prospectively approve a compact required closure and try a fresh context |
| NC-04 | Manual linked-successor recipe below; terminal ledgers remain immutable | Consuming controller: identity, clocks and attempt accounting; no automatic parent validation |
| NC-05 | Result-review checklist and correction format below | Consuming reviewer: independently inspect decisive evidence |
| NC-06 | Session/resource summary format below, reusing canonical verification | Owning controller: reconcile every segment and owned resource |
| NC-07 | Prospective trial/evaluation brief below | Consuming project: strategy, budgets and explicit restart authority |
| NC-08 | Timing coverage format below | Controller: small baseline; provider integration: actual usage receipts |

No evidence schema, `basaltw` CLI, helper or installation contract changes are introduced.
The standalone probe adds the optional `--small-controls` switch. Do not
refresh a healthy running helper for these recipe/probe changes alone.
The existing generic contracts already support these manual recipes; defer new
read-set, parent-link, aggregate-session or telemetry APIs until adoption shows
a measured gap. Manual checklists do not automatically reject cycles, changed
bytes, incompatible identities or corrupt parents. Validate these explicitly;
retain that limitation in adoption receipts.

## Required reading and exposure (NC-03)

Read repository authority first: `AGENTS.md`, the contributor guide, this handoff,
then the relevant capability procedure. This entry point does not waive any
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

## CachyOS agent's execution order

1. Inspect source revision/dirty state, installed CLI and skill delivery, running
   helper provenance and private current-run checkpoint using read-only preflight.
   Record mismatches/unavailable checks. Confirm active-run disposition; this
   handoff carries no assertion that a previous game or cleanup finished.
2. At a separately authorized maintenance point, refresh only components shown
   stale and needed for qualification. Follow the existing safe refresh procedure;
   never weaken permission/freshness or restart during a live match.
3. When disposable native testing is authorized, run the probe matrix below.
   Retain exact private artifacts, command exits and timing gaps. Close only owned
   probe resources normally. No game launch is needed for these checks.
4. Reconcile companion-project instructions and publish a private adoption receipt:
   producing/consuming revisions, fixture digests, invoked/installed/running
   provenance, skill exposure, contracts reused, checks and unresolved objections.
5. Return implementation proposals for demonstrated gaps with exact writable files,
   compatibility impact and synthetic acceptance cases. Keep schema/CLI edits with
   one owner. Run `make check-fast SUITE=desktop`; CLI/packaging changes also need
   `make artifact-check`. Review final diff and `git diff --check` before commit.

| Native probe case | Required evidence / failure behavior |
| --- | --- |
| Reduced full view and bounded crop | Fresh controller activates the intended small control through each established transform; verify visible effect and exactly one event per independent probe |
| Numerically valid wrong target | Identify a neighboring control/incorrect transform before input; after any observed miss, fresh inspection and changed mapping explanation; no blind retry |
| Crop drag boundary | Offline rejection of endpoint outside crop; do not attempt it live |
| Normal observation pair | Exactly one intended input, two unique PNG paths; actual helper/frame/controller times and gaps retained |
| Deliberate second-capture collision | Nonzero receipt retains completed input and first PNG; no replay; exactly one probe event |
| Headroom and rotation | Installed helper reports remaining events/warning; link separate segments and retain gap; do not fill a live journal merely for a clean pass |
| Human control change | Real permitted takeover cancels subsequent work; never simulate account approval; unavailable case stays unqualified |

Offline Debian fixtures do not qualify KDE portal/PipeWire timing. Existing
Debian probe observations remain valid only within their documented boundary.
Native Windows, gamepad and consuming-game transitions remain separate.

## Source acceptance and follow-up scope

On source delivery `f5f3aa671e4f71aceeeb7412c65b17444478c4f4`,
`PATH="$PWD/.venv/bin:$PATH" make check-fast SUITE=desktop`
passed: 848 tests, maintained-Python compilation, CLI documentation, local links
and package metadata checks. `git diff --check` passed. The system Python lacked
development extras; checks used the existing project virtual environment. The
probe behavior was checked with mocked Tk and private temporary files, including
default-mode compatibility, decoy event isolation and blocked-output creation.
No graphical session was opened for that initial delivery. Full CI, native KDE
qualification and consuming-project adoption were not performed at that revision.

The successor-recipe follow-up passed the same fast checks with 849 tests. Its
real-parser/offline-ledger regression verifies frozen parent bytes, distinct
segment clocks, same-match/context references and unavailable elapsed after a
boot change, without contacting either desktop backend. Published successor
commands are also included in the parser-tested recipes. These checks exercise
the existing ledger API; manual parent-chain checks and original-deadline
enforcement still belong to the controller.

Local review on source `18f43def0b0c6b09223a88c8cbec28a3aa2a87e4` subsequently
passed `make check-ci` in the project virtual environment: 9650 tests run, six
skipped, 82% reported aggregate coverage, plus compilation, documentation, brand,
package metadata and wheel build/install smoke checks. This is a local Debian
result, without a claim about hosted CI or native KDE behavior.

The same source passed the [real Tk small-control development check](../../workstations/short-observation-sequences.md#small-control-development-check-2026-11-11)
in an isolated Xvfb display. Instrumented widget geometry supplied the numeric
targets; the test does not qualify a fresh controller's visual interpretation.
Default, Decoy recovery and blocked-output behavior passed with one Advance each
and normal probe close. An initial Settled log/Transition PNG mismatch remains
retained separately from follow-up rendered-frame evidence. Phase logs alone
cannot establish what a capture shows. These disposable fixtures did not touch
the shared desktop, account permission, helper or game.

For the CachyOS follow-up, expected writable source scope is
`scripts/desktop_observation_probe.py`, `tests/test_desktop_recipes.py`,
`tests/test_desktop_native_visuals.py`, `tests/test_desktop_qa_media.py`,
this handoff and the two workstation recipes linked above. Inspect actual failures before extending that scope to
`desktop/coordinates.py`, `desktop/client.py` or native helper code; add focused
regressions and compatibility notes for any demonstrated defect. Do not edit
the companion repository as part of a Basaltwater-only assignment. Keep captures,
runtime provenance, exact evidence paths and operational checkpoints outside Git.
No live helper, shared desktop, account or game state was changed for this
delivery. All disposable probe runs, Xvfb wrappers and local test commands
completed. Previous-run resources remain the prior controller's responsibility
until its private checkpoint is reconciled.

Copy this adoption receipt into private state and fill it from observations.
Pending fields are not a compatibility pass. Keep the tested source revision
separate from whichever HEAD happens to be present during handoff.

```text
Producing Basaltwater revision and clean/dirty state:
Consuming project revision and clean/dirty state (pending until reviewed there):
Probe/recipe fixture paths and SHA-256 digests:
Invoked CLI source; managed source; running-helper startup source:
Installed skills; new context's reported exposure; unavailable provenance:
Changed contracts: optional standalone probe flag; no evidence schema change
Offline commands, tested revision, exits and private evidence:
Disposable native cases: passed / failed / unavailable, with platform and evidence
Consuming-game cases: scene / trigger / resolution / terminal, separately qualified
Current run disposition; owned resources; cleanup responsibility:
Open objections, adoption changes and exact next action:
Explicit next-cycle activation source, or not activated:
```

Review the consuming project's current authority, tasks, controller instructions
and result format against NC-01–08 together; record each adopted recipe and each
remaining difference. No companion revision or installed/runtime compatibility
was established by this source-only delivery. The future agent must not fill
those fields from this repository's HEAD or from a version string alone.

## Consuming-project restart gate (NC-07)

Only explicit activation permits gameplay. First freeze a standalone brief:
question, trigger, resource reserve, bounded action, expected observable effect,
stop rule and concrete deferral condition. Permit a useful negative result without
forcing a purchase or another match. Record qualification separately from later
ordinary use of the mechanic.

After review, freeze an evaluation packet digest, start count, outcomes and
prospective decision criteria. Include actual model/effort/service evidence,
knowledge/media closure, game/build/platform, account-history limits, settings,
root clocks, ownership and cleanup responsibility. Do not extend the block until
a win. Report all started matches, setup failures, sequential learning, coaching
and operational recovery exposure. No causal improvement, model ranking or
cross-platform claim follows from a few wins with changing conditions.
