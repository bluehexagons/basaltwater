# Collect storage cleanup evidence

[Topic index](storage-cleanup.md)

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
| `podman-images` | Apply the [Podman cleanup policy](storage-cleanup-caches.md#podman-image-cleanup) for age, references, and keep labels to the account's rootless store, including eligible abandoned builders. Approval covers this policy at execution time, rather than a frozen preview's IDs or sizes. |

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
