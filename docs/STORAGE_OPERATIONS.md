# Storage operations

`--sync`, `--backup`, and `--scrub` configure recurring storage work. They are saved with
the machine configuration and run together through the root-owned
`storage-ops.service` and its hourly `storage-ops.timer`.

## Configure a mirror and parity protection

```bash
infra-tools setup server_lite fileserver admin \
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

Setup validates the paths and mounts, creates missing destination/metadata
directories, and performs an initial sync. Initial parity files are created in
fast mode; the first full verify-and-repair scrub waits until the configured
scrub interval is due. Parity updates run daily thereafter so changed files are
protected between full scrubs.

Initial setup fails before directory creation or sync/parity work when a required
mount is missing. Failed SMB connectivity checks also abort initial work. Mount
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

Operations validate mount ancestors before running. Declared VM data disks are
also protected by a systemd mount guard on the storage service. If an expected
SMB, VM, or other mounted filesystem is unavailable, the operation is skipped
or prevented from starting instead of writing to an underlying local directory.

## Parity and repair behavior

`--scrub DIRECTORY DATABASE_PATH REDUNDANCY FREQUENCY` uses `par2` to create
redundancy files, verify protected files, repair corruption when possible, and
remove parity for data files that no longer exist. `REDUNDANCY` is an integer
percentage from `1%` through `100%`. Empty files are skipped because par2 cannot
create useful parity for them.

Full scrubs report repaired files as warnings and unrepairable files as errors.
Parity metadata lives under the configured database path; keep it on reliable
storage separate from the data when possible.

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
sudo systemctl status storage-ops.timer
sudo systemctl start storage-ops.service
sudo journalctl -u storage-ops.service -n 200 --no-pager
sudo ls -l /var/log/storage-ops /var/log/scrub
sudo cat /var/lib/storage-ops/last_run.json
```

The service uses `/run/lock/storage-ops.lock` to prevent overlapping runs and
writes its last-run timestamps atomically to
`/var/lib/storage-ops/last_run.json`. A failed or skipped operation remains due
for a later run. The timer itself is hourly; each specification's interval is
enforced by the orchestrator.

## Change or remove storage work

Use the normal saved-configuration flow to change storage specifications:

```bash
infra-tools patch fileserver admin \
  --sync /srv/data /mnt/backup/data weekly
infra-tools deploy fileserver
```

Review the saved configuration with `infra-tools info fileserver` before
changing a production mirror. Existing storage state and logs are retained;
the next service run uses the saved specification set.

Notifications are shared with maintenance and security services. See
[Notifications](./NOTIFICATIONS.md) for target types and delivery behavior.

## Troubleshooting

- If a run skips a path, verify the mount with `findmnt --target PATH` and
  inspect the `storage-ops.service` journal.
- If parity verification reports an unrepairable file, restore it from an
  independent copy; parity cannot repair damage beyond the configured
  redundancy.
- If a run is already active, the second invocation exits cleanly after
  reporting the lock rather than running concurrently.
