# Shares & Data in the web panel

Open **Administration → Shares & Data** to inspect this machine’s managed
Samba shares, recurring backup and sync declarations, and recorded scrub findings.
Share changes, credential resets and routine editing still use the controller
commands described below. Single-file verification and recovery also have
browser controls when the separate privilege approval service is configured.

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

## Review and resolve one finding

On a supported non-root VM operator installation with privilege approvals,
open a finding’s **Inspect evidence and recovery options** disclosure. Choose
**Verify file**, **Repair file**, **Restore file**, or **Accept current content**.
Restoration requires an absolute path to an independent copy on the target host.
The controller inspection command remains available for the full private report.

1. Select **Review recovery** and check the exact file, scrub root, parity
   directory, independent copy (if applicable), and expected effects.
2. For repair and restore, type `CHANGE FILE`. For acceptance, type
   `ACCEPT CURRENT CONTENT`. Verification needs no typed confirmation.
3. Create the approval request, then open its review link and approve or deny
   it using the separate approval password. A panel review never grants approval.
4. Refresh the page to inspect **Recovery activity**. A dispatched request only
   means the worker was launched. The latest worker result reports completion,
   remaining findings, refusal or interruption. The findings snapshot updates on
   the next five-minute export.

Reviews expire after five minutes and can be submitted once. Pending requests
can be cancelled before execution is claimed. Neither the panel nor the worker
automatically retries a lost response, failed operation, or interrupted job.
The worker runs outside the panel with a six-hour deadline and uses the same
storage lock, configured-job selection, mount guards and staged recovery as
the CLI. It refuses a finding that changed since review, and refuses recovery
when the file changed since its recorded check. Older findings without a file
identity must be verified first. A verification that still reports damage is
shown as **findings**, not successful recovery.

Acceptance requires an independent decision that the current content is correct;
it replaces the integrity baseline. Repair and restore verify a staged candidate
against existing parity and retain originals. No bulk acceptance, deletion or
unattended remediation is provided.

Use the [Samba guide](../storage/samba-shares.md) for changing shares and
credentials, [storage operations](../storage/storage-operations.md) for routines
and scheduling, and [scrub recovery](../storage/scrub-recovery.md) for targeted
verification, staged repair, restoration, and acceptance of confirmed content.
Repair and restore verify against existing parity. Acceptance establishes a new
baseline and requires an explicit decision that the current content is correct.
