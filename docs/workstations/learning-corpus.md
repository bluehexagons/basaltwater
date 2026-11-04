# Closed-corpus review, version 1

BW-GL06A adds offline corpus validation and reproducible condition-aware review
inputs. It consumes the existing [version-1 record graphs](learning-records.md)
without changing or migrating their envelope. The canonical corpus validator
is `desktop.learning_records.validate_corpus`; summary/storage APIs are
`desktop.corpus.summarize(document, documents)` (source ID to pinned bytes) and
`create(source, inputs, output=None)` (source ID to input path).

```text
basaltw desktop evidence corpus CORPUS_JSON [--source SOURCE_ID RECORDS_JSON]... [--output NEW_DIRECTORY] [--json]
```

The [synthetic corpus](../examples/learning-records/corpus.json) consumes
[accepted.json](../examples/learning-records/accepted.json) as `source-1`.
Its interrupted window and eventual win are two segments of one start.
Point `--source source-1` at that file, and select a new private output outside
Git. No capture, desktop/helper access, model call, input, source installation
or helper refresh occurs. `--native` is optional and does not alter this command.

The manifest has exactly `schema_version: 1`, `kind: learning-corpus`, text `id`,
UTC `cutoff_at`, `sources` and `segments`. Record-contract text, strict JSON,
nesting/numeric limits and 1 MiB manifest size apply. There are at most 32
sources, 128 segments and 16 MiB declared source bytes, including missing files.

Each source has exactly text `id` (unique), lowercase SHA-256 `sha256`, integer
`bytes` (1–1 MiB), and `availability` (`available` or `missing`). Bind exactly
the available IDs with `--source`; missing evidence is explicitly declared,
never inferred from an unreadable path. A supplied file must be regular,
canonical and hash/size matching, and its record graph must validate. Duplicate
CLI IDs, absent bindings, extra bindings and corrupt/unsupported files fail.
All repeated record IDs across loaded graphs must have identical contents.

Each segment has exactly these fields:

| Field | Contract |
| --- | --- |
| `id`, `source_id`, `packet_id`, `match_id`, `run_id`, `condition_id` | Nonempty text, at most 128 characters; source must be declared |
| `conditions` | Exactly the nullable fields from the observation packet: game/build/environment/profile/recognizer/knowledge/policy/adapter/team/controller/coaching |
| `included`, `reason` | Boolean and nonempty text of at most 4096 characters; a reason is required for both inclusion and exclusion |
| `started_at` | UTC start of the original match, or null for a setup-only failure |
| `cutoff_at` | UTC end of this segment's window, at or before the corpus cutoff and at or after match start |
| `window_outcome`, `eventual_outcome` | Each is win/loss/draw/interrupted/unknown; game-specific mapping belongs to the consumer |
| `eventual_at` | Required UTC for terminal eventual outcome, otherwise null; cannot exceed corpus cutoff or predate match start |
| `setup_failure` | Required reason for null start, otherwise null; setup-only rows have unknown outcomes and null placement |
| `placement` | Null or integer 1–1000000 |
| `rounds`, `coaching_interventions` | Null or integer 0–1000000; null is unknown, zero is observed zero |
| `displayed_timer` | Null or text of at most 128 characters, distinct from host wall time |
| `wall_seconds`, `active_seconds` | Null or finite nonnegative number, maximum 2592000; known active cannot exceed known wall |
| `usage` | Exactly nullable text `scope`, nullable integer `tokens` (0–10^12), nullable finite number `cost` (0–10^9), nullable text `currency` |
| `evidence_availability` | Controller-declared complete/partial/missing; no media verification is implied |

Known usage requires scope; known cost also requires currency. Settings/costs
are never inferred from controller/model IDs. Scoped telemetry is retained per
segment, with known/unknown coverage counts, and is not summed because
continuation windows or usage scopes may overlap. Placement, round count,
displayed timer, wall, active, tokens and costs remain distinct.

Available packet evidence must match the segment's match/run/conditions and
not postdate its cutoff. Missing sources retain declared segment metadata with
effective evidence availability `missing`. Summary `missing_sources` lists all
missing files, including those referenced only by excluded segments.
`media_verification: not-attempted` makes clear that this layer validates pinned
record JSON, not images, costs, scene labels or outcome semantics.

A terminal window outcome must equal the eventual outcome and have a terminal
timestamp at or before the window cutoff. For a nonterminal window, a known
eventual terminal timestamp is at or after the window cutoff. This preserves
original interruption and eventual completion without rewriting old evidence.

Identical repeated segment IDs are deduplicated; different source bindings may
carry identical segments without increasing starts. Other conflicting contents
under one segment ID fail. A condition ID must always map to the same complete
condition object. Included segments sharing a match ID must agree on start UTC
and condition ID; conflicting terminal outcomes, placements or terminal times
fail for review. There is no last-writer-wins rule. Explicit exclusions retain
their full metadata and reasons in the report, outside the outcome denominator.

Each condition group retains all deduplicated segment metadata, setup failures
and one row per started match. The earliest segment cutoff defines the original
window outcome; a known terminal eventual outcome takes precedence over unknown
continuations. Without a known terminal result, the latest segment defines the
eventual interrupted/unknown state. Outcome counts reconcile exactly to starts
for both views. Fractions use all starts, including unknown/interrupted ones;
zero starts yields null fractions. Setup failures are separate and contribute
no starts. Configured coaching remains in conditions, while actual intervention
counts and unknown exposure remain in segment telemetry. No rankings, strategy
interpretation, policy selection or automatic release are performed.

Creation writes a new mode-0700 private directory using the existing QA output
guards. Exact `corpus.json` bytes and available record sources (`source-N.json`,
source-ID sorted) are pinned alongside `summary.json`, written last as the
completion marker. The report retains the corpus SHA-256 and every source pin,
plus portable source filenames. Replaying with these copies reproduces the
report, without overwriting inputs. No personal input paths enter the output.
The maximum input footprint is 17 MiB; the report is limited to 4 MiB, for a
maximum 21 MiB bundle. Privacy review remains necessary before export.

Success returns JSON with artifact path, schema version, group count, missing
sources, `partial: false`, `input_authority: false`, and exit 0. Invalid input
returns `error`, `partial: false`, `input_authority: false`, and exit 1. A write
failure after reserving output returns `partial: true` and `bundle`; retain it
for inspection and use a different output path. Parser errors use exit 2.
Missing evidence declared in the manifest is honest partial coverage, not an
invalid file or proof that a match outcome was observed. Attach summary paths
to existing run checkpoints; do not reset original run clocks.

Consumers retain their manifest hash, source pins and tested repository commits
at adoption. The contract is ready for offline extractors; native/game media
and prospective strategy qualification remain separate consuming-project work.
