# BW-GL05A: effect-verified local batches and event waits

**Implemented with merged records/perception contracts and mocked acceptance.**
See the [API, CLI and qualification recipe](../../workstations/conditional-execution.md).
Native/game qualification remains pending. Read the
[index](README.md), [GL-05 design](../game-testing-learning.md#gl-05-local-observeactverify-batches),
current observation/input contracts and their tests. Live game adoption is a later
consuming-project assignment, not part of implementation acceptance here.

Own a focused executor/wait layer and tests. Inspect `desktop/client.py`,
observations, mapping, receipts/control state and QA/evidence integration before
editing. Do not weaken existing sequence limits or helper admission. Any shared
CLI/native-helper change needs explicit integration-owner coordination.

## Deliverable

Implement one effect-verified action before composing a bounded plan. A plan names
semantic targets, preconditions, expected effects, allowed branches and finite
input/step/time/storage limits. Reobserve/reidentify before every action, map once
and use the current helper authority. Verify effect after dispatch. Return compact
state deltas plus frame/receipt references for review.

Fail-stop on ambiguity, failed predicates, unsupported scenes, control changes or
unknown delivery. Preserve attempted/completed/refused/not-attempted steps and all
uncertainty. No blind retry, stale target/token reuse, automatic new match, strategy
choice or permission transition. Preserve owner run-completion policy: a bounded
batch returning control does not end the match or reset its clocks.

Add a bounded controller-owned event wait that sends no gameplay input and returns
on a qualified scene predicate, unexpected state, cancellation or deadline. Record
actual capture gaps/cadence and bounded storage disposition. Pixel motion/stability
alone is not semantic progress; wait timeout is not a game loss. No watcher polling
a delegated agent and no independent second desktop controller.

## Acceptance and return

Use mocked backends to cover scene/target movement, geometry/control changes,
expired/consumed tokens, ambiguous recognition, missing post-input captures,
disconnect/unknown delivery, deadlines/cancellation and retained partial receipts.
Verify no later action dispatches after failure. Run affected owning checks.

Provide a disposable application qualification recipe and explicit untested cases;
do not open/control a real desktop as part of this implementation brief. Return
actual APIs/CLI, tests, limits and compatibility/adoption instructions. Installation,
native fixture execution and game recipes require a separate declared qualification.
