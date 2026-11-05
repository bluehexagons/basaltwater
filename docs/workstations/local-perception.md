# Read-only scene-region perception prototype

BW-GL04A implements `region-mae-v1`, a fixed-region scene/anchor recognizer.
It consumes [pinned observation packets](learning-records.md) and emits evidence,
literal crops and abstentions. It does not capture, read game memory/source,
send input, interpret victory, renew observation authority or select strategy.
Synthetic qualification is implemented; real game-profile acceptance awaits a
consumer-supplied labelled dataset with a frozen split by whole run.

```text
basaltw desktop evidence perception recognize MANIFEST PACKET_ID FRAME_ID --profile PROFILE [--template ID PNG]... [--output NEW_DIRECTORY] [--json]
basaltw desktop evidence perception evaluate DATASET --bundle RUN_ID MANIFEST... --profile PROFILE [--template ID PNG]... [--output NEW_DIRECTORY] [--json]
```

Repeat `--bundle RUN_ID MANIFEST` for each held-out run. Duplicate bindings fail.
These offline commands work without `--native` and never contact a helper.
The Python interface is `desktop.perception.recognize(packet, frame_id, bytes,
profile, templates)`, returning `(result, literal_crop_bytes_by_field)`.
`desktop.record_bundles.read_packet(manifest, packet_id, frame_id)` supplies a
verified packet/frame pair without a live lease. `perception.field_delta(old,
new)` reports changed field evidence; mismatched settings/drift refuse comparison.
Missing/duplicate fields, unsupported versions and absent drift/profile metadata
are rejected rather than reported as unchanged. Returned delta sources are copies.
Unknown values stay null; a field change makes no assertion of semantic progress.

The profile is strict JSON, at most 64 KiB, with exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind`, `detector` | Integer 1, `region-profile`, `region-mae-v1` |
| `id`, `version` | Nonempty bounded text (128 characters), both project owned |
| `conditions` | Exactly known text `game`, `build`, `profile`, `recognizer`, compared to packet conditions |
| `geometry`, `image_origin`, `image_geometry` | Full geometry, crop origin, PNG dimensions; integer pairs, dimensions 1–16384 and origins 0–16384; crop fits full geometry |
| `fields` | One to four field specifications with at most 65536 combined region pixels |

Each field has exactly `name`, `rectangle` ([x,y,width,height] in PNG-local
pixels), `max_error` (finite 0–1), `min_margin` (finite greater than 0, at most 1),
`action_risk` (text describing intended use), and `templates` (one to four).
Field names are unique. Each template has exactly text `id`, text `label`,
SHA-256 `sha256`, and integer `bytes` (1–1 MiB). Template IDs are globally unique;
labels are unique within a field. Templates have the region's exact dimensions,
and all supplied bytes must match their pins. Required thresholds cannot be null
or booleans. General record-contract text/nesting/numeric bounds apply.

For each region, error is mean absolute RGB-channel difference divided by 255.
The closest template must have error at most `max_error` and exceed its nearest
competitor by at least `min_margin`. Ties abstain. A single-template field has
no competitor; its error threshold alone applies. Scores are image differences,
not calibrated probabilities. No alignment search, scaling, OCR, memory access,
model call or package installation occurs. The prototype supports opaque scene
anchors, not numeric text recognition. OCR would add dependencies and a different
acceptance problem without helping this fixed-pixel field class.

The standard-library PNG reader supports noninterlaced 8-bit RGB and opaque RGBA,
checks chunks/CRCs, all five row filters and exact bounded decompression, and
rejects animation, transparency, unsupported critical chunks, extra streams and
truncation. It accepts at most 16 MiB encoded and four Mi pixels decoded.
This deliberately narrower subset consumes native PNG captures when compatible;
an unsupported input fails explicitly. It does not silently convert media.

Result fields retain canonical `name`, `status`, `value`, `sources` along with
reason and candidate scores. Matched fields are `observed`; abstained fields are
`unknown` with null values. Source identity includes packet/state/frame IDs and
SHA-256; settings include the full profile and version, score semantics and
whole-recognition elapsed seconds. Profile condition, origin, PNG geometry or
full geometry drift produces unknown fields and no source regions/crops from
the incompatible profile. It never reuses old coordinates. Corrupt bytes fail.
Only the four canonical field keys should be copied into a new observation;
the recognition receipt remains a separate artifact.

Recognition output pins exact `profile.json`, templates, a canonical single-packet
graph, original PNG and literal unresampled RGB region crops. Re-encoding an
opaque crop preserves its decoded RGB values; it is not an original PNG byte
slice. The receipt includes crop hashes and portable references, written last
to `recognition.json` in a new private bundle. Missing/changed inputs and existing
outputs fail. A write failure retains a partial bundle and explicit error;
never retry into that directory. The parent/output guards and JSON error/exit
conventions match [corpus tools](learning-corpus.md).

A held-out dataset is strict JSON of at most 64 KiB with exactly
`schema_version: 1`, `kind: region-heldout`, text `id`, `profile_sha256`,
`calibration_runs`, `heldout_runs`, and `samples`. Run arrays have at most 32 unique
text IDs each; held-out runs are nonempty and disjoint from calibration. Bind
exactly the held-out runs to verified record bundles. Each sample has exactly
unique text `id`, `run_id`, `packet_id`, `frame_id`, `case`, and `expected` (one
nullable text value per profile field). Field names, including spaces, are exact
object keys rather than split words. There are 1–32 samples with at most
32 MiB combined encoded frames, all from bound packet run IDs. Duplicate frame
bytes fail so repeated images cannot inflate coverage. Every held-out run must
have a sample. Profile bytes must match the frozen hash; templates are already
pinned by that profile. These checks enforce the declared split, not independent
proof of labelling quality or that a human never tuned against held-out frames.

Freeze thresholds and intended action risk before evaluation. Reserve whole runs
when collecting labels. Include normal, unknown, unreadable, hover, animation,
occlusion, ambiguous, scale and layout/version drift cases. `case` is consumer
metadata, not a label inferred by the detector. Missing cases remain unqualified.
Real media and labels stay private; project-specific profiles stay with the
consumer rather than becoming generic game rules in Basaltwater.

Evaluation writes exact dataset/profile/templates, per-sample packet/frame/crops
and `report.json` (completion marker). Per-field and per-case counts retain false
confident predictions (nonnull wrong label), abstentions, correct predictions
including correct unknowns, and whole-recognition elapsed seconds for each sample.
These measurements include validation and PNG/template decoding. No percentile
or runtime acceptance threshold is inferred from a tiny synthetic dataset.
The report limit is 8 MiB; profile/templates, frame copies and crops keep a
maximum 128 MiB output footprint, enforced before each write group. Output requires a new private
directory; input evidence is unchanged.

`qualification: offline-heldout-only`, `game_qualification: not-asserted` and
`input_authority: false` remain explicit even with zero reported errors. A
confident error is reported, not suppressed or automatically excused by overall
accuracy. Real-profile acceptance thresholds and prospective game trials belong
to the consumer. One working anchor does not qualify other fields or inputs.

The [synthetic interoperability fixture](../examples/learning-records/perception.json)
contains a profile and hex-encoded tiny red/blue PNG bytes. Materialize
`profile` as JSON and `templates_hex` with `bytes.fromhex`, then supply those
files through the actual CLI grammar. Tests exercise held-out wrong labels,
unknown scenes, profile drift, ambiguity, PNG failures and the offline CLI.
