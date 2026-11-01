# Testing methodology and environments

**Snapshot: 2026-11-01.** Platform and workflow evidence comes from repository
records, retained private live-test reports and an owner-reported stack setup
rerun received 2026-11-01. Later native CachyOS T3 session/discovery observations
and automated regressions have a separate
[evidence record](evidence.md#cachyos-t3-desktop-recovery). Historical inventory
and service observations do not establish current guest health.

## Intended use cases

- Set up and maintain Debian servers, virtual machines and containers, including
  Proxmox guest and storage operations.
- Operate coding-agent environments and shared desktops with bounded access,
  credentials, task supervision and recovery.
- Deploy web applications and optional services, with selected builders, backups
  and explicit recovery procedures.
- Prepare development workstations and project-owned tooling on supported
  platform profiles; use native project builds and tests independently.

## How evidence is classified

1. **Automated:** isolated unit, integration and fixture tests use mocked system
   calls and temporary state. They verify contracts and failure paths, but do
   not prove a real installation works.
2. **Component:** controlled native services, processes, packages or disposable
   filesystems exercise a specific boundary. Note any mocks, preinstalled
   packages and operations that were excluded.
3. **Selected live workflow:** run a real setup or operation on a suitable
   test system, verify observable behavior, rerun/reboot or recovery when
   relevant, and record what was not exercised.
4. **Release qualification:** repeat required cases on the exact candidate;
   the [release matrix](../release-qualification.md) owns pass/fail status.

For each result record UTC date, revision, environment class, command or
procedure, outcome, skips and limits. Record failures and retries separately.
Use private storage for raw evidence and publish only sanitized summaries.
Selected successes do not imply hardware endurance, off-host recovery, every
option combination, or current status on a later revision.

Label owner-reported results explicitly and distinguish the report date from
the execution date. If revisions, versions or individual outcomes were not
supplied, record them as unknown. A successful setup rerun indicates basic
functionality; including an option does not qualify its application, service,
notification, pairing or registration workflow. A restart/wait option does not
prove that a reboot occurred. See the
[stack rerun summary](evidence.md#owner-reported-stack-setup-rerun) for this level
of evidence; it does not promote a feature to labbed or verified status.

Use the [dated evidence summaries](evidence.md) to distinguish installed-system
checks from local browser fixtures and automated regressions. In particular,
an actual authenticated HTTP request is not an interactive browser pass;
a browser using simulated approvals is not a real privileged-worker pass.
A user-initiated reboot is not qualification of the Admin power-action path.
Verifying a persistent mount declaration and its current generated unit does
not prove the newly added declaration survived a subsequent reboot.

Keep the test result and fixture availability separate. A stopped guest can
retain valid historical evidence; enabling a storage pool makes it available
for tests without qualifying all workloads on that pool. Recheck live state
before starting the next batch and preserve retained failure checkpoints.

## Environments

| Environment | Purpose and limits |
| --- | --- |
| Physical x86-64 Proxmox host | Guest lifecycle, storage and host operations; selected single-host testing, not cluster/HA qualification |
| Debian 12/13 QEMU guests and unprivileged LXC | Server, service, agent, deployment and failure/recovery workflows; LXC shares the host kernel |
| SSD-backed and USB-attached test storage | Selected mount, Btrfs, sync and recovery behavior; bounded I/O is not drive-endurance or power-loss qualification |
| CachyOS KDE workstation | Native developer tools, graphics contexts, desktop tasks and hardware-oriented development checks |
| Windows 11 workstation | Native PowerShell, desktop tooling and process/Scheduler checks; the full installation and physical-device matrix remains open |
| Ubuntu WSL and browser clients | Platform-specific development and client workflows; native Windows, Linux and browser results are separate evidence |

Exact machine identities, credentials, network topology and storage inventory
remain in private records. Hardware-dependent and destructive checks require
their own authorized, disposable fixtures.

## CI and local checks

The fast local loop runs syntax, documentation, package metadata and smoke
checks. Full CI runs static/brand/package checks, a wheel build/install and the
suite with coverage in Debian; routine CI uses Python 3.13 and release checks
cover the supported Python endpoints. Browser qualification is separate from
the full CI target. See the [contributor guide](../agents/contributing/README.md#standard-checks)
for current commands, versions and selection rules.

The [platform index](features.md) links to scoped feature results; the
[detailed coverage map](../plans/testing-coverage.md) retains narrower evidence
and open checks. Neither replaces exact-candidate release qualification.
The [continuation guide](continuing.md) defines the next bounded live-test batch
and the evidence needed to update these records.
