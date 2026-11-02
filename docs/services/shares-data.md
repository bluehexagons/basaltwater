# Shares & Data in the web panel

Open **Administration → Shares & Data** on the machine you want to manage.

| View | Use it to |
| --- | --- |
| **Overview** | Summarize configured shares, mirrors, integrity findings and paused schedules; start a task from a template |
| **Samba shares** | Publish shares, choose their users, and manage Samba logins and passwords |
| **Backups & sync** | Configure recurring rsync mirrors, pause schedules and run individual routines |
| **Scrubs & findings** | Browse integrity findings, recover individual files, inspect task health and configure scrubs |

The main link opens **Overview**. Samba is hidden when the exporter confirms
`smbd` is absent. Empty mirror and scrub views are hidden from navigation when
there are no declarations, reports or retained activity. Setup templates remain
available to create the first task. Unavailable inventory is shown as unknown,
rather than being treated as empty. Older exporters use their saved Samba
selection until updated.

**On this page** jumps between sections. The scrub view puts findings first;
selecting an editor brings that form forward. **Activity & approvals** groups
requests, cancellations and worker results. It opens for recorded activity,
unavailable controls and page errors. The page works without JavaScript.

The inventory refreshes every five minutes. **Refresh snapshot** reads the latest
export; it does not scan files or start jobs. Exports older than 15 minutes are
unavailable. A successful change can therefore appear in activity before the
inventory catches up.

## Review, approve and check the result

Browser changes require the [privilege approval service](../agents/privilege-approvals.md),
installed with the main web panel by default. `--no-privilege-broker` disables it.
Account creation and password
entry also require the configured HTTPS panel.

1. Choose an action and review its exact target and effects.
2. Enter the displayed confirmation and create an approval request.
3. Open that request's approval review and approve or deny it using the panel login.
4. Return to **Activity & approvals**, refresh and check the worker result.

A panel review does not grant approval. **Dispatched** means a worker was
launched; it does not establish completion. Cancel an unclaimed request from
activity. Cancellation cannot undo changes already made.

Reviews last five minutes and submit once. Input errors retain editable values;
confirmation errors retain the review. If configuration changes or editing
becomes unavailable, share and routine forms show a copyable draft rather than
applying old inputs to a new revision. Password fields always clear after errors.
After a lost response, inspect activity and actual state before creating another
request; operations are never replayed automatically.

If Samba is removed while a share edit is open, the page returns to Overview
and retains the entered values as a copyable draft.

Failed or interrupted multi-step changes can retain a setup recovery guard.
Follow [transaction recovery](../operations/transaction-recovery.md) before
retrying; do not clear the guard just to enable another operation.

## Create, edit or remove a share

Choose **Add a share** or **Edit access and users**. Enter a logical name and
absolute directory, choose read-only or read/write access, and select existing
Samba users. Disabled users remain disabled. The actual SMB name ends in
`_read` or `_write`; changing access changes that name. To rename a share,
add its replacement and remove the old share.

**Review share change** compares current and proposed settings. Confirm with
the logical share name. **Remove this share** contains the removal review.

Saving reconciles access groups and recursive permissions for **all** managed
shares, validates/reloads Samba and updates the target's saved share declarations.
It preserves passwords, login flags, files, accounts and manually configured
share sections. Removal deletes the managed share and access group, retaining
files and accounts. Existing SMB sessions may need to reconnect.

The inventory covers saved managed shares, not manual shares, active sessions
or filesystem ACLs. Use the [Samba guide](../storage/samba-shares.md) for controller
configuration and unsupported cases.

## Manage Samba users and passwords

Use **On this page → Accounts and passwords** for Samba logins, independently
of share membership.
Browser controls require a regular, password-protected local Samba account
with a matching Unix UID from 1000 through 65533. System, trust and external
name-service accounts require the administrator command line.

| Action | Effect |
| --- | --- |
| **Add a Samba user → New username** | Create a Unix identity with a locked Unix password, nologin shell and no new home; then add its Samba login |
| **Add a Samba user → Existing Unix account** | Preserve its Unix password, shell, home and groups; the Samba login inherits its filesystem access |
| **Review enable/disable Samba login** | Change Samba's disabled flag; preserve the password, Unix account and share membership |
| **Reset Samba password** | Change only the Samba password; preserve disabled/lockout flags, Unix/SSH passwords and membership |
| **Remove Samba login** | Delete its Samba record and matching saved credential; retain the Unix identity, groups and files |

New and existing identity choices are reviewed separately; a new-user request
never adopts an account that appeared after review. Neither choice grants share
membership. After the next export, edit the desired shares to grant access.

