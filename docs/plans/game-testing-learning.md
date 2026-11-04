# Game testing: learning, coaching and verified local execution

**Implement an offline evidence contract before adding a game-playing loop.**
BW-GL02A now implements the [version-1 offline record contract](../workstations/learning-records.md).
BW-GL06A implements [closed-corpus review](../workstations/learning-corpus.md).
BW-GL04A implements [read-only scene-region perception](../workstations/local-perception.md)
with synthetic acceptance. BW-GL05A implements [conditional execution and event waits](../workstations/conditional-execution.md)
with mocked acceptance; none of these new learning facilities are live-qualified.
Existing [game-testing tools](../workstations/game-testing.md)
remain usable. No helper restart, model call, game input or system refresh is part
of this planning increment.

Use the [implementation handoffs](game-testing-handoffs/README.md) to assign
bounded jobs with contract ownership, writable scope and acceptance. Start the
offline records contract first; source delivery and live adoption remain separate.

## Responsibility

Basaltwater provides generic private packets, validation, optional local perception,
guarded conditional execution and timing/feedback receipts. A consuming test project
owns game rules, strategy, UI profiles, learning releases, coach policy, allowed
knowledge and outcome interpretation. Use the companion
[game-testing plan](https://github.com/bluehexagons/game-testing/blob/main/common/IMPROVEMENT-PLAN.md)
for that project's tasks. Contracts use stable IDs/versions, never personal paths.

## Reuse rather than replace

| Existing source | Use / remaining boundary |
| --- | --- |
| `desktop/client.py::run_sequence` | Already bounds 1–20 actions and 20 seconds with control checks/partial receipts; no semantic branches, target recognition or fresh-token chaining |
| `desktop/client.py::observe_after_input` | Retains completed input on failed capture; semantic effect verification still needed |
| `desktop/observations.py`, `desktop/coordinates.py` | Capture identity, age, single-use authority and image mapping; recognition cannot renew these tokens |
| `desktop/qa_artifacts.py` | Private bundles, pinned bytes, stamps and process identity; durable output selection must be explicit |
| `desktop/run_checkpoint.py` | Original clocks/context/policy/artifact references; not a scheduler or model launcher. Notes/ref limits favor linked packets |
| `desktop/evidence.py` | Integrity/closeout and selected process state; not proof of action effect or normal exit |
| `lib/agent_visual_batch.py` | Offline image galleries/manual inspection; not OCR or semantic recognition |
| `lib/agent_memory.py` | Mutable CAS notes/todos can point to current state; not immutable knowledge releases |
| `lib/agent_models.py` | Reviewed task acceptance/cost evidence; not game outcomes or an automatic stronger-coach selector |

`desktop/perception.py` now provides a bounded fixed-region template prototype;
numeric OCR is not implemented.
Keep generic additions under the desktop boundary; `game/` has an existing
game-specific setup integration and should not become a universal strategy engine.

## Milestones and acceptance

| ID | Next change | Required acceptance |
| --- | --- | --- |
| GL-01 | Coordinated plans, ownership and consuming-project procedures | Planning delivered; runtime status explicitly planned |
| GL-02 | Offline observation/decision/coach packets and closed-corpus validation | Synthetic complete/missing/conflicting/oversized/stale inputs; no desktop or model calls |
| GL-03 | Coach packet/checkpoint integration and consuming-workflow trial | Immutable packet access, stale response rejection, assistance/actual settings and measured coordination; controller owns dispatch |
| GL-04 | Optional local scene/region detector interface and delta output | Labelled held-out frames, field/target-specific errors, abstentions, processing time and version/profile drift tests |
| GL-05 | Bounded conditional action executor and event waits | Mock failures then disposable app; every action rechecks authority and recognized target/effect; no replay after unknown delivery |
| GL-06 | Offline per-condition corpus summaries and policy-review inputs | Match deduplication, setup failures, original/eventual outcomes, unknown telemetry and confounds retained; future policy validation remains project-owned |

GL-03 can start with manual packets; GL-04 can use retained offline frames while
learning improves. GL-05 depends on GL-02/04 and disposable qualification. GL-06
starts as reviewed corpus extraction, not reinforcement learning or a strategy
ranking service. Ship small increments with source commits and contract versions.

## GL-02: proposed version-1 records

Define one shared contract before adding a CLI. Names below describe data, not
promises of existing commands. Keep JSON and media private; synthetic examples only.

| Record | Minimum fields |
| --- | --- |
| Observation packet | Schema/packet/run/match/phase/round IDs, state revision, capture UTC/clock scope, frame byte hashes/geometry/origin, game/build/profile/recognizer versions, fields with observed/inferred/unknown status and source regions |
| Decision/action record | Packet/state ID, policy/knowledge versions, intended semantic action, preconditions, alternative/opportunity cost, expected effect, dispatch identity/stage/delivery, observed effect/evidence or unknown |
| Coach request/reply/disposition | Immutable packet ID, trigger, allowed corpus/knowledge digests, requested/actual settings, bounded question, deadline/intervention budget; reply evidence/recommendation/validity; controller acceptance/rejection/expiry and result |
| Closed corpus manifest | Corpus cutoff/hash, source record hashes, stable match/segment IDs, explicit inclusion/exclusion reasons, build/environment/knowledge/policy/adapter/team condition, starts/setup failures, cutoff and eventual outcomes, timing/usage scope and evidence availability |

Use existing validators/atomic I/O and private artifact primitives. Explicit limits
must cover record bytes/counts, media bytes/pixels, recursion, path scope and total
output. Reject unsupported versions, duplicate/conflicting IDs, NaN/infinite values,
unsafe paths and mixed frame/geometry identities. Treat booleans distinctly from
numbers. Preserve missing telemetry as null/unknown, never zero or inferred cost.
Store immutable pinned inputs and separate append-only dispositions. An offline
snapshot is not a live observation lease. Add artifact references to existing run
checkpoints rather than embedding large packets in their notes.

Deduplicate continuations by match ID and retain each segment/cutoff. Conflicting
terminal records fail for review, not last-writer-wins. Zero-start failures stay
visible outside match outcome fractions. Group by declared condition, never simply
model ID. The consuming project owns mapping game placement to win/loss and review
of evidence semantics. No automatic model/policy selection from these summaries.

First acceptance uses temporary synthetic fixtures and mocked system boundaries:
complete/partial packets, changed input bytes, unknown state, conflicting outcomes,
stale/late advice, preserved nulls, corpus deduplication, bounds and private output.
Register tests in the owning suite. Then exercise an installed offline CLI; native
capture/input/Windows qualification are separate claims.

## GL-04: qualify small local recognizers

Start with scene labels, stable button/slot anchors and bounded numeric OCR;
template matching or OCR preprocessing may suffice before training a model.
Keep recognizer implementations optional and select dependencies after measuring
them. A detector consumes pinned frames and emits values/regions/uncertainty with
its version, settings and elapsed time. No detector reads game memory/source or
sends input. Recognition scores are not calibrated probabilities by default.

The project labels representative scenes and reserves whole runs for a held-out
set, avoiding near-identical adjacent frames across train/test splits. Include
hover/animation/occlusion, layout/scaling changes, unreadable text and unknown scenes.
Report false confident detections and abstentions per field/target; choose required
thresholds by action risk before live use. Do not let success on one button qualify
card identity or terminal victory. Preserve source frames and literal crops.

Return compact state changes and ambiguous crops to the main agent. Pixel motion
alone is not semantic progress. Detect version/geometry/profile drift and require
requalification of affected regions; never silently keep old coordinates.

## GL-05: local observe–act–verify batches

Build above `send_action` and existing receipt mapping, without relaxing
`run_sequence` or helper admission. A plan declares semantic targets, predicates,
step/input/time/storage limits and permitted terminal branches. Start with a
single verified action, then purchase/place-style recipes. Before every action:
capture/recognize fresh state, validate scene/target/preconditions, map once and
use current helper authority. Afterward verify consumption/gold/board/scene effects.

Stop and return partial results on ambiguity, contradiction, failed predicate,
unknown delivery, control takeover or limits. Never replay possibly sent input or
reuse a token after mutation. Preserve attempted/completed/refused/not-attempted
steps. Snapshot state revision changes invalidate advice; durable policy advice
requires its explicit preconditions to be rechecked. Unexpected terminal scenes
return to the controller for outcome/cleanup handling, not automatic Play Again.

An event wait captures locally under the same controller and returns on a qualified
scene predicate, unexpected state, cancellation or deadline. It sends no gameplay
input. Bound capture cadence and storage, record actual timestamps/gaps, and keep
terminal/issue media plus a declared ring-buffer retention policy. A wait timeout
is not a game loss. This is a controller-owned operation, not a second agent or
orchestrator watcher polling a completion-driven delegate.

Fixtures must cover target movement after purchase, changed geometry/control,
stale/consumed tokens, OCR ambiguity, apparent pixel stability, lost captures,
mid-batch disconnect/unknown delivery and retained partial receipts. Then qualify
one disposable native application before an explicitly scoped game trial. Port
other platforms only through their actual authority/receipt contracts.

## Coach and optimization boundaries

The active controller evaluates game-specific triggers at safe phases. Coaching
is optional declared assistance, not a hidden model escalation. Coach reads an
immutable packet and selected released history; never captures or injects input.
Record configured access even when unused, exact model/effort/service, advice
exposure, latency and rejection. Follow the host's delegation completion policy;
no new scheduler, shell model loop or inferred permission from a saved record.

Cross-run review proposes a policy delta with source/counter evidence, applicability,
expected result, exceptions and a discriminating prospective test. Keep current
phase lessons quarantined unless sequential adaptation was declared. Winning,
completion, round count, wall/active uncertainty and feedback coverage are separate
objectives; quick losses must not appear as strategy improvements.

The existing model-policy family names are ordered roles, not an exhaustive model
catalog. Do not blindly insert a model name into that order or reuse task acceptance
as game win rate. A future routing change should define explicit reviewed role/
eligibility mappings without manufacturing a capability hierarchy or new prices.

## Rollout

Track implementation, offline validation and native/game qualification separately.
Record both repositories' commits plus contract/adapter/knowledge/policy versions
at adoption. Keep current helpers/source stable during live play. Installing a
new implementation and safely refreshing a helper is a separate qualified step;
plans and offline tests alone do not establish live readiness.
