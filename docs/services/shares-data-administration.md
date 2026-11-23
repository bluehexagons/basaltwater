# Shares & Data administration

[Topic index](shares-data.md)

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
| Concurrency | One Samba approval request and one routine approval request. Routine and file-recovery workers share the storage lock |

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
| Recovery controls unavailable but findings visible | Check `basaltwater-data-recovery.socket`, the `basaltwater-data-recovery.service` journal and setup recovery state. Rerun saved panel setup after updating to provision the recovery service |
| Share, account or routine controls unavailable | Check approvals, complete configuration export and setup recovery state |
| Request dispatched without a matching worker result | Inspect the request, worker and actual state; bounded latest records cannot prove older completion |
| Catalog, health or account inspection unavailable | Check mounts, exporter journal and supported limits; rerun setup/refresh if the exporter is older |
| Failed, interrupted or uncertain change | Inspect actual state and follow [transaction recovery](../operations/transaction-recovery.md) before retrying |
