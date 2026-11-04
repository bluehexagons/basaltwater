# Offline game-testing records, version 1

BW-GL02A provides private observation, decision and coach evidence for CachyOS
controllers. The storage/validation contract is platform independent; no helper,
capture, model, input, scheduler or game rules are invoked. Native game testing
and adoption by consuming projects require separate qualification.

Use the existing evidence namespace (the optional `--native` selector has no
effect on these offline commands):

```text
basaltw desktop evidence records create SOURCE [--media FRAME_ID PNG]... [--output NEW_DIRECTORY] [--json]
basaltw desktop evidence records validate MANIFEST [--current-state REVISION --at UTC] [--json]
```

`SOURCE` is strict UTF-8 JSON with exactly `schema_version: 1` and `records`,
a nonempty array of at most 128 records. Duplicate object keys, record IDs and
frame IDs fail. All references resolve within this closed graph, independent
of array order. IDs identify records; observation IDs are packet IDs, and frame
IDs identify pinned media, never a helper observation lease. State revisions
are opaque controller strings, not counters. Use null for unknown revisions;
the CLI reserves `unknown` to express an unknown current revision.

Every field listed below is required; extra fields fail. Text is nonempty,
has no control characters, and is limited to 128 Unicode characters unless
specified. A nullable field accepts null or its listed type. UTC values are
ISO-8601 strings with explicit zero UTC offset. Hashes are 64 lowercase hex
SHA-256 characters. Integers exclude booleans. Free semantic JSON values may
contain booleans, but numeric fields never treat them as numbers.

| Kind | Fields beyond `kind` and text `id` |
| --- | --- |
| `observation` | Text `run_id`, `match_id`, `phase`, `clock_scope`; nullable text `round_id`, `state_revision`; UTC `captured_at`; nullable integer `monotonic_ns` (0–2^63−1); `conditions` object; `frames` array; `fields` array |
| `decision` | Text `packet_id`; nullable text `state_revision`, `policy`, `knowledge`; text `action` (4096); arrays `preconditions`, `evidence` (32 each); semantic JSON `alternative`, `opportunity_cost`, `expected_effect`, `observed_effect`; `dispatch` object |
| `coach-request` | Text `packet_id`; nullable text `state_revision`; text `trigger`, `question` (4096 each); hash array `allowed_digests` (32); object `requested_settings`; UTC `deadline_at`; integer `intervention_budget` (0–100) |
| `coach-reply` | Text `packet_id`, `request_id`; nullable text `state_revision`; UTC `received_at`; object `actual_settings`; text `recommendation` (4096); arrays `evidence`, `validity` (32 each) |
| `coach-disposition` | Text `reply_id`; UTC `at`; nullable text `current_state_revision`; enum `validation`, enum `disposition`; text `reason` (4096); semantic JSON `result` |

`conditions` has exactly these nullable text fields: `game`, `build`,
`environment`, `profile`, `recognizer`, `knowledge`, `policy`, `adapter`, `team`,
`controller`, `coaching`. Declare stable condition/release IDs, never infer
unknown settings from a model name. Settings objects retain configured and
actual model/effort/service evidence separately; null values retain unknowns.
Semantic evidence/precondition/result contents are project owned and bounded;
structural validation does not establish their truth or interpret outcomes.

`dispatch` has exactly nullable text `id`, `stage` (one of `not-attempted`,
`attempted`, `completed`, `refused`) and `delivery` (`not-sent`, `sent`, `unknown`).
These are controller reports, not proof of delivery or an instruction to retry.
Unknown effect, cost, alternative or result is null, never an inferred zero.

Each frame has exactly text `id`, hash `sha256`, integer `bytes` (1–16 MiB),
integer `width`, `height` (1–16384, product at most 16 Mi pixels), `origin`
([x,y], integers 0–16384) and `geometry` ([full width,full height], integers
1–16384). The image rectangle must fit the full geometry. At most eight frames
and 32 MiB of media are allowed across the graph. Each supplied PNG's exact
hash, byte count and IHDR dimensions must match; this checks identity/geometry,
not full PNG decoding or scene correctness. No resizing or recapture occurs.

