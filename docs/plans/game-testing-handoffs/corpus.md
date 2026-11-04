# BW-GL06A: offline corpus validation and grouped summaries

**Implemented offline on the merged BW-GL02A contract.** See the
[corpus API/CLI](../../workstations/learning-corpus.md). The original scope follows:
implement the generic
closed-corpus layer under the [architecture plan](../game-testing-learning.md).
Read the [handoff index](README.md) and published contract/fixtures first.

Own focused corpus/summary modules, tests and documentation; coordinate minimal
CLI/suite edits with the integration owner. Reuse record validators and private
artifact handling. Do not repurpose agent-model accepted/reworked/failed outcomes.
Own the closed-corpus manifest extension in the canonical contract; version its
fields and fixtures there rather than defining a parallel record vocabulary.

## Deliverable

- Validate a bounded corpus manifest with cutoff, pinned record hashes, inclusion/
  exclusion reasons and explicit condition IDs. Missing evidence stays visible;
  unsupported/corrupt records fail with actionable diagnostics.
- Deduplicate segments by match ID. Keep original-window interruption separate
  from eventual placement/outcome. Identical duplicate segments cannot inflate
  starts; conflicting starts or terminal outcomes require review, not last-wins.
- Report setup failures separately; reconcile starts against win/loss/draw/
  interrupted/unknown outcomes. Zero starts produce null/not-applicable fractions.
- Group by declared build/environment/knowledge/policy/adapter/team/controller
  conditions, retaining unknown fields. Report configured coaching and actual
  intervention exposure separately. Never group only by model name.
- Keep placement, rounds, displayed timer, elapsed wall and scoped active/usage
  observations distinct. Unknown costs are not free; quick losses are not
  strategy improvements. Produce reproducible review inputs, not rankings or
  automatic policy release.

## Acceptance and handoff

Synthetic fixtures cover setup-only history, same-match continuations, identical
and conflicting duplicates, mixed conditions, unknown telemetry, changed hashes,
missing evidence and bounds. Test stable grouping/denominators, immutable inputs
and honest partial coverage. No image interpretation, desktop or model calls.
Run focused checks and return contract version, fixture/report examples, exact
commands/results and integration notes for the consuming game's corpus extractor.
Real strategy interpretation and prospective policy evaluation remain project-owned.
