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

Guarded compatibility recovery is qualified on the VM: the sole untracked item
was the root-owned managed state symlink, the exact tested source was installed,
permissions and the readable receipt are verified, and saved setup passes its
dry run. The TLS-verified approval challenge remains healthy. Remaining:
qualify the complete setup replay with the repaired public origin and permissions.
Keep the non-secret policy readable by the approval identity when changing its
window; the administrator reference documents the required mode and recovery.

The subsequent approved full refresh activated `39aa8cf` but again finished
with exit code 1. Dispatch succeeded; setup did not. The running broker's older
status response omits stage/index/failure fields, so this replay's failing stage
is not yet established. The approval HTTPS challenge and Xvfb OpenGL 4.5 context
remain available, and home has about 39 GiB free. Diagnose this failure before
replaying; retain the recovery snapshot and qualify the lifecycle on a disposable
Debian/systemd target. The broader handoff is recorded in
[Antistatic workload feedback](ANTISTATIC_WORKLOAD_FEEDBACK.md).

The limited live diagnostic confirms protected official source with one dirty
entry, and a setup recovery marker from before the first step. The directory-only
`state/` ignore misses the installed `state` symlink; a real-Git activation case
now covers that lifecycle. Replay also reconstructs the reviewed approval origin
from policy rather than replacing it with the local transport placeholder.
That earlier pre-step RuntimeError concerns the earlier replay; it does not
identify the failing stage of the subsequent `39aa8cf` replay. The complete
setup replay remains to be qualified.

## Disposable lifecycle matrix — awaiting target allocation (2026-10-12)

The affected VM is `codebox`. The current maintainer VM has no saved `codebox`
configuration, its Basaltwater history is empty, and accessible diagnostic files
contain no matching refresh record. The latest failing stage therefore remains
unknown. No request was replayed and no recovery snapshot was removed. Hardware
for a disposable Debian/systemd target is not allocated yet; fixture coverage
and passing CI are preparation, not live qualification.

Maintenance readers now expose fixed stage/index/category fields in the Admin
screen and `agent privilege admin-status --json`. New job identities correlate
setup markers and preserve safe final context across later jobs. Legacy or
unrelated evidence remains unknown. Unit fixtures cover pre-step/later-step
failure, matching recovery, process loss, stale/malformed markers, old-reader
results and private-field filtering. Existing refresh fixtures continue to own
source activation, umask normalization, state links and origin preservation.

Run each applicable case with both an installed snapshot and a managed checkout,
using the exact candidate commit, saved desktop selection and `UMask=0077`:

| Case | Required observation | Live status |
| --- | --- | --- |
| QF-01 | Source activation preserves retained state links/data and the original backup; code and version/activation metadata are readable by their consumers while Git/authentication/state stay private. | Not run; target pending |
| QF-02 | Failure before the first setup step retains prior successful setup and reports only its own job's pre-step context. | Not run; target pending |
| QF-03 | A later step fails after partial effects; safe phase/index/class is correlated, later steps stop and saved setup stays unchanged. | Not run; target pending |
| QF-04 | Matching explicit recovery retains the operation identity, replaces the attempt's job identity, succeeds and records the final job result. | Not run; target pending |
| QF-05 | Source activation failure/partial activation preserves or restores actual paths and leaves actionable recovery evidence; no automatic replay. | Not run; target pending |
| QF-06 | Lose the worker/broker process or its dispatch response during source/setup work; executing/uncertain requests remain unreplayed and markers survive. | Not run; target pending |
| QF-07 | Restart broker/panel services deliberately on the disposable target; persisted final job result survives and observed service revisions/lifetimes remain separate from source receipts. | Not run; target pending |
| QF-08 | Public approval origin survives refresh; policy remains readable by the approval identity, credentials/state private, and TLS verification/authentication challenge succeeds. | Not run; target pending |
| QF-09 | An old running reader omits newer fields honestly; stale markers cannot explain a new failure, and new readers expose only fixed correlated metadata. | Not run; target pending |
| QF-10 | Reconnect a real T3 client and shared desktop, then run a small native GL/capture workload; record software renderer separately from hardware qualification. | Not run; target pending |
| QF-11 | Long native build/capture, an unpushed commit and disconnected client survive the reviewed maintenance hold/readiness lifecycle; expiry and job runtime stay independent. | Not run; target pending |

Retain private commands/logs and snapshots on the disposable target. Commit only
the reviewed case ID, exact source, expected/observed outcome and recovery result.
Keep the original `codebox` recovery snapshot until this matrix passes and the
latest failed job is diagnosed through safe status or administrator evidence.

## Next scoped improvements

After refresh recovery is qualified, prioritize these observations from the
Antistatic workload rather than duplicating newer container/resource tooling.

The [workload feedback](ANTISTATIC_WORKLOAD_FEEDBACK.md) expands their acceptance
criteria and maps storage, resource interpretation, cleanup and maintenance
qualification to the existing project owners.

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