Before removing a login, remove it from every managed share and wait for the
next export. The worker checks saved declarations, including legacy inline
credentials; manual share configuration is outside this check. Disable login
when you want to retain membership. Enabling does not clear lockout, and disabling,
removing or resetting a login may leave existing SMB sessions connected.

For creation or reset, review the username, enter and repeat a 12–128 character
password, then type the username to confirm. Store the password securely before
submitting; the panel cannot retrieve it. Clients may need to reconnect.
Creation/removal can leave partial state if interrupted; inspect live identities,
Samba and saved setup using the transaction recovery guide.

## Manage backup, sync and scrub routines

Start with **Overview → Set up a data task** for common defaults:

| Template | Interval | Parity |
| --- | --- | --- |
| Daily sync | Daily mirror | — |
| Weekly backup mirror | Weekly mirror, without retained versions | — |
| Weekly integrity check | Weekly full scan; daily parity maintenance when active | 10% |
| Monthly archive check | Monthly full scan; daily parity maintenance when active | 10% |

Each template opens an editable form with blank directories and a **paused**
schedule. Set the paths, adjust the defaults, review and approve the routine,
then resume it when ready. Choosing a template only fills a form; it creates no
request and starts no host operation. Templates do not change existing routines
or overwrite values retained after an input error. The browser needs the same
approval service and complete export as other routine changes. On hosts without
browser management, use the controller setup options in the
[storage operations guide](../storage/storage-operations.md).
When controls or inventory are unavailable, the template opens a preview of
its interval, paused schedule and parity settings, with the reason editing is
unavailable and a link to controller setup. Refresh after resolving the reason
to enter directories and review it.

Choose **Add backup mirror**, **Add sync mirror** or **Add scrub task**.
Enter absolute directories, an interval and schedule state; scrubs also take
1%–100% parity redundancy. New routines default to paused. **Edit routine**
changes a saved declaration. Saving updates configuration and the timer without
immediately copying files or running PAR2.

Both backup and sync declarations are **rsync mirrors**: replacement and deletion
can propagate to the destination. They provide no snapshots or retained versions.
Mounts must already be declared. Use controller setup to provision rsync/unshare
for active mirrors or PAR2 for active scrubs; the editor does not install tools.

| Control | Behavior |
| --- | --- |
| **Pause schedule** | Stop scheduled execution; paused scrubs also skip parity maintenance while retaining mirror safeguards for open findings |
| **Resume schedule** | Allow the hourly timer to run the routine when due |
| **Run once** | Run only this existing routine, even if paused; override cadence/retry delay for that run while retaining pause state and mount/integrity guards |
| **Remove routine** | Retain files, parity, reports, history and logs; removing a scrub also removes its mirror safeguards |

Each action requires review and approval. Active scrubs maintain parity daily
and perform full verification/staged repair on their scan interval, retaining
originals and recording unresolved findings. Biweekly means 14 days; bimonthly
means 60 days. Pause a scrub instead of removing it to retain its safeguards.

History belongs to the selected routine. A mirror completion does not prove a
recoverable backup. A scan cadence marker can be an initial deferral; **Task health**
establishes the last recorded full scan. Parity maintenance does not clear open
findings. Retry history shows consecutive failures and the earliest retry time;
a paused schedule stays paused.

Inspect the correlated worker result in **Activity & approvals**. Runs can finish
with repairs/precautions or unresolved findings. **Inspect routine execution over
SSH** opens the shared operation-log command; the scheduled service journal does
not contain selected-worker output. Correlate times and paths. See
[storage operations](../storage/storage-operations.md) for scheduling and guards.
The latest worker result remains visible after its approval history expires.
Without matching approval details, the page cannot identify its routine or
whether it changed configuration or ran a task; inspect logs and actual state.

## Review and resolve one finding

In **Scrubs & findings**, search file paths and categories under **Browse findings**.
**Task health** has a separate scrub-root search. **Browse this task’s findings**
selects its exact root and parity directory; **All scrub tasks** clears that
selection. Each list shows twenty rows per page. Filtering, clearing and pagination
preserve the other section's filters. Searches accept 200 Unicode characters,
match case-insensitively and treat punctuation literally.

Open **Inspect evidence and recovery options** on a finding. Browser actions are
followed by a controller command for reading its private evidence:

| Action | Effect / confirmation |
| --- | --- |
| **Verify file** | Check existing parity and update the finding; no typed confirmation |
| **Repair file** | Reconstruct and verify a staged candidate, retain originals; type `CHANGE FILE` |
| **Restore file** | Stage an independent copy from an absolute path on this host, verify against parity and retain originals; type `CHANGE FILE` |
| **Accept current content** | Replace the integrity baseline with content you independently confirmed is correct; type `ACCEPT CURRENT CONTENT` |

