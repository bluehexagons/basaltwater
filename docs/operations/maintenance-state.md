# Maintenance cleanup and recovery

[Documentation index](maintenance.md)

## Cleanup and State Safety

An explicit setup rerun runs the same bounded system cleanup and user-cache
cleanup jobs once, after the selected installation and reconciliation steps.
This gives an infrequently started VM a maintenance opportunity without
waiting for its next timer trigger. Successful cleanup stays compact in setup
output; failures remain visible and the persistent timer retries them later.
Dry-run setup only shows the planned step and does not execute cleanup.

`cleanup-maintenance` removes disposable APT caches, rotates journals before
enforcing both the `100M` size ceiling and a 30-day age ceiling, invokes the
system logrotate policy, removes recognized crash-report files older than 30
days, and removes only exact basaltwater-owned temporary artifact names older
than seven days in `/tmp` and `/var/tmp`.

Cleanup commands use the shared process-group streaming supervisor with a
ten-minute deadline and a combined 256 KiB stdout/stderr capture limit, while
individual inventory checks can use shorter deadlines. Timeout, output overflow
or interruption terminates the owned group, including pipe-holding descendants
after the leader exits. Group cleanup can add two five-second grace periods;
descendants that create their own sessions require the managed service's
control-group stop semantics. Failed commands are reported, never immediately
replayed to recover output. The maintenance-review wrapper separately bounds
its protocol to 16 KiB and its existing model runtime plus 30 seconds; it keeps
failed/interrupted attempts charged and omits raw worker errors.

The cleanup job also runs noninteractive `apt-get autoremove --purge` on APT
hosts other than Proxmox. On those hosts this removes packages APT has marked
as unused, including superseded kernels, while APT's kernel-retention policy
protects kernels it considers required. It deliberately retains configuration
remnants for packages that were already removed: blanket residual purges can
run maintainer hooks for service names now owned by installed replacement
packages. The APT phase holds the local setup/maintenance lock and first audits
`dpkg` state; incomplete packages prevent its package cleanup without an
automatic repair. When the lock is busy, including setup's child cleanup run,
the job defers APT and continues non-package cleanup and capacity checks.
On physical machines, VMs, and Proxmox hosts, cleanup returns unused blocks after
deletion when discard is supported. It defers to an active native `fstrim.timer`
instead of running a duplicate trim; containers skip this host-level operation.

Before autoremove, kernel-capable non-container hosts other than Proxmox return
obsolete manual kernel selections to automatic APT management. This covers
versioned Debian/Ubuntu kernel images matching the running flavour. Only versions
older than the running kernel qualify; the newest older image is also excluded
from these metadata changes as a fallback.
APT holds and `NeverAutoRemove` rules remain respected. Default-kernel and
helper packages, unversioned Debian/Ubuntu tracking metapackages, other flavours,
and unknown custom kernel names are left alone. APT determines actual removals
from its dependency and retention rules; Basaltwater never deletes boot images
directly.

Proxmox has automatic kernel-only cleanup instead of broad autoremove. It
selects obsolete installed kernel images and old series metapackages, whether
manually or automatically installed. It preserves the running kernel, all newer
kernels, the newest older image as a fallback, package holds, and every APT
`NeverAutoRemove` protection, including Proxmox's generated boot-selection rules.
It also reads the current native boot list to preserve manual selections and
permanent or next-boot pins even before those APT rules are refreshed.
Default-kernel and helper packages are never selected. The job requires a
supported healthy node under the same local maintenance lock used by updates;
HA, Ceph, core package holds, and failing health checks defer removal to operators.
It simulates an explicit kernel purge and refuses any dependency removal,
installation, or upgrade outside the selected kernels. Before dpkg runs, an
APT hook rechecks both the actual package actions and kernel-retention policy
under APT's locks. It leaves kernel APT marks and explicit boot-tool selections
alone; standard kernel package hooks maintain the boot files.

Rerun setup to deploy this behavior. Setup's child cleanup defers the APT phase
while setup owns the maintenance lock; the weekly timer handles later runs.
Preview the manual selections on other hosts without changing anything:

```bash
cd /opt/basaltwater
sudo /usr/bin/python3 -m lib.kernel_cleanup
```

