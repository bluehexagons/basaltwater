# Proxmox and swap setup flags

[Documentation index](command-line.md)

### Proxmox provisioning flags

| Flag | Description |
|------|-------------|
| `--provision-on HOST` | Create the setup target on this Proxmox node or registered host |
| `--provision-user USER` | SSH user for the Proxmox node |
| `--provision-key PATH` | SSH key for the Proxmox node; defaults to a saved host key, `--key`, or SSH config |
| `--bridge NAME` | Proxmox bridge for the new guest; defaults to the node's default-route bridge |
| `--memory SIZE` | Guest memory |
| `--balloon-min SIZE` | VM-only minimum memory for dynamic ballooning; defaults to `--memory` |
| `--balloon-shares N` | VM-only relative memory priority during balloon contention (1-50000; default 1000) |
| `--allow-memory-overcommit` | Explicitly allow running VM memory floors to exceed the node balloon target |
| `--storage root POOL AMOUNT` | Required root storage spec |
| `--storage root AMOUNT` | Root storage shorthand using saved defaults or `auto` |
| `--storage NAME POOL AMOUNT` | Declare a named non-root QEMU data disk; repeatable and addable to a saved managed VM when paired with a new mount |
| `--storage NAME AMOUNT` | Named-disk shorthand using the root-pool default |
| `--storage-mount NAME PATH [FILESYSTEM] [empty]` | Prepare the matching blank data disk at an empty guest path; filesystem defaults to `ext4` and may be `ext4` or `xfs` |
| `--storage-cache DATA_NAME CACHE_NAME [MODE]` | Consume a second named VM disk as an LVM cache for a mounted data disk; mode defaults to `writethrough` and may be `writethrough` or `writeback` |
| `--storage template POOL` | LXC template storage spec |
| `--storage template` | LXC shorthand for the saved/default template pool |
| `--cores N` | Guest vCPU count |
| `--cpu-type MODEL` | Proxmox VM CPU model; defaults to `host` |
| `--disk-discard [NAME]` / `--no-disk-discard [NAME]` | Enable or disable discard/TRIM globally, or override only `root` or one named VM disk; enabled by default |
| `--disk-ssd [NAME]` / `--no-disk-ssd [NAME]` | Advertise all declared VM disks, or only `root` or one named disk, as SSD-backed; disabled by default |
| `--disk-backup [NAME]` / `--no-disk-backup [NAME]` | Include disks in Proxmox backups globally or per device; enabled for non-swap disks and forced off for swap disks |
| `--base NAME` | Base image family |

Notes:

- `--storage` is repeatable.
- `root` storage is required on initial provisioning. A saved managed VM's
  additive mounted-disk rerun may omit it because the verified declaration is
  merged from local metadata.
- Provisioned VM disks use VirtIO SCSI single with a per-disk I/O thread and
  discard enabled. Use `--no-disk-discard` when storage policy must suppress
  guest TRIM. `--disk-ssd` is explicit because a Proxmox pool can be remote,
  mixed-media, or backed by a cache whose latency cannot be inferred reliably;
  an unqualified disk flag changes the VM-wide default. Append `root` or a
  declared data/cache disk name to override only that device. Device overrides
  win over the default regardless of command-line order.
- A mixed-media VM with an SSD boot disk and HDD data disk can use:

  ```bash
  --storage root local-lvm 32G \
  --disk-ssd root \
  --storage archive bulk-lvm 2T \
  --storage-mount archive /srv/archive ext4 empty
  ```

  To start from an all-SSD default and exclude one device, use
  `--disk-ssd --no-disk-ssd archive`. Reruns identify `root` as `scsi0` and
  named disks by their stable serials; manually attached SCSI disks outside
  the declaration are left unchanged.
- `--cpu-type host` exposes the node CPU for the best single-node performance.
  Choose a common Proxmox `x86-64-*` model for guests that must migrate across
  nodes with different CPU generations.
- Every named data disk must have exactly one mount declaration unless it is
  consumed as the cache device in `--storage-cache` or by `--swap-device`;
  logical names use lowercase letters, numbers, and hyphens and are at most 17
  characters. A same-provider setup rerun for a saved managed QEMU VM may add
  one or more named disks when each addition has a new empty-path
  `--storage-mount`. Existing storage and mount declarations may be omitted
  and are merged from saved metadata, or may be repeated unchanged. The
  provider verifies every saved disk, enough capacity, and enough SCSI slots
  for the complete request before attaching only the authorized new
  identities. Rerunning the same concise declaration after success is valid
  and performs no duplicate attachment. Additive cache media, swap disks,
  `/home`, LXC disks, manual-volume adoption, replacement, detach, and resize
  remain unsupported.
- `--storage-cache` builds a guest-side LVM cache from two entire blank disks.
  Put the data disk on the durable pool and the cache disk on SSD storage. The
  data disk retains its normal `--storage-mount`; the cache disk must not have
  one. `writethrough` is the safer default because writes reach both the cache
  and origin before completion. `writeback` can improve write latency but
  accepts additional data-loss risk if the cache volume or its backing SSD is
  lost. This declaration is provisioning-only and does not adopt an existing
  VG, partitioned disk, or populated filesystem.
- Automated mounting accepts only a confirmed blank disk and an empty `/data`
  path, a path below `/srv`, `/var/lib`, `/opt`, or `/mnt`, or `/home` while
  provisioning a new QEMU VM. The `/home` case mounts the disk before the
  setup user is created. A newly declared mounted disk can be hot-added to an
  existing saved QEMU VM; start from `basaltw cmd NAME` when the setup has
  other service flags that must be retained. Populated-path migration,
  existing-disk adoption, detach, and data-disk resize remain unsupported.
