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
| Selected-builder controller setup | `<controller-workspace>/build-setups/<host-hash>/setup.json` | Pinned prepared inputs, delivery identities and expected releases in the recorded attempt directory |
| Controller artifact delivery | `<attempt>/delivery/delivery.json` | Request and reservation keys, target generations, artifact identities and locally saved acknowledgements |
| Manifest release | `/var/www/.basaltwater_shared/<app>/manifest-operation.json` | Staging/previous/failed paths, previous ports, desired units, adjacent `manifest-units.previous.json` |
| Static release | `/var/www/.basaltwater_shared/<app>/static-operation.json` | Staging/previous/failed paths |
| Systemd replacement or removal | `/etc/systemd/system/.basaltwater-unit-operation.json` | `backup_dir/previous.json` with old files, modes, ownership, and activation states |
| Deployment Nginx reconciliation | `/etc/nginx/.basaltwater-nginx-operation.json` | `backup_dir/previous.json` with previous site files, links and selected generated certificate/key files |
| Proxmox guest migration | `<controller-workspace>/migrations/<cluster-CA-prefix>-<VMID>.json` | Source/destination identity, original power state, verified disk plan, and narrow saved-setup updates |

Release paths follow the configured deployment base. Static and manifest entry
points share a deployment lock and check both markers, so switching deployment
formats cannot bypass unfinished recovery.

Each completed operation writes a private `<marker>.last.json` result before
removing its marker. It contains the operation ID, the most recent 256 phases,
completion time, final context and `succeeded`, `rolled_back`, or `failed`
outcome. This is bounded last-result evidence, not a permanent audit archive.
An existing result file does not block a new operation.

A retained marker takes precedence over last-result evidence. The result is
written before marker removal, so interrupted or failed finalization can leave
a `succeeded` result beside an unresolved marker. Inspect and reconcile the
marker before retrying, even when the last result appears successful.

Stop concurrent deployment jobs and establish that the original process has
exited before repairing state. Privately preserve the marker, its backups, the
last result and relevant logs. Inspect a marker locally, for example:

```bash
sudo python3 -m json.tool /var/lib/basaltwater/setup-operation.json
```

Do not publish private snapshots or raw error contexts in support reports.
Nginx snapshots contain private TLS keys encoded as base64; encoding does not
make them safe to share.

For an unfinished selected-builder setup, inspect its local metadata without
contacting the builder or target:

```bash
python3 -B -m lib.build_setup_recovery --journal /absolute/path/to/setup.json
```

Run this from the controller's installed environment as the evidence owner.
`-B` prevents interpreter imports from creating bytecode caches. The report
distinguishes saved local acknowledgements from unresolved outcomes; it does
not establish current remote state or verify payload contents. Preserve the
guard and exact inputs for explicit reconciliation. See
[controller setup](../agents/contributing/build-setup.md) for the inspection
contract and limitations.

## Recover by phase

| Operation and phase | What may have changed | Recovery action |
| --- | --- | --- |
| Setup `applying` or `recovery` | Earlier steps and part of the named step | Inspect that step's output and actual state. A handled `recovery_required` failure can rerun the matching profile, machine type and username; the full idempotent plan runs again. A hard-kill `in_progress` marker requires explicit review first. |
| Setup `finalizing` | Machine state may be saved before setup configuration | Reconcile both saved files with the actual host before retrying. Individual atomic writes do not make these files one filesystem transaction. |
| Manifest `preparing` or `building`; static `preparing` or `staging` | Temporary artifacts and prerequisite accounts/tooling | The release has not been switched. Verify the old release and units, then archive or remove the recorded staging tree after confirming it is unused. |
| Release `activating` | Old services may be stopped; the old tree may be at `backup_path`; destination may be absent or new | Inspect all paths. Restore the previous tree if displaced, then restore saved units and active states. Never assume a rename happened solely because its phase was recorded. |
| Manifest `verifying`; static `finalizing` | New release is active; startup and final persistence may be incomplete | Choose between verifying the new release and restoring the old one. For manifest rollback, stop new units, restore the previous tree and unit snapshot, reload systemd, then restore recorded enablement and running state. Restore `previous_ports` too. |
| Release `rolling-back` or `recovery` | Restoration itself may be interrupted | Inspect destination, backup and failed paths before moving anything. A backup may already be consumed by restoration. Check every recorded unit and port assignment; retain the rejected tree until recovery is verified. |
| Units `staging` or `validating` | Private candidates and snapshots | Live files and running states have not changed. Verify existing units, preserve or remove unused staging artifacts, then resolve the marker. |
| Units `replacing`, `removing`, `rolling-back` or `rollback-failed` | Some files and activated/stopped units may have changed | Restore `previous.json`: remove files recorded as null, restore others with recorded mode/owner, reload systemd, and restore enabled/runtime-enabled and active states. Restore the service before rearming its timer/path. Do not restart unrelated executing oneshots. |
| Nginx `staging` | Snapshot preparation | Deployment sites and TLS files have not changed. Inspect any recorded snapshot before resolving the marker. |
| Nginx `applying`, `verifying`, `rolling-back` or `recovery` | Sites, enabled links, TLS files and loaded workers may differ | Restore each absolute path in `previous.json`: null means previously absent; regular-file contents are base64 with mode/uid/gid; links record target/uid/gid. Restore all files and links before validation. Run `nginx -t`, reload Nginx and verify activity and routing before resolving the marker. |
| Proxmox migration `prepared`, `shutting_down`, or `stopped` | Guest may have shut down; disk transfer has not been issued | Inspect native tasks and power state, then use the same `proxmox migrate` command with `--resume`. Shutdown is graceful and verified before transfer. |
| Proxmox migration `migrating` | Native disk transfer may be incomplete or finished despite a lost SSH result | Inspect both nodes and the native task log. `--resume` refuses active tasks and never replays an uncertain transfer that still has the guest on the source. A verified destination can continue without copying again. |
| Proxmox migration `verifying`, `updating_setups`, or `starting` | Guest may be on the destination; some saved bindings may be updated; startup may have finished | `--resume` rechecks destination identity/disks and source config/process absence, preserves concurrent edits, accepts already applied cache updates, and restores the original power state. |

