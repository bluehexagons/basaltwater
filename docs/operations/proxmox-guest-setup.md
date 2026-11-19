# Provision VMs and containers

[Documentation index](proxmox.md)

## Graphical VM hardware baseline

Provisioned VMs that include a desktop or enable RDP are created with both a
VirtIO-GPU display and a serial socket. The VirtIO device supplies a usable
Proxmox noVNC recovery console; the serial socket remains available for boot
diagnostics. Server-only VMs retain `vga: serial0` to avoid an unused emulated
display. Existing desktop VMs created with a serial-only display can be shut
down and changed with `qm set VMID --vga virtio` on the Proxmox node.

The emulated display does **not** by itself accelerate an XRDP session.
xorgxrdp creates its own resizable X.Org display with the `xrdpdev` driver.
Basaltwater probes the guest for a supported, accessible DRM render node and
enables xorgxrdp glamor only when that probe succeeds; otherwise it retains the
software fallback. A VM is not granted `video` or `render` membership merely
because it has an emulated display.

The resulting Proxmox baseline is:

| Setting | Basaltwater default | Rationale / alternative |
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
physical capacity. Before creating a VM, Basaltwater reports the node's total
and currently used RAM, balloon target, running guest floors and burst maxima,
and the corresponding totals after the proposed VM. A floor-over-target or
burst-over-target report includes the GiB excess and percentage of target.
Burst-only overcommit remains advisory. An unreclaimable floor over the target
stops provisioning unless `--allow-memory-overcommit` explicitly accepts it.
Failed or incomplete capacity inspection stops VM creation, memory changes,
and startup during a provider rebind. The overcommit flag accepts a measured
floor excess, not an unknown budget.
The parser accepts Proxmox's plain integer and QEMU `current=MiB` memory formats
and the documented 512 MiB default when the property is omitted. These formats
and current-versus-pending reads follow the
[QEMU configuration schema](https://pve.proxmox.com/pve-docs/qm.conf.5.html) and
[`qm config` reference](https://pve.proxmox.com/pve-docs/qm.1.html).
Downloaded image paths and pre-uploaded storage references both use Proxmox's
`import-from` option to copy and attach the root disk. Proxmox selects the native
format and volume ID for the destination backend, including directory-backed
storage whose volume IDs contain a VMID directory and filename extension. Image
sources containing commas are rejected before VM creation because commas delimit
drive options. Before booting a new VM, provisioning verifies that the root-disk
resize task finished with native status `OK`; this rejects requests smaller than the
source image even when Proxmox's CLI exits zero. A confirmed task failure triggers
cleanup of the new VM. An unknown outcome (including a timeout, lost connection,
or unavailable task status) preserves the VM and its referenced snippet for
inspection. Check its task log and disk state before recovery; setup does not
replay the resize or allocate a replacement VMID after a post-creation failure.
Only an initial guest-creation collision may retry with another VMID. Resize and
verification use the bounded deadlines described under `resize-disk` below.
The cloud-init snippet is detached from the VM before its temporary file is
removed, preventing a stale `cicustom` reference. Before detaching, Basaltwater
waits up to 180 seconds for successful cloud-init completion through the guest
agent and verifies `/etc/cloud/cloud.cfg.d/99-basaltwater-instance.cfg` contains
`manual_cache_clean: true`. This persistent setting trusts the existing instance
cache: Proxmox changes its generated instance ID when custom user-data is removed,
which would otherwise rerun first-boot modules and rotate SSH host keys. Failed
detachment preserves the snippet. Failed provisioning deletes it only after VM
destruction is confirmed; uncertain cleanup preserves it for inspection.

Keep the guard and cloud-init cache for normal reboots, snapshot rollback and
identity-preserving backup restores. Preserve the virtual NIC MAC addresses and
interface layout when restoring a replacement: an inherited network configuration
may match those MAC addresses or interface names. For example,
`qmrestore --unique` generates new MAC addresses and can leave that configuration
inactive. Keep the original guest stopped while its replacement uses the same
address and MAC. Snapshots taken before the guard was installed do not receive
its protection. Cache loss or incompatible cache invalidation can still cause
first-boot behavior. Changes to Proxmox cloud-init fields do not automatically
reinitialize an already provisioned guest; apply
ordinary guest configuration through Basaltwater setup instead.

For reusable images, prefer a never-booted vendor-image template; see the
[VM template workflow](proxmox-vm-templates.md) for construction, first-boot
configuration, clone modes, and identity checks. Basaltwater currently imports
images when provisioning; template creation and cloning use native Proxmox tools.

Before capturing an already provisioned VM with a **new identity**, remove
`99-basaltwater-instance.cfg`, run `cloud-init clean --machine-id`, and shut
down without booting the source again. Removing the guard alone is insufficient
because cloud-init can retain a manual-clean marker in its cache. Prepare fresh
user-data, networking and SSH keys for the clone. If cloud-init will configure
networking, also remove Basaltwater's managed cloud-init network-disable file
and inherited static network configuration before capture. Reset its managed
hostname-preservation setting and review inherited authorizations, credentials,
leases, and application state too. This is an explicit
image-preparation operation, not part of an identity-preserving restore. See
[cloud-init's first-boot behavior](https://docs.cloud-init.io/en/latest/explanation/first_boot.html)
and the current
[Proxmox VE administration guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf).

Physical GPU passthrough is a different profile. It needs host IOMMU/device
isolation, usually Q35/OVMF, and explicit guest drivers. If the guest exposes a
supported render node, the normal xorgxrdp probe can enable glamor; otherwise
the software fallback remains safe. Passthrough still requires separate
performance and compatibility testing and is not enabled by the default
provisioning profile.

## Provisioned web server VM

```bash
basaltw setup server_web 10.0.0.50 admin \
  --provision-on pve1 --memory 4G --storage root 32G --cores 2 \
  --base debian --name web-01-vm --node \
  --ssl --ssl-email admin@example.com \
  --deploy example.com https://github.com/user/repo.git
```

## Provisioned LXC

Use `--machine unprivileged` explicitly for an LXC and include template
storage:

```bash
basaltw setup server_web 10.0.0.50 admin \
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
LXC root disks require a pool supporting `rootdir`. Sizes are converted to
numeric GiB allocations, rounding up to a whole GiB (minimum 1 GiB).
Template selection uses stock amd64 images, including Proxmox catalogs with
an architecture column. Templates are referenced through their storage volume
IDs, so relocated `local` pools and custom template pools work alike. If the
catalog cannot supply a match, Basaltwater checks downloaded templates first.
Setup reruns verify the existing container's name and static address, start it
if stopped, and wait for SSH before refreshing host trust. Ambiguous identities
and locked containers abort recovery; setup does not force-unlock or replace them.
On Debian 13 unprivileged LXC guests, setup adds scoped systemd drop-ins for
`tmp.mount`, `run-lock.mount`, and `dev-mqueue.mount` to use util-linux's classic
mount API. This avoids mount-move denials from stock Proxmox AppArmor confinement
without enabling nesting. Only available units receive the setting. Setup marks
the container for reboot; it does not restart mounts over populated directories.
After reboot, check `systemctl --failed` and query each mount with
`findmnt --mountpoint /tmp`, `findmnt --mountpoint /run/lock`, and
`findmnt --mountpoint /dev/mqueue`.
The guest bridge defaults to the bridge carrying the Proxmox host's default
route; use `--bridge NAME` when the host has multiple routed bridge networks.
VM disks default to discard enabled and SSD emulation disabled. Use
`--no-disk-discard` to suppress TRIM or `--disk-ssd` when every declared backing
device has SSD-like latency. Append `root` or a named data/cache disk to any
positive or negative disk flag to override only that device. For example,
`--disk-ssd root` models an SSD boot device with default HDD-like data devices;
`--disk-ssd --no-disk-ssd archive` does the inverse selection from an SSD
default. Basaltwater deliberately does not guess SSD status from a pool name
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
