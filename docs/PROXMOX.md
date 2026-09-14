# Proxmox workflows

infra-tools can register Proxmox hosts, cache their capabilities, provision
Debian VMs or unprivileged LXCs, and manage guest lifecycle operations.
[Machine types](MACHINE_TYPES.md) explains the guest capability differences.
Provider-specific host operations and advanced mutations remain under
`infra-tools proxmox ...`. Common guest observations, resource statistics,
power-state lifecycle, boot ordering, and confirmed QEMU VM destruction are
available through the provider-neutral `infra-tools vm ...` commands; see the
command reference for the stable JSON shape.

These workflows target Proxmox VE 9.2 and use its current `qm` and `pct`
interfaces. Bridge discovery identifies Linux bridge interfaces by type, so
explicitly named Proxmox SDN bridges are supported alongside conventional
`vmbr*` bridges.

## Quick setup: Proxmox host to coding VM

Install infra-tools on a trusted Linux orchestration machine first. The
Proxmox host does not need a checkout; setup uploads the installed source to
`/opt/infra_tools`.

```bash
wget --timeout=20 --tries=2 -O "$HOME/.infra_tools-install.sh" https://raw.githubusercontent.com/bluehexagons/infra_tools/main/install.sh
sh "$HOME/.infra_tools-install.sh"
rm -f "$HOME/.infra_tools-install.sh"
infra-tools channel
```

If the orchestration machine is itself a Proxmox VM, install and activate its
guest agent during local self-setup:

```bash
sudo infra-tools self-setup --qemu-guest-agent
```

The option installs `qemu-guest-agent` and runs
`systemctl enable --now qemu-guest-agent`. VMs provisioned by infra_tools
already receive the same package and service configuration through cloud-init.

Set up, register, and inspect the host:

```bash
infra-tools setup server_proxmox 10.0.0.10 root \
  --key ~/.ssh/proxmox_ed25519 \
  --name pve1

infra-tools proxmox probe pve1
infra-tools proxmox top pve1
infra-tools proxmox ls pve1
infra-tools vm list pve1 --json
```

Successful `server_proxmox` setup registers the host in the selected workspace;
`--name` supplies the registry name and defaults to the host address when omitted.
New host records explicitly store `schema_version: 1` and
`provider: proxmox`. Records produced by earlier development builds are not
silently reinterpreted. If loading the registry reports an unsupported schema
or missing provider, run `infra-tools proxmox remove NAME` using the name shown
in that record, then run the normal `server_proxmox` setup or
`infra-tools proxmox add` command again. Removal matches only the stored name
or address and can delete an incompatible record without interpreting it.
This is an intentional breaking-release boundary, not a credential or
guest-data migration.

The matching `.pub` file must sit beside the private key, and the Proxmox
`root` account must accept that key.

New VM provisioning uses an SSH identity's `.pub` file for cloud-init. The key
is installed for both root and the configured guest setup username. Key-only
root SSH is the stable privileged setup channel for route repair and remote
setup handoff. The guest user joins `sudo`, but receives an unrestricted
managed `NOPASSWD` rule only when `--nopasswd` is explicit. This also lets a
rerun enable or remove that rule without depending on its previous state.
Existing-only reconciliation of a saved VM uses its established SSH access and
does not require the matching `.pub` file to remain on the controller. `--key`
is optional: infra-tools first uses a matching key
associated with the registered Proxmox host, then the local
`~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa` key.
If the Proxmox node key and guest key differ, pass `--provision-key` for the
node and `--key` for the guest. VM image downloads and cloud-init snippets are
placed through Proxmox storage APIs. By default, image staging prefers an
active file-based storage with `import` content and falls back to `iso` content;
use `--image-storage STORAGE` to choose the source storage explicitly. The
selected node must also have active `snippets` content for cloud-init data.
Custom VM image URLs must use HTTPS and include a matching 128-character
SHA-512 value with `--image-sha512`; the curated Debian catalog carries pinned
hashes automatically.

After a newly created VM or LXC begins accepting SSH, infra-tools scans its
ED25519 host key from the already authenticated Proxmox node and records it in
the workspace `known_hosts` file and the invoking user's default
`~/.ssh/known_hosts` before the first direct guest login. The same refresh
occurs on a later setup rerun when saved infra-tools metadata still identifies
the same address, machine type, and Proxmox node. This lets a managed guest
recover from a legitimate host-key change before either infra-tools or plain
`ssh HOST` performs a strict check. All following guest SSH still uses
`StrictHostKeyChecking=yes`. Existing or adopted guests without matching saved
metadata are not trusted automatically; verify them independently and use
`infra-tools ssh-key enroll HOST` when workspace enrollment is required.

## Concurrent setup and provisioning

