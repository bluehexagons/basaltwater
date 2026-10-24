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
basaltw agent storage inspect --summary --json
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

Default roots also cover Basaltwater-owned worktrees and agent recovery backups,
Node installations/download archives, Go modules (including downloaded toolchains),
rootless container storage, Godot export templates, the managed T3 launcher,
and shared browser/development evidence. Each carries owner-specific retention
guidance. Workspaces below `~/.local/share/basaltwater/worktrees` are separate
from T3's `~/.t3/worktrees`; use `agent workspace status` and `workspace remove
--dry-run` for Basaltwater ownership/ancestry checks. Keep T3 cleanup with T3.
Neither a clean worktree nor a merged branch proves ignored evidence disposable.
Use `--path` for custom locations such as a nondefault `NVM_DIR`, `GOMODCACHE`,
`XDG_DATA_HOME` or managed workspace root; inspection does not execute shell
configuration or query tools to discover overrides.

`--summary` is a compact JSON view of the same scan: it omits absent default roots and
detailed per-candidate metrics, keeps up to three of each root's eight ranked
children, and prints shared database-retention guidance once. It retains every
non-absent candidate and every explicitly selected `--path` (including missing
paths), all collection issues, partial/unavailable statuses,
filesystem capacity, scan limits and database file metadata. Omission counts
describe presentation only, not complete filesystem coverage. The default full
JSON is unchanged in detail. Drill into selected `--path` roots without
`--summary` for ownership, age, hardlink and other metadata before proposing
cleanup. Both views exit nonzero when an explicitly selected `--path` is
unavailable. Missing selected paths remain reported as absent; bounded partial
scans and unavailable default roots retain their existing success status unless
a top-level collection issue occurred. Neither view sums overlapping roots into
a reclamation estimate.

Session-state metadata collection continues after a disappearing or unreadable
database/WAL entry and reports incomplete coverage. A missing session root is
silently absent; a failed directory listing remains a reported collection issue.

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

## Shared cache conventions

The inventory labels recognized `~/.cache` roots with `cleanup_convention` and
includes only the referenced checks in `cleanup_conventions`. This applies to
selected roots and already measured largest children; it adds no scans or file
reads. The largest-child list is bounded to eight, so an absent label is not a
complete cache inventory. Partial coverage and skipped paths remain explicit.
Labels identify review guidance, never deletion eligibility or verified disuse.

| Convention | Checks and retention |
| --- | --- |
| `go-build` | Preview `basaltw agent storage clean-go-build --json`; use `--apply` within approved scope. The shared guard distinguishes completed `go run` wrappers from builds. Keep modules and source; later builds recompile. |
| `playwright-browsers` (`ms-playwright`) | Match `.links` package registrations, their `browsers.json` revisions and live browser/MCP references. Keep latest testing browsers and older revisions still required by projects or managed tools. Use owner-native unused-version cleanup only after checking dependencies; sorting revision numbers or modification times is insufficient. |
| `project-toolchain` (`*-toolchain`) | A name-based review hint for project caches such as build/release toolchain pairs. Read project installers and version/checksum locks; separate download archives from installed editors, SDKs, export templates and evidence. Confirm duplicate content by checksum and active use before choosing exact archive removals. Preserve required installed tools and a verified archive copy; installers may download missing archives again. |

These checks travel with Basaltwater rather than recording one VM's sizes,
browser revisions or project release. Recheck current dependencies on each
system. Custom cache locations can still be inspected with `--path`; unrecognized
paths retain generic review guidance. Do not infer disposability from a cache
name, equal sizes or an old timestamp.

## Go build cache cleanup

Run as the non-root cache owner:

```bash
basaltw agent storage clean-go-build --json
basaltw agent storage clean-go-build --apply --json
```

The default previews; `--apply` requires existing cleanup authorization. The
command queries the selected Go installation's `GOCACHE`, validates a local,
owned directory below the current home without symlinks or shared write access,
and measures it with the bounded storage inventory. It invokes exactly native
`go clean -cache`, with the validated cache pinned and configuration hooks and
automatic toolchain downloads disabled. Module downloads, downloaded toolchains,
project source and temporary running programs remain outside this operation.
Daily maintenance uses the same helper with its existing size/space thresholds;
the module-cache policy retains its conservative active-Go guard.

A long-lived `go run` is not necessarily compiling. The shared guard exempts
only a plain invocation with no build flags or application arguments, backed by
a verified installed Go executable and one stable, directly parented native
program under the standard temporary `go-build…/b…/exe/` layout. It examines
children across every wrapper thread. The child's complete argument vector must
contain only its own executable: execution/build hooks receive additional
arguments. This establishes completed program execution, without reconstructing
historic `GOFLAGS`. Other Go commands, compiler/linker/language-server activity,
unrecognized layouts, cache-resident executable children and incomplete process
observations still defer. Only relevant process arguments are inspected privately;
raw arguments and environments are never returned.

This native cache operation does not inspect every unrelated process's file
descriptors, so a protected login daemon does not itself block it. Generic raw
cache/directory removal retains its stronger open-file guards. The Go observer
assumes ordinary tool process identities; deliberately renamed tools or hooks
that rewrite their identity are outside that boundary. Observation is a fresh
snapshot immediately before cleanup, not coordination against new builds
starting afterwards. Arrange a quiet build window when needed:
[concurrent cache cleaning and compilation can fail](https://github.com/golang/go/issues/31948).

JSON reports `preview`, `cleaned`, `absent`, `below_limits`, `unavailable`,
`deferred_active`, `deferred_unknown` or `failed`, along with bounded measurements,
blocking/waiting PIDs and available filesystem bytes before/after an applied
cleanup. The CLI exits zero for preview, successful cleanup or an absent cache;
deferral, unavailable tooling and failures exit nonzero. `cleanup_executed`
records a successful native command even if subsequent verification fails.
Cache bytes and free-space changes can differ because other jobs share the VM.

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

Use Luna workers at medium reasoning and standard service for bounded
classification of selected file lists or redacted log excerpts when delegation
is available. Use low only for trivial deterministic work, and high/xhigh when
concrete complexity warrants it. Set file/byte limits and request compact findings
with paths or offsets. Do not copy bulk logs or private transcripts into worker
or parent context. If no worker interface is exposed, use bounded local summaries
and report uncertainty. The installed VM-triage skill has the same workflow.

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