Each field has exactly text `name` (unique within a packet), `status`
(`observed`, `inferred`, `unknown`), semantic JSON `value`, and `sources`
(at most eight objects). Only `unknown` requires a null value; other statuses
require a nonnull value. An observed field requires at least one source.
A source has exactly `frame_id` and `rectangle` ([x,y,width,height] integers,
positive dimensions), in that packet's frame-local pixels. Foreign frames and
regions outside the PNG fail. An inferred field may have no image source.
An empty frame list explicitly permits packets without visual evidence.

Every decision/request/reply revision must equal its packet's revision, including
null. A reply must refer to a request for that same packet. A request deadline
cannot predate capture; receipt cannot predate capture; disposition/evaluation
cannot predate receipt. Offline freshness uses the caller's explicit current
revision and evaluation UTC, never a helper or an implicit local clock.

Advice validation is `late` after the deadline (equality is allowed), otherwise
`unknown-state` if either revision is null, otherwise `stale` for unequal
revisions, otherwise `valid`. Structural invalidity returns `invalid` with an
error, separately from freshness. A disposition records that validation result
and a controller choice of `accepted`, `rejected` or `expired`; acceptance
requires `valid`. Valid advice can still be rejected. Freshness validation
never creates a controller disposition or grants live input authority.

All records are limited to 1 MiB of encoded JSON, nesting depth 12 (root is
depth zero), 128 entries per object/array unless narrowed above, 4096 characters
per semantic string, integers of absolute value at most 2^63−1, and finite
floating point numbers of absolute value at most 2^53−1.
These general limits apply inside settings and semantic values too.

Creation copies the exact source bytes to `records.json`, pins media as
`frame-0.png` onward in declaration order, and writes `manifest.json` last.
The manifest has version 1, kind `learning-record-bundle`, creation UTC/monotonic
stamp, QA clock metadata, a `source` pin and `frames` pins indexed by frame ID.
Each pin has exactly `artifact`, `sha256`, `bytes`. No original input paths
are stored. Validation checks private ownership/modes, canonical paths, exact
pin names/hashes/sizes, source contract and media geometry.

Output is a new mode-0700 directory under an existing private owned parent
outside Git; omitting output creates a private temporary bundle. Files use
mode 0600. Symlinks, parent traversal, nonregular inputs, missing media and
existing output are refused. Reads are bounded and detect size/time changes.
The API never updates a bundle. Owner write access is retained: hashes detect
accidental changes, not malicious replacement of both content and manifest.

Keep requests/replies immutable when recording a disposition: create a new
source graph with unchanged original records and a new linked disposition,
then snapshot into a new bundle. Preserve the original bundle and its hash
as adoption evidence. IDs are unique within a graph; cross-bundle identity
and released history selection remain the controller's responsibility.
Attach the returned manifest path using existing `evidence run checkpoint
--artifact MANIFEST`; this does not replace run clocks.

CLI responses are JSON even without `--json`. Success has `validation: valid`,
`partial: false`, schema version, artifact path, record count and
`input_authority: false`. Validation also reports `freshness: not-evaluated`
without current context, or `evaluated` with per-reply statuses and
`controller_disposition: not-evaluated`. Stale/late advice is a valid historical
record, so does not itself change exit status. Exit 1 has `error`,
`validation: invalid`, `partial` and `input_authority: false`; argparse errors
use exit 2. If creation fails after reserving output, `partial: true` includes
`bundle`; retain it for inspection and choose a different output next time.
A manifest is the completion marker. Never treat a partial directory as valid.

The synthetic [accepted](../examples/learning-records/accepted.json) and
[stale/rejected](../examples/learning-records/rejected.json) graphs demonstrate
all five record kinds and explicit unknown visual state. Both are structurally
valid; the latter refuses advice after a state change. Tests also generate a
tiny synthetic PNG and exercise observed regions without host access.
Consumers should adopt these exact fields and grammar, record schema version,
producer commit and fixture hashes, and preserve both tested repository commits.
Corpus aggregation, recognizers, conditional execution, coach dispatch and live
game qualification remain separate assignments in the
[handoff plan](../plans/game-testing-handoffs/README.md).