Independent setup processes on one controller may target different servers at
the same time. Controller staging directories and setup payload leases are
unique per run, shared repository caches are locked per repository, and
workspace credential, Proxmox-host, and SSH host-key updates are serialized so
one writer cannot discard another writer's changes. History records also carry
a unique run suffix when multiple operations finish during the same second.

Only one setup may mutate a particular target at a time. The controller rejects
an overlapping same-target run, and the target holds
`/run/lock/infra-tools-setup.lock` while `/opt/infra_tools` is replaced and the
remote setup executes. This target lock also protects against a second
controller using a separate workspace.

Multiple VM or LXC provisions on one Proxmox node can proceed concurrently when
their requested IPv4 addresses and hostnames differ. On one controller,
infra-tools reserves both guest identity fields for the full provider workflow.
Because Proxmox's `nextid` lookup does not reserve the returned VMID, QEMU and
LXC creation retry bounded VMID collisions. Provisioning from different
controllers is not yet protected by the controller-local guest-identity locks;
coordinate those callers externally to prevent simultaneous claims for the
same address or hostname.

## Host-safety defaults

The `server_proxmox` flow uses Proxmox's native firewall rather than UFW and
does not create a generic `/swapfile`: Proxmox storage (especially ZFS) must
own its swap layout. It retains the host's permissive reverse-path filtering
because strict filtering can drop valid routed, NATed, or bridged guest
traffic.

Setup inspects active host swap without resizing or replacing it, reports each
device's type, size, and current use, and warns when no swap is active. A
direct `/dev/zvol/` or `/dev/zd*` swap device receives a prominent warning but
is not changed. The managed host policy persists `vm.swappiness=10` in a
late-order sysctl drop-in, applies the complete boot-time precedence for that
setting, and verifies the live result.

Every `server_proxmox` setup also reconciles the node's Proxmox balloon target.
The automatic target reserves the larger of 20% of physical RAM or 2 GiB for
the host, never raises the target above Proxmox's 80% default, and uses a 50%
floor on hosts too small to preserve the full 2 GiB reserve. Setup prints the
host total, selected percentage, and resulting headroom for comparison. Use
`--proxmox-balloon-target PERCENT` to override the calculation with a value
from 1 through 95; this changes the node policy, so reserve enough memory for
Proxmox services, storage, and QEMU overhead.

By default, setup does not change Proxmox firewall state. Supplying
`--lan-access` or `--access-source` reconciles only infra-tools-commented
entries in Proxmox's standard cluster-wide `management` IP set, preserves
operator entries, then enables the cluster firewall after the replacement
sources exist. That standard set covers the Proxmox web GUI, SSH, VNC, and
SPICE, while Proxmox automatically includes the local cluster network. Because
enabling the firewall is cluster-wide, keep a recovery SSH or console session
open for the first rollout. `--no-access-source` and `--no-lan-access` remove
the corresponding saved policy and tool-owned set entries but deliberately do
not disable a firewall that may contain other policy.

Automatic host restarts and forced restart deadlines are disabled by default.
The setup reports pending restarts, but schedule any hypervisor reboot around
guest downtime (or opt in explicitly with `--auto-restart` or
`--auto-restart-force-days`).

The default setup installs these recurring host-maintenance timers:

- `security-monitor.timer` checks fail2ban and SSH events every 15 minutes and
  sends findings when notification targets are configured.
- `auto-update-apt.timer` applies non-removing distribution upgrades daily at
  06:00; Debian's competing APT timers are retired only after this replacement
  is verified active.
- `auto-restart-if-needed.timer` checks daily at 02:00 and after boot, but the
  default Proxmox policy records and reports a deferral instead of rebooting.
- `cleanup-maintenance.timer` removes unused APT packages and residual package
  configuration, audits `dpkg` consistency, cleans bounded caches, journals,
  old crash reports, and infra-tools-owned temporary artifacts, and ensures
  filesystem TRIM through the native timer or a cleanup fallback each Sunday.
  Post-cleanup checks cover block and inode pressure on distinct local storage
  mounts. The job does not prune backups, templates, ISOs, guest volumes, or
  directly modify `proxmox-boot-tool` kernel selections.

Inspect these jobs with the commands in [Recurring Maintenance](MAINTENANCE.md).
The timers do not currently validate Proxmox quorum, guest evacuation, storage
health, or backup recoverability. Run the Proxmox audit before planned
maintenance and verify backups through your normal retention and restore
process.

Create a Debian VM with XFCE, RDP, Firefox, and coding tools:

```bash
infra-tools setup workstation_dev 10.0.0.50 agent \
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
infra-tools setup workstation_dev 10.0.0.51 agent \
  --provision-on pve1 --memory 8G --storage root 40G \
  --disk-ssd root \
  --storage agent-data bulk-lvm 128G \
  --storage-mount agent-data /srv/agent-workspace ext4 \
  --agent-workspace /srv/agent-workspace \
  --agent-tool gh --agent-tool codex \
  --repo https://github.com/user/my_codebase.git
```

