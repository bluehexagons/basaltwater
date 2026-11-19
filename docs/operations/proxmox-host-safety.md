# Host safety, storage, and admission

[Documentation index](proxmox.md)

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

Create a Debian VM with XFCE, RDP, Firefox, and coding tools:

```bash
basaltw setup workstation_dev 10.0.0.50 agent \
  --provision-on pve1 --base debian --name agent-dev-01 \
  --cores 4 --memory 8G --storage root 40G --image-storage local \
  --desktop xfce --rdp --browser firefox \
  --password "$RDP_PASSWORD" \
  --agent-tool gh --agent-tool codex --agent-config active --git-access read \
  --repo https://github.com/user/my_codebase.git \
  --node --go --python
```

Memory values use binary units and may include decimals when they resolve to a
whole MiB. For example, `--memory 1.5G --balloon-min 0.5G` becomes 1536 MiB
with a 512 MiB balloon floor. Values are converted exactly rather than rounded.

For a newly provisioned QEMU VM, add named data disks and required guest
mounts in the same declaration:

```bash
basaltw setup workstation_dev 10.0.0.51 agent \
  --provision-on pve1 --memory 8G --storage root 40G \
  --disk-ssd root \
  --storage agent-data bulk-lvm 128G \
  --storage-mount agent-data /srv/agent-workspace ext4 \
  --agent-workspace /srv/agent-workspace \
  --agent-tool gh --agent-tool codex \
  --repo https://github.com/user/my_codebase.git
```

Each non-root `--storage NAME [POOL] AMOUNT` requires exactly one matching
`--storage-mount NAME PATH [ext4|xfs|btrfs] [empty]`, unless that disk is
consumed as cache media by `--storage-cache` or as swap by `--swap-device`.
When `POOL` is omitted, the root-pool default is used. Provisioning checks that
each selected pool is active, accepts VM images, and reports enough aggregate
free capacity. It then attaches the disks as `scsi1`, `scsi2`, and so on with
stable `bw-NAME` serials.
The example advertises only the SSD-backed root device as an SSD; the named
data disk keeps the VM-wide default of SSD emulation disabled. Append a logical
disk name to any disk flag for a device-specific override, such as
`--disk-ssd root`, `--no-disk-discard agent-data`, or
`--disk-discard agent-data`. An unqualified flag remains the default for all
declared disks, and a named override wins over that default.

Choose `btrfs` for a new single-device Btrfs filesystem with:

```bash
--storage-mount agent-data /srv/agent-workspace btrfs empty
```

Setup formats it with one data copy and duplicated metadata on that same device,
retaining Btrfs's default data checksumming and copy-on-write behavior. Metadata
duplication is not device-failure protection. The managed mount selects the
top-level Btrfs filesystem; subvolume mounts are not supported. Setup does not
start Btrfs scrub, balance, or repair operations.

Backup inclusion is also per device. Non-swap disks default to included; use
`--no-disk-backup` for a disposable VM and `--disk-backup work` to retain one
important working-data disk. A swap disk is always emitted with `backup=0`,
and an explicit attempt to enable backup on it is rejected.

A VM can use different provider pools for prioritized swap tiers:

```bash
--storage swap-fast local-lvm 16G \
--disk-ssd swap-fast \
--swap-device fast swap-fast priority=200 discard=once \
--storage swap-bulk bulk-lvm 64G \
--swap-device bulk swap-bulk priority=10
```

The provider attaches both disks with stable serials; target setup confirms
that each whole disk is blank before creating swap and then persists UUID-based
fstab entries. The higher-priority SSD tier is exhausted before the HDD tier.
Neither disk uses `--storage-mount`. If a selected pool is a Proxmox `zfspool`,
setup warns that the layout is unqualified. This guest block-I/O layout is not
the same as configuring host swap directly on a zvol, but sustained guest swap
can still amplify ZFS I/O and memory pressure.

To accelerate one durable HDD-backed data disk with a separate SSD-backed
virtual disk, add an LVM cache declaration:

```bash
--storage data bulk-lvm 3T \
--storage data-cache local-lvm 128G \
--disk-ssd data-cache \
--storage-cache data data-cache writethrough \
--storage-mount data /srv/data ext4 empty
```

Basaltwater verifies both whole disks are blank, creates a guest-side LVM
volume group named `basaltwater_NAME` (hyphens become underscores), consumes
the entire SSD disk as cache media, and formats and
mounts the resulting cached logical volume. The cache disk is not mounted and
must not have a `--storage-mount`. `writethrough` is the default and safer mode:
a completed write has reached both the cache and the HDD origin. `writeback`
can reduce write latency but accepts additional data-loss risk if the cache
volume or SSD fails; use it only with an explicit power-loss and recovery
plan. Cache creation is provisioning-only. Reruns verify and retain an existing
managed cache, including its volume group and both physical volumes. Unmanaged
VGs, partitions, signatures, or filesystems are rejected for cache creation.
Signature-scan failures also stop setup before partitioning or formatting.
Repeated identical LVM mapper records are accepted when both backing disks
report the same cached volume; conflicting records stop setup. If cache
creation fails, cleanup removes PV labels only after successful PV creation
and, when needed, successful removal of the new VG. Failed or uncertain cleanup
preserves the remaining metadata for inspection instead of force-erasing it.

