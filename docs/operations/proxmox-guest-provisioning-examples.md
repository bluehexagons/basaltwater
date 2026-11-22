# Proxmox guest provisioning and disk examples

[Topic index](proxmox-host-safety.md)

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