Each non-root `--storage NAME [POOL] AMOUNT` requires exactly one matching
`--storage-mount NAME PATH [ext4|xfs] [empty]`, unless that disk is consumed as
cache media by `--storage-cache` or as swap by `--swap-device`. When `POOL` is omitted, the root-pool default
is used. Provisioning checks that each selected pool is active, accepts VM
images, and reports enough aggregate free capacity. It then attaches the disks
as `scsi1`, `scsi2`, and so on with stable `it-NAME` serials.
The example advertises only the SSD-backed root device as an SSD; the named
data disk keeps the VM-wide default of SSD emulation disabled. Append a logical
disk name to any disk flag for a device-specific override, such as
`--disk-ssd root`, `--no-disk-discard agent-data`, or
`--disk-discard agent-data`. An unqualified flag remains the default for all
declared disks, and a named override wins over that default.

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
--storage data ts1-storage 3T \
--storage data-cache local-lvm 128G \
--disk-ssd data-cache \
--storage-cache data data-cache writethrough \
--storage-mount data /srv/data ext4 empty
```

Infra-tools verifies both whole disks are blank, creates a guest-side LVM
volume group, consumes the entire SSD disk as cache media, and formats and
mounts the resulting cached logical volume. The cache disk is not mounted and
must not have a `--storage-mount`. `writethrough` is the default and safer mode:
a completed write has reached both the cache and the HDD origin. `writeback`
can reduce write latency but accepts additional data-loss risk if the cache
volume or SSD fails; use it only with an explicit power-loss and recovery
plan. Cache creation is provisioning-only. Existing VGs, partitions,
signatures, or filesystems are rejected rather than adopted.

For a separate home filesystem on a newly provisioned VM:

```bash
--storage home-data local-lvm 32G \
--storage-mount home-data /home ext4 empty
```

After SSH becomes ready, target setup identifies each disk by serial and
declared capacity, partitions and formats it only when it is confirmed blank,
and creates a required UUID-based systemd mount. Existing signatures, an
ambiguous device, a nonempty mount path, a wrong filesystem, or a failed mount
stops setup. The mount does not use `nofail`, and a marker on the mounted
filesystem prevents an empty root-disk directory from passing application
checks. Gogs and agent repository setup verify the mount before writing.
Observed mount and cache state is stored root-only in
`/opt/infra_tools/state/vm-storage.json`; each mounted filesystem also carries
`.infra-tools-storage.json` for fail-closed verification. Gogs, Samba shares,
and agent repositories verify a matching declared mount before writing, so a
failed data mount cannot silently redirect application data to the SSD boot
filesystem.

Named mounted disks may also be added to an existing QEMU VM that has saved
infra-tools provisioning metadata. Start with its reconstructed setup command,
or supply the same target, system type, setup user, and `--provision-on` value,
then add only the new disk and mount declarations:

```bash
infra-tools setup server_lite 10.0.0.60 admin \
  --provision-on pve1 \
  --storage team-data bulk-lvm 512G \
  --storage-mount team-data /srv/team-files ext4 empty
```

The saved root disk, existing data disks, mounts, provider identity, and
network defaults are merged automatically when omitted. Existing declarations
may instead be repeated unchanged. Infra-tools verifies every old managed disk
before mutation, checks capacity for only the additions, uses the first free
SCSI slots without touching unrelated disks, and verifies each new stable
`it-NAME` identity. All requested slots, storage capacity, and any accompanying
memory-floor policy are checked before the first attachment. Target setup then
formats only a blank exact-identity disk and requires an empty mount path.
Multiple new mounted disks may be declared in one command. The same concise
command remains valid after success and is treated as an idempotent subset of
the saved layout. If one attachment succeeds before a later provider response
fails, a retry recognizes the attached identity and continues safely; saved
setup metadata changes only after remote setup succeeds.

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
infra-tools vm show agent-dev-01
infra-tools vm health agent-dev-01
```

The explicit provider host and VMID form remains available:

```bash
infra-tools proxmox ls pve1
infra-tools vm show pve1 100
infra-tools vm health pve1 100
```

Replace `100` with the VMID returned by `proxmox ls`.

`--rdp` requires `--password` for the non-root desktop account. Set
`RDP_PASSWORD` in a secure secret source rather than placing a literal in
shell history; the password is not persisted in saved setup state.

