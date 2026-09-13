# Proxmox Setup and Maintenance Audit (2026-08-09)

Status: active follow-up plan. Small reliability fixes from this audit landed
with the document; the remaining work needs explicit design and live-cluster
validation.

## Progress

- 2026-08-09: added a read-only `proxmox audit` command with text and JSON
  results for core service state, quorum, active tasks, configured storage, root
  free space, guest state and locks, and pending reboots.
- 2026-08-09: made `rolling-update` run that audit before any changes, after
  each node update, and after reconnecting from a reboot. Automatic reboot now
  stops when guests are running or locked, and later nodes remain untouched.

These are the first P1 safety controls, not completion of either P1 item below.
Repository suitability, HA/Ceph health, guest evacuation policy, storage-type
specific checks still require larger work and live-cluster validation. Rolling
updates now keep durable checkpoints and require explicit resume. Their supported
policy requires evacuated nodes and rejects HA/Ceph configurations, which remain
operator-managed until the broader orchestration below is qualified.

## Scope and current behavior

The review traced `server_proxmox` from plugin step selection through the
shared security steps, generated systemd units, service scripts, Proxmox CLI,
rolling-update path, and operator documentation.

The default flow intentionally:

- manages root SSH access and an SSH-only fail2ban jail;
- applies bridge-safe kernel hardening and skips generic swap creation;
- leaves firewall policy to Proxmox;
- installs security monitoring, daily non-removing APT upgrades, a restart
  policy check, and weekly bounded cleanup; and
- disables normal and forced hypervisor restarts unless explicitly requested.

The update command matches Proxmox's documented point-update procedure of
`apt-get update` followed by `apt-get dist-upgrade`. Major-version upgrades are
not safe to infer from that primitive and remain an operator-run workflow.

## Findings addressed in this audit

### PXM-01: SSH validation failure left invalid configuration on disk

The active daemon was not reloaded after `sshd -t` failed, but the invalid
drop-in remained in `/etc/ssh/sshd_config.d`. A later reboot could therefore
activate the invalid configuration and remove remote access. The setup now
restores the previous drop-in, or removes a newly created one, before returning.

### PXM-02: APT replacement could create a maintenance gap

The setup stopped and disabled Debian's APT timers before verifying the
infra_tools replacement. A daemon-reload, enable, start, or verification
failure could leave the host with no automatic updater. The replacement is now
verified first; distro timers remain active if verification fails.

### PXM-03: Installed fail2ban had no Proxmox security-monitor wiring

The monitor already tolerates an absent `auditd` and can report fail2ban bans
and SSH authentication failures, but the Proxmox flow did not install it. It is
now included without adding the more invasive generic auditd, AppArmor, or PAM
changes.

## Larger follow-up work

### P1: Proxmox-aware update orchestration

Current daily APT maintenance is node-local and repository-agnostic. The
separate rolling-update command replays the saved setup. The global
cleanup-first service gap tracked by `ARCH-05` was removed on 2026-08-19, but
the update now keeps durable per-node transaction state. It establishes basic
node health, requires evacuation before mutation, and rejects HA/Ceph topologies.
Automated HA/Ceph maintenance and workload evacuation remain unsupported.

Design a dedicated update transaction that:

1. distinguishes point updates from major-version upgrades;
2. validates repository health, free root space, package holds, quorum, HA and
   Ceph state, active Proxmox tasks, and backup freshness before mutation;
3. enters an appropriate maintenance mode or evacuates guests when policy
   requires it;
4. updates one node at a time, verifies Proxmox services and cluster membership,
   and only then advances; and
5. preserves a manual-recovery route and durable per-node result record.

This should build on
[Transactional execution and reconciliation](TRANSACTIONAL_EXECUTION.md), not
introduce a parallel transaction framework.

### P1: Proxmox health and maintenance observability

The generic cleanup job checks only root-filesystem usage. The security monitor
checks host authentication events, but neither evaluates the resources most
likely to threaten guest availability.

Extend the initial read-only Proxmox maintenance audit to cover:

- quorum, node membership, HA state, and stuck/failed tasks;
- `pveproxy`, `pvedaemon`, `pvestatd`, `pve-cluster`, corosync, and scheduler
  health where applicable;
- ZFS pool status/scrub age, LVM-thin metadata/data pressure, SMART/NVMe health,
  and every enabled guest/backup storage target;
- time synchronization and certificate expiration; and
- timer last-run/next-run/result state, including notification delivery health.

Expose stable text and JSON results through the roadmap's planned audit layer.
Thresholds and service applicability must come from detected storage and
cluster facts rather than assumptions about one Proxmox topology.

### P2: Scheduled backups, retention, and restore verification

infra_tools can create and list immediate `vzdump` backups, but it does not own
a schedule, retention contract, freshness objective, off-host copy, integrity
verification, or restore drill. Extend the existing P2 recovery roadmap with:

1. discovery and validation of native Proxmox backup jobs and storage retention;
2. backup-age and job-result monitoring per protected guest;
3. first-class Proxmox Backup Server integration, including recurring verify
   jobs and separate recovery-key handling for encrypted stores; and
4. a test-restore workflow that boots an isolated restored guest, performs
   declared health checks, and destroys it only after recording evidence.

Proxmox Backup Server recommends frequent verification of new/expired backups
and periodic full reverification, so archive existence alone must not count as
recoverability.

### P2: Proxmox-specific hardening profile

The dedicated flow omits generic AppArmor enforcement, auditd rules, PAM
lockout, and login banners. Applying the generic implementations unchanged is
not appropriate: broad AppArmor enforcement can affect host-managed LXC
profiles, generic audit rules may add high-volume noise, and PAM policy can
interact with Proxmox administrative access.

Build and live-test an explicit Proxmox profile that documents each included,
excluded, or delegated control. It should cover the Proxmox API/UI, realms and
MFA, API-token privilege boundaries, host SSH, native firewall state, and
notification routing without risking cluster access.

### P3: Retention and resource-policy configuration

The shared `100M` journald cap and fixed timer schedules are safe bounded
defaults, but a busy hypervisor may need more diagnostic history and deliberate
I/O staggering around backups, scrubs, replication, and guest workloads. Make
journal retention, maintenance windows, and randomized-delay ranges explicit
saved policy with conservative Proxmox defaults and validation.

## Validation requirements

- Unit tests must cover failed replacement activation, rollback of invalid
  managed files, Proxmox step wiring, and rendering of any new policy.
- Live tests need both standalone and clustered Proxmox nodes; cluster tests
  must include quorum loss simulation, an HA-managed guest, and at least one
  local-storage migration path.
- Storage coverage must include ZFS and LVM-thin plus an unavailable backup
  target.
- Update tests must prove that one unhealthy node stops later nodes and that a
  healthy node is not rebooted while non-evacuated guests violate policy.
- Recovery is complete only after a recorded isolated restore test succeeds.

## Primary references

- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf)
  for repositories, point updates, clustering, storage, backups, and HA.
- [Proxmox Backup Server maintenance](https://pbs.proxmox.com/docs/maintenance.html)
  for verification-job and periodic reverification guidance.
- [Proxmox Backup Server storage guidance](https://pbs.proxmox.com/docs/storage.html)
  for backup verification, retention, and recovery considerations.
