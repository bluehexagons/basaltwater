# Storage cleanup reviews

Use **Agents → Agent tools → Review disk cleanup** or the **Review storage
cleanup** task template for a one-time inspection. Both start with measured
local candidates, rank the best opportunities in the visible summary, and
request a follow-up that applies a selected safe cleanup and verifies reclaimed
space. The summary must contain the actual candidates, measurements and retention
decisions, rather than just saying that the review finished. Opening a template or
running the review does not authorize deletion. Existing saved tasks keep their
prompts; create a fresh review to use the updated guidance.

## Host evidence and approved cache operations

New storage templates select `storage_operation: "review"`. Before Codex starts,
the non-root task worker gathers a bounded host inventory and npm, Go and
Podman previews. It supplies timestamped evidence to the sandboxed review and
retains it in the run log. Numeric owners and process activity come from that
observer; they must not be mixed with sandbox observations. Unavailable probes
remain explicit. The host child rejects nested user namespaces and mismatched
proc views rather than treating their incomplete observations as host evidence.
Native probes can create their normal locks/metadata; review never evicts caches.

An inert follow-up can select one fixed operation for **Approve once**:

| `storage_operation` | Approved scope |
| --- | --- |
| `npm-content-cache` | Remove only `~/.npm/_cacache`, after a complete owned local inventory and package-manager activity checks. Preserve `_npx`, configuration, logs and custom cache locations. |
| `go-build-cache` | Run the existing validated native `go clean -cache` helper; retain modules, toolchains and running temporary programs. |
| `podman-images` | Apply the existing age/reference/keep-label policy below to the account's rootless store, including eligible abandoned builders. Approval covers this policy at execution time, rather than a frozen preview's IDs or sizes. |

These tasks execute deterministic code on the host without starting Codex or
interpreting their prompt as commands. They accept no custom paths or argv.
Use `mode: "workspace"`, `workspace: "existing"`, `persistence: "fresh"`,
`interval: "once"`, network/temporary writes/session history off, web search
disabled and no additional writable directories. The runner caps them at one
attempt. Service restarts cannot accompany a cleanup proposal. Known local
administrator-policy conflicts and unresolved operator prerequisites still
block launch. Creating or approving the task explicitly selects this separate
account-level operation; an inspection does not activate it.

Each operation rechecks host ownership and activity immediately before mutation
and reports coverage, status and available bytes. npm checks ordinary package
manager process identities and bounded Node arguments privately; deliberately
renamed package managers are outside that observation boundary. npm content
eviction uses identity-checked isolation of this fixed rebuildable cache, rather
than `npm cache verify` or generic removal of the npm root. This specialized
operation does not inspect unrelated daemons' file descriptors. None of these
snapshots prevents a new build/install starting afterwards: choose a quiet
development window when necessary.

The host child and its native commands share a supervised process group, a
six-minute ceiling within the task's remaining runtime/deadline, and a 128 KiB
evidence limit. Cancellation, expiry or unavailable evidence never claims
successful cleanup or automatically retries an uncertain mutation. Inspect the
retained evidence and current cache/store state first. Native operations consume
zero model tokens. Existing saved tasks default to `none`; deliberately create
a new review or select the host operation in task settings. Browser revisions,
project outputs and other custom cleanups retain their reviewed owner workflows.

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

Both views include `inspection_context`: the observer's numeric UID/GID,
effective IDs, bounded `uid_map_to_parent`/`gid_map_to_parent` entries and
`proc_self_pid_matches`. Ownership values belong to that observer's user
namespace. A map describes its parent namespace, which may itself be nested;
`1000 0 1` does not establish host-root ownership. Do not compare numeric owners
from different CLI/MCP observers without establishing their namespace context.
Unavailable maps remain null with explicit context issues; these do not erase
usable filesystem measurements or change their existing exit-status policy.

