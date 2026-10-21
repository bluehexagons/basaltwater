# Storage cleanup reviews

Use **Agents → Agent tools → Review disk cleanup** or the **Review storage
cleanup** task template for a one-time inspection. Both start with measured
local candidates, rank the best opportunities in the visible summary, and
request a follow-up that applies a selected safe cleanup and verifies reclaimed
space. The summary must contain the actual candidates, measurements and retention
decisions, rather than just saying that the review finished. Opening a template or
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
directories, agent logs and runtime versions, project checkouts, task
worktrees, session directories and attachments. The separate `session_state`
section lists only metadata for SQLite/WAL/SHM files directly in `~/.codex` and
`~/.t3/userdata` (up to 1024 directory entries per location, outside the recursive
entry budget). It never opens those databases. Missing paths and permission
failures stay explicit. See [session retention](session-retention.md) before
proposing any session or app-state cleanup; archiving threads is not a disk
reclamation operation.

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

## Inspect project conventions economically

Read each project's `AGENTS.md` and existing cleanup tooling before choosing
targets. `basaltw agent storage project /absolute/repo --json` describes an
optional cleanup policy and measures its roots without running commands. Use
`--policy relative/path.json` for a custom location. Existing layouts and
non-JSON systems are supported through project instructions. The standard
layout is a fallback only; inspection creates nothing. See
[project cleanup conventions](project-cleanup.md) for scripts, agent curation
and preserving selected screenshots/videos as development history.

For a reviewed `generated` rule, preview
`basaltw agent storage clean-generated /absolute/repo --rule RULE_NAME`.
The built-in remover accepts only that rule's disjoint directories and checks
tracked files, ownership, mounts, links, inventory coverage and active use.
Removal requires explicit `--apply` and separately authorized cleanup scope;
inspection never invokes it. Include writes to each target's parent when
preparing a follow-up, because disposal first isolates directories there.

Use cheap Luna workers at high reasoning and standard service for bounded
classification of selected file lists or redacted log excerpts when delegation
is available. Set file/byte limits and request compact findings with paths or
offsets. Do not copy bulk logs or private transcripts into worker or parent
context. If no worker interface is exposed, use bounded local summaries and
report uncertainty. The installed VM-triage skill has the same workflow.

## Choose a follow-up

The review should rank up to five candidates by observed benefit, retention and
rebuild or download cost. For example, a large Go build cache can justify a
native cache cleanup proposal after checking active builds; Playwright browser
downloads need installed-version and active-browser checks. A project directory
total should lead to a focused investigation of generated output, rather than
deletion of the project. Preserve source, Git data, dirty or unmerged worktrees,
credentials, sessions, attachments, backups and active runtime versions.

Resolve bounded exact-child classification during the initial pass when possible.
An ambiguous large build/evidence root should not prevent a smaller proven cache
or generated-output cleanup. Preserve the ambiguous material and select the useful
cleanup that can actually proceed. Do not repeatedly hand off the same inventory.

One coherent cleanup proposal should name exact paths, commands, active-use
checks, retention decisions, verification and the irreversible nature of
deletion. If safety is uncertain, propose a targeted read-only investigation
with a concrete decision to resolve. That investigation must produce an apply-ready
cleanup proposal or name the specific retention decision needed from the operator.
Large totals alone are insufficient.

Select **Approve once**, **Review follow-up task**, or **Block** on the report.
A cleanup task uses workspace mode, a private run directory, and only the exact
necessary additional write directories. Do not grant the whole home. Protected
agent directories and system paths cannot receive ordinary task write grants;
use managed retention or separately authorized administrator work. The review
should explain such prerequisites rather than send another agent into the same
blocked operation. See [follow-up access](agent-tasks.md#request-a-follow-up-with-different-access).

The approved follow-up must recheck its targets, perform the approved removal,
and report removed paths, failures, and available filesystem bytes before and
after. Its self-contained prompt carries the selected retention and access
constraints; it must not copy the initial review's prohibition on removal.
