# Session and storage cleanup review

Read this reference when the task is to inspect or clean old agent sessions,
logs, screenshots, videos, or project outputs. Start with
[`basaltw agent storage inspect`](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/storage-cleanup.md)
for metadata-only size, age, and owner evidence, then read the full
[session retention workflow](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/session-retention.md).

## Bounded classification

Use the inventory's `cleanup_convention` labels and referenced checks for Go,
Playwright and project toolchain caches. Read the
[shared cache conventions](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/storage-cleanup.md#shared-cache-conventions)
when relevant. Keep current testing browsers and older revisions required by
installed projects or managed tooling. Toolchain archives need installer/lock
and checksum checks; preserve required installed tools and export templates.

Classify only a selected, bounded file list or a few relevant log excerpts.
Prefer metadata, counters, and app-owned inventories before content. Where
delegation is supported, use a low-cost Luna model at high reasoning and
standard service for bounded classification; use xhigh for an ambiguity that justifies
it. Medium is the normal reasoning floor. Return a tiny ranked finding list
with evidence offsets or paths, confidence, and the next owner-app check.

Do not send bulk logs, transcripts, base64, private database contents, or
unredacted session material to a model. Redact selected excerpts before
classification. If an unattended runner has no delegation capability, use
deterministic local summaries and report what remains uncertain instead of
dumping the source material into the task prompt or report.

## Retention decision

Check active, scheduled, shared, and resume dependencies in the owner app.
Export or checkpoint useful context, and preserve selected screenshots/videos
with session/task provenance and an archive index for timeline use. Obtain
explicit retention approval before removal, and use only a verified supported
owner-app removal control. T3 archive/unarchive/settle controls do not include
deletion or promise space reclamation. Do not remove or edit session files or
SQLite state directly, and do not call internal helpers as a cleanup API.

The project inventory command,
`basaltw agent storage project /absolute/repo --json`, reads
`.basaltwater/cleanup.json` (or `--policy relative/path.json`) and describes
preview/apply argv and agent archive instructions. It never executes commands.
Honor existing project tooling and layouts first; use the standard generated,
review and archive layout only if the project lacks suitable conventions.
Inspection creates nothing. See [project cleanup](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/project-cleanup.md)
for policy and archive details. Treat inspection as an inventory aid, not
approval or execution.

When cleanup is the requested outcome, resolve bounded classification in the
current pass and prepare an exact cleanup for approval. Preserve ambiguous mixed
build/evidence roots and consider smaller proven disposable targets. Put ranked
paths, measured bytes, retention decisions and the next action in the visible
summary. An investigation follow-up must end with an apply-ready proposal or a
specific missing operator decision. Once cleanup is approved, recheck targets,
apply it and report available space before/after; do not inherit the initial
review's no-removal restriction into the approved action.

A reviewed `generated` rule can use the shared preview command
`basaltw agent storage clean-generated /absolute/repo --rule RULE_NAME`.
It checks the exact declared directories without running project scripts;
removal needs explicit `--apply` within authorized scope. Grant follow-up writes
to the selected directories and their parents for guarded isolation, not the
whole home. Stop project jobs first. Script and agent rules keep their own
reviewed workflows; a generated rule cannot overlap protected or curated paths.
