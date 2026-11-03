# Desktop evaluation receipts

These tools address instruction delivery, targeting and evidence friction in
bounded desktop evaluations. They do not play a game, select strategy, prove
focus or relax human control. See [observations](desktop-observations.md) for
frame binding and input failure stages, and [game testing](game-testing.md) for
qualification, original clocks and normal process cleanup.

## Compact read-only preflight

```bash
basaltw desktop --native preflight --repository /absolute/project \
  --instruction /absolute/skills/desktop/SKILL.md \
  --instruction /absolute/project/AGENTS.md --output /absolute/private/preflight --json
basaltw desktop instructions /absolute/skills/desktop/SKILL.md --json
basaltw desktop instructions /absolute/skills/desktop/SKILL.md \
  --start 4000 --sha256 RETURNED_DIGEST --json
```

Omit `--native` on Debian. Preflight uses the existing manifest, installation
context, skill comparison and desktop status facilities. It performs no capture,
input, start, repair or permission transition. Full selected observations stay in
private `preflight.json`; checks individually report `observed`, `unavailable`,
`mismatched` or `not-attempted`. There is no aggregate readiness success. An
observed helper state of `stopped` or control state of `human-override` is an
observation, not permission to proceed. Inspect capture freshness separately.

Only clean, comparable source evidence can report an observed source match.
Missing worker startup evidence and stopped-worker receipts remain unavailable;
the current on-disk HEAD cannot substitute for startup provenance. Matching
startup metadata is not immutable loaded-code attestation. Delivered skill
comparisons expose unknown counts even alongside mismatches. Session-loaded
skill identity and model comprehension remain unverified.

Select only relevant required instruction files (at most 16). Preflight lists
their digests and complete unread character ranges without flooding the context.
`instructions` returns at most 4000 Unicode characters, the exact half-open
range, remaining ranges and a continuation argv bound to the digest. Follow all
needed ranges and keep a caller-owned checklist; each response describes only
its own emitted range. File changes refuse continuation with the old digest.
Referenced files require their own reads. These receipts prove bytes emitted by
the CLI, not delivery through an outer tool that truncates output, image access,
instruction understanding or compliance. Retrieve omitted ranges explicitly.

## Compatibility and qualification

Existing offline `map-point` and journal version 1 remain supported. New optional
receipt fields do not reconstruct old dispatch stages or missing hashes. Old
journals without a comparable boot clock remain unsupported for timeline joins.
Updated native helper behavior requires a normal safe helper restart after
installation; these development checks perform no restart or deployment.

Fixture tests cover coordinate transforms/margins, changed frame context,
pre-input refusal, ambiguous transport, post-input capture failure, missing and
image-less evidence, bounded Unicode instruction reads and explicit preflight
gaps. They do not constitute a native CachyOS, Windows or game smoke test.
Windows still uses its native CLI contracts; these Linux CLI additions do not
implement Windows input-stage or post-input-observation parity. Optional gamepad,
audio and video qualification remains separate.
