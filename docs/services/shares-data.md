# Shares & Data in the web panel

Open **Administration → Shares & Data** to inspect this machine’s managed
Samba shares, recurring backup and sync declarations, and recorded scrub findings.
Share and routine editing, account creation/removal, login enable/disable and password reset, and
single-file verification and recovery have browser controls when the separate
privilege approval service is configured.

The three views work without JavaScript:

- **Samba shares** shows each saved share’s actual SMB name (including its
  `_read` or `_write` suffix), directory, access mode, users, and connection path.
  The separate account list shows exported login/lockout flags and Unix UIDs;
  it does not inspect sessions or filesystem ACLs. Manually configured Samba
  shares are outside the share inventory.
- **Backups & sync** shows source, destination and interval for each recurring
  rsync mirror, its active/paused schedule and exported completion/retry history.
  Backup declarations have the same mirroring behavior as sync;
  they do not provide snapshots or retained versions. Links open timer status and
  storage job logs separately. An inventory entry does not prove job completion
  or that a backup is recoverable.
- **Scrubs & findings** manages scrub declarations and distinguishes unavailable reports, never-scanned roots,
  incomplete scans, open findings, and completed recorded scans. **Task health**
  searches scrub roots and pages through every task, including its last full scan,
  open count and parity directory. Its root filter is independent of the finding
  filters. Filter open
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

The older summary includes at most 64 shares, 64 users per share, 128 backup/sync
routines, eight scrub tasks and twenty preview findings per task. Routine controls
use a separate complete inventory of up to 256 routines across all three kinds;
they do not select tasks from that limited summary. A separate sanitized local
catalog includes health and open findings from every configured scrub task,
including tasks outside that summary. Each browser returns twenty rows per page;
task and finding filters preserve one another when paging or refreshing. Searches
are case-insensitive and literal. Filters and pagination use this complete catalog. Failed
mount checks, unreadable reports or unsupported report metadata produce an explicit
incomplete-results notice. A missing, corrupt or mismatched catalog is unavailable,
never an empty or healthy result. Older exporters retain the limited preview and
show an update notice. Version-one catalogs still provide complete findings;
complete task health requires the current exporter. Invalid health metadata is
marked unavailable independently, so it cannot hide valid findings.

Catalog export shares the storage collector’s twenty-second deadline and has a
128 MiB file limit. The summary has an 8 MiB transport and read limit to accommodate
the supported inventory sizes and escaped Unicode paths. Only a completed export
is published; interruption or exceeding these bounds makes the new export
unavailable. Queries read an immutable local
file, return at most twenty tasks and twenty findings, and share a short execution budget. They never
read NAS paths. Use `basaltw scrub status HOST` for controller inspection and
`--all` to include resolved findings.
Passwords, mount credentials, raw PAR2 output, and recovery copies are not exported.

## Persist panel changes and controller configuration

Approved share and routine changes update the target's private saved setup.
Privileged management protects `/var/lib/basaltwater` directly. The installer
retains `/opt/basaltwater/state` as a convenience symlink to that directory;
symlinked privileged files or ancestors inside the durable directory are refused.
The audit exporter takes a shared read-only setup lock and inspects private
disposable copies of Samba's local databases, leaving its filesystem sandbox
read-only outside the export directory. Each database copy is limited to 16 MiB
and removed after inspection; credentials and hashes are never exported.
Restarting the panel or the machine retains them. Target-side `sudo basaltw refresh`
replays that saved setup; it rejects a configuration change before source
activation or during the handoff to the upgraded setup runner. Rerun refresh after
such a refusal to read the latest saved configuration.

Panel changes do not update a controller's cached configuration. Controller
`setup`, `patch`, deployment and `shares` commands apply their complete desired
configuration for the settings they manage. Before using one after panel edits,
reconcile its share and routine declarations with the target's saved
`/opt/basaltwater/state/setup.json` using your secure configuration workflow.
An older controller selection can restore removed shares, routine declarations
or Samba logins referenced by saved credentials. Inspect both selections before
reapplying them. Pause controls and retained execution history are target state.

## Create, edit or remove a share

On a supported non-root VM operator installation with privilege approvals,
choose **Add a share** or a share's **Edit access and users** link. Enter a logical
share name and target directory, choose read-only or read/write access and check
the existing Samba users who need access. The editor labels disabled logins;
membership does not enable them. Changing access changes the actual SMB name's
`_read`/`_write` suffix. To rename a share, add the replacement and remove the old
share separately. The removal review is inside **Remove this share**.

**Review share change** shows current and proposed directory, access and users.
Type the exact logical share name, create the request and approve or deny it on
the separate approval page. **Share activity** offers pending-request cancellation
and reports the correlated worker result. A dispatched request does not establish
completion. A successful worker applied and saved the selection; the inventory
updates at the next five-minute export. The latest worker record is bounded, so
older dispatched requests may require inspecting the actual share configuration.

