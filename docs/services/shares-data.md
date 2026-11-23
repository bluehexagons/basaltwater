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
there are no declarations, reports, native Btrfs/ext4 observations or retained activity. Setup templates remain
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

Share, account and routine changes require the [privilege approval service](../agents/privilege-approvals.md),
installed with the main web panel by default. `--no-privilege-broker` disables it.
Account creation and password
entry also require the configured HTTPS panel.

For share, account and routine changes, follow these steps.

1. Choose an action and review its exact target and effects.
2. Enter the displayed confirmation and create an approval request.
3. Open that request's approval review and approve or deny it using the panel login.
4. Return to **Activity & approvals**, refresh and check the worker result.

**Dispatched** means a worker was
launched; it does not establish completion. Cancel an unclaimed request from
activity. Cancellation cannot undo changes already made.

Scrub recovery starts from the panel review using the local recovery service,
including when the approval broker is disabled. Review the exact paths, enter
the displayed confirmation and check the job under **Recovery activity**.

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

## Choose a topic

<a id="create-edit-or-remove-a-share"></a>
<a id="manage-samba-users-and-passwords"></a>
<a id="manage-backup-sync-and-scrub-routines"></a>

- [Manage shares, accounts, and routines](shares-data-manage.md) Create shares and manage Samba users, backups, sync, and scrubs.

<a id="review-and-resolve-one-finding"></a>
<a id="triage-selected-findings"></a>
<a id="inspect-btrfs-filesystem-health"></a>
<a id="inspect-other-linux-filesystems"></a>

- [Review storage findings](shares-data-findings.md) Triage findings and inspect Btrfs and other Linux filesystems.

<a id="persist-panel-changes-and-controller-configuration"></a>
<a id="supported-limits-and-safeguards"></a>
<a id="troubleshooting"></a>

- [Shares & Data administration](shares-data-administration.md) Persist configuration, review safeguards, and troubleshoot.