Choose **Review recovery**, check the file, scrub root, parity directory and copy
path, then follow the approval flow above. The worker rejects changed findings
or changed file identities. A missing-file scan records absence as its reviewed
identity; a file appearing afterward blocks recovery. Verify older findings
without an identity before recovery. Verification that still detects damage
reports **findings**, not success.
No bulk acceptance, deletion or unattended remediation is provided.

A recorded scan does not verify later file changes. Missing mounts, unreadable
reports and unsupported metadata show incomplete/unavailable results rather than
healthy or empty results. Invalid configuration or task-health data cannot hide
valid findings. Use `basaltw scrub status HOST` for controller inspection and
`--all` for resolved findings. See [scrub recovery](../storage/scrub-recovery.md)
for evidence, staged repair and recovery decisions.

## Persist panel changes and controller configuration

Approved share and routine changes survive panel/host restarts in the target's
private saved setup. Target-side refresh replays it and rejects
configuration changes during source activation/handoff; rerun after such a refusal.
Use the installed launcher with root privileges, or
`sudo python3 -I /opt/basaltwater/basaltwater.py refresh` when the user launcher
is outside sudo's command search path.
Refresh requires every referenced Samba account to exist and preserves its
password and login flags. It does not require saved plaintext share credentials;
use explicit setup or account management to provision a missing login.

Panel edits do **not** update a controller's cached configuration. Before applying
controller setup, patch, deployment or shares commands, reconcile their desired
share/routine declarations with `/var/lib/basaltwater/setup.json` through your
secure configuration workflow. An older selection can recreate removed shares,
routines or logins referenced by saved credentials. Pause controls and execution
history remain target state.

## Supported limits and safeguards

| Area | Boundary |
| --- | --- |
| Share editing | Complete inventory; up to 64 shares, 64 users each, paths up to 512 characters |
| Account management | Standalone local `tdbsam`; up to 512 Samba accounts and independently 512 regular Unix identities |
| Routine editing | Complete inventory of up to 256 total backup, sync and scrub declarations |
| Older summary | Up to 64 shares, 128 mirrors, eight scrub tasks and twenty findings per task; omitted records are labelled |
| Complete catalog | Health/findings from all configured scrub tasks; twenty tasks and twenty findings per query |
| Export bounds | Twenty-second collection deadline, 128 MiB catalog, 8 MiB summary/transport; only completed exports publish |
| Worker deadlines | Accounts: 45 seconds; share changes: one hour; routine changes/runs and recovery: six hours |
| Concurrent approvals | One Samba request; one routine/file-recovery request, with those workers excluding each other |

Privileged management uses `/var/lib/basaltwater`; `/opt/basaltwater/state` is a
convenience symlink. Privileged files or ancestors inside the durable directory
must not be symlinked. Workers share setup/refresh exclusion and recheck reviewed
configuration/account/file metadata before effects. File recovery holds setup
ownership from configuration loading through completion; unfinished setup guards
block controls, dispatch and execution.

The exporter uses a shared read-only setup lock and disposable Samba database
copies of at most 16 MiB each. HTTP requests query immutable local metadata with
a short budget; they never read NAS paths or private recovery evidence.
Passwords, mount credentials, hashes, raw PAR2 output and recovery copies are
not exported. Missing/corrupt/mismatched catalogs are unavailable. Version-one
catalogs retain complete findings but need an exporter update for complete health;
older exporters retain only a labelled preview.

Creation/reset passwords use one-use root-only staging on tmpfs under
`/run/basaltwater-privilege-broker/samba-passwords` (files `0600`, directory
`0700`). Approval records and argv contain public metadata and an opaque job ID.
Staging lasts at most one hour, is discarded on broker restart, and is removed
after denial, cancellation, expiry or execution. Idle expiry sweeps run within
thirty seconds; execution checks expiry before use. Any intervening Samba passdb
write, including another account's change, rejects the staged password.
The helper consumes it before effects and updates Samba directly through its
Python passdb bindings, preserving login/lockout flags without Unix/PAM changes.
No old password is retained for rollback.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Lists still show old values after success | Wait for the five-minute export, then refresh |
| Controls unavailable but findings visible | Check approvals, complete configuration export and setup recovery state |
| Request dispatched without a matching worker result | Inspect the request, worker and actual state; bounded latest records cannot prove older completion |
| Catalog, health or account inspection unavailable | Check mounts, exporter journal and supported limits; rerun setup/refresh if the exporter is older |
| Failed, interrupted or uncertain change | Inspect actual state and follow [transaction recovery](../operations/transaction-recovery.md) before retrying |