Browser share management requires the complete saved inventory (up to 64 shares,
64 users per share, paths up to 512 characters) and supported local Samba accounts
as described below. The worker binds the review to saved-setup, main Samba config
and mount drop-in metadata, rechecks the exact prior share and existing account
eligibility, and shares the setup/refresh lock. Changed or unsupported reviews
fail before effects. Only one Samba approval request may be outstanding.

Reconciliation reapplies group membership and recursive permissions for **all**
saved managed shares. It validates and reloads Samba, then updates only saved
share declarations using the target's mount and internal-data guards. Passwords,
login flags, files, accounts, unrelated settings and manual share sections are
preserved. Removing a share removes its managed section and access group;
it does not remove its files or user accounts. Existing SMB sessions may need
to reconnect after a change.

The supervised job runs outside the panel with a one-hour deadline. A failure
after mutation begins can leave partial permissions, configuration or saved state
and retains the setup recovery marker. Inspect live Samba, permissions, mount
drop-in and saved setup using the
[transaction recovery guide](../operations/transaction-recovery.md); do not clear
the guard just to retry. Reviews expire after five minutes, submission consumes
them once, and lost responses or interrupted operations are never replayed
automatically. Controller commands remain available for unsupported inventories.

Validation errors retain the share directory, access choice and selected users.
A confirmation typo or temporarily busy approval service retains the same
five-minute review. If the snapshot changes or editing becomes unavailable, a
copyable unsaved draft appears instead of rebasing it onto newer configuration.
Once submission reaches the broker connection, its ticket is consumed even when
the response is lost; inspect activity before creating another request.

## Manage backup, sync and scrub routines

In **Backups & sync**, choose **Add backup mirror** or **Add sync mirror**. In
**Scrubs & findings**, choose **Add scrub task**. Enter absolute source/root and
destination/parity directories, choose the interval and schedule state, and for
scrubs choose parity redundancy from 1% through 100%. New routines default to
paused. **Edit routine** changes an existing declaration. The hourly timer checks
active routines; active scrubs also maintain parity daily. Biweekly is 14 days
and bimonthly is 60 days.

**Review routine change** shows current and proposed values. Type the confirmation
shown and create the request, then approve or deny it on the separate approval
page. Saving changes only configuration and the timer; it does not immediately
copy files or run PAR2. Activating a mirror allows later replacement and deletion
at its destination. Both mirror kinds lack retained versions. Mount declarations
must already exist. Active edits and manual runs require installed rsync/unshare
for mirrors or PAR2 for scrubs; use controller setup to provision missing tools.
The editor does not install packages.

**Pause schedule** and **Resume schedule** each require review and approval.
Pausing a scrub skips verification and parity maintenance but retains mirror
safeguards based on its open findings. **Remove routine** retains files, parity,
reports, completion history and logs. Removing a scrub also removes those mirror
safeguards; pause it when you want to retain them.

**Run once** requests only the selected existing routine, including a paused one.
It overrides its cadence and retry delay for that run while preserving its pause
state and all mount/integrity guards. A scrub run performs full verification and
staged repair, retains originals, updates parity and records findings. A mirror
run can replace and delete destination files. It does not run unrelated routines.

**Routine activity** separates approval state from the correlated worker result
and offers cancellation before execution is claimed. Dispatch is not completion.
Completed runs can report repairs/precautions or unresolved findings; inspect
logs and browse the updated integrity report. **Inspect routine execution over
SSH** provides the shared operation-log command; the scheduled service journal
does not contain the selected worker's output. Correlate times and paths.
The latest worker record is bounded,
so older requests can require inspecting history and actual files. The worker
has a six-hour deadline. Only one routine or file-recovery approval may be
outstanding, and these workers exclude one another.

Exported history belongs to each configured routine. A mirror completion does not
prove a recoverable backup or verify later changes. A **scan cadence marker** can
be an initial deferral; the integrity report establishes the last full scan.
Parity maintenance is separate from full verification and does not clear open
findings. Retry delays show the failure count and earliest scheduled retry time;
paused routines remain paused even after that time.

Reviews expire after five minutes. Validation errors retain entered paths;
confirmation typos retain the review. Submission consumes it before requesting
approval, so lost responses are not replayed. A changed saved configuration or
pause control rejects execution. Interrupted saves retain a setup recovery guard;
inspect and reconcile it using the [storage operations guide](../storage/storage-operations.md)
before starting more storage work. A complete supported export is required for
editing, while valid integrity reports remain available independently.

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

## Create or remove a Samba login

