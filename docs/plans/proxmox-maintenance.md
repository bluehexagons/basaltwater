# Proxmox maintenance

Status: routine audit, update, cleanup, and reboot safeguards are available in
the [Proxmox operator guide](../operations/proxmox.md). Cluster-aware
orchestration, deeper hardware/storage monitoring, and verified backup recovery
remain outside the current automation contract.

## Current contract

- Read-only audit reports core service state, quorum, active tasks, configured
  storage, root capacity, guest state/locks, and pending reboots.
- Daily maintenance validates supported repositories, package state and holds,
  required node health, and configured storage before updates.
- Rolling updates retain durable per-node checkpoints, require guests to be
  evacuated, and stop on unhealthy nodes. HA and Ceph topologies remain
  operator-managed.
- Scheduled reboot requests require evacuation and a healthy audit, including
  when the configured deadline is forced.
- SSH and native firewall setup verify effective policy before activation.
  Kernel cleanup is limited to a guarded kernel-only package purge; broad APT
  autoremove remains operator-managed.

## Current boundaries

| Area | Current support boundary |
| --- | --- |
| Cluster updates | Automated HA/Ceph maintenance mode and guest evacuation are not available. Rolling updates stop on unhealthy nodes and require operator-managed evacuation. |
| Host health | The audit does not inspect ZFS scrub/pool health, LVM-thin pressure, SMART/NVMe status, detailed backup storage, certificate expiry, or timer delivery health. |
| Backup recovery | Immediate guest backups do not establish schedules, retention, off-host copies, freshness, integrity verification, or successful isolated restore. |
| Proxmox hardening | Generic AppArmor, auditd, and PAM policies are not applied unchanged to hypervisors; their access, realm, token, SSH, firewall, and cluster requirements need a platform-specific contract. |
| Resource policy | Journald limits and maintenance windows use fixed safe defaults; there is no saved resource or maintenance-window policy. |

## Qualification status

PVE-01 in the [release matrix](../release-qualification.md) covers multi-node
installation, lock exclusion, and guest operations. It does not qualify
standalone maintenance, storage-specific health, interrupted updates, guest
evacuation, unavailable backup targets, or restore to an isolated guest.
Mocked checks do not qualify a live Proxmox host.
