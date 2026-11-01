# Continue testing on Proxmox

Updated 2026-11-01 after the selected Shares & Data batch. The
[dated results](evidence.md#shares-and-data) record installed HTTP/worker,
repair/acceptance, selected Chromium/Firefox, exclusion and reboot checks and
remaining client/setup limits. This procedure is not blanket permission to modify
retained fixtures. Read the
[status overview](README.md), [dated evidence](evidence.md),
[coverage map](../plans/testing-coverage.md) and local private operational handoff.
The handoff owns connection details, credentials, fixture constraints and exact
evidence paths; keep those out of Git. Follow repository and session instructions.

The subsequent [Admin package batch](evidence.md#package-changing-admin-update)
qualifies a real leaf-package upgrade, separately approved no-op, native index
failure/lock waiting and task/service preservation on another disposable guest.
Package breadth, conffile conflicts and interrupted upgrades remain separate.

## Establish a baseline

Record the checkout revision/dirty state and separately inspect the target's
installed source/configuration. Recheck host capacity, guest states, active
tasks/locks, storage mounts and selected guest health before allocating work.
Historical inventory is not current allocation authority. Keep host keys pinned.

Use an owned disposable Debian guest with a unique network identity. Prefer a
fresh clone of the retained template to overwriting an older qualification
fixture. Snapshot/back up the owned test state as appropriate; a snapshot on the
same node is not an off-host backup. Keep OS/build work on suitable healthy
storage. The USB SSD is available for disposable test disks, with its mount-point
guard retained; it has no endurance qualification. Preserve existing data.

## First batch: current Shares & Data

The selected run closed the installed per-task UI and setup-recovery revision
gap without public DNS or external accounts. Follow-up checks qualify successful
targeted repair with adequate parity, acceptance and selected installed browsers.
Repeat this procedure when affected behavior changes. Shared preview certificate
trust, native Firefox ESR and full-profile refresh remain separate boundaries. Read the
[service procedure](../services/shares-data.md) and
[transaction recovery](../operations/transaction-recovery.md) before testing.

1. Inspect the retained qualification report and cleanup state. Prepare an owned
   disposable Debian guest with a **non-root operator**, trusted HTTPS panel,
   separate approval identity and real privilege broker. Install a pinned current
   revision through the normal supported setup/refresh path. Do not substitute the
   hypervisor's root panel or manually replace installed modules.
2. Exercise the actual authenticated panel → review → separate approval → root
   worker flow using disposable files/accounts. Check denial/cancellation before
   effects, one-use submission and final correlated worker outcomes. Keep secrets
   in protected inputs and preserve original accounts/data.
3. Verify current per-task finding selection, case-sensitive root/parity identity,
   filter/clear links, pagination and unavailable/removed selections. Exercise
   stale revisions and retained drafts; verify no stale input is silently applied.
   Distinguish actual HTTP assertions from interactive browser assertions.
4. Repeat one SMB permission/account lifecycle and one mirror/PAR2 finding
   workflow, verifying resulting bytes and retained identities/parity. Read the
   saved target declarations before controller reruns: panel edits do not update
   controller desired state. Use adequate parity for a successful missing-file
   repair; separately check an intervening-file race, deliberate acceptance and
   a fresh independent verification. Retain old parity and recovery evidence.
5. On disposable state only, verify setup/refresh ownership and retained recovery
   guards block conflicting controls/dispatch/execution without clearing the guard
   or causing effects. Inspect uncertain outcomes before considering any retry.
6. Check persistence after a panel restart and an authorized owned-guest reboot.
   Check that normal setup rearms the audit timer and observe an unattended export.
   Record cleanup, preserved data, remaining guards and final guest state.

Pass evidence must identify the actual installed revision, real approval/worker
results, observable data/access outcomes and cleanup. Local simulated-browser
success alone does not close this batch. Use the configured browser workflow
when a session requests interactive browser work; record unavailable client cases
as untested rather than substituting HTTP or another browser silently.

## Follow-on batches

| Batch | Acceptance boundary / prerequisites |
| --- | --- |
| Package-changing Admin update | Selected leaf-package case passed at `176caeff62ad`; repeat affected cases after changes. Use a separate disposable Debian target with a deliberately available safe upgrade; actual approval, changed package version, correlated worker outcome, separately approved rerun/no-op and task/service preservation. Extend to service-restarting packages/conffile conflicts separately. An empty upgrade plan does not qualify a package change |
| Proxmox panel/exporter | Inspect read-only inventory/HTTPS first; plan ownership before timer/removal/interruption tests. Existing patch/auth checks do not qualify the full inventory timer/HTTP lifecycle, console, native password replacement or teardown |
| Native storage diagnostics/scrub | Use disposable guest data; target missing integration/scheduling. Keep mount guards. Hard-mounted CIFS can leave uninterruptible I/O, so isolation and a bounded containment plan must precede fault injection |
| Registered runner and GitHub mirrors | Authorized test account/repository and privately supplied credentials; actual registration/events/jobs/cancellation or transport permissions. Pending tokenless setup is already covered |
| Off-host recovery / physical clients | Another physical recovery destination, or the required graphics/input/client hardware. A second VM on the same node does not close the off-host case |

Combined build/CI host composition needs implementation before mixed-role
qualification. Power loss, management-network isolation, HA/Ceph and physical
disconnect tests need separate scope and suitable fixtures. Do not turn the
documentation backlog into permission for host downtime or destructive testing.

## Record and hand off each batch

Keep UTC date, source and installed revisions, dirty state, exact commands,
fixture ownership, baseline, expected observations, failures, outcome, skips,
cleanup and evidence paths in a private report. Track running/uncertain jobs with
their original handles; inspect completion before repeating an operation.
Historical mutation scripts are evidence, not resumable runbooks.

Update only the affected [platform rows](debian.md), [coverage map](../plans/testing-coverage.md)
and evidence summary after real checks. Keep unrun cases and failed attempts
visible. Maintain the private server handoff for changed inventory/endpoints.
Fix discovered bugs with focused regressions and normal project checks; commit
and push scoped main milestones when authorized. For documentation-only changes,
run `make docs-check` and review `git diff --check` and the final diff.
Do not mark the [release matrix](../release-qualification.md) complete unless its
exact-candidate acceptance cases were actually run.