On Proxmox, include automatic packages to preview the kernel-only removal
candidates:

```bash
sudo /usr/bin/python3 -m lib.kernel_cleanup --all
```

After setup completes, run `sudo systemctl start cleanup-maintenance.service`
to perform cleanup immediately instead of waiting for the weekly timer. The
same safety checks apply.

To deliberately retain an additional obsolete image, use `apt-mark hold PACKAGE`
(which also blocks package upgrades), or the platform's kernel retention rules.
On hosts with automated kernel cleanup, a manual-install flag alone no longer
reserves an obsolete kernel. Failed inventory, retention inspection, package
simulation, or metadata updates stop package removal and are reported through
the cleanup job's failure notification.

`user-cache-maintenance` starts a root observer and a permanently demoted worker
for the configured non-root account, daily after the scheduled update windows.
Its fixed command is `/usr/bin/python3 -I
/opt/basaltwater/common/service_tools/user_cache_launcher.py --user USER`.
Root setup invokes the same launcher directly, including setup's
`--agent-storage-only` pass. The launcher validates the target account and uses
`setpriv` to clear supplementary groups and capabilities and enable
`no-new-privs` before starting the worker. All cache inspection and deletion
remain unprivileged.

Full user-cache maintenance also applies the rootless Podman image policy
described in [storage cleanup](../agents/storage-cleanup.md#podman-image-cleanup).
On first setup or after reboot, the restricted worker may be unable to initialize
Podman's user namespace. Only an initial `podman info` failure with matching
`newuidmap`/`newgidmap` map-write `Operation not permitted` and namespace-helper
diagnostics, together with a bounded kernel observation of `NoNewPrivs=1` and an
empty capability bounding set, returns `deferred_restricted` with fixed issue
`namespace_helper_restricted`. Maintenance logs this as informational and
explicitly says Podman cleanup was skipped. Both cleanup flags remain false;
there is no inventory, removal or immediate retry after that failed probe.
Other unknown or failed Podman observations still fail maintenance. Diagnostic
logs and failure details include only allowlisted issue codes, without raw vendor
output or metadata. The direct cleanup CLI still exits nonzero for every deferral.

A later scheduled pass may succeed after normal Podman use by the same account
has initialized its user namespace. This is conditional: initialization is not
performed by maintenance, and the namespace disappears across reboot. Repeating
the same restricted pass alone does not guarantee recovery. Successful restricted
probes proceed through the normal cleanup checks. Keep launcher restrictions and
the privileged observer's fixed protocol intact.

The worker inherits a private observer socket. The privileged parent reads only
fixed `/proc` executable metadata for that account, freshly collected for each
inventory and disposal check; worker requests cannot select paths, PIDs, or
UIDs. This lets release retention inspect protected account daemons without
weakening ptrace permissions or trusting a process-name (`comm`) allowlist.
An unavailable or incomplete observation still defers removal.

The worker inventories tool-reported
cache paths before acting. Tool commands receive the account's home-scoped
environment without loading interactive login profiles, and apply these
bounded policies:

- npm runs its supported verification and garbage collection, then uses a
  forced clean if the cache still exceeds 2 GiB or disk space is low. Rebuildable
  `_npx` workspaces are removed when they exceed 1 GiB or have had
  no activity for 30 days, and only while npm and npx are idle;
- pip purges above 2 GiB or under disk pressure. uv runs its supported prune
  operation. Go cleans build and module caches above 2 GiB and 5 GiB
  respectively, or under disk pressure;
- OpenCode and Codex cleanup is restricted to rebuildable cache directories.
  The OpenCode cache limit is 2 GiB and the Codex cache limit is 1 GiB; either
  cache may also be removed after 90 days without activity. Codex temporary
  entries older than seven days are removed individually. Standalone Codex
  installations retain the current release, the newest prior release for
  rollback, the release selected by `~/.local/bin/codex` (which can differ from
  vendor `current` after a rollback), and every release currently executing.
  An existing launcher that cannot be verified prevents removal; a missing
  launcher selects no additional release. Older releases are removed
  only after their vendor manifest, entrypoint, ownership, and directory layout
  have been validated; unfamiliar entries are left unchanged.
  When extras could be removed, an unreadable process executable or incomplete
  account process inventory defers removal. This is an informational safety skip,
  not a setup or scheduled-job failure: all unremoved candidates are retained
  for retry, and the log identifies the blocking PID when available. Protected
  account daemons can prevent inspection in the direct-user fallback; the
  managed root observer supplies the bounded executable inventory for release
  retention. Do not weaken process permissions or infer inactivity from names.
  Release and backup inventory logs use `known_active_count` for confirmed
  running matches and `cleanup_deferred` for safety skips. Zero confirmed
  matches does not establish inactivity when inspection was skipped or
  incomplete. Direct-user retries can keep retaining extra snapshots while a
  long-lived protected daemon remains uninspectable.
  Current and rollback releases alone need no process scan. Process activity
  and launcher selection are checked again before each removal; a failed check
  preserves remaining candidates for a later retry. Failure during process
  directory enumeration is also an informational visibility deferral.
  The host doctor checks this retention policy when more than two releases
  remain: active releases and rollback copies do not trigger an excess-release
  warning. Unused extras, unfamiliar entries, or failed inspection still warn;
  the doctor never removes files.
- Codex package snapshots under `~/.local/state/basaltwater/agent-backups`
  are reconciled during daily maintenance and setup's agent-storage pass. This
  retries obsolete snapshots retained by an update while processes were running.
  Cleanup preserves the rollback executable recorded in `agent-tools.json`,
  selected launchers, running snapshots, and unfamiliar entries. It acquires
  the updater's existing `agent-update.lock` with a nonblocking exclusive flock
  throughout inspection and disposal; it never creates or replaces that lock.
  A busy update lock or incomplete process inventory when extra snapshots exist
  produces the same informational deferral, preserving snapshots for retry.
  Missing or unsafe locks, missing/corrupt/unfinished update state, unverifiable
  rollback copies, and filesystem or disposal errors still report maintenance
  failures. Readiness inspections and update records continue to expose
  incomplete cleanup; a successful maintenance pass does not mean every
  snapshot could be inspected or removed.
  A verified rollback snapshot alone requires no process scan because
  nothing can be deleted. Dry runs use the same
  checks and report eligible snapshot counts without writing files. The legacy
  `codex.previous` recovery executable is retained: migration records do not
  establish that its bytes duplicate a retained validated package.
- T3 Code numbered provider and trace log rotations are retained up to 256 MiB
  and 14 days. Current logs, terminal logs, non-numbered files, and symbolic
  links are never selected by this policy. Agent setup reruns apply both the
  Codex release policy and T3 rotation policy immediately, in addition to the
  daily maintenance job. Setup starts the root launcher directly; its demoted
  worker performs reconciliation as the target account.
- T3 versioned application runtimes retain the active version, the immediately
  preceding installed version, all newer candidates, and versions referenced
  by service update/recovery state or running processes. Other validated older
  runtimes qualify after seven days without modifications. Pending updates,
  unreadable or unknown state, and changes to state during cleanup prevent
  removal. Staging directories and the legacy installation are left alone.
- Recognized Linux Electron download ZIPs under `~/.cache/electron` expire
  after 30 days, or after one day under disk pressure. Installed Electron apps,
  browser profiles, and other archives are not selected.

Disk pressure means the cache's own filesystem has less than 20% available,
with that free-space target bounded between 2 and 8 GiB. Reserved filesystem
blocks do not count as available. Under pressure, npm, pip, Go, npx, and the
allowlisted agent caches can be evicted below their normal size limits, but
caches smaller than 64 MiB are retained unless their ordinary age/size policy
selects them. Cleanup rechecks space before each eviction, so later caches
remain available once the target is met. Agent retention and disposable
Electron downloads run before package-cache eviction; Go module downloads
come after build-cache cleanup. Downloads and subsequent builds can be slower
after eviction.

Logs show cache inventories, active-use deferrals, selected removals, and
measured file-byte reductions for npm, pip, and Go evictions. A dry run reports
eligibility at the current free-space level; it cannot predict which later
evictions a real run would avoid after recovering space. Cleanup cannot
guarantee free space when active tools or persistent project data occupy it.
On a 32 GiB desktop, keep only a few small projects locally and check
`df -h / "$HOME"` before a large build or update. See the
[hardware sizing tiers](../../README.md#hardware-sizing) for larger workloads.

Cache eviction is deferred while a matching tool or referencing process is
running, including processes that reference the cache only through an open file.
Descriptor inspection is bounded; unavailable coverage, including protected
account processes, defers broader cache maintenance. For the default Electron,
pip, and uv cache paths, the managed launcher also checks protected account
processes. It returns only the labels of caches with an open file, working
directory, or executable inside them. Known references, scan failures, and
custom paths with incomplete coverage still defer deletion.

For npm's default cache, the job can still evict just `~/.npm/_cacache` under
disk pressure or above its size limit. It uses the same owned local inventory, package-manager activity
checks and identity-checked disposal as the
[one-time npm cleanup task](../agents/storage-cleanup.md#host-evidence-and-approved-cache-operations).
This path preserves npx workspaces, npm logs and configuration, and custom
cache locations. A known cache reference or active package manager defers
eviction. Symbolic links and paths outside the configured user's home are
never followed or removed. OpenCode data under `.local/share/opencode` and Codex sessions,
memories, credentials, plugins, and unrecognized package layouts are persistent
state and are not cleanup targets. Root-only setups retain system cleanup but
intentionally skip the user-cache timer.

Standalone Codex release, T3 runtime, numbered T3 log rotation, Electron download,
generic rebuildable-cache, stale-tool-temp, named Basaltwater-temp and crash-file
disposal rechecks device/inode, ownership, type and modification time immediately
before acting, along with each policy's state and active-use checks. T3 runtime
disposal rechecks service state and updater/process activity; numbered log
rotations retain their age/size policy without a process scan.
Parent directories are opened without
following links. Selected entries move into a private `0700` sibling disposal
directory before deletion, so a recreated original pathname is preserved. Dry
runs validate without moving or removing data. A changed entry is refused; a
race during isolation or failed/interrupted deletion retains remaining data
privately, with the recovery location in local maintenance logs. Disposal
directories are excluded from automatic retention, including stale-tool-temp
scans regardless of age, and require explicit recovery;
inspect their owner/identity and any recreated original before restoring data.
Never overwrite a new original path or treat a retained disposal as proof of
successful cleanup. Project reports, captures and SDK/image caches do not become
cleanup candidates because of their age.

On CachyOS, setup's pacman/paccache inventory uses the same bounded capture
owner with a 1 MiB output ceiling per query. An incomplete, timed-out or oversized
inventory stops before deletion. Its package transaction, file identity and
installed-version checks remain separate from generic user-cache disposal.

Preview the user job without changing files:

```bash
sudo /usr/bin/python3 -I \
  /opt/basaltwater/common/service_tools/user_cache_launcher.py --user USER --dry-run
```

Replace `USER` with the setup account. Omit `--dry-run` to run cleanup now.
Rerun setup to install this helper and daily timer on an existing host;
updating the controller checkout alone does not change the target's timer.
Unprivileged setup and direct-user invocations retain
`user_cache_maintenance.py` as their fallback. They apply the same deferral
and npm download-cache policy when protected `/proc` metadata cannot be
inspected, without gaining observer privileges. The host supervisor verifies
the exact launcher command and binds `--user` to its trusted baseline account
before dispatch; only the fixed legacy worker commands remain accepted during
migration.

Cleanup never removes installed language runtime versions, Proxmox backups,
templates, ISOs, guest volumes, container/image stores, crash-report
directories, or arbitrary files. Proxmox boot entries and pinned kernels remain
under APT and `proxmox-boot-tool` retention policy.

After cleanup, capacity checks cover each distinct real local filesystem rather
than only `/`. Both block usage and inode usage trigger warnings at 80% and
errors at 90%, so a separate Proxmox storage mount cannot fill silently while
the root filesystem remains healthy. Network and FUSE mounts are excluded to
avoid blocking maintenance on unavailable remote storage.

Storage synchronization and scrub jobs write scheduling state atomically and
use current-user-owned, mode-`0700` lock directories under
`/run/lock/basaltwater`; lock files are regular mode-`0600` files and retain a
persistent inode to prevent overlapping runs. Invalid specifications or
unavailable mounts fail visibly so the next scheduled run can retry them.