If `proc_self_pid_matches` is false, the mounted proc view and the process's
PID namespace disagree. This can explain a `ps` failure such as `lookup self`;
it does not establish that no jobs are active. Even a matching PID does not
prove complete process access. A sandbox can also hide root-owned Go installation
metadata or block rootless Podman operations. Report those checks as unavailable,
not broken installations. Do not change ownership, reinstall tools or relax
cleanup guards on that evidence. Request an authorized check in the cache
owner's host session when the sandbox cannot establish ownership or active use;
another follow-up with unchanged access will repeat the failure.

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
| `npm-content` (`~/.npm`, `~/.npm/_cacache`) | Inspect content-cache metadata and active package managers. `npm cache verify` garbage-collects data and requires write authorization. Approved `npm --cache /exact/cache cache clean --force` evicts downloaded content; preserve configuration and active npx installations. Later installs may need network downloads. |
| `go-build` | Preview `basaltw agent storage clean-go-build --json`; use `--apply` within approved scope. The shared guard distinguishes completed `go run` wrappers from builds. Keep modules and source; later builds recompile. |
| `containers` (rootless Podman) | Use `basaltw agent storage clean-podman --json`; review old unused testing images, including tagged images, and abandoned Buildah builders. Reuse compatible images first; ordinary containers, volumes, mounted/pinned builders and persistent cache mounts are retained. |
| `playwright-browsers` (`ms-playwright`) | Match `.links` package registrations, their `browsers.json` revisions and live browser/MCP references. Keep latest testing browsers and older revisions still required by projects or managed tools. Use owner-native unused-version cleanup only after checking dependencies; sorting revision numbers or modification times is insufficient. |
| `project-toolchain` (`*-toolchain`) | A name-based review hint for project caches such as build/release toolchain pairs. Read project installers and version/checksum locks; separate download archives from installed editors, SDKs, export templates and evidence. Confirm duplicate content by checksum and active use before choosing exact archive removals. Preserve required installed tools and a verified archive copy; installers may download missing archives again. |

These checks travel with Basaltwater rather than recording one VM's sizes,
browser revisions or project release. Recheck current dependencies on each
system. Custom cache locations can still be inspected with `--path`; unrecognized
paths retain generic review guidance. Do not infer disposability from a cache
name, equal sizes or an old timestamp.

