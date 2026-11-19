# Proxmox host setup and maintenance safety

[Topic index](proxmox-host-safety.md)

## Host-safety defaults

The `server_proxmox` flow uses Proxmox's native firewall rather than UFW and
does not create a generic `/swapfile`: Proxmox storage (especially ZFS) must
own its swap layout. It retains the host's permissive reverse-path filtering
because strict filtering can drop valid routed, NATed, or bridged guest
traffic.

Kernel hardening checks and reconciles its managed live sysctls on every setup rerun,
including the permissive `all` and `default` reverse-path filtering settings,
even when `/etc/sysctl.d/99-security-hardening.conf` already matches. This
repairs live drift and retries a partially failed application. Settings absent
from the running kernel are skipped; failure to apply an available setting
stops setup rather than reporting hardening success. Guests without permission
to modify the host kernel retain the existing capability-based skip.

Generic AppArmor, auditd, and PAM policies are not applied unchanged to a
hypervisor; they must account for Proxmox roles, realms, API tokens, SSH,
firewall, and cluster requirements. Journald limits and maintenance schedules
use fixed defaults; there is no saved per-node resource or maintenance-window
policy.

Setup verifies Proxmox VE 9.x on Debian 13 (`trixie`), enabled Debian base and
security sources, and the stable Proxmox enterprise or no-subscription channel
before the first profile change. Test channels and mixed official Debian or
Proxmox suites stop setup. A strict repository refresh must succeed, including
subscription authentication when enterprise sources are enabled. Setup
also rejects incomplete package transactions and held core Proxmox
packages, and checks that the refreshed `pve-manager` candidate stays within
9.x before proceeding. Stable point releases are accepted so a change in the
minor version does not block kernel and other package updates.
When Proxmox explicitly reports that no subscription key is installed, setup
enables the matching no-subscription repositories and disables the standard
enterprise entries before refreshing APT. This handles fresh installer defaults
for both PVE and Ceph, including `.list` and `.sources` files. Proxmox's native
repository API preserves unrelated sources, keeps the configured Ceph release,
and checks for concurrent edits. An existing no-subscription entry is reused.
Installed keys, including expired or invalid subscriptions, retain their
repository selections; authentication failures still stop setup. Custom or mixed
enterprise entries require manual channel selection. Package signatures remain
required, and setup does not perform a major release upgrade. See the
[Proxmox repository reference](https://github.com/proxmox/pve-docs/blob/master/pve-package-repos.adoc)
for subscription requirements and channel differences. `--harden-user` is rejected for
this profile because its forwarding restrictions would interfere with root's cluster SSH.

Every host setup, including a rerun, then performs a noninteractive
`apt-get dist-upgrade --no-remove` using those refreshed indexes. This installs
current stable Proxmox 9.x packages and their new kernel dependencies before
applying the remaining host configuration; `--refresh-packages` is not required.
The upgrade runs inside setup's existing target lock rather than invoking the
scheduled updater, which would defer while that lock is held. Node health and
package state are checked before and after the upgrade, and setup verifies that
the installed `pve-manager` matches APT's candidate. Repository failures,
required package removals, HA/Ceph configurations, held core packages, or failed
health/version checks stop setup and prevent its post-setup restart.

Host setup then checks snippets storage for future VM cloud-init data. If no
snippets pool is configured for this node, setup adds `snippets` to enabled,
active `local` directory storage while retaining every existing content type.
Proxmox's configuration digest protects this update against concurrent edits,
and setup verifies the resulting content policy and availability. Existing
snippets pools are preserved even when disabled or temporarily unavailable;
setup warns when none is usable. Hosts without eligible `local` storage receive
an actionable warning instead of a new pool or a changed storage path. Enable
snippets on a suitable pool manually for those layouts. This default does not
enable VM image content or relocate existing data.

The kernel reboot-marker hook is installed before the package upgrade, including
on first setup. To update an existing host and restart it when a new kernel
records a reboot requirement, rerun its setup with the one-time restart flag:

```bash
basaltw setup server_proxmox 10.0.0.10 root \
  --key ~/.ssh/proxmox_ed25519 --name pve1 --restart-if-needed
```

Add `--wait-for-restart` to verify a new boot and node health before the command
returns. Existing kernel pins and bootloader selections are preserved, so a
deliberately pinned older kernel can still boot after the update.

SSH hardening uses an early `00-basaltwater-hardening.conf` drop-in and verifies
the effective key-only policy with `sshd -T` for root and the setup account,
including the current SSH peer when available. Conflicting earlier or `Match`
settings stop setup and restore a changed drop-in before any reload. Both
identities must belong to `remoteusers`. The former managed `99-` drop-in is
removed only after its replacement passes verification. Root public-key login
and normal forwarding remain available for Proxmox cluster operations.

Setup inspects active host swap without resizing or replacing it, reports each
device's type, size, and current use, and warns when no swap is active. A
direct `/dev/zvol/` or `/dev/zd*` swap device receives a prominent warning but
is not changed. The managed host policy persists `vm.swappiness=10` in a
late-order sysctl drop-in, applies the complete boot-time precedence for that
setting, and verifies the live result.

Every `server_proxmox` setup also reconciles the node's Proxmox balloon target.
The automatic target aims for the larger of 20% of physical RAM or 2 GiB of
host headroom, never raises the target above Proxmox's 80% default, and uses a
50% floor on small hosts. A stricter existing target is preserved unless an
explicit override is supplied. Setup prints the host total, selected
percentage, and nominal headroom for comparison. Ballooning responds to host
memory use and depends on guest support and available reclaimable memory;
the target does not guarantee a physical RAM reserve. Use
`--proxmox-balloon-target PERCENT` to override the calculation with a value
from 1 through 95; this changes the node policy, so reserve enough memory for
Proxmox services, storage, and QEMU overhead.

Setup accepts the node target as either a JSON integer or a decimal digit
string, because Proxmox can return saved node configuration values as strings.
Both the initial read and verification enforce Proxmox's 0–100 range. If an
older setup fails with `Unable to read the existing Proxmox balloon target`
for a valid saved value, update Basaltwater on the controller and rerun the
same setup command; the node setting does not need to be deleted.

By default, setup does not change Proxmox firewall state. Supplying
`--lan-access` or `--access-source` reconciles only basaltwater-commented
entries in Proxmox's standard cluster-wide `management` IP set, preserves
operator entries, then enables the firewall on a verified single-node host.
For a multi-node cluster, review management access on every node, keep recovery
console access available, and enable the firewall explicitly in Proxmox before
running setup with these options. That standard set covers the Proxmox web GUI,
SSH, VNC, and SPICE, while Proxmox includes the local cluster network.
`--no-access-source` and `--no-lan-access` remove
the corresponding saved policy and tool-owned set entries but deliberately do
not disable a firewall that may contain other policy.

Source reconciliation checks the node firewall, the cluster input policy,
the selected backend service, and configuration compilation before changing
access. A disabled node firewall or `ACCEPT` input policy stops setup for
operator correction. New sources must be readable before activation; old
managed sources are removed only after activation passes verification. Failed
activation restores a previously disabled cluster firewall. When available,
the current SSH peer must belong to a requested or retained operator source.
Setup verifies the final IP set and reports retained operator entries and
implicit cluster access. Existing rules still take precedence; this does not
prove that only the requested sources can connect. Backend selection stays
operator-owned, including an already-selected nftables backend.

Maintenance audits read the node's JSON guest inventories, including VM and
LXC locks. Invalid or incomplete inventory responses block updates and reboots;
a stopped guest with a leftover lock also requires operator review.

Automatic host restarts and forced restart deadlines are disabled by default.
The setup reports pending restarts, but schedule any hypervisor reboot around
guest downtime (or opt in explicitly with `--auto-restart` or
`--auto-restart-force-days`).

The one-time setup flag `--restart-if-needed` explicitly authorizes graceful
guest shutdown when a reboot is pending. A job on the node holds the maintenance
lock, verifies health and guest locks, uses Proxmox's configured shutdown order
without forcing guests off, and repeats the audit before rebooting. Shutdown
has a 180-second default per guest, overridden by its configured shutdown
timeout, and a 30-minute overall wait limit. Remaining guests or failed checks
block the host reboot; some guests may already be stopped. Configured HA
resources, local Ceph configuration, and RBD/CephFS storage require
operator-managed maintenance and are rejected before guest shutdown. Incomplete
package transactions and held core packages also block this reboot job.
The job continues if its controller is one of the guests being stopped.
Add `--wait-for-restart` for remote boot and health verification, or inspect
the node's job logs with
`sudo journalctl -u 'basaltwater-setup-reboot-*' --no-pager`. Existing Proxmox
autostart and HA policies control guest startup after the host returns.
After SSH returns, `--wait-for-restart` also waits up to five minutes for a
healthy Proxmox audit covering services, quorum, tasks, guest locks, and storage.
Autostart guests may be running; evacuation is required before reboot only.
The setup command exits nonzero if this verification fails, even when the
general host health summary can still be read over SSH.

The default setup installs these recurring host-maintenance timers:

- `security-monitor.timer` checks fail2ban and SSH events every 15 minutes and
  sends findings when notification targets are configured.
- `auto-update-apt.timer` applies non-removing distribution upgrades daily at
  06:00; Debian's competing APT timers are retired only after this replacement
  is verified active. The job checks the supported release and repositories,
  package consistency and holds, core services, quorum, active tasks, guest
  locks, storage availability, and root/boot disk and inode headroom before
  upgrading. It repeats health checks after refreshing indexes and after
  upgrading, and accepts stable 9.x point updates while refusing an APT
  candidate outside that major version. After upgrading, it also requires the
  installed `pve-manager` to match APT's candidate. HA and Ceph configurations require
  operator-managed updates and produce a notification instead.
- `auto-restart-if-needed.timer` checks daily at 02:00 and after boot, but the
  default Proxmox policy records and reports a deferral instead of rebooting.
- `cleanup-maintenance.timer` audits `dpkg` consistency, cleans bounded caches,
  journals, old crash reports, and Basaltwater-owned temporary artifacts, and
  ensures
  filesystem TRIM through the native timer or a cleanup fallback each Sunday.
  Post-cleanup checks cover block and inode pressure on distinct local storage
  mounts. On supported healthy nodes it also removes obsolete kernels while
  preserving the running kernel, newer kernels, an older fallback, holds, and
  Proxmox's boot-retention protections. A simulated purge and an APT transaction
  guard reject changes outside the selected old kernels. HA/Ceph and failing
  health checks require operator-managed removal. The job does not prune
  backups, templates, ISOs, guest volumes, change kernel APT marks, or alter
  explicit `proxmox-boot-tool` selections. Broad package autoremove remains
  operator-managed. See [kernel cleanup](maintenance.md#cleanup-and-state-safety)
  for a read-only preview and an immediate cleanup command.

Inspect these jobs with the commands in [Recurring Maintenance](maintenance.md).
APT updates and restart checks share the node's setup lock and defer while it
is occupied. Cleanup's APT phase uses the same lock; when busy, its non-package
cleanup and storage checks still run. Scheduled opt-in reboots also require no
running guests; a forced deadline cannot bypass Proxmox health or evacuation checks.
Checks describe the node before scheduling a reboot; operators must keep it
evacuated during the grace period. Package maintainer scripts can restart host services despite
`--no-remove`. There is no cross-node timing coordination or backup
recoverability check. Run the Proxmox audit before planned maintenance and
verify backups through your normal retention and restore process.
