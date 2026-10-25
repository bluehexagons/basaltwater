# Storage operations

`--sync`, `--backup`, and `--scrub` configure recurring storage work. They are saved with
the machine configuration and run together through the root-owned
`storage-ops.service` and its hourly `storage-ops.timer`.

## Configure a mirror and parity protection

```bash
basaltw setup server_lite fileserver admin \
  --sync /srv/data /mnt/backup/data daily \
  --scrub /srv/data .pardatabase 10% weekly \
  --notify mailbox ops@example.com
```

Both paths must be absolute. A scrub database may be a relative path, which is
resolved under the protected directory, or an absolute path on another volume.
Supported intervals are `hourly`, `daily`, `weekly`, `biweekly`, `monthly`, and
`bimonthly`. Multiple `--sync`, `--backup`, and `--scrub` specifications may be supplied.

Use `--backup SOURCE DESTINATION INTERVAL` for a semantic backup mirror. It
uses the same rsync service and mount checks as `--sync` but is kept distinct
in saved configuration and summaries. It is not tied to Samba; see
[backups.md](backups.md) for mounted VM storage and consistency guidance.

Normal setup installs the tools and hourly timer and requests an initial run
about two minutes later. That run creates mirror destinations and initial
parity. The first full verify-and-repair scrub waits until the configured scrub
interval is due. Parity maintenance runs daily thereafter, creating protection
for new files while preserving existing recovery evidence. The explicit custom
`create_sync_service` and `create_scrub_service` steps instead perform their
initial work inline and fail setup if it fails.

The inline custom steps fail before directory creation or sync/parity work when
a required mount is missing. Failed SMB connectivity checks also abort their work. Mount
status inspection is read-only; explicit SMB write probes use unique temporary
files and remove only their own probe. CIFS file creation, readback, cleanup,
and directory listing run in a separate supervised process with a 15-second
probe deadline. If the kernel cannot stop a process blocked in CIFS I/O, its
caller keeps supervising it until it exits; a timed-out write probe can leave
its uniquely named temporary file behind.
Sync source checks require only read access. SMB subdirectories are checked
against their containing mount, and new sync destinations are created after
mount validation before their write probe runs.
`mountpoint` and `findmnt` probes have a 15-second deadline; a timed-out probe
is treated as an unavailable mount after its process exits. Uninterruptible
kernel I/O can delay that exit too.

## Sync behavior

Each due sync runs rsync with archive mode, delayed deletion, partial-transfer
support, destination directory creation, and `.git` exclusion. Both normal and
conservative copy modes keep interrupted replacements in `.rsync-partial`,
separate from an existing destination file. That directory is excluded from
mirroring. The destination
is a mirror: files removed from the source are removed from the destination.
Do not use `--sync` for an append-only backup unless that deletion behavior is
acceptable.

A failed mirror is not an atomic rollback: unrelated file updates and deletions
may already have completed before an I/O error is reported. Keep independent
backups or snapshots for recovery of an entire prior tree. Rsync's usual
size/timestamp comparison can also skip unchanged files without reading their
contents; successful mirroring does not replace integrity verification.

Equal or nested sync paths are rejected, including aliases through symlinks.
The source must be an existing directory. The controller performs only lexical
path validation. Symlink resolution, source-directory checks, configured mount
checks, nested-mount inventory, and scheduled integrity-findings reads run in
the private namespace worker before it starts rsync. This keeps cold CIFS
metadata access under the worker's preflight deadline instead of blocking the
orchestrator before supervision begins. Preflight is limited to 120 seconds or
the smaller configured `RSYNC_TIMEOUT_SECONDS`; rsync then has its own
four-hour deadline. Output from both rsync pipes is drained without waiting
for newlines, and retained output is bounded to prevent large runs from
exhausting memory.

Custom inline sync setup uses the same supervised validation and computes mount
exclusions only inside that worker. Incomplete mount coverage fails the setup
step after preserving the unavailable subtrees; restore the mount and rerun.
The setup caller performs no additional filesystem capacity probes after sync.

On timeout the caller sends TERM and then KILL to the worker process group, and
waits for both its leader and any remaining process-group members to disappear
before returning a failure or recording a retry. This retains the scheduled
storage-operations lock and prevents a retry from overlapping a residual rsync
child. A deadline bounds waiting for normal worker output, not total wall time:
SIGKILL cannot finish a task stuck in uninterruptible kernel I/O, and even an
unreaped zombie can keep the process group visible. In those cases the caller
fails closed and keeps the lock while it continues supervising; total runtime
can exceed the configured deadline indefinitely. A caller forcibly killed
outside this cleanup cannot prove that descendants are gone, so confirm the
service cgroup is empty before manually retrying such an interrupted operation.
Normal mirrors are not tree-atomic. Rerun setup to refresh existing service
units.