The npm label covers the content-cache review, not disposal of every child of
`~/.npm`. In particular, `_npx` can contain installed tools still in use.
For metadata-only reviews use `storage inspect --path /exact/cache/_cacache`;
neither `npm cache verify` nor its description as an offline verification makes
it read-only. npm documents its garbage collection in the
[cache command reference](https://docs.npmjs.com/cli/v11/commands/npm-cache/).
Do not retry an EROFS failure through another integration or assume an unchanged
byte count proves that no cache entry changed.

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
The temporary child's file identity and directory ownership are checked through
its own `/proc/PID/root`, so the managed worker's private temporary mount does
not hide a completed login-session program. Missing/denied process-root access
still defers; worker namespace and service hardening remain in place.

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

## Podman image cleanup

Preview the current user's local rootless Podman store, then apply only after
reviewing the candidate list:

```bash
basaltw agent storage clean-podman --json
basaltw agent storage clean-podman --apply --json
```

Preview is the default; `--dry-run` explicitly requests it. Testing images are
rebuildable cache assets. The policy selects unused images, including tagged
images and intermediate build images, older than seven days. When free
space at the graph root is below the existing dynamic user-cache target,
the age threshold becomes 24 hours. That target is
`min(8 GiB, max(2 GiB, total filesystem size / 5))`. Age means image creation
time, not last use or pull time: a recently used old image may be removed once
its containers are gone. An image with the `io.basaltwater.keep` label (any
value) is excluded; a tag does not pin it. Avoid indefinite pins for test caches.

Abandoned Buildah working containers can retain images and substantial writable
layers after interrupted builds. The same creation-age cutoff applies to these
containers, using Podman's container timestamp rather than the base image's age.
Cleanup matches exact IDs across native Podman and Buildah inventories, checks
that both select the same rootless store, and removes only unmounted, unpinned
Buildah working containers using `buildah rm ID`. Each builder is inspected
again immediately before removal; active build processes defer cleanup. Unknown
external containers and ordinary Podman containers are retained. Mounted
builders, keep-labelled builders, volumes and persistent cache mounts are
retained. No running workload is force-stopped. Builders skipped because the
Buildah CLI is unavailable remain referenced and protected.

An interrupted build can leave stale `MountPoint` metadata. For an authorized
repair, compare native `buildah unshare buildah mount --json` with mount
inventories across the account's live process namespaces. A single namespace's
empty listing does not prove inactivity elsewhere. When these observations are
complete, no actual mount or build process remains, and the exact old builder
is unpinned, clear its stale state with
`buildah unshare buildah unmount FULL_CONTAINER_ID`, then inspect it again and
rerun the cleanup helper. Do not force unmounts, delete storage files or bypass
an incomplete observation. Automatic maintenance retains mounted builders.

After builder cleanup, image references are inventoried again. Cleanup selects
exact full image IDs by creation age, keep label and container references,
preserves ancestors of retained images, and removes children before parents.
It uses native `podman image rm --no-prune --ignore ID…`, without `--force` or
`--all`. A selected image with multiple names is first untagged by exact ID.
Native non-forced removal protects container references at removal time, and
`--no-prune` prevents deletion of parents outside the selection. Broad image
pruning is avoided: Podman 5.4.2's `image prune --all` implicitly clears
persistent cache mounts and recursive pruning does not reapply parent keep
labels. Storage files are never deleted directly.
The command refuses rootful, remote or unsafe stores; active
build/mutation processes and incomplete evidence defer cleanup. A lock
serializes runs. Native probes and mutations share a five-minute command budget.
Timeouts leave the outcome uncertain, so inspect Podman state
before retrying.

On cold startup, including after reboot, a worker with `NoNewPrivs=1` and an
empty capability bounding set can be denied initial user-namespace setup.
The helper recognizes this only after the initial `podman info` fails, when
bounded `/proc/self/status` observations confirm both restrictions and the
diagnostics name the same `newuidmap`/`newgidmap` helper's `uid_map`/`gid_map`
write failure with `Operation not permitted` and failure to set up the namespace.
It reports `deferred_restricted` and fixed issue `namespace_helper_restricted`,
with `cleanup_attempted=false` and `cleanup_executed=false`. No inventory,
pruning or immediate retry follows. Generic permission errors, NNP alone,
unreadable/malformed restriction evidence and unrelated probe failures remain
`deferred_unknown`; a successful restricted probe proceeds normally.

Daily maintenance treats only that exact recognized result with false cleanup
flags as an informational Podman cleanup skip. Other unknown/failed/uncertain
results still fail; logs and failure details expose only allowlisted fixed issue
codes, never raw Podman diagnostics or metadata. The direct CLI exits nonzero
for deferrals, including `deferred_restricted`. A later pass may succeed after
normal Podman use by the same user initializes its namespace, but maintenance
does not perform that initialization or guarantee recovery on its next schedule.
The namespace disappears across reboot. Preserve launcher hardening and the
privileged observer boundary; this skip does not establish a disk failure.

Preview IDs and savings are preflight estimates. Images retained by candidate
builders can become eligible after those builders are removed. Shared layers
affect space reclaimed. Reports distinguish actual filesystem free bytes before and
after from Podman's reported image size. Readonly container inspection and
preflight remain readonly. Daily full user-cache maintenance calls the same
helper automatically; `--agent-storage-only` skips it. Results include retained
counts for recent, referenced, explicitly pinned and dependent images, plus builder
retention reasons. Process observations are snapshots: coordinate a quiet build
window when necessary because a new build can start after a check.

Agents should prefer compatible existing images, use task-specific `--rm`
containers and `podman build --force-rm` to clean intermediate containers even
on build failure, and remove their own finished one-off assets. A crash can still
leave working containers behind. Share immutable images keyed by a versioned
Containerfile and dependency inputs, then recreate them lazily when absent.
Keep source and useful results outside disposable containers. See the
[container lifecycle guidance](../../common/agent_skills/basaltwater-project-development/references/containers.md).

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
A custom cleanup task with `storage_operation: "none"` uses workspace mode, a
private run directory, and only the exact necessary additional write directories.
Fixed host cache operations use the separate settings above. Do not grant the whole home. Protected
agent directories and system paths cannot receive ordinary task write grants;
use managed retention or separately authorized administrator work. The review
should explain such prerequisites rather than send another agent into the same
blocked operation. See [follow-up access](agent-tasks.md#request-a-follow-up-with-different-access).

The approved follow-up must recheck its targets, perform the approved removal,
and report removed paths, failures, and available filesystem bytes before and
after. Its self-contained prompt carries the selected retention and access
constraints; it must not copy the initial review's prohibition on removal.
