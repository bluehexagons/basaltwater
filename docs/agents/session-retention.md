# Agent session retention

Use this guide when old agent sessions, logs, or captured media are taking
space. Session data can be both private and the only record of unfinished work;
start with bounded metadata and app-owned inventories.

## Find candidates

Start with the metadata-only host inventory:

```bash
basaltw agent storage inspect --json
basaltw agent storage inspect --path /absolute/path/to/one/relevant/directory --json
```

Use allocated and apparent bytes, age, owner UID, file counts, and largest
children to choose a small set of candidates. These are estimates: age does not
prove disuse, allocated bytes do not equal reclaimable space, and overlapping
parent/child totals must not be added. Report measured metadata when useful,
without inspecting private session database contents. Codex session history may
appear in `~/.codex/thread_history_1.sqlite`; T3 state may use
`~/.t3/userdata/statev2.sqlite` or `state.sqlite`, with SQLite WAL/SHM files.
Treat these as owner-app state, not files to edit or manually remove.

The guarded release and numbered T3 rotation retention in `lib/agent_storage.py`
are already wired into maintenance. They are bounded retention mechanisms,
not a general session-purge interface. `basaltw agent storage inspect` reports
candidates; it does not delete them.

## Classify with the owner app

For each selected candidate, use the owning app's session/task inventory to
check its age, status, owner, and available archive or resume controls. Confirm
whether it is active, scheduled, shared, or a dependency of work that may
resume. A filename or old timestamp alone is not a retention decision.

For Basaltwater tasks, distinguish context modes before proposing cleanup:

- `fresh` starts a new conversation each run; retained session history does
  not make this task resume them automatically.
- `checkpoint` starts a new conversation from saved findings and next steps.
- `conversation` resumes the exact recorded Codex session and retains its
  session binding.
- `reset-context` clears the checkpoint and session binding while retaining
  workspaces and receipts. Deleting a task removes its saved control but keeps
  its context, evidence, and files.

See [persistent agent tasks](agent-tasks.md#choose-context-and-workspace-persistence)
for task lifecycle details. Do not treat reset or task deletion as a disk
cleanup operation.

T3's exposed session tools support archive, unarchive, and settle. They do not
provide a delete operation or promise that archiving reclaims disk space. UI
archive changes discoverability/status; a move within the same filesystem
usually only changes a directory entry. Neither demonstrates freed bytes.
Report an unavailable owner-app removal operation as feedback instead of
substituting raw filesystem deletion, SQLite edits, or internal helpers.

## Preserve useful context and evidence

Before any retention decision, export or checkpoint the selected findings,
then record a short handoff with the project, session/task identifier, relevant
dates, artifact paths, pending work, and next action. Keep only the context
needed to resume or reconstruct the work, and retain the source receipt or
other provenance where available.

Treat screenshots and videos as timeline evidence. Preserve interesting
captures with their project and session/task reference, capture date, nearby
event or checkpoint, and original path. Put selected captures in an owned
archive with a small index so later reviewers can place them on the timeline.
Keep original media when it is the evidence; do not replace it with an
unattributed thumbnail or summary. Avoid copying unrelated private material.

## Approve and act

Prepare a short proposal with the exact owner-app item, observed metadata,
active/resume check, export or handoff path, retention period, expected effect,
and verification. Obtain explicit retention approval before removal. Remove
only through a verified, supported control in the owning application. If that
application exposes no removal operation, stop at archive/handoff and report
that supported deletion is unavailable. Never use `rm`, truncate files, edit
SQLite databases, or invoke internal retention helpers as a cleanup API.

After approved removal, recheck free space on the affected filesystem. Report
the measured before/after free space separately from item sizes; archive status
and same-filesystem moves do not establish reclaimed capacity.

## Project cleanup metadata

Inspect optional project cleanup metadata with:

```bash
basaltw agent storage project /absolute/repo --json
```

It reads `/absolute/repo/.basaltwater/cleanup.json` (or a custom path selected
by `--policy relative/path.json`) and describes configured
cleanup scripts as preview/apply argv, plus any agent archive instructions.
It is an inventory and proposal aid: it never executes those commands. Review
the target, preview, active-use checks, retention choice, and expected effect;
execution remains a separate explicitly approved action. Do not infer that a
configured apply command is safe merely because the inventory can display it.

Honor existing project layouts and cleanup systems first. For projects without
one, the inspector suggests a standard layout separating generated output,
review captures and curated archives; it creates nothing. See
[project cleanup conventions](project-cleanup.md) for that fallback and the
archive provenance workflow.

See [storage cleanup reviews](storage-cleanup.md) for bounded filesystem
inventory and follow-up review guidance.
