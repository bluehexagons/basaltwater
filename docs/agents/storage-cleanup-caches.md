# Clean up Go and Podman caches

[Topic index](storage-cleanup.md)

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
This includes Podman's quoted `invalid internal status` error chain only when
it contains the same matching helper denial. The suggested `podman system migrate`
command is diagnostic text; maintenance does not run it or reset the store.
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