- Guest mounts are required UUID-based systemd mounts. Missing or mismatched
  storage stops dependent Gogs and agent repository setup instead of writing
  to the root filesystem.
- To give a newly provisioned VM a separate `/home` disk, use a logical disk
  name and mount it at `/home`:

  ```bash
  --storage home-data local-lvm 32G \
  --storage-mount home-data /home ext4 empty
  ```

  The VM setup user is created only after this blank disk is mounted. This is
  available for new VMs only; it does not migrate `/home` on an existing VM.
- Provisioned VMs automatically use a matching key from the registered
  Proxmox host or the local `~/.ssh/id_ed25519`, `id_ecdsa`, or `id_rsa`
  identity. Use `--key PATH` when the guest should use a different identity;
  the matching `PATH.pub` is installed by cloud-init for the SSH handoff when
  a new VM is created. Existing-only reconciliation of a saved VM does not
  require that public-key file to remain on the controller.
- Provisioned VMs keep fixed memory by default while retaining the VirtIO balloon
  device for guest-memory telemetry. Set `--balloon-min` below `--memory` to
  opt into dynamic ballooning; the minimum cannot exceed the maximum. Provisioning
  reports running-guest floors and burst maxima before creation and warns when
  either total exceeds the node balloon target. Burst maxima may exceed the
  target with a warning; a floor-over-target configuration is refused unless
  `--allow-memory-overcommit` is supplied. `--balloon-shares` changes relative
  priority during contention; it is not a reservation.
- `server_proxmox` setup always reconciles a node balloon target. Automatic mode
  reserves at least 20% or 2 GiB (whichever is larger), caps the target at 80%,
  and prints the resulting values. `--proxmox-balloon-target` explicitly
  overrides that policy and is accepted only for `server_proxmox`.
- `server_proxmox` setup preserves the existing swap layout, reports active
  swap devices, warns for absent or direct ZFS zvol-backed swap, and persists
  and verifies `vm.swappiness=10`.
- The positional target is the guest IPv4 address. A bare address defaults to
  `/24`; use `ADDRESS/PREFIX` for another prefix. Do not repeat it with `--ip`.
- `template` storage is LXC-only.
- Direct setup defaults to `--machine auto`, which detects Debian bare metal,
  VMs, and Proxmox LXC containers on the target.
- Proxmox provisioning defaults to a VM because it is creating a new guest;
  use `--machine unprivileged` for an LXC.
- `--machine unprivileged` keeps an existing or intentional LXC path.
- `--ipv6`, gateway, DNS, and `--hostname` remain regular setup options. When
  omitted, the IPv4 gateway comes from the selected Proxmox bridge and DNS
  comes from the Proxmox node (with bridge-specific values preferred). The
  controller machine's gateway is not reused because it may be on a different
  LAN or VPN.

### Swap configuration flags

| Flag | Description |
|------|-------------|
| `--swap-mode auto|preserve|none` | `auto` reconciles declarations or creates `/swapfile` only when no swap exists; `preserve` changes no areas; `none` removes only Basaltwater-owned areas |
| `--swap-file NAME PATH SIZE [priority=N]` | Manage a swap file; repeatable |
| `--swap-device NAME SOURCE [priority=N] [discard=POLICY]` | Manage a whole block device; `SOURCE` is a declared VM disk, `UUID=...`, or `/dev/disk/by-id/...`; discard is `off`, `once`, `pages`, or `both` |
| `--swap-zram NAME SIZE [priority=N] [algorithm=TOKEN]` | Manage compressed-RAM swap with `systemd-zram-generator`; repeatable |
| `--swappiness N` | Set `vm.swappiness` from 0 through 200 |
| `--zswap` / `--no-zswap` | Enable or disable the kernel's compressed zswap cache; managed zram and enabled zswap are mutually exclusive |
| `--zswap-max-pool-percent N` | Limit zswap to 1-50% of RAM; requires `--zswap` |
| `--swap-resume NAME` | Configure a declared swap device for hibernation resume |
| `--no-swap-resume` | Remove the managed hibernation resume-device configuration |
| `--swap-initialize NAME` | One-shot authorization to initialize a blank direct device; newly provisioned named VM swap disks are already tool-owned |

Linux uses the highest-priority swap first and shares I/O across areas with
equal priority. This makes a small SSD tier plus a larger HDD tier explicit:

```bash
--storage swap-fast local-lvm 16G \
--disk-ssd swap-fast \
--swap-device fast swap-fast priority=200 discard=once \
--storage swap-bulk bulk-lvm 64G \
--swap-device bulk swap-bulk priority=10 \
--swappiness 80
```

Swap disks cannot also be mounted or used as LVM cache media, and they are
always excluded from Proxmox backups. Other disks default to backup enabled;
use `--no-disk-backup` for a disposable VM and selectively restore important
working data with `--disk-backup NAME`. Direct devices are never reformatted
unless blank and named by `--swap-initialize`; existing filesystems,
partitions, mounts, or unrelated signatures stop setup. Removing a swap
declaration never wipes its block-device signature.

Reruns accept lsblk's `[SWAP]` marker for an already active whole-device swap
area. An unchanged area stays active without reformatting; priority drift or
a declared priority/discard change triggers reactivation with the requested
policy. Actual filesystem mounts and partitioned devices remain rejected.

Basaltwater records owned areas in `/opt/basaltwater/state/swap.json` and
edits only a marked block in `/etc/fstab`. Existing unmanaged swap is
preserved. Managed fstab entries use `nofail`, and ownership is journaled
before creating files or zram so interrupted setup can be retried safely.
Btrfs swap files are rejected because their creation is
filesystem-specific. ZFS swap files, zvol-like devices, and Proxmox guest
swap disks allocated on a `zfspool` produce warnings because those layouts
have not yet been qualified by this project.
