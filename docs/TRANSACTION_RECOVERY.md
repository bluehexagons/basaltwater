# Interrupted setup and deployment recovery

Basaltwater records a private operation marker before changing managed state.
An unfinished marker blocks another operation at that boundary. A kernel lock
also blocks recovery while its owner is running. Keep stable `.lock` files in
place: deleting them can allow two processes to own the same resource.

These records are not backups of the whole machine. Package installs, arbitrary
build scripts, and application data changes are not automatically reversed.
Deployment rollback restores release files and managed units; SQLite restoration
is a separate operator decision that can discard newer writes.

## Locate the evidence

| Boundary | Marker | Recovery evidence |
| --- | --- | --- |
| Target setup | `/var/lib/basaltwater/setup-operation.json` | Current step, profile, username and error type |
| Manifest release | `/var/www/.basaltwater_shared/<app>/manifest-operation.json` | Staging/previous/failed paths, previous ports, desired units, adjacent `manifest-units.previous.json` |
| Static release | `/var/www/.basaltwater_shared/<app>/static-operation.json` | Staging/previous/failed paths |
| Systemd replacement | `/etc/systemd/system/.basaltwater-unit-operation.json` | `backup_dir/previous.json` with old files, modes, ownership, and activation states |

Release paths follow the configured deployment base. Static and manifest entry
points share a deployment lock and check both markers, so switching deployment
formats cannot bypass unfinished recovery.

Each completed operation writes a private `<marker>.last.json` result before
removing its marker. It contains the operation ID, the most recent 256 phases,
completion time, final context and `succeeded`, `rolled_back`, or `failed`
outcome. This is bounded last-result evidence, not a permanent audit archive.
An existing result file does not block a new operation.

Stop concurrent deployment jobs and establish that the original process has
exited before repairing state. Privately preserve the marker, its backups, the
last result and relevant logs. Inspect a marker locally, for example:

```bash
sudo python3 -m json.tool /var/lib/basaltwater/setup-operation.json
```

Do not publish private snapshots or raw error contexts in support reports.

## Recover by phase

| Operation and phase | What may have changed | Recovery action |
| --- | --- | --- |
| Setup `applying` or `recovery` | Earlier steps and part of the named step | Inspect that step's output and actual state. A handled `recovery_required` failure can rerun the matching profile, machine type and username; the full idempotent plan runs again. A hard-kill `in_progress` marker requires explicit review first. |
| Setup `finalizing` | Machine state may be saved before setup configuration | Reconcile both saved files with the actual host before retrying. Individual atomic writes do not make these files one filesystem transaction. |
| Manifest `preparing` or `building`; static `preparing` or `staging` | Temporary artifacts and prerequisite accounts/tooling | The release has not been switched. Verify the old release and units, then archive or remove the recorded staging tree after confirming it is unused. |
| Release `activating` | Old services may be stopped; the old tree may be at `backup_path`; destination may be absent or new | Inspect all paths. Restore the previous tree if displaced, then restore saved units and active states. Never assume a rename happened solely because its phase was recorded. |
| Manifest `verifying`; static `finalizing` | New release is active; startup and final persistence may be incomplete | Choose between verifying the new release and restoring the old one. For manifest rollback, stop new units, restore the previous tree and unit snapshot, reload systemd, then restore recorded enablement and running state. Restore `previous_ports` too. |
| Release `rolling-back` or `recovery` | Restoration itself may be interrupted | Inspect destination, backup and failed paths before moving anything. A backup may already be consumed by restoration. Check every recorded unit and port assignment; retain the rejected tree until recovery is verified. |
| Units `staging` or `validating` | Private candidates and snapshots | Live files have not been replaced. Verify existing units, preserve or remove unused staging artifacts, then resolve the marker. |
| Units `replacing` or `rollback-failed` | Some files and activated units may have changed | Restore `previous.json`: remove files recorded as null, restore others with recorded mode/owner, reload systemd, and restore enabled/runtime-enabled and active states. Do not restart unrelated executing oneshots. |

For a first deployment with no previous release, recovery removes the rejected
new release and new units. For manifest recovery, `previous_ports: null` means
the port file was absent; remove the new file rather than retaining assignments
from a rejected release. Legacy markers may omit newer context fields: use
retained snapshots and actual service configuration instead of guessing.

## Verify before resolving the marker

Verify restored contents, permissions and ownership. Use `systemctl show` to
compare enablement and running state with snapshots, and test declared health
endpoints using direct loopback requests. Previously inactive units should stay
inactive. Validate Nginx separately if routing changed. Use
[deployment safety](DEPLOYMENT_SAFETY.md#recovery-path) for SQLite restoration.

After verified recovery, privately archive the marker and snapshots or use
`OperationStateStore.complete(recorded_operation_id, outcome="rolled_back")`
from the target's installed Basaltwater environment. Completion acquires the
same kernel lock and checks the operation ID; a running owner or stale ID
prevents removal. Keep the `.lock` file. Never clear a marker solely to make
another setup or deployment proceed.

If a state reader reports corrupt JSON or an unsupported schema, preserve the
file. Restore a verified backup or quarantine it only after reconciling actual
state and deciding that fresh defaults are appropriate. Missing state and
corrupt state have different meanings. Reader errors name the path and do not
print its contents.
