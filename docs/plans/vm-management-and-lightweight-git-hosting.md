# VM management, storage, and Git hosting

Status: active project. Proxmox lifecycle, guest storage, Gogs recovery, and
agent web-interface work share setup and transaction boundaries. The current
operator contracts are in the [Proxmox guide](../operations/proxmox.md),
[Gogs guide](../services/gogs.md), [T3 Code guide](../agents/t3-code.md),
[storage guides](../storage/README.md), and
[transaction recovery](../operations/transaction-recovery.md).

## Current state

| Area | Current behavior | Remaining boundary |
| --- | --- | --- |
| Proxmox VM commands | Provider-neutral observation, resource statistics, power state, boot order, and confirmed destruction are available. | Live-qualify the supported paths; sequence advanced mutation, clone, and restore behind transaction recovery. |
| Guest storage | New QEMU provisioning can attach named blank data disks and mount them at empty paths before dependent setup. | Live Proxmox/reboot validation is open. Existing-disk adoption, detach, resize, and populated-path migration are not supported. |
| Gogs and Git LFS | Native Gogs uses local SQLite and LFS storage with declared-mount checks and health reporting. | Define consistent backup/restore and qualify Git plus LFS transfer after restore. |
| Samba storage roles | Samba and mounted shares are available for ordinary files. | Define restricted archive and asset roles; live Gogs data and active Git worktrees remain local. |
| T3 Code interfaces | Desktop and headless service selection are distinct; loopback access, private-source exposure, pairing, and protected enrollment are available. | Managed session listing/revocation and a validated HTTPS/WebSocket proxy remain open. |
| Generic path backup | The existing storage service can mirror a declared path to a mounted destination. | It is not an application-consistent snapshot and does not replace tested restore. |

## Next work

1. Validate the declared QEMU data-disk path on a disposable Proxmox guest
   across reboot and a missing-mount failure. Keep required mounts fail-closed.
2. Add read-only mount status and coordinated grow-only resize only after
   provider and guest identity can be verified as one operation.
3. Scope Proxmox clone/restore through shared durable operation and recovery
   records; never overwrite an existing guest implicitly.
4. Define a consistent Gogs archive containing configuration secrets, SQLite,
   repositories, attachments, LFS objects, and disk identity. Prove it with an
   authenticated Git and Git LFS restore smoke test before automatic updates
   claim recovery coverage.
5. Add any Samba asset/archive workflow with explicit path roles, restricted
   credentials, atomic archive publication, and a restore test.
6. Add T3 session inspection/revocation or HTTPS exposure only through
   documented upstream interfaces, native-client compatibility checks, and
   the shared Nginx activation/recovery contract.

## Design constraints

- Proxmox remains the only infrastructure provider Basaltwater manages.
  Manually created VPS instances use the normal SSH setup flow.
- Provisioning creates the target, then hands it to the existing setup engine;
  it does not create a second target-configuration path.
- VM lifecycle commands must report provider capabilities and stable typed
  results. Unsupported actions fail before mutation.
- Format only a positively identified blank device. Use stable disk identity
  and UUID mounts; a missing required mount must never redirect writes to root.
- Keep Gogs SQLite, repositories, secrets, and live LFS objects on local
  storage. Samba can hold a consistent archive or explicitly scoped assets.
- Desktop T3, headless T3, agent provider CLIs, and browser automation are
  independent selections with independent authentication and exposure.
- Bind web interfaces to loopback by default. Private network exposure needs
  explicit source policy; public HTTPS/WebSocket proxying needs verified
  native client, origin, and rollback behavior.
- Keep operator state and recovery artifacts private; do not copy provider
  credentials to managed guests or agent workspaces implicitly.

## Qualification

Mocked provider/system calls and temporary directories are necessary for
mutation tests, but they do not qualify a live VM, Proxmox storage, or client
connection. Use disposable targets and the candidate-specific cases in the
[release checklist](../release-qualification.md).
