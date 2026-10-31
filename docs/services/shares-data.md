# Shares & Data in the web panel

Open **Administration → Shares & Data** to inspect this machine’s managed
Samba shares, recurring backup and sync declarations, and recorded scrub findings.
The initial page is read-only. Share changes, credential resets, routine editing,
and single-file recovery still use the controller commands described below.

The three views work without JavaScript:

- **Samba shares** shows each saved share’s actual SMB name (including its
  `_read` or `_write` suffix), directory, access mode, users, and connection path.
  This is the saved setup, not live account, session or filesystem ACL inspection.
  Manually configured Samba shares are outside this inventory.
- **Backups & sync** shows source, destination and interval for each recurring
  rsync mirror. Backup declarations have the same mirroring behavior as sync;
  they do not provide snapshots or retained versions. Links open timer status and
  storage job logs separately. An inventory entry does not prove job completion
  or that a backup is recoverable.
- **Scrubs & findings** distinguishes unavailable reports, never-scanned roots,
  incomplete scans, open findings, and completed recorded scans. Filter exported
  findings by category or file path and move between pages. Each finding explains
  the category and provides a shell-quoted controller inspection command with
  its configured scrub root and parity directory.

The root audit exporter refreshes the bounded, non-secret inventory every five
minutes. Page refresh reads that export; it does not run Samba commands, scan
directories, start storage jobs or read private PAR2 evidence. Exports older than
15 minutes are unavailable. A missing or invalid configuration inventory does
not suppress a valid integrity report. Older exporters need setup/refresh before
the new configuration views can show their data.

Exports include at most 64 shares, 64 users per share, 128 backup/sync routines,
eight scrub tasks and 20 findings per scrub task. Omitted counts remain visible;
filters and pagination apply to exported findings only. Use `basaltw scrub status
HOST` for all recorded open findings, and `--all` to include resolved findings.
Passwords, mount credentials, raw PAR2 output, and recovery copies are not exported.

Use the [Samba guide](../storage/samba-shares.md) for changing shares and
credentials, [storage operations](../storage/storage-operations.md) for routines
and scheduling, and [scrub recovery](../storage/scrub-recovery.md) for targeted
verification, staged repair, restoration, and acceptance of confirmed content.
Repair and restore verify against existing parity. Acceptance establishes a new
baseline and requires an explicit decision that the current content is correct.
