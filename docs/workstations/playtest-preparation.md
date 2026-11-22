# Prepare a small controller packet

Use this worked path with the game's required procedures and the installed
desktop skill. The coordinator selects applicable inputs before dispatch;
the controller completes required reading and live qualification before play.
This recipe uses existing tools and grants no desktop permission. See
[game testing](game-testing.md) for clocks, scope and normal cleanup.

## Bound the reading before launch

Give the controller one deliverable, explicit repository roots, exact required
files, model/effort/service, one desktop owner, setup/play/reporting allowances
and a private return destination. Keep optional references separate from required
reading. Follow a reference when its operation is needed or its instructions
require it; a link alone is not a request to recursively read a documentation
tree. Do not replace mandatory instructions with an informal summary.

Start setup accounting before the first read. If the required reading will not
fit the allowance, narrow the assignment before launching. A truncated response
calls for the missing range, not a new launch or abandonment of an otherwise
usable control route. Retain setup failures separately from gameplay starts.

For a fictional Linux run, create a new private directory under an existing
private parent. Do not reuse an earlier run's filenames:

```bash
umask 077
PLAYTEST_EVIDENCE=/absolute/private/new-run
mkdir "$PLAYTEST_EVIDENCE"
mkdir "$PLAYTEST_EVIDENCE/reads"
basaltw desktop --native preflight --repository /absolute/game \
  --instruction /absolute/game/AGENTS.md \
  --instruction /absolute/skills/desktop/SKILL.md \
  --output "$PLAYTEST_EVIDENCE/preflight" --json \
  > "$PLAYTEST_EVIDENCE/preflight-response.json"
```

Inspect its individual checks. Add every other required file to this selection
(at most 16 per preflight). Preflight emits metadata, not the instruction text.
Keep additional preflight manifests if the assignment requires more files.
Omit `--native` for Debian; qualify its actual backend independently.

Retrieve **one page per tool reply**. Choose a smaller page if the outer tool's
budget requires it. The JSON must reach the controller without truncation:

```bash
basaltw desktop instructions /absolute/game/AGENTS.md --characters 2000 --json \
  > "$PLAYTEST_EVIDENCE/reads/001.json"
cat "$PLAYTEST_EVIDENCE/reads/001.json"
```

After reading that page, record its receipt path, digest and exact half-open
Unicode range in the caller-owned acknowledgment ledger. Continue from its
returned end offset, keeping the digest and the chosen page size:

```bash
basaltw desktop instructions /absolute/game/AGENTS.md \
  --start RETURNED_END --characters 2000 --sha256 RETURNED_DIGEST --json \
  > "$PLAYTEST_EVIDENCE/reads/002.json"
cat "$PLAYTEST_EVIDENCE/reads/002.json"
```

Do not run a loop that prints every page into one large reply. A response's
`unread_ranges` covers only that response: after reading pages `[0,2000)` and
`[2000,4000)`, the cumulative coverage is `[0,4000)`, even though the second
response lists the earlier range as unread. Merge acknowledged ranges per
path/digest, counting overlaps once. Changed digests require a new manifest and
review; preserve the earlier history. Saved bytes or an acknowledgment do not
prove delivery through a truncating outer tool or model comprehension. See
[evaluation receipts](desktop-evaluation.md#compact-read-only-preflight).

## Rehearse a small visual observation

Before play, take and inspect one context image using the session's image viewer.
Save the JSON receipt and returned PNG path; do not print PNG/base64 data as text.
Check how large images are presented and whether the viewer has a response or
session image limit. Prefer a readable crop for later local decisions when the
surrounding scene is already understood. Retain enough context to confirm focus,
scene and target; periodically refresh context after changes.

On native CachyOS, choose a rectangle from the inspected full-monitor image:

```bash
basaltw desktop --native screenshot --crop X Y WIDTH HEIGHT \
  --next-frame --compact > "$PLAYTEST_EVIDENCE/crop-observation.json"
```

Open its returned PNG. The receipt retains full monitor `geometry`, PNG
`image_geometry` and `image_origin`. Use `map-point --capture` with the actual
presented dimensions; never reuse full-image coordinates on a crop. Offline
`evidence crop` can prepare a literal evidence derivative but cannot refresh an
observation or authorize input. Keep original images, receipts and crop provenance.
See [observation mapping](desktop-observations.md) and
[literal evidence crops](native-qa-evidence.md).

If the viewer refuses more images, preserve the failed presentation and try a
supported smaller view only when the viewer still accepts it. A smaller PNG
cannot guarantee recovery from a cumulative image limit. OCR may support a text
claim, but cannot replace visual inspection of layout, focus or pointer targets.
If no qualified visual route remains, stop dependent input and report the gap;
do not widen freshness limits or blindly replay an action.

## Return facts before prose

The controller first records outcome/partial state, observed findings with
evidence IDs and uncertainty, actual route/settings, coverage gaps and timing.
Then close the owned game normally, verify its task/process outcome, finalize
and verify recorders, and return their exact private handles and any unresolved
operations. Preserve shared services and the owner's control state.

The coordinator turns that compact handoff into the game project's reports.
Do not require the controller to write a second full report packet or reread
the evidence corpus to restate facts. Keep declared settings separate from
observed settings and leave unavailable telemetry unknown. Missing fields are
specific follow-up questions, not grounds to rerun a completed playtest.
