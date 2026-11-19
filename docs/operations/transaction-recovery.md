# Interrupted setup and deployment recovery

Basaltwater records a private operation marker before changing managed state.
An unfinished marker blocks another operation at that boundary. A kernel lock
also blocks recovery while its owner is running. Keep stable `.lock` files in
place: deleting them can allow two processes to own the same resource.

These records are not backups of the whole machine. Package installs, arbitrary
build scripts, and application data changes are not automatically reversed.
Deployment rollback restores release files and managed units; SQLite restoration
is a separate operator decision that can discard newer writes.

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

## Choose a topic

<a id="locate-the-evidence"></a>
<a id="recover-by-phase"></a>

- [Recover interrupted setup by phase](transaction-recovery-phases.md) Find evidence, identify the interrupted phase, and verify recovery.

<a id="compressed-swap-recovery"></a>
<a id="resume-device-initramfs-recovery"></a>

- [Recover compressed swap and resume configuration](transaction-recovery-swap-and-resume.md) Follow the recovery procedures for compressed swap and the resume-device initramfs.