For a separate home filesystem on a newly provisioned VM:

```bash
--storage home-data local-lvm 32G \
--storage-mount home-data /home ext4 empty
```

After SSH becomes ready, target setup identifies each disk by serial and
declared capacity, partitions a confirmed blank disk, and checks that its sole
GPT partition reports the declared capacity (allowing up to 2 MiB for GPT and
alignment overhead) before formatting. It creates a required UUID-based
systemd mount with a `local-fs.target` requirement. Reruns reconcile older weak
`Wants` links before starting the mount. Existing signatures, an ambiguous
device, an undersized or unreported partition, a nonempty mount path, a wrong
filesystem, or a failed mount stops setup. The mount does not use `nofail`, and
a marker on the mounted filesystem prevents an empty root-disk directory from
passing application checks. Gogs and agent repository setup verify the mount
before writing and reject non-normalized paths rather than letting path
traversal bypass the managed mount boundary.
Observed mount and cache state is stored root-only in
`/var/lib/basaltwater/vm-storage.json`; each mounted filesystem also carries
`.basaltwater-storage.json` for fail-closed verification. Gogs, Samba shares,
and agent repositories verify a matching declared mount before writing, so a
failed data mount cannot silently redirect application data to the SSD boot
filesystem.

Existing disks retain their `it-NAME` serials and `it_NAME` cache volume groups.
No setup rerun or reboot is needed for this naming change. The next normal
setup rerun records and verifies existing identities without relabeling or
reformatting them. New disks added to an existing VM use `bw-NAME`. Ambiguous
old/new serial pairs and changes to recorded serials or filesystem UUIDs stop
setup before initializing the affected disk.

Named mounted disks may also be added to an existing QEMU VM that has saved
Basaltwater provisioning metadata. Start with its reconstructed setup command,
or supply the same target, system type, setup user, and `--provision-on` value,
then add only the new disk and mount declarations:

```bash
basaltw setup server_lite 10.0.0.60 admin \
  --provision-on pve1 \
  --storage team-data bulk-lvm 512G \
  --storage-mount team-data /srv/team-files ext4 empty
```

The saved root disk, existing data disks, mounts, provider identity, and
network defaults are merged automatically when omitted. Existing declarations
may instead be repeated unchanged. Basaltwater verifies every old managed disk
before mutation, checks capacity for only the additions, uses the first free
SCSI slots without touching unrelated disks, and verifies each new stable
`bw-NAME` identity. All requested slots, storage capacity, and any accompanying
memory-floor policy are checked before the first attachment. Target setup then
formats only a blank exact-identity disk and requires an empty mount path.
Multiple new mounted disks may be declared in one command. The same concise
command remains valid after success and is treated as an idempotent subset of
the saved layout. If one attachment succeeds before a later provider response
fails, a retry recognizes the attached identity and continues safely; saved
setup metadata changes only after remote setup succeeds.

Saved swap declarations and policy are retained when omitted, including on
`--verify-provider` reruns. Explicit swap options keep their normal replacement
and clearing semantics; one-time `--swap-initialize` authorization is never
inherited.

This additive path does not adopt a manually attached disk, replace a missing
saved disk, attach data storage to LXC, add `/home` to an existing VM, create
new cache or swap media, or detach or resize a disk. Supported empty mount
targets are `/data`, `/home` on a newly provisioned QEMU VM, or paths below
`/srv`, `/var/lib`, `/opt`, and `/mnt`. For `/home`, cloud-init defers creation
of the setup user until the blank disk has been mounted. Populated-path
migration is still rejected. `--image-storage` remains only the staging pool
for the VM image; it is not guest data storage. An existing unsaved VM is not
treated as permission to adopt or add disks.

Inspect a provisioned VM by its saved local `--name`:

```bash
basaltw vm show agent-dev-01
basaltw vm health agent-dev-01
```

The explicit provider host and VMID form remains available:

```bash
basaltw proxmox ls pve1
basaltw vm show pve1 100
basaltw vm health pve1 100
```

Replace `100` with the VMID returned by `proxmox ls`.

`--rdp` requires `--password` for the non-root desktop account. Set
`RDP_PASSWORD` in a secure secret source rather than placing a literal in
shell history; the password is not persisted in saved setup state.

