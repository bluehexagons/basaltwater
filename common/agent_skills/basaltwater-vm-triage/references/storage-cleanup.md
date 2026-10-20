# Session and storage cleanup review

Read this reference when the task is to inspect or clean old agent sessions,
logs, screenshots, videos, or project outputs. Start with
[`basaltw agent storage inspect`](../../../../docs/agents/storage-cleanup.md)
for metadata-only size, age, and owner evidence, then read the full
[session retention workflow](../../../../docs/agents/session-retention.md).

## Bounded classification

Classify only a selected, bounded file list or a few relevant log excerpts.
Prefer metadata, counters, and app-owned inventories before content. Where
delegation is supported, use a low-cost Luna model at high reasoning for this
bounded classification; use xhigh only for a specific ambiguity that justifies
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

The planned project inventory command,
`basaltw agent storage project /absolute/repo --json`, reads
`.basaltwater/cleanup.json` and describes preview/apply argv and agent archive
instructions. It never executes commands. Treat it as an inventory aid, not
approval or execution.