After a guest has been provisioned, a later `setup` invocation can retain
`--provision-on`. If the target has saved local setup metadata, infra-tools
reuses its recorded Proxmox details and skips the full provider-shape check
before updating the guest. It still contacts the authenticated Proxmox node to
rescan and replace the managed guest's workspace and default user SSH host keys
before the first direct connection. Guest-shape options emitted by a saved
reconstructed command are also accepted when they match that metadata. Changing
any of
`--machine`, `--bridge`, `--memory`, `--balloon-min`, `--balloon-shares`,
`--allow-memory-overcommit`, `--storage`, `--cores`, `--cpu-type`,
`--base`, `--image`, or `--image-storage` requests a provisioning check instead.
Supplying any `--disk-discard`, `--disk-ssd`, or `--disk-backup` policy also
requests a provider check even when it matches local metadata. This lets an
explicit setup rerun repair disk-hardware drift or apply policy first recorded
by an older infra-tools release. A target without saved provisioning metadata
still requires `--memory` and root `--storage` on its first run. Any provider
check associated with saved VM metadata requires that VM to still exist;
infra-tools does not silently create a replacement when reconciliation cannot
find it.

Use `--verify-provider` to check a cached provisioned guest against Proxmox even
when its declaration matches saved local metadata. Setup reconciles and
verifies the provider-side VM name, vCPU count, memory maximum, balloon
minimum, balloon shares, CPU model, and managed hardware hints on every SCSI
disk declared by logical name. Root is identified as `scsi0`; named data and
cache disks are identified by their stable `it-NAME` serials. Unrelated
manually attached SCSI disks are not modified. Disk reconciliation preserves
the existing volume reference, size, serial, and unowned options. Before
changing memory, setup repeats the host
capacity check while replacing the existing VM's allocation rather than
counting it twice. An unsafe balloon floor remains blocked unless
`--allow-memory-overcommit` is explicit. A running VM may need to be restarted
before the guest observes a vCPU, memory-maximum, CPU-model, or disk-hardware
change; setup reports that requirement without interrupting the guest
automatically.

If a managed VM disk was moved to another pool or resized outside infra-tools,
update its `--storage` declaration and rerun setup. As long as the declaration
retains the same logical disk names, setup bypasses the stale cached layout and
requires Proxmox to report each disk on the requested pool at or above the
requested size before saving the new declaration. `--verify-provider` performs
the same storage check when the declaration itself is unchanged. This adopts
verified metadata drift only: setup still does not move, resize, or detach an
existing disk. New named mounted disks are the exception and use the additive
workflow above.

For an offline GUI migration, rerun the saved setup command with the new
`--provision-on` destination. Infra-tools keeps the explicit destination host,
credentials, and bridge instead of restoring the cached source binding. It
requires the old source VM to be stopped or absent and the destination to have
the saved IPv4 address, VM name, and managed-disk identities. If storage pools
or live capacities changed, repeat `--storage` with the new values and
unchanged logical names. Every rebind verifies the destination bridge, pool,
and minimum size of each declared disk. A missing destination is rejected
instead of creating a new VM; a matching stopped destination is started only
after those checks and allowed time for SSH to return. The saved provider
binding changes only after remote setup succeeds. A replacement clone can
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

During a provisioning check, the configured IPv4 address is the stable guest
identity. If that VM already exists and the corrected declaration changes its
Proxmox name, infra-tools applies and verifies the rename before continuing
setup. A desired name already owned by a different VM is rejected rather than
creating an ambiguous duplicate. Repeating the saved `--name` and `--hostname`
values is an ordinary idempotent rerun and does not perform a full provider
check. Changing `--hostname`, or changing `--name` when it supplies the VM
hostname, performs the identity check. Regardless of whether a full
provisioning check is needed, the authenticated Proxmox node refreshes the
saved guest's workspace and default user SSH host keys before infra-tools checks
the guest route or starts remote setup. A guest without matching saved metadata
still requires explicit host-key enrollment. Use the explicit host and VMID
forms to inspect or repair provider-side drift and duplicates created by older
releases.

## Graphical VM hardware baseline

Provisioned VMs that include a desktop or enable RDP are created with both a
VirtIO-GPU display and a serial socket. The VirtIO device supplies a usable
Proxmox noVNC recovery console; the serial socket remains available for boot
diagnostics. Server-only VMs retain `vga: serial0` to avoid an unused emulated
display. Existing desktop VMs created with a serial-only display can be shut
down and changed with `qm set VMID --vga virtio` on the Proxmox node.

The emulated display does **not** by itself accelerate an XRDP session.
xorgxrdp creates its own resizable X.Org display with the `xrdpdev` driver.
Infra-tools probes the guest for a supported, accessible DRM render node and
enables xorgxrdp glamor only when that probe succeeds; otherwise it retains the
software fallback. A VM is not granted `video` or `render` membership merely
because it has an emulated display.

The resulting Proxmox baseline is:

| Setting | infra-tools default | Rationale / alternative |
| --- | --- | --- |
| Display | VirtIO-GPU for desktop/RDP; serial-only for servers | VirtIO-GPU is a recovery console, not XRDP acceleration by itself. QXL/SPICE and `virtio-gl` do not replace the guest render-node probe. |
| Serial | `serial0: socket` | Retains low-level diagnostics alongside the graphical console. |
| CPU | `host` | Best performance on one node or a CPU-homogeneous cluster. Use `--cpu-type` with a compatible `x86-64-v*` model when cross-generation live migration matters. |
| Machine/firmware | Proxmox defaults | Q35/OVMF are not required for an emulated display or XRDP; prefer them when PCIe GPU passthrough requires them. |
| Disk controller | VirtIO SCSI single with `iothread=1` on every disk | Uses the per-disk I/O thread supported by the selected controller. |
| Disk discard | Enabled | Passes guest TRIM through to thin or sparse storage. Use `--no-disk-discard` as the VM-wide default, or append a logical disk name to either form for an override. |
| SSD emulation | Disabled | Use `--disk-ssd` as a VM-wide default or `--disk-ssd NAME` for only SSD-like devices. It is not auto-detected because one VM can span remote or mixed-media pools. |
| Backup inclusion | Enabled for non-swap disks | Use `--no-disk-backup [NAME]` for disposable data. Swap disks are always excluded. |
| Network | VirtIO | Lowest-overhead normal Linux guest path. Multiqueue is normally unnecessary for interactive RDP traffic. |
| Guest agent | Enabled and installed | Supports clean lifecycle and guest inspection from Proxmox. |
| Entropy | VirtIO RNG backed by `/dev/urandom` | Gives Linux guests a reliable entropy source during early boot and key generation. |
| Memory | Fixed requested allocation with VirtIO balloon device enabled | Start around 8 GiB for the documented coding desktop and size for browsers, editors, builds, and agents. Use `--balloon-min SIZE` below `--memory` only when dynamic reclamation is intentional; `--balloon-shares N` sets relative priority during contention. |

These choices follow Proxmox's documented VirtIO network and VirtIO-SCSI
performance guidance and its warning that `host` CPU trades migration
portability for host feature exposure. Proxmox also documents that selecting a
serial display disables VGA output. Cloud-init installs the guest agent and
ensures Linux loads `virtio_balloon`. Keeping the balloon minimum equal to the
maximum preserves fixed allocation while allowing Proxmox to collect detailed
guest memory information. A lower minimum lets Proxmox reclaim memory under
host pressure, which can force guest swapping or out-of-memory handling; size
it for the VM's working set rather than treating it as free overcommit. Shares
default to 1000 and are relative: a higher value gives that VM more weight when
ballooned guests compete, but does not reserve RAM or permit the host to exceed
physical capacity. Before creating a VM, infra-tools reports the node's total
and currently used RAM, balloon target, running guest floors and burst maxima,
and the corresponding totals after the proposed VM. A floor-over-target or
burst-over-target report includes the GiB excess and percentage of target.
Burst-only overcommit remains advisory. An unreclaimable floor over the target
stops provisioning unless `--allow-memory-overcommit` explicitly accepts it.
Imported images retain the native format selected by the destination storage
backend,
and provisioning rejects a requested disk smaller than the source image. The
cloud-init snippet is detached from the VM before its temporary file is
removed, preventing a stale `cicustom` reference. See the current
[Proxmox VE administration guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf).

Physical GPU passthrough is a different profile. It needs host IOMMU/device
isolation, usually Q35/OVMF, and explicit guest drivers. If the guest exposes a
supported render node, the normal xorgxrdp probe can enable glamor; otherwise
the software fallback remains safe. Passthrough still requires separate
performance and compatibility testing and is not enabled by the default
provisioning profile.

## Provisioned web server VM

```bash
infra-tools setup server_web 10.0.0.50 admin \
  --provision-on pve1 --memory 4G --storage root 32G --cores 2 \
  --base debian --name web-01-vm --node \
  --ssl --ssl-email admin@example.com \
  --deploy example.com https://github.com/user/repo.git
```

## Provisioned LXC

Use `--machine unprivileged` explicitly for an LXC and include template
storage:

```bash
infra-tools setup server_web 10.0.0.50 admin \
  --machine unprivileged --provision-on pve1 \
  --memory 4G --cores 2 --storage root 20G --storage template \
  --base debian --name web-01-lxc --node \
  --ssl --ssl-email admin@example.com \
  --deploy example.com https://github.com/user/repo.git
```