After a guest has been provisioned, a later `setup` invocation can retain
`--provision-on`. If the target has saved local setup metadata, Basaltwater
reuses its recorded Proxmox details and skips the full provider-shape check
before updating the guest. It still identifies the guest on the authenticated
Proxmox node and verifies its host key before the first direct connection.
Guest-shape options emitted by a saved reconstructed command are also
accepted when they match that metadata. Changing any of
`--machine`, `--bridge`, `--memory`, `--balloon-min`, `--balloon-shares`,
`--allow-memory-overcommit`, `--storage`, `--cores`, `--cpu-type`,
`--base`, `--image`, or `--image-storage` requests a provisioning check instead.
Supplying any `--disk-discard`, `--disk-ssd`, or `--disk-backup` policy also
requests a provider check even when it matches local metadata. This lets an
explicit setup rerun repair disk-hardware drift or apply policy first recorded
by an older basaltwater release. A target without saved provisioning metadata
still requires `--memory` and root `--storage` on its first run. Any provider
check associated with saved VM metadata requires that VM to still exist;
Basaltwater does not silently create a replacement when reconciliation cannot
find it.

Use `--verify-provider` to check a cached provisioned guest against Proxmox even
when its declaration matches saved local metadata. Setup reconciles and
verifies the provider-side VM name, vCPU count, memory maximum, balloon
minimum, balloon shares, CPU model, and managed hardware hints on every SCSI
disk declared by logical name. Root is identified as `scsi0`; named data and
cache disks are identified by stable `bw-NAME` or retained `it-NAME` serials. Unrelated
manually attached SCSI disks are not modified. Disk reconciliation preserves
the existing volume reference, size, serial, and unowned options. Before
changing memory, setup repeats the host
capacity check while replacing the existing VM's allocation rather than
counting it twice. An unsafe balloon floor remains blocked unless
`--allow-memory-overcommit` is explicit. The check uses current running guest
configurations and retains the larger current or proposed allocation for the
VM being changed, so a pending decrease does not count as freed memory.
A running VM may need to be restarted before the guest observes a vCPU,
memory-maximum, CPU-model, or disk-hardware change; setup reports that
requirement without interrupting the guest automatically.

If a managed VM disk was moved to another pool or resized outside Basaltwater,
update its `--storage` declaration and rerun setup. As long as the declaration
retains the same logical disk names, setup bypasses the stale cached layout and
requires Proxmox to report each disk on the requested pool at or above the
requested size before saving the new declaration. `--verify-provider` performs
the same storage check when the declaration itself is unchanged. This adopts
verified metadata drift only: setup still does not move, resize, or detach an
existing disk. New named mounted disks are the exception and use the additive
workflow above.

After a GUI migration, an out-of-band move, or a move from another controller,
rerun the saved setup command with the new
`--provision-on` destination. Basaltwater keeps the explicit destination host,
credentials, and bridge instead of restoring the cached source binding. It
requires the old source VM to be stopped or absent and the destination to have
the saved IPv4 address, VM name, and managed-disk identities. If storage pools
or live capacities changed, repeat `--storage` with the new values and
unchanged logical names. Every rebind verifies the destination bridge, pool,
and minimum size of each declared disk. A missing destination is rejected
instead of creating a new VM; a matching stopped destination is started only
after those checks and a fresh memory-capacity check, even when its memory
declaration is unchanged, and is allowed time for SSH to return. The saved
provider binding changes only after remote setup succeeds. A replacement clone can
follow this path while the original remains stopped; a coexisting clone needs
a unique address, hostname, and saved setup identity.

Existing-guest setup does not replace, resize, or detach root/data volumes, and
does not mutate bridges, cache declarations, base images, or image staging
storage. It may add explicitly declared blank mounted data disks through the
saved-guest workflow above. It may also update the managed discard, SSD,
I/O-thread, and backup hints in place while verifying that the volume
reference, size, serial, and other unowned options remain intact.
When saved metadata shows that another provisioning-only declaration changed,
setup rejects the rerun instead of saving a value it did not apply. Use the
explicit `vm` or `proxmox` management command for a supported lifecycle change,
or provision a replacement guest.

During a saved VM provisioning check, the configured IPv4 address must match
a unique VM with the saved or requested Proxmox name. If the corrected
declaration changes its Proxmox name, Basaltwater applies and verifies the
rename before continuing setup. Setup rejects a desired name already owned
by a different VM. Repeating the saved `--name` and `--hostname`
values is an ordinary idempotent rerun and does not perform a full provider
check. Changing `--hostname`, or changing `--name` when it supplies the VM
hostname, performs the identity check. Regardless of whether a full
provisioning check is needed, Basaltwater verifies the saved guest's SSH host
key through the authenticated Proxmox node before checking the guest route or
starting remote setup. A guest without matching saved metadata
still requires explicit host-key enrollment. Use the explicit host and VMID
forms to inspect or repair provider-side drift and duplicates created by older
releases.
