# BW-GL04A: read-only local perception prototype

**Synthetic work can start now.** Real game-profile acceptance depends on the
consuming project's GT-GL04A dataset and frozen split. Read the
[index](README.md) and [GL-04 design](../game-testing-learning.md#gl-04-qualify-small-local-recognizers).
Adopt BW-GL02A's packet interface before merging integration; do not fork its schema.

Own a narrow offline detector interface/implementation, tests and docs. Keep
game-specific templates/regions/labels in the consuming project or private profile
bundle. Coordinate dependencies and suite registration with the integration owner.

## Deliverable and boundaries

Start with one field class: a stable scene/anchor or a bounded numeric region.
Compare a simple template/region approach with OCR only where needed; justify any
optional dependency with actual requirements and measurements. No package install
into the human environment, fine-tuning service, game-memory/source access or input.

Emit field value, region, source frame/hash, detector/profile version, settings,
elapsed processing time, unknown/abstained status and score semantics. Scores are
not calibrated probabilities by default. Validate geometry/profile compatibility
and flag drift. An offline result is never an observation lease or a verified win.

Qualify with synthetic fixtures first, then provided labelled frames. Keep held-out
runs untouched during tuning and report per-field false confident detections,
abstentions and latency. Include unreadable/unknown scenes, hover/animation,
occlusion, scale/layout changes and ambiguous matches. Thresholds must be selected
before acceptance and tied to intended action risk. No claim of broad support from
one working button. Keep real media/results local and synthetic examples in Git.

Return the supported field/profile contract, exact dependencies/settings, tests,
measurement method/results and failure limits. If real data is unavailable, deliver
the synthetic prototype honestly; do not claim game qualification. Execution and
live capture remain outside this assignment.
