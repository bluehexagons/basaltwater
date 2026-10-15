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

## Remaining boundaries

| Priority | Area | Current gap |
| --- | --- | --- |
| P1 | Cluster updates | There is no automated HA/Ceph maintenance mode or guest evacuation. Expand rolling updates only with a verified cluster-health, task, quorum, storage, and recovery contract. |
| P1 | Host health | The audit does not inspect ZFS scrub/pool health, LVM-thin pressure, SMART/NVMe status, detailed backup storage, certificate expiry, or timer delivery health. |
| P2 | Backup recovery | Immediate guest backups do not establish schedules, retention, off-host copies, freshness, integrity verification, or successful isolated restore. Consider native backup jobs and Proxmox Backup Server verification as one recovery contract. |
| P2 | Proxmox hardening | Generic AppArmor, auditd, and PAM policies are not applied unchanged to hypervisors. Any profile must account for API/UI access, realms/MFA, API-token scopes, SSH, native firewall, and cluster access. |
| P3 | Resource policy | Journald limits and maintenance windows use fixed safe defaults. Expose saved policy only when capacity and workload timing requirements are defined. |

## Qualification

Qualify standalone and clustered nodes separately, including storage-specific
health, interrupted updates, guest evacuation, unavailable backup targets, and
restore to an isolated guest. The [release matrix](../release-qualification.md)
remains the release gate; mocked checks do not qualify a live Proxmox host.