Scheduled syncs and backups continue when either tree contains open scrub findings.
Only affected relative paths are excluded from transfer and deletion, preserving
existing destination copies while other files are mirrored normally. This also
protects destination files when the source file is missing. Completion is recorded
on the normal schedule, with a warning listing a bounded sample of preserved paths.
If a file or symlink replaces a protected path's directory ancestor, that ancestor
is preserved too, avoiding repeated transfer errors while other paths sync.
Resolve findings with `basaltw scrub` to include those paths in subsequent syncs.
These precautions use recorded findings; they do not verify every source file.

If a relevant findings report cannot be read (including an unavailable parity
volume), the mirror continues in a conservative mode: it copies new files,
preserves existing destination files, and disables deletion. A finding covering
the entire sync root uses this mode too. Logs and notifications report the warning.
Source directories blocked by existing destination files or symlinks are skipped
so these type conflicts do not trigger repeated transfer failures.
The report and parity remain intact for investigation. Normal mirroring resumes
on a subsequent scheduled run once the report is readable and root findings are
resolved.
Destinations that contain, are inside, or alias a configured parity database are
rejected because rsync could delete recovery evidence. Place the database outside
the mirror destination. These checks apply to the scheduled orchestrator;
standalone rsync and custom inline setup steps do not consult findings.

At application setup boundaries guarded by declared VM mounts, both write
paths and mount declarations must be canonical absolute paths. The guard
rejects dot or dot-dot components, repeated separators, a double-leading
slash, and a trailing slash before deciding whether a path is managed, so an
escaped path fails instead of being treated as unmanaged.

Operations require the exact configured SMB or VM mount before running;
a mounted parent does not satisfy a missing child mount. If an expected SMB,
VM, or other mounted filesystem is unavailable, only operations requiring it
are skipped instead of writing to an underlying local directory. Mount checks
run separately for each operation, so an offline scrub database or another job's
missing mount cannot stop independent backups. Rerun setup to replace older
service units that checked every storage mount before starting the orchestrator.
Mount checks also resolve endpoint aliases. An unavailable configured mount
below either mirror root is excluded from both transfer and deletion, with a
warning, so healthy subtrees continue and existing backup content is preserved.
Such a run is incomplete: its retry history is retained, its completion
timestamp does not advance, aggregate storage-operations health is failed, and
the service exits nonzero. Independent healthy jobs still run. Once coverage
returns, a later retry clears the history and advances the configured cadence.
These nested mount protections also apply to custom inline sync setup steps.
The standalone `sync_rsync.py SOURCE DESTINATION` entry point applies the same
endpoint and nested mount guards using the saved setup configuration. Missing,
unreadable, or invalid configuration stops standalone sync; inline setup uses
its current declarations before they have been saved.
Saved mount fields accept absence, `null`, or an empty list as no declarations;
malformed values such as `false`, `0`, an empty string, or an object are rejected
before normalization, so they cannot silently disable mount guards.

Every rsync runs in a new Linux mount namespace created by util-linux
`unshare --mount --propagation private`. The worker validates required mounts
and computes nested exclusions after propagation is detached, then executes
rsync in that same namespace. Host unmounts after validation cannot expose an
underlying directory to that traversal. A mount removed before namespace
creation is rejected or excluded by the worker. Inline setup's directory
creation and SMB probes also run inside this boundary.
The worker sends a bounded precaution report through a separate anonymous file
descriptor before executing rsync; ordinary rsync output is never interpreted
as that report. Missing or malformed reports fail the operation. Worker-discovered
unavailable subtrees appear in scheduled results and warning notifications, as
well as standalone notifications. When the worker preserves mount subtrees, the
sync does not advance its completion timestamp: it retries with the backoff
used for incomplete operations, from one hour up to twenty-four hours. Every retry uses
the private namespace and rechecks mounts; no unprotected retry is attempted.
Once mount coverage is restored, completion clears the retry history and the
configured interval resumes. Recorded integrity findings alone retain their
normal completion cadence.
Generated exclusions preserve literal spaces and glob characters. The worker
passes their NUL-delimited filters through rsync's standard input using an
anonymous file, so no filter pathname needs cleanup after execution.
An autofs placeholder alone does not satisfy a declared mount: the backing
filesystem must already be mounted when the namespace is created. Otherwise
the worker rejects the endpoint or excludes the nested subtree. Activate the
mount on the host before retrying; host-side activations after isolation are
visible only to subsequent workers.