`--storage` is repeatable. QEMU provisioning accepts one root disk using
`--storage root POOL AMOUNT` or the shorthand `--storage root AMOUNT`, plus
named data disks using `--storage NAME POOL AMOUNT` or
`--storage NAME AMOUNT`. Every named disk requires a matching
`--storage-mount` unless it is the cache medium named by `--storage-cache`.
LXC provisioning uses root storage and
`--storage template` for the saved/default template pool; named disks and
guest mount and cache declarations are VM-only.
The guest bridge defaults to the bridge carrying the Proxmox host's default
route; use `--bridge NAME` when the host has multiple routed bridge networks.
VM disks default to discard enabled and SSD emulation disabled. Use
`--no-disk-discard` to suppress TRIM or `--disk-ssd` when every declared backing
device has SSD-like latency. Append `root` or a named data/cache disk to any
positive or negative disk flag to override only that device. For example,
`--disk-ssd root` models an SSD boot device with default HDD-like data devices;
`--disk-ssd --no-disk-ssd archive` does the inverse selection from an SSD
default. Infra-tools deliberately does not guess SSD status from a pool name
or storage type because LVM, ZFS, Ceph, directory, and cached pools can all
span mixed or remote media. Use `--cpu-type MODEL` to trade host CPU exposure
for cross-node compatibility.
The positional target is also the guest IPv4 address: a bare address assumes
`/24`, while `ADDRESS/PREFIX` selects another subnet without a duplicate
`--ip` option. Unless explicitly set, the IPv4 gateway is selected from the
chosen bridge's default route, then its own address, with the first usable
subnet address as a clearly reported fallback. DNS comes from the Proxmox
node, preferring bridge-specific resolvers; when the node exposes only a local
stub resolver, the inferred gateway is used.

## Guest lifecycle

### Resource pressure and boot recovery

Start troubleshooting a slow or memory-constrained host with the node summary,
then inspect the guests contributing to the load:

```bash
infra-tools proxmox top pve1
infra-tools vm stats fileserver
infra-tools vm stats build-agent --json
```

`vm stats` reads Proxmox's provider-side counters and does not require the QEMU
guest agent. It shows current CPU and memory use, disk allocation, cumulative
disk/network I/O, and uptime. Warnings highlight high CPU, memory, disk, or
reported swap use. Treat one sample as a clue: compare several samples before
resizing a VM, and check `proxmox top` to retain enough host memory for
Proxmox itself.

After a power outage, starting every VM together can overwhelm an older disk
or a small CPU. Inspect and configure typed boot settings with the saved local
VM name:

```bash
infra-tools vm autostart fileserver
infra-tools vm autostart fileserver \
  --enable --order 1 --start-delay 30 --shutdown-timeout 120
infra-tools vm autostart database \
  --enable --order 2 --start-delay 45 --shutdown-timeout 180
infra-tools vm autostart build-agent --disable
```

Lower order values start first and stop last. Put infrastructure dependencies
such as storage and DNS first, give slow services enough delay, and leave
disposable build or test VMs disabled. Unspecified order and delay values are
preserved when autostart is enabled again.

For a small host, a practical routine is:

1. Run `proxmox audit` and `proxmox top` before maintenance.
2. Use `vm stats` on the busiest guests before adding CPU or memory.
3. Keep host memory and disk headroom instead of allocating every available
   unit to guests.
4. Stagger essential autostart guests and disable nonessential ones.
5. Keep verified backups; a VM snapshot is convenient rollback state, not a
   substitute for a backup on separate storage.
6. Prefer `vm shutdown` to `vm stop`; immediate power-off can damage guest
   filesystems and application data.

### Power-state commands

For an infra-tools-provisioned VM, prefer its saved local name:

```bash
infra-tools vm status agent-dev-01
infra-tools vm start agent-dev-01
infra-tools vm pause agent-dev-01       # alias: suspend
infra-tools vm resume agent-dev-01
infra-tools vm shutdown agent-dev-01 --timeout 60
infra-tools vm stop agent-dev-01        # immediate; may cause data loss
infra-tools vm reboot agent-dev-01      # alias: restart
```

These commands also accept an explicit registered provider host and VMID, and
`--json` returns the provider-neutral result envelope. `shutdown` is the clean
power-off path; `stop` exits the guest immediately. Shutdown and reboot pass
an optional timeout to Proxmox and all lifecycle commands report the observed
state after the provider operation completes.

The provider-specific forms remain available for compatibility:

```bash
infra-tools proxmox status pve1 101
infra-tools proxmox start pve1 101
infra-tools proxmox stop pve1 101
infra-tools proxmox pause pve1 101
infra-tools proxmox resume pve1 101
infra-tools proxmox health pve1 101
```

For compatibility, the older `proxmox stop` command keeps its original
behavior: it requests a graceful shutdown unless `--force` is supplied. New
automation should use the explicit generic `vm shutdown` or `vm stop` form.

Show a summary for one or more nodes:

```bash
infra-tools proxmox top pve1 pve2
```

