# Shares & Data in the web panel

Open **Administration → Shares & Data** to inspect this machine’s managed
Samba shares, recurring backup and sync declarations, and recorded scrub findings.
Share changes, credential resets and routine editing still use the controller
commands described below. Single-file verification and recovery also have
browser controls when the separate privilege approval service is configured.
The Samba view also shows local account status and provides approved login
enable/disable controls.

The three views work without JavaScript:

- **Samba shares** shows each saved share’s actual SMB name (including its
  `_read` or `_write` suffix), directory, access mode, users, and connection path.
  The separate account list shows exported login/lockout flags and Unix UIDs;
  it does not inspect sessions or filesystem ACLs. Manually configured Samba
  shares are outside the share inventory.
- **Backups & sync** shows source, destination and interval for each recurring
  rsync mirror. Backup declarations have the same mirroring behavior as sync;
  they do not provide snapshots or retained versions. Links open timer status and
  storage job logs separately. An inventory entry does not prove job completion
  or that a backup is recoverable.
- **Scrubs & findings** distinguishes unavailable reports, never-scanned roots,
  incomplete scans, open findings, and completed recorded scans. Filter open
  findings across all configured tasks by category or file path and move between
  pages of twenty. File searches are case-insensitive and treat punctuation
  literally. Each finding explains
  the category and provides a shell-quoted controller inspection command with
  its configured scrub root and parity directory.

The root audit exporter refreshes the bounded, non-secret inventory every five
minutes. Page refresh reads that export; it does not run Samba commands, scan
directories, start storage jobs or read private PAR2 evidence. Exports older than
15 minutes are unavailable. A missing or invalid configuration inventory does
not suppress a valid integrity report. Older exporters need setup/refresh before
the new configuration views can show their data.

The summary includes at most 64 shares, 64 users per share, 128 backup/sync routines,
eight scrub tasks and twenty preview findings per task. A separate sanitized local
catalog includes open findings from every configured scrub task, including tasks
outside that summary. Filters and pagination use this complete catalog. Failed
mount checks, unreadable reports or unsupported report metadata produce an explicit
incomplete-results notice. A missing, corrupt or mismatched catalog is unavailable,
never an empty or healthy result. Older exporters retain the limited preview and
show an update notice.

Catalog export shares the storage collector’s twenty-second deadline and has a
128 MiB file limit. The summary has an 8 MiB transport and read limit to accommodate
the supported inventory sizes and escaped Unicode paths. Only a completed export
is published; interruption or exceeding these bounds makes the new export
unavailable. Queries read an immutable local
file, return at most twenty rows and have a short execution budget. They never
read NAS paths. Use `basaltw scrub status HOST` for controller inspection and
`--all` to include resolved findings.
Passwords, mount credentials, raw PAR2 output, and recovery copies are not exported.

## Enable or disable a Samba login

The account list supports standalone Samba with the default local `tdbsam`
backend, up to 512 accounts. Unsupported backends, inconsistent records, tool
failures or an oversized export make account inspection unavailable. This does
not suppress the shares inventory or scrub findings.

Choose **Review disable Samba login** or **Review enable Samba login**, check
the account, type its exact username and create an approval request. Open the
separate approval page to approve or deny it. Pending requests can be cancelled
from **Samba activity**. Only regular password-protected Samba accounts with a
matching Unix UID from 1000 through 65533 have browser controls. System accounts,
trust accounts and missing/mismatched Unix accounts require the administrator
command line.

The operation changes only Samba's disabled flag. It preserves credentials,
Unix login and share membership; enabling does not clear account lockout.
Disabling may leave existing SMB sessions connected. The root helper rechecks
account identity, flags and configuration metadata, rejects stale reviews and excludes concurrent
setup/refresh. It runs once in a supervised service with a 45-second deadline.
**Succeeded** means the helper verified the requested flag, while **failed** or
**uncertain** requires inspecting current account state before trying again.
The account list follows the next five-minute export; it may still show the
earlier flag immediately after success. Reviews expire after five minutes and
are consumed before submission; a lost response is never replayed automatically.

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
