# Manage shares, accounts, and routines

[Review, approve and check the result](shares-data.md#review-approve-and-check-the-result)
before applying changes. Enter account passwords through the configured HTTPS panel.

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