The summary includes node CPU, memory, storage, and guest counts. It is a
read-only health and capacity view; `probe` should be run first when a host's
storage or bridge data has not been cached.

Run a maintenance-safety audit before planned work:

```bash
infra-tools proxmox audit pve1 pve2
infra-tools proxmox audit pve1 --json
```

The audit checks core Proxmox services, quorum on clustered nodes, active tasks,
configured storage, at least 4 GiB of free root space, guest locks, running
guests, and whether a reboot is pending. It also reports host RAM and swap use,
swap devices, swappiness, and whether the previous boot journal is retained.
The previous kernel journal is scanned for OOM kills, blocked tasks, lockups,
watchdogs, storage timeouts, thermal events, and hardware-error indicators;
matching lines are included directly in text and JSON output. ZFS zvol-backed
swap is a failing audit condition, while no swap, swappiness above 10, high
memory/swap use, and missing prior-boot evidence are warnings. The command is
read-only and exits nonzero when a health check fails.

Proxmox SSH inspection commands reuse a short-lived OpenSSH control connection.
With an encrypted key, the first connection prompts for its passphrase and the
remaining checks reuse that authenticated connection. All Proxmox operations
allow that prompt when launched from a terminal; piped or parallel operations
require the key to be loaded into an SSH agent. See
[SSH authentication](SSH.md) for setup and troubleshooting.

Modify resources and configuration:

```bash
infra-tools proxmox modify pve1 101 --cores 4 --memory 8G
infra-tools proxmox reconfigure pve1 101 --set hostname=newbox
infra-tools proxmox reconfigure pve1 101 --set balloon=4096
infra-tools proxmox resize-disk pve1 101 rootfs 40G
```

For an existing VM, Proxmox stores `memory` and `balloon` in MiB. Keep
`balloon` no greater than `memory`; setting both to the same value disables
dynamic reclamation without removing the balloon device.

Snapshots and rollback:

```bash
infra-tools proxmox snapshots pve1 101
infra-tools proxmox snapshot pve1 101 pre-upgrade --description "before kernel update"
infra-tools proxmox rollback pve1 101 pre-upgrade
infra-tools proxmox delsnapshot pve1 101 pre-upgrade
```

Snapshot deletion requires typing `yes`, or `--yes` for automation. `--dry-run`
previews the command without confirmation or deletion. The interactive shell
uses the same confirmation and dry-run options.

`vm destroy` is permanent and asks for confirmation. For an infra-tools
provisioned VM, use its exact saved local name; infra-tools resolves the
registered provider host and VMID, then verifies the observed QEMU name and
configured IPv4 address before prompting:

```bash
infra-tools vm destroy agent-dev-01
infra-tools vm destroy agent-dev-01 --yes
infra-tools vm destroy pve1 101
```

The provider host/VMID form is useful for a QEMU VM without saved local setup
metadata. `--yes` skips only confirmation, while `--force` force-stops a
running VM before destruction. The command verifies that the VM is absent
afterward and retains the saved setup declaration for deliberate
reprovisioning; remove that declaration separately with
`infra-tools rm agent-dev-01` when appropriate. The legacy
`infra-tools proxmox destroy` path remains available during the broader guest
command migration.

## Placement, backups, and migration

The placement planner ranks registered nodes without changing them:

```bash
infra-tools proxmox plan place \
  --cores 4 --memory 8192 --disk 40 \
  --prefer-tag production --exclude pve3
infra-tools proxmox plan rebalance --limit 3
```

`plan rebalance` reports overloaded nodes and candidate destinations. It only
migrates when `--apply VMID` is supplied; use `--dry-run` to preview the
migration command and `--yes` to skip its confirmation prompt. `--to HOST`
overrides the top-ranked destination. Online migration and local-disk transfer
have the same storage and cluster prerequisites as the direct `migrate` command.

List and create immediate `vzdump` backups:

```bash
infra-tools proxmox backups pve1 101
infra-tools proxmox backup pve1 101 \
  --storage backup --mode snapshot --compress zstd --dry-run
```

The backup command defaults to the first backup-capable storage pool, snapshot
mode, and zstd compression. `suspend` and `stop` modes trade availability for
stronger consistency where the guest workload requires it. Always verify that
the selected storage has enough capacity and a retention policy outside
infra-tools.

Migrate a guest between registered cluster nodes:

```bash
infra-tools proxmox migrate pve1 101 pve2 --dry-run
infra-tools proxmox migrate pve1 101 pve2 \
  --online --with-local-disks
```

`--online` keeps a VM running and requires suitable shared or migrated storage.
`--with-local-disks` copies local disks to target-node storage. Use the dry run
first for production migrations.

## Orphaned volumes and stuck locks

List unreferenced guest volumes before deleting anything:

```bash
infra-tools proxmox clean-disks pve1 --dry-run
infra-tools proxmox clean-disks pve1 --delete
```

`clean-disks` is list-only by default. `--delete` requires typing `yes` unless
`--yes`/`-y` is supplied; treat it as destructive because an orphaned-volume
check cannot infer whether an external workflow still needs a volume.

Cleanup aborts if either guest inventory, cluster guest inventory, or an active
pool's volume listing fails. Cluster-wide guest IDs protect shared-storage disks
owned by other nodes. Every deletion repeats the complete scan and refuses a
volume that is no longer orphaned. Keep provisioning and migration paused during
cleanup: inventory and deletion are separate Proxmox operations. CLI partial
failures return nonzero; the shell stops cleanup and reports the failure.
`--dry-run` is honored in both interfaces, including with `--delete --yes`.

After confirming that no backup, migration, or snapshot task is still active,
clear a stale Proxmox management lock:

```bash
infra-tools proxmox unlock pve1 101 --dry-run
infra-tools proxmox unlock pve1 101
```

The unlock operation only clears the guest lock; it does not repair a failed
underlying task or roll back partial storage changes.

## Cluster and notifications

```bash
infra-tools proxmox probe-cluster 10.0.0.10 \
  --key ~/.ssh/proxmox_ed25519 --tag prod
infra-tools proxmox hosts
infra-tools proxmox audit pve1 pve2 pve3
infra-tools proxmox rolling-update pve1 pve2 pve3
infra-tools proxmox notifications install-webhook \
  pve1 https://notify.example/hook --send-test
infra-tools proxmox notifications test-webhook pve1
infra-tools proxmox shell
```

`probe-cluster` discovers nodes from Proxmox's configured names and seeds the
host registry. `rolling-update` reuses saved setup commands and workspace
credentials. It audits every target before changing any node, repeats the audit
immediately before each node update, after each update and reboot, and advances
only after verification. Evacuate or shut down guests before starting; running
or locked guests block updates even without a pending reboot. HA resources,
local Ceph configuration, and RBD/CephFS storage require operator-managed
maintenance and are rejected by this command. It does not automate evacuation,
HA maintenance mode or Ceph flags. These configuration locations follow the
[Proxmox administration guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf).
Repository/major-upgrade suitability and backup recovery remain operator checks.

SSH policy/reboot commands have 30-second limits; reachability probes use at
most 15 seconds and the remaining reboot budget. The first failure stops later
nodes. The workspace `cluster-update.json` stores per-node phases/results under
an exclusive lock. Inspect failed targets before resuming with the same ordered
names and unchanged saved configuration:

```bash
infra-tools proxmox rolling-update pve1 pve2 pve3 --resume
```

Completed nodes are skipped; known failed patch runs can be retried explicitly.
A crash during `patching` or `rebooting` has an uncertain outcome and refuses
automatic replay. Confirm the old process has stopped, inspect target-side setup
markers, and finish/verify that node manually. After successful manual patch
recovery, set only its checkpoint phase to `patched`; after a verified reboot,
set it to `rebooted`. Resume repeats health/policy checks before proceeding.
Archive checkpoints privately if abandoning the operation; leave the stable
lock file in place. Success writes `cluster-update-last.json` and clears the
active marker. Dry runs do not create or consume checkpoints.

`notifications install-webhook` configures Proxmox's native notification
matcher; repeat `--severity` to limit routing, and use `--dry-run` before
writing the endpoint. Treat webhook URLs as sensitive values.

## Network planning

Use the separate [Network inventory](NETWORKING.md) workflow to build a
workspace-scoped inventory and render a read-only Proxmox firewall plan before
applying any control-plane lockdown.

## Real-system smoke test

For a first rollout, validate one VM and one LXC compatibility path:

```bash
infra-tools proxmox hosts
infra-tools proxmox probe pve1

infra-tools setup workstation_dev 10.0.0.50 devuser \
  --provision-on pve1 --memory 8G --storage root 40G --cores 4 \
  --name dev-01-vm --rdp

infra-tools proxmox ls pve1
VMID=100  # replace with the VMID returned by `infra-tools proxmox ls`
infra-tools proxmox health pve1 "$VMID"
infra-tools proxmox snapshot pve1 "$VMID" pre-modify
infra-tools proxmox modify pve1 "$VMID" --cores 6 --memory 12G
infra-tools proxmox stop pve1 "$VMID"
infra-tools proxmox start pve1 "$VMID"

infra-tools setup server_lite 10.0.0.60 appuser \
  --machine unprivileged --provision-on pve1 \
  --memory 2G --storage root 10G --storage template
```

For the VM path, verify SSH and optional RDP access. For LXC, focus on basic
provisioning and guest management; advanced desktop behavior is most reliable
on a VM.
