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
[BACKUPS.md](BACKUPS.md) for mounted VM storage and consistency guidance.

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
files and remove only their own probe.
Sync source checks require only read access. SMB subdirectories are checked
against their containing mount, and new sync destinations are created after
mount validation before their write probe runs.
`mountpoint` and `findmnt` probes are bounded to 15 seconds; a timed-out probe
is treated as an unavailable mount so setup cannot write through a stale mount.

## Sync behavior

Each due sync runs rsync with archive mode, delayed deletion, partial-transfer
support, destination directory creation, and `.git` exclusion. The destination
is a mirror: files removed from the source are removed from the destination.
Do not use `--sync` for an append-only backup unless that deletion behavior is
acceptable.

Equal or nested sync paths are rejected, including aliases through symlinks.
The source must be an existing directory. Output from both rsync pipes is
drained without waiting for newlines, and retained output is bounded to prevent
large runs from exhausting memory.

Scheduled syncs pause if either affected tree contains an open scrub finding,
so known damage is not propagated and a suspect destination is not overwritten
before inspection. An unreadable findings report also blocks the affected sync.
Resolve the finding with `basaltw scrub` before retrying. This guard uses recorded
findings; it does not verify every source file before each sync.
Destinations that contain, are inside, or alias a configured parity database are
rejected because rsync could delete recovery evidence. Place the database outside
the mirror destination. These checks apply to the scheduled orchestrator;
standalone rsync and custom inline setup steps do not consult findings.

Operations require the exact configured SMB or VM mount before running;
a mounted parent does not satisfy a missing child mount. Declared VM data disks are
also protected by a systemd mount guard on the storage service. If an expected
SMB, VM, or other mounted filesystem is unavailable, the operation is skipped
or prevented from starting instead of writing to an underlying local directory.

## Parity and repair behavior

`--scrub DIRECTORY DATABASE_PATH REDUNDANCY FREQUENCY` uses `par2` to create
redundancy files, verify protected files, and repair corruption when possible.
Missing protected files are recorded and their parity retained: absence alone
does not prove an intentional deletion. `REDUNDANCY` is an integer
percentage from `1%` through `100%`. Empty files are skipped because par2 cannot
create useful parity for them. An empty file with existing parity is still
verified during a full scrub, so truncation is not silently treated as healthy.
A file supplied as the source directory is rejected before orphan cleanup.

Full scrubs report repaired files as warnings and unresolved integrity findings
as errors. Findings are classified and retained in the parity database; use
[`basaltw scrub`](SCRUB_RECOVERY.md) to inspect or remediate individual files.
Existing parity is no longer regenerated just because a source timestamp is
newer. Such changes are marked uncertain until verified or explicitly accepted.
Automatic repairs use staged copies and retain original data and parity; a
newer source file is never automatically reverted to the old parity baseline.
Parity metadata lives under the configured database path; keep it on reliable
storage separate from the data when possible.
A full scrub already performs parity maintenance, so it is not followed by a
second fast pass. A scan that finishes with unrepairable files records its
completion timestamp and reports an error, then waits for the configured scrub
interval before verifying again. Persistent damage does not trigger full scans
and failure notifications every hour. Daily parity maintenance continues and
reports unresolved findings without verifying those files again. Its completion
does not resolve the findings.
If validation, file access, parity creation, or cleanup prevents completion,
the timestamp stays unchanged and the next hourly run retries the operation.

Scrub rejects symlinks in source and database paths, including parent components.
Both trees must be fully readable before parity changes begin; an incomplete
inventory aborts the run and preserves orphan parity. Keep these trees stable
during scrubs: path checks do not protect against a concurrent hostile writer
replacing directories while PAR2 runs. Cleanup matches only exact parity sets,
and the CLI returns failure for unsuccessful creation, repair, or cleanup.
Special files such as FIFOs and sockets abort the inventory. Volume-only parity
uses the same timestamp/update checks and counters as sets with an index file.

## Inspect and run operations

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

After upgrading an installation affected by hourly retries, the old overdue
timestamp is retained. Expect one more full scrub to record completion under
the new behavior; no manual state reset is needed. The scan still reports any
unrepairable files, which require restoration from an independent copy.

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
[Notifications](./NOTIFICATIONS.md) for target types and delivery behavior.
See [the storage review](STORAGE_REVIEW.md) for remaining automation and web panel
integration work.

## Troubleshooting

- If a run skips a path, verify the mount with `findmnt --target PATH` and
  inspect the `storage-ops.service` journal.
- If parity verification reports an unrepairable file, restore it from an
  independent copy; parity cannot repair damage beyond the configured
  redundancy.
- If a run is already active, the second invocation exits cleanly after
  reporting the lock rather than running concurrently.