On the configured HTTPS panel, open **Add a Samba user**. **New username** creates
a local Unix identity for file ownership with a locked Unix password, a nologin
shell and no new home directory. **Existing Unix account** instead selects an
exported local account and preserves its Unix password, shell, home and groups.
That Samba login inherits the existing account's filesystem access. These are
separate reviewed operations; a new-account request never adopts a name that
appeared after review. System and external name-service accounts require the
administrator command line.

Review the exact username and identity choice, enter and repeat a 12–128 character
Samba password, type the username and create the approval request. The one-use
secret staging, expiry, separate approval and status flow are the same as password
reset below. Neither choice grants share membership. After the next five-minute
account export, edit the desired shares to grant access.

To remove a login, first edit every managed share that references it. After the
next export, **Remove Samba login** offers a separate review. Type the username
and request approval. Removal deletes the Samba database record and any matching
credential in the target's saved setup. It retains the Unix identity, password,
groups and files. The worker rechecks that no saved managed share references the
user, including legacy inline credentials. Manual share configuration is outside
this check. Existing SMB sessions may continue. Disabling is an alternative when
you want to suspend login while retaining membership.

Creation/removal require a complete identity export and saved share inventory;
unsupported or changed state refuses execution. The local Unix inventory supports
up to 512 regular accounts with UIDs 1000–65533, independently of the 512-record
Samba inventory. Creation binds review to saved setup, Samba configuration,
passdb and `/etc/passwd` metadata. The worker consumes a staged password before
creating a Unix identity and excludes concurrent setup/refresh.

These multi-step operations retain a setup recovery guard if interrupted after
mutation starts. Creation can leave a locked Unix identity or disabled Samba
record. Removal can delete the Samba record before its saved credential is
cleared. Inspect live identities, Samba accounts, saved setup and the recorded
phase using the [transaction recovery guide](../operations/transaction-recovery.md).
Do not clear the guard merely to retry. No automatic account deletion, rollback
or password replay follows an uncertain result. Both operations use the supervised
45-second account helper; **succeeded** means its final account checks passed.

Confirmation typos, password mismatches and a busy approval service retain the
five-minute review, with password fields cleared. Once staging or submission
starts, the ticket is consumed before the socket request; inspect activity after
a lost response rather than submitting it again.

## Reset a Samba password

On the configured HTTPS panel, choose **Reset Samba password** for a supported
account. Review the account, enter and repeat a new password of 12–128 characters,
type the exact username and create the approval request. Store the password
securely before submitting; the panel cannot retrieve it. Approve or deny on the
separate approval page. **Samba activity** reports the result and offers pending
request cancellation.

The new password goes through a separate authenticated local broker message into
a root-only `0600` file under `/run/basaltwater-privilege-broker/samba-passwords`
in a `0700` directory. Staging requires a `tmpfs` runtime filesystem. Approval
records and command arguments contain only public account metadata and an opaque
one-use job ID; they never contain the password or a password hash. The password
is not returned in HTML, exported or saved in the approval database.

Staging expires after one hour, or on broker restart, even if the approval policy
allows a longer wait. Expired files are swept within thirty seconds while the
broker is idle; execution always checks expiry before use. Denial, cancellation,
request expiry and terminal execution discard the staging file. An unconfirmed
submission is not replayed. Inspect existing requests before starting again.

The root helper consumes the secret before effects and checks the reviewed
account, saved Samba enablement and setup exclusion. It also binds the reset to
Samba configuration, local account and entire passdb metadata at staging time.
Any intervening Samba database write rejects that reset, including changes to
another account. This is stricter than the per-account login-flag review.

The helper uses Samba's Python passdb bindings and the local `tdbsam` backend
directly, without invoking Unix or PAM password tools. The original account flags
and new password are saved in the same database update, then account identity and
login/lockout flags are checked again. The bindings must be installed (normally
provided by Samba's package dependencies). Samba's
[passdb bindings](https://github.com/samba-team/samba/blob/master/source3/passdb/py_passdb.c)
expose the account password setter and database update used here. The helper
avoids `smbpasswd`'s local password-change flow, which can enable a disabled account.

Reset changes only the Samba password. It does not enable a disabled login,
clear lockout, change Unix/SSH passwords or change share membership. Existing SMB
sessions may continue; clients may need to reconnect using the new password.
The supervised helper has the same 45-second limit as login controls. A failed
or uncertain result requires checking the account before trying again; the old
password is not retained for rollback.

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
Input errors retain the recovery action and independent-copy path for correction.
Confirmation typos or a temporarily busy service retain the same review until it
expires. Once the submission reaches the broker connection, the ticket is consumed;
inspect activity after an uncertain response instead of replaying the review.
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
