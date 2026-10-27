# VM management, storage and Git hosting

Status: Proxmox guest lifecycle, selected clone/restore paths and same-host Gogs
recovery are implemented. Remaining work extends their recovery and storage
boundaries rather than reimplementing them. Use the [Proxmox guide](../operations/proxmox.md),
[Gogs guide](../services/gogs.md) and [coverage map](testing-coverage.md) for current
commands and qualification limits.

## Current boundary

| Area | Available behavior | Remaining boundary |
| --- | --- | --- |
| Proxmox guests | Provisioning, lifecycle/resource controls, snapshots, full/linked clones, selected storage moves, backups and same-host restores. | Off-host recovery, retention, other storage layouts and interruption cases. |
| Guest storage | Declared blank QEMU data disks and required-mount checks; provider disk growth is separate from guest filesystem growth. | Coordinated grow-only resize, existing-disk adoption and populated-path migration. |
| Gogs | Native SQLite/repository/LFS storage with Git/LFS and selected same-host restore checks. | Off-host restore, attachments, restored SSH transfer and migration-failure recovery. |
| File archives | Samba and path mirrors can hold explicitly scoped files or archives. | They are not application-consistent snapshots or qualified off-host recovery. |
| T3 Code | Desktop/headless setup, protected enrollment and exposure policies are documented; selected upgrade smoke checks exist. | Exact-candidate fresh install, provider enrollment and supported client/proxy combinations. |

## Next work

1. Restore Proxmox and Gogs backups onto another physical host. Verify identity,
   credentials, Git and LFS data, attachments and supported transports before
   claiming off-host recovery.
2. Qualify declared data mounts across reboot and mount loss. Add coordinated
   growth only after provider device, guest device and filesystem identities can
   be verified together.
3. Extend interrupted clone/restore and storage-move checks without overwriting
   existing guests or resolving uncertain operations automatically.
4. Add archive/asset roles only with restricted credentials, consistent archive
   publication and a restore test. Keep live Gogs data and active worktrees local.
5. Follow the [T3 Code guide](../agents/t3-code.md) for supported session APIs and
   HTTPS/WebSocket exposure; qualify actual native clients rather than
   maintaining a separate interface design here.

These follow-ups are separate from the immediate shared build/CI host.

## Constraints and acceptance

Proxmox remains the infrastructure provider. Provisioning hands the guest to
the existing setup engine; manually created servers use normal SSH setup.
Unsupported actions fail before mutation. Format only a positively identified
blank device, use durable identity/UUID mounts, and never redirect required-mount
writes to root.

A Gogs recovery set includes configuration secrets, SQLite, repositories,
attachments, LFS objects and storage identity. Generic mirroring alone does not
prove consistency. Keep provider credentials and recovery records private.

Use mocked system/provider calls and temporary directories for automated
mutation checks. Live VM, storage and client behavior requires disposable
qualification under the [release protocol](../release-qualification.md).
