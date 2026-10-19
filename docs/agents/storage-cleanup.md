# Storage cleanup reviews

Use **Agents → Agent tools → Review disk cleanup** or the **Review storage
cleanup** task template for a one-time inspection. Both start with measured
local candidates, rank the best opportunities in the visible summary, and
request one useful follow-up when evidence supports it. Opening a template or
running the review does not authorize deletion. Existing saved tasks keep their
prompts; create a fresh review to use the updated guidance.

## Gather evidence

Run as the account whose caches and projects need inspection:

```bash
basaltw agent storage inspect --json
basaltw agent storage inspect --path /home/agent/.cache/go-build --json
```

The inventory uses filesystem metadata: allocated and apparent bytes, file
counts, owner UID, entries with other owners, bytes with modification times
older than 30 days, newest file timestamp, and largest immediate subdirectories.
It never reads file contents, deletes data, runs package managers or writes a
report. Fixed candidates include tool caches, npm/pnpm stores, Trash, temporary
directories, agent logs and runtime versions, project checkouts and task
worktrees. Missing paths and permission failures stay explicit.

The default budget is 250000 entries across all candidates, up to 50000 per
default root, 20 seconds cooperatively checked between entries, and depth 24.
Use `--path` up to eight times to focus a later scan; selected paths share the
full entry budget. `--max-entries` accepts 1–2000000 and `--timeout` accepts
1–60 seconds. Slow kernel I/O can exceed the cooperative time budget. Symlinks
and their ancestors are not followed; nested mounts are skipped, including bind
mounts. Remote or unknown filesystem types are not traversed. If mount metadata
is unavailable, the scan stops without guessing locality.

Partial sizes are lower bounds. Age does not prove disuse, and allocated bytes
do not guarantee space recovered by deletion. Child sizes are already included
in their parent. Hardlinks are counted once per root; overlapping requests and
shared/reflink extents must not be added as independent savings. Compare free
space on the affected filesystem before and after an approved cleanup.

## Choose a follow-up

The review should rank up to five candidates by observed benefit, retention and
rebuild or download cost. For example, a large Go build cache can justify a
native cache cleanup proposal after checking active builds; Playwright browser
downloads need installed-version and active-browser checks. A project directory
total should lead to a focused investigation of generated output, rather than
deletion of the project. Preserve source, Git data, dirty or unmerged worktrees,
credentials, sessions, attachments, backups and active runtime versions.

One coherent cleanup proposal should name exact paths, commands, active-use
checks, retention decisions, verification and the irreversible nature of
deletion. If safety is uncertain, propose a targeted read-only investigation
with a concrete decision to resolve. Large totals alone are insufficient.

Select **Approve once**, **Review follow-up task**, or **Block** on the report.
A cleanup task uses workspace mode, a private run directory, and only the exact
necessary additional write directories. Do not grant the whole home. Protected
agent directories and system paths cannot receive ordinary task write grants;
use managed retention or separately authorized administrator work. The review
should explain such prerequisites rather than send another agent into the same
blocked operation. See [follow-up access](agent-tasks.md#request-a-follow-up-with-different-access).