Creating the namespace requires `CAP_SYS_ADMIN` in the governing user namespace
(normally root). LXC, non-root execution, and service sandbox restrictions can
deny it even when other storage work is allowed. Missing `unshare`, namespace
creation failure, or unverifiable private propagation fails the sync with no
unprotected fallback. Check the worker's journal error and host policy. New host
mounts are visible to the next run; the current run retains its original mount
view. This is not a data snapshot: concurrent file changes and filesystem or
network errors can still affect the run. Keep endpoint paths stable; namespace
isolation does not prevent another writer replacing paths or symlinks. Scrub
retains its separate mount checks.

Managed SMB client mounts use native systemd automount units, with a 60-second
idle timeout. Access triggers the matching network-ordered mount; enabling the
automount does not prove that the server is reachable. Storage operations still
check the actual mount before writing. Client setup replaces the mount and
automount as one recoverable unit group and reports activation failures; see
[Samba client mounts](samba-shares.md#connecting-from-a-client).

## Parity and repair behavior

### Btrfs filesystem scrub

Btrfs has a separate native scrub that reads filesystem data and metadata and
checks their checksums. It can repair a damaged block only when another valid
copy exists. Basaltwater's single-device Btrfs layout stores data with one copy,
so native scrub can detect damaged data but cannot repair it from another data
copy. Its duplicated metadata profile may provide another metadata copy on the
same device; that is not a backup or device-failure protection. Setup does not
start or schedule native Btrfs scrub, balance, or repair work.

Inspect retained device counters and the latest native scrub result with:

```bash
sudo btrfs device stats --check /srv/data
sudo btrfs scrub status /srv/data
```

Device counters retain historical errors, including after a later successful
scrub; `--check` returns nonzero when any counter is nonzero. Keep that history
when investigating failures. To explicitly read and check the filesystem's
allocated data and metadata without requesting repairs:

```bash
sudo btrfs scrub start -B -r /srv/data
```

This reads the filesystem, not just the named directory, and can be substantial
work. On a writable mount, even `-r` can cause internal filesystem writes; it is
not a forensic no-write operation. See the upstream
[scrub](https://btrfs.readthedocs.io/en/latest/btrfs-scrub.html) and
[device statistics](https://btrfs.readthedocs.io/en/latest/btrfs-device.html)
references. A clean result for selected allocations does not establish the
health of the rest of a failing device.

Basaltwater `--scrub` uses PAR2 files to protect and verify selected directory
contents. It is independent of Btrfs scrub and can repair file corruption while
enough PAR2 data remains available. Configure it explicitly with `--scrub` and
keep a separate backup for failures beyond the configured parity. A checksum
failure can make the original file unreadable; Basaltwater's staged recovery
requires retaining its original bytes and then stops with an I/O error. See
the [recovery limits](scrub-recovery.md#recover-or-accept-one-file) before
attempting to restore an unreadable file.

`--scrub DIRECTORY DATABASE_PATH REDUNDANCY FREQUENCY` uses `par2` to create
redundancy files, verify protected files, and repair corruption when possible.
Missing protected files are recorded and their parity retained: absence alone
does not prove an intentional deletion. `REDUNDANCY` is an integer
percentage from `1%` through `100%`. Empty files are skipped because par2 cannot
create useful parity for them. Fast maintenance flags an empty file with existing
parity as a possible truncation; full scrubs verify it against that baseline.
A file supplied as the source directory is rejected before orphan cleanup.

Full scrubs report repaired files as warnings and unresolved integrity findings
as errors. Findings are classified and retained in the parity database; use
[`basaltw scrub`](scrub-recovery.md) to inspect or remediate individual files.
Existing parity is no longer regenerated just because a source timestamp is
newer. Such changes are marked uncertain until verified or explicitly accepted.
Automatic repairs use staged copies and retain original data and parity; a
newer source file is never automatically reverted to the old parity baseline.
Parity metadata lives under the configured database path; keep it on reliable
storage separate from the data when possible.
New parity is built in staging, independently verified against unchanged source
content, and published by atomically selecting an immutable per-file generation.
Creation subprocesses have a four-hour deadline. Failed builds retain their
staging and manifest; previous generations and legacy parity are never removed
automatically. Recovery payloads are excluded from directory inventory traversal.
Source changes during generation publication leave an open finding, an incomplete
scan, and a retained `published_unverified` build for inspection.
A full scrub already performs parity maintenance, so it is not followed by a
second fast pass. A scan that finishes with unrepairable files records its
completion timestamp and reports an error, then waits for the configured scrub
interval before verifying again. Persistent damage does not trigger full scans
and failure notifications every hour. Daily parity maintenance continues and
reports unresolved findings without verifying those files again. Its completion
does not resolve the findings.
If validation, file access, parity creation, or cleanup prevents completion,
the completion timestamp stays unchanged. Missing generation volumes and per-file
verification errors leave findings for the affected files while the scan continues
through other files. The report records the scan as incomplete and retains all
remaining parity. Executed operations that fail retry
after one hour, then two, four, eight, sixteen, and at most twenty-four hours
between consecutive failures. Completion clears the retry history. Full scrubs
waiting for a retry are not replaced by an unverified fast parity pass.
Scrub mount preflight skips remain eligible for hourly rechecks. Sync worker
preflight failures use the operational retry backoff.

Scrub rejects symlinks in source and database paths, including parent components.
Both trees must be fully readable before parity changes begin; an incomplete
inventory aborts the run and preserves orphan parity. Keep these trees stable
during scrubs: path checks do not protect against a concurrent hostile writer
replacing directories while PAR2 runs. Cleanup matches only exact parity sets,
and the CLI returns failure for unsuccessful creation, repair, or cleanup.
Special files such as FIFOs and sockets abort the inventory. Volume-only parity
uses the same timestamp/update checks and counters as sets with an index file.

## Inspect and run operations

In the web panel, open **Scheduled jobs → Load scheduled jobs** for a read-only
Storage integrity section alongside timer/process status. Its private root-produced
snapshot refreshes every five minutes and shows bounded findings and scan history.
Stale or unavailable snapshots are explicit; loading the page never starts a scan.
Use the CLI for the complete report and remediation.

```bash
basaltw scrub status fileserver
basaltw scrub inspect fileserver --file /srv/data/example.bin
basaltw scrub verify fileserver --file /srv/data/example.bin
sudo systemctl status storage-ops.timer
sudo systemctl start storage-ops.service
sudo journalctl -u storage-ops.service -n 200 --no-pager
sudo ls -l /var/log/storage-ops /var/log/scrub
sudo cat /var/lib/storage-ops/last_run.json
```

The service uses `/run/lock/storage-ops.lock` to prevent overlapping runs and
writes its last-run timestamps atomically to
`/var/lib/storage-ops/last_run.json` after each completed operation, so a later
failure cannot lose earlier completion timestamps. Scrub timestamps represent completed scans,
including scans that found unrepairable files; sync timestamps represent success.
Incomplete or skipped operations remain due for a later run. The timer itself is
hourly; each specification's interval is enforced by the orchestrator.
The `_attempts` mapping in that state file records consecutive operational
failures and `retry_after` Unix timestamps separately from completed cadence.
Starting the service manually still respects this backoff; targeted CLI verify
and recovery remain available under the shared operation lock.

Only a missing last-run file starts a new schedule. Malformed JSON, invalid
timestamps or retry entries, symlinks, special files, and files over 1 MiB stop
all scheduled operations before any sync or parity work. The service reports
failure and retains the file; restore a verified backup or explicitly quarantine
it after reviewing cadence and retry history. Quarantining it resets that history
and delays the first full scrub by its configured interval.

## Change or remove storage work

Use the normal saved-configuration flow to change storage specifications:

```bash
basaltw patch fileserver admin \
  --sync /srv/data /mnt/backup/data weekly
basaltw deploy fileserver
```

Review the saved configuration with `basaltw info fileserver` before
changing a production mirror. Existing storage state and logs are retained;
the next service run uses the saved specification set.

Notifications are shared with maintenance and security services. See
[Notifications](../services/notifications.md) for target types and delivery behavior.

## Troubleshooting

- If a run skips a path, verify the mount with `findmnt --target PATH` and
  inspect the `storage-ops.service` journal.
- If parity verification reports an unrepairable file, restore it from an
  independent copy; parity cannot repair damage beyond the configured
  redundancy.
- If a run is already active, the second invocation exits cleanly after
  reporting the lock rather than running concurrently.
