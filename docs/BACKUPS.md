# Generic path backups

Use `--backup SOURCE DESTINATION INTERVAL` for a recurring path mirror. It is
deliberately independent of Samba: the destination may be a local directory,
a directory on an additional mounted block device, or a mounted filesystem
managed outside Basaltwater.

```bash
basaltw setup server_dev 192.168.0.41 agent \
  --backup /srv/agent-workspace /srv/backups/agent-workspace daily \
  --backup /srv/gogs /srv/backups/gogs daily \
  --scrub /srv/backups /srv/backups.par2 10% weekly
```

Backup jobs use the existing root-owned `storage-ops.service` and hourly
timer. `--backup` is a semantic label for an rsync mirror, so it has the same
interval choices and mount checks as `--sync`. When the destination is a
declared `--storage-mount`, the service has an explicit systemd mount guard and
will not run while that disk is absent. It does not create snapshots,
retention points, or an automatic restore workflow.

For parity protection, add a matching `--scrub` job. Parity metadata should be
stored on reliable storage separate from the data when possible. A mirror and
parity are useful layers, but neither is a substitute for an independent
off-host copy.

## Additional VM storage

When a VM is provisioned on Proxmox, attach and mount a named data disk before
using it as a backup destination:

```bash
basaltw setup server_dev 192.168.0.41 agent \
  --provision-on pve1 --memory 2G --cores 2 \
  --storage root local-lvm 32G \
  --disk-ssd root --disk-discard root --disk-backup root \
  --storage backup-data bulk-lvm 256G \
  --storage-mount backup-data /srv/backups ext4 empty \
  --no-disk-ssd backup-data --disk-discard backup-data --disk-backup backup-data \
  --backup /srv/agent-workspace /srv/backups/agent-workspace daily
```

The mount declaration is fail-closed: setup will not silently write to the
root filesystem if the expected device is absent. The destination remains a
normal path, so the same backup declaration also works with a separately
managed mount, a local disk, or a future storage provider. The disk backup flag
controls whether Proxmox includes the data disk in a VM-level backup; it does
not schedule the file-level mirror declared by `--backup`.

## Consistency and recovery limits

For HomeBox, use [`basaltw homebox backup`](HOMEBOX.md#backup-and-restore).
It stops the application and captures SQLite, attachments, secrets, and the
matching executable together. Mirror completed archives off-host with
`--backup`; do not mirror its live data directory as a recovery snapshot.

Rsync mirrors are file-level copies. Do not assume that copying a live database
produces a transactionally consistent backup. For Gogs or another Git service,
use its application-level export or a coordinated stop/snapshot procedure for
recovery-grade backups; use `--backup` for repositories, LFS objects, static
configuration, and other paths where a mirror is appropriate.

Inspect operations with:

```bash
sudo systemctl status storage-ops.timer
sudo systemctl start storage-ops.service
sudo journalctl -u storage-ops.service -n 200 --no-pager
```

See [STORAGE_OPERATIONS.md](STORAGE_OPERATIONS.md) for scheduling, parity,
locks, mount checks, and troubleshooting.
