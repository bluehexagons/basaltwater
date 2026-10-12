# Privilege execution qualification

## Status: implementation delivered; refresh recovery qualification active

Repair approved command execution on broker-managed Debian VMs and give human
review a practical expiry window. The observed VM initially had a 64 GiB home
disk with a 32 GiB ext4 partition; its separately approved online expansion is
now qualified below.

The original broker executed arbitrary approved argv inside its own
`ProtectSystem=strict` / `PrivateDevices` / `ProtectHome` sandbox. Approval of
`install -d /srv/...` therefore succeeded while execution failed. The original
five-minute default expiry also regularly elapsed before an operator could review
the request. [Privilege approvals](../PRIVILEGE_APPROVALS.md) and the
[administrator reference](../PRIVILEGE_BROKER_REFERENCE.md) own durable behavior.

## Implementation and acceptance

- Retain the broker and approval service sandboxes. Dispatch only already
  approved command requests to a root-owned helper in a bounded transient systemd
  unit. Preserve literal argv, clean environment, closed interactive input,
  discarded privileged output, durable claims and no automatic replay.
- Expose safe exit/failure metadata without command output; preserve the audit
  database across schema migration and interrupted execution.
- Use a one-hour review default, permit an administrator-selected 30-second to
  24-hour policy window, and retain existing policy choices on setup reruns.
- Cover approval, rejection, literal argument transport, executor ownership,
  failures/timeouts, database migration, expiry, and existing setup preservation.
  Run focused tests, `make check`, and `make coverage` in the project environment.
- Retain a separate readable source-activation receipt when refresh converts a
  setup snapshot to a managed checkout with private Git metadata. Diagnostics
  must identify recorded activation separately from observed Git and successful
  setup; never expose private paths, branch names, or configuration.
- Publish tested source, refresh the VM through its existing approved maintenance
  operation, then exercise the actual approved `/srv` directory creation. Update
  this VM's expiry explicitly and verify it on a fresh request.
- Confirm the home disk layout before growing `/dev/sdb1`, back up the partition
  table, grow only into its adjacent free space, grow ext4 online, and verify
  mount identity, partition start, capacity and host health afterwards.

Artifacts remain in the active ignored Antistatic project workspace. Fold lasting
contracts into the references after implementation and retain unresolved live
checks here until qualification completes.

## Implementation evidence

The executor, review-window defaults, safe execution metadata and audit migration
are implemented. A response-size preflight rejects requests whose duplicated
review argv would exceed the socket protocol limit before persistence. Broker and
approval confinement remain enabled. The deployed executor creates the private
`/srv` artifact parent successfully with exit code zero, and new VM requests
have a 3,600-second review window. The confirmed home partition and ext4 grew
online to the enlarged 64 GiB drive with their original start and UUIDs. Private
text and binary partition-table backups remain in the broker state directory;
host/container-storage diagnostics report healthy capacity and maintenance.

The approved refresh activated source but its setup replay failed. Recovery
qualified the new executor without replaying setup. Normalize both downloaded
snapshot code and subsequent managed-checkout runtime updates under the private
job umask, retaining private metadata and runtime data. The 26 focused refresh
cases and 67 receipt/manifest/support cases pass. The combined refresh, broker
and maintenance diagnostic set runs 74 cases. The wider gate runs 5,491 cases
with five platform/expensive-test skips. Wheel, CLI documentation, links,
compilation and generated assets pass.

The source-activation receipt is implemented separately from saved setup and
observed Git. It remains readable when managed Git metadata is private, rejects
unsafe/malformed records, and preserves truthful unknown/recorded distinctions.
Maintenance status now persists fixed stages and one-based command indices
before effects and reports validated failure categories without exception text.

Remaining: install the tested fixes through guarded state-link compatibility
recovery, verify the saved-setup preview, and qualify the complete setup replay.
Keep the non-secret policy readable by the approval identity when changing its
window; the administrator reference documents the required mode and recovery.

The limited live diagnostic confirms protected official source with one dirty
entry, and a setup recovery marker from before the first step. The directory-only
`state/` ignore misses the installed `state` symlink; a real-Git activation case
now covers that lifecycle. Replay also reconstructs the reviewed approval origin
from policy rather than replacing it with the local transport placeholder.
The pre-step RuntimeError is consistent with the private-umask desktop logout
helper permission failure; the complete setup replay remains to be qualified.

## Next scoped improvements

After refresh recovery is qualified, prioritize these observations from the
Antistatic workload rather than duplicating newer container/resource tooling:

- **Artifact storage:** add an explicit private artifact-root setup option with
  ownership/path/mount validation and bounded doctor capacity reporting. Keep
  project completion, verification, links and migration under project control;
  never infer disposability from age or use timed temporary/public web roots.
- **CLI context:** preserve intentional unmanaged developer launchers, expose
  invoked versus managed-source context explicitly, and report known revision
  differences without inventing a deployed revision when evidence is unavailable.
  Source-activation receipts and manual comparison are the current first slice.
- **Maintenance diagnostics:** retain only fixed failure categories and validated
  setup phase/index/class metadata. Fixed maintenance stage/index/category
  reporting is delivered; correlating a target setup marker remains a follow-on.
  Omit privileged output, configuration, credentials and unrelated stale markers.
  Cover partial source activation, private umasks, service restart and recovery
  on a disposable Debian/systemd target before broadening automatic maintenance.