For a first deployment with no previous release, recovery removes the rejected
new release and new units. For manifest recovery, `previous_ports: null` means
the port file was absent; remove the new file rather than retaining assignments
from a rejected release. Legacy markers may omit newer context fields: use
retained snapshots and actual service configuration instead of guessing.

Rollback attempts independent service restorations even after another unit
fails. It does not restart a unit whose old file could not be restored; failed
`daemon-reload` also prevents restarting against cached replacement definitions.
Within a grouped unit transaction, any failed file restoration prevents
restarting the group so a restored timer/path cannot activate an unrestored
service. Removal uses the same lock and snapshot boundary as replacement,
skips absent managed files, and stops all present units before deleting any
of their files.
Socket activators are stopped before their service templates. Templates have
file snapshots but no activation-state entry, since an abstract template cannot
be queried or started; the owning socket stops concrete login instances through
their declared dependencies. Restore template files before rearming the socket.
Services waiting after a failed start (`activating` / `auto-restart`) can be
replaced so setup can repair their configuration. Actual starting, stopping and
reloading states still block changes. If replacement fails, rollback restores
the old definition and enablement but does not restart a previously broken
service that it stopped. Removal verifies that automatic retries have stopped
before deleting service files.
Recurring maintenance timers can preserve an unchanged group without creating
a new operation result, rewriting units, reloading systemd or restarting the
timer. This path holds the same ownership lock and refuses an unfinished marker.
It requires matching contents, mode and ownership for every unit, matching loaded
fragment paths with no pending daemon reload, and active, persistently enabled
activators. Missing observations or drift use the ordinary replacement boundary.
This opt-in applies only to timer/path activation without preparation callbacks
or deactivation. Explicit service activation still restarts the service because
its executable or external configuration can change independently of unit text.
A rename sync error can occur after the old release was restored: inspect the
actual paths and service states rather than treating the error as proof that
the old tree is still at its backup path. Port or marker-completion failures
retain the rejected release and private unit snapshots for further recovery.

A manifest can also leave a systemd replacement or removal marker. Reconcile
both layers before resolving either marker. The inner unit snapshot records
the state at that individual unit operation, after manifest activation may
have stopped the old service; the manifest snapshot records its state before
deployment. Use the chosen release and the manifest snapshot to determine the
final running state, then verify every unit and resolve both recorded operation
IDs. A restored release tree alone does not complete an unresolved inner unit
transaction.

Nginx has its own recovery boundary. A release may have committed before Nginx
reconciliation failed; repairing Nginx does not restore that release or its
database. A reload timeout can occur after workers accepted the new
configuration, so restored files alone are insufficient: validate and reload
the chosen configuration, then verify requests. Failed file restoration blocks
automatic reload, and incomplete recovery retains the private snapshot.

If setup cannot persist its failure details, the original error is still
reported and the marker may remain `in_progress`. Inspect it explicitly before
retrying. A matching automatic retry also refuses a marker that changed after
it was inspected, even when its operation ID is unchanged.

Proxmox migration recovery belongs to the controller workspace that issued the
move. Keep the native task log, marker, and `.last.json` evidence. Do not force
unlock a guest or start a source copy to work around an unfinished checkpoint.
Repeat the original migration options, including storage and timeouts, with
`--resume --dry-run` before applying recovery; see the
[migration workflow](proxmox.md#placement-backups-and-migration).
An uncertain transfer left on the source requires native recovery and explicit
verification of the source configuration, disks, power state, destination
artifacts, and absence of active tasks before resolving its marker. Resolve it
from the controller's installed environment, not from a guest. Saved bindings
are per-file atomic updates rather than one multi-file transaction; resumed
verification finishes partial updates without reverting unrelated current edits.

## Verify before resolving the marker

Verify restored contents, permissions and ownership. Use `systemctl show` to
compare enablement and running state with snapshots, and test declared health
endpoints using direct loopback requests. Previously inactive units should stay
inactive. Validate Nginx separately if routing changed. Use
[deployment safety](../services/deployment-safety.md#recovery-path) for SQLite restoration.

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
Only a confirmed missing marker permits a new operation; permission and I/O
errors while looking it up require inspection and never clear its guard.
Source refresh also holds the target setup marker's kernel lock during source
staging and activation, excluding setup mutations until the fresh runner can
claim its own operation. A competing setup can win that later claim; refresh
then reports the conflict rather than running two setup plans together.
